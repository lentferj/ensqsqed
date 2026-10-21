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

"""The effect editor, over a synthetic preset.

The preset here is invented: what the screen is tested on is its shape
-- the rows it builds, the values it shows, the words it says and the
calls it makes -- and never the text of a name.
"""

import pathlib

from textual.app import App
from textual.widgets import DataTable, Static

from ensqsqed.fx import FxScreen
from ensqsqed.gate import EditGate
from sq import presets
from sq.presets import Effect, Preset
from vinsynlib.ui.confirm import ConfirmScreen
from vinsynlib.ui.modals import ChoiceScreen
from vinsynlib.ui.prompt import PromptScreen

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

#: An effect with values in every byte, invented rather than dumped:
#: byte four holds the modulation destination and the modulation
#: source, byte six the damping and the bandwidth.
PARAMS = bytes([0x38, 0x06, 0x20, 0x10, 0x25, 0x00, 0x8F, 0x54, 0x20, 0x20])


class _Harness(App[None]):
    """The least an app can be and still hold the screen."""

    def __init__(self, screen) -> None:
        super().__init__()
        self._screen = screen

    def on_mount(self) -> None:
        self.push_screen(self._screen)


def _preset(params: bytes = PARAMS, number: int = 0) -> Preset:
    """A preset with an effect and nothing else worth asserting on."""
    preset = Preset()
    preset.effect = Effect(number=number, params=params)
    return preset


def _row_of(preset: Preset, key: str):
    """The model's row with this key in this effect's parameter set."""
    return next(row for row in preset.effect.rows if row.key == key)


def _index_of(preset: Preset, key: str) -> int:
    """Which line of the table the row with this key is on.

    The first line is the effect's type, so the model's rows start at
    one.
    """
    keys = [row.key for row in preset.effect.rows]
    return 1 + keys.index(key)


def _column(table: DataTable, number: int) -> list:
    """One column of the table, as plain strings."""
    return [str(table.get_row_at(index)[number]) for index in range(table.row_count)]


def _screen(preset: Preset, on_apply, **kwargs) -> FxScreen:
    """The screen with the application's write path open.

    The gate is the application's, and the screen refuses an edit when
    it is closed -- so a test that wants an edit to happen has to open
    it, exactly as the application does.
    """
    return FxScreen(preset, on_apply=on_apply, allow_write=True, **kwargs)


class TestTheRowsComeFromTheModel:
    """The screen has no list of its own."""

    async def test_the_rows_are_the_model_s_rows(self):
        preset = _preset()
        screen = _screen(preset, lambda p, d: None)
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.screen.query_one("#fx-table", DataTable)
            assert table.row_count == 1 + len(preset.effect.rows)
            labels = _column(table, 0)
            assert labels[0] == "EFFECT TYPE"
            assert labels[1:] == [row.label for row in preset.effect.rows]

    async def test_the_two_mix_rows_are_always_offered(self):
        preset = _preset()
        screen = _screen(preset, lambda p, d: None)
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.screen.query_one("#fx-table", DataTable)
            labels = _column(table, 0)
            for row in preset.effect.rows:
                if row.key in ("fx1_mix", "fx2_mix"):
                    assert row.label in labels

    async def test_the_mix_rows_are_added_when_a_set_leaves_them_out(self, monkeypatch):
        """The two mix rows are always offered, whichever set is active."""
        without = tuple(
            row
            for row in presets.PARAM_SETS["Common Reverb"]
            if row.key not in ("fx1_mix", "fx2_mix")
        )
        monkeypatch.setitem(presets.PARAM_SETS, "Common Reverb", without)
        preset = _preset()
        screen = _screen(preset, lambda p, d: None)
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.screen.query_one("#fx-table", DataTable)
            labels = _column(table, 0)
            assert "FX1 MIX" in labels
            assert "FX2 MIX" in labels

    async def test_a_short_list_of_names_is_shown_in_the_range_column(self):
        """The multiverb's input invert is two names, not a range."""
        preset = _preset(number=5)
        screen = _screen(preset, lambda p, d: None)
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.screen.query_one("#fx-table", DataTable)
            spans = _column(table, 2)
            assert "OFF / ON" in spans

    async def test_the_values_are_the_model_s_values(self):
        preset = _preset()
        screen = _screen(preset, lambda p, d: None)
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.screen.query_one("#fx-table", DataTable)
            values = _column(table, 1)
            decay = _row_of(preset, "decay_time")
            assert values[1] == str(preset.effect.display(decay))


