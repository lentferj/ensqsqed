# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#

"""The all-presets disk cache: round-trip, age, and missing-file behaviour."""

from __future__ import annotations

import os
from pathlib import Path

from sq import preset_cache


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "presets.cache"
    data = bytes(range(256)) * 8

    preset_cache.save(data, str(path))

    assert path.read_bytes() == data
    assert preset_cache.load(str(path)) == data


def test_a_missing_cache_is_none(tmp_path: Path) -> None:
    assert preset_cache.load(str(tmp_path / "missing")) is None
    assert preset_cache.age_seconds(str(tmp_path / "missing")) is None


def test_age_seconds_is_the_file_age(tmp_path: Path) -> None:
    path = tmp_path / "presets.cache"
    path.write_bytes(b"x")
    old = 1_700_000_000.0
    os.utime(path, (old, old))

    age = preset_cache.age_seconds(str(path))

    assert age is not None
    assert age > 0


def test_save_to_an_unwritable_path_is_silent(tmp_path: Path) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("not a directory")
    # The parent cannot be created; the save must not raise.
    preset_cache.save(b"data", str(blocker / "cache"))

    assert preset_cache.load(str(blocker / "cache")) is None
