# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# The screen shapes are ported from the sibling rxved and p2ked projects,
# which port them from s3ked, eosed and k2kremote:
#   Copyright (C) 2026  rxved / p2ked / s3ked / eosed / k2kremote
#   contributors                                     - GPL-2.0-or-later
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

Two modal screens over one live :class:`sq.presets.Preset` object.

**The preset list** (:class:`PresetListScreen`) answers a question the
browser cannot: *which* of the eighty presets the unit holds is the one
being edited.  Reading them all is one message and about twelve seconds,
so the screen is shown when the data has arrived and not before -- the
spinner belongs to the app's worker, not to a modal that has nothing to
do while it waits.

**The editor** (:class:`MultiScreen`) is the eight tracks of one preset:
the parameters the rack's Track Edit Mode shows, in four column groups
that ``tab`` cycles, edited with the family's gestures -- ``⏎`` edits the
focused cell, a digit starts a number, and ``+`` and ``-`` nudge it.

**The stored on/off marker is pressure 3, and ``space`` edits it.**
The owner's P20-P23 matrix settles it: the pile is `01 00…` in all
four, while track byte 8's pressure bits carry the state — pressure 1
(KEY) is the printed track number, pressure 3 is the panel's `-`.  The
`on` column reads that marker, and ``space`` toggles it between 1 and 3.
It is a normal stored field, so the whole preset goes and ``ctrl+s``
stores it with the unit's Enter/Save three times.

**The live mask is a separate RAM state.**  ``l`` sends the Track-pair
chord (buttons 10 and 12) for the track under the unit's cursor, and
``[``/``]`` move that cursor.  The screen cannot read the cursor or the
mask, so a live sentence names the unit's track, not the app's row.
``p`` opens the preset list, as it does in the main window; the screen
asks the app to open it and never owns the browser or the selection.

**Any track can be off, including all eight.**  Pressure 3 on every
track is a valid stored state; there is no mandatory primary track or
pile entry.

**A screen writes nothing by itself.**  A change is handed to the app
through ``on_apply``, and the app does the gate, the send, the read-back
and the undo record.  The screen holds the one live
:class:`~sq.presets.Preset` object, changes it, and shows what it now
says -- which is the truth about the model, and about the unit only once
the app has said the write landed.  The one action that has to know
about the unit is the store assist (``ctrl+shift+s``): it reads a backed-up
slot out of the newest dump, asks the owner which one and confirms it,
and hands the 168 bytes to the app, which does the gate, the send and
the verified retry.  The screen still owns no device and starts no
worker.

**What the panel numbers and what the dump numbers are not the same
number.**  The unit's display shows its preset locations as ``P00``
through ``P79`` -- zero-based, the owner's reading of the display on
2026-10-22 -- which is the same zero-based counting the dump's slots
use, so the app speaks one numbering everywhere: the display's ``P``
numbers.  (The manual's ``PSET 001`` wording is one-based; the display
wins.)  Both are in the class docstring, because an off-by-one here
would be invisible until it had already written the wrong preset.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, Label, Static

from ensqsqed.choices import ChoicePicker
from ensqsqed.gate import EditGate
from sq import banks
from sq import messages as m
from sq.bridge import (
    TRACK_LEFT_BUTTON,
    TRACK_RIGHT_BUTTON,
    AllPresetsDump,
    DeviceError,
    PresetAlert,
)
from sq.config import DEFAULT_BACKUP_ROOT
from sq.presets import (
    OUTPUT_NAMES,
    PAN_SOUND,
    PRESSURE_NAMES,
    PRESSURE_OFF,
    STATUS_NAMES,
    SUSTAIN_NAMES,
    TRACK_RANGES,
    Preset,
    Track,
    display_value,
    note_name,
    pan_display,
    pan_stored,
    stored_value,
)
from vinsynlib.keys import wrap_blocks
from vinsynlib.ui.confirm import ConfirmScreen

__all__ = [
    "BACKUP_DUMP_NAME",
    "COLUMN_VIEWS",
    "KEY_HINTS_BLOCK",
    "LOCAL_STATUS",
    "ChoicePicker",
    "MultiScreen",
    "NumberPrompt",
    "TextPrompt",
    "PresetListScreen",
    "SoundPickerScreen",
    "StoreAssistError",
    "backup_slot_raw",
    "newest_backup",
]

#: The file a backup run writes, and the one the store assist reads.
BACKUP_DUMP_NAME = "all_presets_dump.bin"

#: The number of presets in the all-presets dump.  The parser is given
#: the count the alert carries, and every backup the bench wrote has all
#: eighty, whether or not a slot was ever changed.
BACKUP_PRESET_COUNT = 80

#: One preset, in bytes.  A backup slot is sent verbatim, so the length
#: it must have is the whole of what "verbatim" means here.
BACKUP_PRESET_BYTES = 168


class StoreAssistError(ValueError):
    """The backup cannot answer the question the store assist asked.

    A :class:`ValueError` rather than a device error: nothing has been
    sent and the unit is not involved.  The sentence is written to be
    shown to the user, naming the directory or the file that could not
    answer, because a refusal that does not say where it looked is a
    refusal a user cannot act on.
    """


def newest_backup(root: Optional[str]) -> Optional[Path]:
    """The newest all-presets dump under ``root``, or ``None``.

    The backup runs write stamped directories -- ``<stamp>/`` -- and
    the investigation read the newest of them.  The stamp is a
    zero-padded date and time, so its directory name sorts the same way
    its time does; the modification time is the tie-break for two
    directories that cannot be told apart by name.  A dump directly in
    ``root`` is considered too, so a single hand-kept copy works.

    No backup at all is not an error here: ``None`` is the answer, and
    the caller says so in words.  A path that does not exist is the
    same answer for the same reason.
    """
    if not root:
        return None
    base = Path(root).expanduser()
    candidates = [path for path in base.glob(f"*/{BACKUP_DUMP_NAME}") if path.is_file()]
    direct = base / BACKUP_DUMP_NAME
    if direct.is_file():
        candidates.append(direct)
    if not candidates:
        return None

    def stamp(path: Path) -> Tuple[str, float]:
        """The run's name, with a plain dump in the root below any run."""
        name = "" if path.parent == base else path.parent.name
        return name, path.stat().st_mtime

    return max(candidates, key=stamp)


def backup_slot_raw(root: Optional[str], slot: int) -> bytes:
    """One slot's backed-up 168 bytes, verbatim.

    **The array order is not the slot order.**  The dump holds the
    presets in the order the unit laid them out, and the preset table
    says which one belongs to which slot -- slot 0 is array 10, and
    slots 70 through 79 are arrays 0 through 9 on the bench's dump --
    so the bytes are looked up through :meth:`AllPresetsDump.slot_offsets`
    and never by indexing ``presets_raw`` with a slot number.

    The bytes are returned exactly as the dump holds them, address
    bytes included.  This protocol has no per-slot addressing: the
    unit rewrites those bytes to the selected slot on receipt, and
    stripping them here would be inventing a different payload rather
    than sending the backup.
    """
    path = newest_backup(root)
    if path is None:
        where = root or DEFAULT_BACKUP_ROOT
        raise StoreAssistError(
            f"no {BACKUP_DUMP_NAME} was found under {where} — there are no "
            "backed-up bytes to send"
        )
    try:
        payload = path.read_bytes()
        dump = AllPresetsDump._from_reply(
            payload,
            PresetAlert(
                command=m.CommandType.ALL_SEQUENCE_ALERT,
                size=0,
                flag=0,
                count=BACKUP_PRESET_COUNT,
            ),
        )
        offsets = dump.slot_offsets()
    except (OSError, DeviceError, IndexError, ValueError) as exc:
        raise StoreAssistError(
            f"the backup {path} could not be read as a preset dump: {exc}"
        ) from exc
    if slot not in offsets:
        raise StoreAssistError(f"the backup {path} holds no P{slot:02d}")
    raw = dump.presets_raw[offsets[slot]]
    if len(raw) != BACKUP_PRESET_BYTES:
        raise StoreAssistError(
            f"the backup {path} holds {len(raw)} bytes for P{slot:02d}; "
            f"a preset is {BACKUP_PRESET_BYTES}"
        )
    return bytes(raw)


#: The MIDI status a track that shall play its internal voices carries.
#: LOCAL -- it receives, it plays, and it is the status the panel calls
#: the track being on.  It is the value a stored *EXT* is repaired to;
#: the repair is a repair of the *stored* preset, and the unit applies
#: it when that preset is selected again.
LOCAL_STATUS = STATUS_NAMES.index("LOCAL")


def _literal(text: str) -> str:
    """``text`` as markup that renders as exactly itself.

    A preset's name is sixteen bytes the unit wrote, and a name holding
    a square bracket would otherwise be read as markup -- which is a
    crash in a label and a lie in a table cell.  The backslash is
    Textual's own escape.
    """
    return text.replace("[", "\\[")


#: The keys this screen answers, in the order somebody meets them.
#:
#: The same list in every tool of the family that has an editor over a
#: table of values: the arrows move, ``tab`` changes what the columns
#: are, ``⏎`` edits the cell under the cursor, a digit types, ``+`` and
#: ``-`` nudge.  The Track pair's keys are this screen's own: ``[``/``]``
#: move the unit's cursor and ``l`` sends its live-mute chord.  ``p``
#: opens the preset list, as it does in the main window.
KEY_HINTS_BLOCK: Tuple[str, ...] = (
    "↑↓ move",
    "tab columns",
    "⏎ edit",
    "0-9 type",
    "+ - nudge",
    "space track on/off",
    "x fx",
    "p presets",
    "ctrl+r reload",
    "w write gate",
    "r label",
    "R rename unit",
    "esc close",
)

#: How wide the hint line is wrapped.  The panel is 90 wide and the
#: padding takes four, so this is the panel's own width.
HINT_WIDTH = 86


