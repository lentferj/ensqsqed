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

from textual.app import App, ComposeResult
from textual.widgets import Checkbox, Label, RadioButton, RadioSet, Static

from ensqsqed.app import BackupSelectionScreen, EnsqsqedApp
from ensqsqed.demo import DemoDevice
from sq import catalog as cat


class _Host(App):
    def compose(self) -> ComposeResult:
        yield Label("host")


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


async def test_defaults_are_all_groups_sounds_and_combined() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        screen = BackupSelectionScreen()
        await app.push_screen(screen)
        await pilot.pause()
        assert screen._selected_groups == {"INT", "CARDA", "CARDB"}
        assert screen._selected_content == "sounds"
        assert screen._separate_files is False
        assert all(checkbox.value for checkbox in screen.query(Checkbox))
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
                "groups": {"INT", "CARDA", "CARDB"},
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
