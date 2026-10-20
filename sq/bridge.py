# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# The requests are transcribed as data from Ensoniq's MIDI System
# Exclusive Implementation Specification v1.43, section 3.1. The transport
# is ported from the sibling p2ked, x5ded, kwsed, rxved, nanosyned and
# emorphed projects.
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

"""Talking to an SQ-R.

**Verified against a real SQ-R on 2026-10-20**, on base channel 11: the
Single Sound Dump Request returned 415 bytes and the Internal Sound Banks
Dump Request returned all eighty sounds with their names. The unit was
cabled **crosswise** on a multi-port interface -- its MIDI IN on the
interface's output 2, its MIDI OUT on input 1 -- which is why
:meth:`SqBridge.autodetect` now listens on every input of an interface
while each of its outputs sends, rather than assuming a port answers on
itself. Before that change the sweep could not find this rig at all, even
though every request was correct.

Two things the specification gives that shape the code:

* **A dump request is a command message.** Message type ``00``, then a
  command type of 01-05 which is *data* and therefore nibble-ized. The
  unit receives these and never sends them, so anything of that shape on
  the wire came from a computer.
* **The unit answers errors.** An error message (type ``01``) carries
  NAK, ACK or "invalid button number". A NAK means busy or
  unintelligible, and is worth far more than waiting out a timeout --
  which is why :meth:`request` raises on one instead of ignoring it.

There is no request for a sound *by number*. The protocol has one for the
current sound and one for the whole internal bank, and nothing in
between. So reading one particular sound means selecting it first, which
makes the instrument play; :meth:`read_current_sound` says so and
:meth:`read_internal_bank` is the one to prefer.

**A preset arrives in two messages, and the first one is the
interesting one.** The specification's sequence dump protocol (section
3.1.7) is a handshake: the sender announces the size of what is coming
-- command 06 for the current preset, 07 for all of them -- and only
then sends the dump. The unit is the sender here, so the unit sends
the alert, and a program that wants the dump has to take the pair
together: the alert is what says how long the dump should be, and this
protocol's answer to a request it cannot serve is silence.

Verified on the same unit, the same day: the Single Sequence Dump
Request answered with 216 bytes -- a 48-byte sequence blob and a
168-byte preset -- and the All Sequence Dump Request with 17977 bytes
carrying all eighty of its presets. The layout those two dumps have is
written down in ``docs/PRESET-FRAMING.md``; this module parses it and
keeps the bytes verbatim.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from sq import banks
from sq import config as cfg
from sq import messages as m
from vinsynlib import midi

__all__ = [
    "SqBridge",
    "DeviceError",
    "DeviceRefused",
    "PresetRefused",
    "PresetAlert",
    "CurrentPreset",
    "AllPresetsDump",
    "list_ports",
    "likely_ports",
    "DEFAULT_TIMEOUT",
    "PROBE_TIMEOUT",
    "BANK_TIMEOUT",
    "SEQ_TIMEOUT",
    "ALL_PRESETS_TIMEOUT",
    "BUTTON_GAP",
]


class DeviceError(midi.DeviceError):
    """Something about the connection, not about the music.

    The library's class, so a probe that finds nothing is recognised by
    :func:`vinsynlib.midi.open_remembered_or_swept` and the sweep it falls
    back to happens -- with an announcement, rather than as a launch that
    quietly takes the slow path.
    """


class DeviceRefused(RuntimeError):
    """The unit said no, which is different from saying nothing.

    Deliberately *not* a :class:`DeviceError` here, as it was not before:
    every ``except DeviceError`` in this program means "the connection is
    unusable", and a refusal to one request does not make it so. The
    library does treat a refusal as something the remembered-port probe
    should give up on, which is why the probe wraps it.
    """


class PresetRefused(DeviceRefused, DeviceError):
    """The unit said no to a preset request.

    Both of the above, and on purpose. A preset read is a two-message
    exchange -- an alert and then a dump -- and a refusal in the middle
    of it leaves the exchange unfinished, which is a device error for
    whoever asked for the preset. It is still a :class:`DeviceRefused`,
    so a caller that wants to tell "the unit refused this preset" from
    "the connection is unusable" can catch that one first.
    """


DEFAULT_TIMEOUT = 2.0
PROBE_TIMEOUT = 0.6
#: An internal bank dump is 32647 bytes; at MIDI's 3125 bytes a second
#: that is ten and a half seconds. This is arithmetic, not generosity.
BANK_TIMEOUT = 25.0

#: A single preset dump is 216 data bytes -- a 48-byte sequence blob and
#: a 168-byte preset -- which is 432 bytes on the wire, a seventh of a
#: second at MIDI's 3125 bytes a second. Fifteen seconds is generosity
#: rather than arithmetic; the reason to name it is that a preset read
#: is not the thing that takes a minute, and the thing that does must
#: not borrow this number.
SEQ_TIMEOUT = 15.0

#: All eighty presets are 17977 data bytes, 35954 on the wire, which at
#: MIDI's 3125 bytes a second is about 11.6 seconds -- so this is the
#: sixty-second class, the way the internal bank dump's 25 seconds is to
#: its 32647 bytes. The unit also waits a second for an acknowledgement
#: of the alert before it sends the dump at all, which is part of what
#: this has to cover.
ALL_PRESETS_TIMEOUT = 60.0

#: One preset is 168 bytes: eight tracks, a pile, an effect, and the
#: sequence it belongs to (specification section 4.8.2).
PRESET_BYTES = 168

#: The global parameters at the end of an all-presets dump: 21 bytes
#: that neither the specification nor the manual documents field by
#: field, so they are kept verbatim (section 4.7).
GLOBAL_BYTES = 21

#: The track-data table: 100 big-endian 32-bit offsets, one per
#: possible sequence or song (section 4.7).
TRACK_TABLE_BYTES = 400

#: The allocation variables in front of the track-data table. Eight
#: bytes whose meaning the fixture does not give (section 4.7).
TRACK_ALLOC_BYTES = 8

#: The preset table: eight bytes of allocation variables followed by
#: 100 big-endian 16-bit offsets (section 4.7).
PRESET_TABLE_BYTES = 208

#: The word entries of the preset table, without the allocation
#: variables in front of them.
PRESET_WORD_BYTES = 200

#: Between a button press and its release. The specification asks for
#: "approximately 2-300 msecs" between button commands, which is a range
#: this program takes the middle of. Nothing here presses buttons; the
#: constant exists so that anything which later does has a documented
#: number rather than inventing one.
BUTTON_GAP = 0.150


@dataclass(frozen=True)
class PresetAlert:
    """The alert that comes before a preset dump.

    The specification's sequence dump protocol (section 3.1.7) is a
    two-message affair: the sender announces the size of what is
    coming, and only then sends it. The unit is the sender here, so
    the unit sends the alert -- and a program that wants the dump has
    to take both messages, because the alert is what says how long the
    dump should be.

    ``command`` is 06 for the current preset and 07 for all of them.
    ``size`` is the four-byte big-endian number the alert carries, and
    what it counts **differs between the two**:

    * command 06: the size of the sequence data segment, which is the
      blob the current preset's sequence is made of -- 48 bytes for an
      empty sequence, which is what the unit said on the bench.
    * command 07: the size of the sequence data segment of the whole
      dump. The fixture says this is the offset where the track data
      *ends*, allocation variables and all -- 4308, for the 3900 bytes
      of track data in front of it -- which is not what the wording
      says. ``docs/PRESET-FRAMING.md`` records the finding; the parser
      does not rely on it.

    ``flag`` is the sequence/song flag of command 06 (0 = sequence,
    255 = song). ``count`` is the number of defined presets of command
    07. Each is zero when the alert does not carry it.
    """

    #: 06 for the current preset, 07 for all of them.
    command: int
    #: The four-byte big-endian size from the alert. What it counts
    #: depends on the command; see the class docstring.
    size: int
    #: Sequence (0) or song (255). Command 06 only.
    flag: int = 0
    #: How many presets are defined. Command 07 only.
    count: int = 0

    @classmethod
    def parse(cls, wire_payload: bytes) -> "PresetAlert":
        """Parse the **wire payload** of an alert message.

        The alert is a command message (type 00), and a command
        message's data is nibble-ized, so the payload on the wire is
        twice as long as the six bytes it carries. This takes the wire
        form and does the de-nibbling; :meth:`from_bytes` takes the
        six bytes themselves.
        """
        return cls.from_bytes(m.from_nibbles(wire_payload))

    @classmethod
    def from_bytes(cls, data: bytes) -> "PresetAlert":
        """Parse the six bytes of an alert: command, size, flag/count."""
        if len(data) != 6:
            raise DeviceError(
                f"a preset alert is 6 bytes -- a command byte, a 4-byte "
                f"size and one flag or count byte; this one is {len(data)} "
                f"bytes"
            )
        command = data[0]
        if command not in (
            m.CommandType.SINGLE_SEQUENCE_ALERT,
            m.CommandType.ALL_SEQUENCE_ALERT,
        ):
            raise DeviceError(
                f"the unit sent a command message with command byte "
                f"{command:#04x}; a preset dump is announced by "
                f"{m.CommandType.name(m.CommandType.SINGLE_SEQUENCE_ALERT)} "
                f"or {m.CommandType.name(m.CommandType.ALL_SEQUENCE_ALERT)}"
            )
        size = int.from_bytes(data[1:5], "big")
        if command == m.CommandType.SINGLE_SEQUENCE_ALERT:
            return cls(command=command, size=size, flag=data[5], count=0)
        return cls(command=command, size=size, flag=0, count=data[5])

    @property
    def is_song(self) -> bool:
        """Whether the current preset's blob is a song rather than a
        sequence. Command 06 only; the flag is 255 for a song."""
        return self.flag == 255

    def wire_payload(self) -> bytes:
        """The bytes to put on the wire for this alert.

        The inverse of :meth:`parse`, and the half of the dump
        handshake that a *sender* needs -- which the unit is, when
        something is sending a preset to it.
        """
        last = (
            self.flag
            if self.command == m.CommandType.SINGLE_SEQUENCE_ALERT
            else self.count
        )
        return m.to_nibbles([self.command, *self.size.to_bytes(4, "big"), last])


@dataclass(frozen=True)
class CurrentPreset:
    """The preset the unit is on: its sequence data and the preset bytes.

    Two blobs, in that order, and both verbatim. The first is the
    sequence data the preset's sequence is made of -- 48 bytes for an
    empty sequence, and the alert says how long it really is. The
    second is the 168-byte preset itself (specification section
    4.8.2), which is what the model in :mod:`sq.presets` decodes.

    Nothing here is interpreted: the sequence data format is not
    documented anywhere, and a program that guessed at it would be
    guessing.
    """

    #: The sequence data blob, exactly as the unit sent it.
    seq_blob: bytes
    #: The 168-byte preset, exactly as the unit sent it.
    preset_raw: bytes

    @classmethod
    def from_reply(cls, data: bytes, blob_size: int) -> "CurrentPreset":
        """Split a single-sequence dump into its two blobs.

        ``blob_size`` is the size the alert gave. It is not assumed:
        an empty sequence is 48 bytes, but a song is longer, and the
        alert is the only thing that says which.
        """
        expected = blob_size + PRESET_BYTES
        if len(data) != expected:
            raise DeviceError(
                f"the single preset dump is {len(data)} bytes; the alert "
                f"said {blob_size} bytes of sequence data, and the preset "
                f"itself is {PRESET_BYTES}, so {expected} bytes were "
                f"expected."
            )
        return cls(seq_blob=data[:blob_size], preset_raw=data[blob_size:])


@dataclass(frozen=True)
class AllPresetsDump:
    """Every preset the unit holds, and the memory around them.

    The layout is the one in ``docs/PRESET-FRAMING.md``, which was
    written against a dump captured from the unit and checked byte by
    byte. In order, the dump is::

        allocation variables            8 bytes
        track-data offset table       100 x 4 bytes
        track data (the sequences)    variable
        preset table                  208 bytes
        presets                       168 bytes each
        global parameters             21 bytes

    Everything here is kept verbatim. The one derived number is
    :attr:`preset_area_start`, and it is derived from the **end** of
    the dump rather than from the alert, because the alert's size does
    not mean what the specification says it means on this unit (see
    :class:`PresetAlert`): the presets sit immediately before the 21
    global bytes, so the area starts ``21 + 168 * count`` bytes
    before the end. The table in front of the presets is 208 bytes,
    so it starts there too.

    The whole dump can be put back together from these fields, byte
    for byte -- with one wrinkle, which is that
    :attr:`track_blobs` is in *table* order, not in the order the
    blobs sit in the dump. The table's entries are absolute offsets
    into the dump, and the unit does not lay its sequences out in
    table order; the fixture has the ten song sequences first in the
    dump and last in the table. So the track-data region is rebuilt by
    placing each blob at the offset its table entry gives.
    """

    #: How many presets the dump holds, from the alert.
    count: int
    #: The eight allocation variables in front of the track table.
    alloc: bytes
    #: The 100 big-endian 32-bit track-data offsets, verbatim.
    track_table: bytes
    #: The sequence blobs the track table points at, in table order.
    #: Each blob is as long as its own leading long word says.
    track_blobs: Tuple[bytes, ...]
    #: The preset table verbatim: eight bytes of allocation variables
    #: followed by the 100 big-endian 16-bit offsets.
    preset_table: bytes
    #: Where the presets start in the dump.
    preset_area_start: int
    #: The presets, 168 bytes each, in the order the dump holds them.
    presets_raw: Tuple[bytes, ...]
    #: The 21 global parameters at the end of the dump, verbatim.
    globals: bytes

    @classmethod
    def _from_reply(cls, data: bytes, alert: PresetAlert) -> "AllPresetsDump":
        """Parse an all-sequences dump, given the alert that announced it.

        The alert gives the count of presets; everything else is
        found in the dump itself.
        """
        if alert.command != m.CommandType.ALL_SEQUENCE_ALERT:
            raise DeviceError(
                f"the dump was announced by {m.CommandType.name(alert.command)}; "
                f"an all-presets dump is announced by "
                f"{m.CommandType.name(m.CommandType.ALL_SEQUENCE_ALERT)}"
            )
        count = alert.count
        minimum = (
            TRACK_ALLOC_BYTES
            + TRACK_TABLE_BYTES
            + PRESET_TABLE_BYTES
            + PRESET_BYTES * count
            + GLOBAL_BYTES
        )
        if len(data) < minimum:
            raise DeviceError(
                f"the all-presets dump is {len(data)} bytes; {count} presets "
                f"with the tables around them need at least {minimum}."
            )
        preset_area_start = len(data) - GLOBAL_BYTES - PRESET_BYTES * count
        presets = tuple(
            data[
                preset_area_start + index * PRESET_BYTES : preset_area_start
                + (index + 1) * PRESET_BYTES
            ]
            for index in range(count)
        )
        table = data[TRACK_ALLOC_BYTES : TRACK_ALLOC_BYTES + TRACK_TABLE_BYTES]
        return cls(
            count=count,
            alloc=data[:TRACK_ALLOC_BYTES],
            track_table=table,
            track_blobs=cls._blobs(data, table),
            preset_table=data[
                preset_area_start - PRESET_TABLE_BYTES : preset_area_start
            ],
            preset_area_start=preset_area_start,
            presets_raw=presets,
            globals=data[len(data) - GLOBAL_BYTES :],
        )

    @staticmethod
    def _blobs(data: bytes, track_table: bytes) -> Tuple[bytes, ...]:
        """The sequence blobs the track table points at, in table order.

        A table entry is an absolute offset into the dump, and the
        blob there says how long it is in its own first four bytes --
        the size the specification calls the data size. An entry of
        zero means the slot is unused.
        """
        blobs = []
        for index in range(len(track_table) // 4):
            offset = int.from_bytes(track_table[index * 4 : index * 4 + 4], "big")
            if offset == 0:
                continue
            if offset + 4 > len(data):
                raise DeviceError(
                    f"track-table entry {index} points at offset {offset}, "
                    f"which is past the end of a {len(data)}-byte dump"
                )
            size = int.from_bytes(data[offset : offset + 4], "big")
            if size < 4 or offset + size > len(data):
                raise DeviceError(
                    f"track-table entry {index} points at offset {offset}, "
                    f"where the blob claims to be {size} bytes long; that "
                    f"does not fit in a {len(data)}-byte dump"
                )
            blobs.append(data[offset : offset + size])
        return tuple(blobs)

    def slot_offsets(self) -> Dict[int, int]:
        """Which preset in the dump belongs to which sequence slot.

        The preset table says so: its word entry for slot ``i`` is
        the offset of that slot's preset, counted from the start of
        the table's own allocation variables -- which is
        ``preset_area_start - 208`` bytes into the dump. The result
        maps a slot number to an index into :attr:`presets_raw`.

        The fixture's mapping is a cyclic shift of ten, because the
        ten song presets sit at the front of the preset area while
        their slots are the last ten.
        """
        base = self.preset_area_start - PRESET_TABLE_BYTES
        words = self.preset_table[PRESET_TABLE_BYTES - PRESET_WORD_BYTES :]
        out: Dict[int, int] = {}
        for slot in range(len(words) // 2):
            value = int.from_bytes(words[slot * 2 : slot * 2 + 2], "big")
            if value == 0:
                continue
            offset = value + base - self.preset_area_start
            if offset < 0 or offset % PRESET_BYTES:
                raise DeviceError(
                    f"preset-table entry {slot} points at offset {value}, "
                    f"which is not {PRESET_BYTES} bytes into the preset "
                    f"area"
                )
            out[slot] = offset // PRESET_BYTES
        return out


def _rtmidi():
    try:
        import rtmidi
    except ImportError as exc:  # pragma: no cover
        raise DeviceError(
            "python-rtmidi is not installed, so no MIDI port can be "
            "opened. ensqsqed still reads .syx and .mid files without it."
        ) from exc
    return rtmidi


def list_ports() -> Tuple[List[str], List[str]]:
    rtmidi = _rtmidi()
    midi_in, midi_out = rtmidi.MidiIn(), rtmidi.MidiOut()
    try:
        return list(midi_in.get_ports()), list(midi_out.get_ports())
    finally:
        midi_in.delete()
        midi_out.delete()


def likely_ports() -> List[str]:
    """Ports whose name suggests the instrument. Only an ordering."""
    _, outs = list_ports()
    wanted = ("sq-r", "sqr", "ensoniq", "sq1", "sq-1", "sq2", "sq-2", "ks32", "ks-32")
    return [p for p in outs if any(w in p.lower() for w in wanted)]


class SqBridge:
    """One open pair of ports, and the requests this program makes."""

    def __init__(
        self,
        midi_in,
        midi_out,
        *,
        channel: int = 0,
        description: str = "",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._in = midi_in
        self._out = midi_out
        #: The unit's **base MIDI channel**, 0-15. It is the fifth byte of
        #: every message, and a request carrying the wrong one is ignored
        #: in silence.
        self.channel = channel
        self.description = description
        self.timeout = timeout
        self.port: Optional[str] = None
        self.recv_port: Optional[str] = None
        try:
            self._in.ignore_types(sysex=False, timing=True, active_sense=True)
        except AttributeError:  # a test double
            pass

    @classmethod
    def open(
        cls,
        port: str,
        *,
        recv_port: Optional[str] = None,
        channel: int = 0,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> "SqBridge":
        rtmidi = _rtmidi()
        ins, outs = list_ports()
        listen = recv_port or port
        if port not in outs:
            raise DeviceError(f"no output port named {port!r}; have {outs}")
        if listen not in ins:
            raise DeviceError(f"no input port named {listen!r}; have {ins}")
        midi_out, midi_in = rtmidi.MidiOut(), rtmidi.MidiIn()
        try:
            midi_out.open_port(outs.index(port))
            midi_in.open_port(ins.index(listen))
        except Exception:
            for client in (midi_in, midi_out):
                try:
                    client.close_port()
                    client.delete()
                except Exception:
                    pass
            raise
        bridge = cls(
            midi_in,
            midi_out,
            channel=channel,
            description=f"{port} -> {listen}",
            timeout=timeout,
        )
        bridge.port = port
        bridge.recv_port = listen
        return bridge

    def close(self) -> None:
        for client in (self._in, self._out):
            try:
                client.close_port()
                client.delete()
            except Exception:
                pass

    def __enter__(self) -> "SqBridge":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # --- the wire -----------------------------------------------------------

    def _drain(self) -> None:
        while self._in.get_message() is not None:
            pass

    def request(
        self, outgoing: bytes, wanted: int, *, timeout: Optional[float] = None
    ) -> m.Packet:
        """Send a request and wait for the dump that answers it.

        ``wanted`` is the **message type** of the reply, never of the
        request: a request is itself a valid Ensoniq message and comes
        back on any chain with a soft thru, as several of the sibling
        instruments' chains have.

        An error message aborts the wait rather than being ignored until
        the clock runs out. A NAK is the one piece of diagnosis this
        protocol offers that most of the siblings' do not.
        """
        self._drain()
        self._out.send_message(list(outgoing))
        deadline = time.time() + (self.timeout if timeout is None else timeout)
        while time.time() < deadline:
            message = self._in.get_message()
            if message is None:
                time.sleep(0.002)
                continue
            packet = m.parse(message[0])
            if packet is None:
                continue
            if packet.device != self.channel:
                continue
            if packet.function == m.Function.ERROR and packet.payload:
                code = m.from_nibbles(packet.payload)[:1]
                value = code[0] if code else 0xFF
                if value != m.ErrorCode.ACK:
                    raise DeviceRefused(f"the unit refused: {m.ErrorCode.name(value)}")
                continue
            if packet.function != wanted:
                continue
            if not packet.size_is_right:
                continue
            return packet
        raise TimeoutError(self._silence_means(wanted))

    def _silence_means(self, wanted: int) -> str:
        """Why nothing came back. Never picks one reason."""
        return (
            f"no {m.Function.name(wanted)} came back. The unit did not "
            f"refuse it either, so this means one of: powered off, the "
            f"wrong port, the wrong base MIDI channel (ensqsqed is using "
            f"{self.channel + 1}), System Exclusive reception switched off "
            f"in the System bank, or a chain that carries requests out but "
            f"not replies back."
        )

    def _preset_silence(self, wanted: int) -> str:
        """Why a preset dump did not arrive. Never picks one reason.

        The same causes as :meth:`_silence_means`, and one more that
        only a two-message exchange can have: a dump that stopped
        halfway. The protocol cannot report that -- the alert arrives
        first, and the dump after it is just silence -- so it is named
        rather than ruled out.
        """
        return (
            f"no {m.Function.name(wanted)} came back after the alert. The "
            f"unit did not refuse it either, so this means one of: powered "
            f"off, the wrong port, the wrong base MIDI channel (ensqsqed is "
            f"using {self.channel + 1}), System Exclusive reception switched "
            f"off in the System bank, a chain that carries requests out but "
            f"not replies back, or a dump that stopped halfway -- the "
            f"protocol has no way of saying that, so it is named rather "
            f"than ruled out."
        )

    def _request_preset(
        self,
        outgoing: bytes,
        alert_command: int,
        dump_function: int,
        *,
        timeout: float,
    ) -> Tuple[PresetAlert, bytes]:
        """Send a preset request and take the two messages that answer it.

        A sequence dump is two messages, not one: the alert that says
        how much is coming, and then the dump. :meth:`request` waits
        for one message of one type, so this waits for two and knows
        which is which.

        The alert is a command message, so it arrives like one -- and
        so does the request this program just sent, on any chain with
        a soft thru. The command byte tells them apart: the request
        carries 03 or 04, the alert 06 or 07.

        An error message aborts the wait, as it does in
        :meth:`request`. A NAK here means the unit understood the
        request and would not serve it, which is worth more than
        waiting out the clock.
        """
        self._drain()
        self._out.send_message(list(outgoing))
        deadline = time.time() + timeout
        alert: Optional[PresetAlert] = None
        while time.time() < deadline:
            message = self._in.get_message()
            if message is None:
                time.sleep(0.002)
                continue
            packet = m.parse(message[0])
            if packet is None or packet.device != self.channel:
                continue
            refusal = self._refusal(packet)
            if refusal is not None:
                raise PresetRefused(refusal)
            if alert is None:
                candidate = self._alert_of(packet, alert_command)
                if candidate is not None:
                    alert = candidate
                continue
            if packet.function != dump_function:
                continue
            return alert, m.from_nibbles(packet.payload)
        raise TimeoutError(self._preset_silence(dump_function))

    @staticmethod
    def _refusal(packet: m.Packet) -> Optional[str]:
        """The sentence for a refusal, or ``None`` if this is not one.

        An ACK is not a refusal: the unit acknowledges a dump it has
        been sent, and the reply to that is silence.
        """
        if packet.function != m.Function.ERROR or not packet.payload:
            return None
        code = m.from_nibbles(packet.payload)[:1]
        value = code[0] if code else 0xFF
        if value == m.ErrorCode.ACK:
            return None
        return f"the unit refused the preset dump: {m.ErrorCode.name(value)}"

    @staticmethod
    def _alert_of(packet: m.Packet, command: int) -> Optional[PresetAlert]:
        """The alert in this packet, if it is the one being waited for.

        The request this program sent is also a command message, and
        comes back on any chain with a soft thru, so the command byte
        is what tells the two apart.
        """
        if packet.function != m.Function.COMMAND:
            return None
        try:
            candidate = PresetAlert.parse(packet.payload)
        except (DeviceError, ValueError):
            return None
        return candidate if candidate.command == command else None

    # --- reading, all of it silent -----------------------------------------

    def read_internal_bank(
        self, *, timeout: Optional[float] = None
    ) -> Dict[int, bytes]:
        """All 80 internal sounds. Read-only, and **makes no sound**.

        One request, one 32647-byte reply, eighty sounds. This is the one
        to use: the alternative reaches a single sound only, and only the
        one the instrument is already on.
        """
        packet = self.request(
            m.internal_banks_request(channel=self.channel),
            m.Function.ALL_SOUNDS_DUMP,
            timeout=BANK_TIMEOUT if timeout is None else timeout,
        )
        out: Dict[int, bytes] = {}
        for number in range(banks.SOUNDS_PER_GROUP):
            chunk = packet.payload[
                number * m.SOUND_BYTES_PACKED : (number + 1) * m.SOUND_BYTES_PACKED
            ]
            out[number] = m.from_nibbles(chunk)
        return out

    def read_names(self, *, timeout: Optional[float] = None) -> Dict[int, str]:
        """Every internal sound's name. Silent."""
        return {
            number: m.sound_name(record)
            for number, record in self.read_internal_bank(timeout=timeout).items()
        }

    def read_current_sound(self, *, timeout: Optional[float] = None) -> bytes:
        """The sound the unit is on, edits included. Silent **in itself**.

        The request makes no sound. Reaching a *particular* sound with it
        does, because the only way to change which sound is current is to
        select one -- so a caller walking the bank this way would play
        eighty sounds. :meth:`read_internal_bank` exists so that nobody
        has to.
        """
        packet = self.request(
            m.single_sound_request(channel=self.channel),
            m.Function.SINGLE_SOUND_DUMP,
            timeout=timeout,
        )
        return m.from_nibbles(packet.payload)

    def read_current_preset(self, *, timeout: Optional[float] = None) -> CurrentPreset:
        """The preset the unit is on, with the sequence data behind it.

        Read-only, and silent. One request (command 03) and two
        messages back: the alert that says how many bytes of sequence
        data are coming, and then the dump -- the sequence blob and the
        168-byte preset.

        The alert is what makes this safe to parse. A dump of 216
        bytes is an empty sequence and a preset; a song's sequence data
        is longer, and nothing in the dump itself says where the
        preset begins. The unit says so, and the size it says is used.

        Nothing here is interpreted. The sequence data format is not
        documented, and the preset's bytes are handed on as they came.

        **A read sends nothing but the request.** The unit's dump
        protocol has the *receiver* acknowledge the alert, and this
        does not: the unit sends the dump a second later whether or not
        anything answered, and a read that wrote to the instrument
        would not be a read.
        """
        alert, data = self._request_preset(
            m.command_message(
                m.CommandType.SINGLE_SEQUENCE_REQUEST, channel=self.channel
            ),
            m.CommandType.SINGLE_SEQUENCE_ALERT,
            m.Function.SINGLE_SEQUENCE_DUMP,
            timeout=SEQ_TIMEOUT if timeout is None else timeout,
        )
        return CurrentPreset.from_reply(data, alert.size)

    def read_all_presets(self, *, timeout: Optional[float] = None) -> AllPresetsDump:
        """Every preset the unit holds. Read-only, and silent.

        One request (command 04) and two messages back: the alert,
        which says how many presets are defined, and then the dump --
        17977 bytes on the unit that was checked, eighty presets and
        the memory around them.

        It takes about twelve seconds: 35954 bytes on the wire at
        MIDI's 3125 bytes a second, plus the second the unit waits for
        an acknowledgement of the alert before it sends the dump at
        all. The timeout says sixty, which is the arithmetic with room
        in it.

        The layout is parsed as ``docs/PRESET-FRAMING.md`` records it,
        and every region is kept verbatim, so the dump can be put back
        together from what this returns.

        As with the single preset, **a read sends nothing but the
        request**: the unit's dump protocol would have the receiver
        acknowledge the alert, and the unit sends the dump a second
        later whether or not anything answered.
        """
        alert, data = self._request_preset(
            m.command_message(m.CommandType.ALL_SEQUENCE_REQUEST, channel=self.channel),
            m.CommandType.ALL_SEQUENCE_ALERT,
            m.Function.ALL_SEQUENCES_DUMP,
            timeout=ALL_PRESETS_TIMEOUT if timeout is None else timeout,
        )
        return AllPresetsDump._from_reply(data, alert)

    # --- selecting, which is the one thing that changes what plays --------

    def select(self, target: "banks.Slot", *, with_effect: bool = False) -> None:
        """Select ``target`` on the unit. **This changes what is playing.**

        Always the group selector first, then the sound: the two are
        separate program changes on this instrument, and the second means
        whatever the last selector said. A drum kit's program change is
        absolute, so it gets no selector.

        ``with_effect`` puts program change 123 first, which asks the unit
        to bring the next sound's effect too (MULTI mode). The manual is
        explicit that the selector goes *between* 123 and the real program
        change, which is the order :func:`sq.banks.select_messages` emits.
        """
        for message in banks.select_messages(
            target, self.channel, with_effect=with_effect
        ):
            self._out.send_message(list(message))

    # --- discovery ----------------------------------------------------------

    @classmethod
    def connect(
        cls,
        *,
        config_path: str = cfg.DEFAULT_PATH,
        remembered: Optional[Tuple[str, str]] = None,
        scan: bool = False,
        channel: Optional[int] = None,
        on_try: Optional[Callable[[str], None]] = None,
    ) -> "SqBridge":
        """Open the unit, sweeping every port only when that is necessary.

        **The normal path is one Single Sound Dump Request, to the port
        remembered in ``config_path``.** That is not only an optimisation,
        and on this instrument the arithmetic is worth stating:
        :meth:`autodetect` sweeps every port at each of the sixteen base
        channels, which on a rig with thirty ports is about five minutes
        before it gives up. A sweep also pings every piece of hardware on
        the chain once per launch, and once the answer is known there is
        nothing left to learn from asking again.

        So: use the remembered port if there is one, sweep if there is not,
        and sweep on request. ``scan=True`` is what ``--scan`` reaches,
        which is what is needed after the unit moves to another port --
        USB re-enumeration renumbers ALSA clients out from under a saved
        name.

        A remembered port that does not answer falls back to the sweep, but
        **it says so first**. The guess is gone, and the announcement is
        what keeps the fallback from looking like the fast path having
        worked -- which is how a slow launch quietly becomes the normal one.
        What the sweep then finds is written back, so the launch after this
        one is fast again.

        The policy itself is :func:`vinsynlib.midi.open_remembered_or_swept`,
        written and tested once for the whole family; this is the SQ-R's
        half of it, which is what a Single Sound Dump Request on a named
        port means and where the answer is filed.

        ``remembered`` defaults to what ``config_path`` holds. It is a
        parameter so a caller can pass a pair it has already read, and so a
        test can say what the file says without writing one.

        ``channel`` defaults to the base channel remembered in
        ``config_path``. That is the unit's SysEx device byte as well as the
        channel a program change goes out on, so a launch that remembers the
        unit on channel 11 must probe on 11 rather than sweep from 1.
        """
        remembered = (
            remembered if remembered is not None else cfg.load_port_pair(config_path)
        )
        if channel is None:
            channel = cfg.load_channel(config_path)
        if channel is None:
            channel = 0
        base_channel: int = channel

        def probe(send: str, recv: str) -> "SqBridge":
            """One round trip to the named port. Raises if nothing answers."""
            try:
                bridge = cls.open(
                    send, recv_port=recv, channel=base_channel, timeout=PROBE_TIMEOUT
                )
            except Exception as exc:
                raise DeviceError(str(exc)) from exc
            try:
                bridge.read_current_sound(timeout=PROBE_TIMEOUT)
            except Exception as exc:
                # DeviceRefused is deliberately not a DeviceError, so it is
                # caught here and re-raised as one: a unit that answers "no"
                # is not the instrument on this port either, and the policy
                # decides what to do about that.
                bridge.close()
                raise DeviceError(
                    f"no Single Sound Dump came back on {send!r}: {exc}"
                ) from exc
            bridge.timeout = DEFAULT_TIMEOUT
            return bridge

        def remember(bridge: "SqBridge") -> None:
            if bridge.port and bridge.recv_port:
                cfg.save_ports(bridge.port, bridge.recv_port, config_path)

        return midi.open_remembered_or_swept(
            probe=probe,
            sweep=lambda try_hook: cls.autodetect(
                timeout=PROBE_TIMEOUT, on_try=try_hook, start_channel=base_channel
            ),
            remembered=remembered,
            scan=scan,
            remember=remember,
            on_try=on_try,
        )

    @classmethod
    def autodetect(
        cls,
        *,
        timeout: float = PROBE_TIMEOUT,
        on_try: Optional[Callable[[str], None]] = None,
        start_channel: int = 0,
    ) -> "SqBridge":
        """Find an SQ-R by asking each output port while listening on every
        input **of the same interface**, on each base channel, for the
        sound it is on.

        The single-sound request is the probe rather than the bank
        request: 415 bytes against 32647, and equally silent.

        **The input and output port names need not match.** A unit cabled
        crosswise on a multi-port interface -- MIDI IN on the interface's
        output 2, MIDI OUT on its input 1 -- answers on a different port
        from the one the request went out on. The old sweep paired each
        port with *itself*, so that rig could never be found. This one
        opens the inputs of one interface at a time and polls all of them
        while each of that interface's outputs sends, so the pair is
        discovered rather than assumed.

        Opening *every* input on the machine at once is not an option: each
        ``MidiIn`` is an ALSA sequencer client, and a 55-port bench
        exhausts the slots ("Cannot allocate memory"). Grouping by client
        name keeps the count to one interface's ports, usually four or
        eight.

        **This is slow, and the arithmetic is worth knowing before you
        wait for it.** Sixteen base channels times every output port times
        the probe timeout: on a rig with thirty ports that is several
        minutes before it gives up. The channel given by ``--device-channel``
        (``start_channel``) is tried first, so a unit set to a non-default
        channel is found in the first pass; the others follow. Passing
        ``--port`` and ``--recv-port`` skips all of it, and the CLI
        remembers both once something answers.
        """
        ins, outs = list_ports()
        hinted = set(likely_ports())
        groups = cls._interface_groups(ins, outs)
        # A group whose output is named like the instrument first.
        ordered = sorted(
            groups.items(),
            key=lambda item: 0 if any(p in hinted for p in item[1]["outs"]) else 1,
        )

        for channel in ((start_channel + i) % 16 for i in range(16)):
            for _name, group in ordered:
                pair = cls._probe_interface(group, ins, outs, channel, timeout, on_try)
                if pair is None:
                    continue
                send, recv = pair
                # The pair is known now: reopen it through the ordinary
                # constructor so the returned bridge owns its ports and the
                # caller can close them normally.
                bridge = cls.open(
                    send, recv_port=recv, channel=channel, timeout=timeout
                )
                bridge.timeout = DEFAULT_TIMEOUT
                return bridge

        raise DeviceError(
            "no SQ-R answered a Single Sound Dump Request on any output "
            "port, on any of the sixteen base channels, with the inputs of "
            "its own interface listening. Check the cable, that the unit is "
            "on, that System Excl is ON in its System bank, and that a MIDI "
            "cable runs from the unit's MIDI OUT back to this computer -- a "
            "unit that plays but never answers has only one direction "
            "cabled. If the unit's input and output are on two different "
            "interfaces, pass --port and --recv-port explicitly."
        )

    @staticmethod
    def _interface_groups(
        ins: List[str], outs: List[str]
    ) -> Dict[str, Dict[str, List[str]]]:
        """Group port names by ALSA client (interface).

        A crosswise cable can only plausibly land on another port of the
        same interface, so that is the unit of search. ``Midi Through`` is
        skipped: it is the kernel's loopback, not an instrument.
        """
        groups: Dict[str, Dict[str, List[str]]] = {}
        for kind, names in (("ins", ins), ("outs", outs)):
            for name in names:
                if "Midi Through" in name:
                    continue
                client = name.split(":", 1)[0]
                groups.setdefault(client, {"ins": [], "outs": []})[kind].append(name)
        return groups

    @classmethod
    def _probe_interface(
        cls,
        group: Dict[str, List[str]],
        ins: List[str],
        outs: List[str],
        channel: int,
        timeout: float,
        on_try: Optional[Callable[[str], None]],
    ) -> Optional[Tuple[str, str]]:
        """Probe one interface's outputs, listening on all its inputs.

        Returns ``(send_port, recv_port)`` for the first pair that answered
        a Single Sound Dump on ``channel``, or ``None``. The inputs are
        opened once for the whole interface, so a reply is caught whichever
        of its inputs the unit is cabled to.
        """
        rtmidi = _rtmidi()
        request = m.single_sound_request(channel=channel)
        listeners = cls._open_listeners(rtmidi, group["ins"], ins)
        try:
            for send in group["outs"]:
                if on_try is not None:
                    on_try(f"{send} (channel {channel + 1})")
                cls._drain_listeners(listeners)
                if not cls._send_probe(rtmidi, outs, send, request):
                    continue
                found = cls._await_dump(listeners, channel, timeout)
                if found is not None:
                    return send, found
            return None
        finally:
            cls._close_listeners(listeners)

    @classmethod
    def _open_listeners(
        cls, rtmidi: Any, recvs: List[str], ins: List[str]
    ) -> Dict[str, Any]:
        """Open every input of one interface, best-effort."""
        listeners: Dict[str, Any] = {}
        for recv in recvs:
            listener = cls._open_listener(rtmidi, ins, recv)
            if listener is not None:
                listeners[recv] = listener
        return listeners

    @staticmethod
    def _drain_listeners(listeners: Dict[str, Any]) -> None:
        """Throw away anything buffered from an earlier send."""
        for listener in listeners.values():
            while listener.get_message() is not None:
                pass

    @staticmethod
    def _close_listeners(listeners: Dict[str, Any]) -> None:
        for listener in listeners.values():
            try:
                listener.close_port()
            except Exception:
                pass
            try:
                listener.delete()
            except Exception:
                pass

    @staticmethod
    def _open_listener(rtmidi: Any, ins: List[str], name: str) -> Any:
        """Open one input for the sweep, or ``None`` if it cannot be had."""
        listener = None
        try:
            listener = rtmidi.MidiIn(queue_size_limit=65536)
            listener.open_port(ins.index(name))
            # SysEx must not be filtered; timing and active sensing are
            # noise for a dump request.
            listener.ignore_types(sysex=False, timing=True, active_sense=True)
            return listener
        except Exception:
            if listener is not None:
                try:
                    listener.delete()
                except Exception:
                    pass
            return None

    @staticmethod
    def _send_probe(rtmidi: Any, outs: List[str], send: str, request: bytes) -> bool:
        """Open ``send`` and put ``request`` on the wire.

        Returns whether it went out. The output is closed again either way,
        so only one output at a time is held open during the sweep.
        """
        port_out = None
        try:
            port_out = rtmidi.MidiOut()
            port_out.open_port(outs.index(send))
            port_out.send_message(list(request))
            return True
        except Exception:
            return False
        finally:
            if port_out is not None:
                try:
                    port_out.close_port()
                except Exception:
                    pass
                try:
                    port_out.delete()
                except Exception:
                    pass

    @staticmethod
    def _await_dump(
        listeners: Dict[str, Any], channel: int, timeout: float
    ) -> Optional[str]:
        """Poll the open inputs for the dump that answers ``channel``.

        Returns the input port name it arrived on, or ``None``. Foreign
        SysEx and the request itself echoed back by a soft-thru are
        ignored: only a Single Sound Dump with the right device byte and
        the right payload size counts.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            for name, listener in listeners.items():
                message = listener.get_message()
                if message is None:
                    continue
                packet = m.parse(message[0])
                if packet is None:
                    continue
                if packet.function != m.Function.SINGLE_SOUND_DUMP:
                    continue
                if packet.device != channel:
                    continue
                if not packet.size_is_right:
                    continue
                return name
            time.sleep(0.002)
        return None
