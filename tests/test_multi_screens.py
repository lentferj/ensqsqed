# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# The frame layout is derived from System Exclusive dumps produced by the
# instrument itself and from Ensoniq's own MIDI specification; see
# docs/PRESET-FRAMING.md.  Ensoniq's specification for this family was a
# printed document supplied on request and is not available; see
# DISCLAIMER.md.
#
# ensqsqed is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 2 of the License, or (at your option) any later
# version.
#
# ensqsqed is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.

"""The MULTI-mode screens: the preset list, and the eight-track editor.

Every fixture here is invented.  **No name is asserted anywhere**: the
names in a preset are the unit's, and a test that pinned one would break
the day the unit is refilled.  What is asserted is structure -- which
number a row carries, which columns exist, what a gesture changes.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

from textual.app import App, ComposeResult
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Input, Label, Static

from ensqsqed.gate import EditGate
from ensqsqed.multi import (
    COLUMN_VIEWS,
    KEY_HINTS_BLOCK,
    ChoicePicker,
    MultiScreen,
    NumberPrompt,
    PresetListScreen,
    SoundPickerScreen,
    _Column,
)
from sq import catalog as cat
from sq import favorites as fav
from sq.presets import EXTERNAL_STATUS, Preset, Track, sound_word


def _track(index: int) -> Track:
    """One track of the synthetic preset, with the numbers a test needs.

    The volume is stored 64, which the panel prints as 50 -- the
    Note 7 table is not the identity, and a test that treated it as one
    would be testing the wrong thing.
    """
    return Track(
        sound_number=0,
        volume=64,
        pan=-128,
        timbre=32,
        release=0,
        output=4,
        status=1,
        pressure=1,
        midi_channel=index,
        sustain=True,
        midi_program=index,
        key_low=21,
        transpose=0,
        key_high=108,
    )


def _preset() -> Preset:
    """A preset with eight tracks and a pile that says one track is on.

    The pile is the unit's own: byte 0 names the primary track and the
    other seven are zero, which is what every preset in the captured
    dump holds.
    """
    return Preset(
        tracks=[_track(index) for index in range(8)],
        pile=bytes([1, 0, 0, 0, 0, 0, 0, 0]),
    )


class _Host(App):
    """An app to hold the modal screens.

    The screens are modals, and a modal needs an app to be pushed onto.
    This one has no bindings of its own: every key in the tests belongs
    to the screen under test.
    """

    def compose(self) -> ComposeResult:
        yield Label("host")


def _focus(table: DataTable, row: int, key: str) -> None:
    """Put the table's cursor on one cell.

    By key rather than by index: the columns are rebuilt when the view
    changes, and a test that counted them would have to know how many
    there are.
    """
    for index, column in enumerate(table.columns.values()):
        if column.key == key:
            table.move_cursor(row=row, column=index)
            return
    raise AssertionError(f"no column {key!r} in the table")


def _keys(table: DataTable) -> tuple:
    """The column keys of a table, in order."""
    return tuple(column.key for column in table.columns.values())


def _status(screen) -> str:
    """What the screen's own status line says."""
    return str(screen.query_one(".status", Static).content)


class TestPresetList:
    """The list of the eighty presets."""

    async def test_the_rows_carry_the_units_numbers(self):
        raw = _preset().to_bytes()
        app = _Host()
        async with app.run_test() as pilot:
            screen = PresetListScreen([(0, raw), (1, raw), (2, raw)], current=1)
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#preset-table", DataTable)
            assert table.row_count == 3
            # The unit's display counts its preset locations from
            # zero (P00 through P79, the owner's reading of the
            # display on 2026-10-22), and the dump's slots are counted
            # the same way: the row shows the unit's number.
            assert table.get_cell("0", "slot") == "P00"
            assert table.get_cell("2", "slot") == "P02"
            # The current slot is marked, and only the current slot.
            assert table.get_cell("1", "mark") == "←"
            assert table.get_cell("0", "mark") == ""

    async def test_the_name_comes_from_the_preset(self):
        preset = _preset()
        preset.name = "Something"
        app = _Host()
        async with app.run_test() as pilot:
            screen = PresetListScreen([(0, preset.to_bytes())])
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#preset-table", DataTable)
            # The name is shown, whatever the unit wrote in it; this
            # test only says that *something* is there.
            assert table.get_cell("0", "name").strip() != ""

    async def test_picking_a_row_answers_with_its_slot(self):
        raw = _preset().to_bytes()
        picked = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = PresetListScreen([(0, raw), (1, raw), (2, raw)])
            await app.push_screen(screen, picked.append)
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            assert picked == [1]

    async def test_escape_answers_with_nothing(self):
        raw = _preset().to_bytes()
        picked = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = PresetListScreen([(0, raw)])
            await app.push_screen(screen, picked.append)
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert picked == [None]


