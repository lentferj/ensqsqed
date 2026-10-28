# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# ensqsqed is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 2 of the License, or (at your option)
# any later version.

"""The preset-bank backup reader: every slot gets its own sequence."""

from pathlib import Path

import pytest

from sq.preset_backup import PRESET_BYTES, read_preset_backup

FIXTURE = Path(__file__).parent / "fixtures" / "all_seq.bin"


def test_the_fixture_gives_eighty_slots_with_sequences():
    backup = read_preset_backup(str(FIXTURE))
    assert backup.count == 80
    assert [slot.slot for slot in backup.slots] == list(range(80))
    assert all(len(slot.preset) == PRESET_BYTES for slot in backup.slots)
    assert all(len(slot.sequence) >= 4 for slot in backup.slots)
    assert {len(slot.sequence) for slot in backup.slots} <= {48, 54}
    assert [len(backup.slots[slot].sequence) for slot in range(70, 80)] == [54] * 10


def test_a_truncated_file_is_refused_before_midi(tmp_path):
    path = tmp_path / "truncated.bin"
    path.write_bytes(b"\x00" * 10)
    with pytest.raises(ValueError, match="17977"):
        read_preset_backup(str(path))


def test_a_file_with_the_wrong_content_is_refused(tmp_path):
    path = tmp_path / "not-a-preset.bin"
    path.write_bytes(b"\x00" * 17977)
    with pytest.raises(ValueError, match="preset slots"):
        read_preset_backup(str(path))