class TestAnEnumeratedRow:
    """A row whose values are names is a list to pick from."""

    async def test_the_list_of_names_opens(self):
        preset = _preset()
        screen = _screen(preset, lambda p, d: None)
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press(*(["down"] * _index_of(preset, "mod_source")))
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ChoiceScreen)
            choices = app.screen.query_one("#choice-table", DataTable)
            assert choices.row_count == len(presets.MIX_SOURCES)

    async def test_a_pick_changes_the_effect_and_says_what_it_did(self):
        preset = _preset()
        applied = []
        screen = _screen(preset, lambda p, d: applied.append(d))
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press(*(["down"] * _index_of(preset, "mod_source")))
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            source = _row_of(preset, "mod_source")
            assert preset.effect.raw(source) == 6
            assert preset.effect.display(source) == "XCONTROL"
            assert applied == ["MOD SOURCE → XCONTROL"]

    async def test_a_digit_on_a_list_of_names_is_refused(self):
        preset = _preset()
        applied = []
        said = []
        screen = _screen(
            preset,
            lambda p, d: applied.append(d),
            say_=lambda message, refused=False: said.append((message, refused)),
        )
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press(*(["down"] * _index_of(preset, "mod_source")))
            await pilot.press("7")
            await pilot.pause()
            assert applied == []
            assert said and said[-1][1] is True


class TestTheBump:
    """`+` and `-` step the row's own values."""

    async def test_a_bump_applies_one_step(self):
        preset = _preset()
        applied = []
        screen = _screen(preset, lambda p, d: applied.append(d))
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("+")
            await pilot.pause()
            decay = _row_of(preset, "decay_time")
            assert preset.effect.display(decay) == 45
            assert applied == ["DECAY TIME → 45"]

    async def test_a_bump_down_applies_one_step(self):
        preset = _preset()
        applied = []
        screen = _screen(preset, lambda p, d: applied.append(d))
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("-")
            await pilot.pause()
            decay = _row_of(preset, "decay_time")
            assert preset.effect.display(decay) == 43
            assert applied == ["DECAY TIME → 43"]

    async def test_a_bump_past_the_end_is_refused(self):
        preset = _preset(bytes([0x7F] + list(PARAMS[1:])))
        applied = []
        said = []
        screen = _screen(
            preset,
            lambda p, d: applied.append(d),
            say_=lambda message, refused=False: said.append((message, refused)),
        )
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("+")
            await pilot.pause()
            assert applied == []
            assert said, "a refused bump says so"
            message, refused = said[-1]
            assert refused is True
            assert "0 to 99" in message

    async def test_a_bump_on_the_type_row_is_refused(self):
        preset = _preset()
        applied = []
        said = []
        screen = _screen(
            preset,
            lambda p, d: applied.append(d),
            say_=lambda message, refused=False: said.append((message, refused)),
        )
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("+")
            await pilot.pause()
            assert applied == []
            assert said and said[-1][1] is True

    async def test_a_bump_on_a_list_of_names_steps_through_it(self):
        preset = _preset()
        applied = []
        screen = _screen(preset, lambda p, d: applied.append(d))
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press(*(["down"] * _index_of(preset, "mod_source")))
            await pilot.press("+")
            await pilot.pause()
            source = _row_of(preset, "mod_source")
            assert preset.effect.display(source) == "XCONTROL"
            assert applied == ["MOD SOURCE → XCONTROL"]

    async def test_a_bump_past_the_end_of_a_list_of_names_is_refused(self):
        preset = _preset()
        applied = []
        said = []
        screen = _screen(
            preset,
            lambda p, d: applied.append(d),
            say_=lambda message, refused=False: said.append((message, refused)),
        )
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press(*(["down"] * _index_of(preset, "mod_source")))
            for _ in range(len(presets.MIX_SOURCES) + 1):
                await pilot.press("+")
            await pilot.pause()
            source = _row_of(preset, "mod_source")
            assert preset.effect.display(source) == "*OFF*"
            assert applied[-1] == "MOD SOURCE → *OFF*"
            assert said and said[-1][1] is True


