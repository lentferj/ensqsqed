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

"""The preset's effect, edited over one live preset.

**What a write reaches, and what it does not.**  The application's
write path is a whole-preset dump, and the unit's OS (v2.03, read from
the ROM) commits a received dump by copying only the live engine's
120-byte track-and-pile snapshot into the preset's stored bytes --
offsets 32 through 151.  **The effect block (offsets 152 through 163)
is never written by a dump.**  An effect edit made here is real: it is
decoded into the unit's edit buffer, it applies to what the unit is
playing, and the send's read-back verifies it byte for byte.  But the
preset's *stored* effect block keeps its own values, so selecting the
preset again loads the stored effect and this edit is gone.  What
persists an effect change is the unit's panel gesture -- ENTER/SAVE,
which stores the whole edit buffer, effect block included -- and the
screen's note says so rather than letting the verification imply more
than the instrument does.

**The rows are the model's, not this screen's.**  The effect is not one
parameter but a whole family of them: the specification prints six
parameter sets (§5.2), each of which says what the ten bytes of the
effect mean, and the number of the effect says which set applies.  A
screen that listed the rows by hand would be wrong the moment the
specification was read again, and it would be wrong in a way nobody
could see -- the wrong row would simply be there.  So this screen asks
:mod:`sq.presets` for the rows and asks it for the values as well:
:meth:`sq.presets.Effect.rows` says which parameters the active effect
has, :meth:`sq.presets.Effect.display` says what the panel prints for
one of them, and :meth:`sq.presets.Effect.with_value` is the only way
anything is written.  Every conversion between the panel's number and
the byte in the effect therefore goes through the model, and the model's
own tables are the only place a range, a mask or a list of names is
written down.

**Changing the effect's type is a different kind of edit.**  The
specification is explicit (§5.1) that the number and the parameter
bytes belong together: the number says what the bytes mean, and a new
number is a new effect.  So the type row asks first -- a confirmation
that says what is about to happen -- and only then changes the number.
The parameter bytes keep the values they have, because the unit is the
one that reinterprets them, and this program cannot know what the
factory package it loads will do with them.

**A byte can hold two parameters.**  The modulation destination takes
the high four bits of byte four and the modulation source the low
four; the rotary speaker's speed mode shares a byte with its source;
the guitar effect's compression threshold shares one with its input
level.  Each of those is a row of its own here, and each row is edited
through the model, which masks the bits it does not own -- so setting
the modulation source cannot disturb the modulation destination that
lives in the same byte.

**The screen owns no device.**  It edits a :class:`sq.presets.Preset`
object in memory and hands it to the application through ``on_apply``,
which is where the send and the read-back live.  The screen says what
it did; the application says whether it landed.  Whether the
application's write path is open at all is told to the screen rather
than asked of it, and a screen that may not write says so and changes
nothing: a change that cannot be sent is not a change.
"""

from __future__ import annotations

from typing import (
    Any,
    Callable,
    ClassVar,
    Dict,
    List,
    NamedTuple,
    Optional,
    Tuple,
)

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, Label, Static

from ensqsqed.choices import ChoicePicker
from ensqsqed.gate import EditGate
from sq import presets
from sq.presets import Effect, EffectParam, Preset
from vinsynlib.keys import wrap_blocks
from vinsynlib.ui.confirm import ConfirmScreen
from vinsynlib.ui.prompt import PromptScreen

__all__ = ["FX_HINTS", "TYPE_CHANGE_WARNING", "FxPrompt", "FxScreen"]

#: The hints under the table, as blocks.  The screen wraps them with
#: :func:`vinsynlib.keys.wrap_blocks`, so the footer wraps rather than
#: truncating: a footer that silently drops its last keys teaches the
#: reader that those keys do not exist.
FX_HINTS: Tuple[str, ...] = (
    "⏎ edits",
    "a digit opens the prompt",
    "+/- adjust",
    "z undo",
    "w write gate",
    "esc closes",
)

#: The width the hints are wrapped to.  The modal is 78 columns wide
#: with two of padding and two of border on each side, which leaves
#: this many for the text.
HINT_WIDTH = 72