@dataclass(frozen=True)
class _Column:
    """One column of the track table, and what an edit on it would write.

    ``kind`` is the whole of the editing decision, and it is declared per
    column rather than derived from a range: a column whose values are
    words is a list to pick from, a column whose values are numbers is a
    number to type, and the two are told apart by the words the manual
    uses, not by a guess about the byte behind them.

    ``field`` is the :class:`sq.presets.Track` attribute the column
    writes, empty for the columns that write nothing -- the track's own
    number, and the sound, which the app's picker owns.

    ``low`` and ``high`` are the range the value is *typed* in, which is
    the panel's range and not always the stored one: the volume is typed
    0-99 and stored 0-127, and the channel is typed 1-16 and stored
    0-15.  ``fmt`` says how the two are related.

    ``names`` is the panel's own words for an enumerated value, in the
    order the field numbers them, so that a pick answers with the
    integer the field stores.

    ``bool_field`` marks the one column whose stored value is a bit and
    whose panel value is a name: the sustain switch.  ON is the first of
    the two names and the bit is the opposite of it, so the index into
    ``names`` is not the stored value.
    """

    key: str
    label: str
    field: str = ""
    kind: str = "read"  # "read" | "number" | "enum"
    low: int = 0
    high: int = 0
    names: Tuple[str, ...] = ()
    step: int = 1
    fmt: str = ""
    bool_field: bool = False
    width: int = 0  # 0 = auto

    # --- reading -----------------------------------------------------------

    def stored_of(self, track: Track) -> int:
        """The track's own value for this column.

        For an enumerated column this is the index into :attr:`names`,
        which is the value the field stores -- except for the sustain
        switch, where the byte is a bit and the names run the other way.
        """
        value = getattr(track, self.field)
        if self.bool_field:
            return 0 if value else 1
        return int(value)

    def text_of(self, track: Track) -> str:
        """What the cell shows for a track.

        The panel's wording, not the stored number: "SOUND" and not
        -128, "A0" and not 21, "+1 +07" and not 19.  A dump that holds
        a value outside a documented range shows "?" rather than
        raising -- a corrupt preset is still a row somebody has to be
        able to look at.
        """
        if self.kind == "enum":
            index = self.stored_of(track)
            if 0 <= index < len(self.names):
                return self.names[index]
            return f"?{index}"
        stored = int(getattr(track, self.field))
        try:
            if self.fmt == "table":
                return str(display_value(stored))
            if self.fmt == "plus_one":
                return str(stored + 1)
            if self.fmt == "signed":
                return f"{stored:+d}"
            if self.fmt == "note":
                return note_name(stored)
            if self.fmt == "transpose":
                return f"{track.transpose_octave:+d} {track.transpose_semitone:+03d}"
            if self.fmt == "pan":
                return track.pan_label
        except ValueError:
            return "?"
        return str(stored)

    def edit_text(self, track: Track) -> str:
        """The value as it is *typed*, which is not always as it is shown.

        A key range is shown as notes and typed as numbers, because a
        note name is not a number a prompt can check.  The transpose is
        shown as octaves and semitones and typed as the semitone count,
        which is the byte.
        """
        stored = int(getattr(track, self.field))
        if self.fmt == "table":
            return str(display_value(stored))
        if self.fmt == "plus_one":
            return str(stored + 1)
        if self.fmt == "pan":
            shown = pan_display(stored)
            return "SOUND" if shown is None else str(shown)
        return str(stored)

    # --- writing -----------------------------------------------------------

    def to_stored(self, shown: int) -> int:
        """The stored value that prints as ``shown``.

        Only the two conversions that are not the identity: the
        Note 7 table (the volume and the timbre are stored 0-127 and
        printed 0-99) and the one-based numbering (the channel and the
        program are stored from 0 and printed from 1).
        """
        if self.fmt == "table":
            return stored_value(shown)
        if self.fmt == "plus_one":
            return shown - 1
        return shown

    def to_track(self, track: Track, value: int) -> Track:
        """The track with this column's value changed to ``value``.

        ``value`` is the column's own value: the index into
        :attr:`names` for an enumeration, the stored value for a
        number.  The range check is the model's, and its refusal names
        the range in words.
        """
        if self.kind == "enum" and self.bool_field:
            return track.updated(**{self.field: value == 0})
        return track.updated(**{self.field: value})


#: The row's own track number.  It writes nothing.
TRACK_COLUMN = _Column("trk", "trk", width=4)

#: The track's stored ON/OFF: pressure 3 is the panel's "-".
ON_COLUMN = _Column("on", "on", width=4)

#: The sound the track plays: the group and the slot inside it.
#:
#: The drum kits are the numbers 80 to 99 and ignore the bank code, so
#: they are shown as they are -- "~DRUM~ 01" -- rather than as a group
#: and a slot that do not exist.
SPOT_COLUMN = _Column("spot", "sound", width=10)

#: The sound's name, from the catalog the app hands in.
NAME_COLUMN = _Column("name", "name", width=16)

#: SOUND: what the track plays, what MIDI program change it answers to,
#: and **the channel it receives on**.  The channel is here rather than
#: only under ``tab`` because it is the setting MULTI mode is opened for:
#: eight tracks on eight channels is the whole point, and a per-track
#: channel the user has to hunt for reads as missing.
PRG_COLUMNS: Tuple[_Column, ...] = (
    SPOT_COLUMN,
    NAME_COLUMN,
    _Column(
        "channel",
        "ch",
        field="midi_channel",
        kind="number",
        low=1,
        high=16,
        fmt="plus_one",
    ),
    _Column(
        "program",
        "MIDI PC",
        field="midi_program",
        kind="number",
        low=1,
        high=128,
        fmt="plus_one",
        width=8,
    ),
)

#: MIDI: the rest of what the track listens to.  The channel is in the
#: SOUND group above; these are the switches around it.
#: MIDI: the status, the pressure (whose value 3 is the track OFF
#: marker), and the sustain switch.
PRESSURE_COLUMN = _Column(
    "pressure", "press", field="pressure", kind="enum", names=PRESSURE_NAMES
)
MIDI_COLUMNS: Tuple[_Column, ...] = (
    _Column("status", "status", field="status", kind="enum", names=STATUS_NAMES),
    PRESSURE_COLUMN,
    _Column(
        "sustain",
        "sustain",
        field="sustain",
        kind="enum",
        names=SUSTAIN_NAMES,
        bool_field=True,
    ),
)

#: MIX: how loud it is, where it sits, and where it goes.
MIX_COLUMNS: Tuple[_Column, ...] = (
    _Column(
        "volume", "vol", field="volume", kind="number", low=0, high=99, fmt="table"
    ),
    _Column("pan", "pan", field="pan", kind="number", low=-99, high=99, fmt="pan"),
    _Column(
        "timbre", "timbre", field="timbre", kind="number", low=0, high=99, fmt="table"
    ),
    _Column(
        "release", "rel", field="release", kind="number", low=-64, high=63, fmt="signed"
    ),
    _Column("output", "out", field="output", kind="enum", names=OUTPUT_NAMES),
)

#: KEY: which keys it answers to, and how it is transposed.
KEY_COLUMNS: Tuple[_Column, ...] = (
    _Column(
        "keylow", "low", field="key_low", kind="number", low=21, high=108, fmt="note"
    ),
    _Column(
        "keyhigh", "high", field="key_high", kind="number", low=21, high=108, fmt="note"
    ),
    _Column(
        "transpose",
        "transp",
        field="transpose",
        kind="number",
        low=-59,
        high=59,
        fmt="transpose",
    ),
)

#: The column groups ``tab`` cycles through, in the order the rack's
#: own screens are: the sound, the MIDI settings, the mix settings, and
#: the keyboard.
#:
#: The shape is the sibling rxved's ``COLUMN_VIEWS``: a name and a
#: tuple of columns, and the table is rebuilt when the group changes.
#: Four groups of eight rows fit a terminal; the whole track -- sound,
#: MIDI, mix and key -- does not.
COLUMN_VIEWS: Tuple[Tuple[str, Tuple[_Column, ...]], ...] = (
    ("sound", PRG_COLUMNS),
    ("MIDI", MIDI_COLUMNS),
    ("mix", MIX_COLUMNS),
    ("key", KEY_COLUMNS),
)