class TestTheTypeChange:
    """The effect's number and its bytes belong together, so it asks."""

    async def test_the_list_of_effects_opens(self):
        preset = _preset()
        screen = _screen(preset, lambda p, d: None)
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ChoiceScreen)
            choices = app.screen.query_one("#choice-table", DataTable)
            assert choices.row_count == len(presets.EFFECTS)

    async def test_a_pick_asks_before_it_happens(self):
        preset = _preset()
        applied = []
        screen = _screen(preset, lambda p, d: applied.append(d))
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            prompt = app.screen.query_one("#confirm-prompt")
            assert "factory package" in str(prompt.render())

    async def test_picking_the_effect_it_already_has_changes_nothing(self):
        preset = _preset()
        applied = []
        said = []
        screen = _screen(
            preset,
            lambda p, d: applied.append(d),
            say_=lambda message, refused=False: said.append((message, refused)),
        )
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert applied == []
            assert preset.effect.number == 0
            assert said and said[-1][1] is False

    async def test_a_refused_confirmation_sends_nothing(self):
        preset = _preset()
        applied = []
        screen = _screen(preset, lambda p, d: applied.append(d))
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            assert applied == []
            assert preset.effect.number == 0

    async def test_a_confirmed_change_sets_the_number_and_the_rows(self):
        preset = _preset()
        applied = []
        screen = _screen(preset, lambda p, d: applied.append(d))
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            # Four down: the eighth-voice chorus, whose parameter set is
            # not the common reverb's.
            await pilot.press(*(["down"] * 4))
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            assert preset.effect.number == 4
            assert applied == [f"effect type → {presets.EFFECTS[4]}"]
            table = app.screen.query_one("#fx-table", DataTable)
            assert table.row_count == 1 + len(preset.effect.rows)

    async def test_the_other_bytes_keep_their_values(self):
        preset = _preset()
        screen = _screen(preset, lambda p, d: None)
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press(*(["down"] * 4))
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            assert preset.effect.params == PARAMS


class TestTheSharedByte:
    """Two parameters in one byte: each row edits its own bits.

    This is the model's promise that the screen leans on, and it is
    proved here against the dump the instrument produced rather than
    against an invented effect.
    """

    def test_the_sibling_bits_survive_a_partial_write(self):
        data = (FIXTURES / "all_seq.bin").read_bytes()
        start = len(data) - 21 - 168 * 80
        raw = data[start : start + 168]
        preset = Preset.from_bytes(raw)
        effect = preset.effect
        destination = _row_of(preset, "mod_destination")
        source = _row_of(preset, "mod_source")
        assert effect.params[4] == 0x25
        updated = effect.with_value(source, "SUS-PEDL")
        assert updated.raw(source) == 7
        assert updated.raw(destination) == effect.raw(destination)
        assert updated.params[4] & 0xF0 == effect.params[4] & 0xF0


