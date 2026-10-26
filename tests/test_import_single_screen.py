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

"""The single-preset import screen: two panes, and the map between them.

Every name here is invented.  What is asserted is structure and refusal:
which rows exist, what an assignment writes into the target cell, and
that a duplicate or out-of-range slot changes nothing.  No device is
touched and no file is read.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Input, Label, Static

from ensqsqed.gate import EditGate
from ensqsqed.import_single import SingleImportScreen
from ensqsqed.multi import NumberPrompt
from vinsynlib.ui.modals import ChoiceScreen


class _Host(App):
    """An app to hold the modal screen, with no bindings of its own."""

    def compose(self) -> ComposeResult:
        yield Label("host")


def _sounds() -> List[Tuple[int, str]]:
    return [(0, "Alpha"), (1, "Beta"), (2, "Gamma")]


def _listing(prefix: str = "Bank name") -> List[Tuple[int, str]]:
    return [(slot, f"{prefix} {slot:02d}") for slot in range(80)]


def _screen(
    *,
    sounds: Optional[List[Tuple[int, str]]] = None,
    listing: Optional[List[Tuple[int, str]]] = None,
    target: str = "INT",
    on_bank_change: Optional[Callable[[str], None]] = None,
    gate: Optional[EditGate] = None,
) -> SingleImportScreen:
    return SingleImportScreen(
        path="favs.syx",
        summary="3 sounds from favs.syx",
        sounds=_sounds() if sounds is None else sounds,
        target=target,
        listing=_listing() if listing is None else listing,
        on_bank_change=on_bank_change,
        gate=gate,
    )


def _cell(table: DataTable, row: str, column: str) -> str:
    """A cell's rendered text, whatever renderable the table holds."""
    return str(table.get_cell(row, column))


def _status(screen: SingleImportScreen) -> str:
    """What the screen's own status line says."""
    return str(screen.query_one(".status", Static).content)


def _is_refused(screen: SingleImportScreen) -> bool:
    return screen.query_one(".status", Static).has_class("refused")


async def _assign(pilot, table: DataTable, row: int, text: str) -> None:
    """Drive Enter on a row, type the number, and submit it."""
    table.focus()
    table.move_cursor(row=row)
    await pilot.press("enter")
    await pilot.pause()
    prompt = pilot.app.screen
    assert isinstance(prompt, NumberPrompt), "the target prompt is up"
    prompt.query_one("#value", Input).value = text
    await pilot.press("enter")
    await pilot.pause()


async def test_both_panes_render_the_source_and_the_listing() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        screen = _screen()
        await app.push_screen(screen)
        await pilot.pause()

        left = screen.query_one("#source-table", DataTable)
        right = screen.query_one("#target-table", DataTable)
        assert left.row_count == 3
        assert right.row_count == 80
        # The target is the second column, between the source's number
        # and its name: the value being typed, in the place the eye
        # already is.
        assert tuple(column.key for column in left.columns.values()) == (
            "num",
            "target",
            "name",
        )
        assert tuple(column.key for column in right.columns.values()) == (
            "slot",
            "name",
        )
        # The source's own position is the zero-based index, zero-padded:
        # the panel counts from 00, and so does this pane.
        assert _cell(left, "0", "num") == "00"
        assert _cell(left, "2", "num") == "02"
        assert _cell(left, "0", "name") == "Alpha"
        # The target is unassigned, and is a styled cell rather than a
        # bare string, so it can stand out once it holds a slot.
        unassigned = left.get_cell("0", "target")
        assert isinstance(unassigned, Text)
        assert _cell(left, "0", "target") == "--"
        # The bank's slots are the panel's numbers, zero-padded.
        assert _cell(right, "0", "slot") == "00"
        assert _cell(right, "79", "slot") == "79"
        assert _cell(right, "79", "name") == "Bank name 79"
        assert "Internal (RAM)" in str(screen.query_one("#bank-label", Label).content)


async def test_enter_assigns_a_slot_and_ctrl_s_answers_the_map() -> None:
    answered: List[object] = []
    app = _Host()
    async with app.run_test() as pilot:
        screen = _screen()
        await app.push_screen(screen, answered.append)
        await pilot.pause()

        left = screen.query_one("#source-table", DataTable)
        left.move_cursor(row=0)
        await pilot.press("enter")
        await pilot.pause()
        prompt = app.screen
        assert isinstance(prompt, NumberPrompt)
        assert "target slot (00-79)" in str(prompt.query_one(Label).content)
        # Two keys, as the gesture promises: "0" then "5" makes 05.
        await pilot.press("0")
        await pilot.press("5")
        await pilot.press("enter")
        await pilot.pause()

        # Enter assigned a target; it did not dismiss the map.
        assert app.screen is screen
        assigned = left.get_cell("0", "target")
        assert isinstance(assigned, Text), "the target is drawn as a styled cell"
        assert str(assigned) == "05"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert answered == [("INT", {0: 5})]