class TestTheSoundPicker:
    """Every group is reachable, not just the browser's current bank."""

    async def test_every_group_is_reachable(self):
        picked = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = SoundPickerScreen(cat.empty(), current=("INT", 1))
            await app.push_screen(screen, picked.append)
            await pilot.pause()
            groups = screen.query_one("#picker-groups", DataTable)
            slots = screen.query_one("#picker-slots", DataTable)
            assert groups.row_count == 5, "INT, ROM, Card A, Card B, drums"
            assert slots.row_count == 80
            # The track's own group is where it opens; tab reaches the
            # group pane, down moves to ROM, and the slot pane follows.
            await pilot.press("tab")
            await pilot.press("down")
            await pilot.pause()
            assert slots.row_count == 80
            await pilot.press("tab")
            for _ in range(4):
                await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            assert picked == [("ROM", 5)]

    async def test_the_drum_kits_are_a_group_too(self):
        picked = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = SoundPickerScreen(cat.empty(), current=("INT", 1))
            await app.push_screen(screen, picked.append)
            await pilot.pause()
            await pilot.press("tab")
            for _ in range(4):
                await pilot.press("down")
            await pilot.pause()
            slots = screen.query_one("#picker-slots", DataTable)
            assert slots.row_count == 20, "twenty kits, not eighty sounds"
            await pilot.press("tab")
            await pilot.press("enter")
            await pilot.pause()
            assert picked == [("DRUM", 1)]

    async def test_it_opens_on_the_tracks_own_sound(self):
        picked = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = SoundPickerScreen(cat.empty(), current=("CARDA", 7))
            await app.push_screen(screen, picked.append)
            await pilot.pause()
            groups = screen.query_one("#picker-groups", DataTable)
            slots = screen.query_one("#picker-slots", DataTable)
            assert groups.cursor_row == 2, "Card A is the third group"
            assert slots.cursor_row == 6, "slot 007 is the seventh row"
            await pilot.press("enter")
            await pilot.pause()
            assert picked == [("CARDA", 7)]

    async def test_escape_changes_nothing(self):
        picked = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = SoundPickerScreen(cat.empty(), current=("INT", 1))
            await app.push_screen(screen, picked.append)
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert picked == [None]


class TestTheSoundPickerFavourites:
    """`v` steps the same three views the browser's own table has."""

    @staticmethod
    def _store(tmp_path):
        return fav.Favorites(str(tmp_path / "f.db"))

    async def test_v_steps_all_this_group_then_every_favourite(self, tmp_path):
        store = self._store(tmp_path)
        try:
            store.add("INT", 3, name="three")
            store.add("INT", 7, name="seven")
            store.add("CARDA", 5, name="card")
            app = _Host()
            async with app.run_test() as pilot:
                screen = SoundPickerScreen(
                    cat.empty(), current=("INT", 1), favorites=store
                )
                await app.push_screen(screen, lambda result: None)
                await pilot.pause()
                slots = screen.query_one("#picker-slots", DataTable)
                assert slots.row_count == 80, "the first view is all sounds"
                await pilot.press("v")
                await pilot.pause()
                assert slots.row_count == 2, "this group's favourites"
                await pilot.press("v")
                await pilot.pause()
                assert slots.row_count == 3, "every favourite, all groups"
                # The all-groups view names the group in the slot column.
                assert slots.get_cell_at(Coordinate(0, 0)) == "INT 003"
                assert slots.get_cell_at(Coordinate(2, 0)) == "CARDA 005"
        finally:
            store.close()

    async def test_a_favourite_in_another_group_can_be_picked(self, tmp_path):
        store = self._store(tmp_path)
        try:
            store.add("INT", 3, name="three")
            store.add("CARDA", 5, name="card")
            picked = []
            app = _Host()
            async with app.run_test() as pilot:
                screen = SoundPickerScreen(
                    cat.empty(), current=("INT", 1), favorites=store
                )
                await app.push_screen(screen, picked.append)
                await pilot.pause()
                await pilot.press("v")
                await pilot.press("v")
                await pilot.pause()
                await pilot.press("down")
                await pilot.press("enter")
                await pilot.pause()
                assert picked == [("CARDA", 5)]
        finally:
            store.close()

    async def test_a_group_with_no_favourites_is_empty(self, tmp_path):
        store = self._store(tmp_path)
        try:
            store.add("INT", 3, name="three")
            app = _Host()
            async with app.run_test() as pilot:
                screen = SoundPickerScreen(
                    cat.empty(), current=("ROM", 1), favorites=store
                )
                await app.push_screen(screen, lambda result: None)
                await pilot.pause()
                await pilot.press("v")
                await pilot.pause()
                slots = screen.query_one("#picker-slots", DataTable)
                assert slots.row_count == 0, "ROM has no favourites here"
        finally:
            store.close()

    async def test_without_a_favourites_database_v_does_nothing(self):
        app = _Host()
        async with app.run_test() as pilot:
            screen = SoundPickerScreen(cat.empty(), current=("INT", 1))
            await app.push_screen(screen, lambda result: None)
            await pilot.pause()
            await pilot.press("v")
            await pilot.pause()
            slots = screen.query_one("#picker-slots", DataTable)
            assert slots.row_count == 80, "the only view there is"