class TestAValueThePanelCannotPrint:
    """A dump may hold a byte the specification's list does not have.

    The unit writes what it writes, and a preset read back from it can
    hold a value this program has no name for.  The screen has to say
    so on that one row: a refusal there would take the whole table
    with it, and the rows below the bad one would never be drawn.
    """

    async def test_the_table_is_whole_and_the_bad_cell_says_so(self):
        params = bytearray(PARAMS)
        # Byte four holds the modulation destination in its high four
        # bits: 5 is not one of the common reverb's five destinations.
        params[4] = 0x55
        preset = _preset(bytes(params))
        screen = _screen(preset, lambda p, d: None)
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.screen.query_one("#fx-table", DataTable)
            # Every row is drawn: the bad value takes one cell, not the
            # whole table.
            assert table.row_count == 1 + len(preset.effect.rows)
            row = _index_of(preset, "mod_destination")
            assert table.get_row_at(row)[1] == "?"

    async def test_a_number_the_panel_cannot_print_says_so(self):
        params = bytearray(PARAMS)
        # Byte two holds the detune rate, which the panel prints 0..99.
        params[2] = 120
        preset = _preset(bytes(params))
        applied = []
        said = []
        screen = _screen(
            preset,
            lambda p, d: applied.append(d),
            say_=lambda message, refused=False: said.append((message, refused)),
        )
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.screen.query_one("#fx-table", DataTable)
            assert table.get_row_at(_index_of(preset, "detune_rate"))[1] == "?"
            table.move_cursor(row=_index_of(preset, "detune_rate"))
            await pilot.pause()
            await pilot.press("+")
            await pilot.pause()
            assert applied == []
            assert said and said[-1][1] is True
            assert "cannot print" in said[-1][0]


class TestTheRedrawSeam:
    """``redraw``: the table is a picture of the model."""

    async def test_the_rows_are_drawn_again_from_the_model(self):
        preset = _preset()
        screen = _screen(preset, lambda p, d: None)
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            table = app.screen.query_one("#fx-table", DataTable)
            row = _index_of(preset, "decay_time")
            assert table.get_row_at(row)[1] == "44"
            # The model changes behind the screen's back, the way the
            # app's undo changes it.
            preset.effect = preset.effect.with_value(_row_of(preset, "decay_time"), 50)
            screen.redraw()
            await pilot.pause()
            assert table.get_row_at(row)[1] == "50"

    async def test_a_landed_change_redraws_the_row_it_touched(self):
        preset = _preset()
        screen = _screen(preset, lambda p, d: None)
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("+")
            await pilot.pause()
            assert preset.effect.display(_row_of(preset, "decay_time")) == 45
            screen.result(True, "sent", row="decay_time")
            await pilot.pause()
            table = app.screen.query_one("#fx-table", DataTable)
            assert table.get_row_at(_index_of(preset, "decay_time"))[1] == "45"

    async def test_a_result_about_a_row_that_is_not_shown_is_harmless(self):
        preset = _preset()
        screen = _screen(preset, lambda p, d: None)
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen.result(True, "sent", row="no-such-row")
            await pilot.pause()
            assert preset.effect.display(_row_of(preset, "decay_time")) == 44


class TestTheCancelledChoices:
    """A choice that is not made changes nothing."""

    async def test_a_cancelled_type_choice_changes_nothing(self):
        preset = _preset()
        applied = []
        screen = _screen(preset, lambda p, d: applied.append(d))
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ChoiceScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert applied == []
            assert preset.effect.number == 0

    async def test_a_cancelled_list_of_names_changes_nothing(self):
        preset = _preset()
        applied = []
        screen = _screen(preset, lambda p, d: applied.append(d))
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press(*(["down"] * _index_of(preset, "mod_source")))
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ChoiceScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert applied == []
            source = _row_of(preset, "mod_source")
            assert preset.effect.raw(source) == 5

    async def test_a_cancelled_prompt_changes_nothing(self):
        preset = _preset()
        applied = []
        screen = _screen(preset, lambda p, d: applied.append(d))
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, PromptScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert applied == []
            decay = _row_of(preset, "decay_time")
            assert preset.effect.display(decay) == 44

    async def test_an_empty_prompt_changes_nothing(self):
        preset = _preset()
        applied = []
        screen = _screen(preset, lambda p, d: applied.append(d))
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            prompt = app.screen
            assert isinstance(prompt, PromptScreen)
            prompt.query_one("Input").value = ""
            await pilot.press("enter")
            await pilot.pause()
            assert applied == []
            decay = _row_of(preset, "decay_time")
            assert preset.effect.display(decay) == 44


