# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# The frame layout and the dump sizes are derived from System Exclusive
# dumps produced by the instrument itself. Ensoniq's own MIDI
# specification for this family was a printed document supplied on request
# and is not available; see DISCLAIMER.md.
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
#: byte after it is ``00`` in all of them and its meaning is unknown; it
#: is carried rather than interpreted.
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
    """Opcodes seen in real dumps.

    Deliberately short. Opcodes that have been *observed* are named;
    opcodes that Ensoniq's unavailable specification presumably also
    defines are not invented here.
    """

    #: An 11-byte message seen four times in one capture, payloads
    #: ``00 00 00 09`` and ``00 00 06 09``. It is not a dump. Community
    #: archives list a "Data Dump Request" among this family's messages,
    #: so this may be it -- or a mode change, or a handshake. Unknown, and
    #: therefore **never sent** by this program. An unknown opcode sent to
    #: a synthesizer holding somebody's sounds is not an experiment worth
    #: running.
    SHORT_COMMAND = 0x00

    #: One sound: 408 bytes. The front panel's "SAVE SOUND TO MIDI".
    SINGLE_SOUND_DUMP = 0x02

    #: All 80 internal sounds in one message: 32640 bytes. The front
    #: panel's "SAVE INT TO MIDI".
    ALL_SOUNDS_DUMP = 0x03

    NAMES = {
        0x00: "short command (meaning unknown)",
        0x02: "Single Sound Dump",
        0x03: "All Sounds Dump",
    }

    #: What payload size each dump must have, as a check that a message is
    #: what its opcode claims. Counted across 116 files from three
    #: different people's instruments: 74 all-sounds dumps and 8 single
    #: sounds, every one of them these exact sizes.
    PAYLOAD_SIZE = {
        0x02: 408,
        0x03: 80 * 408,
    }

    @staticmethod
    def name(opcode: int) -> str:
        return Function.NAMES.get(opcode, f"opcode {opcode:#04x}")


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
    if not 0 <= device <= 0x7F:
        raise ProtocolError(f"device {device} is not a 7-bit value")
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


def identity_request() -> bytes:
    """The universal Identity Request.

    Kept so that a user can try it, and documented so that its silence is
    not mistaken for a fault: this instrument is from 1990, its
    implementation chart lists no reply, and it did not answer one on the
    bench.
    """
    return bytes([SYSEX_START, 0x7E, 0x7F, 0x06, 0x01, SYSEX_END])
