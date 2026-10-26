# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# The key-hint legend is ported from the sibling rxved and p2ked projects,
# which port it from s3ked, eosed and k2kremote:
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

"""``ensqsqed`` -- a terminal browser for the Ensoniq SQ-R Plus.

Two panes: groups on the left, that group's sounds on the right.

Four rules, each about not lying to the user:

**Moving the cursor never sends anything.** Selecting a sound changes
what the instrument plays. Selection is bound to Enter and nothing else,
and a test enforces it.

**A program change is not a program change.** This instrument has no Bank
Select. The group -- Internal, ROM, Card A, Card B -- is chosen with a
*program change*, 124 to 127, and every later program change means
whatever that said until the next one. So Enter always sends the group
selector first, and the status line says both numbers it used. A drum kit
needs no selector at all; its program change is absolute.

**Only Internal has a bank dump.** One Internal Sound Banks Dump Request
brings back all eighty sounds with their names in 32647 bytes. ROM, Card
A and Card B have no bank dump request; their names are read the slow
way, by selecting each sound and reading its current-sound record. `S`
does that for every such group; `r` does it for the group on screen.

**The unit numbers from 1 and MIDI numbers from 0.** The manual says so
itself. The slot pane shows both, because this is the family of bug these
projects exist to avoid.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import threading
from collections import deque
import time
from dataclasses import fields
from typing import Any, Dict, List, Optional, Tuple

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import DataTable, Header, Input, ProgressBar, RadioSet, Static

from ensqsqed.choices import ChoicePicker
from ensqsqed.file_picker import FilePickerScreen, start_folder
from ensqsqed.fx import FxScreen
from ensqsqed.gate import EditGate
from ensqsqed.import_bank import BankImportScreen
from ensqsqed.import_single import SingleImportScreen
from ensqsqed.multi import (
    COLUMN_VIEWS,
    MultiScreen,
    PresetListScreen,
    SoundPickerScreen,
)
from ensqsqed.remote import (
    PANEL_KEYS as _PANEL_KEYS,
    SELECT_PRESET,
    SELECT_SOUND,
    RemoteScreen,
)
from sq import banks
from sq import catalog as cat
from sq import config as cfg
from sq import importer
from sq import messages as msg
from sq import preset_labels
from sq import preset_cache
from sq.bridge import (
    AllPresetsDump,
    DeviceError,
    PRESET_SETTLE,
    PresetAlert,
    PresetMismatch,
    preset_needs_write,
)
from sq.favorites import Favorites, Favorite
from sq.presets import PRESSURE_NAMES, STATUS_NAMES, Preset, sound_word
from vinsynlib.favorites import rating_cell
from vinsynlib import keys
from vinsynlib.cli import add_common_arguments, make_parser, validate_common
from vinsynlib.keys import wrap_blocks as _wrap_blocks
from vinsynlib.ui.confirm import ConfirmScreen
from vinsynlib.ui.hints import KeyHints
from vinsynlib.ui.modals import (
    CategoryScreen,
    ChoiceScreen,
    RatingFilter,
    RatingFilterScreen,
    SearchScreen,
)
from vinsynlib.ui.report import ReportScreen
from vinsynlib.ui.work import BusyGate
from vinsynlib.undo import Record, UndoLog

__all__ = [
    "EnsqsqedApp",
    "EditGate",
    "main",
    "build_parser",
    "KEY_HINTS",
    "wrap_blocks",
]

_SEP = " · "

#: The first label PANEL_KEYS gives for each button number, so a sentence
#: can name the button it sent. Duplicates (5, 7, 12) keep the first.
_REMOTE_LABELS: dict[int, str] = {}
for _entry in _PANEL_KEYS:
    if _entry.number is not None and _entry.number not in _REMOTE_LABELS:
        _REMOTE_LABELS[_entry.number] = _entry.label
#: What `v` steps through. The same three the sibling rxved and p2ked use,
#: so the key means the same thing across these projects.
VIEW_ALL = "all"
VIEW_BANK_FAVOURITES = "bank-favourites"
VIEW_ALL_FAVOURITES = "all-favourites"
VIEW_CYCLE = (VIEW_ALL, VIEW_BANK_FAVOURITES, VIEW_ALL_FAVOURITES)
VIEW_LABELS = {
    VIEW_ALL: "all slots",
    VIEW_BANK_FAVOURITES: "favourites in this bank",
    VIEW_ALL_FAVOURITES: "every favourite, all banks",
}

#: The writable sound groups an import can target, in the order the target
#: choice lists them. ``ROM`` and ``DRUM`` are not writable, so they are
#: never offered.
_IMPORT_BANKS = ("INT",)

#: The panel's words for each writable group.  The wire takes the group id;
#: every sentence a person reads uses these.
_IMPORT_LABELS = {
    "INT": "Internal (RAM)",
    "CARDA": "Card A",
    "CARDB": "Card B",
}

#: How long to let the unit load a selected sound before reading its name
#: back. A program change is not instantaneous: reading too early returns
#: the previous sound, silently, and every name would be one slot out of
#: step. A twentieth of a second is enough on the bench and short enough
#: that a full scan stays bearable.
SCAN_SETTLE = 0.08

#: How many consecutive slots may read back the *same* name before a scan
#: decides the unit is not moving. Two adjacent sounds can share a name;
#: four in a row is not a bank, it is a selector that did not land -- the
#: normal state of Card A/B with no card fitted, where the unit keeps
#: playing whatever it already had.
STUCK_SLOT_LIMIT = 4

#: How long the preset bank read at startup is trusted by ``p``.  The
#: bank changes only when somebody stores a preset on the panel, and a
#: twelve-second re-read on every press is what this cache exists to
#: avoid; a stale entry is re-read after this many seconds.
PRESET_CACHE_TTL = 60.0

#: How long a persisted sidecar (the live names) or the all-presets
#: cache is trusted at startup.  A fresh cache means no startup MIDI
#: read and therefore no "busy" gap; an old or missing one is checked
#: once, in the background, with the startup meter saying so.
STARTUP_CHECK_TTL = 6 * 3600.0

#: How many times the store assist asks the unit to take the backed-up
#: bytes.  The receiver measured on 2026-10-22 accepted byte-identical
#: dumps 2/2 before panel navigation and 1/10 after it, with no cause
#: to name; :meth:`sq.bridge.SqBridge.send_preset_with_retry` is the
#: app's answer, and three tries is the budget it is given here.
STORE_ASSIST_ATTEMPTS = 3


def _group_source(group: "banks.Group") -> str:
    """Where this group's names can come from, in one word.

    ``unit`` means the instrument will dump them (Internal only);
    ``ROM`` and ``card`` mean it will not, and a name has to be learned by
    selecting the sound and reading it back, or come from a catalog. The
    old cell said ``rom`` for all three, which was wrong for a card.
    """
    if group.readable:
        return "unit"
    if group.id in ("CARDA", "CARDB"):
        return "card"
    return "ROM"


def _view_key(blocks: tuple[str, ...]) -> tuple[str, ...]:
    """Swap the family's ``F favourites view`` for this tool's ``v``.

    ``f`` writes the favourites database and ``F`` does not, so the
    family's shift-pair left a write one slip away from a read; ``v`` has
    no such twin. rxved and p2ked made the same swap, and the family asks
    a tool that renames a shared key to say why in its own test -- see
    tests/test_conformance.py, where the whole divergence is two
    sentences.
    """
    return tuple(
        "v favourites view" if block == "F favourites view" else block
        for block in blocks
    )


class PromptScreen(ModalScreen[Optional[str]]):
    """One line of text."""

    DEFAULT_CSS = """
    PromptScreen { align: center middle; }
    PromptScreen > Vertical {
        width: 70; height: auto; border: thick $accent;
        background: $surface; padding: 1 2;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, prompt: str, current: str = "") -> None:
        super().__init__()
        self._prompt = prompt
        self._current = current

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical
        from textual.widgets import Label

        with Vertical():
            yield Label(self._prompt)
            yield Input(value=self._current, id="value")

    def on_mount(self) -> None:
        self.query_one("#value", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class WaitScreen(ModalScreen[None]):
    """A modal that says a long read or write is running.

    It has no bindings on purpose: the operation it covers owns the
    exchange, and dismissing it would leave a worker with nowhere to
    report.  The caller pops it when the worker is done.
    """

    DEFAULT_CSS = """
    WaitScreen { align: center middle; }
    WaitScreen > Vertical {
        width: 60; height: auto; border: thick $accent;
        background: $surface; padding: 1 2;
    }
    WaitScreen .message { height: auto; text-style: bold; }
    WaitScreen .note { height: auto; color: $text-muted; }
    """

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical
        from textual.widgets import Label, Static

        with Vertical():
            yield Label(self._message, classes="message")
            yield Static(
                "please wait — the unit is being read or written",
                classes="note",
                markup=False,
            )


#: The writable sound groups an import can target, in the order the target
#: choice lists them. ``ROM`` and ``DRUM`` are not writable, so they are
#: never offered.
_IMPORT_BANKS = ("INT",)

#: The panel's words for each writable group.  The wire takes the group id;
#: every sentence a person reads uses these.
_IMPORT_LABELS = {
    "INT": "Internal (RAM)",
    "CARDA": "Card A",
    "CARDB": "Card B",
}


class BackupSelectionScreen(ModalScreen[Optional[Dict[str, Any]]]):
    """Screen for selecting backup options.

    Let the user select which sound groups to back up (INT, CARDA, CARDB),
    either individually or all at once, and what content to back up (sounds,
    presets, or all content).  When the user confirms, the screen dismisses
    with the selected options and the app performs the backup.
    """

    DEFAULT_CSS = """
    BackupSelectionScreen { align: center middle; }
    BackupSelectionScreen > Vertical {
        width: 60; height: auto; border: thick $accent;
        background: $surface; padding: 1 2;
    }
    BackupSelectionScreen .label { margin: 1 0; }
    BackupSelectionScreen .note { height: auto; color: $text-muted; }
    """

    def __init__(
        self,
        *,
        backup_root: str = "",
    ) -> None:
        super().__init__()
        self._backup_root = backup_root
        self._selected_groups: set[str] = set()
        self._selected_content: str = "sounds"  # sounds, presets, all

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical
        from textual.widgets import Checkbox, DataTable, Label, RadioSet, Static

        with Vertical():
            yield Label("Select sound groups to back up", classes="label")
            yield RadioSet(
                "All groups (INT, Card A, Card B)",
                "Internal (INT)",
                "Card A",
                "Card B",
                id="groups",
            )
            yield Label("Select content to back up", classes="label")
            yield RadioSet(
                "Sounds",
                "Presets",
                "All content",
                id="content",
            )
            yield Static(
                "Note: CARDA/CARDB sounds/presets can only be backed up if "
                "the card is present and the unit can read them. Preset names "
                "are the unit's own and are not restored by a dump; local "
                "labels are copied beside the dump as a sidecar. The effect "
                "block needs the panel's ENTER/SAVE to be stored.",
                classes="note",
            )
            yield Static("")  # spacer
            yield Static("")  # spacer

    def on_radio_set_selected(self, event: RadioSet.Selected) -> None:
        """Handle group and content selection changes."""
        if event.region == "groups":
            # "All groups" overrides individual selections
            if event.pressed_label == "All groups (INT, Card A, Card B)":
                self._selected_groups = {"INT", "CARDA", "CARDB"}
            else:
                # Individual group selection: determine which was pressed
                label = event.pressed_label
                if label == "Internal (INT)":
                    self._selected_groups = {"INT"}
                elif label == "Card A":
                    self._selected_groups = {"CARDA"}
                elif label == "Card B":
                    self._selected_groups = {"CARDB"}
                else:
                    # If "All groups" was unselected and no individual is
                    # pressed, clear selection
                    self._selected_groups = set()
        elif event.region == "content":
            if event.pressed_label == "Sounds":
                self._selected_content = "sounds"
            elif event.pressed_label == "Presets":
                self._selected_content = "presets"
            elif event.pressed_label == "All content":
                self._selected_content = "all"

    def action_button_press(self) -> None:
        """Handle button presses (Enter or button click)."""
        # The modal owns its own button handling; dismiss with selections
        self.dismiss(
            {
                "groups": self._selected_groups,
                "content": self._selected_content,
                "backup_root": self._backup_root,
            }
        )


#: The legend. Every binding the app has, in the order somebody meets them.
#: Built from the family's shared legend rather than hand-written: it used
#: to be a tuple of its own, in its own order. ``keys.legend`` puts this
#: tool's own hints in the one place the family leaves for them -- after
#: `n note`, before `i device` -- and this tool's are the import key, the
#: preset editors (``m``, ``x``, ``p``), the scan key, the two rating keys,
#: and the ones the write path adds (edit program, mix, global, mode
#: select, gate, undo, undo all). ``w``/``z``/``Z`` are the family's
#: canonical editor keys, spelled the family's way; this browser legend has
#: no editor tier, so they are carried here as this tool's hints. `v`
#: apart, every other key it binds is a shared one, which `_view_key`
#: renames.
#:
#: ``I import`` leads the list because it is the newest and most
#: destructive surface: it replaces whole banks, so the eye should find it
#: before the editors. The three editor keys follow, the way the sibling
#: nanosyned puts its own edit keys first: they are this tool's headline,
#: and the family leaves the one place for a tool's own hints for exactly
#: that.
WRITE_HINTS: tuple[str, ...] = (
    "I import",
    "b backup",
    "M mode",
    "m multi",
    "x fx",
    "p preset list",
    "ctrl+o remote",
    "S scan device",
    "R rebuild catalog",
    "* rating",
    "V rating filter",
    "C category",
    "z undo",
    "Z undo all",
    "w write gate",
)
KEY_HINTS = _view_key(
    keys.legend(extras=WRITE_HINTS, favourites=True, channel=True, select=True)
)


def wrap_blocks(blocks, width: int, sep: str = _SEP) -> str:
    """Pack hints into lines no wider than ``width``.

    Re-exported from :func:`vinsynlib.keys.wrap_blocks`, which every tool
    in this family now uses. Kept as a name because it is part of this
    module's published surface.
    """
    return _wrap_blocks(blocks, width, sep)


#: Family quiet set plus this instrument's own bridge errors.
_BRIDGE_ERRORS: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
    ValueError,
    LookupError,
    RuntimeError,
    SystemError,
)


def _plain(text: str) -> str:
    """``text`` as markup that renders as exactly itself.

    A preset's name is sixteen bytes the unit wrote, and a name holding
    a square bracket would otherwise be read as markup.  The backslash is
    Textual's own escape; the sibling screens have the same helper.
    """
    return text.replace("[", "\\[")


#: The two editor screens, which share one write path.
EditorScreen = MultiScreen | FxScreen


def _copy_preset(preset: Preset) -> Preset:
    """A copy of a preset, deep enough to be a snapshot.

    The bytes are the copy: a preset that goes back to the unit is the
    bytes it was read as, and a snapshot that is a different object with
    the same fields is worth exactly as much.
    """
    return Preset.from_bytes(preset.to_bytes())


def _column_key(field: str) -> Optional[str]:
    """The table column that writes ``field``, or ``None``.

    The columns are the screen's; the app asks the model's field names
    for the key that shows them, because the field is what the model's
    own API calls it and the key is what the table's cell is called.
    """
    for _name, columns in COLUMN_VIEWS:
        for column in columns:
            if column.field == field:
                return column.key
    return None


