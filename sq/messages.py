# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# The wire protocol is transcribed as data from Ensoniq's MIDI System
# Exclusive Implementation Specification version 1.43, printed as
# Appendix A of the SQ-1 Musician's Manual, and checked against 116 dump
# files produced by three instruments. The transport layer is ported from
# the sibling p2ked, x5ded, kwsed, rxved, nanosyned and emorphed projects.
#
# ensqsqed is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 2 of the License, or (at your option)
# any later version.
#
# ensqsqed is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.

"""The SQ-R's System Exclusive messages, as far as they are known.

**There is no specification for this instrument.** The owner's manual
says so in as many words: a complete MIDI implementation could be had by
writing to Ensoniq's distributor and waiting two to three weeks. That
document does not appear to have survived anywhere public. Rainer
Buchty's Ensoniq archive, which has the ESQ-1, SQ-80 and TS-series
specifications, has nothing for the SQ-1/SQ-2/SQ-R.

So everything here is derived from dumps the instrument produced, and the
parts that are *not* known are marked as not known rather than guessed at.
That distinction is the whole point of this module: a plausible guess
about a synthesizer's memory is worse than an admission, because it will
be believed.

What is established, from eight bank dumps made by three different
people's instruments:

* every message begins ``F0 0F 06 00 <device> <opcode>`` and ends ``F7``
* ``0F`` is Ensoniq's manufacturer ID
* the all-sounds dump is opcode ``03`` and carries 32640 payload bytes,
  which is exactly 80 x 408 -- the 80 internal sounds
* the payload is seven-bit throughout, and 408 bytes per sound is 204
  bytes nibble-encoded

All of it was then confirmed against Ensoniq's own **MIDI System
Exclusive Implementation Specification version 1.43**, which covers the
SQ-1, SQ-1+, SQ-1+/32, SQ-2, SQ-2/32 and KS-32 and states that the SQ-R
recognises the same header. That document settled two things this
project had got wrong by inference, which is the reason it is cited in
every docstring that depends on it:

* **the nibble order is high first.** "H = Hi 4 bits of data byte -
  transmitted first". Decoding low-first yields 204 bytes of plausible
  nonsense rather than an error.
* **a name is sixteen ASCII characters packed into fourteen bytes**, at
  offset 177, seven bits each, most significant bit first. Not sixteen
  bytes, and not at offset 0.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

__all__ = [
    "SYSEX_START", "SYSEX_END", "ENSONIQ_ID", "SQ_FAMILY",
    "Function", "ProtocolError", "Packet",
    "frame", "parse", "to_nibbles", "from_nibbles",
    "identity_request", "NAME_ENCODING_UNKNOWN", "sound_name",
    "NAME_OFFSET", "NAME_CHARS", "NAME_BYTES",
    "from_nibbles_low_first",
    "SOUND_BYTES_PACKED", "SOUND_BYTES",
]

SYSEX_START = 0xF0
SYSEX_END = 0xF7

#: Ensoniq. A one-byte manufacturer ID, unlike Alesis's extended one.
ENSONIQ_ID = 0x0F

#: The product family byte. ``06`` in every SQ-family dump seen here. The
#: byte after it is the **SQ-1 Model ID**, ``00``, which the specification
#: says the SQ-R recognises too so that the two can exchange messages. It
#: is neither carried nor checked by :func:`parse` -- it is skipped, and
#: saying so is more honest than saying it is carried.
SQ_FAMILY = 0x06

#: 408 seven-bit bytes per sound on the wire...
SOUND_BYTES_PACKED = 408
#: ...which is 204 bytes once nibble pairs are joined. Note that a
#: SoundDiver library stores 200 bytes per SQ entry, so up to four of
#: these are header, checksum or padding. Which, is not known.
SOUND_BYTES = SOUND_BYTES_PACKED // 2

#: Where a sound's name sits in the 204-byte structure, and how big it is.
#: Specification section 4.2 and Note 3: "Sound names are sixteen ASCII
#: characters long, but are packed into 14 consecutive bytes in the sound
#: structure." Sixteen sevens are 112 bits, which is those fourteen bytes
#: exactly, so there is no padding to skip.
NAME_OFFSET = 177
NAME_CHARS = 16
NAME_BYTES = 14

#: Kept so that nothing starts guessing again. The encoding *is* known
#: now; this records that it was not, and what closed it.
NAME_ENCODING_UNKNOWN = False


class ProtocolError(ValueError):
    """A message that is not what it claims to be."""


class Function:
    """Message types -- the last byte of the packet head.

    Not to be confused with :class:`CommandType`, which is the *first
    data byte* of a message-type-00 message and is nibble-ized. The
    specification is careful about the difference and so is this: "Message
    types are part of the head and are sent as bytes, but Command types
    are considered data and are sent as two nybbles."
    """

    #: Command messages: virtual buttons, and the dump requests.
    COMMAND = 0x00
    #: Error messages: ACK, NAK, invalid button.
    ERROR = 0x01
    #: One sound: 408 bytes. The front panel's "SAVE SOUND TO MIDI".
    SINGLE_SOUND_DUMP = 0x02
    #: All 80 internal sounds: 32640 bytes. "SAVE INT TO MIDI".
    ALL_SOUNDS_DUMP = 0x03
    SINGLE_SEQUENCE_DUMP = 0x04
    ALL_SEQUENCES_DUMP = 0x05

    NAMES = {
        0x00: "Command Message",
        0x01: "Error Message",
        0x02: "Single Sound Dump",
        0x03: "All Sounds Dump",
        0x04: "Single Sequence Dump",
        0x05: "All Sequences Dump",
    }

    #: What payload size each dump must have, as a check that a message is
    #: what its type claims. Counted across 116 files from three
    #: instruments: 74 all-sounds dumps and 8 single sounds, every one of
    #: them these exact sizes.
    PAYLOAD_SIZE = {
        0x02: 408,
        0x03: 80 * 408,
    }

    @staticmethod
    def name(message_type: int) -> str:
        return Function.NAMES.get(message_type,
                                  f"message type {message_type:#04x}")


class CommandType:
    """The first data byte of a command message. **Nibble-ized.**

    Specification section 3.1. Command type 00 is virtual buttons; 01 to
    05 are the dump requests, and for those "the command type is the only
    byte in these commands".

    The unit *receives* the dump requests and never sends them, so
    anything of type 01-05 arriving on the wire came from a computer.
    """

    VIRTUAL_BUTTON = 0x00
    SINGLE_SOUND_REQUEST = 0x01
    INTERNAL_BANKS_REQUEST = 0x02
    SINGLE_SEQUENCE_REQUEST = 0x03
    ALL_SEQUENCE_REQUEST = 0x04
    DUMP_EVERYTHING_REQUEST = 0x05
    SINGLE_SEQUENCE_ALERT = 0x06
    ALL_SEQUENCE_ALERT = 0x07

    NAMES = {
        0x00: "Virtual Button",
        0x01: "Single Sound Dump Request",
        0x02: "Internal Sound Banks Dump Request",
        0x03: "Single Sequence Dump Request",
        0x04: "All Sequence Dump Request",
        0x05: "Dump Everything Request",
        0x06: "Single Sequence Dump Alert",
        0x07: "All Sequence Memory Dump Alert",
    }

    #: Which dump answers which request. The sequence requests are not
    #: here: they are answered under the Sequence Dump Protocol, with an
    #: alert and a handshake, which this program does not speak.
    REPLY_TO = {
        SINGLE_SOUND_REQUEST: Function.SINGLE_SOUND_DUMP,
        INTERNAL_BANKS_REQUEST: Function.ALL_SOUNDS_DUMP,
    }

    #: Button up is button down plus this. Specification 3.1.1.
    BUTTON_UP_OFFSET = 96

    @staticmethod
    def name(command: int) -> str:
        return CommandType.NAMES.get(command, f"command {command:#04x}")


class ErrorCode:
    """The data byte of an error message. Specification 3.2.1."""

    NAK = 0x00
    ACK = 0x01
    INVALID_BUTTON = 0x02

    NAMES = {0x00: "NAK -- busy, or the message was unintelligible",
             0x01: "ACK",
             0x02: "invalid button number"}

    @staticmethod
    def name(code: int) -> str:
        return ErrorCode.NAMES.get(code, f"error {code:#04x}")


class Packet:
    """One parsed Ensoniq message."""

    __slots__ = ("device", "function", "payload")

    def __init__(self, device: int, function: int, payload: bytes) -> None:
        #: The byte after the family bytes. It is the unit's **base MIDI
        #: channel**, 0-15: dumps from three instruments carry 0x00, 0x0A
        #: and 0x0D, and nothing else in the message varies with it. It is
        #: carried rather than acted on -- a dump is a dump whichever
        #: channel it came from.
        self.device = device
        self.function = function
        self.payload = payload

    @property
    def function_name(self) -> str:
        return Function.name(self.function)

    @property
    def is_all_sounds(self) -> bool:
        return self.function == Function.ALL_SOUNDS_DUMP

    @property
    def is_single_sound(self) -> bool:
        return self.function == Function.SINGLE_SOUND_DUMP

    @property
    def size_is_right(self) -> bool:
        """Whether the payload is the size this opcode always has.

        A dump of the wrong size is not this message, whatever its opcode
        says. There is no checksum in these messages -- none has been
        found, and the sizes are the only structural check available.
        """
        expected = Function.PAYLOAD_SIZE.get(self.function)
        return expected is None or len(self.payload) == expected

    def __repr__(self) -> str:
        return (f"Packet(device={self.device}, {self.function_name!r}, "
                f"{len(self.payload)} bytes)")


def frame(function: int, body: Sequence[int] = (), *,
          device: int = 0) -> bytes:
    """One Ensoniq message, ready to send.

    Used for building test fixtures. Nothing in this program frames a
    message and sends it to an instrument: there is no request opcode
    known to be safe, and an unknown opcode sent to a synthesizer holding
    somebody's sounds is not an experiment worth running.
    """
    # 0-15, not 0-127: this byte is the unit's base MIDI channel. Dumps
    # from three instruments carry 0x00, 0x0A and 0x0D, the bridge sweeps
    # exactly that range, and every docstring here says so -- so accepting
    # more would be this one function disagreeing with the rest.
    if not 0 <= device <= 15:
        raise ProtocolError(
            f"device {device} is outside 0-15; this byte is the unit's "
            f"base MIDI channel")
    for byte in body:
        if not 0 <= byte <= 0x7F:
            raise ProtocolError(
                f"{byte:#04x} is not a 7-bit data byte; SysEx carries no "
                f"byte with bit 7 set")
    return bytes([SYSEX_START, ENSONIQ_ID, SQ_FAMILY, 0x00, device,
                  function, *body, SYSEX_END])


def parse(message: Sequence[int]) -> Optional[Packet]:
    """Parse one Ensoniq SQ message, or ``None`` if it is not one.

    ``None`` rather than an exception for foreign traffic: a shared MIDI
    port carries other manufacturers' messages, and none of those are
    errors.
    """
    data = bytes(message)
    if len(data) < 7:
        return None
    if data[0] != SYSEX_START or data[-1] != SYSEX_END:
        return None
    if data[1] != ENSONIQ_ID or data[2] != SQ_FAMILY:
        return None
    return Packet(device=data[4], function=data[5], payload=bytes(data[6:-1]))


def to_nibbles(data: Sequence[int]) -> bytes:
    """Bytes to nibble pairs, **high nibble first**.

    Two seven-bit bytes carry one eight-bit byte, which is how a 204-byte
    sound becomes 408 bytes on the wire.
    """
    out = bytearray()
    for byte in data:
        if not 0 <= byte <= 0xFF:
            raise ProtocolError(f"{byte} is not a byte")
        out.append((byte >> 4) & 0x0F)      # hi first, per section 2.3
        out.append(byte & 0x0F)
    return bytes(out)


def from_nibbles(data: Sequence[int]) -> bytes:
    """Nibble pairs back to bytes, **high nibble first**.

    The inverse of :func:`to_nibbles`, and the order the specification
    gives in section 2.3: "H = Hi 4 bits of data byte - transmitted
    first". Getting it the other way round does not fail -- it returns a
    record of exactly the right length, full of values in exactly the
    right ranges, and every name in it is noise. This project decoded 80
    sounds the wrong way before the specification turned up, which is why
    :func:`from_nibbles_low_first` still exists: to be named, and to be
    the thing a test compares against.

    A trailing odd byte is dropped: half a byte is not a byte, and
    inventing the missing nibble would put a plausible value at the end of
    a record.
    """
    out = bytearray()
    for index in range(0, len(data) - 1, 2):
        high, low = data[index], data[index + 1]
        for part in (high, low):
            if not 0 <= part <= 0x0F:
                raise ProtocolError(
                    f"{part:#04x} is not a nibble; nibble data is 0x00-0x0F")
        out.append((high << 4) | low)
    return bytes(out)


def from_nibbles_low_first(data: Sequence[int]) -> bytes:
    """The other order. Wrong for this instrument; see :func:`from_nibbles`."""
    out = bytearray()
    for index in range(0, len(data) - 1, 2):
        low, high = data[index], data[index + 1]
        for part in (low, high):
            if not 0 <= part <= 0x0F:
                raise ProtocolError(
                    f"{part:#04x} is not a nibble; nibble data is 0x00-0x0F")
        out.append((high << 4) | low)
    return bytes(out)


def sound_name(record: Sequence[int]) -> str:
    """The sixteen-character name of a sound.

    Seven bits per character, most significant first, from
    :data:`NAME_OFFSET`. Returned with its padding intact: the
    instrument centres its names with spaces and that is how they look on
    its display, so stripping them here would be this program's opinion
    rather than the instrument's.
    """
    if len(record) < NAME_OFFSET + NAME_BYTES:
        raise ProtocolError(
            f"a sound is {SOUND_BYTES} bytes; this one is {len(record)}, "
            f"which does not reach the name at {NAME_OFFSET}")
    accumulator = bits = 0
    chars: List[str] = []
    for byte in record[NAME_OFFSET:NAME_OFFSET + NAME_BYTES]:
        accumulator = (accumulator << 8) | byte
        bits += 8
        while bits >= 7 and len(chars) < NAME_CHARS:
            bits -= 7
            value = (accumulator >> bits) & 0x7F
            chars.append(chr(value) if 32 <= value < 127 else "?")
    return "".join(chars)


def command_message(command: int, body: Sequence[int] = (), *,
                    channel: int = 0) -> bytes:
    """A message-type-00 command, with its data nibble-ized.

    The command type and everything after it are *data*, so each byte
    becomes two nibbles, high first. Getting that wrong sends a command
    type of 0 and a button number made of the halves of two numbers.
    """
    return frame(Function.COMMAND, to_nibbles([command, *body]),
                 device=channel)


def single_sound_request(*, channel: int = 0) -> bytes:
    """Ask for the sound the unit is on. Read-only, **makes no sound**.

    Note *which* sound: the current one, edited if it is being edited.
    There is no way to ask for a sound by number -- the protocol has none
    -- so reaching a particular one means selecting it first, which does
    make a sound. :mod:`sq.bridge` says so at the point of use.
    """
    return command_message(CommandType.SINGLE_SOUND_REQUEST, channel=channel)


def internal_banks_request(*, channel: int = 0) -> bytes:
    """Ask for all 80 internal sounds. Read-only, **makes no sound**.

    The one request this program leans on: 80 sounds and their names in a
    single 32647-byte reply.
    """
    return command_message(CommandType.INTERNAL_BANKS_REQUEST,
                           channel=channel)


def virtual_button(button: int, *, down: bool = True,
                   channel: int = 0) -> bytes:
    """Press or release a front-panel button remotely.

    Provided because it explains the four otherwise unreadable messages
    in one of the owner's captures, and because releasing a button that
    was pressed is not optional -- the specification says to send an up
    for every down. **Nothing in this program calls it.** Driving a front
    panel blind is a different program, and a button number that is wrong
    is answered with an INVALID BUTTON error rather than a refusal to act.
    """
    if not 0 <= button <= 95:
        raise ProtocolError(
            f"button {button} is outside 0-95; up is sent as the button "
            f"number plus {CommandType.BUTTON_UP_OFFSET}, which is what "
            f"`down=False` does")
    value = button if down else button + CommandType.BUTTON_UP_OFFSET
    return command_message(CommandType.VIRTUAL_BUTTON, [value],
                           channel=channel)


def identity_request() -> bytes:
    """The universal Identity Request.

    Kept so that a user can try it, and documented so that its silence is
    not mistaken for a fault: this instrument is from 1990, its
    implementation chart lists no reply, and it did not answer one on the
    bench.
    """
    return bytes([SYSEX_START, 0x7E, 0x7F, 0x06, 0x01, SYSEX_END])