class NumberPrompt(ModalScreen[Optional[str]]):
    """One number to type, in the modal shape the family uses.

    **Local rather than the app's own prompt**, and the reason is a
    cycle: the app imports this module to build its screens, so a
    module that imported the app back could not be imported at all.
    The screen is the family's prompt screen with the one thing this
    tool needs added -- the seeded text can be selected, so that the
    first keystroke replaces the value being changed.

    ``escape`` cancels and answers ``None``; ``⏎`` answers with what
    was typed.
    """

    DEFAULT_CSS = """
    NumberPrompt { align: center middle; }
    NumberPrompt > Vertical {
        width: 70; height: auto; border: thick $accent;
        background: $surface; padding: 1 2;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self, prompt: str, seed: str = "", *, select_all: bool = False
    ) -> None:
        super().__init__()
        self._prompt = prompt
        self._seed = seed
        #: Pre-select the seeded text, so the first keystroke replaces it.
        #: Wanted when the seed is "the value you are changing" and not
        #: when it is "the digit you just typed" -- and it is the only
        #: way to enter a negative number on a screen where ``-`` steps
        #: down.
        self._select_all = select_all

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._prompt)
            yield Input(value=self._seed, id="value")

    def on_mount(self) -> None:
        field = self.query_one("#value", Input)
        field.focus()
        if self._select_all:
            field.select_all()
        else:
            # The cursor at the end, so a prompt opened by typing a digit
            # continues that number rather than inserting in front of it.
            field.cursor_position = len(field.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """``⏎``: the number that was typed."""
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        """``escape``: nothing was typed."""
        self.dismiss(None)


class TextPrompt(ModalScreen[Optional[str]]):
    """One line of text, seeded and selected, in the family's modal shape.

    The sibling of :class:`NumberPrompt` for text: a preset name is not a
    number, and the first keystroke replaces the seeded value.
    """

    DEFAULT_CSS = """
    TextPrompt { align: center middle; }
    TextPrompt > Vertical {
        width: 70; height: auto; border: thick $accent;
        background: $surface; padding: 1 2;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self, prompt: str, seed: str = "", *, select_all: bool = False
    ) -> None:
        super().__init__()
        self._prompt = prompt
        self._seed = seed
        self._select_all = select_all

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._prompt)
            yield Input(value=self._seed, id="value")

    def on_mount(self) -> None:
        field = self.query_one("#value", Input)
        field.focus()
        if self._select_all:
            field.select_all()
        else:
            field.cursor_position = len(field.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


#: What `v` steps through in the sound picker.  The same three views and
#: the same strings the browser's own favourites key uses, so a user who
#: has learned `v` in one place has learned it in the other.
SOUND_VIEW_ALL = "all"
SOUND_VIEW_BANK_FAVOURITES = "bank-favourites"
SOUND_VIEW_ALL_FAVOURITES = "all-favourites"
SOUND_VIEW_CYCLE = (
    SOUND_VIEW_ALL,
    SOUND_VIEW_BANK_FAVOURITES,
    SOUND_VIEW_ALL_FAVOURITES,
)
SOUND_VIEW_LABELS = {
    SOUND_VIEW_ALL: "all sounds",
    SOUND_VIEW_BANK_FAVOURITES: "favourites in this group",
    SOUND_VIEW_ALL_FAVOURITES: "every favourite, all groups",
}


class SoundPickerScreen(ModalScreen[Optional[Tuple[str, int]]]):
    """Pick a sound for a track, from **any** of the five groups.

    Two panes, the same shape the browser itself uses: the groups on the
    left, the selected group's sounds on the right.  The app's old picker
    offered only the bank the browser happened to be showing, which made
    ROM and the cards unreachable from a track that was on Internal --
    the one thing a MULTI setup is for.

    **`v` steps through the same three favourites views the browser has**
    -- all sounds, this group's favourites, every favourite across
    groups -- because the sounds marked there are exactly the ones a
    setup is built from, and hunting for them again in a second browser
    is the thing the favourites store exists to prevent.  In the
    all-groups view the slot column carries the group ("CARDA 007") and
    the group pane no longer filters.

    The screen answers ``(group_id, slot)`` with the picture the rest of
    the family uses: group ids are ``INT``/``ROM``/``CARDA``/``CARDB``/
    ``DRUM`` and slots count from one, exactly as the panel prints them.
    Names come from the catalog the app hands in; a missing one shows as
    ``(no name)`` rather than a blank cell, which reads as corruption.

    ``tab`` swaps panes, the arrows move, ``⏎`` picks, ``v`` cycles the
    views, ``esc`` cancels.  ``⏎`` is handled as the table's own row
    selection, not as a binding: a binding at screen level would be
    eaten by the focused DataTable.
    """

    DEFAULT_CSS = """
    SoundPickerScreen { align: center middle; }
    SoundPickerScreen > Vertical {
        width: 84; height: 80%; border: thick $accent;
        background: $surface; padding: 1 2;
    }
    SoundPickerScreen Horizontal { height: 1fr; }
    SoundPickerScreen #picker-groups {
        width: auto; min-width: 18; max-width: 30;
        border-right: solid $panel;
    }
    SoundPickerScreen #picker-slots { width: 1fr; }
    SoundPickerScreen DataTable { height: 1fr; }
    SoundPickerScreen .hint { color: $text-muted; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", "Cancel"),
        Binding("tab", "switch_pane", "Switch pane", show=False),
        Binding("v", "cycle_view", "Favourites view", show=False),
    ]

    def __init__(
        self,
        catalog,
        current: Optional[Tuple[str, int]] = None,
        *,
        favorites=None,
        view: int = 0,
    ) -> None:
        super().__init__()
        self._catalog = catalog
        #: ``(group_id, slot)`` the track plays now, marked in the list.
        self._current = current
        #: The favourites store, for `v`.  ``None`` in a test that does
        #: not care about favourites: `v` then says so and does nothing.
        self._favorites = favorites
        #: Index into :data:`SOUND_VIEW_CYCLE`, cycled by `v`.
        self._view = view % len(SOUND_VIEW_CYCLE)
        self._group_ids: List[str] = banks.group_ids()
        #: The group whose slots are drawn; set by the group pane.
        self._filled_group: Optional[str] = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._title(), id="picker-title")
            with Horizontal():
                yield DataTable(id="picker-groups", cursor_type="row")
                yield DataTable(id="picker-slots", cursor_type="row")
            yield Static(
                wrap_blocks(
                    (
                        "tab pane",
                        "↑↓ move",
                        "v favourites view",
                        "⏎ pick",
                        "esc cancel",
                    ),
                    78,
                ),
                classes="hint",
            )

    def on_mount(self) -> None:
        groups = self.query_one("#picker-groups", DataTable)
        groups.add_column("group", key="group")
        groups.add_column("n", key="n")
        for group_id in self._group_ids:
            entry = banks.group(group_id)
            groups.add_row(entry.label, str(entry.count), key=group_id)
        self._fill_slots(self._start_group())
        self._place_current()
        # A picker opened from a track almost always wants another sound
        # in the group the track is already on, so the cursor starts in
        # the slot pane; ``tab`` reaches the groups. A fresh picker with
        # no current sound starts on the groups, where a choice is owed.
        if self._current is not None:
            self.query_one("#picker-slots", DataTable).focus()
        else:
            groups.focus()

    def _start_group(self) -> str:
        """The group to open on: the track's own, else the first."""
        if self._current is not None and self._current[0] in self._group_ids:
            return self._current[0]
        return self._group_ids[0]

    def _title(self) -> str:
        """The heading, carrying which of the three views is showing."""
        label = SOUND_VIEW_LABELS[SOUND_VIEW_CYCLE[self._view]]
        return f"[b]Pick a sound for this track[/b]  [dim]· {label}[/dim]"

    def _visible_slots(self, group_id: str) -> List[banks.Slot]:
        """The rows the slot pane should show, for the current view.

        In the all-groups favourites view the rows come from every group,
        so callers cannot assume every slot belongs to ``group_id``; the
        row keys carry the group for that reason.
        """
        view = SOUND_VIEW_CYCLE[self._view]
        if view == SOUND_VIEW_ALL:
            return banks.slots(group_id)
        if self._favorites is None:
            return []
        if view == SOUND_VIEW_BANK_FAVOURITES:
            marked = self._favorites.keys_for_bank(group_id)
            return [slot for slot in banks.slots(group_id) if slot.number in marked]
        rows: List[banks.Slot] = []
        for favourite in self._favorites.all(order="bank"):
            try:
                rows.append(banks.slot(favourite.bank_id, favourite.number))
            except LookupError:
                # A favourite for a group this build no longer defines.
                continue
        order = {group: index for index, group in enumerate(self._group_ids)}
        rows.sort(key=lambda slot: (order.get(slot.group_id, len(order)), slot.number))
        return rows

    def _place_current(self) -> None:
        """Put both cursors on what the track plays now, when it is shown."""
        if self._current is None:
            return
        group_id, slot = self._current
        if group_id not in self._group_ids:
            return
        self.query_one("#picker-groups", DataTable).move_cursor(
            row=self._group_ids.index(group_id)
        )
        self._fill_slots(group_id)
        table = self.query_one("#picker-slots", DataTable)
        for index, entry in enumerate(self._visible_slots(group_id)):
            if entry.group_id == group_id and entry.number == slot:
                table.move_cursor(row=index)
                break

    def _fill_slots(self, group_id: str, *, force: bool = False) -> None:
        """Draw the current view's sounds, keeping the slot pane stable."""
        if group_id == self._filled_group and not force:
            return
        self._filled_group = group_id
        table = self.query_one("#picker-slots", DataTable)
        across = SOUND_VIEW_CYCLE[self._view] == SOUND_VIEW_ALL_FAVOURITES
        table.clear(columns=True)
        table.add_column("#", key="num")
        table.add_column("name", key="name")
        for slot in self._visible_slots(group_id):
            name = self._catalog.display_name(slot.bank_id, slot.number)
            table.add_row(
                f"{slot.group_id} {slot.number:03d}"
                if across
                else f"{slot.number:03d}",
                _literal(name) if name else "[dim](no name)[/dim]",
                key=f"{slot.group_id}:{slot.number}",
            )

    def _group_under_cursor(self) -> Optional[str]:
        table = self.query_one("#picker-groups", DataTable)
        if not table.row_count:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate)[0].value
        return str(key)

    def on_data_table_row_highlighted(self, event) -> None:
        if event.data_table.id != "picker-groups":
            return
        # In the all-groups favourites view the slot pane is the whole
        # store, not one group's slice; letting the group cursor rebuild
        # it would make that view impossible to hold still.
        if SOUND_VIEW_CYCLE[self._view] == SOUND_VIEW_ALL_FAVOURITES:
            return
        group_id = self._group_under_cursor()
        if group_id is not None:
            self._fill_slots(group_id)

    def action_cycle_view(self) -> None:
        """``v``: all sounds, this group's favourites, every favourite.

        Exactly the browser's three steps, and always three even when a
        view is empty: skipping one to be helpful makes a keypress land
        somewhere different depending on what happens to be favourited.
        """
        if self._favorites is None:
            self.app.notify("no favourites database", severity="warning")
            return
        self._view = (self._view + 1) % len(SOUND_VIEW_CYCLE)
        try:
            self.query_one("#picker-title", Label).update(self._title())
        except Exception:  # pragma: no cover - the screen closed under us
            return
        group_id = self._group_under_cursor() or self._start_group()
        self._fill_slots(group_id, force=True)
        table = self.query_one("#picker-slots", DataTable)
        if table.row_count:
            table.move_cursor(row=0)
        if not table.row_count:
            label = SOUND_VIEW_LABELS[SOUND_VIEW_CYCLE[self._view]]
            self.app.notify(f"{label} — nothing favourited yet")

    def on_data_table_row_selected(self, event) -> None:
        if event.data_table.id != "picker-slots":
            return
        table = self.query_one("#picker-slots", DataTable)
        if not table.row_count:
            return
        key = table.coordinate_to_cell_key(table.cursor_coordinate)[0].value
        group_id, _, number = str(key).rpartition(":")
        try:
            self.dismiss((group_id, int(number)))
        except ValueError:  # pragma: no cover - the keys are ours
            self.dismiss(None)

    def action_switch_pane(self) -> None:
        left = self.query_one("#picker-groups", DataTable)
        right = self.query_one("#picker-slots", DataTable)
        (right if left.has_focus else left).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)


