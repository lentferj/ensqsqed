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

"""The import file browser: rows, navigation and the answer.

Only the screen is exercised.  No file is opened, no unit is touched and
no MIDI is sent: every check runs against a temporary directory holding
a SysEx file, an unrelated text file and a subdirectory.

The checks pin what the app relies on: the table lists ``..``,
directories and every file (with the import extensions first), ``⏎`` on
a directory rebuilds the table without closing the screen, ``⏎`` on a
file answers its absolute path, cancelling answers ``None``, and a
directory that cannot be read is a status line rather than a traceback.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

import pytest
from textual.app import App, ComposeResult
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Label, Static

from ensqsqed.file_picker import (
    PRIORITY_SUFFIXES,
    FilePickerScreen,
    start_folder,
)


class _Host(App[None]):
    """An app to hold the modal screen, with no bindings of its own."""

    def compose(self) -> ComposeResult:
        yield Label("host")


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    """A directory with a SysEx file, a text file and a subdirectory."""
    (tmp_path / "song.syx").write_bytes(b"\xf0\xf7")
    (tmp_path / "notes.txt").write_text("not a MIDI file", encoding="utf-8")
    sub = tmp_path / "Sub"
    sub.mkdir()
    (sub / "inner.mid").write_bytes(b"\x90\x40\x7f")
    return tmp_path


def _table(screen: FilePickerScreen) -> DataTable:
    """The screen's own table."""
    return screen.query_one(f"#{FilePickerScreen.TABLE_ID}", DataTable)


def _listing(screen: FilePickerScreen) -> List[str]:
    """Every row's name, top to bottom."""
    table = _table(screen)
    return [
        str(table.get_cell_at(Coordinate(row, 0))) for row in range(table.row_count)
    ]


def _folder_line(screen: FilePickerScreen) -> str:
    """What the header says the current directory is."""
    label = screen.query_one(f"#{FilePickerScreen.FOLDER_ID}", Static)
    return str(label.content)


def _status(screen: FilePickerScreen) -> str:
    """What the status line says, if anything."""
    label = screen.query_one(f"#{FilePickerScreen.STATUS_ID}", Static)
    return str(label.content)


async def test_start_row_puts_the_cursor_back(folder: Path) -> None:
    """The app reopens the browser on the row it was left on."""
    app = _Host()
    async with app.run_test(size=(100, 40)) as pilot:
        screen = FilePickerScreen(start=str(folder), start_row=2)
        await app.push_screen(screen)
        await pilot.pause()
        assert _table(screen).cursor_row == 2


async def test_the_listing_shows_parent_directories_and_files(folder: Path) -> None:
    """``..`` first, then directories, then files; every file is shown."""
    app = _Host()
    async with app.run_test(size=(100, 40)) as pilot:
        screen = FilePickerScreen(start=str(folder))
        await app.push_screen(screen)
        await pilot.pause()

        # `notes.txt` would sort before `song.syx` alphabetically; the
        # import extensions are ahead of it, and the text file still has
        # a row of its own.
        assert _listing(screen) == ["..", "Sub/", "song.syx", "notes.txt"]
        assert _folder_line(screen) == str(folder)
        assert _status(screen) == ""


async def test_a_sqs_file_sorts_with_the_other_import_extensions(
    tmp_path: Path,
) -> None:
    """SoundQuest banks are listed, and ahead of unrelated files."""
    (tmp_path / "bank.sqs").write_bytes(b"LIST")
    (tmp_path / "song.mid").write_bytes(b"\x90\x40\x7f")
    (tmp_path / "notes.txt").write_text("not a dump", encoding="utf-8")

    assert ".sqs" in PRIORITY_SUFFIXES

    app = _Host()
    async with app.run_test(size=(100, 40)) as pilot:
        screen = FilePickerScreen(start=str(tmp_path))
        await app.push_screen(screen)
        await pilot.pause()

        assert _listing(screen) == ["..", "bank.sqs", "song.mid", "notes.txt"]


