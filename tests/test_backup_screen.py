# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
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

"""The backup selection screen: choices, defaults and its start/exit keys."""

from __future__ import annotations

from itertools import combinations

from textual.app import App, ComposeResult
from textual.widgets import Checkbox, Label, RadioButton, RadioSet, Static

from ensqsqed.app import BackupReportScreen, BackupSelectionScreen, EnsqsqedApp
from ensqsqed.demo import DemoDevice
from sq import catalog as cat
from sq import preset_labels


class _Host(App):
    def compose(self) -> ComposeResult:
        yield Label("host")


class _Everything:
    def wire(self) -> bytes:
        return b"everything"


class _CombinationBridge:
    def __init__(self) -> None:
        self.sounds_calls = []
        self.presets_calls = []
        self.everything_calls = 0

    def backup_sounds(self, group: str) -> bytes:
        self.sounds_calls.append(group)
        return b"sounds"

    def backup_presets(self, group: str) -> bytes:
        self.presets_calls.append(group)
        if group != "INT":
            raise AssertionError("card presets are not directly dumpable")
        return b"presets"

    def read_everything(self):
        self.everything_calls += 1
        return _Everything()


class _Sounds:
    def backup_sounds(self, group: str) -> bytes:
        assert group == "INT"
        return b"sounds"