class TestTheColumns:
    """The four column groups, and the keys in them."""

    async def test_the_first_view_shows_the_sound_the_channel_and_the_program(self):
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                _preset(),
                on_apply=lambda preset, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            assert _keys(table) == (
                "trk",
                "on",
                "spot",
                "name",
                "channel",
                "program",
            )
            assert table.row_count == 8

    async def test_tab_cycles_the_four_column_groups(self):
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                _preset(),
                on_apply=lambda preset, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            seen = []
            for _ in range(len(COLUMN_VIEWS) + 1):
                seen.append(_keys(table))
                await pilot.press("tab")
                await pilot.pause()
            # Four different groups, and the fifth press is the first
            # one again.
            assert len(set(seen[: len(COLUMN_VIEWS)])) == len(COLUMN_VIEWS)
            assert seen[0] == seen[-1]

    async def test_every_view_has_a_track_and_a_state_column(self):
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                _preset(),
                on_apply=lambda preset, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            for _ in range(len(COLUMN_VIEWS)):
                keys = _keys(table)
                assert keys[0] == "trk"
                assert keys[1] == "on"
                await pilot.press("tab")
                await pilot.pause()

    async def test_the_hints_name_the_keys(self):
        assert "tab columns" in KEY_HINTS_BLOCK
        assert "space track layer" in KEY_HINTS_BLOCK


class TestEditing:
    """The gestures, and what they change."""

    async def test_plus_moves_the_volume_and_says_what_it_did(self):
        preset = _preset()
        applied = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: applied.append(description),
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            # The volume is in the MIX view, which is the third one.
            await pilot.press("tab")
            await pilot.press("tab")
            await pilot.pause()
            _focus(table, 0, "volume")
            await pilot.press("plus")
            await pilot.pause()
            # The stored value moved by one step...
            assert preset.tracks[0].volume == 65
            # ...and the sentence names the track, the field and both
            # numbers the panel prints.
            assert applied == ["track 1 volume 50→51"]

    async def test_minus_moves_the_volume_the_other_way(self):
        preset = _preset()
        applied = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: applied.append(description),
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            await pilot.press("tab")
            await pilot.press("tab")
            await pilot.pause()
            _focus(table, 0, "volume")
            await pilot.press("minus")
            await pilot.pause()
            assert preset.tracks[0].volume == 63
            assert applied == ["track 1 volume 50→49"]

    async def test_a_digit_seeds_the_number_prompt(self):
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                _preset(),
                on_apply=lambda preset, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            _focus(table, 0, "program")
            await pilot.press("5")
            await pilot.pause()
            # The prompt is up, and it holds the digit that was pressed.
            prompt = app.screen
            assert isinstance(prompt, NumberPrompt)
            assert prompt.query_one("#value", Input).value == "5"

    async def test_the_status_is_picked_from_the_list(self):
        preset = _preset()
        applied = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: applied.append(description),
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            await pilot.press("tab")
            await pilot.pause()
            _focus(table, 0, "status")
            await pilot.press("enter")
            await pilot.pause()
            picker = app.screen
            assert isinstance(picker, ChoicePicker)
            # The list holds the panel's own words for the status.
            choices = picker.query_one("#choice-table", DataTable)
            assert [choices.get_cell_at(Coordinate(row, 1)) for row in range(4)] == [
                "BOTH",
                "LOCAL",
                "MIDI",
                "*EXT*",
            ]
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            assert preset.tracks[0].status == 2
            assert applied == ["track 1 status LOCAL→MIDI"]

    async def test_a_refused_change_is_put_back(self):
        preset = _preset()
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            await pilot.press("tab")
            await pilot.press("tab")
            await pilot.pause()
            _focus(table, 0, "volume")
            await pilot.press("plus")
            await pilot.pause()
            assert preset.tracks[0].volume == 65
            # The app says the write did not land.
            screen.result(False, "the unit refused it", cell=(0, "volume"))
            await pilot.pause()
            assert preset.tracks[0].volume == 64
            assert "refused" in _status(screen)

    async def test_an_edit_is_refused_when_the_write_path_is_closed(self):
        preset = _preset()
        applied = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: applied.append(description),
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=False,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            await pilot.press("tab")
            await pilot.press("tab")
            await pilot.pause()
            _focus(table, 0, "volume")
            await pilot.press("plus")
            await pilot.pause()
            assert preset.tracks[0].volume == 64
            assert applied == []
            assert "refused" in _status(screen)


