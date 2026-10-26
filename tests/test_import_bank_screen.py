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

"""The whole-bank import preview: every name, the bank, the gate, the answer.

Only the screen is exercised.  The file is never opened, no unit is
touched and no MIDI is sent: the screen is handed a path, a summary and
a list of names, which is exactly what the app hands it after reading
the import source.

The checks pin the things the app relies on: the table has one row per
source sound with the panel's zero-based numbers, a bank chosen with
``b`` is the one ``ctrl+s`` answers, Enter is not a start key, a locked
gate keeps the screen open until ``w`` arms it, ``escape``/``q`` answer
``None``, and a name whose bytes contain a bracket is data rather than
markup.
"""

from __future__ import annotations

from typing import List, Optional

from rich.text import Text
from textual.app import App, ComposeResult
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Label, Static

from ensqsqed.choices import ChoicePicker
from ensqsqed.gate import EditGate
from ensqsqed.import_bank import (
    BANK_LABELS,
    HINT,
    WRITABLE_BANKS,
    BankImportScreen,
)

#: A full bank, with invented names -- the repo never carries the real
#: ones.
NAMES = [f"S{index:02d}" for index in range(80)]


class _Host(App[None]):
    """An app to hold the modal screen, with no bindings of its own."""

    def compose(self) -> ComposeResult:
        yield Label("host")


def _table(screen: BankImportScreen) -> DataTable:
    """The screen's own table."""
    return screen.query_one(f"#{BankImportScreen.TABLE_ID}", DataTable)


def _target_line(screen: BankImportScreen) -> str:
    """What the target line says now."""
    label = screen.query_one(f"#{BankImportScreen.TARGET_ID}", Static)
    return str(label.content)


def _gate_line(screen: BankImportScreen) -> str:
    """What the gate note says now."""
    note = screen.query_one(f"#{BankImportScreen.GATE_ID}", Static)
    return str(note.content)


def _status(screen: BankImportScreen) -> str:
    """What the screen's own status line says."""
    return str(screen.query_one(".status", Static).content)


def _is_refused(screen: BankImportScreen) -> bool:
    """Whether the status line is drawing the family's alarm."""
    return screen.query_one(".status", Static).has_class("refused")


def _screen(
    *,
    names: Optional[List[str]] = None,
    target: str = "INT",
    gate: Optional[EditGate] = None,
    path: str = "x.syx",
) -> BankImportScreen:
    return BankImportScreen(
        path=path,
        summary="80 sounds (full bank)",
        names=NAMES if names is None else names,
        target=target,
        gate=gate,
    )


def test_only_internal_is_writable_over_sysex() -> None:
    """A card is copied from Internal on the panel, not written by SysEx."""
    assert WRITABLE_BANKS == ("INT",)
    assert BANK_LABELS["INT"] == "Internal (RAM)"


def test_an_unwritable_target_falls_back_to_internal() -> None:
    """ROM and DRUM are not writable, so an unknown target is ignored."""
    screen = BankImportScreen(
        path="x.syx", summary="80 sounds (full bank)", names=NAMES, target="ROM"
    )
    assert screen.target == "INT"


def test_the_start_key_is_ctrl_s_and_enter_is_not_bound() -> None:
    """The hint and the bindings agree: ``ctrl+s`` starts, ``⏎`` does not."""
    keys = {binding.key for binding in BankImportScreen.BINDINGS}
    assert "ctrl+s" in keys
    assert "enter" not in keys
    assert {"escape", "q", "w"} <= keys
    assert "b" not in keys

    assert "ctrl+s start" in HINT
    assert "esc / q cancel" in HINT


async def test_the_table_lists_every_source_sound() -> None:
    """Eighty sounds, numbered 00..79, every name rendered."""
    answered: List[Optional[str]] = []
    app = _Host()
    async with app.run_test(size=(100, 40)) as pilot:
        screen = _screen()
        await app.push_screen(screen, answered.append)
        await pilot.pause()

        table = _table(screen)
        assert table.row_count == 80
        assert table.get_cell_at(Coordinate(0, 0)) == "00"
        assert table.get_cell_at(Coordinate(79, 0)) == "79"
        for index, name in enumerate(NAMES):
            assert table.get_cell_at(Coordinate(index, 1)) == name

        # The file and what was found in it are on the screen too, and a
        # loading line says the screen is doing something.
        assert "x.syx" in str(screen.query_one(".path", Static).content)
        assert "80 sounds" in str(screen.query_one(".summary", Static).content)
        assert "Loading sounds" in str(screen.query_one(".loading", Static).content)

        # Nothing has been answered yet.
        assert answered == []


