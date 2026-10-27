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

"""The remote screen: a modal mirror of the SQ-R's front panel.

What is asserted is shape -- which number a key carries, which PC key
stands in for it, that the screen draws every label, and that the gate
and the unknown Bank Select behave honestly.  No device is touched.
"""

from __future__ import annotations

from typing import List

from textual.app import App, ComposeResult
from textual.widgets import Label, Static

from ensqsqed.remote import PANEL_KEYS, RemoteScreen


class _Host(App):
    """An app to hold the modal screen, with no bindings of its own."""

    def compose(self) -> ComposeResult:
        yield Label("host")


def test_panel_keys_numbers_and_pc_keys() -> None:
    """Every numbered entry has a unique PC key, and the numbers match."""
    expected = {
        # row one
        "Left": (6, "q"),
        "Right": (8, "w"),
        "Up/YES": (7, "e"),
        "System": (11, "u"),
        "Effects": (13, "i"),
        "Trk L": (10, "o"),
        "Trk R": (12, "p"),
        # row two
        "Enter/Sv": (9, "a"),
        "Bank Sel": (0, "s"),
        "Down/NO": (5, "d"),
        "Sel Snd": (1, "h"),
        "Sel Prst": (2, "j"),
        "Ed Snd": (3, "k"),
        "Ed Trk": (4, "l"),
    }
    actual = {entry.label: (entry.number, entry.pc_key) for entry in PANEL_KEYS}
    assert actual == expected

    seen: set[str] = set()
    for entry in PANEL_KEYS:
        if entry.number is not None:
            assert entry.pc_key not in seen
            seen.add(entry.pc_key)


def test_panel_keys_groups() -> None:
    """The keys split into the two clusters."""
    groups = {entry.group for entry in PANEL_KEYS}
    assert groups == {"left", "right"}


def test_bank_select_is_button_zero() -> None:
    """Bank Select is button 0 -- it cycles the open menu and is inert on presets."""
    bank = next(entry for entry in PANEL_KEYS if entry.label == "Bank Sel")
    assert bank.number == 0
    assert bank.pc_key == "s"
    assert "cycles" in bank.note


def test_copy_effect_is_not_a_key_of_its_own() -> None:
    """Copy Effect is written under Select Sound on the panel."""
    sound = next(entry for entry in PANEL_KEYS if entry.label == "Sel Snd")
    assert "Copy Effect" in sound.note


async def test_renders_entries_for_every_label() -> None:
    """The screen draws one entry per table row."""
    app = _Host()
    async with app.run_test() as pilot:
        screen = RemoteScreen()
        await app.push_screen(screen)
        await pilot.pause()
        rendered = "\n".join(str(widget.content) for widget in screen.query(Static))
        for entry in PANEL_KEYS:
            assert entry.label in rendered, entry.label
            # the PC key is inside the box, in plain brackets that Rich
            # markup cannot eat (u/i/s/d are tags!)
            assert f"({entry.pc_key})" in rendered, entry.pc_key


async def test_the_remote_says_poly_and_omni_run_only_track_one() -> None:
    app = _Host()
    async with app.run_test() as pilot:
        screen = RemoteScreen()
        await app.push_screen(screen)
        await pilot.pause()
        hint = str(screen.query_one(".mode-hint", Static).content)
        assert "MIDI Mode must be MULTI" in hint
        assert "POLY or OMNI" in hint
        assert "only track 1 runs" in hint


async def test_pressing_the_system_key_sends_eleven() -> None:
    """The panel keys are never gated: pressing U sends System."""
    called: List[int] = []
    app = _Host()
    async with app.run_test() as pilot:
        screen = RemoteScreen(on_press=called.append)
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("u")
        await pilot.pause()
        assert called == [11]


async def test_no_write_gate_takes_part_in_the_remote() -> None:
    """The screen has no gate at all: the owner drives the panel."""
    import inspect

    from ensqsqed import remote as module

    parameters = inspect.signature(module.RemoteScreen.__init__).parameters
    assert "gate" not in parameters


async def test_an_unmapped_key_does_nothing() -> None:
    """A key that is not in the table is ignored."""
    called: List[int] = []
    app = _Host()
    async with app.run_test() as pilot:
        screen = RemoteScreen(on_press=called.append)
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("v")
        await pilot.pause()
        assert called == []


async def test_space_calls_on_live_mute() -> None:
    """Space is the live-mute chord -- no gate either."""
    called: List[bool] = []
    app = _Host()
    async with app.run_test() as pilot:
        screen = RemoteScreen(on_live_mute=lambda: called.append(True))
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        assert called == [True]


async def test_space_with_no_callback_says_so() -> None:
    """A screen with no write path says so rather than pretending."""
    app = _Host()
    async with app.run_test() as pilot:
        screen = RemoteScreen()
        await app.push_screen(screen)
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()
        said = str(screen.query_one(".status", Static).content)
        assert "no write path" in said