class TestTheGateInTheScreen:
    """`w` here toggles the app's gate, and the note follows it."""

    @staticmethod
    def _note(screen) -> str:
        return str(screen.query_one(".note", Static).content)

    async def test_w_arms_and_locks_the_gate_the_app_checks(self):
        preset = _preset()
        gate = EditGate()
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                gate=gate,
            )
            await app.push_screen(screen)
            await pilot.pause()
            assert "write gate locked" in self._note(screen).lower()
            await pilot.press("w")
            await pilot.pause()
            assert gate.describe() == "write gate ARMED"
            assert "write gate armed" in self._note(screen).lower()
            await pilot.press("w")
            await pilot.pause()
            assert gate.describe() == "write gate locked"
            assert "write gate locked" in self._note(screen).lower()

    async def test_an_edit_after_w_goes_through(self):
        preset = _preset()
        applied = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: applied.append(description),
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                gate=EditGate(),
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            await pilot.press("w")
            await pilot.press("tab")
            await pilot.press("tab")
            await pilot.pause()
            _focus(table, 0, "volume")
            await pilot.press("plus")
            await pilot.pause()
            assert applied, "the gate was armed on this screen"
            assert preset.tracks[0].volume == 65

    async def test_a_refusal_names_the_key_that_opens_the_gate(self):
        preset = _preset()
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                gate=EditGate(),
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            await pilot.press("tab")
            await pilot.press("tab")
            await pilot.pause()
            _focus(table, 0, "volume")
            await pilot.press("plus")
            await pilot.pause()
            said = _status(screen)
            assert "the write gate is locked" in said
            assert "Press w" in said
            status = screen.query_one(".status", Static)
            assert status.has_class("refused"), "a refusal is the red alarm"

    async def test_without_an_app_gate_w_says_there_is_none(self):
        preset = _preset()
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
            )
            await app.push_screen(screen)
            await pilot.pause()
            await pilot.press("w")
            await pilot.pause()
            assert "no write gate" in _status(screen)


class TestTheTrackSwitch:
    """``space``: the track in the layer list and out of it."""

    async def test_space_switches_the_track_on(self):
        preset = _preset()
        applied = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: applied.append(description),
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            _focus(table, 1, "trk")
            await pilot.press("space")
            await pilot.pause()
            # The pile is what changed: the track's own byte names it.
            assert preset.pile[1] == 2
            assert preset.is_active(2)
            assert preset.tracks[1].status == 1, "a switch-on keeps LOCAL"
            assert applied == ["track 2 off→on"]

    async def test_switching_off_a_middle_track_leaves_no_hole(self):
        """Two layered tracks, then the lower one off: the pile compacts."""
        preset = _preset()
        applied = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: applied.append(description),
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            _focus(table, 1, "trk")
            await pilot.press("space")
            _focus(table, 2, "trk")
            await pilot.press("space")
            await pilot.pause()
            assert preset.pile == bytes([1, 2, 3, 0, 0, 0, 0, 0])
            _focus(table, 1, "trk")
            await pilot.press("space")
            await pilot.pause()
            assert preset.pile == bytes([1, 3, 0, 0, 0, 0, 0, 0])
            assert not preset.is_active(2)
            assert preset.is_active(3)
            assert preset.tracks[1].status == 1, "the status byte is untouched"
            assert preset.tracks[2].status == 1
            assert applied[-1] == "track 2 on→off"

    async def test_switching_off_leaves_every_status_byte_alone(self):
        """Off is the pile.  The status byte is not part of it any more.

        A dump that carries *EXT* silences the track in the stored
        preset and nowhere else, and it latches: a later dump that
        restores LOCAL does not bring the live map back.  So the switch
        writes the pile and nothing else.
        """
        preset = _preset()
        applied = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: applied.append(description),
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            _focus(table, 1, "trk")
            await pilot.press("space")  # on
            await pilot.pause()
            assert preset.tracks[1].status == 1
            before = [track.status for track in preset.tracks]
            await pilot.press("space")  # off
            await pilot.pause()
            assert not preset.is_active(2)
            assert [track.status for track in preset.tracks] == before
            assert preset.tracks[1].status != EXTERNAL_STATUS
            assert applied[-1] == "track 2 on→off"

    async def test_switching_off_says_the_live_switch_is_on_the_panel(self):
        """The sentence must not promise a live change.

        The unit's live switch is a front-panel gesture, and the status
        line says where it is instead of pretending the dump did it.
        """
        preset = _preset()
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            _focus(table, 1, "trk")
            await pilot.press("space")
            await pilot.pause()
            said = _status(screen)
            assert "layer list" in said
            assert "front panel" in said
            assert "ENTER/SAVE" in said

    async def test_the_primary_track_cannot_be_switched_off(self):
        preset = _preset()
        applied = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: applied.append(description),
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            _focus(table, 0, "trk")
            await pilot.press("space")
            await pilot.pause()
            assert preset.is_active(1)
            assert applied == []
            assert "primary" in _status(screen)

    async def test_a_primary_track_marked_external_can_be_switched_on(self):
        """The refusal is about switching it off, not about recovery."""
        preset = _preset()
        preset.tracks[0] = preset.tracks[0].updated(status=EXTERNAL_STATUS)
        applied = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: applied.append(description),
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            _focus(table, 0, "trk")
            await pilot.press("space")
            await pilot.pause()
            assert preset.tracks[0].status == 1
            assert applied == ["track 1 off→on"]

    async def test_a_stored_external_track_is_repaired_to_local(self):
        """Switching a track on repairs a stored *EXT* status.

        *EXT* is the status that stops the internal voices, so a track
        the user wants to hear should not carry it.  The repair is of
        the *stored* preset: the unit applies it when the preset is
        selected again.
        """
        preset = _preset()
        preset.tracks[1] = preset.tracks[1].updated(status=EXTERNAL_STATUS)
        applied = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: applied.append(description),
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            _focus(table, 1, "trk")
            await pilot.press("space")
            await pilot.pause()
            assert preset.tracks[1].status == 1
            assert preset.is_active(2)
            assert applied == ["track 2 off→on"]
            said = _status(screen)
            assert "LOCAL" in said
            assert "selected again" in said

    async def test_a_switched_on_track_can_be_switched_off_again(self):
        preset = _preset()
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            _focus(table, 1, "trk")
            await pilot.press("space")
            await pilot.pause()
            await pilot.press("space")
            await pilot.pause()
            assert preset.pile[1] == 0
            assert not preset.is_active(2)
            assert preset.tracks[1].status == 1, "the status byte is untouched"