class TestTheSeams:
    """What the application looks for, and what happens after the screen
    has gone."""

    async def test_a_reply_to_a_closed_screen_is_not_a_crash(self):
        preset = _preset()
        applied = []
        said = []
        screen = _screen(
            preset,
            lambda p, d: applied.append(d),
            say_=lambda message, refused=False: said.append((message, refused)),
        )
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("+")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            # A refusal that arrives after the screen has gone must put
            # the old value back without raising.
            screen.result(False, "the unit refused it", row="decay_time")
            screen.result(True, "sent", row="decay_time", value=45)
            screen.say("and this too")
            assert said, "the hook is told even when the screen is gone"
            decay = _row_of(preset, "decay_time")
            assert preset.effect.display(decay) == 44

    async def test_a_failed_result_puts_the_old_value_back(self):
        preset = _preset()
        applied = []
        screen = _screen(preset, lambda p, d: applied.append(d))
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("+")
            await pilot.pause()
            decay = _row_of(preset, "decay_time")
            assert preset.effect.display(decay) == 45
            screen.result(False, "the unit refused it", row="decay_time")
            await pilot.pause()
            assert preset.effect.display(decay) == 44


class TestTheTypedPrompt:
    """A number is typed, and the seed says where the typing starts."""

    async def test_enter_opens_the_prompt(self):
        preset = _preset()
        screen = _screen(preset, lambda p, d: None)
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, PromptScreen)

    async def test_a_typed_number_is_applied(self):
        preset = _preset()
        applied = []
        screen = _screen(preset, lambda p, d: applied.append(d))
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("5", "0")
            await pilot.press("enter")
            await pilot.pause()
            decay = _row_of(preset, "decay_time")
            assert preset.effect.display(decay) == 50
            assert applied == ["DECAY TIME → 50"]

    async def test_a_digit_seeds_the_prompt(self):
        preset = _preset()
        applied = []
        screen = _screen(preset, lambda p, d: applied.append(d))
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("5")
            await pilot.pause()
            assert isinstance(app.screen, PromptScreen)
            await pilot.press("0")
            await pilot.press("enter")
            await pilot.pause()
            decay = _row_of(preset, "decay_time")
            assert preset.effect.display(decay) == 50

    async def test_a_number_outside_the_range_is_refused(self):
        preset = _preset()
        applied = []
        said = []
        screen = _screen(
            preset,
            lambda p, d: applied.append(d),
            say_=lambda message, refused=False: said.append((message, refused)),
        )
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("1", "2", "0")
            await pilot.press("enter")
            await pilot.pause()
            assert applied == []
            assert said and said[-1][1] is True
            assert "0 to 99" in said[-1][0]

    async def test_something_that_is_not_a_number_is_refused(self):
        preset = _preset()
        applied = []
        said = []
        screen = _screen(
            preset,
            lambda p, d: applied.append(d),
            say_=lambda message, refused=False: said.append((message, refused)),
        )
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("a")
            await pilot.press("enter")
            await pilot.pause()
            assert applied == []
            assert said and said[-1][1] is True

    async def test_a_value_the_model_cannot_store_is_refused(self):
        """The damping is stored in four bits and printed in sixteen
        steps, so most of the numbers it prints are not values it can
        hold.  The model says so, and the screen says what it said."""
        preset = _preset()
        applied = []
        said = []
        screen = _screen(
            preset,
            lambda p, d: applied.append(d),
            say_=lambda message, refused=False: said.append((message, refused)),
        )
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press(*(["down"] * _index_of(preset, "hf_damping")))
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("1")
            await pilot.press("enter")
            await pilot.pause()
            assert applied == []
            assert said and said[-1][1] is True