async def test_enter_on_a_directory_navigates_in_place(folder: Path) -> None:
    """``⏎`` on a directory rebuilds the table and keeps the screen open."""
    app = _Host()
    async with app.run_test(size=(100, 40)) as pilot:
        screen = FilePickerScreen(start=str(folder))
        await app.push_screen(screen)
        await pilot.pause()

        await pilot.press("down")  # `..` -> `Sub/`
        await pilot.press("enter")
        await pilot.pause()

        assert app.screen is screen
        assert _folder_line(screen) == str(folder / "Sub")
        assert _listing(screen) == ["..", "inner.mid"]

        # The cursor is back on `..`, so one more `⏎` walks out again.
        await pilot.press("enter")
        await pilot.pause()
        assert _listing(screen) == ["..", "Sub/", "song.syx", "notes.txt"]


async def test_enter_on_a_file_answers_its_absolute_path(folder: Path) -> None:
    """The answer is the file the cursor is on, absolute."""
    answered: List[Optional[str]] = []
    app = _Host()
    async with app.run_test(size=(100, 40)) as pilot:
        screen = FilePickerScreen(start=str(folder))
        await app.push_screen(screen, answered.append)
        await pilot.pause()

        await pilot.press("down")  # `..` -> `Sub/`
        await pilot.press("down")  # `Sub/` -> `song.syx`
        await pilot.press("enter")
        await pilot.pause()

        assert answered == [os.path.abspath(folder / "song.syx")]


async def test_cancelling_answers_none(folder: Path) -> None:
    """``escape`` and ``q`` both back out without an answer."""
    answered: List[Optional[str]] = []
    app = _Host()
    async with app.run_test(size=(100, 40)) as pilot:
        screen = FilePickerScreen(start=str(folder))
        await app.push_screen(screen, answered.append)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert answered == [None]

    answered = []
    app = _Host()
    async with app.run_test(size=(100, 40)) as pilot:
        screen = FilePickerScreen(start=str(folder))
        await app.push_screen(screen, answered.append)
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        assert answered == [None]


def test_start_folder_maps_a_file_to_its_parent(folder: Path) -> None:
    """What the app persists after a file is chosen is the file's folder."""
    assert start_folder(str(folder / "song.syx")) == str(folder)


def test_start_folder_keeps_a_directory(folder: Path) -> None:
    """A directory is its own starting folder."""
    assert start_folder(str(folder)) == str(folder)


def test_start_folder_falls_back_to_home(folder: Path) -> None:
    """An empty or unusable path opens at home."""
    assert start_folder("") == str(Path.home())
    assert start_folder(str(folder / "missing.syx")) == str(Path.home())


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read a mode-000 directory")
async def test_an_unreadable_directory_is_a_message_not_a_crash(
    tmp_path: Path,
) -> None:
    """Permission denied leaves the screen open with a readable status."""
    denied = tmp_path / "locked"
    denied.mkdir()
    denied.chmod(0o000)
    try:
        app = _Host()
        async with app.run_test(size=(100, 40)) as pilot:
            screen = FilePickerScreen(start=str(denied))
            await app.push_screen(screen)
            await pilot.pause()

            assert app.screen is screen
            assert _table(screen).row_count == 0
            status = _status(screen)
            assert "Cannot read" in status
            assert str(denied) in status
    finally:
        # Leave the directory removable for pytest's cleanup.
        denied.chmod(0o700)


async def test_a_letter_jumps_to_the_next_matching_entry(folder: Path) -> None:
    """Type-ahead: a letter walks to the next row starting with it."""
    app = _Host()
    async with app.run_test(size=(100, 40)) as pilot:
        screen = FilePickerScreen(start=str(folder))
        await app.push_screen(screen)
        await pilot.pause()
        table = _table(screen)

        # `Sub/` and `song.syx` both start with S: the first press takes
        # the first, the second press the next one.
        await pilot.press("s")
        await pilot.pause()
        assert table.cursor_row == 1
        await pilot.press("s")
        await pilot.pause()
        assert table.cursor_row == 2
        await pilot.press("n")
        await pilot.pause()
        assert table.cursor_row == 3