class TestTheRange:
    """A value that cannot move says so, and changes nothing."""

    async def test_a_bump_that_cannot_move_is_refused(self):
        preset = _preset()
        # The channel of the first track is the top of its range: the
        # unit counts the channels from one and stores them from zero.
        preset.tracks[0] = preset.tracks[0].updated(midi_channel=15)
        applied = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: applied.append(description),
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            # The channel is in the first view now: eight tracks on
            # eight channels is what MULTI is for.
            _focus(table, 0, "channel")
            await pilot.press("plus")
            await pilot.pause()
            assert preset.tracks[0].midi_channel == 15
            assert applied == []
            said = _status(screen)
            assert "refused" in said
            assert "1-16" in said

    async def test_a_number_outside_the_range_is_refused(self):
        preset = _preset()
        applied = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: applied.append(description),
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            _focus(table, 0, "channel")
            await pilot.press("enter")
            await pilot.pause()
            prompt = app.screen
            assert isinstance(prompt, NumberPrompt)
            prompt.query_one("#value", Input).value = "99"
            await pilot.press("enter")
            await pilot.pause()
            assert preset.tracks[0].midi_channel == 0
            assert applied == []
            assert "1-16" in _status(screen)


class TestTheSeams:
    """What the screen hands to the app, and what it takes from it."""

    async def test_the_sound_picker_is_the_apps(self):
        asked = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                _preset(),
                on_apply=lambda preset, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: asked.append((index, number)),
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            _focus(table, 2, "spot")
            await pilot.press("enter")
            await pilot.pause()
            # The screen does not own a picker: it says which track
            # wants a sound, and the app runs its own.
            assert asked == [(2, 3)]

    async def test_the_apps_own_status_line_is_told_too(self):
        said = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                _preset(),
                on_apply=lambda preset, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=False,
                say_=lambda message, refused=False: said.append((message, refused)),
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            await pilot.press("tab")
            await pilot.press("tab")
            await pilot.pause()
            _focus(table, 0, "volume")
            await pilot.press("plus")
            await pilot.pause()
            assert said and said[-1][1] is True

    async def test_the_catalog_supplies_the_sound_name(self):
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                _preset(),
                on_apply=lambda preset, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                name_for=lambda group, slot: f"{group} {slot:03d} sound",
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            # The spot column is the group and the slot; the name column
            # is what the catalog says about it.
            assert table.get_cell("t1", "spot") == "INT 001"
            assert table.get_cell("t1", "name") == "INT 001 sound"

    async def test_without_a_catalog_the_name_says_so(self):
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                _preset(),
                on_apply=lambda preset, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            assert "no name" in table.get_cell("t1", "name")


class TestTheRefusalsOfTheScreen:
    """The gestures that change nothing, and say why."""

    async def test_a_digit_on_a_list_of_names_is_refused(self):
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                _preset(),
                on_apply=lambda preset, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            await pilot.press("tab")
            await pilot.pause()
            _focus(table, 0, "status")
            await pilot.press("5")
            await pilot.pause()
            assert "picks one" in _status(screen)

    async def test_a_digit_on_a_column_with_no_number_is_refused(self):
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                _preset(),
                on_apply=lambda preset, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            _focus(table, 0, "trk")
            await pilot.press("5")
            await pilot.pause()
            assert "no number to type" in _status(screen)

    async def test_a_bump_on_a_list_of_names_is_refused(self):
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                _preset(),
                on_apply=lambda preset, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            await pilot.press("tab")
            await pilot.pause()
            _focus(table, 0, "status")
            await pilot.press("plus")
            await pilot.pause()
            assert "list of names" in _status(screen)

    async def test_a_bump_on_a_column_with_no_number_is_refused(self):
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                _preset(),
                on_apply=lambda preset, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            _focus(table, 0, "trk")
            await pilot.press("plus")
            await pilot.pause()
            assert "step a number" in _status(screen)

    async def test_a_bump_on_a_pan_at_sound_is_refused(self):
        """SOUND is a word, not a number, so there is no step to take."""
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                _preset(),
                on_apply=lambda preset, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            await pilot.press("tab")
            await pilot.press("tab")
            await pilot.pause()
            _focus(table, 0, "pan")
            await pilot.press("plus")
            await pilot.pause()
            assert "SOUND" in _status(screen)

    async def test_a_bump_past_the_end_of_the_range_is_refused(self):
        """The volume is printed 0..99 and stored 0..127; 99 is the end."""
        preset = _preset()
        preset.tracks[0] = preset.tracks[0].updated(volume=127)
        applied = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: applied.append(description),
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            await pilot.press("tab")
            await pilot.press("tab")
            await pilot.pause()
            _focus(table, 0, "volume")
            await pilot.press("plus")
            await pilot.pause()
            assert applied == []
            assert "0-99" in _status(screen)

    async def test_editing_the_track_number_is_refused(self):
        """The track's own number is not a value; space is the gesture."""
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                _preset(),
                on_apply=lambda preset, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            _focus(table, 0, "trk")
            await pilot.press("enter")
            await pilot.pause()
            assert "space switches" in _status(screen)

    async def test_a_switch_is_refused_when_the_write_path_is_closed(self):
        preset = _preset()
        applied = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: applied.append(description),
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=False,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            _focus(table, 1, "trk")
            await pilot.press("space")
            await pilot.pause()
            assert applied == []
            assert preset.pile[1] == 0
            assert "refused" in _status(screen)


