# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#

"""The command-05 full-dump backup path and its no-op store assist."""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from ensqsqed.app import EnsqsqedApp
from sq import catalog as cat
from sq import preset_labels


class _BridgeWithEverything:
    def __init__(self, wire: bytes) -> None:
        self._wire = wire
        self.read_everything_calls = 0
        self.backup_sounds_calls: List[str] = []
        self.backup_presets_calls: List[str] = []

    def read_everything(self, *, timeout=None):
        self.read_everything_calls += 1

        class _Dump:
            def __init__(self, wire):
                self._wire = wire

            def wire(self):
                return self._wire

        return _Dump(self._wire)

    def backup_sounds(self, group: str) -> bytes:
        self.backup_sounds_calls.append(group)
        return b""

    def backup_presets(self, group: str) -> bytes:
        self.backup_presets_calls.append(group)
        return b""


class _BridgeWithoutEverything:
    def __init__(self) -> None:
        self.backup_sounds_calls: List[str] = []
        self.backup_presets_calls: List[str] = []

    def backup_sounds(self, group: str) -> bytes:
        self.backup_sounds_calls.append(group)
        return b"sounds"

    def backup_presets(self, group: str) -> bytes:
        self.backup_presets_calls.append(group)
        return b"presets"


async def test_full_dump_writes_everything_file(tmp_path: Path) -> None:
    wire = b"EVERYTHING-WIRE-BYTES"
    bridge = _BridgeWithEverything(wire)
    app = EnsqsqedApp(bridge, catalog=cat.empty())
    async with app.run_test() as pilot:
        await pilot.pause()
        app._do_backup({"INT"}, "all", str(tmp_path))
        await pilot.pause()
        files = list(tmp_path.glob("everything_*.syx"))
        assert len(files) == 1
        assert files[0].read_bytes() == wire
        assert bridge.read_everything_calls == 1
        assert bridge.backup_sounds_calls == []
        assert bridge.backup_presets_calls == []


async def test_fallback_without_read_everything(tmp_path: Path) -> None:
    bridge = _BridgeWithoutEverything()
    app = EnsqsqedApp(bridge, catalog=cat.empty())
    async with app.run_test() as pilot:
        await pilot.pause()
        app._do_backup({"INT"}, "all", str(tmp_path))
        await pilot.pause()
        assert bridge.backup_sounds_calls == ["INT"]
        assert bridge.backup_presets_calls == ["INT"]
        assert list(tmp_path.glob("everything_*.syx")) == []
        assert list(tmp_path.glob("internal_sounds_*.bin"))
        assert list(tmp_path.glob("internal_presets_*.bin"))


async def test_store_assist_done_skipped_says_nothing_sent() -> None:
    app = EnsqsqedApp(_BridgeWithoutEverything(), catalog=cat.empty())
    recorded: List[str] = []

    def fake_say(message, *, refused=False):
        recorded.append(message)

    app._say_to_editor = fake_say  # type: ignore[assignment]
    async with app.run_test() as pilot:
        await pilot.pause()
        app._store_assist_done(0, None, None, skipped=True)
        await pilot.pause()
    assert recorded
    assert "nothing sent" in recorded[0]
    assert "P00" in recorded[0]


async def test_full_dump_also_writes_the_local_labels(
    tmp_path: Path, monkeypatch
) -> None:
    """The app's labels are user data, not part of the unit dump."""
    source = tmp_path / "preset_labels.json"
    preset_labels.save({3: "BASS", 20: "LEAD"}, str(source))
    monkeypatch.setattr(preset_labels, "default_path", lambda: str(source))
    bridge = _BridgeWithEverything(b"EVERYTHING")
    app = EnsqsqedApp(bridge, catalog=cat.empty())
    backup_root = tmp_path / "backup"
    async with app.run_test() as pilot:
        await pilot.pause()
        app._do_backup({"INT"}, "all", str(backup_root))
        await pilot.pause()
    files = list(backup_root.glob("preset_labels_*.json"))
    assert len(files) == 1
    assert preset_labels.load(str(files[0])) == {3: "BASS", 20: "LEAD"}