class PresetListScreen(ModalScreen[Optional[int]]):
    """The eighty presets the unit holds, by number and by name.

    **The list is read before the screen is shown.**  Reading all
    eighty presets is one message and about twelve seconds, so the
    caller keeps the spinner in its own worker and hands the finished
    list in here.  A modal that waited for the data would be a modal
    with nothing to do for twelve seconds.

    **The number shown is the unit's, and it is the dump's too.**  The
    unit's display counts its preset locations from zero -- ``P00``
    through ``P79``, the owner's reading of the display on 2026-10-22;
    the manual's ``PSET 001`` wording is one-based, and the display
    wins.  The dump's slots are counted from zero as well, so the row
    shows the unit's number and the screen answers with the same
    number as the dump's slot; the alignment is written down here
    because an off-by-one here would be invisible until it had already
    written the wrong preset.

    **The names are the unit's, and they are only ever shown.**  The
    name field carries the unit's mute flag in bit 7 of every byte
    (Note 3), so it is read with that bit cleared and never written
    back; the screen decodes the name to show it and changes nothing.

    ``escape`` and ``q`` cancel and answer ``None``; ``⏎`` on a row
    answers with that row's slot.
    """

    DEFAULT_CSS = """
    PresetListScreen { align: center middle; }
    PresetListScreen > Vertical {
        width: 78; height: auto; border: thick $accent;
        background: $surface; padding: 1 2;
    }
    PresetListScreen DataTable { height: auto; }
    PresetListScreen .note { height: auto; color: $text-muted; padding-top: 1; }
    PresetListScreen .hint { height: auto; color: $text-muted; }
    PresetListScreen .status { height: auto; color: $text; padding: 0 1; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", "Cancel"),
        Binding("r", "rename", "Label"),
        Binding("ctrl+r", "reload", "Reload"),
    ]

    #: The table's DOM id: a rule or a query that names one screen's
    #: table must not reach another's.
    TABLE_ID = "preset-table"

    def __init__(
        self,
        presets: List[Tuple[int, bytes]],
        current: Optional[int] = None,
        favorites_like_names: Optional[Dict[int, str]] = None,
        *,
        progress_note: Optional[str] = None,
        mode_note: Optional[str] = None,
        on_rename: Optional[Callable[[int, str], None]] = None,
        on_reload: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__()
        #: ``(slot, raw)`` for every preset the unit holds.  The slot is
        #: the dump's own number, counted from zero.
        self._presets = list(presets)
        #: The slot the unit is on now, marked in the list.  ``None``
        #: when the caller does not know.
        self._current = current
        #: The user's own names for slots, if they have any -- the
        #: favourites database's idea of a preset's name.  Shown in its
        #: own column, so the unit's own name stays visible beside it:
        #: the two are different claims and one of them is wrong.
        self._names = dict(favorites_like_names or {})
        #: What the app said while it was reading the dump, kept as a
        #: caption so the list says where it came from.  The spinner
        #: itself stays in the app's worker.
        self._progress_note = progress_note
        #: A short note about the mode this list is shown in.
        self._mode_note = mode_note
        #: The app's rename callback: ``(slot, new_name)``.
        self._on_rename = on_rename
        #: The app's reload callback: refreshes the list from the device.
        #: ``None`` means there is no write path to hand the reload to,
        #: and ``ctrl+r`` says so rather than pretending to reload.
        self._on_reload = on_reload

    # --- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("[b]Presets[/b]  [dim]· the eighty the unit holds[/dim]")
            if self._mode_note:
                yield Static(_literal(self._mode_note), classes="note")
            if self._progress_note:
                yield Static(_literal(self._progress_note), classes="note")
            yield DataTable(id=self.TABLE_ID, cursor_type="row", zebra_stripes=True)
            yield Static("", classes="status", id="preset-status")
            yield Static(
                "[dim]⏎ picks a preset · r label · ctrl+r reload · "
                "esc / q to close[/dim]",
                classes="hint",
            )

    def on_mount(self) -> None:
        table = self.query_one(f"#{self.TABLE_ID}", DataTable)
        table.add_column("", key="mark", width=2)
        table.add_column("preset", key="slot")
        table.add_column("name", key="name")
        table.add_column("label", key="label")
        for slot, raw in self._presets:
            table.add_row(
                "←" if slot == self._current else "",
                f"P{slot:02d}",
                self._name_of(raw),
                self._names.get(slot, ""),
                key=str(slot),
            )
        table.focus()

    def refresh_presets(self, presets: List[Tuple[int, bytes]]) -> None:
        """Refresh the preset list rows from the provided data.

        The screen is already open, so we just update the rows in place.
        The spinner stays in the app's worker; the screen repaints itself.
        """
        self._presets = list(presets)
        if not self._presets:
            return
        try:
            table = self.query_one(f"#{self.TABLE_ID}", DataTable)
        except Exception:  # pragma: no cover - the screen closed under us
            return
        table.clear(columns=True)
        table.add_column("", key="mark", width=2)
        table.add_column("preset", key="slot")
        table.add_column("name", key="name")
        table.add_column("label", key="label")
        for slot, raw in self._presets:
            table.add_row(
                "←" if slot == self._current else "",
                f"P{slot:02d}",
                self._name_of(raw),
                self._names.get(slot, ""),
                key=str(slot),
            )

    # --- picking -----------------------------------------------------------

    def on_data_table_row_selected(self, event) -> None:
        """``⏎`` on a row: that preset.

        A focused DataTable consumes the key for its own row selection,
        so the pick arrives as a message rather than as a binding --
        the same reason the browser has no app-level Enter at all.
        """
        if event.data_table.id != self.TABLE_ID:
            return
        slot = self._slot_at(event.data_table.cursor_row)
        if slot is not None:
            self.dismiss(slot)

    def _slot_at(self, row: int) -> Optional[int]:
        """The slot a row names, or ``None``."""
        if not 0 <= row < len(self._presets):
            return None
        return self._presets[row][0]

    def action_rename(self) -> None:
        """``r``: set the app's local label for the preset under the cursor.

        **This is the local label, not the unit's name.**  The text is
        handed to the app through ``on_rename`` and kept in the sidecar;
        it never leaves this program.  The unit's own name is ``R`` in
        the multi editor (``rename_on_unit``), which drives the save
        dialog's name page.
        """
        if self._on_rename is None:
            self.app.notify("label is not wired", severity="warning")
            return
        table = self.query_one(f"#{self.TABLE_ID}", DataTable)
        row = table.cursor_row
        slot = self._slot_at(row)
        if slot is None:
            self.app.notify("no preset selected", severity="warning")
            return
        raw = dict(self._presets).get(slot)
        if raw is None:
            return
        current_name = self._name_of(raw)

        def on_name(new_name: Optional[str]) -> None:
            if new_name is None:
                return
            new_name = new_name.strip()
            if not new_name:
                return
            self._on_rename(slot, new_name)
            self.dismiss(None)

        self.app.push_screen(
            TextPrompt(
                f"Local label for P{slot:02d} (unit name: {current_name}):",
                current_name.strip(),
                select_all=True,
            ),
            on_name,
        )

    def _name_of(self, raw: bytes) -> str:
        """The preset's name, as the unit prints it.

        The name field's bit 7 carries the unit's mute flag and is not
        part of the name, so the name is the bytes with that bit
        cleared -- which is what :attr:`sq.presets.Preset.name` reads.

        A preset that cannot be decoded shows "?" rather than stopping
        the list: one bad row must not hide the other seventy-nine.
        """
        try:
            return _literal(Preset.from_bytes(raw).name.strip())
        except ValueError:
            return "?"

    def action_cancel(self) -> None:
        """``escape`` and ``q``: nothing was picked."""
        self.dismiss(None)

    def action_reload(self) -> None:
        """``ctrl+r``: reload the presets from the device.

        The screen asks for the reload via ``on_reload``; if not wired,
        it says so rather than pretending. The app handles the worker
        that reads all presets and updates the list.
        """
        if self._on_reload is None:
            self.app.notify("reload is not wired", severity="warning")
            return
        self._on_reload()

    def say(self, message: str, *, refused: bool = False) -> None:
        """Update the screen's own status line."""
        with contextlib.suppress(Exception):
            status = self.query_one("#preset-status", Static)
            status.update(message)
            status.set_class(refused, "refused")