class TestTheTypedValue:
    """A number that is typed, and the ones that are not."""

    async def test_the_prompt_is_seeded_with_the_value_as_it_is_typed(self):
        """The volume is stored 64, which the panel prints as 50."""
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                _preset(),
                on_apply=lambda preset, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            await pilot.press("tab")
            await pilot.press("tab")
            await pilot.pause()
            _focus(table, 0, "volume")
            await pilot.press("enter")
            await pilot.pause()
            prompt = app.screen
            assert isinstance(prompt, NumberPrompt)
            assert prompt.query_one("#value", Input).value == "50"

    async def test_a_typed_volume_is_stored_through_the_table(self):
        preset = _preset()
        applied = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: applied.append(description),
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            await pilot.press("tab")
            await pilot.press("tab")
            await pilot.pause()
            _focus(table, 0, "volume")
            await pilot.press("enter")
            await pilot.pause()
            prompt = app.screen
            assert isinstance(prompt, NumberPrompt)
            prompt.query_one("#value", Input).value = "60"
            await pilot.press("enter")
            await pilot.pause()
            # The stored value is the one that prints as 60.
            assert Track(volume=preset.tracks[0].volume).volume_display == 60

    async def test_something_that_is_not_a_number_is_refused(self):
        applied = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                _preset(),
                on_apply=lambda live, description: applied.append(description),
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            await pilot.press("tab")
            await pilot.press("tab")
            await pilot.pause()
            _focus(table, 0, "volume")
            await pilot.press("enter")
            await pilot.pause()
            prompt = app.screen
            assert isinstance(prompt, NumberPrompt)
            prompt.query_one("#value", Input).value = "loud"
            await pilot.press("enter")
            await pilot.pause()
            assert applied == []
            assert "not a number" in _status(screen)

    async def test_the_prompt_can_be_cancelled_with_nothing(self):
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                _preset(),
                on_apply=lambda preset, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            await pilot.press("tab")
            await pilot.press("tab")
            await pilot.pause()
            _focus(table, 0, "volume")
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, NumberPrompt)
            await pilot.press("escape")
            await pilot.pause()
            # The prompt is gone and nothing was changed.
            assert isinstance(app.screen, MultiScreen)

    async def test_a_pan_can_be_typed_as_the_word_sound(self):
        preset = _preset()
        applied = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: applied.append(description),
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            await pilot.press("tab")
            await pilot.press("tab")
            await pilot.pause()
            _focus(table, 0, "pan")
            await pilot.press("enter")
            await pilot.pause()
            prompt = app.screen
            assert isinstance(prompt, NumberPrompt)
            # The pan is SOUND, and the prompt says so.
            assert prompt.query_one("#value", Input).value == "SOUND"
            prompt.query_one("#value", Input).value = "SOUND"
            await pilot.press("enter")
            await pilot.pause()
            assert preset.tracks[0].pan == -128
            assert applied == ["track 1 pan SOUND→SOUND"]

    async def test_a_pan_can_be_typed_as_a_number(self):
        preset = _preset()
        applied = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: applied.append(description),
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            await pilot.press("tab")
            await pilot.press("tab")
            await pilot.pause()
            _focus(table, 0, "pan")
            await pilot.press("enter")
            await pilot.pause()
            prompt = app.screen
            assert isinstance(prompt, NumberPrompt)
            prompt.query_one("#value", Input).value = "-50"
            await pilot.press("enter")
            await pilot.pause()
            assert preset.tracks[0].pan_display == -50
            assert applied == ["track 1 pan SOUND→-50"]


