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

**A preset goes back the same way it came.** The dump protocol is a
handshake in both directions, and when a program sends a preset the
unit is the *receiver*: the alert first, then the unit's ACK, then the
dump. :meth:`SqBridge.send_preset` speaks that side of it, and the
bytes it sends are the whole preset -- this protocol has no
field-level write, so one changed byte means one whole dump.

**What that writes is the slot the unit is on -- measured 2026-10-22.**
A sentinel byte sent into the current preset (track one's volume, 127
to 99) was still there after the owner selected another slot and came
back, and the display's *Edited Preset* -- the notice the send lights
up, which the manual pairs with "Press ENTER to save new preset" --
had cleared by itself in between. No ENTER is involved in this path:
the panel's ENTER/SAVE is what stores *panel* edits, and a dump
stores its own. There is also no addressing: the payload's address
bytes (preset offsets 00 and 01) are rewritten to the selected slot's
own values, so the destination is always the current selection. The
name is never taken from a dump either -- the unit keeps its own name
bytes, bit-7 decoration and all -- and preset byte 30 and the channel
bytes of tracks 2 through 8 are rewritten to the unit's current
values. Driving that panel through virtual buttons stays out of scope
here on purpose.
"""

from __future__ import annotations

import threading
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
    "PresetMismatch",
    "PresetSelectionError",
    "PresetWalkError",
    "PresetWalkFailed",
    "PresetNotConverged",
    "PresetAlert",
    "CurrentPreset",
    "AllPresetsDump",
    "PresetSent",
    "list_ports",
    "likely_ports",
    "DEFAULT_TIMEOUT",
    "PROBE_TIMEOUT",
    "BANK_TIMEOUT",
    "SEQ_TIMEOUT",
    "ALL_PRESETS_TIMEOUT",
    "ACK_WINDOW",
    "PRESET_SETTLE",
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


class PresetMismatch(DeviceError):
    """The unit did not hold what was sent to it.

    A :class:`DeviceError`, so it is caught with the rest, and it
    carries the comparison rather than only a sentence about it:
    :attr:`diff_offsets` is **every** offset at which the read-back
    differed from what was sent -- the sentence names the first few,
    this is all of them -- and :attr:`result` is the
    :class:`PresetSent` that says what the unit holds now.

    The offsets are counted from the first byte of the sequence data,
    which is how the framing document counts them: offset 82 is track
    one's volume.
    """

    def __init__(self, sentence: str, result: "PresetSent") -> None:
        super().__init__(sentence)
        #: What the send did, with the full tuple of differing offsets.
        self.result = result

    @property
    def diff_offsets(self) -> Tuple[int, ...]:
        """Every offset at which the read-back differed."""
        return self.result.diff_offsets


class PresetSelectionError(DeviceError):
    """The unit did not end up on the preset the walk aimed at.

    A :class:`DeviceError`, so it is caught with the rest. It carries
    the slot the unit was observed on, in both the sentence and
    :attr:`observed_slot`, so a caller can say where the walk stopped
    rather than pretending it arrived.

    The walk is best-effort and bounded: a dropped button press is
    detected by the check-read and corrected by re-synchronising, but
    when it cannot converge the observed slot is the honest answer.
    """

    def __init__(self, sentence: str, *, observed_slot: int) -> None:
        super().__init__(sentence)
        #: The slot the unit was on when the walk gave up.
        self.observed_slot = observed_slot


#: Aliases for the walk's failure, under the names a caller might use.
PresetWalkError = PresetSelectionError
PresetWalkFailed = PresetSelectionError
PresetNotConverged = PresetSelectionError


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

#: How long the sender of a dump waits for the receiver's acknowledgement
#: of the alert. This is the specification's own window -- "If the
#: receiver does not respond within one second, the transmitter will
#: send the dump message anyway" (section 3, Sequence Dump Protocol) --
#: and it exists for receivers that cannot answer at all.
#:
#: This unit answers in about eleven milliseconds, which is one percent
#: of the window, so waiting the full second costs nothing in practice
#: and the refusal it does catch is worth the wait.
ACK_WINDOW = 1.0

#: How long to let the unit settle after a dump before asking it what it
#: holds. The manual says the unit changes mode on receipt of a preset,
#: and a mode change is a screen redraw. The bench probe
#: (``tools/probe_preset_write.py``) waited half a second for it, and
#: this is that half second: no more, and no less.
PRESET_SETTLE = 0.5

#: The alert's sequence/song flag: 0 is a sequence and 255 is a song
#: (specification section 3.1.7). A preset edit is a sequence.
SEQUENCE_FLAG = 0x00

#: The preset-stepping buttons: **5 = previous, 7 = next**, one slot
#: each with mod-80 wraparound. Measured on the unit (button 5 took
#: P79 -> P78 five times; button 7 took P79 -> P00), and the OS image
#: names them: the main screen's action table at ``$C0AA44`` holds
#: ``$C0B776`` for key 5 and ``$C0B770`` for key 7, the two actions
#: that install the adjacent prev/next preset screen records
#: (``$FFFFAD18`` / ``$FFFFAD14``). ``docs/KEY-MAP.md`` has the full
#: firmware map.
PRESET_PREV_BUTTON = 5
PRESET_NEXT_BUTTON = 7

#: Enter/Save: the panel's save key, and the dialog's "next" (the
#: manual's three-step save is three presses of it).
ENTER_SAVE_BUTTON = 9

#: The two Track keys. A single press moves the panel's track cursor
#: (10 backwards, 12 forwards); both together toggle the track under
#: the cursor — the live mute the panel does by hand. Measured on the
#: unit 2026-10-23: the display's track number turns into "-" and the
#: track's notes go silent, while the preset's bytes do not change (the
#: audio gate is the RAM mask ``$FFFFD718``; ``docs/KEY-MAP.md`` §3).
TRACK_LEFT_BUTTON = 10
TRACK_RIGHT_BUTTON = 12

#: The preset offsets a read-back may differ at without the send having
#: failed -- the regions the unit keeps for itself on a store.
#:
#: The store commits only offsets 32-151 (the live engine's 120-byte
#: track-and-pile snapshot) of the selected slot; everything outside
#: that range -- 0-31 and 152-167 -- is never written by a dump, so
#: the seqno byte (0), the flag byte (1), the name bytes (2-17), the
#: bar/time-sig/edit-start/tempo/song-step bytes (18-31) and the
#: effect block and trailing bytes (152-167) are kept from the slot's
#: own values. Inside the committed range, the per-track byte 9
#: (sustain<<7 | program) of all eight tracks -- offsets 41, 55, 69,
#: 83, 97, 111, 125 and 139 -- is saved and restored from the slot's
#: own values by the store, so a dump's per-track MIDI program and
#: sustain bit are never adopted. Everything else in 32-151 -- the
#: tracks' other parameters and the pile -- is what the send writes,
#: and a difference there is a real mismatch.
UNIT_KEPT_OFFSETS = frozenset(
    {0, 1, *range(2, 19), 30, 41, 55, 69, 83, 97, 111, 125, 139}
)

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
class PresetSent:
    """What one preset send did.

    The unit answered the alert, the dump went out, and -- when the
    caller asked for it -- the preset was read back and compared. That
    comparison is worth something: command 03 returns the preset the
    unit is on, and after a send that is what was sent -- minus the
    regions the unit keeps for itself, which
    :meth:`SqBridge.send_preset` names and the comparison leaves out
    (:data:`sq.bridge.UNIT_KEPT_OFFSETS`; the name, preset byte 30, the
    channel bytes of tracks 2 through 8; measured 2026-10-22).

    **A verified send is a stored change -- of the unit's current
    selection.** Measured on the unit, 2026-10-22: the sent bytes
    survived a selection change away and back, while the display's
    *Edited Preset* -- which a send does light up -- had cleared by
    itself in between. What the verification itself says is narrower:
    the unit holds, right now, what was sent.
    """

    #: Whether the unit acknowledged the alert. Always true: a
    #: refusal and a timeout raise instead of coming back here.
    acked: bool
    #: Whether the read-back matched what was sent. False when the
    #: caller did not ask for a verification, and false on a mismatch
    #: -- which is raised as a :class:`PresetMismatch` rather than
    #: returned.
    verified: bool
    #: The offsets at which the read-back differed from what was sent.
    #: **Always empty in a result that was returned**: a mismatch is
    #: raised, not returned, and the exception carries the same tuple
    #: in :attr:`PresetMismatch.diff_offsets`.
    diff_offsets: Tuple[int, ...]
    #: What the unit held after the send, when it was read back.
    read_back: Optional[CurrentPreset] = None


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

    def to_bytes(self) -> bytes:
        """The dump, byte for byte, out of the parsed fields.

        **The inverse of :meth:`_from_reply`**, and the reason every
        region of the dump is kept verbatim: a dump that cannot be put
        back together cannot be written to a file and sent on, and the
        whole point of parsing it is to be able to keep it.

        The one wrinkle is the track data: :attr:`track_blobs` is in
        *table* order, and the unit does not lay its sequences out in
        that order -- the fixture has the ten song sequences first in
        the dump and last in the table.  So each blob goes back at the
        offset its own table entry gives.
        """
        header = TRACK_ALLOC_BYTES + TRACK_TABLE_BYTES
        region = bytearray(self.preset_area_start - PRESET_TABLE_BYTES - header)
        blobs = iter(self.track_blobs)
        for index in range(len(self.track_table) // 4):
            offset = int.from_bytes(self.track_table[index * 4 : index * 4 + 4], "big")
            if not offset:
                continue
            blob = next(blobs)
            region[offset - header : offset - header + len(blob)] = blob
        return b"".join(
            [
                self.alloc,
                self.track_table,
                bytes(region),
                self.preset_table,
                b"".join(self.presets_raw),
                self.globals,
            ]
        )

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


#: The minimum gap between outgoing SysEx messages, in seconds.  **The
#: unit crashes under a flood** -- measured 2026-10-23: two keys pressed
#: in quick succession gave "MIDI SYSEX ERROR" once and a reboot with
#: "unexpected event ID=131" another time.  The sibling k2kremote paces
#: its K2000 for the same reason; its revised-in-use default is 500 ms
#: (150 ms looked safe in a sweep and locked the unit up in ordinary
#: use), so this sits between the two: 300 ms per message.
SYSEX_GAP = 0.30


class PacedOut:
    """A MIDI output that never sends SysEx faster than ``gap`` seconds.

    Every message this instrument takes is SysEx -- virtual buttons and
    the dump exchange -- so everything is paced.  The lock is held
    across the sleep on purpose: serialising the senders *is* the
    throttle, the argument k2kremote's ``ThrottledOut`` makes.
    """

    def __init__(self, port, gap: float = SYSEX_GAP) -> None:
        self._port = port
        self._gap = max(gap, 0.0)
        self._last = 0.0
        self._lock = threading.Lock()
        #: Cumulative seconds spent waiting on the gap, for tests and
        #: instrumentation: "slow" and "throttled" are not the same.
        self.throttled_seconds = 0.0

    def send_message(self, message) -> None:
        with self._lock:
            wait = self._gap - (time.monotonic() - self._last)
            if wait > 0:
                self.throttled_seconds += wait
                time.sleep(wait)
            self._port.send_message(message)
            self._last = time.monotonic()

    # The raw port's lifecycle, delegated so :meth:`SqBridge.close` and
    # the tests can treat this as the port.
    def close_port(self) -> None:
        self._port.close_port()

    def delete(self) -> None:
        self._port.delete()


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
        #: The paced output: the unit reboots under a flood of SysEx, so
        #: nothing may leave faster than :data:`SYSEX_GAP`.
        self._out = PacedOut(midi_out)
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

    def _silence_causes(self) -> str:
        """The causes of silence, in one place.

        Every wait in this class ends in the same handful of causes;
        the sentences differ only in what was being waited for.
        """
        return (
            "powered off, the wrong port, the wrong base MIDI channel "
            f"(ensqsqed is using {self.channel + 1}), System Exclusive "
            "reception switched off in the System bank, or a chain that "
            "carries requests out but not replies back"
        )

    def _silence_means(self, wanted: int) -> str:
        """Why nothing came back. Never picks one reason."""
        return (
            f"no {m.Function.name(wanted)} came back. The unit did not "
            f"refuse it either, so this means one of: "
            f"{self._silence_causes()}."
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
            f"unit did not refuse it either, so this means one of: "
            f"{self._silence_causes()}, or a dump that stopped halfway -- "
            "the protocol has no way of saying that, so it is named rather "
            "than ruled out."
        )

    def _write_silence(self) -> str:
        """Why no acknowledgement came. Never picks one reason.

        The same causes as :meth:`_silence_means`, and one that only
        the *sending* side can have: the alert went out and nothing
        came back for it. The unit is not refusing -- a refusal would
        have said so -- it is not answering.
        """
        alert = m.CommandType.name(m.CommandType.SINGLE_SEQUENCE_ALERT)
        return (
            f"no ACK came back for the {alert} within {ACK_WINDOW:.1f} s. "
            "The unit did not refuse the dump either, so this means one "
            f"of: {self._silence_causes()}."
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

    # --- the writing side of the handshake ------------------------------------

    def _await_ack(self) -> None:
        """Wait for the unit's answer to the alert.

        The unit answers with an error message: ACK (1) means the dump
        may go, and anything else -- NAK (0) is the one to expect --
        means it will not be sent at all. The window is the
        specification's one second, and the unit uses about eleven
        milliseconds of it.

        Only error messages are looked at. The alert this program just
        sent comes back on any chain with a soft thru, and it is a
        command message, so it is ignored here rather than mistaken
        for an answer.
        """
        deadline = time.monotonic() + ACK_WINDOW
        while time.monotonic() < deadline:
            message = self._in.get_message()
            if message is None:
                time.sleep(0.001)
                continue
            packet = m.parse(message[0])
            if packet is None or packet.device != self.channel:
                continue
            if packet.function != m.Function.ERROR or not packet.payload:
                continue
            refusal = self._refusal(packet)
            if refusal is not None:
                raise PresetRefused(refusal)
            return
        raise TimeoutError(self._write_silence())

    @staticmethod
    def _check_preset_sizes(seq_blob: bytes, preset_raw: bytes) -> None:
        """The two halves have to be the sizes the protocol says.

        A preset is 168 bytes, and the sequence data says how long it
        is in its own first four bytes. Both are checked here rather
        than by the unit, which answers a wrong size with silence or
        with a refusal that says nothing about the size.
        """
        if len(preset_raw) != PRESET_BYTES:
            raise ValueError(
                f"a preset is {PRESET_BYTES} bytes; this one is {len(preset_raw)} bytes"
            )
        if len(seq_blob) < 4:
            raise ValueError(
                "the sequence data is shorter than the four bytes of its own size word"
            )
        size = int.from_bytes(seq_blob[:4], "big")
        if size != len(seq_blob):
            raise ValueError(
                f"the sequence data is {len(seq_blob)} bytes, but its own "
                f"size word says {size}"
            )

    @staticmethod
    def _mismatch_sentence(offsets: Tuple[int, ...]) -> str:
        """What differed, in words rather than as a list of numbers.

        Only the first few offsets are named -- a preset can be wrong
        in every byte, and a sentence with 216 numbers in it is no
        sentence. The whole tuple is on the exception.
        """
        shown = ", ".join(str(offset) for offset in offsets[:8])
        if len(offsets) > 8:
            shown += f", ... and {len(offsets) - 8} more"
        return (
            f"the unit did not read back what was sent: {len(offsets)} "
            f"byte(s) differ at offset {shown}"
        )

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

    # --- writing, which stores into the unit's selected slot ----------------

    def send_preset(
        self,
        seq_blob: bytes,
        preset_raw: bytes,
        *,
        verify: bool = True,
        timeout: float = SEQ_TIMEOUT,
    ) -> "PresetSent":
        """Send a preset to the unit, and check that it took.

        **The whole preset goes, every time.** This protocol has no
        field-level write: the only thing that can be sent is a
        complete preset -- the sequence data followed by the 168 bytes
        of the preset, in the order the unit itself sends them
        (specification section 3, "Sequence Dump Protocol"). An editor
        that changes one track therefore reads the whole preset, changes
        one byte of it and sends the whole thing back, which is what
        :meth:`read_current_preset` and this method are for. The
        siblings' rule is the same one: a field is not written, the
        dump is.

        **The exchange is the specification's three steps** (sections
        3.1.7 and 3.2.1, and what the bench measured -- see
        ``docs/PRESET-FRAMING.md`` section 9):

        1. the alert -- command 06, carrying the size of the sequence
           data that will follow and the sequence/song flag. **The
           size counts the sequence data only**, not the preset: 48
           bytes for the sequence data of an empty sequence, with the
           216-byte dump behind it;
        2. the unit's answer -- an error message whose code is ACK
           (1) or NAK (0). The unit answers in about eleven
           milliseconds, and the specification's window is one second;
        3. the dump -- a type-04 message, the sequence data followed by
           the preset, nibble-ized: the same message the unit sends as
           the answer to a dump request.

        A NAK means the unit will not take the dump, so **nothing is
        sent** and :class:`PresetRefused` says so. Silence inside the
        window is a :class:`TimeoutError` that names the alert.

        **This stores into the slot the unit is on -- measured
        2026-10-22.** A sentinel byte sent into the current preset
        (track one's volume, 127 to 99) was still there after the
        owner selected another slot and came back, and the display's
        *Edited Preset* -- which the send lights up, and which the
        manual pairs with "Press ENTER to save new preset" -- had
        cleared by itself in between. No ENTER is needed for a dump:
        the panel's ENTER/SAVE is what stores *panel* edits, and this
        exchange stores its own.

        **The destination is the current selection, and nothing is
        addressed.** The payload's address bytes (preset offsets 00
        and 01) are rewritten to the selected slot's own values on the
        way in -- a payload carrying slot 70's ``46 0b`` lands in slot
        0 when slot 0 is selected -- so what a send writes is wherever
        the unit is pointing. Select first, send second, or the bytes
        land somewhere else.

        **Three regions stay the unit's.** The name is never taken
        from a dump: the unit keeps its own name bytes, bit-7
        decoration and all, and every probe that meant to change a
        name was either rewritten or dropped. Preset byte 30 and the
        channel bytes of tracks 2 through 8 are rewritten to the
        unit's current values too -- so restoring a slot's own bytes
        is idempotent-safe, byte for byte.

        **``verify`` reads the preset back and compares it byte for
        byte**, and that comparison means something: command 03
        returns the preset the unit is on, and after a send that is
        what was sent -- the read-back of a send that landed. The
        comparison skips :data:`UNIT_KEPT_OFFSETS`, the regions the
        unit rewrites on receipt: a read-back that differs from what
        went out only in those regions is a verified send, measured so
        twice on 2026-10-22 (a restore whose read-back carried the
        unit's own channel bytes where the backup carried the factory
        values was a landed store the old comparison refused three
        times). Any other difference raises :class:`PresetMismatch` --
        a :class:`DeviceError` -- naming the offsets that differ. A
        read-back whose sequence data is a different length is a
        :class:`DeviceError` of its own, with both lengths in the
        sentence: that is not a byte that differs but a sequence that
        changed size.

        ``timeout`` is the budget for that read-back. It is not the
        acknowledgement window, which is the specification's one
        second and is not adjustable: the unit is ready in about
        eleven milliseconds.
        """
        self._check_preset_sizes(seq_blob, preset_raw)
        alert = PresetAlert(
            command=m.CommandType.SINGLE_SEQUENCE_ALERT,
            size=len(seq_blob),
            flag=SEQUENCE_FLAG,
        )
        self._drain()
        self._out.send_message(
            list(m.frame(m.Function.COMMAND, alert.wire_payload(), device=self.channel))
        )
        self._await_ack()
        self._out.send_message(
            list(
                m.frame(
                    m.Function.SINGLE_SEQUENCE_DUMP,
                    m.to_nibbles(seq_blob + preset_raw),
                    device=self.channel,
                )
            )
        )
        # The unit changes mode on receipt of a preset, and a mode
        # change is a screen redraw. The bench probe waited half a
        # second for it, and the read-back below is that read-back.
        time.sleep(PRESET_SETTLE)
        if not verify:
            return PresetSent(
                acked=True, verified=False, diff_offsets=(), read_back=None
            )
        read_back = self.read_current_preset(timeout=timeout)
        if len(read_back.seq_blob) != len(seq_blob):
            raise DeviceError(
                f"the unit's sequence data is {len(read_back.seq_blob)} "
                f"bytes after {len(seq_blob)} bytes were sent"
            )
        expected = seq_blob + preset_raw
        actual = read_back.seq_blob + read_back.preset_raw
        diff_offsets = tuple(
            index
            for index in range(len(expected))
            if expected[index] != actual[index]
            # The unit's own regions are not part of what a send can
            # change, so a difference there is not a failed store: the
            # comparison skips them (see UNIT_KEPT_OFFSETS).
            and (
                index < len(seq_blob) or index - len(seq_blob) not in UNIT_KEPT_OFFSETS
            )
        )
        result = PresetSent(
            acked=True,
            verified=not diff_offsets,
            diff_offsets=diff_offsets,
            read_back=read_back,
        )
        if diff_offsets:
            raise PresetMismatch(self._mismatch_sentence(diff_offsets), result)
        return result

    def send_preset_with_retry(
        self,
        seq_blob: bytes,
        preset_raw: bytes,
        *,
        attempts: int = 3,
        verify: bool = True,
        timeout: float = SEQ_TIMEOUT,
    ) -> "PresetSent":
        """Send a preset, and ask again when the read-back says no.

        :meth:`send_preset` with the verification on is the whole
        exchange -- alert, acknowledgement, dump, settle, read-back --
        and it raises :class:`PresetMismatch` when the read-back is
        not what went out. On the unit this program was measured
        against (2026-10-22) that failure came and went with no cause
        anyone could name: hand-rolled replicas of the very bytes
        :meth:`send_preset` puts on the wire were accepted twice out
        of two before the panel was touched and once out of ten after
        the owner navigated it, while :meth:`send_preset` itself
        landed five out of five in the same window -- byte-identical
        frames. No theory explains that, and this method is not a
        theory: asking again is what a receiver like this gets.

        So the **whole** exchange goes again -- alert, dump, and the
        verify read-back -- up to ``attempts`` times in all. Only a
        :class:`PresetMismatch` is retried: a refusal is the unit
        saying no, a silence is the unit not answering, and neither
        grows better on repetition. When the tries run out, the last
        :class:`PresetMismatch` is the one raised, so its
        :attr:`~PresetMismatch.diff_offsets` are the final read-back's.

        ``attempts`` counts the tries, so ``attempts=1`` is
        :meth:`send_preset` with nothing added. ``verify`` and
        ``timeout`` pass through, and the default of ``verify`` is the
        point of the whole method: a send that is not read back cannot
        mismatch, so there is nothing to retry and the one try is the
        try.

        One honesty a caller should carry: the unit rewrites the name,
        preset byte 30 and the channel bytes of tracks 2 through 8 on
        the way in (see :meth:`send_preset`), so a mismatch whose
        offsets all live in those regions is the unit keeping itself,
        not a failed store. The comparison knows that now --
        :data:`UNIT_KEPT_OFFSETS` is skipped before a mismatch is
        declared -- so a landed store whose read-back differs only
        there verifies on the first try and no retry is spent on it.
        The retries that remain are for the drops no one can name.
        """
        if attempts < 1:
            raise ValueError(
                f"a retry needs at least one attempt; this one was given {attempts}"
            )
        remaining = attempts
        while True:
            try:
                return self.send_preset(
                    seq_blob, preset_raw, verify=verify, timeout=timeout
                )
            except PresetMismatch:
                # Only a mismatch is retried, and the whole exchange
                # goes again. On the last try this raises, and what
                # comes out is that try's own read-back's mismatch.
                remaining -= 1
                if remaining <= 0:
                    raise

    def select_preset_by_walk(
        self,
        target_slot: int,
        *,
        known_slot: int,
        force_reload: bool = False,
        check_every: int = 8,
        settle: float = 0.4,
    ) -> int:
        """Step the unit's preset selection to ``target_slot`` with button 5/7.

        **Both directions exist.**  Virtual button 5 is "previous
        preset" and button 7 is "next preset" -- one slot each, with
        mod-80 wraparound (79 -> 00 and 00 -> 79).  The key map came
        from the OS image (screen 7's action table, `$C0AA44`: key 5 ->
        `$C0B776` which installs the preset screen record `$FFFFAD18`,
        key 7 -> `$C0B770` which installs `$FFFFAD14`, the two adjacent
        prev/next records) and was measured on the unit: button 5 took
        P79 -> P78 five times in a row, button 7 took P79 -> P00 (the
        wrap).  The walk therefore takes the shorter way round -- at
        most 40 presses, usually one or two.

        The walk is verified by reading the working preset, whose seqno
        byte (preset offset 0) is the slot the unit is on; a periodic
        check-read re-synchronises if a press is dropped.

        ``force_reload`` re-selects the target when it is already the
        current slot: one "previous" press and one "next" press -- two
        selection events, the second reloading the target with its
        stored content.  (A preset reload is a preset-select event; a
        dump stores bytes but does not by itself make the unit re-read
        them.)  Two presses, not the eighty the earlier one-directional
        model required.
        """
        if not 0 <= target_slot < 80:
            raise ValueError(
                f"a preset slot is 0..79; the walk was aimed at {target_slot}"
            )
        if not 0 <= known_slot < 80:
            raise ValueError(
                f"a preset slot is 0..79; the walk started from {known_slot}"
            )
        if check_every < 1:
            raise ValueError(
                f"check_every must be at least 1; this one was {check_every}"
            )

        current = known_slot
        last_observed = known_slot
        for _ in range(6):
            down = (current - target_slot) % 80
            up = (target_slot - current) % 80
            if down == 0 and up == 0:
                observed = self._on_the_target(target_slot, force_reload, settle)
                last_observed = observed
                if observed == target_slot:
                    return target_slot
                current = observed
                continue
            if down <= up:
                button, step, count = PRESET_PREV_BUTTON, -1, down
            else:
                button, step, count = PRESET_NEXT_BUTTON, 1, up
            observed = self._walk_one_round(
                current, target_slot, button, step, count, check_every, settle
            )
            last_observed = observed
            if observed == target_slot:
                return target_slot
            current = observed

        raise PresetSelectionError(
            f"the preset walk did not converge: the unit is on "
            f"P{last_observed:02d} (observed slot P{last_observed:02d}), "
            f"not P{target_slot:02d}",
            observed_slot=last_observed,
        )

    def _on_the_target(
        self, target_slot: int, force_reload: bool, settle: float
    ) -> int:
        """Already on the target slot: optionally reload, then read it back.

        A reload is one "previous" press and one "next" press -- two
        selection events, the second one re-reading the slot.
        """
        if force_reload:
            self._press_release(PRESET_PREV_BUTTON)
            self._press_release(PRESET_NEXT_BUTTON)
            time.sleep(settle)
        return self._read_walk_slot()

    def _press_release(
        self, button: int, *, hold: float = 0.12, gap: float = 0.4
    ) -> None:
        """One press and release of a virtual button."""
        self._out.send_message(
            list(m.virtual_button(button, down=True, channel=self.channel))
        )
        time.sleep(hold)
        self._out.send_message(
            list(m.virtual_button(button, down=False, channel=self.channel))
        )
        time.sleep(gap)

    def toggle_live_mute(self, *, hold: float = 0.30) -> None:
        """Toggle the live mute of the track under the panel's cursor.

        **This is the panel's own gesture, not a preset edit.**  The two
        Track keys pressed together mute or unmute the track the
        panel's cursor sits on: the display's track number turns into
        "-" and that track's notes fall silent, while the preset's
        bytes do not change at all (measured 2026-10-23).  The audio
        gate is the RAM mask ``$FFFFD718``, which a dump never writes
        (``docs/KEY-MAP.md`` §3), so this is the only way to change what
        the unit plays without touching the stored preset.

        **The app cannot read the panel's cursor.**  There is no
        read-back for it, so this toggles *whichever track the panel is
        pointing at*, not a track the caller names.  A caller must say
        so; a screen that shows eight tracks must not pretend this
        action picks one of them.

        The chord is the two presses down, a hold, then the two
        releases -- one selection gesture, as the panel does it.
        """
        for button in (TRACK_LEFT_BUTTON, TRACK_RIGHT_BUTTON):
            self._out.send_message(
                list(m.virtual_button(button, down=True, channel=self.channel))
            )
            time.sleep(0.02)
        time.sleep(hold)
        for button in (TRACK_LEFT_BUTTON, TRACK_RIGHT_BUTTON):
            self._out.send_message(
                list(m.virtual_button(button, down=False, channel=self.channel))
            )
            time.sleep(0.02)

    def tap_button(self, number: int, *, hold: float = 0.12) -> None:
        """One press (down) and one release (up) of a virtual button.

        The remote window's single key: the same wire frames as the
        panel's own buttons, sent through this bridge rather than by
        hand.  The gate that decides whether the press may go is the
        caller's business -- this method sends what it is asked to send,
        and the unit answers a button number it does not recognise
        (14 and 15, in particular) with error 02, which the caller's
        exchange machinery reports.

        The release is the button number plus
        :data:`sq.messages.CommandType.BUTTON_UP_OFFSET` (96), as the
        specification asks for one up for every down.
        """
        self._out.send_message(
            list(m.virtual_button(number, down=True, channel=self.channel))
        )
        time.sleep(hold)
        self._out.send_message(
            list(m.virtual_button(number, down=False, channel=self.channel))
        )

    def _walk_one_round(
        self,
        start: int,
        target: int,
        button: int,
        step: int,
        count: int,
        check_every: int,
        settle: float,
    ) -> int:
        """Walk ``count`` presses with ``button``, checking every ``check_every``.

        ``step`` is -1 for the previous button and +1 for the next one;
        after k presses the expected slot is ``(start + step*k) mod 80``.
        Returns the target on success, or the observed slot on a
        mismatch (the caller re-synchronises).
        """
        walked = 0
        while walked < count:
            chunk = min(check_every, count - walked)
            for _ in range(chunk):
                self._press_release(button)
                walked += 1
            time.sleep(settle)
            expected = (start + step * walked) % 80
            observed = self._read_walk_slot()
            if observed != expected:
                return observed
            if walked == count:
                return target
        return self._read_walk_slot()

    def _read_walk_slot(self) -> int:
        """Read the working preset and return its seqno byte (offset 0)."""
        current = self.read_current_preset()
        if not current.preset_raw:
            raise DeviceError("the unit's preset dump is empty")
        return current.preset_raw[0]

    def store_current_slot(
        self,
        seq_blob: bytes,
        preset_raw: bytes,
        *,
        attempts: int = 3,
        timeout: float = SEQ_TIMEOUT,
    ) -> "PresetSent":
        """Store the whole working buffer into the slot the unit is on.

        **Measured on the unit, 2026-10-23.**  A dump alone stores only
        the live engine's 120-byte track-and-pile snapshot (offsets
        32-151); the effect block (152-163) and the name are outside the
        commit (``docs/PRESET-FRAMING.md`` §9.7).  The panel's save
        dialog stores the *whole* buffer, and it can be driven blind: a
        dump leaves an edit pending, and then three presses of
        Enter/Save walk the manual's steps -- "Save Preset <name>?"
        (the name is left alone), the slot selection (defaulting to the
        slot the unit is on), and the confirmation, after which the
        display says *SAVED*.

        Verified end to end with an effect edit: FX1 MIX 42 -> 100 sent,
        the three presses, then a reload (one "previous" and one "next"
        press), and the effect read back as 100 -- it survived the
        reload, so the slot's stored bytes changed.

        **The verification is the reload, not the read-back**: command
        03 returns the buffer, which holds the edit either way.  After
        the store this method re-selects the slot and compares the
        read-back with what was sent, skipping :data:`UNIT_KEPT_OFFSETS`
        -- the regions the unit keeps its own values in.  A mismatch is
        a :class:`PresetMismatch`.

        Blind by nature: the dialog's state cannot be read, so the
        caller must have just sent a dump (an edit pending), and the
        owner should be watching the display.  If a press is dropped the
        dialog may be left open; ``select``/the panel's Select Preset
        closes it.
        """
        self.send_preset_with_retry(
            seq_blob, preset_raw, attempts=attempts, timeout=timeout
        )
        # The manual's save steps: the dialog, the name page, the slot
        # selection, the confirmation.
        for _ in range(3):
            self._press_release(ENTER_SAVE_BUTTON)
        # Leave and return, so the read-back is the slot's stored bytes.
        self._press_release(PRESET_PREV_BUTTON)
        self._press_release(PRESET_NEXT_BUTTON)
        time.sleep(PRESET_SETTLE)
        read_back = self.read_current_preset(timeout=timeout)
        if len(read_back.seq_blob) != len(seq_blob):
            raise DeviceError(
                f"the unit's sequence data is {len(read_back.seq_blob)} "
                f"bytes after {len(seq_blob)} bytes were sent"
            )
        expected = seq_blob + preset_raw
        actual = read_back.seq_blob + read_back.preset_raw
        diff_offsets = tuple(
            index
            for index in range(len(expected))
            if expected[index] != actual[index]
            and (
                index < len(seq_blob) or index - len(seq_blob) not in UNIT_KEPT_OFFSETS
            )
        )
        result = PresetSent(
            acked=True,
            verified=not diff_offsets,
            diff_offsets=diff_offsets,
            read_back=read_back,
        )
        if diff_offsets:
            raise PresetMismatch(self._mismatch_sentence(diff_offsets), result)
        return result

    def send_current_preset(
        self,
        seq_blob: bytes,
        preset_raw: bytes,
        *,
        verify: bool = True,
        timeout: float = SEQ_TIMEOUT,
    ) -> "PresetSent":
        """The name the plan gave this method; :meth:`send_preset` is it.

        Kept so that the name in the implementation plan works, and so
        that the two cannot drift apart: this does nothing but call the
        other.
        """
        return self.send_preset(seq_blob, preset_raw, verify=verify, timeout=timeout)

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
