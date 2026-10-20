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

from textual.app import App, ComposeResult
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Input, Label, Static

from ensqsqed.multi import (
    COLUMN_VIEWS,
    KEY_HINTS_BLOCK,
    ChoicePicker,
    MultiScreen,
    NumberPrompt,
    PresetListScreen,
)
from sq.presets import Preset, Track


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
            # The unit counts its preset locations from one and the
            # dump's slots from zero, so the row shows one more than
            # the slot the screen answers with.
            assert table.get_cell("0", "slot") == "001"
            assert table.get_cell("2", "slot") == "003"
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


class TestTheColumns:
    """The four column groups, and the keys in them."""

    async def test_the_first_view_shows_the_sound_and_the_program(self):
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
            assert _keys(table) == ("trk", "on", "spot", "name", "program")
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
        assert "space track on/off" in KEY_HINTS_BLOCK


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


class TestTheTrackSwitch:
    """``space``: the track on and off, and the primary track's refusal."""

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
            assert applied == ["track 2 off→on"]

    async def test_space_leaves_the_status_byte_alone(self):
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
            before = preset.tracks[1].to_bytes()
            _focus(table, 1, "trk")
            await pilot.press("space")
            await pilot.pause()
            after = preset.tracks[1].to_bytes()
            # Byte 8 is the status, the pressure and the channel: the
            # off state is in the pile, and the pile is not in the track.
            assert after[8] == before[8]

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
            await pilot.press("tab")
            await pilot.pause()
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
            await pilot.press("tab")
            await pilot.pause()
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