class TestTheRollback:
    """What a refusal puts back, and what it leaves alone."""

    async def test_a_landed_change_is_not_put_back_by_a_later_refusal(self):
        """A change that landed has nothing to put back."""
        preset = _preset()
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            await pilot.press("tab")
            await pilot.press("tab")
            await pilot.pause()
            _focus(table, 0, "volume")
            await pilot.press("plus")
            await pilot.pause()
            assert preset.tracks[0].volume == 65
            screen.result(True, "sent", cell=(0, "volume"))
            await pilot.pause()
            screen.result(False, "the unit refused it", cell=(0, "volume"))
            await pilot.pause()
            # The change landed; a later refusal has nothing to undo.
            assert preset.tracks[0].volume == 65

    async def test_a_refusal_about_another_cell_leaves_this_one_alone(self):
        """The rollback is about the cell it names, and no other."""
        preset = _preset()
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            await pilot.press("tab")
            await pilot.press("tab")
            await pilot.pause()
            _focus(table, 0, "volume")
            await pilot.press("plus")
            await pilot.pause()
            assert preset.tracks[0].volume == 65
            screen.result(False, "the unit refused it", cell=(1, "volume"))
            await pilot.pause()
            # The other track had nothing to put back, and this one
            # still holds the change nobody refused.
            assert preset.tracks[1].volume == 64
            assert preset.tracks[0].volume == 65

    async def test_a_refusal_about_a_cell_that_writes_nothing_is_harmless(self):
        """The sound and the track's own number are not cells."""
        preset = _preset()
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            screen.result(False, "the unit refused it", cell=(0, "spot"))
            await pilot.pause()
            assert preset.tracks[0].sound_number == 0

    async def test_the_sustain_switch_goes_back_to_what_it_was(self):
        """The switch is a bit, and the bit goes back as a bit."""
        preset = _preset()
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            await pilot.press("tab")
            await pilot.pause()
            _focus(table, 0, "sustain")
            await pilot.press("enter")
            await pilot.pause()
            picker = app.screen
            assert isinstance(picker, ChoicePicker)
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            assert preset.tracks[0].sustain is False
            screen.result(False, "the unit refused it", cell=(0, "sustain"))
            await pilot.pause()
            assert preset.tracks[0].sustain is True


class TestTheRedrawSeam:
    """``redraw``: the table is a picture of the model."""

    async def test_the_table_is_drawn_again_from_the_model(self):
        preset = _preset()
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            await pilot.press("tab")
            await pilot.press("tab")
            await pilot.pause()
            _focus(table, 0, "volume")
            assert table.get_cell("t1", "volume") == "50"
            # The model changes behind the screen's back, the way the
            # app's undo changes it.
            preset.tracks[0] = preset.tracks[0].updated(volume=10)
            screen.redraw()
            await pilot.pause()
            assert table.get_cell("t1", "volume") == "8"

    async def test_the_screen_tells_the_app_when_it_closes(self):
        closed = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                _preset(),
                on_apply=lambda preset, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                on_close=lambda: closed.append(True),
            )
            await app.push_screen(screen)
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert closed == [True]

    async def test_capital_z_asks_for_all_of_them(self):
        undone = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                _preset(),
                on_apply=lambda preset, description: None,
                on_undo=undone.append,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            await pilot.press("Z")
            await pilot.pause()
            assert undone == [None]

    async def test_z_asks_for_one_change_back(self):
        undone = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                _preset(),
                on_apply=lambda preset, description: None,
                on_undo=undone.append,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            await pilot.press("z")
            await pilot.pause()
            assert undone == [1]

    async def test_a_cell_selection_from_another_table_is_ignored(self):
        """A rule that names one screen's table must not reach another's."""
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                _preset(),
                on_apply=lambda preset, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            event = SimpleNamespace(
                data_table=SimpleNamespace(id="some-other-table", cursor_row=0)
            )
            screen.on_data_table_cell_selected(event)
            await pilot.pause()
            # Nothing was edited and nothing was said.
            assert _status(screen) == ""

    async def test_a_picker_that_is_cancelled_changes_nothing(self):
        preset = _preset()
        applied = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: applied.append(description),
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                allow_write=True,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            await pilot.press("tab")
            await pilot.pause()
            _focus(table, 0, "status")
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ChoicePicker)
            await pilot.press("escape")
            await pilot.pause()
            assert applied == []
            assert preset.tracks[0].status == 1


class TestWhatTheTableShows:
    """The cells, and the ones the panel cannot show."""

    async def test_the_drum_kits_are_shown_as_themselves(self):
        preset = _preset()
        preset.tracks[0] = preset.tracks[0].updated(sound_number=sound_word("DRUM", 3))
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            assert table.get_cell("t1", "spot") == "~DRUM~ 03"

    async def test_a_value_outside_the_panel_s_range_is_shown_as_a_mark(self):
        """A corrupt dump is still a row somebody has to be able to read."""
        preset = _preset()
        preset.tracks[0] = Track(
            sound_number=0,
            volume=200,
            pan=-128,
            timbre=32,
            release=0,
            output=4,
            status=1,
            pressure=1,
            midi_channel=0,
            sustain=True,
            midi_program=0,
            key_low=21,
            transpose=0,
            key_high=108,
        )
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            await pilot.press("tab")
            await pilot.press("tab")
            await pilot.pause()
            assert table.get_cell("t1", "volume") == "?"

    async def test_an_enumerated_value_outside_the_list_is_shown_as_a_mark(self):
        preset = _preset()
        # A dump is a fact about the unit's memory: a value outside the
        # documented range is kept, not refused, and the cell says so.
        preset.tracks[0] = dataclasses.replace(preset.tracks[0], status=7)
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                preset,
                on_apply=lambda live, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            await pilot.press("tab")
            await pilot.pause()
            assert table.get_cell("t1", "status") == "?7"