class EnsqsqedApp(App):
    """The browser."""

    CSS = """
    Screen { layers: base; }
    #panes { height: 1fr; }
    /* The startup meter, shown only while the background reads run. */
    #startup {
        height: 1;
        padding: 0 1;
        background: $boost;
    }
    #startup-label { width: auto; padding-right: 1; color: $text; }
    #startup-label.waiting { color: $warning; text-style: bold; }
    #startup-label.ready { color: $success; text-style: bold; }
    #startup-bar { width: 1fr; }
    #startup.hidden { display: none; }
    /* Sized to its content. A fixed width does not truncate the end
       of a label -- the table scrolls sideways and clips the
       *start*, so a bank reads as nonsense rather than as narrow. */
    #bank-table {
        width: auto; min-width: 20; max-width: 52;
        border-right: solid $panel;
    }
    #slot-table { width: 1fr; }
    DataTable { height: 1fr; }
    #detail { height: auto; padding: 0 1; border-top: solid $panel; }
    KeyHints { max-height: 3; overflow-y: hidden; }
    /* The ID is repeated or the rule never paints: an ID selector outranks
       a class, so a bare ``.refused`` was dead CSS and every refusal drew in
       the same grey as a success. Red and bold, the sibling rxved's shape,
       so a refused action is legible at a glance. */
    #status.refused {
        background: $error;
        color: $text;
        text-style: bold;
    }
    /* A verified write's confirmation: green, so "stored" cannot be
       mistaken for another grey status line. */
    #status.ok {
        background: $success;
        color: $text;
        text-style: bold;
    }
    /* The shared confirmation's prompt is a Label with no width, so a
       sentence longer than the dialog lays out at its full length and
       the dialog clips the end -- the FX type-change question and the
       catalog warnings all lost their last clause that way. Constrain
       it to the dialog and Textual wraps it. */
    ConfirmScreen #confirm-prompt { width: 1fr; }
    """

    BINDINGS = [
        # Enter is NOT bound here: a focused DataTable consumes it for its
        # own row selection, so it arrives as RowSelected instead.
        Binding("r", "read_names", "Read names"),
        Binding("S", "scan_device", "Scan device"),
        Binding("left_square_bracket", "channel_down", "Channel -"),
        Binding("right_square_bracket", "channel_up", "Channel +"),
        Binding("c", "pick_channel", "Set channel"),
        Binding("f", "toggle_favorite", "Favourite"),
        # `v`, not the family's `F`: `f` writes the favourites database and
        # `F` does not, so the family's shift-pair left a write one slip away
        # from a read. `v` has no such twin. rxved and p2ked made the same
        # swap; the rename is declared in tests/test_conformance.py.
        Binding("v", "cycle_favorites", "Favourites view"),
        Binding("t", "edit_tags", "Tags"),
        Binding("n", "edit_note", "Note"),
        Binding("asterisk", "edit_rating", "Rating"),
        Binding("V", "rating_filter", "Rating filter"),
        Binding("C", "pick_categories", "Categories"),
        Binding("z", "undo", "Undo"),
        Binding("Z", "undo_all", "Undo all"),
        Binding("w", "toggle_write_gate", "Write gate"),
        # The preset editors.  `m` is the eight-track editor, `x` the
        # effect editor over the same preset, and `p` the list of the
        # eighty presets the unit holds.  None of the three is a family
        # key, so none of them can be mistaken for one.
        Binding("m", "multi", "Multi"),
        Binding("x", "fx", "FX"),
        Binding("p", "preset_list", "Preset list"),
        # `I` is the import: whole banks and single sounds from a `.syx` or
        # `.mid`. It is destructive, so it is the browser's only capital
        # `I` and its whole flow asks twice behind the write gate.
        Binding("I", "import", "Import"),
        # `b` opens the backup selection screen.
        Binding("b", "backup", "Backup"),
        # `M` toggles the unit's panel between Select Sound and Select
        # Preset -- the two mode keys, on one key.  `m` is the editor.
        Binding("M", "toggle_mode", "Mode"),
        Binding("ctrl+o", "remote", "Remote"),
        Binding("slash", "search", "Search"),
        Binding("i", "device_info", "Device"),
        Binding("question_mark", "help", "Help"),
        Binding("tab", "switch_pane", "Switch pane"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        device,
        *,
        catalog=None,
        favorites=None,
        config_path: Optional[str] = None,
        read_names_at_startup: bool = False,
        allow_write: bool = False,
        backup_root: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.device = device
        self.catalog = catalog if catalog is not None else cat.empty()
        #: Injected, never opened here, so a test cannot write into the
        #: user's real database.
        self.favorites = favorites
        #: Where the send channel is remembered. Injected and None
        #: in tests, so a test cannot drop a config into the
        #: working directory.
        self._config_path = config_path
        #: Whether to re-read the Internal bank's names once, quietly,
        #: after the first frame. Off by default so a test never starts a
        #: background read it did not ask for; ``main`` turns it on, the
        #: way rxved re-reads its writable banks at startup.
        self._read_names_at_startup = read_names_at_startup
        #: Where the live names sidecar lives. Injected for the same
        #: reason: a test that exercises a bank read must not write into
        #: the real data directory.
        self._live_names_path: Optional[str] = None
        #: Where the store assist looks for backups.  Injected by
        #: ``main`` from the settings file; ``None`` means the screen
        #: uses its own default, which is the bench's location.
        self._backup_root = backup_root
        self.view_mode = VIEW_ALL
        self._lock = threading.RLock()
        self._bank_ids: List[str] = banks.group_ids()
        self._current_bank = self._bank_ids[0]
        self._current: List[banks.Slot] = []
        self._filling = False
        #: The write gate. Locked unless ``--allow-write`` was given;
        #: ``w`` toggles it. See :class:`EditGate`.
        self._write_gate = EditGate(armed=allow_write)
        #: The preset the editors are working on, and where it came from.
        #:
        #: **One live preset per opening.**  ``m`` and ``x`` are two
        #: views of the same object while an editor is open, so an edit
        #: made in one is an edit in the other -- and the bytes that go
        #: to the unit are the ones the model holds.  Opening an editor
        #: for the unit's current preset re-reads it first: the panel can
        #: change the unit while the app is not looking, and a cached
        #: on/off would make the next keypress write the wrong direction.
        #: A preset picked from the list is a copy of a slot and is kept.
        self._edit_preset: Optional[Preset] = None
        #: The sequence data the preset plays, verbatim. It does not
        #: change when a track is edited: the edits are in the preset's
        #: own 168 bytes, and the sequence blob goes with them.
        self._edit_seq_blob: Optional[bytes] = None
        #: The preset's 168 bytes as they were read -- the original, for
        #: the undo record and for what the session started from.
        self._edit_preset_raw: Optional[bytes] = None
        #: The model as it was before the change now being applied. It
        #: is what a refused change is put back to, and what the undo
        #: record carries.
        self._edit_snapshot: Optional[Preset] = None
        #: Whether the app has sent an edit to the unit that has not
        #: been stored/committed yet -- a rename on the unit uses it
        #: to decide whether to store the current edit too.
        self._edit_dirty: bool = False
        #: The slot the loaded preset came from, when it came from the
        #: preset list. ``None`` when the unit's own current preset was
        #: read, because the unit does not say which slot it is on.
        self._edit_slot: Optional[int] = None
        #: Whether a preset is loaded at all.
        self._preset_loaded = False
        #: The all-presets dump the last read produced, kept so that a
        #: slot picked out of the list can be loaded without reading
        #: again.
        self._all_dump: Optional[AllPresetsDump] = None
        #: When the preset bank was last read, for the warm cache.  The
        #: bank changes only on the panel, so a short-lived cache is safe.
        self._all_dump_at: float = 0.0
        #: Timer handle for the reload progress meter.
        self._reload_timer: Optional[Any] = None
        self._reload_t0: float = 0.0
        #: The user's own labels for preset slots.  Local to this app: the
        #: unit keeps its own preset name, and a dump never writes it; a
        #: real rename on the unit is `_rename_current_preset_on_unit`.
        self._preset_labels: Dict[int, str] = preset_labels.load()
        #: The editor screens that are open, so that a worker that
        #: finishes after its screen closed still has somewhere to
        #: report -- and so that the app knows which screen an undo
        #: belongs to.
        self._multi_screen: Optional[MultiScreen] = None
        #: The preset list screen, while open, so a reload worker can
        #: update it rather than pushing a new modal.
        self._preset_list_screen: Optional[PresetListScreen] = None
        #: Whether the multi screen was opened from the preset list.
        #: When True, dismissing the multi screen should re-open the
        #: preset list rather than restoring a previous editor.
        self._multi_from_preset_list = False
        #: Whether the preset list was opened from the MULTI editor (via
        #: ``p``).  When True, picking a preset replaces the current
        #: editor's preset in place instead of pushing a second screen.
        self._preset_list_from_multi = False
        self._fx_screen: Optional[FxScreen] = None
        self._remote_screen: Optional[RemoteScreen] = None
        #: The panel mode the last ``M`` left the unit in.  The unit does
        #: not report it, so this is the app's memory of what it sent.
        self._remote_mode: str = "SOUND"
        #: Whether the mode claim is real — false until the user presses M.
        self._remote_mode_confirmed: bool = False
        #: The single-sound import screen, while one is open.  A worker
        #: that rereads the bank after a write reports back through this,
        #: and a screen that has already closed is ``None``.
        self._single_import_screen: Optional[SingleImportScreen] = None
        self._active_editor: Optional[EditorScreen] = None
        self._previous_editor: Optional[EditorScreen] = None
        #: The remote window's press queue (the wire is paced, so bursts
        #: are buffered rather than refused).  Items are either an int
        #: (single button) or a tuple ("live_mute", 10, 12) for the chord.
        self._remote_queue: "deque[int | tuple]" = deque()
        self._remote_draining = False
        #: Coalescing for the editors' writes.  While one send is in
        #: flight, another press replaces the single target instead of
        #: being refused; the worker sends the last one when it is done.
        #: One slot, not a queue: a burst lands on one preset, and the
        #: value that matters is the last one pressed (the sibling
        #: p2ked's rule for a burst of +/- presses).
        self._apply_inflight = False
        self._apply_target: Optional[Preset] = None
        self._apply_target_description = ""
        self._apply_target_screen: Optional[EditorScreen] = None
        #: Why the last undo failed, when the failure had a sentence.
        self._undo_reason: Optional[str] = None
        #: Raised by a second `S` to stop a device scan between slots.
        self._scan_cancel = threading.Event()
        #: True from the moment a scan is accepted until its worker says it
        #: is done, so `S` can mean "stop" without mistaking a selection for
        #: a scan.
        self._scanning = False
        #: The family's busy gate, over the old boolean. It tracks whether
        #: an exchange is in flight (for an idle close) and refuses a
        #: second one before a worker is spawned.
        self._busy_gate = BusyGate()
        #: One exchange at a time, claimed on the main thread before the
        #: worker is spawned and released by the worker in a ``finally``.
        #: The gate alone cannot carry this: entering it *clears* a
        #: refusal, so between claiming and entering a second action would
        #: find neither signal set and start a second worker. ``_busy``
        #: reads this claim as the third signal.
        self._exchange = threading.Lock()
        #: The UI thread's ident, set by ``on_mount``.  Workers use it to
        #: decide whether a completion callback is already on the UI thread
        #: (a test or a direct call) and may run at once, or must be
        #: marshalled with ``call_from_thread``.
        self._thread_id: Optional[int] = None
        #: The startup meter: which background reads were queued, how many
        #: are done, and when the first started.  Empty means no startup
        #: work, and the meter stays hidden.
        self._startup_phases: List[str] = []
        self._startup_done = 0
        self._startup_t0 = 0.0
        self._startup_active = False
        self._startup_names_inflight = False
        self._startup_presets_inflight = False
        self._startup_text = ""
        #: The family's undo log. ``plan``/``settle`` are the doors used
        #: here: the re-send runs on a worker, so the entries leave the log
        #: as the plan is taken, on the main thread, and ``settle`` reports
        #: what actually landed.
        self._undo_log = UndoLog(
            apply_hook=self._undo_apply,
            gate_hook=self._undo_gate,
            status_hook=self._undo_status,
        )
        #: The rating filter, or ``None``. Applied on top of the favourites
        #: view rather than instead of it, so "3+ stars I have favourited"
        #: is one filter over another.
        self.rating_filter: RatingFilter | None = None
        #: Category codes to narrow to; empty means no narrowing. Applied on
        #: top of the favourites view rather than instead of it.
        self.categories: set = set()
        #: Restore the remembered browser state: which bank the left pane
        #: was on, which row the right pane's cursor was on, and where the
        #: cycling ``v`` view stood. Loaded here, not in ``on_mount``,
        #: because the first ``_fill`` down there builds its rows out of
        #: ``view_mode`` and ``_current_bank``; restoring anything later
        #: would mean filling twice. An injected path only -- ``None``
        #: means a test that asked for silence and gets it.
        self._saved_slot_cursor: Optional[int] = None
        #: The folder the last import file came from.  The picker opens
        #: here next time; the *file* is never remembered.
        self._import_folder: Optional[str] = None
        #: The row the last import file was picked from, so cancelling a
        #: preview returns to the same place in the browser.
        self._import_cursor: int = 0
        #: 0 = whole bank, 1 = single sounds; used when reopening the
        #: browser after a cancelled preview.
        self._import_kind: int = 0
        #: The modal that covers a long bank read or write.
        self._wait_screen: Optional["WaitScreen"] = None
        if self._config_path is not None:
            ui_state = cfg.load_ui_state(self._config_path)
            if "import_folder" in ui_state:
                self._import_folder = ui_state["import_folder"]
            if "bank_index" in ui_state:
                idx = ui_state["bank_index"]
                if 0 <= idx < len(self._bank_ids):
                    self._current_bank = self._bank_ids[idx]
                    self._saved_bank_index = idx
            if "slot_cursor" in ui_state:
                self._saved_slot_cursor = ui_state["slot_cursor"]
            else:
                self._saved_slot_cursor = None
            if "view_mode" in ui_state:
                vm = ui_state["view_mode"]
                if vm in VIEW_CYCLE:
                    self.view_mode = vm
            if "categories" in ui_state:
                self.categories = set(ui_state["categories"])
            if "rating_stars" in ui_state:
                self.rating_filter = RatingFilter(
                    stars=ui_state["rating_stars"],
                    at_least=bool(ui_state.get("rating_at_least", False)),
                )

    @property
    def _busy(self) -> bool:
        """Whether a MIDI exchange is pending or in flight.

        A property over the gate and the exchange, not a flag set beside
        the check: a ``@work(thread=True)`` method returns at once, so a
        boolean it set could only be set on its own thread, too late --
        which is the race two quick selections used to win.
        """
        return (
            self._busy_gate.check() is not None
            or self._busy_gate.is_busy()
            or self._exchange.locked()
        )

    @property
    def _gate(self) -> EditGate:
        """The write gate, and the only one there is.

        The flag is a starting state rather than a second gate: two
        gates would mean a caller could hold one the app does not
        check, and the screen's ``w`` and the app's ``w`` toggle the
        same object either way.
        """
        return self._write_gate

    def action_toggle_write_gate(self) -> None:
        """``w``: arm the write gate, or lock it again.

        The state is said out loud, because a locked gate that says
        nothing is a gate the user has to guess about.
        """
        self._gate.toggle()
        self.notify_status(self._gate.describe())

    def action_backup(self) -> None:
        """``b``: open the backup selection screen."""
        self.push_screen(
            BackupSelectionScreen(),
            self._handle_backup_selection,
        )

    def _handle_backup_selection(self, result: Optional[Dict[str, Any]]) -> None:
        """Handle the backup selection screen dismissal.

        ``result`` is ``None`` when the user cancels, or a dict with
        ``groups`` (set of group ids), ``content`` ("sounds"|"presets"|"all"),
        and ``backup_root`` when the user confirms.
        """
        if result is None:
            self.notify_status("backup cancelled", refused=True)
            return

        groups: set[str] = result["groups"]
        content: str = result["content"]
        backup_root: str = result.get("backup_root", cfg.DEFAULT_BACKUP_ROOT)

        self.notify_status(f"backup starting: groups={groups}, content={content}")

        # Run the backup in a worker thread
        self.run_worker(
            lambda: self._do_backup(groups, content, backup_root), thread=True
        )

    def _do_backup(self, groups: set[str], content: str, backup_root: str) -> None:  # noqa: C901
        """Perform the actual backup operation.

        This runs in a worker thread.  It uses the bridge's backup methods
        and writes the results to files in the backup directory.
        """
        import os
        import time

        # Ensure backup directory exists
        os.makedirs(backup_root, exist_ok=True)

        bridge = self.device  # type: ignore[attr-defined]
        if bridge is None:
            self._call_from_thread(
                self.notify_status, "no device connected", refused=True
            )
            return

        stamp = time.strftime("%Y%m%d-%H%M%S")
        successful = 0
        failed = 0

        try:
            int_done = False
            if (
                content == "all"
                and "INT" in groups
                and hasattr(bridge, "read_everything")
            ):
                try:
                    everything = bridge.read_everything()
                    filename = os.path.join(backup_root, f"everything_{stamp}.syx")
                    with open(filename, "wb") as f:
                        f.write(everything.wire())
                    successful += 1
                    int_done = True
                    self._call_from_thread(
                        self.notify_progress,
                        f"backed up everything (all sounds + all presets) to "
                        f"{filename} ({len(everything.wire())} bytes, "
                        f"one command-05 dump)",
                    )
                except Exception as exc:
                    failed += 1
                    self._call_from_thread(
                        self.notify_status,
                        f"full dump failed ({exc}); falling back to separate reads",
                        refused=True,
                    )
            if "INT" in groups and not int_done:
                if content in ("sounds", "all"):
                    # Backup internal sounds
                    data = bridge.backup_sounds("INT")
                    filename = os.path.join(backup_root, f"internal_sounds_{stamp}.bin")
                    with open(filename, "wb") as f:
                        f.write(data)
                    successful += 1
                    self._call_from_thread(
                        self.notify_progress,
                        f"backed up INT sounds to {filename} ({len(data)} bytes)",
                    )
                if content in ("presets", "all"):
                    # Backup internal presets
                    try:
                        data = bridge.backup_presets("INT")
                        filename = os.path.join(
                            backup_root, f"internal_presets_{stamp}.bin"
                        )
                        with open(filename, "wb") as f:
                            f.write(data)
                        successful += 1
                        self._call_from_thread(
                            self.notify_progress,
                            f"backed up INT presets to {filename} ({len(data)} bytes)",
                        )
                    except ValueError as e:
                        failed += 1
                        self._call_from_thread(
                            self.notify_status,
                            f"failed to backup INT presets: {e}",
                            refused=True,
                        )
            for group in groups:
                if group in ("CARDA", "CARDB"):
                    if content in ("sounds", "all"):
                        try:
                            data = bridge.backup_sounds(group)
                            filename = os.path.join(
                                backup_root, f"{group.lower()}_sounds_{stamp}.bin"
                            )
                            with open(filename, "wb") as f:
                                f.write(data)
                            successful += 1
                            self._call_from_thread(
                                self.notify_progress,
                                f"backed up {group} sounds to {filename} "
                                f"({len(data)} bytes)",
                            )
                        except (ValueError, Exception) as e:
                            failed += 1
                            self._call_from_thread(
                                self.notify_status,
                                f"failed to backup {group} sounds: {e}",
                                refused=True,
                            )
                    if content in ("presets", "all"):
                        try:
                            data = bridge.backup_presets(group)
                            filename = os.path.join(
                                backup_root, f"{group.lower()}_presets_{stamp}.bin"
                            )
                            with open(filename, "wb") as f:
                                f.write(data)
                            successful += 1
                            self._call_from_thread(
                                self.notify_progress,
                                f"backed up {group} presets to {filename} "
                                f"({len(data)} bytes)",
                            )
                        except ValueError as e:
                            failed += 1
                            self._call_from_thread(
                                self.notify_status,
                                f"failed to backup {group} presets: {e}",
                                refused=True,
                            )
        except Exception as e:
            failed += 1
            self._call_from_thread(
                self.notify_status, f"backup error: {e}", refused=True
            )

        # The app's local preset labels are user data too, and they are
        # not part of the unit's dump (the unit owns its own names).
        if content in ("presets", "all"):
            labels_source = preset_labels.default_path()
            if os.path.exists(labels_source):
                labels = preset_labels.load(labels_source)
                labels_file = os.path.join(backup_root, f"preset_labels_{stamp}.json")
                preset_labels.save(labels, labels_file)
                successful += 1
                self._call_from_thread(
                    self.notify_progress,
                    f"backed up {len(labels)} local preset label(s) to {labels_file}",
                )

        # Summary
        summary = f"backup complete: {successful} success, {failed} failed"
        self._call_from_thread(self.notify_status, summary)

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="startup", classes="hidden"):
            yield Static("startup", id="startup-label")
            yield ProgressBar(
                total=None,
                show_percentage=False,
                show_eta=False,
                id="startup-bar",
            )
        with Horizontal(id="panes"):
            yield DataTable(id="bank-table", cursor_type="row")
            yield DataTable(id="slot-table", cursor_type="row")
        yield Static("", id="detail")
        yield Static("", id="status")
        # KEY_HINTS passed explicitly: the shared widget defaults to the
        # canonical family legend, and this tool's own hints are its
        # business, not the library's.
        yield KeyHints(KEY_HINTS)

    def on_mount(self) -> None:
        self._thread_id = threading.get_ident()
        self.title = "ensqsqed"
        table = self.query_one("#bank-table", DataTable)
        for label, key in (("group", "bank"), ("n", "n"), ("src", "src"), ("♥", "fav")):
            table.add_column(label, key=key)
        for bank_id in self._bank_ids:
            entry = banks.group(bank_id)
            marked = len(self._favorite_keys(bank_id))
            table.add_row(
                entry.label,
                str(entry.count),
                _group_source(entry),
                # blank rather than "0": a column of zeroes is
                # noise, and the eye wants the few that differ
                str(marked) if marked else "",
                key=bank_id,
            )

        slots = self.query_one("#slot-table", DataTable)
        # The favourite marker leads the row rather than trailing it: it is
        # a gutter mark the eye runs down, and gluing it to the name would
        # carry it into anything that sorts or searches.
        # The rating column is pinned to five rather than fitted to its
        # content: fitted, it is five wide in a bank you have rated and one
        # wide in a bank you have not, so the same column changes shape
        # depending on data that has nothing to do with it -- and the row
        # you just rated would jump sideways as it appeared.
        #
        # ``#`` is the slot's position in its group, counted from 1
        # (the bank index the cursor moves by); it is NOT the program
        # change. ``PC`` is the actual MIDI program change the unit
        # sends (00-79 for sounds, 80-99 for drum kits) -- the number
        # the unit's own display prints. ``sel`` is this instrument's
        # answer to the siblings' LSB/MSB columns: the SQ-R has no Bank
        # Select, so the other number that selects a sound is the group
        # selector (124-127). A drum kit has no selector -- its program
        # change is absolute -- and shows "—".
        for label, key, width in (
            ("♥", "fav", None),
            ("#", "num", None),
            ("name", "name", None),
            ("★", "rating", 5),
            ("sel", "sel", None),
            ("PC", "pc", None),
        ):
            slots.add_column(label, key=key, width=width)

        # Names read on a previous run, applied before the first fill so a
        # bank that has been read before opens on its own names rather than
        # on blanks for one frame. Best-effort: a missing or corrupt
        # sidecar reads as an empty one.
        applied = 0
        if self._live_names_path:
            from sq import livenames

            applied = livenames.apply_to(self.catalog, self._live_names_path)

        self._fill(self._current_bank)
        table.focus()
        self._load_preset_cache()
        self._startup_begin()
        if "Internal names" in self._startup_phases:
            # The sidecar above paints the first frame, but it can be
            # stale: the panel, or an import, changes the bank without
            # this program knowing.  Check it once in the background when
            # it is old (or missing); a fresh one costs no gap.  `r` still
            # refreshes on demand.
            self.set_timer(0.3, self._startup_read_names)

        if "the 80 presets" in self._startup_phases:
            # The preset bank is a twelve-second read.  Warm the cache in
            # the background so `p` opens at once; the worker waits for any
            # other exchange (the names read) to finish before it starts.
            self.set_timer(1.0, self._startup_read_presets)

        printed = len(self.catalog)
        if printed:
            self.notify_status(f"{printed} names from the catalog")
        elif applied:
            self.notify_status(f"{applied} names from the unit (read last time)")
        else:
            self.notify_status(
                "no catalog -- reading the Internal bank's names from the "
                "unit; ROM, Card A and Card B have no dump request"
            )

    # --- filling ------------------------------------------------------------

    def _favorite_keys(self, bank_id: str) -> set:
        if self.favorites is None:
            return set()
        return self.favorites.keys_for_bank(bank_id)

    def _all_favourites(self) -> List[banks.Slot]:
        """Every favourite in every bank, in bank order then number.

        Built from the slots rather than from the favourites rows, so a row
        naming a bank this build does not know is skipped rather than
        breaking the list.
        """
        if self.favorites is None:
            return []
        wanted = {}
        for favourite in self.favorites.all(order="bank"):
            wanted.setdefault(favourite.bank_id, set()).add(favourite.number)
        out = []
        for bank_id in banks.group_ids():
            numbers = wanted.get(bank_id)
            if not numbers:
                continue
            out.extend(e for e in banks.slots(bank_id) if e.number in numbers)
        return out

    def _visible(self, bank_id: str) -> List[banks.Slot]:
        if self.view_mode == VIEW_ALL_FAVOURITES:
            entries = self._all_favourites()
        else:
            entries = banks.slots(bank_id)
            if self.view_mode == VIEW_BANK_FAVOURITES:
                marked = self._favorite_keys(bank_id)
                entries = [e for e in entries if e.number in marked]
        return self._by_rating(self._by_category(entries))

    def _by_rating(self, entries: List[banks.Slot]) -> List[banks.Slot]:
        """Keep the slots whose stored rating matches the filter.

        A slot nobody has favourited has rating 0, which is why the
        ``at_least`` form needs no special case: "at least 1" already
        excludes the unrated ones. ``stars == 0`` is the family's
        spelling of "any", though, so it disables the filter whatever
        ``at_least`` says -- and that is also the value the
        rating-filter dialog produces when it is cleared.
        """
        flt = self.rating_filter
        if flt is None or flt.stars == 0 or self.favorites is None:
            return entries
        out: List[banks.Slot] = []
        for entry in entries:
            favourite = self.favorites.get(entry.bank_id, entry.number)
            rating = favourite.rating if favourite is not None else 0
            if (rating >= flt.stars) if flt.at_least else (rating == flt.stars):
                out.append(entry)
        return out

    def _by_category(self, entries: List[banks.Slot]) -> List[banks.Slot]:
        """Narrow to the selected categories. A no-op when none are chosen."""
        if not self.categories:
            return entries
        return [
            e
            for e in entries
            if (entry := self.catalog.entry(e.bank_id, e.number)) is not None
            and entry.category in self.categories
        ]

    def _cursor_entry(self):
        """The row under the cursor, or None."""
        table = self.query_one("#slot-table", DataTable)
        row = table.cursor_row
        if not self._current or row is None or row >= len(self._current):
            return None
        return self._current[row]

    # --- the send channel ---------------------------------------------------

    def _channel(self) -> int:
        return getattr(self.device, "channel", 0)

    def _set_channel(self, channel: int) -> None:
        """Change the channel selections are sent on, and remember it."""
        channel = max(0, min(15, channel))
        try:
            self.device.channel = channel
        except Exception:
            self.notify_status("this device has no settable channel", refused=True)
            return
        self._remember_channel(channel)
        self._update_subtitle()
        self.notify_status(f"sending on MIDI channel {channel + 1}")

    def _remember_channel(self, channel: int) -> None:
        """Save it for next time. Only OSError is swallowed."""
        path = self._config_path
        if path is None:
            return
        try:
            cfg.save_channel(channel, path)
        except OSError:
            pass

    # --- status -------------------------------------------------------------

    def notify_status(
        self, message: str, *, refused: bool = False, success: bool = False
    ) -> None:
        """Say the last thing; a refusal goes red and bold.

        The family's shape (rxved): the widget carries a ``refused`` class
        rather than a text prefix, so a refusal is a red bar the eye cannot
        miss instead of another grey status line.  ``success`` is its green
        counterpart, used for a write that was verified by a read-back.
        """
        self._last_status_refused = refused
        status = self.query_one("#status", Static)
        status.update(message)
        status.set_class(refused, "refused")
        status.set_class(success and not refused, "ok")

    def notify_progress(self, message: str) -> None:
        """Report a background worker's running total, which is not news.

        A tick must not overwrite a refusal: a scan posts one every few
        slots, and a refusal the user keyed in should stay readable.
        """
        if getattr(self, "_last_status_refused", False):
            return
        self.notify_status(message)

    def _call_from_thread(self, callback, *args, **kwargs) -> None:
        """Call ``callback`` on the app's UI thread, from any thread.

        ``call_from_thread`` refuses to run when the caller is already on
        the UI thread, which makes a worker method impossible to invoke
        directly (as tests and ``_do_backup``'s callers do).  Fall back to
        a direct call in that case.
        """
        import threading

        if getattr(self, "_thread_id", None) == threading.get_ident():
            callback(*args, **kwargs)
        else:
            self.call_from_thread(callback, *args, **kwargs)

    def _update_subtitle(self) -> None:
        """Say in the header which pane this is and which channel sends on.

        The family puts the same line in the Header's subtitle, and for the
        same reason: the send channel is the one piece of state that is
        always in play and never asked about. ``[`` and ``]`` move it
        silently, and a Bank Select plus Program Change sent on the wrong
        channel is silent too -- the unit just never hears it, or hears it
        on a channel nobody is listening to. Showing it makes both visible
        at a glance.

        The count carries its denominator -- "12" alone reads as a small
        bank, "12 of 128" reads as part of one -- which is what answers
        "why is this list shorter than I remember" after a restart that
        brought a rating filter back.
        """
        entry = banks.group(self._current_bank)
        shown = len(self._current)
        # The denominator is the same view with the rating filter lifted:
        # the filter is the only thing that can quietly take rows away, so
        # the two numbers say exactly how many it took.
        saved = self.rating_filter
        self.rating_filter = None
        try:
            total = len(self._visible(self._current_bank))
        finally:
            self.rating_filter = saved
        counted = f"{shown} of {total}" if shown != total else f"{shown}"

        if self.view_mode == VIEW_ALL_FAVOURITES:
            what = f"every favourite — {counted}"
        elif self.view_mode == VIEW_BANK_FAVOURITES:
            plural = "" if shown == 1 else "s"
            what = f"{entry.label} — {counted} favourite{plural}"
        else:
            plural = "" if shown == 1 else "s"
            what = f"{entry.label} — {counted} sound{plural}"

        filters = ""
        if self.rating_filter is not None:
            stars = "★" * self.rating_filter.stars or "any"
            sign = "≥" if self.rating_filter.at_least else "="
            filters = f"{stars} {sign}   "
        if self.categories:
            filters += "/".join(sorted(self.categories)) + "   "
        editing = ""
        if self._preset_loaded and self._edit_preset is not None:
            # The preset the editors are working on is named here, so
            # that the browser says which preset an edit would change.
            name = self._edit_preset.name.strip() or "(no name)"
            editing = f"   ·   editing “{_plain(name)}”"
        self.sub_title = (
            f"{filters}{what}   ·   ch {self._channel() + 1}"
            f"   ·   mode "
            f"{self._remote_mode.title() if self._remote_mode_confirmed else '?'}"
            f"{editing}"
        )

    # --- navigation ---------------------------------------------------------

    def on_data_table_row_highlighted(self, event) -> None:
        if self._filling:
            return
        if event.data_table.id == "bank-table":
            row = event.cursor_row
            if 0 <= row < len(self._bank_ids):
                bank_id = self._bank_ids[row]
                # Idempotent on purpose. ``_filling`` cannot guard this --
                # the event is delivered after the flag is cleared -- so
                # the handler has to be safe to receive for the bank it
                # is already showing. Moving the cursor never triggers it
                # for a row already shown, and a stale queued event for a
                # bank that was showing a moment ago must not refill the
                # pane over a newer fill.
                if bank_id != self._current_bank:
                    self._fill(bank_id)
                    self._save_ui_state(bank_index=row)
        elif event.data_table.id == "slot-table":
            self._update_detail(event.cursor_row)
            # Only save the slot cursor when the slot table is focused --
            # the user is actively navigating. Programmatic cursor moves
            # during startup (while the bank pane has focus) must not
            # overwrite the saved position they are busy restoring.
            if event.data_table.has_focus:
                self._save_ui_state(slot_cursor=event.cursor_row)

    def on_data_table_row_selected(self, event) -> None:
        """Enter on a slot. It arrives here, not as a binding."""
        if event.data_table.id == "slot-table":
            self.action_select()

    def action_switch_pane(self) -> None:
        left = self.query_one("#bank-table", DataTable)
        right = self.query_one("#slot-table", DataTable)
        (right if left.has_focus else left).focus()

    # --- operations ---------------------------------------------------------

    def action_select(self) -> None:
        table = self.query_one("#slot-table", DataTable)
        row = table.cursor_row
        if not 0 <= row < len(self._current):
            return
        if self._busy:
            self.notify_status("busy", refused=True)
            return
        # The exchange is claimed here, on the main thread, before any
        # worker exists: a @work(thread=True) method returns at once, so
        # two quick presses both passed every check run on their own
        # thread -- two Program Changes from one intent, and for `r` two
        # bulk requests back-to-back, which is the traffic that draws
        # "data format error" from this unit. The worker releases the
        # exchange in a ``finally``; entering the busy gate is what marks
        # the exchange in progress for an idle close.
        if not self._exchange.acquire(blocking=False):
            self.notify_status("busy", refused=True)
            return
        self._select_worker(self._current[row])

    @work(thread=True)
    def _select_worker(self, entry: banks.Slot) -> None:
        """**MIDI only.** The caller claimed the exchange; this frees it."""
        failure: BaseException | None = None
        try:
            with self._busy_gate, self._lock:
                self.device.select(entry)
        except _BRIDGE_ERRORS as exc:
            failure = exc
        finally:
            self._exchange.release()
        if failure is not None:
            msg = f"select: {failure}"
            self._call_from_thread(self.notify_status, msg, refused=True)
            return
        self._call_from_thread(
            self.notify_status,
            f"selected {entry.display} — "
            + (
                f"program change {entry.group.selector} for the group, then "
                f"{entry.program_change}"
                if entry.needs_selector
                else f"program change {entry.program_change}, absolute"
            )
            + f", on channel {self.device.channel + 1}",
        )

    def action_read_names(self) -> None:
        """Read the highlighted bank's names off the synth.

        Internal has a bank dump and is genuinely read-only: nothing is
        selected.  A group without a dump is read one sound at a time, so
        that path asks first, exactly like `S` (it changes what the unit
        is playing while it runs).

        Named ``read_names`` because that is what the family calls this key:
        ``r`` means *re-read from the device*, and it was bound to
        ``read_bank`` here, which said the same thing in a way no other tool
        in the family used.
        """
        if self._scanning:
            self.notify_status(
                "a scan is already running; press S to stop it", refused=True
            )
            return
        if self._busy:
            self.notify_status("busy", refused=True)
            return
        entry = banks.group(self._current_bank)
        if not entry.readable:
            # No bank dump, but the group can still be read the slow way:
            # select each sound, read its current-sound record back.  That
            # is exactly what `S` does for every such group; `r` does it
            # for the group on screen, and asks first because it changes
            # what the unit is playing while it runs.
            def start(confirmed: bool | None) -> None:
                if not confirmed:
                    return
                self._start_scan([entry.id])

            self.push_screen(
                ConfirmScreen(
                    f"Read {entry.label}'s names?\n\n"
                    f"{entry.label} has no bank dump request, so each sound "
                    "is selected in turn and its current-sound record is read "
                    "back. That changes what the unit is playing while it "
                    "runs, and takes a minute or two for a full group. The "
                    "write gate is not involved: nothing is stored.\n\n"
                    "Press S to stop early.",
                    confirm="Scan",
                    cancel="Cancel",
                ),
                start,
            )
            return
        # Main thread; see action_select for why it is claimed here.
        if not self._exchange.acquire(blocking=False):
            self.notify_status("busy", refused=True)
            return
        self._read_worker(self._current_bank)

    # --- the startup meter ---------------------------------------------------

    def _load_preset_cache(self) -> None:
        """Open on the last all-presets dump, if there is one.

        The dump is twelve seconds on the wire; a cache means ``p`` is
        instant and the instrument is only asked again when the cache is
        old.  A cache that cannot be parsed is ignored, not fatal.
        """
        if self._all_dump is not None:
            return
        if not self._read_names_at_startup:
            # Without the startup reads the app behaves as it did before
            # the cache existed: `p` reads on demand.  This also keeps a
            # test's fake device from being overridden by a real cache.
            return
        data = preset_cache.load()
        if not data:
            return
        try:
            alert = PresetAlert(
                command=msg.CommandType.ALL_SEQUENCE_ALERT, size=0, count=80
            )
            self._all_dump = AllPresetsDump._from_reply(data, alert)
            self._all_dump_at = time.monotonic()
        except (DeviceError, ValueError, IndexError):
            self._all_dump = None

    def _startup_stale(self, path: Optional[str]) -> bool:
        """Whether a sidecar is missing or older than the check interval."""
        if not path:
            return True
        try:
            return (time.time() - os.path.getmtime(path)) > STARTUP_CHECK_TTL
        except OSError:
            return True

    def _startup_begin(self) -> None:
        """Queue the startup checks that are actually needed, and show why.

        A fresh sidecar/cache means no MIDI read at all, so the app has no
        "busy" gap: the cached data is already on screen.  Only a missing
        or stale cache is checked, and the meter says what is running.
        """
        phases: List[str] = []
        if (
            self._read_names_at_startup
            and hasattr(self.device, "read_names")
            and self._startup_stale(self._live_names_path)
        ):
            phases.append("Internal names")
        if (
            self._read_names_at_startup
            and hasattr(self.device, "read_all_presets")
            and (
                self._all_dump is None
                or (preset_cache.age_seconds() or 0) > STARTUP_CHECK_TTL
            )
        ):
            phases.append("the 80 presets")
        self._startup_phases = phases
        self._startup_done = 0
        self._startup_t0 = time.monotonic()
        self._startup_active = bool(phases)
        if not phases:
            return
        self._startup_text = "waiting for the unit"
        self._show_startup(self._startup_text)
        self.set_interval(0.5, self._tick_startup)

    def _show_startup(self, text: str) -> None:
        """Show the meter with this phase text, if the app is mounted."""
        try:
            self.query_one("#startup", Horizontal).remove_class("hidden")
            label = self.query_one("#startup-label", Static)
            label.update(f"startup: {text}")
            label.set_class(True, "waiting")
            label.set_class(False, "ready")
        except Exception:  # pragma: no cover - called before compose
            return

    def _tick_startup(self) -> None:
        """Refresh the elapsed seconds while a startup read is running."""
        if not self._startup_active:
            return
        elapsed = time.monotonic() - self._startup_t0
        try:
            status = self.query_one("#startup-label", Static)
            status.update(f"startup: {self._startup_text} — {elapsed:.0f}s")
        except Exception:  # pragma: no cover - the app closed under us
            return

    def _startup_step(self, text: str) -> None:
        """Name the phase that just started and refresh the meter."""
        self._startup_text = text
        self._show_startup(text)
        self._tick_startup()

    def _startup_step_done(self) -> None:
        """One phase finished.  Main thread."""
        if not self._startup_active:
            return
        self._startup_done += 1
        if self._startup_done >= len(self._startup_phases):
            self._startup_text = "ready"
            self._tick_startup()
            try:
                label = self.query_one("#startup-label", Static)
            except Exception:  # pragma: no cover - the app closed under us
                label = None
            if label is not None:
                label.set_class(False, "waiting")
                label.set_class(True, "ready")
            self._startup_active = False
            self.set_timer(2.0, self._hide_startup)
            return
        self._startup_step(
            f"waiting for the next read "
            f"({self._startup_done}/{len(self._startup_phases)} done)"
        )

    def _hide_startup(self) -> None:
        """Hide the meter once the reads have finished."""
        try:
            self.query_one("#startup", Horizontal).add_class("hidden")
        except Exception:  # pragma: no cover - the app closed under us
            return

    def _startup_phase_text(self, extra: str = "") -> str:
        """The current phase, e.g. ``reading Internal names (step 1 of 2)``."""
        index = min(self._startup_done, max(0, len(self._startup_phases) - 1))
        name = self._startup_phases[index] if self._startup_phases else ""
        step = (
            f" (step {self._startup_done + 1} of {len(self._startup_phases)})"
            if self._startup_phases
            else ""
        )
        return f"reading {name}{step}{extra}"

    def _startup_read_names(self, attempt: int = 0) -> None:
        """Re-read the Internal bank once, quietly, after the first frame.

        The same silent request `r` makes: one Internal Sound Banks Dump
        Request, no program change. It runs because a fresh install has no
        catalog and a blank name column reads as a broken program; the unit
        can name four fifths of what it holds, so it is asked. Failure is
        silent here -- the user did not press anything -- but a later `r`
        still reports it.
        """
        if self._busy or not self._exchange.acquire(blocking=False):
            # A user action or the other startup read won the exchange.
            # Try again shortly; the meter already says startup is running.
            if attempt < 60:
                self.set_timer(0.5, lambda: self._startup_read_names(attempt + 1))
            else:
                self._startup_step_done()
            return
        self._startup_names_inflight = True
        self._startup_step(self._startup_phase_text(" (about 10 s)"))
        self._read_worker("INT")

    @work(thread=True)
    def _read_worker(self, bank_id: str) -> None:
        """**MIDI only** -- the catalog is updated on the main thread.

        One request, one 32647-byte reply, eighty sounds with their names
        inside it. The caller claimed the exchange; this frees it.
        """
        failure: BaseException | None = None
        names: Dict[int, str] | None = None
        try:
            with self._busy_gate, self._lock:
                names = self.device.read_names()
        except _BRIDGE_ERRORS as exc:
            failure = exc
        finally:
            self._exchange.release()
        if self._startup_names_inflight:
            self._startup_names_inflight = False
            self._call_from_thread(self._startup_step_done)
        if failure is not None:
            self._call_from_thread(self.notify_status, f"read: {failure}", refused=True)
            return
        if names is None:
            return
        self._call_from_thread(self._adopt_internal, names)

    def _adopt_internal(self, names: Dict[int, str]) -> None:
        for number, text in names.items():
            # The dump numbers sounds from 0; the unit shows them from 1,
            # and the catalog keys on what the unit shows.
            self.catalog.set_live_name("INT", number + 1, text.rstrip())
        # Persist what the unit just said, keyed as the catalog keys it, so
        # the next launch opens on these names before it asks anything.
        self._save_live_names(
            "INT", {number + 1: text.rstrip() for number, text in names.items()}
        )
        if self.favorites is not None:
            self.favorites.refresh_names(
                lambda bank, number: self.catalog.live_name(bank, number)
            )
        if self._current_bank == "INT":
            self._fill("INT")
        differing = sum(
            1 for number in names if self.catalog.differs("INT", number + 1)
        )
        self.notify_status(
            f"read {len(names)} internal name(s)"
            + (f"; {differing} differ from the catalog" if differing else "")
        )

    def _save_live_names(self, bank_id: str, names: Dict[int, str]) -> None:
        """Persist one bank's hardware-read names, if we have somewhere to.

        ``live_names_path`` is resolved by ``main()`` and injected, rather
        than defaulted here, for the reason ``backup_dir`` is: a path this
        class looks up for itself is a path a test can write to by accident.
        None meaning "do not persist" makes that impossible.
        """
        if not self._live_names_path:
            return
        from sq import livenames

        livenames.save(
            {livenames.key(bank_id, n): name for n, name in names.items()},
            self._live_names_path,
        )

    def _fill(self, bank_id: str, cursor: int = 0) -> None:
        table = self.query_one("#slot-table", DataTable)
        self._current_bank = bank_id
        self._current = self._visible(bank_id)
        marked = self._favorite_keys(bank_id)
        # One query for the heart set and the ratings together, rather
        # than a `keys_for_bank()` and a `get()` per row: a bank is over a
        # hundred rows, and the store is behind a lock. Membership in the
        # whole set is also the right question in the all-favourites view,
        # where the rows come from every bank while the pane sits on one.
        everything = self.favorites.all() if self.favorites is not None else []
        ratings = {favourite.key: favourite.rating for favourite in everything}
        self._filling = True
        try:
            table.clear()
            for entry in self._current:
                name = self.catalog.display_name(entry.bank_id, entry.number)
                if not name:
                    # A group the unit cannot dump (ROM, Card A/B) has no
                    # name until a catalog supplies one. Say so rather than
                    # leaving a blank cell that reads as a broken row.
                    shown = "[dim](no name)[/dim]"
                elif self.catalog.differs(entry.bank_id, entry.number):
                    shown = f"[b]{name}[/b] [dim]*[/dim]"
                elif self.catalog.is_live(entry.bank_id, entry.number):
                    shown = f"[b]{name}[/b]"
                else:
                    shown = name
                here = (
                    self._favorite_keys(entry.bank_id)
                    if self.view_mode == VIEW_ALL_FAVOURITES
                    else marked
                )
                table.add_row(
                    "[b]♥[/b]" if entry.number in here else "",
                    f"{entry.number:03d}",
                    shown,
                    rating_cell(ratings.get(entry.key, 0)),
                    # This instrument's answer to LSB/MSB: the group
                    # selector is the only other number a program change
                    # needs, and a drum kit has none.
                    "—" if entry.group.selector is None else str(entry.group.selector),
                    f"{entry.program_change:02d}",
                    key=entry.key,
                )
        finally:
            self._filling = False
        if self._current:
            table.move_cursor(row=min(cursor, len(self._current) - 1))
        self._update_detail(table.cursor_row)
        self._update_subtitle()

    def _update_detail(self, row: int) -> None:
        if not self._current or row >= len(self._current):
            self.query_one("#detail", Static).update("")
            return
        entry = self._current[row]
        name = self.catalog.display_name(entry.bank_id, entry.number)
        if not name:
            name = "[dim](no name)[/dim]"
        source = (
            "read from the unit"
            if self.catalog.is_live(entry.bank_id, entry.number)
            else (
                "from the catalog"
                if entry.group.readable
                else "from the catalog -- this group cannot be read"
            )
        )
        if entry.needs_selector:
            how = (
                f"program change [b]{entry.group.selector}[/b] selects "
                f"the {entry.group.label} group, then "
                f"[b]{entry.program_change}[/b] the sound"
            )
        else:
            how = (
                f"program change [b]{entry.program_change}[/b], "
                f"[dim]absolute -- a drum kit needs no group[/dim]"
            )
        fav = (
            self.favorites.get(entry.bank_id, entry.number) if self.favorites else None
        )
        lines = [
            f"[b]{entry.display}[/b]  {name}   [dim]bank {entry.bank}[/dim]",
            f"{how} · {source}",
        ]
        if fav is not None:
            bits = ["favourite"]
            if fav.rating:
                bits.append("★" * fav.rating)
            if fav.tags:
                bits.append(f"tags: {fav.tags}")
            if fav.note:
                bits.append(fav.note)
            lines.append("[b]" + "  |  ".join(bits) + "[/b]")
        self.query_one("#detail", Static).update("\n".join(lines))

    def _refill_keeping_place(self) -> None:
        table = self.query_one("#slot-table", DataTable)
        row = table.cursor_row or 0
        self._fill(self._current_bank)
        if self._current:
            table.move_cursor(row=min(row, len(self._current) - 1))

    def _save_ui_state(  # noqa: C901
        self,
        *,
        bank_index: Optional[int] = None,
        slot_cursor: Optional[int] = None,
        view_mode: Optional[str] = None,
        categories: Optional[list] = None,
        rating_stars: Optional[int] = None,
        rating_at_least: Optional[bool] = None,
        import_folder: Optional[str] = None,
    ) -> None:
        """Persist UI state (bank position, slot cursor, view mode, categories).

        Only called when ``_config_path`` is set. ``None`` on a rating key
        removes it, which is how clearing a filter stops it coming back
        next launch. Both states have to exist, and only one of them is
        ``None``.
        """
        if self._config_path is None:
            return
        try:
            changes: Dict[str, Any] = {}
            if bank_index is not None:
                changes["bank_index"] = bank_index
            if slot_cursor is not None:
                changes["slot_cursor"] = slot_cursor
            if view_mode is not None:
                changes["view_mode"] = view_mode
            if categories is not None:
                changes["categories"] = categories
            if rating_stars is not None:
                changes["rating_stars"] = rating_stars
            if rating_at_least is not None:
                changes["rating_at_least"] = rating_at_least
            if import_folder is not None:
                changes["import_folder"] = import_folder
            if changes:
                cfg.save_ui_state(self._config_path, **changes)
        except OSError:
            pass

    # --- channel actions ----------------------------------------------------

    def action_channel_up(self) -> None:
        self._set_channel(self._channel() + 1)

    def action_channel_down(self) -> None:
        self._set_channel(self._channel() - 1)

    def action_pick_channel(self) -> None:
        def apply(text):
            if not text:
                return
            try:
                value = int(text)
            except ValueError:
                self.notify_status(f"{text!r} is not a number", refused=True)
                return
            if not 1 <= value <= 16:
                self.notify_status("MIDI channels are 1-16", refused=True)
                return
            self._set_channel(value - 1)

        self.push_screen(
            PromptScreen("MIDI channel (1-16):", str(self._channel() + 1)), apply
        )

    # --- favourite actions --------------------------------------------------

    def action_toggle_favorite(self) -> None:
        """Mark or unmark the slot under the cursor. Sends no MIDI."""
        if self.favorites is None:
            self.notify_status("no favourites database")
            return
        table = self.query_one("#slot-table", DataTable)
        row = table.cursor_row
        if not self._current or row is None or row >= len(self._current):
            return
        entry = self._current[row]
        # entry.bank_id, not self._current_bank. In the all-favourites view
        # the rows come from every bank while the bank pane sits on one of
        # them, so using the pane's bank favourited a slot the user was not
        # looking at and left the one they were. _fill has always read the
        # row's own bank; these did not.
        bank_id = entry.bank_id
        name = self.catalog.display_name(bank_id, entry.number)
        # Favorites.toggle, not remove-then-add: un-favouriting keeps
        # the row so a rating, tags, a note and the original date
        # survive being toggled off and on. As a delete followed by a
        # fresh add this destroyed all four, and this is one keystroke.
        was = self.favorites.get(bank_id, entry.number) is not None
        now = self.favorites.toggle(bank_id, entry.number, name=name)
        self._undo_log.push(
            Record(
                "favourite",
                {"bank_id": bank_id, "number": entry.number, "was": was, "name": name},
            )
        )
        self.notify_status(f"{'favourited' if now else 'unfavourited'} {name}")
        self._refill_keeping_place()

    def _favourite_under_cursor(self):
        """The favourite row for the slot under the cursor, if there is one.

        Tags and notes hang off a favourite, so there is nothing to attach
        them to until `f` has been pressed. Saying so is better than
        silently creating one: the user asked to annotate, not to favourite.
        """
        entry = self._cursor_entry()
        if entry is None or self.favorites is None:
            return None, None
        # The row's bank, for the reason action_toggle_favorite gives: in
        # the all-favourites view a tag or a note written against the bank
        # pane's bank lands on a different slot than the one on screen.
        bank_id = entry.bank_id
        number = entry.number
        return (bank_id, number), self.favorites.get(bank_id, number)

    def action_edit_tags(self) -> None:
        where, existing = self._favourite_under_cursor()
        if where is None:
            return
        if existing is None:
            self.notify_status("not a favourite yet -- press f first", refused=True)
            return

        def apply(value):
            if value is None:
                return
            old = existing.tags
            self.favorites.set_tags(where[0], where[1], value)
            self._undo_log.push(
                Record(
                    "tags",
                    {"bank_id": where[0], "number": where[1], "old": old, "new": value},
                )
            )
            self._refill_keeping_place()
            self.notify_status("tags set")

        self.push_screen(PromptScreen("Tags (comma separated):", existing.tags), apply)

    def action_edit_note(self) -> None:
        where, existing = self._favourite_under_cursor()
        if where is None:
            return
        if existing is None:
            self.notify_status("not a favourite yet -- press f first", refused=True)
            return

        def apply(value):
            if value is None:
                return
            old = existing.note
            self.favorites.set_note(where[0], where[1], value)
            self._undo_log.push(
                Record(
                    "note",
                    {"bank_id": where[0], "number": where[1], "old": old, "new": value},
                )
            )
            self._refill_keeping_place()
            self.notify_status("note set")

        self.push_screen(PromptScreen("Note:", existing.note), apply)

    def action_edit_rating(self) -> None:
        """Rate the favourite under the cursor, 0 to 5."""
        if self.favorites is None:
            self.notify_status("no favourites database")
            return
        entry = self._cursor_entry()
        if entry is None:
            self._refuse_no_slot("rate")
            return
        existing = self.favorites.get(entry.bank_id, entry.number)
        if existing is None:
            self._refuse_no_favourite(entry, "*")
            return
        options = ["*" * stars or "no rating" for stars in range(6)]
        self.push_screen(
            ChoicePicker(f"Rating for {entry.display}", options, existing.rating),
            lambda value: self._apply_rating(entry, existing, value),
        )

    def _apply_rating(self, entry: banks.Slot, existing: Favorite, value) -> None:
        if value is None:
            return
        old = existing.rating
        self.favorites.set_rating(entry.bank_id, entry.number, value)
        self._undo_log.push(
            Record(
                "rating",
                {
                    "bank_id": entry.bank_id,
                    "number": entry.number,
                    "old": old,
                    "new": value,
                },
            )
        )
        table = self.query_one("#slot-table", DataTable)
        if not self._still_shown(entry):
            self._relist_without(entry, table)
            self._update_subtitle()
            msg = (
                f"{entry.display} rated {value} — no longer matches the filter"
                if value
                else f"{entry.display} rating cleared — no longer matches the filter"
            )
            self.notify_status(msg)
            return
        try:
            table.update_cell(entry.key, "rating", rating_cell(value))
        except (KeyError, ValueError, LookupError, AttributeError):
            self._refresh_current_bank()
        self._update_detail(table.cursor_row)
        if value:
            msg = f"{entry.display} rated {value}"
        else:
            msg = f"{entry.display} rating cleared"
        self.notify_status(msg)

    def _still_shown(self, slot: banks.Slot) -> bool:
        """Whether a slot would survive all filters right now."""
        return any(s.key == slot.key for s in self._visible(slot.bank_id))

    def _relist_without(self, slot: banks.Slot, table) -> None:
        """Rebuild after a row stopped matching, holding the cursor put."""
        row = table.cursor_row
        self._fill(self._current_bank)
        remaining = len(self._current)
        if remaining:
            table.move_cursor(row=min(row, remaining - 1))
        self._update_detail(table.cursor_row)

    def _refuse_no_slot(self, what: str) -> None:
        """Refuse an action when there's no slot under the cursor."""
        self.notify_status(
            f"nothing under the cursor to {what} -- this view has no slots; "
            f"press v to step to another view",
            refused=True,
        )

    def _refuse_no_favourite(self, entry: banks.Slot, key: str) -> None:
        """Refuse an action when the slot isn't a favourite yet."""
        self.notify_status(
            f"{entry.display} is not a favourite yet -- {key} writes to a favourite, "
            f"so press f first and {key} again",
            refused=True,
        )

    def _refresh_current_bank(self) -> None:
        """Rebuild the slot table in place, keeping the cursor."""
        table = self.query_one("#slot-table", DataTable)
        self._fill(self._current_bank, cursor=table.cursor_row)

    def action_rating_filter(self) -> None:
        """Choose which star ratings the slot pane shows. Sends no MIDI."""
        if self.favorites is None:
            self.notify_status("no favourites database")
            return
        current = self.rating_filter or RatingFilter(stars=0, at_least=False)

        def apply(value: RatingFilter | None) -> None:
            if value is None:
                return
            if value == current:
                return
            self.rating_filter = value
            self._fill(self._current_bank)
            self.query_one("#slot-table", DataTable).focus()
            shown = len(self._current)
            if value.stars == 0 and value.at_least:
                self.rating_filter = None
                self.notify_status(f"rating filter cleared — {shown} rows")
                self._save_ui_state(rating_stars=None, rating_at_least=None)
            else:
                how = "at least" if value.at_least else "exactly"
                msg = f"rating filter: {how} {value.stars} star(s) — {shown} rows"
                self.notify_status(msg)
                self._save_ui_state(
                    rating_stars=value.stars, rating_at_least=value.at_least
                )

        self.push_screen(RatingFilterScreen(current), apply)

    def action_pick_categories(self) -> None:
        """Narrow the current list by category. Stacks on the current view."""
        # Offered over the *unfiltered* rows of the current view, so the
        # picker still lists a category after you have filtered it away --
        # otherwise the only way back would be to clear the filter blind.
        saved, self.categories = self.categories, set()
        try:
            rows = self._visible(self._current_bank)
        finally:
            self.categories = saved
        counts = self.catalog.categories_in([(s.bank_id, s.number) for s in rows])
        if not counts:
            self.notify_status("nothing to categorise here", refused=True)
            return

        def apply(chosen) -> None:
            if chosen is None:
                return
            self.categories = set(chosen)
            self._fill(self._current_bank)
            self.query_one("#slot-table", DataTable).focus()
            shown = len(self._current)
            if not self.categories:
                self.notify_status(f"category filter cleared — {shown} rows")
                self._save_ui_state(categories=[])
            else:
                self.notify_status(
                    f"{'/'.join(sorted(self.categories))} — {shown} of {len(rows)} rows"
                )
                self._save_ui_state(categories=list(self.categories))

        self.push_screen(CategoryScreen("Categories", counts, self.categories), apply)

    def action_cycle_favorites(self) -> None:
        """Step: all slots -> this bank's favourites -> every favourite.

        Always exactly three steps, even when a view is empty: skipping one
        to be helpful makes a keypress land somewhere different depending on
        what happens to be favourited.
        """
        if self.favorites is None:
            self.notify_status("no favourites database")
            return
        index = VIEW_CYCLE.index(self.view_mode)
        self.view_mode = VIEW_CYCLE[(index + 1) % len(VIEW_CYCLE)]
        self._fill(self._current_bank)
        self._save_ui_state(view_mode=self.view_mode)
        self.query_one("#slot-table", DataTable).focus()
        shown = len(self._current)
        if self.view_mode == VIEW_ALL:
            self.notify_status(VIEW_LABELS[self.view_mode])
        elif not shown:
            where = (
                "this bank" if self.view_mode == VIEW_BANK_FAVOURITES else "any bank"
            )
            self.notify_status(
                f"{VIEW_LABELS[self.view_mode]} - nothing favourited in {where} yet"
            )
        else:
            self.notify_status(f"{VIEW_LABELS[self.view_mode]} - {shown}")

    # --- undo ---------------------------------------------------------------

    def _undo_gate(self) -> str | None:
        """Refuse undo while an exchange is in flight, in one word."""
        return "busy" if self._busy else None

    def _undo_status(self, message: str) -> None:
        """The undo log's sentence for the status bar."""
        self.notify_status(message)

    def _undo_apply(self, record: Record) -> bool:
        """Apply one undo record. Return True on success, False on refusal."""
        payload = record.payload
        if isinstance(payload, tuple):
            # The preset editors' own record: the whole preset as it was
            # before the change, sent back.  This runs on the undo's
            # worker, inside the busy gate, so the send is one exchange
            # with the rest of the batch and nothing can land between
            # the write and its read-back.
            return self._restore_preset(payload[0], payload[1])
        kind = payload.get("kind", "unknown")
        if kind == "favourite":
            # toggle is its own inverse: it flips the active flag and restores
            # whatever annotations the row was carrying.
            self.favorites.toggle(
                payload["bank_id"], payload["number"], name=payload.get("name", "")
            )
            return True
        elif kind == "tags":
            self.favorites.set_tags(
                payload["bank_id"], payload["number"], payload["old"]
            )
            return True
        elif kind == "note":
            self.favorites.set_note(
                payload["bank_id"], payload["number"], payload["old"]
            )
            return True
        elif kind == "rating":
            self.favorites.set_rating(
                payload["bank_id"], payload["number"], payload["old"]
            )
            return True
        return False

    def _restore_preset(self, seq_blob: bytes, preset_raw: bytes) -> bool:
        """Put the unit's Edit Preset back to the bytes it held before.

        **The whole preset goes back**, which is the only thing this
        protocol can do and the reason the undo record carries the whole
        of it: the unit takes a complete preset or nothing.

        **The gate is asked here too**, because an undo is a write.  The
        change it undoes went out through the gate, but the gate can
        have been locked since -- the undo can be pressed in the browser
        as well as in the editor, and ``w`` is a browser key.  A locked
        gate refuses, and the record stays on the log, because the
        preset it holds is still the one the unit has.
        """
        if not self._gate.armed:
            self._undo_reason = self._gate.refuse("the undo")
            return False
        try:
            self.device.send_preset(seq_blob, preset_raw, verify=True)
        except _BRIDGE_ERRORS as exc:
            self._undo_reason = str(exc)
            return False
        return True

    def action_undo(self) -> None:
        self._undo(1)

    def action_undo_all(self) -> None:
        self._undo(len(self._undo_log))

    def _undo(self, count: int) -> None:
        """Reverse the last ``count`` changes, newest first.

        Every sentence goes through :meth:`_say_to_editor`: `z` is
        served by the editors themselves, and the app's status line is
        behind the modal, so a "nothing to undo" written there is
        written where nobody is looking.
        """
        if self._busy:
            self._say_to_editor("busy", refused=True)
            return
        taken = self._undo_log.plan(count)
        if not taken:
            refusal = self._undo_log.last_refusal
            if refusal is not None:
                self._say_to_editor(f"refused: {refusal}", refused=True)
            else:
                self._say_to_editor("nothing to undo")
            return
        # Claimed on the main thread, before the worker exists.
        if not self._exchange.acquire(blocking=False):
            for record in reversed(taken):
                self._undo_log.push(record)
            self._say_to_editor("busy", refused=True)
            return
        self._undo_worker(taken)

    @work(thread=True)
    def _undo_worker(self, records: List[Record]) -> None:
        """**MIDI only.** The caller claimed the exchange; this frees it."""
        results: List[bool] = []
        self._undo_reason = None
        try:
            with self._busy_gate, self._lock:
                for record in records:
                    try:
                        results.append(self._undo_apply(record))
                    except _BRIDGE_ERRORS:
                        results.append(False)
        finally:
            self._exchange.release()
        self._call_from_thread(self._undo_done, records, results)

    def _undo_done(self, records: List[Record], results: List[bool]) -> None:
        """Report the batch on the main thread, and keep what did not land."""
        summary = self._undo_log.settle(results)
        failed = [record for record, ok in zip(records, results, strict=True) if not ok]
        # Oldest first again, so the log still describes the order the
        # changes happened in -- not the newest-first order they were tried.
        for record in reversed(failed):
            self._undo_log.push(record)
        if failed:
            reason = self._undo_reason
            if reason:
                self._say_to_editor(f"{summary} ({reason})", refused=True)
            else:
                self._say_to_editor(f"{summary}; {len(failed)} failed", refused=True)
            return
        if any(isinstance(record.payload, tuple) for record in records):
            # A preset went back.  The model is the truth the next edit
            # is built on, so it has to say what the unit holds again.
            self._adopt_restored_preset(records, results)
            self._say_to_editor("restored the preset as it was")
            return
        self._refresh_current_bank()
        self._update_detail(self.query_one("#slot-table", DataTable).cursor_row)

    # --- the preset editors -------------------------------------------------
    #
    # Three keys open three things over one live preset: `m` the eight
    # tracks, `x` the effect, `p` the list of the eighty presets the
    # unit holds.  The two editors are views of the same object, so an
    # edit made in one is an edit in the other.
    #
    # **Reading is silent, writing is not.**  A read of the current
    # preset is one message; the all-presets dump is 17977 bytes and
    # takes about twelve seconds, which is why `p` says so before it
    # starts rather than after.
    #
    # **A write reaches the working preset.**  The unit displays
    # "Press ENTER to save new preset", and no message in this protocol
    # stores a preset: storing it is the user's front panel act.  The
    # send stores into the buffer the unit plays from, but what it plays
    # changes only when the preset is reloaded -- ctrl+r forces that
    # reload remotely.  The unit goes briefly deaf to incoming MIDI
    # after a send, answering again by about thirty-five seconds.  Every
    # sentence the app writes after a send says so.

    def action_multi(self) -> None:
        """``m``: the eight tracks of the preset in hand.

        **A current-preset editor reads again every time it opens.**  The
        unit can be edited on its panel between two openings, and its
        Edit Preset mode can leave the app holding a state the unit no
        longer has; a cached copy then shows the old on/off and a
        keypress writes the wrong direction.  A preset picked from the
        list is a copy of a slot, not the unit's current preset, so that
        one is kept.

        Within one opening, ``m`` and ``x`` are two views of the same
        object -- the app re-reads, so they cannot disagree.
        """
        if (
            self._preset_loaded
            and self._edit_preset is not None
            and self._edit_slot is not None
        ):
            self._push_multi_screen()
            return
        self._read_preset(open_fx=False)

    def action_fx(self) -> None:
        """``x``: the effect of the preset in hand, re-read on opening."""
        if (
            self._preset_loaded
            and self._edit_preset is not None
            and self._edit_slot is not None
        ):
            self._push_fx_screen()
            return
        self._read_preset(open_fx=True)

    def _startup_read_presets(self, attempt: int = 0) -> None:
        """Warm the preset cache once, quietly, after the first frame.

        The all-presets dump is about twelve seconds; it runs on a worker,
        waits for any other exchange (the names read) to finish, and fills
        ``_all_dump`` so ``p`` opens immediately.  Failure is silent -- the
        user did not press anything -- and ``p`` still reads the bank
        itself when the cache is missing or stale.
        """
        if not hasattr(self.device, "read_all_presets"):
            return
        if self._busy or not self._exchange.acquire(blocking=False):
            if attempt < 30:
                self.set_timer(1.0, lambda: self._startup_read_presets(attempt + 1))
            else:
                # Give up gracefully: the meter must not claim a read that
                # is never coming.
                self._startup_step_done()
            return
        self._startup_presets_inflight = True
        self._startup_step(self._startup_phase_text(" (about 12 s)"))
        self._startup_presets_worker()

    @work(thread=True)
    def _startup_presets_worker(self) -> None:
        """**MIDI only** -- the background all-presets read."""
        failure: Optional[BaseException] = None
        dump: Optional[AllPresetsDump] = None
        try:
            with self._busy_gate, self._lock:
                dump = self.device.read_all_presets()
        except _BRIDGE_ERRORS as exc:
            failure = exc
        finally:
            self._exchange.release()
        if self._startup_presets_inflight:
            self._startup_presets_inflight = False
            self._call_from_thread(self._startup_step_done)
        if failure is not None or dump is None:
            return
        self._call_from_thread(self._startup_presets_done, dump)

    def _startup_presets_done(self, dump: AllPresetsDump) -> None:
        """Keep the background read for ``p``.  Main thread."""
        self._all_dump = dump
        self._all_dump_at = time.monotonic()
        try:
            preset_cache.save(dump.to_bytes())
        except (ValueError, AttributeError):
            # A fake dump in a test, or a parser that cannot round-trip;
            # the cache is an optimisation, never a requirement.
            return

    def action_preset_list(self) -> None:
        """``p``: the eighty presets the unit holds.

        Reading them all is one message and about twelve seconds, so the
        status line says what is happening before it starts rather than
        after: a screen that appears twelve seconds later with no
        explanation is a screen that looks like a hang.  The list is
        read once (at startup or on the first ``p``) and then served
        from the cache; the cache is refreshed only after a change
        (rename).
        """
        # This is the main window's entry; the MULTI editor has its own
        # entry in `_open_preset_list_from_multi`.
        self._preset_list_from_multi = False
        if self._all_dump is not None:
            self._show_preset_list(self._all_dump)
            return
        if self._busy:
            self.notify_status("busy", refused=True)
            return
        # Main thread; see action_select for why it is claimed here.
        if not self._exchange.acquire(blocking=False):
            self.notify_status("busy", refused=True)
            return
        self.notify_status("reading 80 presets (about 12 s, silent)...")
        self._preset_list_worker()

    def _open_preset_list_from_multi(self) -> None:
        """Open the preset browser over the MULTI editor (``p`` from MULTI).

        The list is served from the cached dump when it exists; otherwise
        it is read fresh, and the request is reported on the editor rather
        than on the status line.
        """
        self._preset_list_from_multi = True
        if self._all_dump is not None:
            self._show_preset_list(self._all_dump)
            return
        if self._busy:
            self._preset_list_from_multi = False
            self._say_to_editor("preset list not read: busy", refused=True)
            return
        if not self._exchange.acquire(blocking=False):
            self._preset_list_from_multi = False
            self._say_to_editor("preset list not read: busy", refused=True)
            return
        self._say_to_editor("reading 80 presets (about 12 s, silent)...")
        self._preset_list_worker()

    def _read_preset(self, *, open_fx: bool) -> None:
        """Claim the exchange and read the unit's current preset."""
        if self._busy:
            self.notify_status("busy", refused=True)
            return
        # Main thread; see action_select for why it is claimed here.
        if not self._exchange.acquire(blocking=False):
            self.notify_status("busy", refused=True)
            return
        self._read_preset_worker(open_fx)

    @work(thread=True)
    def _read_preset_worker(self, open_fx: bool) -> None:
        """**MIDI only** -- the preset is decoded on the main thread."""
        failure: Optional[BaseException] = None
        current = None
        try:
            with self._busy_gate, self._lock:
                current = self.device.read_current_preset()
        except AttributeError:
            failure = RuntimeError("this device does not answer preset requests")
        except _BRIDGE_ERRORS as exc:
            failure = exc
        finally:
            self._exchange.release()
        if failure is not None:
            self._call_from_thread(
                self.notify_status, f"preset: {failure}", refused=True
            )
            return
        self._call_from_thread(self._adopt_preset, current, open_fx)

    def _adopt_preset(self, current, open_fx: bool) -> None:
        """Take the unit's preset into the session.  Main thread.

        **The slot is not known.**  The unit does not say which of its
        eighty preset locations it is on, so the session's slot stays
        empty and the sentences after a send cannot name one.
        """
        try:
            preset = Preset.from_bytes(current.preset_raw)
        except ValueError as exc:
            self.notify_status(f"preset: {exc}", refused=True)
            return
        self._edit_preset = preset
        self._edit_seq_blob = current.seq_blob
        self._edit_preset_raw = current.preset_raw
        self._edit_snapshot = _copy_preset(preset)
        self._edit_dirty = False
        self._edit_slot = None
        self._preset_loaded = True
        self._update_subtitle()
        if open_fx:
            self._push_fx_screen()
        else:
            self._push_multi_screen()

    def _preset_list_failed(self, failure) -> None:
        """Main-thread handling of a failed preset list read."""
        from_multi = self._preset_list_from_multi
        self._preset_list_from_multi = False
        if from_multi and self._multi_screen is not None:
            self._say_to_editor(f"presets: {failure}", refused=True)
        else:
            self.notify_status(f"presets: {failure}", refused=True)

    @work(thread=True)
    def _preset_list_worker(self) -> None:
        """**MIDI only** -- the all-presets dump, about twelve seconds."""
        failure: Optional[BaseException] = None
        dump = None
        try:
            with self._busy_gate, self._lock:
                dump = self.device.read_all_presets()
        except AttributeError:
            failure = RuntimeError("this device does not answer preset requests")
        except _BRIDGE_ERRORS as exc:
            failure = exc
        finally:
            self._exchange.release()
        if failure is not None:
            self._call_from_thread(self._preset_list_failed, failure)
            return
        self._call_from_thread(self._save_preset_cache, dump)
        self._call_from_thread(self._show_preset_list, dump)

    def _tick_reload(self) -> None:
        """Refresh the reload elapsed seconds while the worker runs."""
        if self._reload_timer is None:
            return
        elapsed = time.monotonic() - self._reload_t0
        if self._preset_list_screen is not None:
            with contextlib.suppress(Exception):
                self._preset_list_screen.say(f"reloading presets... {elapsed:.0f}s")

    def _save_preset_cache(self, dump: AllPresetsDump) -> None:
        """Save the preset dump to the cache file. Main thread."""
        try:
            preset_cache.save(dump.to_bytes())
        except (ValueError, AttributeError, OSError):
            pass

    def _show_preset_list(self, dump) -> None:
        """The list of the eighty presets.  Main thread.

        The list is shown when the data has arrived, not before: a modal
        with nothing to do for twelve seconds is a modal that looks
        broken.  The rows carry the unit's own names, read with the
        mute bit cleared and never written back.

        **Note:** The app cannot read the unit's own ``Preset Prog``
        setting.  It knows only the panel mode it last sent (if the user
        has pressed ``M``), and Program Change selects a preset only when
        the unit is in Select Preset *and* Preset Prog is ON.  The note
        says that rather than inventing a device state: the unit on the
        bench was really ON while the old note claimed OFF.
        """
        try:
            rows: List[Tuple[int, bytes]] = [
                (slot, dump.presets_raw[index])
                for slot, index in sorted(dump.slot_offsets().items())
            ]
        except (DeviceError, IndexError, ValueError) as exc:
            self.notify_status(f"presets: {exc}", refused=True)
            return
        self._all_dump = dump
        self._all_dump_at = time.monotonic()
        if self._remote_mode_confirmed:
            if self._remote_mode == "PRESET":
                mode_note = (
                    "This app last sent Select Preset. Program Change selects "
                    "a preset only while the unit's own Preset Prog setting "
                    "(system bank) is ON — this app cannot read that setting."
                )
            else:
                mode_note = (
                    "This app last sent Select Sound. Press M to send Select "
                    "Preset; Program Change selects presets only while the "
                    "unit's own Preset Prog setting (system bank) is ON."
                )
        else:
            mode_note = (
                "The unit does not report its Select Sound / Select Preset "
                "mode. Press M to send Select Preset; remote preset selection "
                "also needs the unit's own Preset Prog setting (system bank) ON."
            )
        if hasattr(self.device, "select_preset_by_walk"):
            mode_note += (
                " ⏎ selects the slot on the unit: a Program Change first, "
                "then the button walk when it does not take."
            )
        # A screen that is already open is refreshed in place; a new
        # screen pushes onto the stack when the user opens the list
        # fresh (with ``p`` or after multi closes back to it).
        if self._preset_list_screen is not None:
            with contextlib.suppress(Exception):
                self._preset_list_screen.refresh_presets(rows)
                if self._reload_timer is not None:
                    self._reload_timer.stop()
                    self._reload_timer = None
                if self._preset_list_screen is not None:
                    self._preset_list_screen.say("presets reloaded")
                return
        screen = PresetListScreen(
            rows,
            current=self._edit_slot,
            progress_note=None,
            mode_note=mode_note,
            favorites_like_names=self._preset_labels,
            on_rename=self._rename_preset,
            on_reload=self._reload_preset_list,
        )
        self._preset_list_screen = screen
        self.push_screen(screen, self._preset_picked)
        # The screen will remain open until a preset is picked; when it
        # closes, the preset picker does not track it, so we rely on the
        # screen being replaced or cleared by the reload flow.

    def _reload_preset_list(self) -> None:
        """Reload the eighty presets from the device.

        **The spinner stays here, not in the modal.**  Reading all
        presets is one message and about twelve seconds, so the
        worker keeps the app's own spinner and hands the finished
        list to the screen, which repaints its rows.  The screen
        stays open throughout: a reload is a refresh, not a
        navigation.
        """
        if self._preset_list_screen is None:
            return
        if self._busy or not self._exchange.acquire(blocking=False):
            self.notify_status("presets reload not sent: busy", refused=True)
            return
        if self._preset_list_screen is not None:
            self._preset_list_screen.say("reloading presets...")
        self._reload_t0 = time.monotonic()
        self._reload_timer = self.set_interval(0.5, self._tick_reload)
        self._preset_list_worker()

    def _rename_preset(self, slot: int, new_name: str) -> None:
        """Give a preset slot a local label (app-side only).

        **This method is only the local label.**  The firmware's store
        copies only the live track-and-pile snapshot (preset offsets
        32-151), so a dump never writes the name field (2-17).  A real
        rename of the unit's own preset name is done by
        ``_rename_current_preset_on_unit`` (the ``R`` key), which drives
        the unit's save dialog.  What this app can also do is keep its
        own label for the slot; the unit's name is unchanged by this
        method, and the label is shown in the preset list's "label"
        column and beside the name in the multi editor.
        """
        updated = preset_labels.set_label(self._preset_labels, slot, new_name)
        if updated == self._preset_labels:
            return
        self._preset_labels = updated
        preset_labels.save(self._preset_labels)
        label = self._preset_labels.get(slot)
        if self._multi_screen is not None:
            self._multi_screen.set_local_label(label)
        if label is None:
            self.notify_status(f"P{slot:02d} label cleared")
        else:
            self.notify_status(
                f"P{slot:02d} labelled {label!r} in this app; the unit keeps "
                f"its own preset name (a dump never writes it)"
            )

    def _slot_of_edit_preset(self) -> Optional[int]:
        """The slot the multi editor's preset came from.

        **The preset's own seqno/address byte is the slot.**  The unit
        rewrites it to the selected slot, and the preset walk verifies
        the slot by reading it.  A content match against the cached dump
        is *not* safe: after editing P23 it can be byte-identical in
        32-151 to P01, and the editor then stored into P01 (the owner's
        fresh-start report).  A preset picked out of the list already
        carries `_edit_slot`; otherwise the address byte is the answer.
        """
        if self._edit_slot is not None:
            return self._edit_slot
        raw = self._edit_preset_raw
        if raw and 0 <= raw[0] < 80:
            self._edit_slot = raw[0]
            return raw[0]
        return None

    def _rename_current_preset_label(self, new_name: str) -> None:
        """Label the preset the multi editor is over, when its slot is known."""
        slot = self._slot_of_edit_preset()
        if slot is None:
            self._say_to_editor(
                "this preset's slot is unknown; open the preset list with p "
                "and rename the slot there",
                refused=True,
            )
            return
        self._rename_preset(slot, new_name)

    def _rename_current_preset_on_unit(self, new_name: str) -> None:
        """Rename the preset on the unit itself (via the save dialog)."""
        slot = self._slot_of_edit_preset()
        if slot is None:
            self._say_to_editor(
                "this preset's slot is unknown; open the preset list with p "
                "and pick it, then rename on the unit",
                refused=True,
            )
            return
        # Validate before touching the wire.
        name = new_name
        if len(name) > 16 or any(not (0x20 <= ord(ch) <= 0x7E) for ch in name):
            self._say_to_editor(
                "rename refused: a name is at most 16 printable ASCII characters",
                refused=True,
            )
            return
        if not self._gate.armed:
            self._say_to_editor(self._gate.refuse("the rename"), refused=True)
            return
        if self._busy or not self._exchange.acquire(blocking=False):
            self._say_to_editor("busy", refused=True)
            return
        had_edit = self._edit_dirty
        if had_edit:
            self._say_to_editor(
                f"renaming P{slot:02d} on the unit and storing the current edit..."
            )
        else:
            self._say_to_editor(f"renaming P{slot:02d} on the unit...")
        self._rename_on_unit_worker(slot, name, had_edit)

    @work(thread=True)
    def _rename_on_unit_worker(self, slot: int, new_name: str, had_edit: bool) -> None:
        """**MIDI only** -- the save-dialog name page, then the read-back.

        A SysEx dump leaves the working preset changed but still presents
        ``Replace edit preset?`` on Enter/Save (bench, P79: a volume edit
        then the copy path renamed it).  The app's edits are dumps, so it
        takes the copy path (``pending=False``) and Yes loads that working
        preset into the edit buffer before the name page.
        """
        failure: Optional[BaseException] = None
        observed = None
        try:
            with self._busy_gate, self._lock:
                if hasattr(self.device, "rename_preset_on_unit"):
                    observed = self.device.rename_preset_on_unit(
                        slot, new_name, pending=False
                    )
                else:
                    failure = RuntimeError(
                        "this device cannot rename presets on the unit"
                    )
        except _BRIDGE_ERRORS as exc:
            failure = exc
        except ValueError as exc:
            failure = exc
        finally:
            self._exchange.release()
        self._call_from_thread(
            self._rename_on_unit_done, slot, had_edit, observed, failure
        )

    def _rename_on_unit_done(
        self,
        slot: int,
        had_edit: bool,
        observed,
        failure: Optional[BaseException],
    ) -> None:
        if failure is not None:
            self._say_to_editor(f"rename P{slot:02d}: {failure}", refused=True)
            return
        if observed is None:
            self._say_to_editor(f"rename P{slot:02d}: no read-back", refused=True)
            return
        observed = str(observed)
        if self._edit_preset is not None:
            self._edit_preset.name = observed
            self._edit_preset_raw = self._edit_preset.to_bytes()
            self._edit_snapshot = _copy_preset(self._edit_preset)
            self._cache_slot_raw(slot, self._edit_preset.to_bytes())
        self._edit_slot = slot
        self._edit_dirty = False
        self._update_subtitle()
        if self._multi_screen is not None:
            self._multi_screen.set_preset(self._edit_preset)
        else:
            self._redraw_screen(self._active_editor)
        status = f"P{slot:02d} renamed on the unit to {observed.strip()!r}"
        if had_edit:
            status += " (the current edit was stored too)"
        self._say_to_editor(status, success=True)

    def _preset_picked(self, slot: Optional[int]) -> None:
        """A preset was picked: select it on the unit, then load it.

        **Enter on the list is a selection, not a preset write.**  With
        Preset Prog ON, a Program Change on the base channel selects the
        slot; the app reads the working preset back to see whether it
        took.  When it did not (Preset Prog OFF, the unit in Select Sound
        mode, or the receiver's own flakiness), the app walks the
        selection with buttons 5/7 and verifies the seqno.  Only then
        does the editor open, over the bytes the unit actually holds.

        A device without the selection path (the demo, the test fakes)
        falls back to decoding the cached dump, which is the old
        behaviour: that load is not a write and the unit is untouched.
        """
        if slot is None:
            # The screen was closed; clear the reference so a later reload
            # can push a fresh screen.
            self._preset_list_screen = None
            self._preset_list_from_multi = False
            return
        from_multi = self._preset_list_from_multi
        if hasattr(self.device, "select_preset_by_walk") and hasattr(
            self.device, "read_current_preset"
        ):
            if self._busy or not self._exchange.acquire(blocking=False):
                self._preset_list_screen = None
                if from_multi:
                    self._say_to_editor("preset selection not sent: busy", refused=True)
                else:
                    self.notify_status("preset selection not sent: busy", refused=True)
                self._preset_list_from_multi = False
                return
            # The list has dismissed; do not leave a reference that a
            # later `p` would try to refresh.
            self._preset_list_screen = None
            if from_multi:
                self._say_to_editor(f"selecting P{slot:02d} on the unit...")
            else:
                self.notify_status(f"selecting P{slot:02d} on the unit...")
            self._select_preset_worker(slot)
            return
        self._load_preset_from_dump(slot)

    @work(thread=True)
    def _select_preset_worker(self, slot: int) -> None:
        """**MIDI only** -- Program Change, read back, walk if needed."""
        failure: Optional[BaseException] = None
        current = None
        try:
            with self._busy_gate, self._lock:
                # **A read first, and no panel key when the unit is
                # already there.**  Sending Select Preset on a slot that
                # is already selected reloads the preset; after a commit
                # that is exactly what showed the owner an all-off
                # PSET01 and "Edited Prest" again.  A pick of the current
                # slot is a read, nothing more.
                current = self.device.read_current_preset()
                known = current.preset_raw[0] if current.preset_raw else 0
                if known != slot and hasattr(self.device, "tap_button"):
                    # Preset Prog and the panel mode are two different
                    # things.  Button 2 is Select Preset; sending it makes
                    # the Program Change mean what the list promises.
                    self.device.tap_button(SELECT_PRESET)
                    time.sleep(PRESET_SETTLE)
                    current = self.device.read_current_preset()
                    known = current.preset_raw[0] if current.preset_raw else known
                if known != slot:
                    if hasattr(self.device, "select_preset"):
                        try:
                            current = self.device.select_preset(slot)
                            known = (
                                current.preset_raw[0] if current.preset_raw else known
                            )
                        except _BRIDGE_ERRORS:
                            # A Program Change that does not take is not a
                            # failed operation; the walk is the fallback.
                            current = self.device.read_current_preset()
                            known = (
                                current.preset_raw[0] if current.preset_raw else known
                            )
                    if known != slot:
                        self.device.select_preset_by_walk(slot, known_slot=known)
                        current = self.device.read_current_preset()
        except _BRIDGE_ERRORS as exc:
            failure = exc
        finally:
            self._exchange.release()
        self._call_from_thread(self._preset_selected, slot, current, failure)

    def _preset_selected(  # noqa: C901
        self,
        slot: int,
        current,
        failure: Optional[BaseException],
    ) -> None:
        """The unit has moved: adopt its bytes and open the editor.  Main thread."""
        from_multi = self._preset_list_from_multi and self._multi_screen is not None
        if failure is not None:
            if from_multi:
                self._say_to_editor(
                    f"P{slot:02d} not selected: {failure}", refused=True
                )
            else:
                self.notify_status(f"P{slot:02d} not selected: {failure}", refused=True)
            self._preset_list_from_multi = False
            self._preset_list_screen = None
            return
        if current is None or not current.preset_raw:
            msg = f"P{slot:02d} not selected: the unit sent no preset"
            if from_multi:
                self._say_to_editor(msg, refused=True)
            else:
                self.notify_status(msg, refused=True)
            self._preset_list_from_multi = False
            self._preset_list_screen = None
            return
        try:
            preset = Preset.from_bytes(current.preset_raw)
        except ValueError as exc:
            msg = f"P{slot:02d}: {exc}"
            if from_multi:
                self._say_to_editor(msg, refused=True)
            else:
                self.notify_status(msg, refused=True)
            self._preset_list_from_multi = False
            self._preset_list_screen = None
            return
        observed = current.preset_raw[0]
        self._edit_preset = preset
        self._edit_seq_blob = current.seq_blob
        self._edit_preset_raw = current.preset_raw
        self._edit_snapshot = _copy_preset(preset)
        self._edit_dirty = False
        self._edit_slot = observed
        self._preset_loaded = True
        if hasattr(self.device, "tap_button"):
            # The worker sent Select Preset before the selection, so the
            # app's own memory of the panel mode is now confirmed.
            self._remote_mode = "PRESET"
            self._remote_mode_confirmed = True
        self._update_subtitle()
        # The list has dismissed; a reference to it would make the next
        # `p` try to refresh a screen that is no longer on the stack.
        self._preset_list_screen = None
        self._preset_list_from_multi = False
        if from_multi:
            if self._multi_screen is not None:
                self._multi_screen.set_preset(preset, slot=observed)
                self._multi_screen.set_local_label(self._preset_labels.get(observed))
            if observed == slot:
                self._say_to_editor(
                    f"P{slot:02d} selected on the unit; the editor now shows it"
                )
            else:
                self._say_to_editor(
                    f"the unit reports P{observed:02d}, not P{slot:02d}; the editor "
                    f"is open over what the unit holds",
                    refused=True,
                )
            return
        # Remember that the multi screen came from the preset list, so
        # that dismissing it with `q` can pop back to the list instead of
        # dropping to the main window.
        self._multi_from_preset_list = True
        self._push_multi_screen()
        if observed == slot:
            self.notify_status(
                f"P{slot:02d} selected on the unit and loaded into the editor"
            )
        else:
            self.notify_status(
                f"the unit reports P{observed:02d}, not P{slot:02d}; the editor "
                f"is open over what the unit holds",
                refused=True,
            )

    def _load_preset_from_dump(self, slot: int) -> None:
        """Decode a slot from the cached dump into the editor.

        The fallback for a device that cannot select a preset.  That load
        is not a write and the unit is untouched; the reader is told so.
        """
        from_multi = self._preset_list_from_multi and self._multi_screen is not None
        dump = self._all_dump
        if dump is None:
            if from_multi:
                self._say_to_editor(
                    "the preset list was read a long time ago", refused=True
                )
            else:
                self.notify_status(
                    "the preset list was read a long time ago", refused=True
                )
            self._preset_list_from_multi = False
            return
        # The command line's own two helpers know how a slot in the
        # dump pairs with the sequence it plays, and that pairing is the
        # fiddly part of the layout (docs/PRESET-FRAMING.md section 4).
        # The import is here rather than at the top because that module
        # is a command-line program, and this is a browser.
        from ensqsqed import cli

        try:
            preset, seq_blob = cli.preset_from_dump(dump, slot)
        except SystemExit as exc:
            self.notify_status(str(exc), refused=True)
            return
        self._edit_preset = preset
        self._edit_seq_blob = seq_blob
        self._edit_preset_raw = preset.to_bytes()
        self._edit_snapshot = _copy_preset(preset)
        self._edit_dirty = False
        self._edit_slot = slot
        self._preset_loaded = True
        self._update_subtitle()
        # The list has dismissed; do not refresh a popped screen.
        self._preset_list_screen = None
        from_multi = self._preset_list_from_multi and self._multi_screen is not None
        self._preset_list_from_multi = False
        if from_multi:
            if self._multi_screen is not None:
                self._multi_screen.set_preset(preset, slot=slot)
                self._multi_screen.set_local_label(self._preset_labels.get(slot))
            self._say_to_editor(
                f"preset {slot + 1} loaded into the editor — the unit is "
                f"untouched until a change is applied"
            )
            return
        # Remember that the multi screen came from the preset list, so
        # that dismissing it with `q` can pop back to the list instead of
        # dropping to the main window.
        self._multi_from_preset_list = True
        self._push_multi_screen()
        self.notify_status(
            f"preset {slot + 1} loaded into the editor — the unit is "
            f"untouched until a change is applied"
        )

    def action_remote(self) -> None:
        """``ctrl+o``: open the remote front-panel window."""
        self._push_remote_screen()

    def action_toggle_mode(self) -> None:
        """``M``: toggle the unit between Select Sound and Select Preset.

        The unit does not report its mode, so the app remembers the last
        one it sent.  The press goes through the remote queue, so it is
        paced like every other panel key and the press is not dropped.
        """
        new = "PRESET" if self._remote_mode == "SOUND" else "SOUND"
        number = 1 if new == "SOUND" else 2
        label = "SELECT SOUND" if new == "SOUND" else "SELECT PRESET"
        if not hasattr(self.device, "tap_button"):
            self.notify_status(
                "mode not sent: this device does not take button presses",
                refused=True,
            )
            return
        self._remote_mode = new
        self._remote_mode_confirmed = True
        if self._remote_screen is not None:
            self._remote_screen.set_mode(new)
        self._remote_press(number)
        self.notify_status(f"mode: {label} (button {number} sent)")
        self._update_subtitle()

    def _set_remote_mode(self, mode: str) -> None:
        """The remote window toggled the mode: keep the app's copy."""
        if mode in ("SOUND", "PRESET"):
            self._remote_mode = mode
            # The window only calls this after it has sent the mode key,
            # so from here on the mode is a fact about what went out --
            # not about what the unit's Preset Prog setting says.
            self._remote_mode_confirmed = True
            self._update_subtitle()

    # --- the editors themselves ---------------------------------------------

    def _push_multi_screen(self) -> None:
        """Open the eight-track editor over the live preset."""
        preset = self._edit_preset
        if preset is None:
            self.notify_status("no preset is loaded", refused=True)
            return
        screen = MultiScreen(
            preset,
            on_apply=self._apply_multi,
            on_undo=self._undo_from_screen,
            on_sound_picked=self._pick_sound,
            allow_write=self._gate.armed,
            gate=self._gate,
            name_for=self.catalog.display_name,
            on_store_assist=self._store_assist,
            on_reload_preset=self._reload_preset,
            on_live_mute=self._live_mute,
            on_panel_button=self._remote_press,
            on_open_fx=self._push_fx_screen,
            on_open_preset_list=self._open_preset_list_from_multi,
            on_rename=self._rename_current_preset_label,
            on_rename_on_unit=self._rename_current_preset_on_unit,
            on_commit=self._commit_preset,
            local_label=(
                self._preset_labels.get(self._edit_slot)
                if self._edit_slot is not None
                else None
            ),
            slot=self._edit_slot,
            backup_root=self._backup_root,
        )
        self._multi_screen = screen
        self._previous_editor = self._active_editor
        self._active_editor = screen
        self.push_screen(screen, self._multi_screen_dismissed)

    def _push_fx_screen(self) -> None:
        """Open the effect editor over the live preset."""
        preset = self._edit_preset
        if preset is None:
            self.notify_status("no preset is loaded", refused=True)
            return
        screen = FxScreen(
            preset,
            on_apply=self._apply_fx,
            on_undo=self._undo_from_screen,
            on_commit=self._commit_preset,
            allow_write=self._gate.armed,
            gate=self._gate,
        )
        self._fx_screen = screen
        self._previous_editor = self._active_editor
        self._active_editor = screen
        self.push_screen(screen, self._fx_screen_dismissed)

    def _push_remote_screen(self) -> None:
        """Open the remote front-panel window."""
        screen = RemoteScreen(
            on_press=self._remote_press,
            on_live_mute=self._remote_live_mute,
            mode=self._remote_mode,
            on_mode=self._set_remote_mode,
        )
        self._remote_screen = screen
        self.push_screen(screen, self._remote_closed)

    def _remote_closed(self, _result: Any = None) -> None:
        """The remote screen was closed."""
        self._remote_screen = None

    def _multi_screen_dismissed(self, _result: Any = None) -> None:
        """The multi editor screen was closed.

        When the editor was opened from the preset list, closing it --
        with ``q`` or ``escape`` -- pops back to that list rather than to
        the main window, which is the screen the user came from.  A
        multi opened straight from ``m`` over the unit's own current
        preset still drops back to the browser behind it.
        """
        self._multi_screen = None
        self._active_editor = self._previous_editor
        self._previous_editor = None
        if self._multi_from_preset_list:
            # The multi window was opened out of the preset list, so the
            # preset browser is the screen the user meant to come back
            # to. Re-open it from the cached dump; it was served from
            # there anyway, and the list is the browser the flow
            # started from.
            self._multi_from_preset_list = False
            self._preset_list_screen = None
            self._show_preset_list(self._all_dump)

    def _fx_screen_dismissed(self, _result: Any = None) -> None:
        """The FX editor screen was closed."""
        self._fx_screen = None
        self._active_editor = self._previous_editor
        self._previous_editor = None

    # --- the write path -----------------------------------------------------

    def _apply_multi(self, preset: Preset, description: str) -> None:
        """A change made in the eight-track editor."""
        self._apply_preset(preset, description, self._multi_screen)

    def _apply_fx(self, preset: Preset, description: str) -> None:
        """A change made in the effect editor."""
        self._apply_preset(preset, description, self._fx_screen)

    def _apply_preset(
        self, preset: Preset, description: str, screen: Optional[EditorScreen]
    ) -> None:
        """One change to the live preset: the gate, the send, the record.

        **This is the only place a change can be stopped before it
        reaches the wire.**  The screen offers the change and has
        already put it in the model -- that is how the table can show it
        straight away -- so the check has to live here, or a second
        caller of this method could simply not make it.

        **The whole preset goes.**  This protocol has no field-level
        write: the unit takes a complete preset, the sequence data
        followed by the 168 bytes, and nothing else.  So one changed
        byte means one whole dump, and the dump is read back and
        compared afterwards.
        """
        if not self._preset_loaded or self._edit_seq_blob is None:
            self._refuse_change(screen, preset, description, "no preset is loaded")
            return
        if not self._gate.armed:
            self._refuse_change(
                screen, preset, description, self._gate.refuse(description)
            )
            return
        if self._apply_inflight:
            # A send is on the wire: remember the latest value and let
            # the running worker take it next -- no refusal, nothing
            # lost, and the screen already shows the change.
            self._apply_target = _copy_preset(preset)
            self._apply_target_description = description
            self._apply_target_screen = screen
            return
        if self._busy:
            self._refuse_change(screen, preset, description, "busy")
            return
        # Main thread; see action_select for why it is claimed here.
        if not self._exchange.acquire(blocking=False):
            self._refuse_change(screen, preset, description, "busy")
            return
        sent_raw = preset.to_bytes()
        self._apply_inflight = True
        self._apply_target = None
        self._apply_target_description = ""
        self._apply_target_screen = None
        self._apply_preset_worker(preset, description, screen, sent_raw)

    # --- the store assist --------------------------------------------------

    def _store_assist(self, slot: int, preset_raw: bytes) -> None:
        """Send a slot's backed-up bytes to the unit's selected slot.

        **This is a write, and the gate is asked here as well as in the
        screen.**  The screen asks before it opens its dialogs and again
        after the confirmation; this check is the one between a
        confirmation and the wire, and it is the same one every other
        writer goes through.

        The unit is not addressed.  The bytes are the backed-up slot's
        own 168, address bytes included; the unit rewrites those to the
        selected slot on receipt, and there is no message that says
        otherwise.  So the destination is whatever the owner selected on
        the front panel, and the sentence after the send says exactly
        that.
        """
        if not hasattr(self.device, "send_preset_with_retry") or not hasattr(
            self.device, "read_current_preset"
        ):
            self._say_to_editor(
                "store assist not sent: this device does not answer preset requests",
                refused=True,
            )
            return
        if not self._gate.armed:
            self._say_to_editor(
                self._gate.refuse("the store assist"),
                refused=True,
            )
            return
        if self._busy:
            self._say_to_editor("store assist not sent: busy", refused=True)
            return
        # Main thread; see action_select for why it is claimed here.
        if not self._exchange.acquire(blocking=False):
            self._say_to_editor("store assist not sent: busy", refused=True)
            return
        self._store_assist_worker(slot, preset_raw)

    # --- the reload ---------------------------------------------------------

    def _reload_preset(self, slot: int) -> None:
        """Force a reload of the preset the editor is editing.

        **This is a write, and the gate is asked here as well as in the
        screen.**  The screen asks before it opens its dialog and again
        after the confirmation; this check is the one between a
        confirmation and the wire, and it is the same one every other
        writer goes through.
        """
        if not hasattr(self.device, "select_preset_by_walk") or not hasattr(
            self.device, "read_current_preset"
        ):
            self._say_to_editor(
                "reload not sent: this device does not answer preset requests",
                refused=True,
            )
            return
        if not self._gate.armed:
            self._say_to_editor(
                self._gate.refuse("the reload"),
                refused=True,
            )
            return
        if self._busy:
            self._say_to_editor("reload not sent: busy", refused=True)
            return
        # Main thread; see action_select for why it is claimed here.
        if not self._exchange.acquire(blocking=False):
            self._say_to_editor("reload not sent: busy", refused=True)
            return
        self._reload_preset_worker(slot)

    @work(thread=True)
    def _reload_preset_worker(self, slot: int) -> None:
        """**MIDI only** -- leave and return, then read the stored preset."""
        failure: Optional[BaseException] = None
        current = None
        try:
            with self._busy_gate, self._lock:
                known = self.device.read_current_preset()
                known_slot = known.preset_raw[0] if known.preset_raw else 0
                self.device.select_preset_by_walk(
                    slot, known_slot=known_slot, force_reload=True
                )
                current = self.device.read_current_preset()
        except _BRIDGE_ERRORS as exc:
            failure = exc
        finally:
            self._exchange.release()
        self._call_from_thread(self._reload_preset_done, slot, current, failure)

    def _reload_preset_done(
        self,
        slot: int,
        current,
        failure: Optional[BaseException],
    ) -> None:
        """Adopt what the unit reloaded, and say so.  Main thread.

        **A reload is a read as well as a walk.**  Without this the table
        went on showing the bytes the editor already had, which is why
        ``ctrl+r`` appeared to change nothing (the owner's PSET 03 was
        selected track 2 with layers 3-8, and the table still showed an
        older model).
        """
        if failure is not None:
            self._say_to_editor(f"reload not sent: {failure}", refused=True)
            return
        if current is None or not current.preset_raw:
            self._say_to_editor("reload read no preset from the unit", refused=True)
            return
        try:
            preset = Preset.from_bytes(current.preset_raw)
        except ValueError as exc:
            self._say_to_editor(f"reload: {exc}", refused=True)
            return
        observed = current.preset_raw[0]
        self._edit_preset = preset
        self._edit_seq_blob = current.seq_blob
        self._edit_preset_raw = current.preset_raw
        self._edit_snapshot = _copy_preset(preset)
        # A reload reads the working buffer; it does not store anything.
        # An un-stored edit therefore stays un-stored; leave _edit_dirty
        # alone so a later rename uses the pending-edit path.
        self._edit_slot = observed
        self._preset_loaded = True
        self._update_subtitle()
        if self._multi_screen is not None:
            self._multi_screen.set_preset(preset)
        else:
            self._redraw_screen(self._active_editor)
        self._say_to_editor(
            f"P{observed:02d} reloaded from the unit — the editor now shows "
            f"its stored bytes"
        )

    def _live_mute(self) -> None:
        """Toggle the live mute of the track under the panel's cursor.

        **No write gate.**  It is the panel's own gesture, not a preset
        write; the MULTI screen gates its own ``l`` when it calls this.
        """
        if not hasattr(self.device, "toggle_live_mute"):
            self._say_to_editor(
                "live mute not sent: this device does not take button presses",
                refused=True,
            )
            return
        if self._busy:
            self._say_to_editor("live mute not sent: busy", refused=True)
            return
        if not self._exchange.acquire(blocking=False):
            self._say_to_editor("live mute not sent: busy", refused=True)
            return
        self._live_mute_worker()

    @work(thread=True)
    def _live_mute_worker(self) -> None:
        """**MIDI only** -- the Track pair's chord, nothing else."""
        failure: Optional[BaseException] = None
        try:
            with self._busy_gate, self._lock:
                self.device.toggle_live_mute()
        except _BRIDGE_ERRORS as exc:
            failure = exc
        finally:
            self._exchange.release()
        self._call_from_thread(self._live_mute_done, failure)

    def _live_mute_done(self, failure: Optional[BaseException]) -> None:
        """Say what the live mute did.  Main thread."""
        if failure is not None:
            message = f"live mute not sent: {failure}"
            self._say_to_editor(message, refused=True)
            if self._remote_screen is not None:
                self._remote_screen.live_mute_result(False, message)
            return
        message = (
            "live mute toggled for the track under the unit's cursor — the "
            "app cannot read the panel's cursor; check the display's '-'."
        )
        self._say_to_editor(message)
        if self._remote_screen is not None:
            self._remote_screen.live_mute_result(True, message)

    def _remote_press(self, number: int) -> None:
        """Queue one virtual button for the unit.

        **No write gate.**  The gate guards this program's preset
        writes; the remote window drives the unit's own panel, and the
        program cannot tell which presses the unit treats as edits --
        the owner is the one pressing.

        **Nothing is dropped.**  The wire is paced (``SYSEX_GAP`` in the
        bridge: the unit reboots under a flood of button frames), so a
        burst of keypresses would otherwise be refused as "busy".  Each
        press goes into a queue and one worker drains it in order; the
        queue is the buffer the owner asked for.
        """
        if not hasattr(self.device, "tap_button"):
            self._remote_refuse(
                "remote not sent: this device does not take button presses",
                refused=True,
            )
            return
        self._remote_queue.append(number)
        if not self._remote_draining:
            self._remote_draining = True
            self._remote_drain()

    def _remote_live_mute(self) -> None:
        """Queue the live-mute chord (buttons 10 + 12) for the unit.

        The live mute is a two-button chord sent atomically.  It goes
        through the same paced queue as single presses so it respects
        the wire pacing.  A sentinel tuple marks the chord in the queue.
        """
        if not hasattr(self.device, "toggle_live_mute"):
            self._remote_refuse(
                "live mute not sent: this device does not take button presses",
                refused=True,
            )
            # Reset the pending flag on the screen so it can be retried
            if self._remote_screen is not None:
                self._remote_screen._pending_live_mute = False
            return
        # Sentinel for the live-mute chord: (10, 12) are the Track L/R buttons
        self._remote_queue.append(("live_mute", 10, 12))
        if not self._remote_draining:
            self._remote_draining = True
            self._remote_drain()

    @work(thread=True)
    def _remote_drain(self) -> None:
        """**MIDI only** -- send the queued presses, in order, one by one."""
        while self._remote_queue:
            item = self._remote_queue.popleft()
            failure: Optional[BaseException] = None
            try:
                with self._busy_gate, self._lock:
                    if isinstance(item, tuple) and item[0] == "live_mute":
                        # Live-mute chord: buttons 10 (Track L) and 12 (Track R)
                        self.device.toggle_live_mute()
                    else:
                        # Single button press
                        number = item
                        self.device.tap_button(number)
            except _BRIDGE_ERRORS as exc:
                failure = exc
            self._call_from_thread(self._remote_press_done, item, failure)
        self._call_from_thread(self._remote_drained)

    def _remote_drained(self) -> None:
        """The queue is empty: the next press starts a fresh drain."""
        self._remote_draining = False
        if self._remote_queue:
            self._remote_draining = True
            self._remote_drain()

    def _remote_press_done(
        self, item: int | tuple, failure: Optional[BaseException]
    ) -> None:
        """Say what the remote key did.  Main thread."""
        if failure is not None:
            if isinstance(item, tuple) and item[0] == "live_mute":
                self._remote_report(f"live mute not sent: {failure}", refused=True)
            else:
                self._remote_report(f"remote not sent: {failure}", refused=True)
            return
        if isinstance(item, tuple) and item[0] == "live_mute":
            self._remote_report("remote: sent live-mute chord (Track L + Track R).")
            return
        number = item
        label = _REMOTE_LABELS.get(number)
        if label is not None:
            self._remote_report(f"remote: sent virtual button {number} ({label}).")
        else:
            self._remote_report(f"remote: sent virtual button {number}.")

    def _remote_report(self, message: str, *, refused: bool = False) -> None:
        """Say one sentence on the remote screen (or the app status)."""
        screen = self._remote_screen
        if screen is None:
            self.notify_status(message, refused=refused)
            return
        screen.say(message, refused=refused)

    def _remote_refuse(self, message: str, *, refused: bool = True) -> None:
        """Refuse on the remote screen, deferred past its own line."""
        screen = self._remote_screen
        if screen is None:
            self.notify_status(message, refused=refused)
            return
        self.call_later(lambda: screen.say(message, refused=refused))

    def _commit_preset(self) -> None:
        """Store the edit buffer with the manual's Enter/Save three times.

        **The store is the three Enter/Save presses, not a reselection.**
        The owner's panel sequence is Edit Track → toggle → Select Preset
        → Enter/Enter/Enter → *SAVED*.  This sends those three presses,
        then walks to the neighbouring slot only to read the slot's
        stored bytes back (the dump mirrors the working preset for the
        selected slot), walks back, and compares.  The earlier
        "leaving a slot commits" claim did not hold for a panel edit.
        """
        if not self._gate.armed:
            self._say_to_editor(
                "write gate is locked — press w to arm it before committing",
                refused=True,
            )
            return
        slot = self._slot_of_edit_preset()
        if slot is None:
            self._say_to_editor(
                "this preset's slot is unknown; open the preset list with p "
                "and pick it, then commit",
                refused=True,
            )
            return
        if self._busy:
            self._say_to_editor("busy", refused=True)
            return
        if not self._exchange.acquire(blocking=False):
            self._say_to_editor("busy", refused=True)
            return
        self._say_to_editor(
            "storing... (Enter/Save three times, then reading the slot back)"
        )
        intended = (
            self._edit_preset.to_bytes() if self._edit_preset is not None else None
        )
        self._commit_preset_worker(slot, intended)

    @work(thread=True)
    def _commit_preset_worker(self, slot: int, intended_raw: Optional[bytes]) -> None:
        """**MIDI only** -- Enter/Save three times, then read the slot back."""
        failure: Optional[BaseException] = None
        result = None
        try:
            with self._busy_gate, self._lock:
                if hasattr(self.device, "store_current_edit"):
                    # The manual's store: three Enter/Save presses on the
                    # edit buffer, then walk away only to *read* the slot.
                    result = self.device.store_current_edit(slot=slot)
                else:  # a device double without the panel store
                    result = self.device.commit_current_preset(slot=slot)
        except _BRIDGE_ERRORS as exc:
            failure = exc
        finally:
            self._exchange.release()
        self._call_from_thread(
            self._commit_preset_done, slot, failure, result, intended_raw
        )

    def _cache_slot_raw(self, slot: int, raw: bytes) -> None:
        """Replace one slot's bytes in the cached all-presets dump.

        Updates ``_all_dump`` and its timestamp, and saves the cache file
        with the usual suppressions.  Never indexes ``presets_raw[slot]``;
        the slot-to-index mapping comes from ``slot_offsets()``.
        """
        if self._all_dump is None:
            return
        with contextlib.suppress(Exception):
            offsets = self._all_dump.slot_offsets()
            if slot not in offsets:
                return
            index = offsets[slot]
            new_raw = list(self._all_dump.presets_raw)
            new_raw[index] = raw
            self._all_dump = AllPresetsDump(
                count=self._all_dump.count,
                alloc=self._all_dump.alloc,
                track_table=self._all_dump.track_table,
                track_blobs=self._all_dump.track_blobs,
                preset_table=self._all_dump.preset_table,
                preset_area_start=self._all_dump.preset_area_start,
                presets_raw=tuple(new_raw),
                globals=self._all_dump.globals,
            )
            self._all_dump_at = time.monotonic()
            with contextlib.suppress(ValueError, AttributeError, OSError):
                preset_cache.save(self._all_dump.to_bytes())

    def _commit_preset_done(  # noqa: C901
        self,
        slot: int,
        failure: Optional[BaseException],
        result,
        intended_raw: Optional[bytes] = None,
    ) -> None:
        """Say whether the commit survived the re-read.  Main thread."""
        if failure is not None:
            self._say_to_editor(f"commit P{slot:02d}: {failure}", refused=True)
            return
        if result is None:  # pragma: no cover - the worker always returns one
            self._say_to_editor(f"commit P{slot:02d}: no read-back", refused=True)
            return
        from sq.bridge import (
            COMMIT_KEPT_OFFSETS,
            COMMIT_LIVE_TRACK_BYTES,
            TRACK_PROGRAM_OFFSETS,
        )

        # **The unit's edit buffer is the thing the store saves.**  The
        # app's own model does not know about a change made with the
        # panel's Track keys, so compare the slot with `working_raw` (the
        # buffer read immediately before Enter/Save), not with the model.
        expected = result.working_raw

        def kept_program_offsets(actual: Optional[bytes]) -> List[int]:
            """Per-track program/sustain bytes the slot kept for itself.

            The store restores these from the slot's own bytes after the
            commit (firmware ``$C128FA``), so a difference is the unit's
            value, not a failed write.  Same for the live track byte 8.
            """
            if actual is None or len(actual) != 168:
                return []
            return [
                index
                for index in TRACK_PROGRAM_OFFSETS
                if expected[index] != actual[index]
            ]

        def split(actual: Optional[bytes]) -> Tuple[List[int], List[int]]:
            """Real differences and the unit's own live track bytes.

            The commit copies the live engine's track records, and the
            Track-pair chord writes a track's byte 8 (Status, Pressure,
            channel) there.  A difference in that byte is the unit's live
            value, not a failed write; it is reported, not refused, so a
            commit whose on/off arrangement landed is not called a
            failure over the status word.
            """
            real: List[int] = []
            live: List[int] = []
            if actual is None or len(actual) != 168:
                return real, live
            for index in range(168):
                if index in COMMIT_KEPT_OFFSETS or expected[index] == actual[index]:
                    continue
                (live if index in COMMIT_LIVE_TRACK_BYTES else real).append(index)
            return real, live

        differing, live_stored = split(result.stored_raw)
        # **The dump is not the only witness.**  The slot's entry while
        # the unit was on the neighbour is stored memory, and the preset
        # reloaded after walking back is the same memory read through the
        # unit's own loader.  If the two disagree, the reload is what the
        # panel will work from; a commit that only looks right in the dump
        # is not a commit the user can hear.
        reloaded = getattr(result, "after_raw", None)
        differ_after, live_after = split(reloaded)
        live_kept = sorted(set(live_stored) | set(live_after))
        program_kept = sorted(
            set(kept_program_offsets(result.stored_raw))
            | set(kept_program_offsets(reloaded))
        )
        if not differing and not differ_after:
            # Refresh the cached all-presets dump so the preset browser
            # shows the committed sounds on the next selection.
            if result.stored_raw is not None:
                self._cache_slot_raw(slot, result.stored_raw)
            # **Adopt the slot the unit actually stored.**  A panel edit
            # (the Track keys) is not in the app's model, so without this
            # the next send would carry the old pile/status back.
            if reloaded is not None and len(reloaded) == 168:
                try:
                    stored_preset = Preset.from_bytes(reloaded)
                except ValueError:
                    stored_preset = None
                if stored_preset is not None:
                    self._edit_preset = stored_preset
                    self._edit_preset_raw = reloaded
                    self._edit_snapshot = _copy_preset(stored_preset)
                    self._edit_dirty = False
                    if self._multi_screen is not None:
                        self._multi_screen.set_preset(stored_preset)
                    else:
                        self._redraw_screen(self._active_editor)
            live_note = ""
            if live_kept:
                source = (
                    reloaded
                    if reloaded is not None and len(reloaded) == 168
                    else result.stored_raw
                )
                facts = []
                for offset in live_kept[:3]:
                    track_number = (offset - 40) // 14 + 1
                    byte = source[offset]
                    facts.append(
                        f"track {track_number} status "
                        f"{STATUS_NAMES[(byte >> 6) & 0x03]}, pressure "
                        f"{PRESSURE_NAMES[(byte >> 4) & 0x03]}, channel "
                        f"{(byte & 0x0F) + 1}"
                    )
                live_note = (
                    " The unit kept its own live "
                    + "; ".join(facts)
                    + " at preset offset(s) "
                    + ", ".join(str(offset) for offset in live_kept)
                    + " — that is the track's MIDI routing, not its stored "
                    "on/off."
                )
                if any((source[offset] >> 6) & 0x03 == 3 for offset in live_kept):
                    live_note += " (*EXT* silences internal voices after a reload.)"
            program_note = ""
            if program_kept:
                source = (
                    reloaded
                    if reloaded is not None and len(reloaded) == 168
                    else result.stored_raw
                )
                facts = []
                for offset in program_kept[:4]:
                    track_number = (offset - 41) // 14 + 1
                    byte = source[offset]
                    facts.append(
                        f"track {track_number} program {byte & 0x7F}, "
                        f"sustain {'on' if byte & 0x80 else 'off'}"
                    )
                program_note = (
                    " The unit kept its own per-track program/sustain at "
                    + "; ".join(facts)
                    + " (preset offset(s) "
                    + ", ".join(str(offset) for offset in program_kept)
                    + "); the PC column shows the edit, not what the slot kept."
                )
            self._say_to_editor(
                f"P{slot:02d} stored with Enter/Save three times and verified — "
                f"the slot matches the unit's edit buffer."
                f"{live_note}{program_note}",
                success=True,
            )
            return
        reported = differing or differ_after
        pile_offsets = [index for index in reported if 144 <= index <= 151]
        if pile_offsets:
            source = result.stored_raw if differing else reloaded
            wanted = " ".join(f"{byte:02X}" for byte in expected[144:152])
            kept = " ".join(f"{byte:02X}" for byte in source[144:152])
            self._say_to_editor(
                f"commit P{slot:02d} did not store the track on/off: the app's "
                f"ON list is {wanted}, the unit's pile stayed {kept} at preset "
                f"offset(s) {', '.join(str(i) for i in pile_offsets)}. A dump "
                f"changes the working preset, but the store commits the "
                f"unit's live pile, and the app cannot set that pile; the "
                f"panel's Select Sound/Track/YES flow is the only way. `l` "
                f"changes the live mask, not the stored pile.",
                refused=True,
            )
            return
        where = (
            ", ".join(
                f"{index} (edit 0x{expected[index]:02X}, "
                f"{'stored' if differing else 'reloaded'} 0x"
                f"{(result.stored_raw if differing else reloaded)[index]:02X})"
                for index in (differing or differ_after)[:4]
            )
            or "unknown"
        )
        witness = "stored bytes" if differing else "the reloaded preset"
        self._say_to_editor(
            f"store P{slot:02d} was not verified; {witness} differ at "
            f"preset offset(s) {where} — the unit did not save the edit. "
            f"Check the display (did it reach SAVED?) and try again",
            refused=True,
        )

    @work(thread=True)
    def _store_assist_worker(self, slot: int, preset_raw: bytes) -> None:
        """**MIDI only** -- read, send, verify and retry, as one operation.

        The sequence data is the preset's own, read from the unit now:
        a send is the whole preset, and the backed-up 168 bytes are
        only the half that comes after it.  The sequence read and the
        send share the one exchange lock, because a read landing between
        a send and its verification would be read back as the
        verification's answer.

        ``send_preset_with_retry`` is the guard the receiver needs: the
        read-back is compared byte for byte, and a mismatch asks the
        whole exchange again.  Only the last mismatch escapes, with its
        own offsets.
        """
        failure: Optional[BaseException] = None
        mismatch: Optional[BaseException] = None
        skipped = False
        kept: Tuple[int, ...] = ()
        try:
            with self._busy_gate, self._lock:
                known = self.device.read_current_preset()
                known_slot = known.preset_raw[0] if known.preset_raw else 0
                self.device.select_preset_by_walk(slot, known_slot=known_slot)
                # Read the sequence data *after* the walk: the buffer
                # reloaded from the target slot on the last selection,
                # so this is the slot's own sequence data, which is
                # what a restore of the preset should carry.
                current = self.device.read_current_preset()
                if not preset_needs_write(current.preset_raw, preset_raw):
                    skipped = True
                else:
                    sent = self.device.send_preset_with_retry(
                        current.seq_blob,
                        preset_raw,
                        attempts=STORE_ASSIST_ATTEMPTS,
                        verify=True,
                    )
                    kept = sent.kept_preset_offsets
        except PresetMismatch as exc:
            # Its own case: the unit took the dump and holds something
            # else, and the offsets are the sentence.
            mismatch = exc
        except _BRIDGE_ERRORS as exc:
            failure = exc
        finally:
            self._exchange.release()
        self._call_from_thread(
            self._store_assist_done, slot, failure, mismatch, skipped, kept
        )

    def _store_assist_done(
        self,
        slot: int,
        failure: Optional[BaseException],
        mismatch: Optional[BaseException],
        skipped: bool = False,
        kept: Tuple[int, ...] = (),
    ) -> None:
        """Say what the store assist did.  Main thread."""
        if mismatch is not None:
            self._say_to_editor(
                self._store_assist_mismatch_sentence(slot, mismatch),
                refused=True,
            )
            return
        if failure is not None:
            # The bridge's own sentence: a refusal and a silence each
            # say what happened, and neither is improved by a rewrite.
            self._say_to_editor(f"store assist not sent: {failure}", refused=True)
            return
        if skipped:
            self._say_to_editor(
                f"store assist — P{slot:02d} already matches the backed-up "
                f"bytes in every byte a dump can write; nothing sent. The "
                f"unit's own name and inactive tracks' program bytes are "
                f"left untouched."
            )
            return
        sentence = self._store_assist_sentence(slot)
        if kept:
            sentence += (
                f" The unit kept its own values at preset offset(s) "
                f"{', '.join(map(str, kept))} (name, tempo/effect regions "
                f"or inactive-track programs)."
            )
        self._say_to_editor(sentence)

    def _store_assist_sentence(self, slot: int) -> str:
        """What a verified store assist did, in the measured model's words.

        The send stores into the selected slot -- that is measured, not
        inferred -- but this program cannot read back which slot that
        is, so the sentence names the backed-up preset and says the
        destination is the unit's selection.  The name is the unit's
        own and *Edited Preset* is transient; both are stated because
        both are the parts a user would otherwise wonder about.
        """
        return (
            f"store assist — the backed-up bytes of P{slot:02d} are "
            f"stored in P{slot:02d}. The send is live for the sound's "
            f"levels; the track arrangement takes effect when the preset "
            f"is reloaded — ctrl+r forces that reload remotely. The effect "
            f"block is outside a dump; press ENTER/SAVE on the unit to "
            f"store it too. The unit is left in Preset Select Mode at "
            f"P{slot:02d}; the unit keeps its own name."
        )

    def _store_assist_mismatch_sentence(
        self, slot: int, mismatch: BaseException
    ) -> str:
        """A refusal that names the offsets the final read-back differed at."""
        offsets = getattr(mismatch, "diff_offsets", ())
        where = ", ".join(str(offset) for offset in offsets) or "unknown"
        return (
            f"store assist not sent: after {STORE_ASSIST_ATTEMPTS} tries the "
            f"unit's read-back differs at offset(s) {where} — the backed-up "
            f"bytes of P{slot:02d} are not verified in the selected slot "
            f"P{slot:02d}."
        )

    @work(thread=True)
    def _apply_preset_worker(
        self,
        preset: Preset,
        description: str,
        screen: Optional[EditorScreen],
        sent_raw: bytes,
    ) -> None:
        """**MIDI only** -- the send and its read-back are one operation.

        A write and its verification are not split across threads: a
        read landing between them would be read back as the write's
        result, which is exactly the failure this machinery exists to
        catch.
        """
        failure: Optional[BaseException] = None
        mismatch: Optional[BaseException] = None
        try:
            with self._busy_gate, self._lock:
                self.device.send_preset(
                    self._edit_seq_blob or b"",
                    sent_raw,
                    verify=True,
                )
        except PresetMismatch as exc:
            # Its own case, and its own sentence: the unit took the
            # dump and holds something else.
            mismatch = exc
        except _BRIDGE_ERRORS as exc:
            failure = exc
        # The exchange is released by _change_applied, not here: a
        # coalesced burst keeps the wire between its sends.
        self._call_from_thread(
            self._change_applied,
            preset,
            description,
            screen,
            failure,
            mismatch,
            sent_raw,
        )

    def _change_applied(
        self,
        preset: Preset,
        description: str,
        screen: Optional[EditorScreen],
        failure: Optional[BaseException],
        mismatch: Optional[BaseException],
        sent_raw: bytes,
    ) -> None:
        """What the write path did with a change.  Main thread.

        **The burst ends here.**  A failure stops the burst and releases
        the wire; a success records the undo and then, if a press landed
        while this one was in flight, sends the latest target next --
        still holding the exchange, so no other writer can slip between.
        """
        if failure is not None or mismatch is not None:
            self._apply_inflight = False
            self._apply_target = None
            self._apply_target_description = ""
            self._apply_target_screen = None
            self._exchange.release()
            problem = failure if failure is not None else mismatch
            self._refuse_change(
                screen, preset, description, f"{description} not sent: {problem}"
            )
            return
        # It landed.  The undo record carries the bytes that were there
        # before, so that `z` can put them back: the whole preset, which
        # is the only thing this protocol can send.
        before = self._edit_snapshot
        raw = before.to_bytes() if before is not None else self._edit_preset_raw
        if raw is not None:
            self._undo_log.push(Record(description, (self._edit_seq_blob, raw)))
        self._edit_snapshot = Preset.from_bytes(sent_raw)
        self._edit_dirty = True
        self._tell_screen(
            screen, True, self._store_sentence(description), cell=self._changed_cell()
        )
        # A press that landed during the send: take it now, on the same
        # wire, and only then let go.
        target = self._apply_target
        target_description = self._apply_target_description
        target_screen = self._apply_target_screen
        self._apply_target = None
        self._apply_target_description = ""
        self._apply_target_screen = None
        if target is not None and target.to_bytes() != sent_raw:
            self._apply_preset_worker(
                target, target_description, target_screen, target.to_bytes()
            )
            return
        self._apply_inflight = False
        self._exchange.release()

    def _refuse_change(
        self,
        screen: Optional[EditorScreen],
        preset: Preset,
        description: str,
        message: str,
    ) -> None:
        """Say no, and put the change back.

        **The screen changes the model before it calls**, so that the
        table can show the change at once; a refusal here therefore has
        to undo that.  The model goes back to the snapshot -- which is
        the state before this change and nothing else, because only one
        change is ever in flight -- and the screen is told which cell to
        put back, so that what is on screen is what the unit holds.
        """
        cell = self._changed_cell()
        self._restore_model(self._edit_preset)
        self._tell_screen(screen, False, message, cell=cell)

    def _restore_model(self, preset: Preset) -> None:
        """Put the live model back to the snapshot.

        Only the three parts an edit can touch: the tracks, the pile and
        the effect.  The rest of the preset is not editable here and was
        not changed.
        """
        snapshot = self._edit_snapshot
        if snapshot is None:
            return
        preset.tracks = list(snapshot.tracks)
        preset.pile = snapshot.pile
        preset.effect = snapshot.effect

    def _changed_cell(self) -> Optional[Tuple[int, str]]:
        """Which cell of the track editor changed, as ``(index, column key)``.

        The screen's callback says what changed in words; this works it
        out from the model instead, because the words are for people and
        the cell is what a rollback needs.  The snapshot is the model as
        it was before the change, so the difference between the two *is*
        the change.
        """
        snapshot = self._edit_snapshot
        preset = self._edit_preset
        if snapshot is None or preset is None:
            return None
        for index, (before, after) in enumerate(zip(snapshot.tracks, preset.tracks)):
            if before == after:
                continue
            for field in fields(before):
                if getattr(before, field.name) == getattr(after, field.name):
                    continue
                key = _column_key(field.name)
                if key is not None:
                    return index, key
            # A field with no column of its own -- the sound a track
            # plays is one -- is put back by the app, and the table is
            # drawn again after it.
            return None
        return None

    def _tell_screen(
        self,
        screen: Optional[EditorScreen],
        ok: bool,
        message: str,
        *,
        cell: Optional[Tuple[int, str]] = None,
    ) -> None:
        """Tell the editor what the write path did.

        The screen's own ``result`` is the seam: it puts the cell back
        when the answer is no, and says the sentence on the line the
        user is looking at -- the app's status line is the dimmed
        backdrop behind the modal.  The table is drawn again afterwards,
        because the model is the truth and the table is a picture of it.
        """
        if screen is None:
            self.notify_status(message, refused=not ok)
            return
        if isinstance(screen, MultiScreen):
            screen.result(ok, message, cell=cell)
        else:
            screen.result(ok, message)
        self._redraw_screen(screen)

    def _redraw_screen(self, screen: Optional[EditorScreen]) -> None:
        """Draw the editor again from the model.

        The screens redraw the one cell their own ``result`` knows
        about; the whole table is drawn here, because the model's pile
        and the sound a track plays are not fields and so are not cells
        the screen can put back on its own.
        """
        if screen is None:
            return
        try:
            screen.redraw()
        except Exception:  # pragma: no cover - the screen closed under us
            return

    def _say_to_editor(
        self, message: str, *, refused: bool = False, success: bool = False
    ) -> None:
        """Say one sentence on the open editor's own line.

        The app's status line is behind the modal, so a sentence written
        there is written where nobody is looking.  ``refused`` carries
        the difference between news and a "no" to the screen, which
        marks it the way the rest of the family does; ``success`` marks
        a verified write green.
        """
        screen = self._active_editor
        if screen is None:
            self.notify_status(message, refused=refused, success=success)
            return
        screen.say(message, refused=refused, ok=success)

    def _store_sentence(self, description: str) -> str:
        """What the app says after a change has landed.

        **The send is live.**  The dump lands in the unit's working
        preset -- the Edit Preset buffer -- and that is the preset the
        unit plays from, so the change is heard.  What the bench caught
        is a short blackout: the unit goes briefly deaf to incoming
        MIDI after a send and answers again by about thirty-five
        seconds, by itself (the clean no-op dump measured 32.65 before,
        5.72 at +5 s and 29.72 at +35 s).

        **Storing is a front-panel act**: this protocol has no message
        that stores a preset, so the sentence says so every time, and
        names the slot when the app knows which one the preset came
        from -- which it does after a pick out of the preset list, and
        does not after a read of the unit's own current preset.
        """
        where = ""
        if self._edit_slot is not None:
            where = f" into slot {self._edit_slot + 1}"
        return (
            f"{description} — sent. The send is live for the sound's "
            f"levels and stores the preset's tracks, pile and effect "
            f"parameters into the unit's working preset (its Edit Preset "
            f"buffer). The track arrangement -- which tracks are on -- takes "
            f"effect when the preset is reloaded; ctrl+r forces that reload "
            f"remotely. ctrl+s commits the live parameters by "
            f"re-selecting the slot and verifies the read-back; the unit "
            f"keeps its own preset name{where}."
        )

    def _undo_from_screen(self, count: Optional[int]) -> None:
        """``z`` and ``Z`` inside an editor: the app's undo.

        A modal screen cuts the app's binding chain, so the screens
        serve these keys themselves and call in here -- and this is
        where ``z`` gets pressed, after an edit made in the editor.
        """
        if count is None:
            self.action_undo_all()
        else:
            self.action_undo()

    def _adopt_restored_preset(
        self, records: List[Record], results: List[bool]
    ) -> None:
        """The undo put a preset back: say so in the model.

        The model is what the next edit is built on, so it has to be
        the bytes the unit holds again -- and the screen has to be
        told, because its table is a picture of the model and a picture
        of the old model is a lie.

        **The last send wins.**  The plan is newest first, so the state
        the unit holds now is the payload of the *oldest* record in the
        batch that landed -- and the records are gone from the log by
        then, which is why they are read here and not off the log.
        """
        restored: Optional[bytes] = None
        for record, ok in zip(records, results, strict=True):
            if ok and isinstance(record.payload, tuple):
                restored = record.payload[1]
        if restored is None:
            return
        try:
            preset = Preset.from_bytes(restored)
        except ValueError:
            return
        live = self._edit_preset
        if live is not None:
            live.tracks = list(preset.tracks)
            live.pile = preset.pile
            live.effect = preset.effect
        self._edit_snapshot = _copy_preset(preset)
        self._redraw_screen(self._active_editor)

    # --- the sound picker ---------------------------------------------------

    def _pick_sound(self, index: int, number: int) -> None:
        """The app's sound picker, for the track under the cursor.

        The screens do not own a picker: the app has the catalog and the
        groups, so the app runs it.  The picker offers **every** group --
        Internal, ROM, Card A, Card B and the drum kits -- because a
        multi setup exists to mix sounds across them; the browser's
        current bank is only where it opens.
        """
        preset = self._edit_preset
        if preset is None:
            return
        track = preset.tracks[index]

        def picked(choice: Optional[Tuple[str, int]]) -> None:
            if choice is None:
                return
            group_id, slot_number = choice
            try:
                slot = banks.slot(group_id, slot_number)
            except LookupError:
                return
            self._set_track_sound(index, slot)

        self.push_screen(
            SoundPickerScreen(
                self.catalog,
                current=(track.group, track.slot),
                favorites=self.favorites,
            ),
            picked,
        )

    def _set_track_sound(self, index: int, slot: banks.Slot) -> None:
        """Put a sound on a track, and hand the change to the write path."""
        preset = self._edit_preset
        if preset is None:
            return
        try:
            updated = preset.tracks[index].updated(
                sound_number=sound_word(slot.group_id, slot.number)
            )
        except ValueError as exc:
            self.notify_status(str(exc), refused=True)
            return
        preset.tracks[index] = updated
        name = (
            self.catalog.display_name(slot.bank_id, slot.number) or f"{slot.number:03d}"
        )
        self._apply_multi(preset, f"track {index + 1} sound → {name}")

    # --- scan & rebuild -----------------------------------------------------

    def action_scan_device(self) -> None:
        """Learn the names of every group the unit will not dump.

        Internal is read silently by `r`, one bank dump. ROM, Card A,
        Card B and the drum kits have no dump request at all, so their
        names can only be learned the way a front panel would: select the
        sound, then read the current sound back and take its name. That
        changes what the unit is playing while it runs, so this asks
        first. A second `S` stops it between slots.

        The names are kept in the same sidecar as the Internal ones, so
        the next launch opens on them without touching the unit.
        """
        if self._scanning:
            # A scan is running: this press is the stop.
            self._scan_cancel.set()
            self.notify_status("stopping the scan after the current sound")
            return
        if self._busy:
            self.notify_status("busy", refused=True)
            return

        def start(confirmed: bool | None) -> None:
            if confirmed:
                self._start_scan(None)

        self.push_screen(
            ConfirmScreen(
                "Read the names of ROM, Card A, Card B and the drum kits?\n\n"
                "Those groups have no dump request, so each sound is selected "
                "in turn and its current-sound record is read back. That "
                "changes what the unit is playing while it runs, and takes a "
                "minute or two. Internal is not scanned -- `r` reads it "
                "silently in one message.\n\nPress S again to stop early.",
                confirm="Scan",
                cancel="Cancel",
            ),
            start,
        )

    def _start_scan(self, group_ids: Optional[List[str]]) -> None:
        """Claim the exchange and start a scan, all or selected groups."""
        if self._busy:
            self.notify_status("busy", refused=True)
            return
        if not self._exchange.acquire(blocking=False):
            self.notify_status("busy", refused=True)
            return
        self._scan_cancel.clear()
        self._scanning = True
        self._scan_worker(group_ids)

    def _scan_enter_sound_mode(self) -> None:
        """Put the panel in Select Sound before the scan selects sounds.

        The scan's program changes mean sounds only in that mode; after
        preset work the unit can still be in Select Preset, where the same
        messages select presets and every group reads as stuck.
        """
        if not hasattr(self.device, "tap_button"):
            return
        self.device.tap_button(SELECT_SOUND)
        time.sleep(PRESET_SETTLE)
        self._call_from_thread(self._set_remote_mode, "SOUND")

    def _scan_groups(self, group_ids: Optional[List[str]]) -> List[banks.Group]:
        """The non-dumpable groups a scan should visit."""
        return [
            group
            for group in banks.groups()
            if not group.readable and (group_ids is None or group.id in group_ids)
        ]

    @work(thread=True)
    def _scan_worker(self, group_ids: Optional[List[str]] = None) -> None:  # noqa: C901
        """**MIDI only** -- names are adopted on the main thread.

        One select, one settle, one read per slot. The settle matters: a
        program change is not instantaneous, and reading too early returns
        the previous sound, which would attach every name to the slot
        before the one it belongs to.

        ``group_ids`` narrows a scan to selected groups; ``None`` is the
        full `S` scan.
        """
        groups = self._scan_groups(group_ids)
        total = sum(group.count for group in groups)
        done = 0
        stopped = False
        self._call_from_thread(
            self.notify_progress,
            f"- scanning names: 0/{total} — S stops",
        )
        try:
            with self._busy_gate, self._lock:
                self._scan_enter_sound_mode()
                # A full scan is the moment the user expects every bank to
                # be current.  INT is one fast dump, so read it here as
                # well: a stale INT (from before an import) would otherwise
                # look exactly like the factory ROM set and be mistaken for
                # it.  A one-group `r` scan leaves INT alone.
                if group_ids is None:
                    internal = self._read_internal_for_scan()
                    if internal:
                        self._call_from_thread(self._adopt_scanned, "INT", internal)
                for group in groups:
                    names: Dict[int, str] = {}
                    last_name: Optional[str] = None
                    repeats = 0
                    stuck = False
                    for slot in banks.slots(group.id):
                        if self._scan_cancel.is_set():
                            stopped = True
                            break
                        try:
                            self.device.select(slot)
                            time.sleep(SCAN_SETTLE)
                            record = self.device.read_current_sound()
                            name = msg.sound_name(record).rstrip()
                        except _BRIDGE_ERRORS as exc:
                            self._call_from_thread(
                                self.notify_status,
                                f"scan {slot.display}: {exc}",
                                refused=True,
                            )
                            stopped = True
                            break
                        names[slot.number] = name
                        repeats = repeats + 1 if name == last_name else 1
                        last_name = name
                        done += 1
                        if repeats >= STUCK_SLOT_LIMIT:
                            stuck = True
                            break
                        spinner = "-\\|/"[done % 4]
                        self._call_from_thread(
                            self.notify_progress,
                            f"{spinner} scanning names: {done}/{total} "
                            f"({slot.display}) — S stops",
                        )
                    if stuck:
                        # The selector is not landing. Do not adopt partial
                        # names: they would all describe the same sound.
                        self._call_from_thread(
                            self.notify_status,
                            f"{group.label}: the unit stopped changing sound "
                            f"(no card fitted?); its names were not saved",
                            refused=True,
                        )
                        continue
                    if names:
                        self._call_from_thread(self._adopt_scanned, group.id, names)
                    if stopped:
                        break
        finally:
            self._exchange.release()
            self._call_from_thread(self._finish_scan, done, total, stopped)

    def _finish_scan(self, done: int, total: int, stopped: bool) -> None:
        """Say how the scan ended, and let `S` start another."""
        self._scanning = False
        if stopped:
            self.notify_status(f"scan stopped after {done}/{total} names")
        else:
            self.notify_status(f"scan finished: {done} names read from the unit")

    def _read_internal_for_scan(self) -> Optional[Dict[int, str]]:
        """The fast Internal dump the scan reads beside the slow groups.

        Returns names keyed 1-based, the way the catalog keys them, or
        ``None`` when the dump failed (the scan itself still runs).
        """
        try:
            internal = self.device.read_names()
        except _BRIDGE_ERRORS:
            return None
        return {number + 1: text for number, text in internal.items()}

    def _adopt_scanned(self, group_id: str, names: Dict[int, str]) -> None:
        """Take a scanned group's names, persist them, redraw if showing."""
        for number, text in names.items():
            self.catalog.set_live_name(group_id, number, text)
        self._save_live_names(group_id, names)
        if self.favorites is not None:
            self.favorites.refresh_names(
                lambda bank, number: self.catalog.live_name(bank, number)
            )
        if self._current_bank == group_id:
            self._refill_keeping_place()

    def action_rebuild_catalog(self) -> None:
        """Throw the names away and scan them back. Asks twice.

        Names only: favourites, ratings, tags and notes live in the
        favourites database, keyed by bank and slot, which the catalog
        cannot reach -- so a rebuild loses none of them. Two confirmations
        rather than one because this is the only key that empties the
        catalog, and the second is what a person means when they have read
        the first and still pressed on.
        """

        def rebuild(confirmed: bool | None) -> None:
            if not confirmed:
                return
            self.catalog.clear()
            self._fill(self._current_bank)
            self.notify_status(
                "catalog emptied -- favourites, ratings, tags and notes are untouched"
            )

        def warn(confirmed: bool | None) -> None:
            if not confirmed:
                return
            self.push_screen(
                ConfirmScreen(
                    "Really discard every name?\n\n"
                    "This empties the name catalog, printed and read. "
                    "[b]Favourites, ratings, tags and notes are not "
                    "touched[/b] -- they live in the favourites database, "
                    "not here. After this the sounds show as numbers until "
                    "`S` (or `r` on a group) reads the names back."
                ),
                rebuild,
            )

        self.push_screen(
            ConfirmScreen(
                "Discard the whole name catalog and rebuild it from the "
                "synth?\n\n"
                "Every name is forgotten and can be read back: Internal "
                "silently with `r`, the other groups by scanning one sound "
                "at a time with `S`. Favourites, ratings, tags and notes "
                "are untouched."
            ),
            warn,
        )

    # --- importing ----------------------------------------------------------

    def action_import(self) -> None:
        """``I``: import a whole bank or single sounds from a file.

        The screen that follows is the only thing this key opens; the file
        is not read until the user has typed a path, and nothing reaches
        the wire until the write gate is armed and two confirmations have
        been answered.  The two flows share the gate, the exchange and the
        worker shape; they differ in what is sent.
        """
        self.push_screen(
            ChoicePicker("Import", ["Whole bank", "Single sounds"], 0),
            self._import_kind_chosen,
        )

    def _import_kind_chosen(self, kind: Optional[int]) -> None:
        """The first choice list answered: open the file browser."""
        if kind is None:
            return
        self._import_kind = kind
        self._open_import_picker()

    def _open_import_picker(self) -> None:
        """Open the file browser at the last folder and file row."""
        picker = FilePickerScreen(
            start=self._import_folder or "",
            start_row=self._import_cursor,
        )

        def picked(path: Optional[str]) -> None:
            if path:
                self._import_folder = str(picker.folder)
                self._import_cursor = picker.last_row
                self._save_ui_state(import_folder=self._import_folder)
            self._import_path_chosen(self._import_kind, path)

        self.push_screen(picker, picked)

    def _reopen_import_picker(self) -> None:
        """A preview was cancelled: go back to the file browser."""
        self._open_import_picker()

    def _import_path_chosen(self, kind: int, path: Optional[str]) -> None:
        """Read the file the user picked, or say why it cannot be read."""
        if not path:
            return
        # The callback already remembered the folder and row; keep the
        # fallback for direct callers.
        if not self._import_folder:
            self._import_folder = start_folder(path)
            self._save_ui_state(import_folder=self._import_folder)
        try:
            source = importer.read_import(path)
        except (OSError, ValueError) as exc:
            self.notify_status(f"import: {exc}", refused=True)
            return
        if kind == 1:
            self._import_single_start(source)
        else:
            self._import_bank_start(source)

    # --- whole-bank import --------------------------------------------------

    def _import_bank_start(self, source: importer.ImportSource) -> None:
        """Preview the file; it replaces Internal (RAM)."""
        if not source.is_full_bank:
            self.notify_status(
                f"import: {source.summary()}; the whole-bank flow needs all "
                f"{banks.SOUNDS_PER_GROUP} sounds -- use the single-sound flow "
                f"for a shorter file",
                refused=True,
            )
            return
        self.push_screen(
            BankImportScreen(
                path=source.path,
                summary=source.summary(),
                names=[sound.name for sound in source.sounds],
                gate=self._gate,
            ),
            lambda target: self._bank_target_chosen(source, target),
        )

    def _bank_target_chosen(
        self, source: importer.ImportSource, target: Optional[str]
    ) -> None:
        """The preview answered: ask the first confirmation, gate first."""
        if target is None:
            self._reopen_import_picker()
            return
        if not self._gate.armed:
            self.notify_status(self._gate.refuse("the import"), refused=True)
            return
        self.push_screen(
            ConfirmScreen(
                f"Send {len(source.sounds)} sounds into "
                f"{_IMPORT_LABELS[target]} as one All-Sounds dump?",
                confirm="Send",
                cancel="Cancel",
                danger=True,
            ),
            lambda ok: self._bank_confirm(source, target, ok),
        )

    def _bank_confirm(
        self, source: importer.ImportSource, target: str, ok: Optional[bool]
    ) -> None:
        """The first yes: ask the one that names what is destroyed."""
        if not ok:
            return
        self.push_screen(
            ConfirmScreen(
                f"Send {len(source.sounds)} sounds to "
                f"{_IMPORT_LABELS[target]} now? Measured on this unit, a "
                f"dump loads the edit buffer and does not store the bank — "
                f"the panel's SAVE SOUND is still needed.",
                confirm="Send anyway",
                cancel="Cancel",
                danger=True,
            ),
            lambda ok2: self._bank_go(source, target, ok2),
        )

    def _bank_go(
        self, source: importer.ImportSource, target: str, ok: Optional[bool]
    ) -> None:
        """The second yes: gate, exchange, worker."""
        if not ok or not self._write_allowed_for_import():
            return
        if not self._exchange.acquire(blocking=False):
            self.notify_status("import: busy", refused=True)
            return
        self._wait_screen = WaitScreen(
            f"Sending {len(source.sounds)} sounds to {_IMPORT_LABELS[target]}…"
        )
        self.push_screen(self._wait_screen)
        sounds = {sound.index: sound.raw for sound in source.sounds}
        self._import_worker("bank", target, sounds, source)

    def _write_allowed_for_import(self) -> bool:
        """The checks between the second confirmation and the wire.

        Re-asking the gate here is the point: the confirmations can be
        answered slowly, and ``w`` is a browser key that can lock the gate
        while a modal is open.  The device check names the absence of a
        write path rather than failing later inside a worker.
        """
        if not self._gate.armed:
            self.notify_status(self._gate.refuse("the import"), refused=True)
            return False
        if not hasattr(self.device, "send_sound_bank"):
            self.notify_status(
                "import not sent: this device has no sound-write path",
                refused=True,
            )
            return False
        return True

    # --- single-sound import ------------------------------------------------

    def _import_single_start(self, source: importer.ImportSource) -> None:
        """Read Internal and open the single-sound map.

        Only Internal accepts a SysEx write.  A card is copied from
        Internal on the front panel, so there is no target choice here.
        """
        self._single_bank_chosen(source, 0)

    def _single_bank_chosen(
        self, source: importer.ImportSource, index: Optional[int]
    ) -> None:
        """Read Internal's names, then open the map."""
        if index is None:
            return
        target = _IMPORT_BANKS[index]
        if not hasattr(self.device, "read_bank_names"):
            self.notify_status("import: this device cannot read banks", refused=True)
            return
        if not self._exchange.acquire(blocking=False):
            self.notify_status("import: busy", refused=True)
            return
        # Internal is one bank dump; cover the wait while the worker
        # reads it, then open the map.
        self._wait_screen = WaitScreen(
            f"Reading {_IMPORT_LABELS[target]}… this can take a minute"
        )
        self.push_screen(self._wait_screen)
        self._open_single_worker(source, target)

    def _pop_wait(self) -> None:
        """Dismiss the wait modal, if one is up."""
        screen = self._wait_screen
        self._wait_screen = None
        if screen is None:
            return
        try:
            self.pop_screen()
        except Exception:  # pragma: no cover - already gone
            return

    @work(thread=True)
    def _open_single_worker(self, source: importer.ImportSource, target: str) -> None:
        """**MIDI only** -- read Internal's names for the right pane."""
        failure: Optional[BaseException] = None
        names: Optional[Dict[int, str]] = None
        try:
            with self._busy_gate, self._lock:
                names = self.device.read_bank_names(target)
        except _BRIDGE_ERRORS as exc:
            failure = exc
        finally:
            self._exchange.release()
        self._call_from_thread(self._open_single_done, source, target, names, failure)

    def _open_single_done(
        self,
        source: importer.ImportSource,
        target: str,
        names: Optional[Dict[int, str]],
        failure: Optional[BaseException],
    ) -> None:
        """The bank has been read: open the two-pane map.  Main thread."""
        self._pop_wait()
        if failure is not None:
            self.notify_status(f"import: {failure}", refused=True)
            return
        screen = SingleImportScreen(
            path=source.path,
            summary=source.summary(),
            sounds=[(sound.index, sound.name) for sound in source.sounds],
            target=target,
            listing=sorted((names or {}).items()),
            on_bank_change=self._single_bank_change,
            gate=self._gate,
        )
        self._single_import_screen = screen
        self.push_screen(screen, lambda result: self._single_start(source, result))

    def _single_bank_change(self, target: str) -> None:
        """The screen's ``b``: read the newly chosen bank in a worker."""
        if not self._exchange.acquire(blocking=False):
            screen = self._single_import_screen
            if screen is not None:
                screen.say("import: busy; the bank was not reread", refused=True)
            return
        self._reload_single_worker(target)

    @work(thread=True)
    def _reload_single_worker(self, target: str) -> None:
        """**MIDI only** -- the right pane's fresh read after ``b``."""
        failure: Optional[BaseException] = None
        names: Optional[Dict[int, str]] = None
        try:
            with self._busy_gate, self._lock:
                names = self.device.read_bank_names(target)
        except _BRIDGE_ERRORS as exc:
            failure = exc
        finally:
            self._exchange.release()
        self._call_from_thread(self._reload_single_done, target, names, failure)

    def _reload_single_done(
        self,
        target: str,
        names: Optional[Dict[int, str]],
        failure: Optional[BaseException],
    ) -> None:
        """Hand the fresh listing back to the open map.  Main thread."""
        screen = self._single_import_screen
        if screen is None:
            return
        if failure is not None:
            screen.say(f"read {target}: {failure}", refused=True)
            return
        screen.set_bank(target, sorted((names or {}).items()))

    def _single_start(
        self,
        source: importer.ImportSource,
        result: Optional[Tuple[str, Dict[int, int]]],
    ) -> None:
        """The map answered: turn it into a slot map and ask to write."""
        self._single_import_screen = None
        if result is None:
            self._reopen_import_picker()
            return
        target, assignments = result
        if not assignments:
            return
        by_index = {sound.index: sound.raw for sound in source.sounds}
        writes = {slot: by_index[index] for index, slot in assignments.items()}
        if not self._gate.armed:
            self.notify_status(self._gate.refuse("the import"), refused=True)
            return
        self.push_screen(
            ConfirmScreen(
                f"Write {len(writes)} sound(s) into {_IMPORT_LABELS[target]} at "
                f"slots {', '.join(f'{slot:02d}' for slot in sorted(writes))}?",
                confirm="Write",
                cancel="Cancel",
                danger=True,
            ),
            lambda ok: self._single_confirm(source, target, writes, ok),
        )

    def _single_confirm(
        self,
        source: importer.ImportSource,
        target: str,
        writes: Dict[int, bytes],
        ok: Optional[bool],
    ) -> None:
        """The first yes: ask again, naming the slots that are overwritten."""
        if not ok:
            return
        self.push_screen(
            ConfirmScreen(
                f"Really overwrite those {len(writes)} slot(s) in "
                f"{_IMPORT_LABELS[target]}? This cannot be undone.",
                confirm="Overwrite",
                cancel="Cancel",
                danger=True,
            ),
            lambda ok2: self._single_go(source, target, writes, ok2),
        )

    def _single_go(
        self,
        source: importer.ImportSource,
        target: str,
        writes: Dict[int, bytes],
        ok: Optional[bool],
    ) -> None:
        """The second yes: gate, exchange, worker."""
        if not ok or not self._write_allowed_for_import():
            return
        if not hasattr(self.device, "send_sound_slots"):
            self.notify_status(
                "import not sent: this device has no sound-write path",
                refused=True,
            )
            return
        if not self._exchange.acquire(blocking=False):
            self.notify_status("import: busy", refused=True)
            return
        self._wait_screen = WaitScreen(
            f"Sending {len(writes)} sound(s) to {_IMPORT_LABELS[target]}…"
        )
        self.push_screen(self._wait_screen)
        self._import_worker("single", target, writes, source)

    # --- the one import worker ----------------------------------------------

    @work(thread=True)
    def _import_worker(
        self,
        mode: str,
        target: str,
        writes: Dict[int, bytes],
        source: importer.ImportSource,
    ) -> None:
        """**MIDI only** -- the write, verified where the unit allows it."""
        failure: Optional[BaseException] = None
        result: Any = None
        try:
            with self._busy_gate, self._lock:
                if mode == "bank":
                    result = self.device.send_sound_bank(target, writes)
                else:
                    result = self.device.send_sound_slots(target, writes)
        except _BRIDGE_ERRORS as exc:
            failure = exc
        finally:
            self._exchange.release()
        self._call_from_thread(
            self._import_done, mode, target, writes, source, result, failure
        )

    def _import_done(
        self,
        mode: str,
        target: str,
        writes: Dict[int, bytes],
        source: importer.ImportSource,
        result: Any,
        failure: Optional[BaseException],
    ) -> None:
        """Say what the import did, and reread a single-sound target.

        A bank write is the whole answer: the unit's read-back is the
        verification, and INT is the only bank that can be dumped.  A
        single-sound write reopens the map over a fresh read, because the
        point of that flow is to keep assigning into the same bank.
        """
        if failure is not None:
            self._pop_wait()
            self.notify_status(f"import not sent: {failure}", refused=True)
            return
        verified = getattr(result, "verified", False)
        diffs = getattr(result, "diff_slots", ())
        if mode == "bank":
            self._pop_wait()
            if verified:
                message = (
                    f"imported {len(writes)} sound(s) into "
                    f"{_IMPORT_LABELS[target]} (verified)"
                )
            else:
                message = (
                    f"sent {len(writes)} sound(s) to {_IMPORT_LABELS[target]}; "
                    f"the read-back did not change — the unit did not store "
                    f"the bank (SAVE SOUND is still a panel act)"
                )
            if diffs:
                message += f"; {len(diffs)} slot(s) differ"
            self.notify_status(message)
            self._start_bank_refresh(target)
            return
        if not self._exchange.acquire(blocking=False):
            self._pop_wait()
            self.notify_status(
                f"imported into {_IMPORT_LABELS[target]}; bank busy, not reread",
                refused=True,
            )
            return
        self._reopen_after_import_worker(source, target, verified)

    def _start_bank_refresh(self, target: str) -> None:
        """Reread an imported bank so the browser shows what is there now.

        RAM is one dump; a card is a per-slot scan, so the wait screen is
        pushed before the worker rather than after.
        """
        if not hasattr(self.device, "read_bank_names"):
            return
        if not self._exchange.acquire(blocking=False):
            self.notify_status(
                "import done; the bank is busy, not reread", refused=True
            )
            return
        self._wait_screen = WaitScreen(f"Reading back {_IMPORT_LABELS[target]}…")
        self.push_screen(self._wait_screen)
        self._refresh_bank_worker(target)

    @work(thread=True)
    def _refresh_bank_worker(self, target: str) -> None:
        """**MIDI only** -- the read behind the imported bank's refresh."""
        failure: Optional[BaseException] = None
        names: Optional[Dict[int, str]] = None
        try:
            with self._busy_gate, self._lock:
                names = self.device.read_bank_names(target)
        except _BRIDGE_ERRORS as exc:
            failure = exc
        finally:
            self._exchange.release()
        self._call_from_thread(self._refresh_bank_done, target, names, failure)

    def _refresh_bank_done(
        self,
        target: str,
        names: Optional[Dict[int, str]],
        failure: Optional[BaseException],
    ) -> None:
        """Adopt the freshly read bank into the browser.  Main thread."""
        self._pop_wait()
        if failure is not None:
            self.notify_status(f"import done; reread failed: {failure}", refused=True)
            return
        self._adopt_bank_names(target, names)
        self.notify_status(f"{_IMPORT_LABELS[target]} reread — the browser is current")

    def _adopt_bank_names(self, target: str, names: Optional[Dict[int, str]]) -> None:
        """Make one bank's names the catalog's live names, and redraw.

        ``names`` comes from :meth:`read_bank_names`, whose keys are the
        zero-based slots the dump uses; the catalog keys on the panel's
        one-based number, so the slot is shifted here.
        """
        shifted = {number + 1: name for number, name in (names or {}).items()}
        for number, name in shifted.items():
            self.catalog.set_live_name(target, number, name)
        # Persist what was just adopted: without this the sidecar keeps the
        # pre-import names, and the next launch paints the old bank again.
        self._save_live_names(target, shifted)
        if self.favorites is not None:
            self.favorites.refresh_names(
                lambda bank, number: self.catalog.live_name(bank, number)
            )
        if self._current_bank == target:
            self._refill_keeping_place()
        self._update_subtitle()

    @work(thread=True)
    def _reopen_after_import_worker(
        self, source: importer.ImportSource, target: str, verified: bool
    ) -> None:
        """**MIDI only** -- the fresh read behind the reopened map."""
        failure: Optional[BaseException] = None
        names: Optional[Dict[int, str]] = None
        try:
            with self._busy_gate, self._lock:
                names = self.device.read_bank_names(target)
        except _BRIDGE_ERRORS as exc:
            failure = exc
        finally:
            self._exchange.release()
        self._call_from_thread(
            self._reopen_after_import_done, source, target, names, failure, verified
        )

    def _reopen_after_import_done(
        self,
        source: importer.ImportSource,
        target: str,
        names: Optional[Dict[int, str]],
        failure: Optional[BaseException],
        verified: bool,
    ) -> None:
        """Open the map again over the bank as it now is.  Main thread."""
        self._pop_wait()
        if failure is not None:
            self.notify_status(
                f"imported into {_IMPORT_LABELS[target]}; reread failed: {failure}",
                refused=True,
            )
            return
        self._adopt_bank_names(target, names)
        screen = SingleImportScreen(
            path=source.path,
            summary=source.summary(),
            sounds=[(sound.index, sound.name) for sound in source.sounds],
            target=target,
            listing=sorted((names or {}).items()),
            on_bank_change=self._single_bank_change,
            gate=self._gate,
        )
        self._single_import_screen = screen
        self.push_screen(screen, lambda result: self._single_start(source, result))
        if verified:
            where = f"stored and verified in {_IMPORT_LABELS[target]}"
        else:
            where = (
                f"loaded into the edit buffer, not stored in "
                f"{_IMPORT_LABELS[target]}; the unit's SAVE SOUND is still needed"
            )
        self.notify_status(f"{where}; the pane was reread")

    # --- search -------------------------------------------------------------

    def action_search(self) -> None:
        """Prompt for search term with checkboxes for what to search in."""

        def run(result) -> None:
            if result is None:
                return
            needle, fields = result
            self._perform_search(needle, fields)

        self.push_screen(
            SearchScreen(
                "Search",
                {"names": True, "tags": True, "notes": True},
            ),
            run,
        )

    def _perform_search(self, needle: str, fields: Dict[str, bool]) -> None:
        """Run the search with the given options and show results."""
        hits = self._search_catalog_names(needle, fields)
        if self.favorites and (fields.get("tags") or fields.get("notes")):
            hits.extend(self._search_favorites(needle, fields))
        unique_hits = self._deduplicate_hits(hits)
        self._show_search_results(needle, unique_hits)

    def _search_catalog_names(
        self, needle: str, fields: Dict[str, bool]
    ) -> List[Tuple[str, int, str]]:
        if not fields.get("names"):
            return []
        return [
            (bank_id, number, name)
            for bank_id, number, name, _ in self.catalog.search(needle)
        ]

    def _search_favorites(
        self, needle: str, fields: Dict[str, bool]
    ) -> List[Tuple[str, int, str]]:
        hits = []
        lower = needle.lower()
        for favourite in self.favorites.search(needle):
            if self._favorite_matches(favourite, fields, lower):
                hits.append((favourite.bank_id, favourite.number, favourite.name))
        return hits

    def _favorite_matches(
        self, favourite: Favorite, fields: Dict[str, bool], lower: str
    ) -> bool:
        tags_match = (
            fields.get("tags") is True
            and favourite.tags is not None
            and lower in favourite.tags.lower()
        )
        notes_match = (
            fields.get("notes") is True
            and favourite.note is not None
            and lower in favourite.note.lower()
        )
        return tags_match or notes_match

    def _deduplicate_hits(
        self, hits: List[Tuple[str, int, str]]
    ) -> List[Tuple[str, int, str]]:
        seen = set()
        unique = []
        for bank_id, number, name in hits:
            key = (bank_id, number)
            if key not in seen:
                seen.add(key)
                unique.append((bank_id, number, name))
        return unique

    def _show_search_results(
        self, needle: str, unique_hits: List[Tuple[str, int, str]]
    ) -> None:
        if not unique_hits:
            self.notify_status(f"nothing matching {needle!r}")
            return
        unique_hits.sort(key=lambda row: (row[0], row[1]))
        lines = [f"{len(unique_hits)} match(es) for {needle!r}", ""]
        for bank_id, number, name in unique_hits[:400]:
            try:
                entry = banks.slot(bank_id, number)
            except LookupError:
                continue
            lines.append(f"{entry.display:<6} {name:<12} PC {entry.program_change:>3}")
        if len(unique_hits) > 400:
            lines.append(f"... and {len(unique_hits) - 400} more")
        self.push_screen(ReportScreen("Search", "\n".join(lines)))

    # --- device info --------------------------------------------------------

    def action_device_info(self) -> None:
        """``i``: ask the unit who it is.

        **The exchange is claimed here, not in the worker.**  Every
        other worker takes the lock before it is started and releases it
        in its own ``finally``; the info worker was started without it,
        so its ``finally`` released a lock nobody held -- "release
        unlocked lock" was a crash on the first press of ``i``.
        """
        if not self._exchange.acquire(blocking=False):
            self.notify_status("device info: busy", refused=True)
            return
        self._info_worker()

    @work(thread=True)
    def _info_worker(self) -> None:
        """Ask the synth who it is. **MIDI only** -- see _show_info."""
        failure: BaseException | None = None
        record: bytes | None = None
        try:
            with self._busy_gate, self._lock:
                record = self.device.read_current_sound()
        except _BRIDGE_ERRORS as exc:
            failure = exc
        finally:
            self._exchange.release()
        if failure is not None:
            msg = f"device: {failure}"
            self._call_from_thread(self.notify_status, msg, refused=True)
            return
        self._call_from_thread(self._show_info, record)

    def _show_info(self, record) -> None:
        """Build and show the device report. Main thread only."""
        lines = [
            f"connection      {self.device.description}",
            f"base channel    {self.device.channel + 1}",
            "",
        ]
        lines.append(f"manufacturer    {msg.ENSONIQ_ID:02X}  (Ensoniq)")
        lines.append(f"family          {msg.SQ_FAMILY:02X}  (SQ)")
        lines.append(f"current sound   {msg.sound_name(record).rstrip()!r}")
        lines.append("")
        lines.append("There is no identity request this unit answers and no")
        lines.append("version message. What it will tell you is which sound")
        lines.append("it is on, which is what this asked for.")
        lines.append("")
        lines.append(
            f"catalog         {len(self.catalog)} names"
            + (f" from {self.catalog.source}" if self.catalog.source else "")
        )
        lines.append(
            "favourites      "
            + (
                f"{len(self.favorites)}"
                if self.favorites is not None
                else "(none open)"
            )
        )
        self.push_screen(ReportScreen("Device", "\n".join(lines)))

    # --- help ---------------------------------------------------------------

    def action_help(self) -> None:
        self.push_screen(ReportScreen("ensqsqed", _HELP))


_HELP = """What the SQ-R holds

  Internal  80 sounds in RAM. **The only group that can be read.**
  ROM       80, fixed.
  Card A/B  80 each, on a memory card.
  Drum      20 kits, in ROM, reachable from any group.

340 in all. Eight banks of ten per group, which is what the front
panel's Left/Right cursor steps through.

A program change is not a program change

This instrument has no Bank Select. The group is chosen with a program
change -- 124 Internal, 125 ROM, 126 Card A, 127 Card B -- and every
later program change means whatever that one said, until the next. So
Enter sends the selector first, every time, and says both numbers. A
drum kit takes no selector: its program change is absolute.

The unit shows sounds as 00-79: two digits, counted from zero, which is
also the program change it sends. The slot pane's `#` is the slot's
position, `PC` is that program change, and `sel` is the group selector --
the SQ-R's replacement for the siblings' LSB/MSB columns. A drum kit
shows `—` in `sel`.

Names

`r` reads all eighty internal sounds in one silent message and takes
their names out of it. The same read runs quietly at startup, so a fresh
install shows names rather than blanks; what it reads is kept beside the
catalog for next time. ROM, Card A/B and the drum kits have no bank
dump: `r` on those groups reads them one selected sound at a time and
asks first; `S` does the same for all of them in one run.

Keys

  up/down   move; this never sends anything
  tab       switch pane
  enter     select on the unit -- selector first, then the sound
  r         read this bank's names (Internal: one silent dump; others: scan)
  /         search the names
  [ ]       previous / next MIDI channel
  c         set the base MIDI channel
  f         favourite the slot under the cursor
  v         all slots / favourites here / every favourite
  t n       tags, note
  * V       rating, rating filter
  C         filter by category
  m         the eight tracks of the preset (Multi editor)
  x         the preset's effect (FX editor)
  p         the list of the eighty presets
  I         import a bank or single sounds from a .syx / .mid
  b         back up sounds, presets or everything
  M         toggle the unit: Select Sound / Select Preset
  z Z       undo, undo all
  w         toggle write gate (armed by --allow-write)
  S         learn ROM/Card/Drum names (selects each sound; asks first)
  R         rebuild catalog (discard & re-read)
  i         what the unit is playing
  ?         this screen
  q         quit

The preset editor

  `m` opens the eight-track editor over the preset the unit is on, `x`
  opens the effect editor over the same preset, and `p` reads all
  eighty presets and lets you pick one to edit. Reading is silent; the
  list of eighty takes about twelve seconds and says so.

  A change is sent as a whole preset -- the sequence data and the 168
  bytes of the preset -- because this protocol has no field-level
  write. The unit takes it into its working preset (its Edit Preset
  buffer) and updates the stored slot's tracks, pile and effect
  parameters. The send is live for the sound's levels; the track
  arrangement takes effect when the preset is reloaded -- ctrl+r forces
  that reload remotely. ctrl+s commits the live parameters by
  re-selecting the slot and verifies the read-back; the unit keeps its
  own preset name. The confirmation is green.

  `z` puts the previous preset back, the whole of it. The editors are
  behind the write gate, so arm it with `w` before editing.

Importing sounds

  `I` reads an All-Sounds dump -- a .syx or the .mid that carries the
  same SysEx -- and writes it into Internal (RAM). "Whole bank" previews
  all eighty sounds and replaces Internal after two confirmations.
  "Single sounds" opens a two-pane map: assign a target slot (00-79) to
  any number of the file's sounds, then ctrl+s.

  Every write needs the write gate armed (`w`), and both flows ask twice.
  Internal RAM is read back and verified. Cards are not SysEx write
  targets: import or restore into Internal, then copy to the card on the
  unit's front panel.

Backing up and restoring

  `b` opens the backup screen: choose sound groups and whether to back up
  sounds, presets or all content. The app writes the files under the
  configured backup folder and also copies the local preset labels.

  Restore is the command line, not a key in this window:
  `sqcli restore FILE --yes` writes a backup back; it asks for --yes and
  verifies a preset read-back. A single preset slot can be restored with
  `sqcli send SLOT.syx --yes` after selecting the slot on the unit.
  Sounds restore to Internal only; to put a bank on Card A/B, restore it
  to Internal, back Internal up, then copy on the unit's front panel.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser("ensqsqed", "Browse an Ensoniq SQ-R Plus.")
    add_common_arguments(
        parser,
        port=True,
        scan=True,
        recv_port=True,
        device_channel=True,
        catalog=True,
        config=True,
        favorites=True,
        allow_write=True,
    )
    return parser


def _channel_for_bridge(args, config_path: str) -> int:
    """The base/send channel, 0-15.

    On this single-timbral instrument the unit's base channel and the
    channel a program change goes out on are the same number, so
    ``--device-channel`` wins, ``--channel`` is the fallback, then the
    value remembered from last time. Reading only ``--channel`` here is how
    ``ensqsqed --device-channel 11`` came to sweep for channel 1.
    """
    if args.device_channel is not None:
        return args.device_channel - 1
    if getattr(args, "channel", None) is not None:
        return args.channel - 1
    remembered = cfg.load_channel(config_path)
    return remembered if remembered is not None else 0


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    config_path = getattr(args, "config", None) or cfg.DEFAULT_PATH
    validate_common(args)

    if args.demo:
        from ensqsqed.demo import DemoDevice

        device = DemoDevice()
    else:
        from sq import bridge as b

        try:
            channel = _channel_for_bridge(args, config_path)
            if args.port:
                device = b.SqBridge.open(
                    args.port,
                    recv_port=(args.recv_port or cfg.load_last_recv_port(config_path)),
                    channel=channel,
                )
            else:
                device = b.SqBridge.connect(
                    config_path=config_path,
                    channel=channel,
                    scan=args.scan,
                    on_try=lambda n: print(f"  probing {n}...", file=sys.stderr),
                )
        except Exception as exc:
            sys.exit(f"error: {exc}")

        try:
            cfg.save_channel(getattr(device, "channel", 0), config_path)
            port = getattr(device, "port", None)
            if port:
                cfg.save_last_port(port, config_path)
            recv = getattr(device, "recv_port", None)
            if recv and recv != getattr(device, "port", None):
                cfg.save_last_recv_port(recv, config_path)
        except OSError:
            pass

    try:
        store = Favorites(args.favorites)
    except Exception as exc:
        raise SystemExit(f"error: {exc}")

    # The sidecar travels beside the catalog. A launch without --catalog
    # reads the default catalog and must still keep the names it reads,
    # or every start would ask the unit again.
    from sq import livenames

    catalog_path = args.catalog or cat.DEFAULT_PATH
    live_names_path = livenames.path_for(catalog_path)

    app = EnsqsqedApp(
        device,
        catalog=cat.load(args.catalog),
        favorites=store,
        config_path=config_path,
        read_names_at_startup=True,
        allow_write=bool(getattr(args, "allow_write", False)),
        backup_root=cfg.load_backup_root(config_path),
    )
    app._live_names_path = live_names_path
    try:
        app.run()
    finally:
        store.close()
        device.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
