# SPDX-License-Identifier: GPL-2.0-or-later
#
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# ensqsqed is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation as published by the Free Software Foundation; either
# version 2 of the License, or (at your option) later version.
#
# ensqsqed is distributed in the hope that it will be distributed under the
# terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# later version.

"""The import model: reading a file, validating assignments, merging banks."""

from __future__ import annotations

import struct
from typing import Dict

import pytest

from sq import banks, importer
from sq import messages as m


def _vlq(value: int) -> bytes:
    """A Standard MIDI File variable-length quantity."""
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.insert(0, (value & 0x7F) | 0x80)
        value >>= 7
    return bytes(out)


def _record(name: str) -> bytes:
    """An invented 204-byte sound record, with an invented name in it."""
    record = bytearray(m.SOUND_BYTES)
    m.encode_name(record, name)
    return bytes(record)


def _all_sounds_message(device: int = 0) -> bytes:
    payload = b"".join(
        m.to_nibbles(_record(f"Inv{slot:03d}"))
        for slot in range(banks.SOUNDS_PER_GROUP)
    )
    return m.frame(m.Function.ALL_SOUNDS_DUMP, payload, device=device)


def _smf(message: bytes) -> bytes:
    """A one-track Standard MIDI File wrapping one SysEx message."""
    count = len(message) - 1  # everything after the F0
    body = b"\x00\xf0" + _vlq(count) + message[1:]
    track = b"MTrk" + struct.pack(">I", len(body)) + body
    return b"MThd" + struct.pack(">I", 6) + b"\x00\x00\x00\x01\x00\x60" + track


class TestReadImport:
    def test_an_all_sounds_dump_reads_as_a_full_bank(self, tmp_path):
        path = tmp_path / "favs.syx"
        path.write_bytes(_all_sounds_message())

        source = importer.read_import(str(path))

        assert source.is_full_bank
        assert source.kinds == ("all-sounds",)
        assert source.device == 0
        assert len(source.sounds) == banks.SOUNDS_PER_GROUP
        assert source.summary() == f"80 sounds (full bank) from {path}"

        for slot in range(banks.SOUNDS_PER_GROUP):
            sound = source.sounds[slot]
            assert sound.index == slot
            assert sound.name == f"Inv{slot:03d}"
            assert len(sound.raw) == m.SOUND_BYTES
            assert sound.raw == _record(f"Inv{slot:03d}")

    def test_a_singles_dump_reads_the_whole_file(self, tmp_path):
        blob = b"".join(
            m.frame(
                m.Function.SINGLE_SOUND_DUMP,
                m.to_nibbles(_record(f"Inv{index:03d}")),
                device=0,
            )
            for index in range(2)
        )
        path = tmp_path / "one.syx"
        path.write_bytes(blob)

        source = importer.read_import(str(path))

        assert source.kinds == ("single-sound",)
        assert not source.is_full_bank
        assert len(source.sounds) == 2
        assert [sound.index for sound in source.sounds] == [0, 1]
        assert [sound.name for sound in source.sounds] == ["Inv000", "Inv001"]

    def test_a_midi_file_is_read_the_same_way(self, tmp_path):
        path = tmp_path / "favs.mid"
        path.write_bytes(_smf(_all_sounds_message(device=9)))

        source = importer.read_import(str(path))

        assert source.is_full_bank
        assert source.device == 9
        assert len(source.sounds) == banks.SOUNDS_PER_GROUP
        assert source.sounds[0].name == "Inv000"

    def test_a_file_that_holds_no_sound_is_refused(self, tmp_path):
        path = tmp_path / "nothing.syx"
        path.write_bytes(b"")

        with pytest.raises(ValueError):
            importer.read_import(str(path))


class TestValidateAssignments:
    def test_no_assignments_is_valid(self):
        assert importer.validate_assignments({}) == []

    def test_ordinary_assignments_are_valid(self):
        assert importer.validate_assignments({0: 0, 3: 12}) == []

    def test_a_duplicate_target_is_reported(self):
        problems = importer.validate_assignments({0: 4, 5: 4})
        assert len(problems) == 1
        assert "4" in problems[0]

    def test_an_out_of_range_target_is_reported(self):
        assert importer.validate_assignments({0: 80}) != []
        assert importer.validate_assignments({5: -1}) != []


class TestMergeBank:
    def test_assigned_slots_are_overwritten(self):
        merged = importer.merge_bank({0: b"old", 1: b"keep"}, {0: b"new"})

        assert merged == {0: b"new", 1: b"keep"}

    def test_unassigned_slots_are_preserved(self):
        existing: Dict[int, bytes] = {
            slot: bytes([slot]) * m.SOUND_BYTES
            for slot in range(banks.SOUNDS_PER_GROUP)
        }
        merged = importer.merge_bank(existing, {3: b"new"})

        assert len(merged) == banks.SOUNDS_PER_GROUP
        assert merged[3] == b"new"
        assert merged[0] == existing[0]


class TestSourceSound:
    def test_the_record_is_the_decoded_204_bytes(self, tmp_path):
        path = tmp_path / "favs.syx"
        path.write_bytes(_all_sounds_message())
        source = importer.read_import(str(path))

        assert source.sounds[0].raw == _record("Inv000")
        assert len(source.sounds[0].raw) == 204
