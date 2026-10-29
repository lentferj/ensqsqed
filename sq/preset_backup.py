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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

from sq import messages as m
from sq.bridge import UNIT_KEPT_OFFSETS, AllPresetsDump, DeviceError, PresetAlert

__all__ = [
    "PresetBackup",
    "PresetBackupSlot",
    "PresetRestoreError",
    "read_preset_backup",
    "restore_preset_bank",
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


class PresetRestoreError(DeviceError):
    """A full restore stopped at one slot."""

    def __init__(self, slot: int, message: str) -> None:
        super().__init__(message)
        self.slot = slot


def _select(device, target: int, known: int) -> int:
    """Walk to a slot, retrying a dropped panel press."""
    retry = getattr(device, "select_preset_retry", None)
    if retry is not None:
        return retry(target, known_slot=known)
    last: Optional[BaseException] = None
    for _ in range(3):
        try:
            return device.select_preset_by_walk(target, known_slot=known)
        except Exception as exc:
            last = exc
            current = device.read_current_preset()
            if current.preset_raw:
                known = current.preset_raw[0]
    if last is None:  # pragma: no cover - loop always raises once
        raise DeviceError("preset selection failed without a reason")
    raise last


def restore_preset_bank(
    device,
    backup: PresetBackup,
    *,
    on_slot: Optional[Callable[[int, str], None]] = None,
) -> int:
    """Send and reselect every slot in ``backup``.

    The device owns the wire; the caller owns the gate, the pre-restore
    snapshot and the report.  A plain send leaves the effect parameters in
    the working buffer, so each slot is left and reselected before its
    read-back is compared.  The unit's own regions are skipped in that
    comparison.
    """
    # A previous save may have left its dialog open; PREV/NEXT would then
    # move the dialog's target instead of the preset.  Select Preset closes
    # it and is harmless when none is open.
    tap = getattr(device, "tap_button", None)
    if tap is not None:
        tap(2)
        time.sleep(0.5)
    current = device.read_current_preset()
    if not current.preset_raw:
        raise DeviceError("the unit did not return a current preset")
    current_slot = current.preset_raw[0]
    for item in backup.slots:
        try:
            if current_slot != item.slot:
                current_slot = _select(device, item.slot, current_slot)
            # The measured whole-buffer store: the three Enter/Save presses
            # are what commit the FX block; a plain send + reselect does not.
            device.store_current_slot(item.sequence, item.preset, attempts=3)
            actual = device.read_current_preset()
            if len(actual.seq_blob) != len(item.sequence):
                raise PresetRestoreError(
                    item.slot,
                    f"P{item.slot:02d} sequence is {len(actual.seq_blob)} "
                    f"bytes, expected {len(item.sequence)}",
                )
            expected = item.sequence + item.preset
            actual_bytes = actual.seq_blob + actual.preset_raw
            diff = tuple(
                index
                for index, (want, got) in enumerate(zip(expected, actual_bytes))
                if want != got
                and not (
                    index >= len(item.sequence)
                    and index - len(item.sequence) in UNIT_KEPT_OFFSETS
                )
            )
            if diff:
                first = diff[0]
                raise PresetRestoreError(
                    item.slot,
                    f"P{item.slot:02d} read-back differs at offset(s) {diff[:8]} "
                    f"(expected {expected[first]:#04x}, got "
                    f"{actual_bytes[first]:#04x})",
                )
        except PresetRestoreError:
            raise
        except Exception as exc:
            raise PresetRestoreError(item.slot, str(exc)) from exc
        if on_slot is not None:
            on_slot(item.slot, "sequence + preset sent; reselect commit")
    return len(backup.slots)


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