#: What the confirmation before an effect-type change says.  The number
#: and the parameter bytes belong together, so the change is worth a
#: question: the unit loads a factory package, and the other bytes keep
#: the values they have until the unit reinterprets them.
TYPE_CHANGE_WARNING = (
    "changing the effect type loads a factory package on the unit; the "
    "other parameter bytes keep their stored values until the unit "
    "reinterprets them"
)


class _Row(NamedTuple):
    """One line of the table.

    ``param`` is the model's own row, or ``None`` for the first line,
    which is the effect's type -- a number that is not a parameter of
    any set, and the one row that changes what the other rows are.
    """

    key: str
    label: str
    param: Optional[EffectParam]


class FxPrompt(PromptScreen):
    """The shared prompt, with the cursor where the family puts it.

    The shared screen leaves the cursor at the start of the field, so
    the first keystroke lands in front of the value that is being
    changed -- "544" where "50" was meant.  This puts it at the end,
    and selects the whole value when the seed is the value being
    changed and the first keystroke is to replace it.
    """

    def __init__(
        self, prompt: str, current: str = "", *, select_all: bool = False
    ) -> None:
        super().__init__(prompt, current)
        self._select_all = select_all

    def on_mount(self) -> None:
        super().on_mount()
        field = self.query_one(Input)
        field.focus()
        if self._select_all:
            field.select_all()
        else:
            # The cursor at the end, so a prompt opened by typing a
            # digit continues that number rather than inserting in
            # front of it.
            field.cursor_position = len(field.value)


