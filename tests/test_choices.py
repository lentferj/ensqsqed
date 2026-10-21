# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# ensqsqed is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version.
#
# ensqsqed is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
# more details.

"""The choice list: the row under the cursor is the row that is answered.

The shared screen and its table each moved the cursor, so the highlight
ran one row ahead of the answer.  These tests pin the two together: the
gesture count, the marker and the index that is dismissed.
"""

from __future__ import annotations

from typing import cast

from textual.app import App
from textual.coordinate import Coordinate
from textual.widgets import DataTable

from ensqsqed.choices import ChoicePicker
from sq import presets

CHOICES = [f"row {index}" for index in range(13)]


class _Host(App[None]):
    """The least an app can be and still hold the picker."""

    def __init__(self, current: int = 0) -> None:
        super().__init__()
        self.picked: list[int | None] = []
        self._current = current

    def on_mount(self) -> None:
        self.push_screen(
            ChoicePicker("pick one", CHOICES, self._current),
            self.picked.append,
        )


async def test_one_down_moves_one_row() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        picker = cast(ChoicePicker, app.screen)
        table = picker.query_one("#choice-table", DataTable)
        await pilot.press("down")
        await pilot.pause()
        assert table.cursor_row == 1
        assert picker._current == 1
        assert table.get_cell_at(Coordinate(1, 0)) == "[x]"
        assert table.get_cell_at(Coordinate(0, 0)) == "[ ]"


async def test_the_row_under_the_cursor_is_the_answer() -> None:
    """The report's case: move to a row and get that row, not the one above."""
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        picker = cast(ChoicePicker, app.screen)
        table = picker.query_one("#choice-table", DataTable)
        for _ in range(9):
            await pilot.press("down")
        await pilot.pause()
        assert table.cursor_row == 9
        assert picker._current == 9
        # No marker is ever blank, whatever the cursor did.
        marks = [table.get_cell_at(Coordinate(row, 0)) for row in range(len(CHOICES))]
        assert all(mark in ("[x]", "[ ]") for mark in marks)
        assert marks[9] == "[x]"
        await pilot.press("enter")
        await pilot.pause()
        assert app.picked == [9]


async def test_up_and_the_edges_stay_in_step() -> None:
    app = _Host(current=2)
    async with app.run_test() as pilot:
        await pilot.pause()
        picker = cast(ChoicePicker, app.screen)
        table = picker.query_one("#choice-table", DataTable)
        await pilot.press("up")
        await pilot.pause()
        assert table.cursor_row == 1
        assert picker._current == 1
        await pilot.press("up")
        await pilot.press("up")
        await pilot.pause()
        assert table.cursor_row == 0
        assert picker._current == 0
        await pilot.press("enter")
        await pilot.pause()
        assert app.picked == [0]


async def test_the_confirmation_is_the_highlighted_index() -> None:
    """The exact mismatch from the bench: index 9 is PHASER+REVERB."""
    assert presets.EFFECTS[9] == "PHASER+REVERB"
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        for _ in range(9):
            await pilot.press("down")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.picked == [9]