async def test_finished_backup_report_lists_the_file_and_folder(tmp_path) -> None:
    app = EnsqsqedApp(_Sounds(), catalog=cat.empty(), backup_root=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_backup()
        await pilot.pause()
        await pilot.press("s")
        for _ in range(100):
            await pilot.pause()
            if isinstance(app.screen, BackupReportScreen):
                break
        assert isinstance(app.screen, BackupReportScreen)
        report = app.screen
        for _ in range(100):
            await pilot.pause()
            current = str(report.query_one("#backup-current", Static).content)
            if "backup complete" in current:
                break
        assert "backup complete" in current
        log = str(report.query_one("#backup-log", Static).content)
        assert "OK" in log
        assert "Internal sounds" in log
        assert "internal_sounds_" in log
        assert str(tmp_path) in log
        assert str(tmp_path) in str(report.query_one(".note", Static).content)


async def test_report_screen_keeps_failures_separate_from_successes(
    tmp_path,
) -> None:
    folder = str(tmp_path)
    app = _Host()
    async with app.run_test() as pilot:
        report = BackupReportScreen(folder)
        await app.push_screen(report)
        await pilot.pause()
        report.add_result(True, "Internal sounds", f"{folder}/sounds.bin")
        report.add_result(False, "Card A sounds", "no card fitted")
        report.finish("backup complete: 1 success, 1 failed")
        await pilot.pause()
        log = str(report.query_one("#backup-log", Static).content)
        assert "OK" in log and "FAILED" in log
        assert "Internal sounds" in log
        assert "no card fitted" in log
        assert "1 success, 1 failed" in str(
            report.query_one("#backup-current", Static).content
        )


async def test_presets_without_internal_are_refused() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        screen = BackupSelectionScreen()
        result = []
        await app.push_screen(screen, result.append)
        await pilot.pause()
        for checkbox in screen.query(Checkbox):
            checkbox.value = checkbox.id == "backup-group-carda"
        content = screen.query_one("#backup-content", RadioSet)
        list(content.query(RadioButton))[1].value = True
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert result == []
        assert "Internal-only" in str(
            screen.query_one("#backup-status", Static).content
        )


async def test_every_group_content_and_layout_combination(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        preset_labels, "default_path", lambda: str(tmp_path / "no-labels.json")
    )
    bridge = _CombinationBridge()
    app = EnsqsqedApp(bridge, catalog=cat.empty())
    group_sets = [
        set(combo)
        for count in range(1, 4)
        for combo in combinations(("INT", "CARDA", "CARDB"), count)
    ]
    async with app.run_test() as pilot:
        await pilot.pause()
        for groups in group_sets:
            for content in ("sounds", "presets", "all"):
                for separate in (False, True):
                    folder = tmp_path / (
                        "-".join(sorted(groups)) + f"-{content}-{separate}"
                    )
                    app._do_backup(
                        set(groups), content, str(folder), separate_files=separate
                    )
                    await pilot.pause()
                    names = {path.name for path in folder.glob("*")}
                    has_int = "INT" in groups
                    combined = content == "all" and has_int and not separate

                    if content == "presets" and not has_int:
                        assert names == set()
                        continue
                    if combined:
                        assert any(name.startswith("everything_") for name in names)
                    else:
                        if content in ("sounds", "all"):
                            for group in groups:
                                prefix = "internal" if group == "INT" else group.lower()
                                assert any(
                                    name.startswith(f"{prefix}_sounds_")
                                    for name in names
                                )
                        if content in ("presets", "all") and has_int:
                            assert any(
                                name.startswith("internal_presets_") for name in names
                            )
                    assert not any(
                        name.startswith("carda_presets_")
                        or name.startswith("cardb_presets_")
                        for name in names
                    )
        assert "CARDA" not in bridge.presets_calls
        assert "CARDB" not in bridge.presets_calls


async def test_b_opens_the_screen_with_the_configured_folder(tmp_path) -> None:
    app = EnsqsqedApp(DemoDevice(), catalog=cat.empty(), backup_root=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_backup()
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, BackupSelectionScreen)
        assert screen._backup_root == str(tmp_path)
        assert "Back up the SQ-R" in str(screen.query_one(".title", Label).content)
        where = str(screen.query_one("#backup-where", Static).content)
        assert str(tmp_path) in where


async def test_defaults_are_all_groups_sounds_and_combined() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        screen = BackupSelectionScreen()
        await app.push_screen(screen)
        await pilot.pause()
        assert screen._selected_groups == {"INT"}
        assert screen._selected_content == "sounds"
        assert screen._separate_files is False
        assert [checkbox.value for checkbox in screen.query(Checkbox)] == [
            True,
            False,
            False,
        ]
        hint = str(screen.query_one(".hint", Static).content)
        assert "s or ctrl+s" in hint
        assert "start backup" in hint
        assert "esc" in hint


async def test_s_starts_the_backup() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        screen = BackupSelectionScreen()
        result = []
        await app.push_screen(screen, result.append)
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert result == [
            {
                "groups": {"INT"},
                "content": "sounds",
                "separate_files": False,
                "backup_root": "",
            }
        ]


async def test_choices_are_collected_and_ctrl_s_starts() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        screen = BackupSelectionScreen()
        result = []
        await app.push_screen(screen, result.append)
        await pilot.pause()
        for checkbox in screen.query(Checkbox):
            if checkbox.id in ("backup-group-carda", "backup-group-cardb"):
                checkbox.value = False
        content = screen.query_one("#backup-content", RadioSet)
        list(content.query(RadioButton))[2].value = True
        layout = screen.query_one("#backup-files", RadioSet)
        list(layout.query(RadioButton))[1].value = True
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert result == [
            {
                "groups": {"INT"},
                "content": "all",
                "separate_files": True,
                "backup_root": "",
            }
        ]


async def test_no_groups_keeps_the_screen_open_and_explains() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        screen = BackupSelectionScreen()
        result = []
        await app.push_screen(screen, result.append)
        await pilot.pause()
        for checkbox in screen.query(Checkbox):
            checkbox.value = False
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert result == []
        assert "Choose at least one group" in str(
            screen.query_one("#backup-status", Static).content
        )


async def test_cancelling_clears_the_browsers_status(tmp_path) -> None:
    app = EnsqsqedApp(DemoDevice(), catalog=cat.empty(), backup_root=str(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_backup()
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        status = app.query_one("#status", Static)
        assert str(status.content) == ""
        assert not status.has_class("refused")


async def test_q_also_cancels_without_a_result() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        screen = BackupSelectionScreen()
        result = []
        await app.push_screen(screen, result.append)
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        assert result == [None]


async def test_escape_cancels_without_a_result() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        screen = BackupSelectionScreen()
        result = []
        await app.push_screen(screen, result.append)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert result == [None]
