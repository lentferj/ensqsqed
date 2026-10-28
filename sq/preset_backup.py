# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# ensqsqed is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 2 of the License, or (at your option)
# any later version.

"""Read a preset-bank backup and pair every slot with its sequence data.

A backup written by the app is the decoded 17977-byte all-presets dump:
the track-data table and blobs, the preset table, the 80 preset records and
the 21 global bytes.  The restore path needs more than the preset table: a
whole-preset send carries sequence data too, and the preset's first byte is
the index of its blob in the track table.

This module is deliberately free of Textual and MIDI.  It turns a file into
per-slot records, and leaves all writing to the app.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from sq import messages as m
from sq.bridge import AllPresetsDump, PresetAlert

__all__ = [
    "PresetBackup",
    "PresetBackupSlot",
    "read_preset_backup",
]

#: An all-presets backup has all eighty defined slots.
BACKUP_SLOTS = 80
#: A preset record, verbatim from the dump.
PRESET_BYTES = 168
#: The empty sequence the unit itself returns for a slot with no sequence.
EMPTY_SEQUENCE = b"\x00\x00\x00\x30" + b"\x00" * 44


@dataclass(frozen=True)
class PresetBackupSlot:
    """One slot: its 168-byte preset and its own sequence blob."""

    slot: int
    preset: bytes
    sequence: bytes


@dataclass(frozen=True)
class PresetBackup:
    """A validated preset-bank backup, ready to restore slot by slot."""

    path: str
    slots: Tuple[PresetBackupSlot, ...]

    @property
    def count(self) -> int:
        return len(self.slots)


def _sequence_blob(dump: AllPresetsDump, sequence_number: int) -> bytes:
    """The track-data blob at ``sequence_number`` in the dump's table.

    ``AllPresetsDump.track_blobs`` is in non-zero table order.  The table is
    scanned once to keep the correspondence; a preset's sequence byte points
    at the table index.
    """
    if not 0 <= sequence_number < len(dump.track_table) // 4:
        raise ValueError(
            f"preset sequence {sequence_number} is outside the track table"
        )
    blobs = iter(dump.track_blobs)
    for index in range(len(dump.track_table) // 4):
        offset = struct.unpack(">I", dump.track_table[index * 4 : index * 4 + 4])[0]
        if not offset:
            continue
        try:
            blob = next(blobs)
        except StopIteration as exc:  # pragma: no cover - malformed table
            raise ValueError("the track table has more entries than blobs") from exc
        if index == sequence_number:
            return blob
    raise ValueError(f"there is no sequence blob at index {sequence_number}")


def read_preset_backup(path: str) -> PresetBackup:
    """Read the app's raw all-presets ``.bin`` backup.

    The file is the decoded payload of a type-05 dump, not a SysEx frame.
    A truncated or non-preset file is refused here rather than halfway
    through a restore.
    """
    data = Path(path).read_bytes()
    if len(data) != 17977:
        raise ValueError(f"{path} is {len(data)} bytes; an all-presets backup is 17977")

    alert = PresetAlert(
        command=m.CommandType.ALL_SEQUENCE_ALERT,
        size=0,
        count=BACKUP_SLOTS,
    )
    try:
        dump = AllPresetsDump._from_reply(data, alert)
        offsets = dump.slot_offsets()
    except Exception as exc:
        raise ValueError(f"{path} is not a readable all-presets backup: {exc}") from exc

    if len(offsets) != BACKUP_SLOTS:
        raise ValueError(
            f"{path} defines {len(offsets)} preset slots; expected {BACKUP_SLOTS}"
        )
    if len(set(offsets.values())) != BACKUP_SLOTS:
        raise ValueError(f"{path} has duplicate preset-table entries")

    slots = []
    for slot in range(BACKUP_SLOTS):
        index = offsets.get(slot)
        if index is None or not 0 <= index < len(dump.presets_raw):
            raise ValueError(f"{path} has no preset record for P{slot:02d}")
        preset = dump.presets_raw[index]
        if len(preset) != PRESET_BYTES:
            raise ValueError(
                f"P{slot:02d} in {path} is {len(preset)} bytes; "
                f"a preset is {PRESET_BYTES}"
            )
        try:
            sequence = _sequence_blob(dump, preset[0])
        except ValueError:
            # A zero track entry is a valid empty sequence, not a corrupt
            # whole backup.  Restore it as the unit's own empty blob.
            sequence = EMPTY_SEQUENCE
        slots.append(
            PresetBackupSlot(slot=slot, preset=bytes(preset), sequence=sequence)
        )

    return PresetBackup(path=path, slots=tuple(slots))
