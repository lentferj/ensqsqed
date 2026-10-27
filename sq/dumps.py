# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
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

"""Reading what the instrument sent, from ``.syx`` and from ``.mid``.

**Both, because the collection is half one and half the other.** A dump
saved by a sequencer is a Standard MIDI File with the System Exclusive
inside it, and the interesting thing about that is not the wrapper: it is
that a ``.mid`` in this collection turned out to hold *two* messages where
the matching ``.syx`` held one. The single-sound opcode was found that
way. A reader that handles only ``.syx`` would have missed it.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from sq import banks
from sq import messages as m

__all__ = ["Library", "read_file", "read_bytes", "split_sysex", "sysex_from_smf"]


@dataclass
class Library:
    """Whatever a file turned out to hold."""

    #: Sound number (0-79) -> its 204 decoded bytes.
    sounds: Dict[int, bytes] = field(default_factory=dict)
    #: Sounds that arrived on their own, in the order they appeared.
    singles: List[bytes] = field(default_factory=list)
    #: Messages that parsed as Ensoniq SQ but are not dumps this program
    #: knows, counted rather than dropped silently.
    unknown: int = 0
    foreign: int = 0
    #: The base channel the sending instrument was on, if any dump said.
    device: Optional[int] = None

    @property
    def is_full_bank(self) -> bool:
        return len(self.sounds) == banks.SOUNDS_PER_GROUP

    def names(self) -> Dict[int, str]:
        """Every sound name this file carries, by number."""
        out = {}
        for number, record in sorted(self.sounds.items()):
            try:
                out[number] = m.sound_name(record)
            except m.ProtocolError:
                continue
        return out

    def summary(self) -> str:
        parts = []
        if self.sounds:
            parts.append(
                f"{len(self.sounds)} sound(s)"
                + (" -- a full internal bank" if self.is_full_bank else "")
            )
        if self.singles:
            parts.append(f"{len(self.singles)} single sound dump(s)")
        if self.device is not None:
            parts.append(f"from base channel {self.device + 1}")
        if self.unknown:
            parts.append(f"{self.unknown} message(s) of unknown opcode")
        if self.foreign:
            parts.append(f"{self.foreign} foreign message(s)")
        return ", ".join(parts) or "nothing this program recognises"


def split_sysex(raw: bytes) -> List[bytes]:
    """Every complete SysEx message in a blob.

    A trailing ``F0`` with no ``F7`` is dropped rather than raising: that
    is what a transfer cut short looks like, and the messages before it
    are still good.
    """
    out, cursor = [], 0
    while True:
        start = raw.find(b"\xf0", cursor)
        if start < 0:
            break
        end = raw.find(b"\xf7", start)
        if end < 0:
            break
        out.append(raw[start : end + 1])
        cursor = end + 1
    return out


def _varlen(data: bytes, index: int):
    value = 0
    while index < len(data):
        byte = data[index]
        value = (value << 7) | (byte & 0x7F)
        index += 1
        if not byte & 0x80:
            return value, index
    raise m.ProtocolError("a variable-length quantity ran off the end")


def sysex_from_smf(raw: bytes) -> List[bytes]:
    """Every SysEx message inside a Standard MIDI File.

    Two things a naive reader gets wrong, and this is the file format
    this project leans on -- a ``.mid`` here held a message its matching
    ``.syx`` did not, which is how the single-sound opcode was found.

    First, in an SMF the ``F0`` is followed by a variable-length count
    rather than by the message body, so searching for ``F0`` ... ``F7``
    finds the right bytes only by luck.

    Second, and worse: **a long message may be split across a continuing
    ``F7`` event.** The format allows a sequencer to send part of a SysEx
    in the ``F0`` event and the rest in one or more ``F7`` events, and an
    all-sounds dump is 32647 bytes, which is exactly the size that gets
    split. Unjoined, the ``F0`` fragment fails its length check and is
    counted as unknown while the ``F7`` fragments do not parse at all --
    so a perfectly good bank dump reads as noise.

    An ``F7`` event whose first byte is a status byte is an escaped raw
    event, not a continuation, and is left alone.
    """
    out: List[bytes] = []
    index = raw.find(b"MTrk")
    while index >= 0:
        length = struct.unpack(">I", raw[index + 4 : index + 8])[0]
        body = raw[index + 8 : index + 8 + length]
        cursor = 0
        pending: Optional[bytearray] = None
        while cursor < len(body):
            at_f0 = body.find(b"\xf0", cursor)
            at_f7 = body.find(b"\xf7", cursor)
            candidates = [p for p in (at_f0, at_f7) if p >= 0]
            if not candidates:
                break
            at = min(candidates)
            try:
                count, after = _varlen(body, at + 1)
            except m.ProtocolError:
                break
            chunk = body[after : after + count]
            if body[at] == 0xF0:
                if pending is not None:
                    # An F0 while one is open: the previous message was
                    # never terminated. Keep it rather than lose it.
                    out.append(bytes(pending))
                pending = bytearray(b"\xf0") + chunk
            else:
                if pending is None or (chunk and chunk[0] & 0x80):
                    # An escaped raw event, not a continuation.
                    cursor = after + count
                    continue
                pending += chunk
            if pending is not None and pending.endswith(b"\xf7"):
                out.append(bytes(pending))
                pending = None
            cursor = after + count
        if pending is not None:
            out.append(bytes(pending))
        index = raw.find(b"MTrk", index + 8 + length)
    return out


def read_bytes(raw: bytes, *, smf: bool = False) -> Library:
    library = Library()
    messages = sysex_from_smf(raw) if smf else split_sysex(raw)
    for message in messages:
        packet = m.parse(message)
        if packet is None:
            library.foreign += 1
            continue
        if library.device is None:
            library.device = packet.device
        if not packet.size_is_right:
            # The opcode says one thing and the length says another. The
            # length wins: there is no checksum in these messages, so it
            # is the only structural check there is.
            library.unknown += 1
            continue
        if packet.is_all_sounds:
            for number in range(banks.SOUNDS_PER_GROUP):
                chunk = packet.payload[
                    number * m.SOUND_BYTES_PACKED : (number + 1) * m.SOUND_BYTES_PACKED
                ]
                library.sounds[number] = m.from_nibbles(chunk)
        elif packet.is_single_sound:
            library.singles.append(m.from_nibbles(packet.payload))
        else:
            library.unknown += 1
    return library


def read_file(path: str) -> Library:
    """Load a ``.syx``, a ``.mid``, or a raw sound-bank ``.bin``.

    The app's backup writes sound banks as decoded 204-byte records in a
    ``.bin``.  That is not a frame, so it has no ``F0`` for :func:`read_bytes`
    to find; a full 16320-byte file is recognised here as the same thing.
    Preset backups are decoded all-presets data and are not sound banks.
    """
    with open(path, "rb") as handle:
        raw = handle.read()
    if (
        not raw.startswith(b"\xf0")
        and raw[:4] != b"MThd"
        and len(raw) == banks.SOUNDS_PER_GROUP * m.SOUND_BYTES
    ):
        sounds = {
            number: raw[number * m.SOUND_BYTES : (number + 1) * m.SOUND_BYTES]
            for number in range(banks.SOUNDS_PER_GROUP)
        }
        return Library(sounds=sounds)
    return read_bytes(raw, smf=raw[:4] == b"MThd")