class TestTheWritePath:
    """The gate is the application's, and a closed one changes nothing."""

    async def test_a_closed_write_path_refuses_the_edit(self):
        preset = _preset()
        applied = []
        said = []
        screen = FxScreen(
            preset,
            on_apply=lambda p, d: applied.append(d),
            say_=lambda message, refused=False: said.append((message, refused)),
        )
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("+")
            await pilot.pause()
            assert applied == []
            decay = _row_of(preset, "decay_time")
            assert preset.effect.display(decay) == 44
            assert said and said[-1][1] is True

    async def test_a_closed_write_path_refuses_the_type_change(self):
        preset = _preset()
        applied = []
        screen = FxScreen(preset, on_apply=lambda p, d: applied.append(d))
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            assert applied == []
            assert preset.effect.number == 0


class TestTheGateInTheScreen:
    """`w` here toggles the app's gate, and the note follows it.

    The modal's bindings cut the app's chain, so without this key the
    gate could only be armed by closing the editor -- and the warning
    that said so was written on the dimmed browser behind it.
    """

    @staticmethod
    def _note(screen) -> str:
        return str(screen.query_one(".note", Static).content)

    async def test_w_arms_and_locks_the_gate_the_app_checks(self):
        preset = _preset()
        gate = EditGate()
        screen = FxScreen(preset, on_apply=lambda p, d: None, gate=gate)
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert "write gate locked" in self._note(screen)
            await pilot.press("w")
            await pilot.pause()
            assert gate.describe() == "write gate ARMED"
            assert "write gate ARMED" in self._note(screen)
            await pilot.press("w")
            await pilot.pause()
            assert gate.describe() == "write gate locked"
            assert "write gate locked" in self._note(screen)

    async def test_an_edit_after_w_goes_through(self):
        preset = _preset()
        gate = EditGate()
        applied = []
        screen = FxScreen(preset, on_apply=lambda p, d: applied.append(d), gate=gate)
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("w")
            await pilot.press("down")
            await pilot.press("+")
            await pilot.pause()
            assert applied, "the gate was armed on this screen"

    async def test_a_refusal_names_the_key_that_opens_the_gate(self):
        preset = _preset()
        said = []
        screen = FxScreen(
            preset,
            on_apply=lambda p, d: None,
            gate=EditGate(),
            say_=lambda message, refused=False: said.append((message, refused)),
        )
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("+")
            await pilot.pause()
            assert said and said[-1][1] is True
            assert "the write gate is locked" in said[-1][0]
            assert "Press w" in said[-1][0]

    async def test_without_an_app_gate_w_says_there_is_none(self):
        preset = _preset()
        screen = FxScreen(preset, on_apply=lambda p, d: None)
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("w")
            await pilot.pause()
            status = str(screen.query_one(".status", Static).content)
            assert "no write gate" in status


class TestTheUndoSeam:
    """`z` and `Z` are the application's undo, served here."""

    async def test_z_asks_for_one_change_back(self):
        preset = _preset()
        undone = []
        screen = _screen(preset, lambda p, d: None, on_undo=undone.append)
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("z")
            await pilot.pause()
            assert undone == [1]

    async def test_capital_z_asks_for_all_of_them(self):
        preset = _preset()
        undone = []
        screen = _screen(preset, lambda p, d: None, on_undo=undone.append)
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("Z")
            await pilot.pause()
            assert undone == [None]

    async def test_the_result_hook_is_told_what_happened(self):
        preset = _preset()
        reported = []
        screen = _screen(
            preset,
            lambda p, d: None,
            result_cb=lambda ok, message: reported.append((ok, message)),
        )
        app = _Harness(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen.result(True, "sent")
            screen.result(False, "refused")
            assert reported == [(True, "sent"), (False, "refused")]