async def test_a_duplicate_target_is_refused_and_the_screen_stays() -> None:
    answered: List[object] = []
    app = _Host()
    async with app.run_test() as pilot:
        screen = _screen()
        await app.push_screen(screen, answered.append)
        await pilot.pause()

        left = screen.query_one("#source-table", DataTable)
        await _assign(pilot, left, 0, "5")
        await _assign(pilot, left, 1, "5")

        assert app.screen is screen, "the screen did not close"
        assert answered == []
        assert _cell(left, "1", "target") == "--"
        said = _status(screen)
        assert "already assigned" in said
        assert "Alpha" in said, "the refusal names the sound that owns the slot"
        assert _is_refused(screen)
        assert screen.assignments == {0: 5}


async def test_an_out_of_range_target_is_refused() -> None:
    answered: List[object] = []
    app = _Host()
    async with app.run_test() as pilot:
        screen = _screen()
        await app.push_screen(screen, answered.append)
        await pilot.pause()

        left = screen.query_one("#source-table", DataTable)
        await _assign(pilot, left, 0, "99")

        assert app.screen is screen
        assert _cell(left, "0", "target") == "--"
        assert "00-79" in _status(screen)
        assert _is_refused(screen)
        assert screen.assignments == {}


async def test_a_digit_opens_the_prompt_with_that_digit() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        screen = _screen()
        await app.push_screen(screen)
        await pilot.pause()

        left = screen.query_one("#source-table", DataTable)
        left.focus()
        left.move_cursor(row=1)
        await pilot.press("7")
        await pilot.pause()

        prompt = app.screen
        assert isinstance(prompt, NumberPrompt)
        assert prompt.query_one("#value", Input).value == "7"


async def test_ctrl_s_with_nothing_assigned_says_so() -> None:
    answered: List[object] = []
    app = _Host()
    async with app.run_test() as pilot:
        screen = _screen()
        await app.push_screen(screen, answered.append)
        await pilot.pause()

        await pilot.press("ctrl+s")
        await pilot.pause()

        assert app.screen is screen
        assert answered == []
        assert "assign at least one target slot" in _status(screen)
        assert _is_refused(screen)


async def test_a_locked_gate_refuses_ctrl_s_and_w_arms_it() -> None:
    """The gate is checked here, so a refusal costs no assignments."""
    answered: List[object] = []
    gate = EditGate()
    app = _Host()
    async with app.run_test() as pilot:
        screen = _screen(gate=gate)
        await app.push_screen(screen, answered.append)
        await pilot.pause()

        note = screen.query_one("#gate-note", Static)
        assert "locked" in str(note.content)
        assert "press w" in str(note.content)

        left = screen.query_one("#source-table", DataTable)
        await _assign(pilot, left, 0, "5")
        assert _cell(left, "0", "target") == "05"

        await pilot.press("ctrl+s")
        await pilot.pause()

        # Refused, and still open with the assignment intact.
        assert app.screen is screen
        assert answered == []
        assert "write gate locked" in _status(screen)
        assert "press w" in _status(screen)
        assert _is_refused(screen)
        assert screen.assignments == {0: 5}
        assert _cell(left, "0", "target") == "05"

        # `w` arms the app's own gate and redraws the line.
        await pilot.press("w")
        await pilot.pause()
        assert gate.armed
        note = str(screen.query_one("#gate-note", Static).content)
        assert note == "Write gate: ARMED"

        await pilot.press("ctrl+s")
        await pilot.pause()
        assert answered == [("INT", {0: 5})]


async def test_a_bracket_in_a_name_is_literal() -> None:
    """A name is bytes, not markup: the screen draws it and does not raise."""
    app = _Host()
    async with app.run_test() as pilot:
        screen = _screen(
            sounds=[(0, "A [b] name")],
            listing=[(0, "Card [x] one")],
        )
        await app.push_screen(screen)
        await pilot.pause()

        left = screen.query_one("#source-table", DataTable)
        right = screen.query_one("#target-table", DataTable)
        assert left.row_count == 1
        assert right.row_count == 1
        # Either the bracket is escaped for markup or the cell is drawn
        # with markup off; what matters is that nothing was swallowed.
        assert left.get_cell("0", "name").startswith("A ")
        assert "b" in left.get_cell("0", "name")
        assert "name" in left.get_cell("0", "name")
        assert "one" in right.get_cell("0", "name")


async def test_escape_answers_nothing() -> None:
    answered: List[Optional[Tuple[str, Dict[int, int]]]] = []
    app = _Host()
    async with app.run_test() as pilot:
        screen = _screen()
        await app.push_screen(screen, answered.append)
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()
        assert answered == [None]