class FxScreen(ModalScreen[None]):
    """One preset's effect, edited over the live preset.

    **The constructor's contract.**  ``preset`` is the live
    :class:`sq.presets.Preset`; the screen edits that object and hands
    it back, so the application sends the same object it read.

    ``on_apply(preset, description)`` is called after every change, with
    the preset and a sentence saying what changed -- "effect type →
    HALL.REVERB", "DECAY TIME → 45".  The application owns the send and
    the read-back; the screen owns neither.

    ``on_undo(count)`` is called by ``z`` (one change) and ``Z`` (all of
    them), with ``None`` meaning all -- the family's shape.

    ``say_(message, refused=...)`` is an optional second route for the
    sentence -- a test's capture, or a caller that wants to log it.  The
    app does **not** pass one: the browser's status line is the dimmed
    backdrop behind this modal, and a warning written there is written
    where nobody is looking.  ``result_cb(ok, message)`` is told what an
    edit did.  Both are optional: without them the screen says things on
    its own status line only.

    ``allow_write`` says whether the application's write path is open.
    A screen that may not write says so and changes nothing: a change
    that cannot be sent is not a change, so the refusal comes before
    the value goes into the preset.

    **The seams the application looks for.**  :meth:`say` and
    :meth:`result` are how a worker reports what happened, and both
    tolerate the screen having been closed underneath them: a reply to a
    screen nobody is looking at is a sentence in the void, not a crash.
    """

    DEFAULT_CSS = """
    FxScreen { align: center middle; }
    FxScreen > Vertical {
        width: 78; height: 85%; border: thick $accent;
        background: $surface; padding: 1 2;
    }
    FxScreen DataTable { height: 1fr; }
    FxScreen .note { color: $text-muted; }
    /* The refusal names the key that opens the gate, which does not fit
       on one line at this width; a fixed height clipped the remedy. */
    FxScreen .status { height: auto; padding: 0 1; }
    /* The browser's alarm, on this screen's own line: a refusal is red
       wherever it is said, so it cannot be mistaken for news. */
    FxScreen .status.refused {
        background: $error; color: $text; text-style: bold;
    }
    FxScreen .hint { color: $text-muted; }
    """

    BINDINGS: ClassVar[list[Any]] = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
        # The same gestures as the sibling screens: Enter, a digit, or
        # +/-. An effect value should not be changed one way here and
        # another way one screen over.
        Binding("plus", "bump(1)", "+1", show=False),
        Binding("equals_sign", "bump(1)", "+1", show=False),
        Binding("minus", "bump(-1)", "-1", show=False),
        Binding("z", "undo", "Undo"),
        Binding("Z", "undo_all", "Undo all"),
        # `w` is the app's key, served here for the reason `z` is: a
        # modal screen cuts the app's binding chain, so the gate could
        # only be armed by closing the editor -- leaving the warning
        # that said so on the dimmed backdrop behind it.
        Binding("w", "toggle_write_gate", "Write gate"),
    ] + [
        Binding(str(digit), f"type_digit('{digit}')", show=False) for digit in range(10)
    ]

    #: The key of the first row: the effect's type.
    TYPE_KEY = "type"

    #: The two mix rows every effect has, named by the model's own
    #: keys.  They are always offered, and they are the model's rows
    #: rather than rows written here.
    MIX_KEYS = ("fx1_mix", "fx2_mix")

    def __init__(
        self,
        preset: Preset,
        *,
        on_apply: Callable[[Preset, str], None],
        on_undo: Optional[Callable[[Optional[int]], None]] = None,
        say_: Optional[Callable[..., None]] = None,
        result_cb: Optional[Callable[[bool, str], None]] = None,
        allow_write: bool = False,
        gate: Optional[EditGate] = None,
        title_prefix: str = "Preset effect",
    ) -> None:
        super().__init__()
        #: The live preset this screen edits.  It is edited in place,
        #: so the object the application holds is the object that
        #: changes.
        self._preset = preset
        self._on_apply = on_apply
        self._on_undo = on_undo
        self._say_hook = say_
        self._result_hook = result_cb
        self._allow_write = allow_write
        #: The app's own gate, when there is an app.  Held rather than
        #: copied so `w` here toggles the object every edit is checked
        #: against, and the note line can be redrawn from it.
        self._gate = gate
        self._title_prefix = title_prefix
        #: The rows the table shows, in order.
        self._rows: Tuple[_Row, ...] = ()
        #: The effect as it was before the last change, so a change the
        #: unit refused can be put back.
        self._before: Optional[Effect] = None
        #: The effect number a confirmation is waiting about.
        self._pending_number: Optional[int] = None
        #: What each row can take, cached: the answer is the same for
        #: every edit and it is not cheap to work out.
        self._reachable_cache: Dict[EffectParam, Tuple[int, ...]] = {}

    # --- layout -------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._title_text(), id="fx-title")
            yield Static(self._note_text(), classes="note")
            yield DataTable(id="fx-table", cursor_type="row", zebra_stripes=True)
            yield Static("", classes="status", id="fx-status")
            yield Static(wrap_blocks(FX_HINTS, HINT_WIDTH), classes="hint")

    def on_mount(self) -> None:
        self._rebuild()
        self.query_one("#fx-table", DataTable).focus()

    def _title_text(self) -> str:
        """The heading: what this screen is editing."""
        effect = self._preset.effect
        name = effect.name or f"effect {effect.number}"
        return f"[b]{self._title_prefix} — {name}[/b]"

    def _note_text(self) -> str:
        """The line under the heading: which set the rows come from, and
        what the application's write gate does now.

        The consequence is spelled out rather than left to the word
        "locked": a reader who has just watched an edit do nothing should
        not have to work out that the gate was the reason or which key
        opens it.
        """
        effect = self._preset.effect
        set_name = effect.parameter_set or "unknown"
        state = self._gate_description()
        if not self._writes_allowed():
            state += " — edits are refused; w arms it"
        return (
            f"parameter set: {set_name} · {state} — an effect edit lives "
            "in the unit's edit buffer and reverts when the preset is "
            "reselected; the unit's ENTER/SAVE is what stores an effect "
            "change"
        )

    def _gate_description(self) -> str:
        """The gate's state, read from the gate itself when there is one.

        A copy of the boolean would go stale the moment `w` was pressed
        here: the note would go on saying "locked" while the edits went
        through, which is worse than not saying it at all.
        """
        if self._gate is not None:
            return self._gate.describe()
        return "write gate armed" if self._allow_write else "write gate locked"

    def _writes_allowed(self) -> bool:
        """Whether an edit made now may be sent."""
        if self._gate is not None:
            return self._gate.armed
        return self._allow_write

    def _refresh_note(self) -> None:
        """Redraw the gate line after the gate changes."""
        try:
            self.query_one(".note", Static).update(self._note_text())
        except Exception:  # pragma: no cover - the screen closed under us
            return

    # --- the rows -----------------------------------------------------------

    def _build_rows(self) -> Tuple[_Row, ...]:
        """The rows for the effect as it is now.

        The first is the type; the rest are the active parameter set's
        own rows, plus the two mix rows every effect has.
        """
        rows: List[_Row] = [_Row(self.TYPE_KEY, "EFFECT TYPE", None)]
        for param in self._effect_params():
            rows.append(_Row(param.key, param.label, param))
        return tuple(rows)

    def _effect_params(self) -> Tuple[EffectParam, ...]:
        """The model's rows for this effect.

        The active set's rows, and then the two mix rows -- which every
        set defines anyway, and which are looked up in the tables rather
        than written here, so that an effect whose set left them out
        still offers them.
        """
        rows = list(self._preset.effect.rows)
        known = {row.key for row in rows}
        for wanted in self.MIX_KEYS:
            if wanted in known:
                continue
            found = self._find_row(wanted)
            if found is not None:
                rows.append(found)
        return tuple(rows)

    @staticmethod
    def _find_row(key: str) -> Optional[EffectParam]:
        """The first row with this key in any of the parameter sets."""
        for name in sorted(presets.PARAM_SETS):
            for row in presets.PARAM_SETS[name]:
                if row.key == key:
                    return row
        return None

    def _rebuild(self) -> None:
        """Rebuild the rows and the table.

        The rows belong to the effect's parameter set, so a change of
        type is a different table -- the same reason the sibling
        rxved's effects screen rebuilds when its algorithm changes.

        Guarded throughout: a worker can report a change after the
        screen it was started from has closed, and a reply to a screen
        nobody is looking at is a sentence in the void, not a crash.
        """
        self._rows = self._build_rows()
        try:
            self._draw_table()
            self.query_one("#fx-title", Label).update(self._title_text())
            self.query_one(".note", Static).update(self._note_text())
        except Exception:  # pragma: no cover - the screen closed under us
            return

    def _draw_table(self) -> None:
        """Draw every row."""
        table = self.query_one("#fx-table", DataTable)
        where = table.cursor_coordinate
        table.clear(columns=True)
        table.add_column("parameter", key="parameter")
        table.add_column("value", key="value")
        table.add_column("range", key="range")
        for row in self._rows:
            table.add_row(
                row.label,
                self._value_of(row),
                self._span_of(row),
                key=row.key,
            )
        if table.row_count:
            table.cursor_coordinate = where

    def _draw_row(self, key: str) -> None:
        """Redraw one row's value."""
        row = self._row_by_key(key)
        if row is None:
            return
        try:
            self.query_one("#fx-table", DataTable).update_cell(
                key, "value", self._value_of(row)
            )
        except Exception:  # pragma: no cover - the screen closed under us
            return

    def _row_by_key(self, key: str) -> Optional[_Row]:
        """The row with this key, if the screen is showing it."""
        for row in self._rows:
            if row.key == key:
                return row
        return None

    def _current_row(self) -> Optional[_Row]:
        """The row the cursor is on."""
        try:
            table = self.query_one("#fx-table", DataTable)
        except Exception:  # pragma: no cover - the screen closed under us
            return None
        if not table.row_count:
            return None
        index = table.cursor_row
        if not 0 <= index < len(self._rows):
            return None
        return self._rows[index]

    def _value_of(self, row: _Row) -> str:
        """What the row reads now.

        **A value outside the row's documented range shows a question
        mark**, the way the command line shows it.  A dump is a fact
        about the instrument's memory, and the unit may hold a byte the
        specification's list does not have -- the modulation
        destination of a set with fewer than sixteen of them, say.  A
        refusal here would take the whole table with it: the rows
        below the bad one would never be drawn, and the screen would
        show a part of the effect with nothing saying why.
        """
        if row.param is None:
            effect = self._preset.effect
            return effect.name or f"effect {effect.number}"
        try:
            value = self._preset.effect.display(row.param)
        except ValueError:
            return "?"
        return value if isinstance(value, str) else str(value)

    def _span_of(self, row: _Row) -> str:
        """The range the row may take, as the third column says it."""
        if row.param is None:
            return f"0 - {len(presets.EFFECTS) - 1}"
        param = row.param
        if param.names is not None:
            if len(param.names) <= 4:
                return " / ".join(param.names)
            return f"{param.low} - {param.high} ({len(param.names)} named)"
        low, high = self._range_of(param)
        return f"{low} - {high}"

    # --- the model's conversions --------------------------------------------

    def _reachable(self, param: EffectParam) -> Tuple[int, ...]:
        """The displayed values this row can take, in order.

        Every step of this is the model's: a raw value is read with
        :meth:`sq.presets.EffectParam.displayed`, and only the values
        the model can encode again are listed.  That is why a row
        stored 0..15 steps by six and not by one -- the panel prints
        those sixteen values through the same table every other
        parameter uses -- and why the numbers here are the panel's and
        not the bytes'.
        """
        cached = self._reachable_cache.get(param)
        if cached is not None:
            return cached
        values: List[int] = []
        for raw in range((param.mask >> param.shift) + 1):
            try:
                value = param.displayed(raw)
            except ValueError:
                continue
            if isinstance(value, int) and value not in values:
                values.append(value)
        values.sort()
        result = tuple(values)
        self._reachable_cache[param] = result
        return result

    def _range_of(self, param: EffectParam) -> Tuple[int, int]:
        """The lowest and highest value the row can take."""
        values = self._reachable(param)
        if not values:
            return (param.low, param.high)
        return (values[0], values[-1])

    def _current_number(self, param: EffectParam) -> Optional[int]:
        """The row's value as a number, or None if it is not one.

        None covers the two cases the caller has to refuse anyway: a
        row that names its values, and a row holding a value outside
        the range the specification documents -- which the unit may
        have written and which this program did not.
        """
        try:
            value = self._preset.effect.display(param)
        except ValueError:
            return None
        if isinstance(value, int):
            return value
        return None

    # --- editing ------------------------------------------------------------

    def on_data_table_row_selected(self, event) -> None:
        """Enter on a row offers that one edit.

        A DataTable consumes Enter for its own row selection, so the
        edit arrives here rather than from a binding.
        """
        if event.data_table.id == "fx-table":
            self.action_edit()

    def action_edit(self) -> None:
        """Open the edit the row asks for.

        An enumeration is not a number to type: the effect's type and
        the rows that name their values are lists to pick from.  A
        number is typed.
        """
        row = self._current_row()
        if row is None:
            return
        if row.param is None:
            self._choose_type()
            return
        param = row.param
        if param.names is not None:
            self._choose(param)
            return
        current = self._current_number(param)
        self._prompt(
            param,
            "" if current is None else str(current),
            select_all=True,
        )

    def action_type_digit(self, digit: str) -> None:
        """A digit opens the prompt with that digit in it."""
        row = self._current_row()
        if row is None:
            return
        if row.param is None or row.param.names is not None:
            self.say(
                f"{row.label} is a list of names — press ⏎ to pick one",
                refused=True,
            )
            return
        self._prompt(row.param, digit)

    def action_bump(self, delta: int) -> None:
        """One step of the row's own values, up or down.

        A step is a step of the row: a parameter the panel prints 0..99
        steps by one, and one the panel prints in sixteen steps steps by
        one of those.  Either way the value that goes into the byte is
        the model's own conversion of the panel's number.
        """
        row = self._current_row()
        if row is None:
            return
        param = row.param
        if param is None:
            self.say(
                "the effect type is a list of names — press ⏎ to pick one",
                refused=True,
            )
            return
        if param.names is not None:
            self._bump_name(param, delta)
            return
        values = self._reachable(param)
        current = self._current_number(param)
        if current is None:
            # The unit holds a value this program cannot print: the row
            # says "?" and there is no step to take from it.
            self.say(
                f"{param.label} holds a value this program cannot print",
                refused=True,
            )
            return
        if current not in values:
            self.say(
                f"{param.label} reads {current}, which is not a value it can take",
                refused=True,
            )
            return
        index = values.index(current)
        wanted = index + delta
        if not 0 <= wanted < len(values):
            low, high = self._range_of(param)
            self.say(
                f"{param.label} takes {low} to {high}, not {current + delta}",
                refused=True,
            )
            return
        self._apply(param, values[wanted])

    def _bump_name(self, param: EffectParam, delta: int) -> None:
        """One step through an enumerated row's names."""
        names = param.names or ()
        index = self._preset.effect.raw(param)
        wanted = index + delta
        if not 0 <= wanted < len(names):
            edge = names[-1] if delta > 0 else names[0]
            where = "after" if delta > 0 else "before"
            self.say(f"{param.label} takes no value {where} {edge}", refused=True)
            return
        self._apply(param, names[wanted])

    def _choose_type(self) -> None:
        """The list of the thirteen effects."""
        effect = self._preset.effect
        self.app.push_screen(
            ChoicePicker("Effect type", list(presets.EFFECTS), effect.number),
            self._type_picked,
        )

    def _type_picked(self, index: Optional[int]) -> None:
        """An effect was picked: ask before it is taken."""
        if index is None:
            return
        if index == self._preset.effect.number:
            self.say("the effect type is unchanged")
            return
        self._pending_number = index
        self.app.push_screen(
            ConfirmScreen(TYPE_CHANGE_WARNING),
            self._type_confirmed,
        )

    def _type_confirmed(self, confirmed: Optional[bool]) -> None:
        """The confirmation's answer.

        The number changes and the parameter bytes keep the values they
        have: the unit loads a factory package for the new effect and
        reinterprets those bytes itself.
        """
        number = self._pending_number
        self._pending_number = None
        if not confirmed or number is None:
            return
        if not self._writes_allowed():
            self._refuse_write()
            return
        effect = self._preset.effect
        self._before = effect
        self._preset.effect = Effect(number=number, params=effect.params)
        self._rebuild()
        self._on_apply(self._preset, f"effect type → {presets.EFFECTS[number]}")

    def _choose(self, param: EffectParam) -> None:
        """The list of an enumerated row's values."""
        names = param.names or ()
        current = self._preset.effect.raw(param)

        def picked(index: Optional[int]) -> None:
            if index is None:
                return
            self._apply(param, names[index])

        self.app.push_screen(ChoicePicker(param.label, list(names), current), picked)

    def _prompt(
        self, param: EffectParam, seed: str, *, select_all: bool = False
    ) -> None:
        """The typed prompt for a numeric row.

        The seed is the digit that was pressed, or the value the row
        holds.  The two want the cursor in different places: a digit
        continues a number, and a value that is being changed is
        replaced whole.
        """
        low, high = self._range_of(param)

        def done(text: Optional[str]) -> None:
            if text is None or not text.strip():
                return
            try:
                value = int(text.strip())
            except ValueError:
                self.say(f"{text!r} is not a number", refused=True)
                return
            self._apply_number(param, value)

        question = FxPrompt(
            f"{param.label} ({low} to {high})", seed, select_all=select_all
        )
        self.app.push_screen(question, done)

    def _apply_number(self, param: EffectParam, value: int) -> None:
        """A number that was typed, checked against the row's range."""
        low, high = self._range_of(param)
        if not low <= value <= high:
            self.say(
                f"{param.label} takes {low} to {high}, not {value}",
                refused=True,
            )
            return
        self._apply(param, value)

    def _apply(self, param: EffectParam, value: int | str) -> None:
        """Put one value into the effect and hand the preset over.

        The write goes through the model, so the bits of the byte that
        belong to another parameter keep their values.
        """
        if not self._writes_allowed():
            self._refuse_write()
            return
        effect = self._preset.effect
        try:
            updated = effect.with_value(param, value)
        except ValueError as exc:
            self.say(str(exc), refused=True)
            return
        self._before = effect
        self._preset.effect = updated
        self._draw_row(param.key)
        self._on_apply(self._preset, f"{param.label} → {self._shown(value)}")

    @staticmethod
    def _shown(value: int | str) -> str:
        """The value as the description says it."""
        return value if isinstance(value, str) else str(value)

    # --- the seams the application looks for --------------------------------

    def say(self, message: str, refused: bool = False) -> None:
        """One sentence on this screen's own status line.

        The application's status line is the dimmed backdrop behind
        this modal, so a refusal written there is written where nobody
        is looking.  ``[b]refused[/b] · `` in front of a refusal,
        because the difference between a "no" and news is carried by
        the words and the weight, not by the colour alone.
        """
        prefix = "[b]refused[/b] · " if refused else ""
        self._write_status(prefix + message, refused=refused)
        if self._say_hook is not None:
            self._say_hook(message, refused=refused)

    def result(
        self,
        ok: bool,
        message: str,
        *,
        row: Optional[str] = None,
        value: object = None,
    ) -> None:
        """What an edit did, from the application's worker.

        A success redraws the row it touched, so the table says what
        the unit holds rather than what it was asked to hold.  A
        failure puts the old value back: nothing was sent, so the
        screen must not go on showing a change that did not happen.
        """
        if ok:
            if row is not None:
                self._draw_row(row)
        elif self._before is not None:
            self._preset.effect = self._before
            self._rebuild()
        self._before = None
        self.say(message, refused=not ok)
        if self._result_hook is not None:
            self._result_hook(ok, message)

    def _write_status(self, text: str, *, refused: bool = False) -> None:
        """Put a sentence on the status line, if there still is one.

        ``refused`` turns the line into the browser's red alarm; a
        success clears it again, so the colour always describes the
        sentence that is there now.
        """
        try:
            status = self.query_one(".status", Static)
        except Exception:  # pragma: no cover - the screen closed under us
            return
        status.update(text)
        status.set_class(refused, "refused")

    def _refuse_write(self) -> None:
        """Say that this screen may not write, and change nothing.

        A change that cannot be sent is not a change, so the refusal
        comes before the value is put into the preset.  The sentence
        carries the remedy as well as the state, and `w` works here, so
        the reader can act on it without closing the editor.
        """
        if self._gate is not None:
            self.say(self._gate.refuse("the change"), refused=True)
            return
        self.say(
            "edits are refused — the app has not opened the write path",
            refused=True,
        )

    # --- the keys that are not edits ----------------------------------------

    def action_close(self) -> None:
        """``escape`` and ``q``."""
        self.dismiss(None)

    def action_toggle_write_gate(self) -> None:
        """``w`` here: the app's own gate, toggled where it is shown.

        The gate is the app's, handed in rather than copied, so this
        toggles the one the write path checks.  The note is redrawn and
        the state is said on this screen's own line -- the app's status
        line is the dimmed backdrop behind the modal, which is exactly
        where the reader was not looking.
        """
        if self._gate is None:
            self.say("this screen has no write gate to toggle", refused=True)
            return
        self._gate.toggle()
        self._refresh_note()
        self.say(self._gate.describe())

    def action_undo(self) -> None:
        """``z``: one change back."""
        if self._on_undo is not None:
            self._on_undo(1)

    def action_undo_all(self) -> None:
        """``Z``: every change back."""
        if self._on_undo is not None:
            self._on_undo(None)

    # --- the app's seam ----------------------------------------------------

    def redraw(self) -> None:
        """Draw the rows again from the model.

        **Cross-task fix (T8).**  The app's undo puts the *previous*
        effect back into the live model -- the model's bytes are what
        the next edit is built on -- and the rows have to say so.  The
        screen's own :meth:`result` redraws the one row it knows about;
        the whole table is a different set of rows when the effect type
        has changed, so it is rebuilt.
        """
        self._rebuild()