async def test_the_target_starts_on_the_internal_bank_and_says_so() -> None:
    """The panel's word for INT is on the line the app and the user read."""
    app = _Host()
    async with app.run_test(size=(100, 40)) as pilot:
        screen = _screen()
        await app.push_screen(screen)
        await pilot.pause()
        assert screen.target == "INT"
        assert "Internal (RAM)" in _target_line(screen)


async def test_enter_does_not_answer() -> None:
    """The focused table must not turn ``⏎`` into a start key."""
    answered: List[Optional[str]] = []
    app = _Host()
    async with app.run_test(size=(100, 40)) as pilot:
        screen = _screen()
        await app.push_screen(screen, answered.append)
        await pilot.pause()
        assert _table(screen).has_focus
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen is screen
        assert answered == []


async def test_ctrl_s_without_a_gate_answers() -> None:
    """A caller that checks the gate itself gets its answer unchanged."""
    answered: List[Optional[str]] = []
    app = _Host()
    async with app.run_test(size=(100, 40)) as pilot:
        screen = _screen()
        await app.push_screen(screen, answered.append)
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert answered == ["INT"]


async def test_a_locked_gate_refuses_and_keeps_the_screen_open() -> None:
    """``ctrl+s`` on a locked gate says why and does not dismiss."""
    answered: List[Optional[str]] = []
    gate = EditGate()
    app = _Host()
    async with app.run_test(size=(100, 40)) as pilot:
        screen = _screen(gate=gate)
        await app.push_screen(screen, answered.append)
        await pilot.pause()

        assert not gate.armed
        assert _gate_line(screen) == "Write gate: locked (press w)"

        await pilot.press("ctrl+s")
        await pilot.pause()

        assert app.screen is screen, "the screen stayed open"
        assert answered == [], "nothing was answered"
        said = _status(screen)
        assert "write gate locked" in said
        assert "press w" in said
        assert _is_refused(screen)


async def test_w_arms_the_gate_and_then_ctrl_s_answers() -> None:
    """``w`` flips the object the app holds, and the second start key works."""
    answered: List[Optional[str]] = []
    gate = EditGate()
    app = _Host()
    async with app.run_test(size=(100, 40)) as pilot:
        screen = _screen(gate=gate)
        await app.push_screen(screen, answered.append)
        await pilot.pause()

        await pilot.press("w")
        await pilot.pause()
        assert gate.armed, "the screen toggled the app's gate, not a copy"
        assert _gate_line(screen) == "Write gate: ARMED"

        await pilot.press("w")
        await pilot.pause()
        assert not gate.armed
        assert _gate_line(screen) == "Write gate: locked (press w)"

        await pilot.press("w")
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert answered == ["INT"]


async def test_w_without_a_gate_says_there_is_none() -> None:
    """No gate handed in means ``w`` says so rather than flipping nothing."""
    answered: List[Optional[str]] = []
    app = _Host()
    async with app.run_test(size=(100, 40)) as pilot:
        screen = _screen()
        await app.push_screen(screen, answered.append)
        await pilot.pause()

        assert screen.gate is None
        assert _gate_line(screen) == "Write gate: not held by this screen"
        await pilot.press("w")
        await pilot.pause()
        assert app.screen is screen
        assert "no write gate to toggle" in _status(screen)
        assert _is_refused(screen)


async def test_escape_answers_none() -> None:
    """Backing out of the preview imports nothing."""
    answered: List[Optional[str]] = []
    app = _Host()
    async with app.run_test(size=(100, 40)) as pilot:
        screen = _screen()
        await app.push_screen(screen, answered.append)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert answered == [None]


async def test_q_answers_none() -> None:
    """``q`` is the family's second cancel key."""
    answered: List[Optional[str]] = []
    app = _Host()
    async with app.run_test(size=(100, 40)) as pilot:
        screen = _screen()
        await app.push_screen(screen, answered.append)
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        assert answered == [None]


async def test_a_name_with_brackets_is_data_not_markup() -> None:
    """A ``[b]`` in a sound name is shown, not read as a tag."""
    names = ["plain", "[b]not bold[/b]", *NAMES[2:]]
    app = _Host()
    async with app.run_test(size=(100, 40)) as pilot:
        screen = _screen(names=names, path="take[1].syx")
        await app.push_screen(screen)
        await pilot.pause()

        table = _table(screen)
        assert table.row_count == len(names)
        cell = str(table.get_cell_at(Coordinate(1, 1)))
        # The escaping is what keeps the table from treating the name
        # as markup, and the rendered text is the name itself.
        assert Text.from_markup(cell).plain == "[b]not bold[/b]"
        assert "take[1].syx" in str(screen.query_one(".path", Static).content)
