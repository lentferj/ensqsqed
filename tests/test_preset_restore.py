# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# ensqsqed is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 2 of the License, or (at your option)
# any later version.

"""The full preset restore worker, against a synthetic unit."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from textual.widgets import Static

from ensqsqed.app import BackupReportScreen, EnsqsqedApp
from sq import catalog as cat
from sq.preset_backup import read_preset_backup
from vinsynlib.ui.confirm import ConfirmScreen

FIXTURE = Path(__file__).parent / "fixtures" / "all_seq.bin"


class _Dump:
    def to_bytes(self) -> bytes:
        return FIXTURE.read_bytes()


class _RestoreDevice:
    def __init__(self) -> None:
        self.channel = 0
        self.select_calls = []
        self.send_calls = []
        self.current = (b"\x00" * 48, b"\x00" * 168)
        self.fail_at = None

    def read_all_presets(self):
        return _Dump()

    def read_current_preset(self):
        return SimpleNamespace(seq_blob=self.current[0], preset_raw=self.current[1])

    def select_preset_by_walk(self, target: int, *, known_slot: int):
        self.select_calls.append((target, known_slot))
        return target

    def store_current_slot(self, seq, preset, *, attempts=3, timeout=None):
        if self.fail_at == len(self.send_calls):
            raise RuntimeError("synthetic write failure")
        self.send_calls.append((seq, preset))
        self.current = (seq, preset)
        return SimpleNamespace(verified=True, kept_preset_offsets=())


async def _run(app, backup, pilot):
    app._gate.armed = True
    app._preset_restore_go(backup, True)
    for _ in range(400):
        await pilot.pause()
        if isinstance(app.screen, BackupReportScreen):
            current = str(app.screen.query_one("#backup-current", Static).content)
            if "preset restore" in current:
                return app.screen
    raise AssertionError("the restore never finished")


async def test_the_import_choice_two_opens_the_preset_confirm():
    app = EnsqsqedApp(_RestoreDevice(), catalog=cat.empty())
    async with app.run_test() as pilot:
        await pilot.pause()
        app._import_path_chosen(2, str(FIXTURE))
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        assert "Restore all 80 Internal presets" in str(
            app.screen.query_one("#confirm-prompt", Static).content
        )


async def test_all_eighty_slots_are_stored_with_their_sequences(tmp_path):
    backup = read_preset_backup(str(FIXTURE))
    device = _RestoreDevice()
    app = EnsqsqedApp(device, catalog=cat.empty(), backup_root=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        report = await _run(app, backup, pilot)
        assert len(device.send_calls) == 80
        assert [seq for seq, _ in device.send_calls] == [
            slot.sequence for slot in backup.slots
        ]
        assert [preset for _, preset in device.send_calls] == [
            slot.preset for slot in backup.slots
        ]
        assert device.select_calls[:2] == [(1, 0), (0, 1)]
        assert device.select_calls[2:] == [(slot, slot - 1) for slot in range(1, 80)]
        log = str(report.query_one("#backup-log", Static).content)
        assert "P00" in log and "P79" in log
        assert "complete: 80/80" in str(
            report.query_one("#backup-current", Static).content
        )
        snapshots = list((tmp_path).glob("pre_restore_*/all_presets_dump.bin"))
        assert len(snapshots) == 1


async def test_a_failure_stops_at_the_slot_and_is_reported(tmp_path):
    backup = read_preset_backup(str(FIXTURE))
    device = _RestoreDevice()
    device.fail_at = 3
    app = EnsqsqedApp(device, catalog=cat.empty(), backup_root=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        report = await _run(app, backup, pilot)
        assert len(device.send_calls) == 3
        assert "P03" in str(report.query_one("#backup-current", Static).content)
        assert "synthetic write failure" in str(
            report.query_one("#backup-log", Static).content
        )