async def test_escape_closes_the_screen() -> None:
    """Escape dismisses the modal."""
    app = _Host()
    async with app.run_test() as pilot:
        screen = RemoteScreen()
        await app.push_screen(screen)
        await pilot.pause()
        assert app.screen is screen
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is not screen


async def test_the_grid_fits_a_standard_terminal() -> None:
    """Every key box is inside a 100-column screen, and shows its key.

    The first layout was wider than its window (the last box was cut) and
    one row tall for two lines of text (the border ate the key line);
    both are pinned here.
    """
    app = _Host()
    async with app.run_test(size=(100, 40)) as pilot:
        screen = RemoteScreen()
        await app.push_screen(screen)
        await pilot.pause()
        keys = list(screen.query(".key"))
        assert len(keys) == len(PANEL_KEYS)
        for key in keys:
            assert key.region.right <= 100, key.id
            assert key.region.height >= 3, key.id
            assert "(" in str(key.content), key.id


async def test_the_frame_fits_the_buttons_and_centres_them() -> None:
    """The yellow frame hugs the grid on a wide terminal, centred.

    Textual's Horizontal defaults to full width, which made the frame
    stretch across the screen with the keys huddled at its left edge; the
    containers are content-sized now.
    """
    app = _Host()
    async with app.run_test(size=(160, 50)) as pilot:
        screen = RemoteScreen()
        await app.push_screen(screen)
        await pilot.pause()
        frame = screen.query_one("Vertical")
        keys = list(screen.query(".key"))
        assert frame.region.width < 120, "the frame hugs the grid"
        assert frame.region.x > 0, "the frame is centred, not at the edge"
        inner_left = frame.region.x + 2
        inner_right = frame.region.x + frame.region.width - 2
        assert keys[0].region.x >= inner_left
        assert keys[-1].region.x + keys[-1].region.width <= inner_right


async def test_the_two_track_keys_together_are_the_mute_chord() -> None:
    """O and P within the window send the chord, not two taps."""
    pressed: List[int] = []
    muted: List[bool] = []
    app = _Host()
    async with app.run_test() as pilot:
        screen = RemoteScreen(
            on_press=pressed.append, on_live_mute=lambda: muted.append(True)
        )
        await app.push_screen(screen)
        await pilot.pause()
        screen._press_track("o")
        screen._press_track("p")
        await pilot.pause()
        assert muted == [True]
        assert pressed == []


async def test_one_track_key_alone_scrolls_the_cursor() -> None:
    """A single Track key waits, then sends its plain press."""
    pressed: List[int] = []
    muted: List[bool] = []
    app = _Host()
    async with app.run_test() as pilot:
        screen = RemoteScreen(
            on_press=pressed.append, on_live_mute=lambda: muted.append(True)
        )
        await app.push_screen(screen)
        await pilot.pause()
        screen._press_track("o")
        await pilot.pause(0.3)
        assert pressed == [10]
        assert muted == []


async def test_the_keyboard_arrows_drive_the_panel_arrows() -> None:
    """The real arrow keys do the panel's arrow keys, beside the letters."""
    pressed: List[int] = []
    app = _Host()
    async with app.run_test() as pilot:
        screen = RemoteScreen(on_press=pressed.append)
        await app.push_screen(screen)
        await pilot.pause()
        for key in ("left", "right", "up", "down"):
            await pilot.press(key)
        await pilot.pause()
        assert pressed == [6, 8, 7, 5]


async def test_m_toggles_between_select_sound_and_select_preset() -> None:
    """One key walks the panel's two mode keys: buttons 2 then 1."""
    pressed: List[int] = []
    modes: List[str] = []
    app = _Host()
    async with app.run_test() as pilot:
        screen = RemoteScreen(on_press=pressed.append, on_mode=modes.append)
        await app.push_screen(screen)
        await pilot.pause()

        mode_line = screen.query_one("#remote-mode", Static)
        assert "SELECT SOUND" in str(mode_line.content)

        await pilot.press("m")
        await pilot.pause()
        assert pressed == [2], "the first toggle selects preset"
        assert modes == ["PRESET"]
        assert "SELECT PRESET" in str(mode_line.content)

        await pilot.press("M")
        await pilot.pause()
        assert pressed == [2, 1], "the second toggle selects sound"
        assert modes == ["PRESET", "SOUND"]
        assert "SELECT SOUND" in str(mode_line.content)


async def test_set_mode_redraws_the_line() -> None:
    """The browser's M can move the window's line without a keypress."""
    app = _Host()
    async with app.run_test() as pilot:
        screen = RemoteScreen()
        await app.push_screen(screen)
        await pilot.pause()
        screen.set_mode("PRESET")
        await pilot.pause()
        assert "SELECT PRESET" in str(screen.query_one("#remote-mode", Static).content)