class MultiScreen(ModalScreen[None]):
    """The eight tracks of one preset, and the editor over them.

    **One live preset, not a copy.**  The screen is handed the
    :class:`sq.presets.Preset` object the app is editing and changes
    that object; every committed change is handed to ``on_apply``
    together with a sentence naming it, and the app does the rest --
    the write gate, the send, the read-back and the undo record.  A
    screen that kept a copy of its own would be a screen that could
    show a value the unit never accepted.

    **The columns are grouped, and ``tab`` cycles the groups.**  Eight
    tracks times the twenty parameters a track has does not fit a
    terminal row, and cramming it would cost the name -- which is the
    one column that says what a track *is*.  So there are four views:
    PRG (the sound and the program), MIDI (the channel, the status, the
    pressure and the sustain), MIX (the volume, the pan, the timbre,
    the release and the output) and KEY (the key range and the
    transpose).  The shape is the sibling rxved's ``COLUMN_VIEWS``.

    **The gestures are the family's.**  ``⏎`` edits the focused cell --
    a list to pick from when the values are words, a number to type when
    they are numbers -- a digit starts that number with the digit in it,
    ``+`` and ``-`` nudge the value by its own step.  Every one of them
    is the same path: the value is changed in the model and handed to
    the app.

    **A refused change is put back.**  The screen knows the value each
    cell held before the change, so when the app answers
    ``result(ok=False)`` the cell goes back to what it was and says why.
    A row left reading a value the unit refused is the same lie a stale
    cell is.

    **What is not here.**  The sound picker is the app's -- this screen
    says which track wants a sound and the app runs its own picker.
    The preset list is the app's too: ``p`` asks the app to open it, and
    the app owns the browser and the selection.  And the screen sends
    nothing itself, including the store assist:
    ``ctrl+shift+s`` finds and confirms the backed-up bytes and hands them to
    the app, which does the gate, the send and the verified retry.  See
    the module docstring.  The ``r`` key asks for a **local label** for
    the preset's slot; ``R`` asks for the **unit's own name**, which a
    real rename can now write through the unit's save-dialog name page
    -- the screen only asks for the text and the app runs that
    sequence.
    """

    DEFAULT_CSS = """
    MultiScreen { align: center middle; }
    MultiScreen > Vertical {
        width: 90; height: auto; max-height: 90%; border: thick $accent;
        background: $surface; padding: 1 2;
    }
    MultiScreen DataTable { width: 1fr; height: auto; max-height: 16; }
    MultiScreen .note { height: auto; color: $text-muted; padding-top: 1; }
    MultiScreen .status { height: auto; color: $text; padding-top: 1; }
    /* The browser's alarm, on this screen's own line. */
    MultiScreen .status.refused {
        background: $error; color: $text; text-style: bold;
    }
    /* A verified store's confirmation: green, never another grey line. */
    MultiScreen .status.ok {
        background: $success; color: $text; text-style: bold;
    }
    MultiScreen .hint { height: auto; color: $text-muted; }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
        Binding("r", "rename", "Label"),
        # The unit's own name, through its save dialog's name page.  The
        # screen only asks for the text; the app owns the unit sequence.
        Binding("R", "rename_on_unit", "Rename on unit", show=False),
        # Enter is NOT bound here.  A focused DataTable consumes it for
        # its own cell selection, so it arrives as CellSelected instead;
        # see on_data_table_cell_selected.
        Binding("tab", "toggle_view", "Columns", show=False),
        Binding("space", "toggle_track", "Track on/off", show=False),
        Binding("plus", "bump(1)", "Value +", show=False),
        # `=` as well as `+`, because `+` is a shift on most layouts and
        # a nudge is a one-handed gesture.
        Binding("equals_sign", "bump(1)", "Value +", show=False),
        Binding("minus", "bump(-1)", "Value -", show=False),
        # `z` and `Z` are the app's undo, served here for the reason the
        # sibling editors serve `w` themselves: a modal screen cuts the
        # app's binding chain, so an app-level binding is dead the
        # moment this screen is open -- and this is where `z` gets
        # pressed, after an edit made here.
        Binding("z", "undo", "Undo", show=False),
        Binding("Z", "undo_all", "Undo all", show=False),
        # `w` is the app's key, served here for the reason `z` is: a
        # modal screen cuts the app's binding chain, so the gate could
        # only be armed by closing the editor -- leaving the warning
        # that said so on the dimmed backdrop behind it.
        Binding("w", "toggle_write_gate", "Write gate", show=False),
        # The cooperative restore: pick a backup slot, select it on the
        # unit, confirm, and the app sends it to wherever the unit is
        # selected.  See action_store_assist.
        Binding("ctrl+shift+s", "store_assist", "Store assist"),
        # Store the edit buffer with the unit's own Enter/Save three times.  The
        # walk after it is only the stored read-back, not the store.
        Binding("ctrl+s", "commit_to_unit", "Store on unit"),
        # Force a reload of the preset this screen is editing: walk the
        # unit's selection a full lap so the final press reloads it with
        # its stored content.  See action_reload_on_unit.
        Binding("ctrl+r", "reload_on_unit", "Reload on unit"),
        # The live mute: the unit's own Track pair as a chord (buttons 10
        # and 12), toggling the track under the panel's cursor.  It is
        # the only way to change what the unit plays without touching the
        # stored preset.  See action_live_mute.
        Binding("l", "live_mute", "Live mute"),
        # The panel's own track cursor, one key each: the unit's display
        # moves its track number, so the owner can put `l` on the track
        # they mean.  The cursor cannot be read back; the unit is the
        # display for it.
        Binding("left_square_bracket", "track_cursor(-1)", "Track left", show=False),
        Binding("right_square_bracket", "track_cursor(1)", "Track right", show=False),
        # The effect is part of this preset: its editor opens from here,
        # not only from the browser behind.
        Binding("x", "open_fx", "FX"),
        # The preset list, where `p` opens it in the main window.  The
        # app owns the browser and the selection; the screen only asks.
        Binding("p", "open_preset_list", "Presets", show=False),
    ]

    # The ten digits, added after the list rather than into it: the
    # list has to keep the type the base class declares, and a
    # comprehension concatenated onto it would widen it to a narrower
    # type than that.
    BINDINGS += [
        # Typing a digit on a numeric cell starts entering a number, the
        # way a spreadsheet does: the value is three keystrokes and a
        # dialog for each of them is a step nobody wants.
        Binding(str(digit), f"type_digit('{digit}')", "Type", show=False)
        for digit in range(10)
    ]

    #: The table's DOM id: a rule or a query that names one screen's
    #: table must not reach another's.
    TABLE_ID = "track-table"

    def __init__(
        self,
        preset: Preset,
        *,
        on_apply: Callable[[Preset, str], None],
        on_undo: Callable[[Optional[int]], None],
        on_sound_picked: Callable[[int, int], None],
        on_close: Optional[Callable[[], None]] = None,
        allow_write: bool = False,
        gate: Optional[EditGate] = None,
        say_: Optional[Callable[..., None]] = None,
        name_for: Optional[Callable[[str, int], Optional[str]]] = None,
        on_store_assist: Optional[Callable[[int, bytes], None]] = None,
        on_reload: Optional[Callable[[int], None]] = None,
        on_reload_preset: Optional[Callable[[int], None]] = None,
        on_live_mute: Optional[Callable[[], None]] = None,
        on_panel_button: Optional[Callable[[int], None]] = None,
        on_open_fx: Optional[Callable[[], None]] = None,
        on_open_preset_list: Optional[Callable[[], None]] = None,
        on_rename: Optional[Callable[[str], None]] = None,
        on_rename_on_unit: Optional[Callable[[str], None]] = None,
        on_commit: Optional[Callable[[], None]] = None,
        local_label: Optional[str] = None,
        slot: Optional[int] = None,
        preset_slot: Optional[int] = None,
        backup_root: Optional[str] = None,
    ) -> None:
        super().__init__()
        #: The one live preset object.  The screen changes it in place.
        self._preset = preset
        #: ``on_apply(preset, description)`` -- the app's write path.
        #: The description is the sentence the undo log is labelled
        #: with: "track 3 volume 50→49".
        self._on_apply = on_apply
        #: ``on_undo(count)`` -- the app's undo.  ``None`` means all of
        #: it, which is the app's to know.
        self._on_undo = on_undo
        #: ``on_sound_picked(track index, track number)`` -- the app
        #: runs its own sound picker.  The two integers are the track's
        #: index into ``preset.tracks`` and the number the panel
        #: prints for it.
        self._on_sound_picked = on_sound_picked
        #: Called when the screen closes, for the app's own bookkeeping.
        self._on_close = on_close
        #: Whether the app's write path is open.  A screen that is not
        #: allowed to write says so and changes nothing: a change that
        #: cannot be sent is not a change.
        self._allow_write = allow_write
        #: The app's own gate, when there is an app.  Held rather than
        #: copied so `w` here toggles the object every edit is checked
        #: against, and the note can be redrawn from it.
        self._gate = gate
        #: An optional second route for the sentence, called as
        #: ``say_(message, refused=refused)``.  The app does not pass
        #: one: its status line is the dimmed backdrop behind this
        #: modal, and a warning written there is written where nobody
        #: is looking.  Tests use it as a capture seam.
        self._say = say_
        #: ``name_for(group, slot)`` -- the catalog's name for a sound.
        #: ``None`` means there is no catalog, and the name column says
        #: so rather than showing a blank that reads as a broken row.
        self._name_for = name_for
        #: ``on_store_assist(slot, raw)`` -- the app's store-assist write
        #: path.  The screen finds and confirms the backed-up bytes; the
        #: app owns the gate, the exchange, the send and its read-back.
        #: ``None`` means there is no write path to hand them to, and the
        #: action says so rather than pretending it sent anything.
        self._on_store_assist = on_store_assist
        #: ``on_reload_preset(slot)`` -- the app's reload write path.
        #: The screen confirms; the app owns the gate, the exchange and
        #: the walk.  ``None`` means there is no write path to hand it
        #: to, and the action says so rather than pretending it reloaded.
        self._on_reload_preset = (
            on_reload_preset if on_reload_preset is not None else on_reload
        )
        #: ``on_live_mute()`` -- the app's live-mute write path (the two
        #: Track keys as a chord).  The screen only asks; the app owns
        #: the gate, the exchange and the send.  ``None`` means there is
        #: no write path, and the action says so.
        self._on_live_mute = on_live_mute
        #: ``on_panel_button(number)`` -- one virtual button, for the
        #: panel's own track cursor (Track L/R).  The unit displays the
        #: cursor; this screen cannot read it.
        self._on_panel_button = on_panel_button
        #: ``on_open_fx()`` -- open the effect editor for the same preset.
        #: The effect is part of the preset, so its editor belongs to the
        #: preset editor's own key row; the app owns the screen.
        self._on_open_fx = on_open_fx
        #: ``on_open_preset_list()`` -- open the preset list that `p`
        #: opens in the main window.  The app owns the browser, the
        #: read-back and the selection; the screen only asks.
        self._on_open_preset_list = on_open_preset_list
        #: ``on_rename(text)`` -- the app stores a local label for the
        #: preset slot; this is the sidecar, not the unit's name.  The
        #: unit's own name is ``on_rename_on_unit`` below.
        self._on_rename = on_rename
        #: ``on_rename_on_unit(name)`` -- rename the preset on the unit
        #: itself through its save dialog's name page.  The screen asks
        #: for the text only; the app owns the gate and the whole unit
        #: sequence.
        self._on_rename_on_unit = on_rename_on_unit
        #: ``on_commit()`` -- store the edit buffer with Enter/Save three
        #: times.  The app owns the presses, the walk and the verification.
        self._on_commit = on_commit
        #: The user's own label for this preset's slot, when one exists.
        #: Shown beside the unit's name because the two are different
        #: claims: the unit keeps its own name, the label is this app's.
        self._local_label = local_label.strip() if local_label else None
        #: The slot this screen is editing, when it is known -- a preset
        #: picked out of the list carries one, and the unit's own current
        #: preset does not.  The reload re-selects this slot.
        self._slot = slot if slot is not None else preset_slot
        #: The slot and bytes waiting on the confirmation dialog.
        self._reload_pending: Optional[int] = None
        #: The directory backups live under.  Injected by the app; the
        #: default is the bench's own location, so a standalone screen
        #: still looks where the backup runs write.
        self._backup_root = backup_root or DEFAULT_BACKUP_ROOT
        #: The slot and bytes waiting on the confirmation dialog.
        self._store_pending: Optional[Tuple[int, bytes]] = None
        #: Index into :data:`COLUMN_VIEWS`.  Cycled by ``tab``.
        self._view = 0
        #: The values the cells held before the last change, so a
        #: refused change can be put back: ``(track index, column key,
        #: the value the field had)``.
        self._pending: List[Tuple[int, str, int | bool]] = []

    # --- layout ------------------------------------------------------------

    def _title(self) -> str:
        name = self._preset.name.strip() or "(no name)"
        tempo = self._preset.tempo
        where = f"[dim]· {tempo} bpm[/dim]" if 25 <= tempo <= 250 else ""
        label = (
            f"  [dim]· label “{_literal(self._local_label)}”[/dim]"
            if self._local_label
            else ""
        )
        return f"[b]Multi — preset “{_literal(name)}”[/b]  {where}{label}"

    def set_local_label(self, label: Optional[str]) -> None:
        """Show the app's local label for this preset's slot, or clear it."""
        self._local_label = label.strip() if label else None
        try:
            self.query_one("#multi-title", Label).update(self._title())
        except Exception:  # pragma: no cover - the screen closed under us
            return

    def _note(self) -> str:
        """What the user cannot change from here, said once.

        MULTI mode is a System-bank setting on the unit itself: this
        program has no message that changes it, and pretending otherwise
        would be a lie the user finds out about at the keyboard.
        """
        state = self._gate_description()
        where = (
            "w toggles it here"
            if self._gate is not None
            else "w in the browser toggles it"
        )
        consequence = (
            "edits may be sent" if self._writes_allowed() else "edits are refused"
        )
        return (
            "[dim]MIDI Mode = MULTI is a System-bank setting on the unit "
            "itself — this program cannot change it. In MULTI mode each "
            "track takes one MIDI channel, and the lowest track number "
            "wins a channel.[/dim]\n"
            f"[dim]{state[0].upper()}{state[1:]} — {consequence}; {where}.[/dim]"
        )

    def _gate_description(self) -> str:
        """The gate's state, read from the gate itself when there is one.

        A copy of the boolean would go stale the moment `w` was pressed
        here: the note would go on saying "locked" while the edits went
        through, which is worse than not saying it at all.
        """
        if self._gate is not None:
            return self._gate.describe()
        return "write gate ARMED" if self._allow_write else "write gate locked"

    def _writes_allowed(self) -> bool:
        """Whether an edit made now may be sent."""
        if self._gate is not None:
            return self._gate.armed
        return self._allow_write

    def _refresh_note(self) -> None:
        """Redraw the gate/MULTI note after the gate changes.

        The id is specific so the store note below it is never overwritten:
        there are two ``.note`` widgets now, and ``.note`` alone is
        ambiguous.
        """
        try:
            self.query_one("#multi-gate-note", Static).update(self._note())
        except Exception:  # pragma: no cover - the screen closed under us
            return

    def _columns(self) -> Tuple[_Column, ...]:
        """The columns of the current view.

        The track's own number and its on/off state are in every view:
        the number says which track the row is, and the state is the
        answer to the question the screen is usually opened with.
        """
        return (TRACK_COLUMN, ON_COLUMN) + COLUMN_VIEWS[self._view][1]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._title(), id="multi-title")
            yield Static(self._note(), classes="note", id="multi-gate-note")
            yield Static(
                "[b $accent]space[/] toggles the track's stored ON/OFF "
                "(pressure 3 = OFF). [b $accent]ctrl+s[/] stores with the "
                "unit's Enter/Save three times and verifies the slot. The preset name "
                "is local to this app.",
                classes="note",
                id="multi-store-note",
            )
            yield DataTable(id=self.TABLE_ID, cursor_type="cell", zebra_stripes=True)
            # The screen's own status line.  Without it everything the
            # app says -- a refusal, a result -- lands on the app's
            # `#status`, which is the dimmed backdrop behind this modal.
            yield Static("", classes="status")
            yield Static(self._view_hint(), classes="hint", id="view-hint")
            yield Static(wrap_blocks(KEY_HINTS_BLOCK, HINT_WIDTH), classes="hint")

    def on_mount(self) -> None:
        self._build_table()
        self.query_one(f"#{self.TABLE_ID}", DataTable).focus()

    def _build_table(self) -> None:
        """The table, in the current view.

        Rebuilt rather than repainted when the view changes: the
        columns are not the same columns, and ``DataTable.refresh()``
        would repaint the cells the table already has and never
        recompute them.
        """
        table = self.query_one(f"#{self.TABLE_ID}", DataTable)
        try:
            coord = table.cursor_coordinate
            row = int(coord.row)
            column = int(coord.column)
        except Exception:  # pragma: no cover - a table with no cursor yet
            row = 0
            column = 0
        table.clear(columns=True)
        for col in self._columns():
            if col.width:
                table.add_column(col.label, key=col.key, width=col.width)
            else:
                table.add_column(col.label, key=col.key)
        for index in range(len(self._preset.tracks)):
            table.add_row(*self._row(index), key=f"t{index + 1}")
        if table.row_count and len(table.columns):
            row = max(0, min(row, table.row_count - 1))
            column = max(0, min(column, len(table.columns) - 1))
            table.move_cursor(row=row, column=column)
        with contextlib.suppress(Exception):
            table.focus()

    def _view_hint(self) -> str:
        """Which column group is shown, and what comes next."""
        which = COLUMN_VIEWS[self._view][0]
        nxt = COLUMN_VIEWS[(self._view + 1) % len(COLUMN_VIEWS)][0]
        return f"[b]{which}[/b] columns · tab for {nxt}"

    def _update_hints(self) -> None:
        try:
            self.query_one("#view-hint", Static).update(self._view_hint())
        except Exception:  # pragma: no cover - the screen closed under us
            return

    # --- the rows ----------------------------------------------------------

    def _is_on(self, number: int) -> bool:
        """The stored track ON/OFF marker (pressure 3 is OFF)."""
        return self._preset.tracks[number - 1].is_on

    def _row(self, index: int) -> Tuple[str, ...]:
        """One track's cells, in the current view."""
        track = self._preset.tracks[index]
        cells = []
        for column in self._columns():
            if column is ON_COLUMN:
                cells.append("on" if self._is_on(index + 1) else "[b]OFF[/b]")
            elif column is SPOT_COLUMN:
                cells.append(self._spot(track))
            elif column is NAME_COLUMN:
                cells.append(self._sound_name(track))
            elif column is TRACK_COLUMN:
                cells.append(f"t{index + 1}")
            else:
                cells.append(column.text_of(track))
        return tuple(cells)

    def _spot(self, track: Track) -> str:
        """The sound, as the group and the slot inside it.

        The drum kits are the numbers 80 to 99 and ignore the bank
        code, so they are shown as they are rather than as a group and
        a slot that do not exist.
        """
        if track.group == "DRUM":
            return f"~DRUM~ {track.slot:02d}"
        return f"{track.group} {track.slot:03d}"

    def _sound_name(self, track: Track) -> str:
        """The sound's name, from the catalog the app handed in.

        No catalog means "(no name)", said rather than left blank: a
        blank cell reads as a broken row.
        """
        if self._name_for is None:
            return "[dim](no name)[/dim]"
        name = self._name_for(track.group, track.slot)
        if not name:
            return "[dim](no name)[/dim]"
        return _literal(name)

    # --- the cursor --------------------------------------------------------

    def _cursor(self) -> Tuple[Optional[int], Optional[_Column]]:
        """``(track index, column)`` under the cursor, or ``(None, ...)``.

        Every editing gesture starts here.  The table's cursor moves
        between cells -- ``cursor_type="cell"`` -- because nine of the
        columns are values with ranges, and a row-wide cursor cannot
        say which of them a keystroke was aimed at.
        """
        try:
            table = self.query_one(f"#{self.TABLE_ID}", DataTable)
        except Exception:  # pragma: no cover - the screen closed under us
            return None, None
        if not table.row_count:
            return None, None
        try:
            row_key, column_key = table.coordinate_to_cell_key(table.cursor_coordinate)
        except Exception:  # pragma: no cover - a table with no cursor yet
            return None, None
        index = self._index_of(row_key.value)
        column = self._column_by_key(column_key.value)
        return index, column

    def _index_of(self, row_key: Optional[str]) -> Optional[int]:
        """The track index a row key names, or ``None``.

        The row keys are "t1".."t8", so the index is the number the key
        carries, less one: the panel counts tracks from one.
        """
        if not row_key or not row_key.startswith("t"):
            return None
        try:
            number = int(row_key[1:])
        except ValueError:  # pragma: no cover - the keys are ours
            return None
        index = number - 1
        if not 0 <= index < len(self._preset.tracks):
            return None
        return index

    def _column_by_key(self, key: Optional[str]) -> Optional[_Column]:
        """The column a key names, or ``None``."""
        for column in self._columns():
            if column.key == key:
                return column
        return None

    # --- the actions -------------------------------------------------------

    def action_close(self) -> None:
        """``escape`` and ``q``: leave the editor.

        A binding whose action method does not exist is not an error --
        Textual logs it and presses on -- so the keys this screen
        advertises have to have a method to serve them.
        """
        if self._on_close is not None:
            self._on_close()
        self.dismiss(None)

    def action_commit_to_unit(self) -> None:
        """``ctrl+s``: store the edit buffer with Enter/Save three times."""
        if self._on_commit is None:
            self.say("store is not wired", refused=True)
            return
        self._on_commit()

    def action_rename(self) -> None:
        """``r``: label the preset this editor is over.

        **This is a local label, not the unit's name.**  The firmware's
        store copies only preset offsets 32-151, so the name field is
        never written by a dump; a real rename is the unit's own name,
        done as ``R``/``rename_on_unit``, which drives the save dialog's
        name page.  The text is handed to the app, which keeps it for the
        preset's slot and shows it in the preset list.
        """
        current = (self._local_label or self._preset.name).strip()
        if self._on_rename is None:
            if self._say is not None:
                self._say("label is not wired", refused=True)
            return

        def on_name(new_name: Optional[str]) -> None:
            if new_name is None:
                return
            new_name = new_name.strip()
            if not new_name:
                return
            self._on_rename(new_name[:16])

        self.app.push_screen(
            TextPrompt("Local preset label:", current, select_all=True),
            on_name,
        )

    def action_rename_on_unit(self) -> None:
        """``R``: rename the preset on the unit itself.

        **The unit's own name, not the local label.**  A real rename is
        possible through the save dialog's name page, so this asks for
        the text and hands it to the app, which owns the sequence, the
        write gate and the verification.  The screen sends nothing.

        The prompt is seeded with the unit's current name -- what the
        preset says now -- and not with the local label: the label is a
        sidecar, and typing over it must not become the new unit name.
        """
        if self._on_rename_on_unit is None:
            self.say("rename on unit is not wired", refused=True)
            return
        prompt = (
            f"Rename P{self._slot:02d} on the unit (max 16):"
            if self._slot is not None
            else "Rename this preset on the unit (max 16):"
        )

        def on_name(new_name: Optional[str]) -> None:
            if new_name is None:
                return
            new_name = new_name.strip()
            if not new_name:
                return
            self._on_rename_on_unit(new_name)

        self.app.push_screen(
            TextPrompt(prompt, self._preset.name.strip(), select_all=True),
            on_name,
        )

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

    # --- the store assist --------------------------------------------------

    def action_store_assist(self) -> None:
        """``ctrl+shift+s``: send a slot's backed-up bytes to the unit.

        **A send stores into the unit's current selection.**  The
        measured model (2026-10-22) has no per-slot addressing: the
        payload's address bytes are rewritten to whatever slot the unit
        is selected to, so the selection is the destination.  The
        action therefore does not try to address a slot -- it asks which
        slot's backed-up bytes to send, says that the owner has to
        select that slot on the unit's front panel, and sends nothing
        until the confirmation is answered.

        **The write gate is asked before the question is asked.**  A
        locked gate is a refusal with the family's sentence, and no
        dialog opens for a send that cannot happen.  It is asked again
        after the confirmation, because the gate is a live key and can
        have been locked while the dialog was up.
        """
        if not self._writes_allowed():
            self._refuse_write()
            return
        if self._on_store_assist is None:
            self.say(
                "store assist not sent: this screen has no write path to "
                "hand the bytes to",
                refused=True,
            )
            return
        if newest_backup(self._backup_root) is None:
            self.say(
                f"store assist: no {BACKUP_DUMP_NAME} was found under "
                f"{self._backup_root} — there are no backed-up bytes to send",
                refused=True,
            )
            return
        self.app.push_screen(
            NumberPrompt(
                "Store assist — which preset's backed-up bytes? (00-79)",
                select_all=True,
            ),
            self._store_slot_chosen,
        )

    def action_reload_on_unit(self) -> None:
        """``ctrl+r``: force a reload of the preset this screen edits.

        **The write gate is asked before the question is asked.**  A
        locked gate is a refusal with the family's sentence, and no
        dialog opens for a reload that cannot happen.  It is asked
        again after the confirmation, because the gate is a live key
        and can have been locked while the dialog was up.

        The reload leaves the preset and comes straight back -- one
        "previous" press and one "next" press (buttons 5 and 7), two
        selection events, about a second.  Any pending edit on the
        unit's display is committed into the slot it names when the
        selection steps, and at the end the unit is back on this preset
        playing its stored content.
        """
        if not self._writes_allowed():
            self._refuse_write()
            return
        if self._on_reload_preset is None:
            self.say(
                "reload not sent: this screen has no write path to hand the reload to",
                refused=True,
            )
            return
        slot = self._slot
        if slot is None:
            self.say(
                "reload not sent: this screen does not know which preset it is editing",
                refused=True,
            )
            return
        self._reload_pending = slot
        self.app.push_screen(
            ConfirmScreen(
                self._reload_question(slot),
                confirm="Reload",
                cancel="Cancel",
            ),
            self._reload_confirmed,
        )

    def _reload_question(self, slot: int) -> str:
        panel = f"P{slot:02d}"
        return (
            f"Reload {panel} on the unit?\n\n"
            "The app leaves the preset and comes straight back (button 5 "
            "then button 7 -- two selection events, about a second). "
            "Any pending edit on the unit's display is committed into "
            "the slot it names when the selection steps, and at the end "
            "the unit is back on this preset playing its stored content."
        )

    def _reload_confirmed(self, confirmed: Optional[bool]) -> None:
        pending = self._reload_pending
        self._reload_pending = None
        if not confirmed or pending is None:
            return
        if not self._writes_allowed():
            self._refuse_write()
            return
        callback = self._on_reload_preset
        if callback is None:  # pragma: no cover - the action refused first
            self.say("reload not sent: no write path is open", refused=True)
            return
        callback(pending)

    def _store_slot_chosen(self, text: Optional[str]) -> None:
        """The slot prompt's answer: a panel number, then the bytes.

        The number is the unit's own -- ``P00`` through ``P79``, the
        numbers the unit's display shows in preset select -- and it is
        the dump's slot number directly: the unit counts from zero
        here, so no conversion happens and none can drift.
        """
        if text is None or not text.strip():
            return
        try:
            number = int(text.strip())
        except ValueError:
            self.say(f"{text!r} is not a number", refused=True)
            return
        if not 0 <= number < BACKUP_PRESET_COUNT:
            self.say(
                "the unit's presets are numbered P00 through "
                f"P{BACKUP_PRESET_COUNT - 1:02d}, not {number}",
                refused=True,
            )
            return
        slot = number
        try:
            raw = backup_slot_raw(self._backup_root, slot)
        except StoreAssistError as exc:
            self.say(str(exc), refused=True)
            return
        self._store_pending = (slot, raw)
        self.app.push_screen(
            ConfirmScreen(
                self._store_question(slot),
                confirm="Send",
                cancel="Cancel",
            ),
            self._store_confirmed,
        )

    def _store_question(self, slot: int) -> str:
        """What the confirmation says before anything is sent.

        Four facts and no promises: which bytes, where they land, the
        gesture that puts the unit there, and what the unit keeps for
        itself.  The name is the unit's -- a dump never takes it -- and
        the *Edited Preset* mark is transient, so neither is presented
        as something the send changes.  The preset is named the way the
        unit's display names it.
        """
        panel = f"P{slot:02d}"
        return (
            f"Send the backed-up bytes of {panel} to the unit?\n\n"
            "The app will step the unit's preset selection with button-5 "
            f"presses until it reaches {panel}, then send the backed-up "
            "bytes.\n\n"
            "Any pending edit shown on the unit's display will be stored "
            "into the slot it names when the selection steps; check the "
            "unit is not showing a save dialog before confirming.\n\n"
            "The unit keeps its own name."
        )

    def _store_confirmed(self, confirmed: Optional[bool]) -> None:
        """The confirmation's answer: hand the bytes to the app, or not."""
        pending = self._store_pending
        self._store_pending = None
        if not confirmed or pending is None:
            return
        if not self._writes_allowed():
            self._refuse_write()
            return
        callback = self._on_store_assist
        if callback is None:  # pragma: no cover - the action refused first
            self.say("store assist not sent: no write path is open", refused=True)
            return
        slot, raw = pending
        callback(slot, raw)

    def action_toggle_view(self) -> None:
        """``tab``: the next column group.

        The sibling rxved's gesture, on its part table: the columns
        that do not fit at once are shown a group at a time.
        """
        self._view = (self._view + 1) % len(COLUMN_VIEWS)
        self._build_table()
        self._update_hints()

    def action_undo(self) -> None:
        """``z``: the app's undo, one change."""
        self._on_undo(1)

    def action_undo_all(self) -> None:
        """``Z``: the app's undo, all of it."""
        self._on_undo(None)

    def action_type_digit(self, digit: str) -> None:
        """A digit starts a number on the focused cell, in place.

        The spreadsheet gesture: the value is three keystrokes, and a
        dialog for each of them is a step nobody wants.  The digit is
        the seed of the prompt, so the number that was wanted is one
        keystroke away from being typed.

        There is nothing to type into an enumerated column, and saying
        so is more use than ignoring the key: ``⏎`` picks one.
        """
        index, column = self._cursor()
        if index is None or column is None:
            return
        if column.kind == "enum":
            self.say(
                f"{column.label} is one of {', '.join(column.names)} — ⏎ picks one",
                refused=True,
            )
            return
        if column.kind != "number":
            self.say(f"there is no number to type into {column.label}", refused=True)
            return
        self._offer_number(index, column, seed=digit)

    def action_bump(self, by: int) -> None:
        """``+`` / ``-``: one step of the focused value.

        Not a shortcut around the edit path but another way into it:
        the stepped value goes to the same ``on_apply`` a typed number
        goes to, so the gate refuses it, the range check sees it, the
        log records it and the read-back verifies it.

        The step is the value's own: a channel moves by one, a volume
        by one stored step, a key by one semitone.  A step that cannot
        move is said, not sent -- a write that sends the value the
        unit already holds would put an entry on the undo timeline that
        restores something that was never changed.
        """
        index, column = self._cursor()
        if index is None or column is None:
            return
        if column.kind == "enum":
            self.say(f"{column.label} is a list of names — ⏎ picks one", refused=True)
            return
        if column.kind != "number":
            self.say(f"+ / - step a number; {column.label} is not one", refused=True)
            return
        track = self._preset.tracks[index]
        current = int(getattr(track, column.field))
        if column.fmt == "pan" and current == PAN_SOUND:
            # SOUND is not a number, so there is no step to take from
            # it: the value has to be typed.
            self.say(
                "pan is SOUND — type a number for it, or pick a sound",
                refused=True,
            )
            return
        stepped = current + by * column.step
        low, high = TRACK_RANGES[column.field]
        if not low <= stepped <= high:
            self.say(
                f"track {index + 1} {column.label} takes {column.low}-{column.high}",
                refused=True,
            )
            return
        self._apply(index, column, stepped)

    def action_live_mute(self) -> None:
        """``l``: toggle the live mute of the track under the panel's cursor.

        **This is the unit's own gesture, and the app cannot see its
        target.**  The two Track keys pressed together mute or unmute
        the track the panel's cursor sits on -- buttons 10 and 12 as one
        chord; the display's track number turns into "-" and the track's
        notes go silent, while no preset byte changes (the audio gate is
        the unit's live mask, not the preset).  There is no read-back
        for the cursor, so this toggles whichever track the panel points
        at; the sentence says so rather than pretending the screen's
        cursor chooses the track.  It changes the live mask, not the
        stored pile, so it is the control to use for what plays now.

        **No write gate.**  This is a panel gesture, not a preset write:
        the remote window's chord is ungated for the same reason, and
        gating it here would refuse the one control that actually turns
        a track off while the gate is locked.
        """
        if self._on_live_mute is None:
            self.say(
                "live mute not sent: this screen has no write path",
                refused=True,
            )
            return
        self._on_live_mute()

    def action_toggle_track(self) -> None:
        """``space``: switch the track on or off in the stored preset.

        The unit marks an off track with **pressure 3** (the panel's
        ``-``); an on track is pressure 1 (KEY).  The pile is the separate
        layer/selection list and is not touched.  This is a normal field
        edit: the app sends the whole preset, and ``ctrl+s`` stores it
        with the unit's Enter/Save three times.
        """
        index, _column = self._cursor()
        if index is None:
            return
        if not self._writes_allowed():
            self._refuse_write()
            return
        current = self._preset.tracks[index]
        wanted = 1 if current.pressure == PRESSURE_OFF else PRESSURE_OFF
        self._apply(index, PRESSURE_COLUMN, wanted)
        self.say(f"track {index + 1} {'off' if wanted == PRESSURE_OFF else 'on'}")

    def action_track_cursor(self, direction: int) -> None:
        """``[`` / ``]``: move the unit's own track cursor one step.

        **The unit is the display for this cursor.**  The Track keys
        (buttons 10 and 12) move the blinking track number on the
        panel's preset display, and ``l`` mutes the track it points at;
        the app cannot read that number back, so this sentence tells the
        owner to watch the unit rather than pretending a row is marked.
        """
        if self._on_panel_button is None:
            self.say(
                "track cursor not moved: this screen has no panel path",
                refused=True,
            )
            return
        number = TRACK_LEFT_BUTTON if direction < 0 else TRACK_RIGHT_BUTTON
        label = "Track L" if direction < 0 else "Track R"
        self._on_panel_button(number)
        self.say(
            f"{label} sent — watch the unit's display; l mutes the track it points at"
        )

    def action_open_fx(self) -> None:
        """``x``: the effect editor over this same preset."""
        if self._on_open_fx is None:
            self.say("the effect editor is not reachable from here", refused=True)
            return
        self._on_open_fx()

    def action_open_preset_list(self) -> None:
        """``p``: open the preset list, where the main window opens it.

        The app owns the browser and the selection: the screen asks and
        sends nothing itself.  ``None`` means there is no callback to
        hand the request to, and the action says so rather than
        pretending a browser opened.
        """
        if self._on_open_preset_list is None:
            self.say("preset list is not wired", refused=True)
            return
        self._on_open_preset_list()

    # --- offering an edit --------------------------------------------------

    def on_data_table_cell_selected(self, event) -> None:
        """``⏎`` on a cell: edit it.

        Enter arrives here rather than as a binding, for the reason the
        browser has no app-level Enter: a focused DataTable consumes
        the key for its own selection.  With ``cursor_type="cell"`` it
        comes back as a *cell*, which is the only way to know which of
        a row's values the key was aimed at.
        """
        if event.data_table.id != self.TABLE_ID:
            return
        self._edit_cell()

    def _edit_cell(self) -> None:
        """Edit the focused cell, in the way its values are written."""
        index, column = self._cursor()
        if index is None or column is None:
            return
        if column is SPOT_COLUMN or column is NAME_COLUMN:
            self._pick_sound(index)
            return
        if column is ON_COLUMN:
            self.action_toggle_track()
            return
        if column is TRACK_COLUMN:
            self.say(
                f"{column.label} is the track's number, not a value — it is "
                "not edited here",
                refused=True,
            )
            return
        if column.field == "":
            self.say(f"{column.label} is read-only", refused=True)
            return
        if column.kind == "enum":
            self._offer_names(index, column)
            return
        self._offer_number(index, column)

    def _offer_names(self, index: int, column: _Column) -> None:
        """A list to pick from, for a column whose values are words.

        The answer is the index the field numbers the value by, so the
        range check and the undo log downstream cannot tell which door
        the edit came through.
        """
        track = self._preset.tracks[index]
        current = column.stored_of(track)

        def picked(value: Optional[int]) -> None:
            if value is None:
                return
            self._apply(index, column, value)

        self.app.push_screen(
            ChoicePicker(
                f"track {index + 1} — {column.label}",
                list(column.names),
                current,
            ),
            picked,
        )

    def _offer_number(
        self, index: int, column: _Column, seed: Optional[str] = None
    ) -> None:
        """A number to type, with the range and the current value on it.

        The question names the range, because the value is checked
        against it -- and the current value is the seed, so that the
        first keystroke replaces it rather than appending to it.
        """
        track = self._preset.tracks[index]
        shown = column.text_of(track)
        question = (
            f"track {index + 1} — {column.label} "
            f"({column.low}-{column.high}), now {shown}"
        )

        def done(text: Optional[str]) -> None:
            if text is None or not text.strip():
                return
            self._apply_text(index, column, text.strip())

        self.app.push_screen(
            NumberPrompt(
                question,
                seed if seed is not None else column.edit_text(track),
                select_all=seed is None,
            ),
            done,
        )

    def _pick_sound(self, index: int) -> None:
        """The app's sound picker, for the track under the cursor.

        The screen does not own a picker: the app has the catalog and
        the groups, so the app runs it.  What the screen says is which
        track wants a sound.
        """
        self._on_sound_picked(index, index + 1)

    # --- committing a change ------------------------------------------------

    def _apply_text(self, index: int, column: _Column, text: str) -> None:
        """A typed value, turned into the stored value it means."""
        if column.fmt == "pan" and text.upper() == "SOUND":
            self._apply(index, column, PAN_SOUND)
            return
        try:
            value = int(text)
        except ValueError:
            self.say(f"{text!r} is not a number", refused=True)
            return
        if not column.low <= value <= column.high:
            self.say(
                f"track {index + 1} {column.label} takes "
                f"{column.low}-{column.high}, not {value}",
                refused=True,
            )
            return
        if column.fmt == "pan":
            self._apply(index, column, pan_stored(value))
            return
        self._apply(index, column, column.to_stored(value))

    def _apply(self, index: int, column: _Column, value: int) -> None:
        """One change to the live preset, and the app's write path after it.

        The order matters: the model is changed first, so the screen
        shows what it now says, and the app is told afterwards.  The
        value the cell held is kept, because a change the app refuses
        has to be put back.
        """
        if not self._writes_allowed():
            self._refuse_write()
            return
        track = self._preset.tracks[index]
        before = column.text_of(track)
        try:
            updated = column.to_track(track, value)
        except ValueError as exc:
            # The model's own refusal, which names the range in words.
            self.say(str(exc), refused=True)
            return
        after = column.text_of(updated)
        self._pending.append((index, column.key, getattr(track, column.field)))
        self._preset.tracks[index] = updated
        # The row, not just the cell: the `on` cell also depends on the
        # status, and an edit that turns *EXT* on must dim it at once.
        for visible in self._columns():
            self._draw_cell(index, visible)
        # The sentence the undo log is labelled with names the *field*,
        # not the column's short label: "track 3 volume 50→49".  The
        # field is what a reader of the undo log will be looking for.
        self._on_apply(
            self._preset,
            f"track {index + 1} {column.field or column.label} {before}→{after}",
        )

    def _draw_cell(self, index: int, column: _Column) -> None:
        """Redraw one cell from the model.

        ``DataTable.update_cell`` rather than ``refresh()``, which
        repaints the cells the table already has and never recomputes
        them -- an edit that appears to do nothing.

        Guarded, because a worker can finish after the screen it was
        started from has closed.
        """
        try:
            table = self.query_one(f"#{self.TABLE_ID}", DataTable)
            table.update_cell(
                f"t{index + 1}",
                column.key,
                self._row(index)[self._column_index(column)],
            )
        except Exception:  # pragma: no cover - the screen closed underneath
            return

    def _column_index(self, column: _Column) -> int:
        """Where a column sits in the current view."""
        for index, candidate in enumerate(self._columns()):
            if candidate.key == column.key:
                return index
        return 0  # pragma: no cover - the column is always there

    # --- the seams the app looks for ---------------------------------------

    def say(self, message: str, *, refused: bool = False, ok: bool = False) -> None:
        """One sentence on this screen's own status line.

        The app's ``#status`` is the dimmed backdrop behind this modal,
        so a refusal written there is written where nobody is looking.
        ``[b]refused[/b] · `` in front of a refusal, because the
        difference between a "no" and news is carried by the words and
        the weight, not by the colour alone.  ``ok`` marks a verified
        store green.

        The optional ``say_`` hook gets the sentence too, when a caller
        wants it; the app does not pass one, because its own line is
        behind this modal.
        """
        try:
            status = self.query_one(".status", Static)
        except Exception:  # pragma: no cover - the screen closed under us
            return
        prefix = "[b]refused[/b] · " if refused else ""
        status.update(prefix + message)
        status.set_class(refused, "refused")
        status.set_class(ok and not refused, "ok")
        if self._say is not None:
            self._say(message, refused=refused)

    def result(
        self,
        ok: bool,
        message: str,
        *,
        cell: Optional[Tuple[int, str]] = None,
    ) -> None:
        """What the app's write path did with the last change.

        A success is drawn where it happened, and a refusal puts the
        cell back: the value the model held before the change is still
        known, so a change the unit did not accept does not stay on
        screen as though it had.

        ``cell`` is the ``(track index, column key)`` the result is
        about, so the right cell is the one that goes back.

        The app calls this on the main thread -- from a worker, with
        ``call_from_thread`` -- and a worker that outlives the screen
        finds the guard in :meth:`say`.
        """
        if cell is not None:
            if ok:
                self._forget(cell)
            else:
                self._rollback(cell)
        self.say(message, refused=not ok)

    def _forget(self, cell: Tuple[int, str]) -> None:
        """Drop the record of a change that landed.

        The record is only kept so that a change that *did not* land
        can be put back; a change that landed has nothing to put back,
        and its record is dropped so the list does not grow for ever.
        """
        index, key = cell
        for position, entry in enumerate(self._pending):
            if entry[0] == index and entry[1] == key:
                del self._pending[position]
                return

    def _rollback(self, cell: Tuple[int, str]) -> None:
        """Put one cell back to the value it held before the change.

        The **newest** unreported change for the cell is the one that
        goes back, so a burst of nudges steps back one at a time
        rather than all at once: a change that failed leaves the cell
        where the change before it left it.
        """
        index, key = cell
        column = self._column_by_key(key)
        if column is None or column.field == "":
            return
        for position in range(len(self._pending) - 1, -1, -1):
            entry_index, entry_key, old_value = self._pending[position]
            if entry_index != index or entry_key != key:
                continue
            del self._pending[position]
            track = self._preset.tracks[index]
            try:
                self._preset.tracks[index] = self._with_value(track, column, old_value)
            except ValueError:  # pragma: no cover - the old value was valid
                return
            for visible in self._columns():
                self._draw_cell(index, visible)
            return

    @staticmethod
    def _with_value(track: Track, column: _Column, value: int | bool) -> Track:
        """The track with one column's value put back to ``value``.

        The sustain switch is the awkward one: its stored value is a
        bit and its panel value is a name, so the bit is put back as a
        bit.  Everything else is a number.
        """
        if column.bool_field:
            return track.updated(**{column.field: bool(value)})
        return track.updated(**{column.field: int(value)})

    def _refuse_write(self) -> None:
        """Say that this screen may not write, and change nothing.

        The sentence carries the remedy as well as the state, and `w`
        works here, so the reader can act on it without closing the
        editor.
        """
        if self._gate is not None:
            self.say(self._gate.refuse("the change"), refused=True)
            return
        self.say(
            "edits are refused — the app has not opened the write path",
            refused=True,
        )

    # --- the app's seam ----------------------------------------------------

    def set_preset(self, preset: Preset, slot: Optional[int] = None) -> None:
        """Adopt a preset read from the unit and draw it.

        The app's ``ctrl+r`` walks the unit and reads the slot back; this
        is where those bytes replace the model the table is showing.  The
        screen keeps one live preset object, so the reference is replaced
        and the table rebuilt from it.

        ``slot`` is the panel number the preset came from, when the
        caller knows it: selecting a preset in place changes the name
        the title shows and the slot the reload and rename prompts
        name.  ``None`` leaves the slot the screen already knows alone.
        """
        self._preset = preset
        if slot is not None:
            self._slot = slot
        self._pending.clear()
        self._build_table()
        with contextlib.suppress(Exception):
            self.query_one("#multi-title", Label).update(self._title())

    def redraw(self) -> None:
        """Draw the table again from the model.

        **Cross-task fix (T8).**  The app's undo puts the *previous*
        preset back into the live model -- the model's bytes are what
        the next edit is built on -- and the table has to say so.  The
        screen's own :meth:`result` knows about one cell; the track
        pile and the sound a track plays are not fields, so they are not
        cells it can put back on its own, and the app asks for the whole
        table to be drawn again.

        The table is rebuilt rather than repainted, because the columns
        are the same columns but the cells are not the same cells.
        """
        self._build_table()