class TestTheColumnHelper:
    """The column helper: what a cell shows, and what it writes.

    Three of the conversions are not the identity -- the Note 7
    table, the one-based numbering and the panel's own words -- and
    the fourth is: a column with no conversion shows the number and
    stores the number.
    """

    def test_a_column_with_no_conversion_shows_the_number(self):
        column = _Column("x", "x", field="volume", kind="number")
        track = Track(volume=42)
        assert column.text_of(track) == "42"
        assert column.edit_text(track) == "42"
        assert column.to_stored(42) == 42

    def test_the_table_conversion_is_note_sevens_table(self):
        column = _Column("volume", "vol", field="volume", kind="number", fmt="table")
        track = Track(volume=64)
        assert column.text_of(track) == "50"
        assert column.edit_text(track) == "50"
        assert column.to_stored(50) == 64

    def test_the_one_based_conversion_takes_one_off(self):
        column = _Column(
            "ch", "ch", field="midi_channel", kind="number", fmt="plus_one"
        )
        track = Track(midi_channel=3)
        assert column.text_of(track) == "4"
        assert column.edit_text(track) == "4"
        assert column.to_stored(4) == 3

    def test_the_pan_conversion_is_the_models_own(self):
        column = _Column("pan", "pan", field="pan", kind="number", fmt="pan")
        assert column.text_of(Track(pan=-128)) == "SOUND"
        assert column.edit_text(Track(pan=-128)) == "SOUND"
        assert column.text_of(Track(pan=0)) == "+0"
        assert column.edit_text(Track(pan=0)) == "0"

    def test_the_sustain_switch_reads_its_bit_the_other_way_round(self):
        column = _Column(
            "sustain",
            "sustain",
            field="sustain",
            kind="enum",
            names=("ON", "OFF"),
            bool_field=True,
        )
        assert column.stored_of(Track(sustain=True)) == 0
        assert column.stored_of(Track(sustain=False)) == 1
        assert column.text_of(Track(sustain=True)) == "ON"
        assert column.to_track(Track(sustain=True), 1).sustain is False


class TestThePresetListEdges:
    """The list, and the rows it cannot show."""

    async def test_the_list_says_where_it_came_from(self):
        app = _Host()
        async with app.run_test() as pilot:
            screen = PresetListScreen(
                [(0, _preset().to_bytes())], progress_note="read the dump"
            )
            await app.push_screen(screen)
            await pilot.pause()
            assert "read the dump" in str(screen.query_one(".note", Static).content)

    async def test_a_preset_that_cannot_be_read_shows_a_mark(self):
        """One bad row must not hide the other seventy-nine."""
        app = _Host()
        async with app.run_test() as pilot:
            screen = PresetListScreen([(0, b"\x00" * 10)])
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#preset-table", DataTable)
            assert table.get_cell("0", "name") == "?"

    async def test_a_row_selection_that_names_nothing_picks_nothing(self):
        """A row the list does not have is not a row."""
        picked = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = PresetListScreen([(0, _preset().to_bytes())])
            await app.push_screen(screen, picked.append)
            await pilot.pause()
            event = SimpleNamespace(
                data_table=SimpleNamespace(id=PresetListScreen.TABLE_ID, cursor_row=99)
            )
            screen.on_data_table_row_selected(event)
            await pilot.pause()
            assert picked == []

    async def test_a_selection_from_another_table_is_ignored(self):
        """A rule that names one screen's table must not reach another's."""
        picked = []
        app = _Host()
        async with app.run_test() as pilot:
            screen = PresetListScreen([(0, _preset().to_bytes())])
            await app.push_screen(screen, picked.append)
            await pilot.pause()
            event = SimpleNamespace(
                data_table=SimpleNamespace(id="some-other-table", cursor_row=0)
            )
            screen.on_data_table_row_selected(event)
            await pilot.pause()
            assert picked == []

    async def test_a_catalog_that_has_no_name_says_so(self):
        """A blank cell reads as a broken row."""
        app = _Host()
        async with app.run_test() as pilot:
            screen = MultiScreen(
                _preset(),
                on_apply=lambda preset, description: None,
                on_undo=lambda count: None,
                on_sound_picked=lambda index, number: None,
                name_for=lambda group, slot: None,
            )
            await app.push_screen(screen)
            await pilot.pause()
            table = screen.query_one("#track-table", DataTable)
            assert "no name" in table.get_cell("t1", "name")
