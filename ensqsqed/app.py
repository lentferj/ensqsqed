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

**Only Internal can be read.** One Internal Sound Banks Dump Request
brings back all eighty sounds with their names in 32647 bytes. ROM, Card
A and Card B have no dump request of any kind, so their names can only
come from a catalog, and `r` says so rather than appearing to fail.

**The unit numbers from 1 and MIDI numbers from 0.** The manual says so
itself. The slot pane shows both, because this is the family of bug these
projects exist to avoid.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from dataclasses import fields
from typing import Any, Dict, List, Optional, Tuple

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import DataTable, Header, Input, Static

from ensqsqed.choices import ChoicePicker
from ensqsqed.fx import FxScreen
from ensqsqed.gate import EditGate
from ensqsqed.multi import (
    COLUMN_VIEWS,
    ON_COLUMN,
    MultiScreen,
    PresetListScreen,
    SoundPickerScreen,
)
from sq import banks
from sq import catalog as cat
from sq import config as cfg
from sq import messages as msg
from sq.bridge import AllPresetsDump, DeviceError, PresetMismatch
from sq.favorites import Favorites, Favorite
from sq.presets import Preset, sound_word
from vinsynlib.favorites import rating_cell
from vinsynlib import keys
from vinsynlib.cli import add_common_arguments, make_parser, validate_common
from vinsynlib.keys import wrap_blocks as _wrap_blocks
from vinsynlib.ui.confirm import ConfirmScreen
from vinsynlib.ui.hints import KeyHints
from vinsynlib.ui.modals import (
    CategoryScreen,
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


#: The legend. Every binding the app has, in the order somebody meets them.
#: Built from the family's shared legend rather than hand-written: it used
#: to be a tuple of its own, in its own order. ``keys.legend`` puts this
#: tool's own hints in the one place the family leaves for them -- after
#: `n note`, before `i device` -- and this tool's are the preset editors
#: (``m``, ``x``, ``p``), the scan key, the two rating keys, and the ones
#: the write path adds (edit program, mix, global, mode select, gate,
#: undo, undo all). ``w``/``z``/``Z`` are the family's canonical editor
#: keys, spelled the family's way; this browser legend has no editor
#: tier, so they are carried here as this tool's hints. `v` apart, every
#: other key it binds is a shared one, which `_view_key` renames.
#:
#: The three editor keys come first, the way the sibling nanosyned puts
#: its own edit keys first: they are this tool's headline, and the
#: family leaves the one place for a tool's own hints for exactly that.
WRITE_HINTS: tuple[str, ...] = (
    "m multi",
    "x fx",
    "p preset list",
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
    /* Sized to its content. A fixed width does not truncate the end
       of a label -- the table scrolls sideways and clips the
       *start*, so a bank reads as nonsense rather than as narrow. */
    #bank-table {
        width: auto; min-width: 20; max-width: 52;
        border-right: solid $panel;
    }
    #slot-table { width: 1fr; }
    DataTable { height: 1fr; }
    #detail { height: 3; padding: 0 1; border-top: solid $panel; }
    #status { height: 1; background: $boost; color: $text; padding: 0 1; }
    /* The ID is repeated or the rule never paints: an ID selector outranks
       a class, so a bare ``.refused`` was dead CSS and every refusal drew in
       the same grey as a success. Red and bold, the sibling rxved's shape,
       so a refused action is legible at a glance. */
    #status.refused {
        background: $error;
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
        #: **One live preset per session.**  ``m`` and ``x`` are two
        #: views of the same object, so an edit made in one is an edit in
        #: the other -- and the bytes that go to the unit are the ones
        #: the model holds.
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
        #: The editor screens that are open, so that a worker that
        #: finishes after its screen closed still has somewhere to
        #: report -- and so that the app knows which screen an undo
        #: belongs to.
        self._multi_screen: Optional[MultiScreen] = None
        self._fx_screen: Optional[FxScreen] = None
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
        if self._config_path is not None:
            ui_state = cfg.load_ui_state(self._config_path)
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

    def compose(self) -> ComposeResult:
        yield Header()
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
        # ``sel`` is this instrument's answer to the siblings' LSB/MSB
        # columns: the SQ-R has no Bank Select, so the two numbers that
        # actually select a sound are the group selector (124-127) and the
        # program change. A drum kit has no selector -- its program change
        # is absolute -- and shows "—".
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
        if (
            self._read_names_at_startup
            and applied == 0
            and hasattr(self.device, "read_names")
        ):
            # Only when there are no names yet. A cached sidecar is good
            # enough to paint the first frame; re-reading the whole 10 s
            # bank on every launch would hold the busy gate and refuse the
            # first selection for ten seconds. `r` refreshes on demand.
            self.set_timer(0.3, self._startup_read_names)

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

    def notify_status(self, message: str, *, refused: bool = False) -> None:
        """Say the last thing; a refusal goes red and bold.

        The family's shape (rxved): the widget carries a ``refused`` class
        rather than a text prefix, so a refusal is a red bar the eye cannot
        miss instead of another grey status line.
        """
        self._last_status_refused = refused
        status = self.query_one("#status", Static)
        status.update(message)
        status.set_class(refused, "refused")

    def notify_progress(self, message: str) -> None:
        """Report a background worker's running total, which is not news.

        A tick must not overwrite a refusal: a scan posts one every few
        slots, and a refusal the user keyed in should stay readable.
        """
        if getattr(self, "_last_status_refused", False):
            return
        self.notify_status(message)

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
        self.sub_title = f"{filters}{what}   ·   ch {self._channel() + 1}{editing}"

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
            self.call_from_thread(self.notify_status, msg, refused=True)
            return
        self.call_from_thread(
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
        """Read the highlighted bank's names off the synth, if it has an address.

        Genuinely read-only -- nothing is selected and the instrument keeps
        playing whatever it was playing -- so unlike scan it needs no
        confirmation.

        Named ``read_names`` because that is what the family calls this key:
        ``r`` means *re-read from the device*, and it was bound to
        ``read_bank`` here, which said the same thing in a way no other tool
        in the family used.
        """
        if self._busy:
            self.notify_status("busy", refused=True)
            return
        entry = banks.group(self._current_bank)
        if not entry.readable:
            self.notify_status(
                f"{entry.label} has no dump request of any kind; only "
                f"Internal can be read, and the rest come from a catalog",
                refused=True,
            )
            return
        # Main thread; see action_select for why it is claimed here.
        if not self._exchange.acquire(blocking=False):
            self.notify_status("busy", refused=True)
            return
        self._read_worker(self._current_bank)

    def _startup_read_names(self) -> None:
        """Re-read the Internal bank once, quietly, after the first frame.

        The same silent request `r` makes: one Internal Sound Banks Dump
        Request, no program change. It runs because a fresh install has no
        catalog and a blank name column reads as a broken program; the unit
        can name four fifths of what it holds, so it is asked. Failure is
        silent here -- the user did not press anything -- but a later `r`
        still reports it.
        """
        if self._busy:
            return
        if not self._exchange.acquire(blocking=False):
            return
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
        if failure is not None:
            self.call_from_thread(self.notify_status, f"read: {failure}", refused=True)
            return
        if names is None:
            return
        self.call_from_thread(self._adopt_internal, names)

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
                    str(entry.program_change),
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

    def _save_ui_state(
        self,
        *,
        bank_index: Optional[int] = None,
        slot_cursor: Optional[int] = None,
        view_mode: Optional[str] = None,
        categories: Optional[list] = None,
        rating_stars: Optional[int] = None,
        rating_at_least: Optional[bool] = None,
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
        self.call_from_thread(self._undo_done, records, results)

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
    # **A write reaches the Edit Preset buffer and nothing else.**  The
    # unit displays "Press ENTER to save new preset", and no message in
    # this protocol stores a preset: storing it is the user's front
    # panel act.  Every sentence the app writes after a send says so.

    def action_multi(self) -> None:
        """``m``: the eight tracks of the preset in hand.

        The editor is over one live :class:`sq.presets.Preset`, and the
        session keeps it: ``x`` opens the effect editor over the same
        object, so the two screens are two views of one preset rather
        than two copies of it -- a copy would be a screen that could
        show a value the unit never accepted.
        """
        if self._preset_loaded and self._edit_preset is not None:
            self._push_multi_screen()
            return
        self._read_preset(open_fx=False)

    def action_fx(self) -> None:
        """``x``: the effect of the preset in hand."""
        if self._preset_loaded and self._edit_preset is not None:
            self._push_fx_screen()
            return
        self._read_preset(open_fx=True)

    def action_preset_list(self) -> None:
        """``p``: the eighty presets the unit holds.

        Reading them all is one message and about twelve seconds, so the
        status line says what is happening before it starts rather than
        after: a screen that appears twelve seconds later with no
        explanation is a screen that looks like a hang.
        """
        if self._busy:
            self.notify_status("busy", refused=True)
            return
        # Main thread; see action_select for why it is claimed here.
        if not self._exchange.acquire(blocking=False):
            self.notify_status("busy", refused=True)
            return
        self.notify_status("reading 80 presets (about 12 s, silent)...")
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
            self.call_from_thread(
                self.notify_status, f"preset: {failure}", refused=True
            )
            return
        self.call_from_thread(self._adopt_preset, current, open_fx)

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
        self._edit_slot = None
        self._preset_loaded = True
        self._update_subtitle()
        if open_fx:
            self._push_fx_screen()
        else:
            self._push_multi_screen()

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
            self.call_from_thread(
                self.notify_status, f"presets: {failure}", refused=True
            )
            return
        self.call_from_thread(self._show_preset_list, dump)

    def _show_preset_list(self, dump) -> None:
        """The list of the eighty presets.  Main thread.

        The list is shown when the data has arrived, not before: a modal
        with nothing to do for twelve seconds is a modal that looks
        broken.  The rows carry the unit's own names, read with the
        mute bit cleared and never written back.
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
        screen = PresetListScreen(rows, current=self._edit_slot, progress_note=None)
        self.push_screen(screen, self._preset_picked)

    def _preset_picked(self, slot: Optional[int]) -> None:
        """A preset was picked out of the list: load it into the session.

        **Loading is not writing.**  The unit is not touched: the
        preset is decoded here and the editor is opened over it, and
        nothing reaches the instrument until a change is applied.
        """
        if slot is None:
            return
        dump = self._all_dump
        if dump is None:
            self.notify_status("the preset list was read a long time ago", refused=True)
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
        self._edit_slot = slot
        self._preset_loaded = True
        self._update_subtitle()
        self._push_multi_screen()
        self.notify_status(
            f"preset {slot + 1} loaded into the editor — the unit is "
            f"untouched until a change is applied"
        )

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
        )
        self._multi_screen = screen
        self.push_screen(screen, self._editor_closed)

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
            allow_write=self._gate.armed,
            gate=self._gate,
        )
        self._fx_screen = screen
        self.push_screen(screen, self._editor_closed)

    def _editor_closed(self, _result: Any = None) -> None:
        """An editor screen was closed."""
        self._multi_screen = None
        self._fx_screen = None

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
        if self._busy:
            self._refuse_change(screen, preset, description, "busy")
            return
        # Main thread; see action_select for why it is claimed here.
        if not self._exchange.acquire(blocking=False):
            self._refuse_change(screen, preset, description, "busy")
            return
        self._apply_preset_worker(preset, description, screen)

    @work(thread=True)
    def _apply_preset_worker(
        self, preset: Preset, description: str, screen: Optional[EditorScreen]
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
                    preset.to_bytes(),
                    verify=True,
                )
        except PresetMismatch as exc:
            # Its own case, and its own sentence: the unit took the
            # dump and holds something else.
            mismatch = exc
        except _BRIDGE_ERRORS as exc:
            failure = exc
        finally:
            self._exchange.release()
        self.call_from_thread(
            self._change_applied, preset, description, screen, failure, mismatch
        )

    def _change_applied(
        self,
        preset: Preset,
        description: str,
        screen: Optional[EditorScreen],
        failure: Optional[BaseException],
        mismatch: Optional[BaseException],
    ) -> None:
        """What the write path did with a change.  Main thread."""
        if failure is not None:
            self._refuse_change(
                screen, preset, description, f"{description} not sent: {failure}"
            )
            return
        if mismatch is not None:
            self._refuse_change(
                screen, preset, description, f"{description} not sent: {mismatch}"
            )
            return
        # It landed.  The undo record carries the bytes that were there
        # before, so that `z` can put them back: the whole preset, which
        # is the only thing this protocol can send.
        before = self._edit_snapshot
        raw = before.to_bytes() if before is not None else self._edit_preset_raw
        if raw is not None:
            self._undo_log.push(Record(description, (self._edit_seq_blob, raw)))
        self._edit_snapshot = _copy_preset(preset)
        self._tell_screen(screen, True, self._store_sentence(description))

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
        self._restore_model(preset)
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
        if snapshot.pile != preset.pile:
            # The pile says which tracks are on; the byte that changed
            # names the track it was about.
            for position, (old, new) in enumerate(zip(snapshot.pile, preset.pile)):
                if old != new:
                    return position, ON_COLUMN.key
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

    def _say_to_editor(self, message: str, *, refused: bool = False) -> None:
        """Say one sentence on the open editor's own line.

        The app's status line is behind the modal, so a sentence written
        there is written where nobody is looking.  ``refused`` carries
        the difference between news and a "no" to the screen, which
        marks it the way the rest of the family does.
        """
        screen = self._multi_screen or self._fx_screen
        if screen is None:
            self.notify_status(message, refused=refused)
            return
        screen.say(message, refused=refused)

    def _store_sentence(self, description: str) -> str:
        """What the app says after a change has landed.

        **The unit is holding the change in its Edit Preset buffer**, and
        storing it is a front-panel act: this protocol has no message
        that stores a preset.  So the sentence says so every time, and
        names the slot when the app knows which one the preset came
        from -- which it does after a pick out of the preset list, and
        does not after a read of the unit's own current preset.
        """
        where = ""
        if self._edit_slot is not None:
            where = f" into slot {self._edit_slot + 1}"
        return (
            f"{description} — sent. The unit holds it in the Edit Preset "
            f"buffer and does not play incoming MIDI while that is open; "
            f"press ENTER/SAVE to store it{where}, then leave Edit mode on "
            f"the unit to hear it."
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
        self._redraw_screen(self._multi_screen or self._fx_screen)

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
            if not confirmed:
                return
            if self._busy:
                self.notify_status("busy", refused=True)
                return
            if not self._exchange.acquire(blocking=False):
                self.notify_status("busy", refused=True)
                return
            self._scan_cancel.clear()
            self._scanning = True
            self._scan_worker()

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

    @work(thread=True)
    def _scan_worker(self) -> None:
        """**MIDI only** -- names are adopted on the main thread.

        One select, one settle, one read per slot. The settle matters: a
        program change is not instantaneous, and reading too early returns
        the previous sound, which would attach every name to the slot
        before the one it belongs to.
        """
        groups = [group for group in banks.groups() if not group.readable]
        total = sum(group.count for group in groups)
        done = 0
        stopped = False
        try:
            with self._busy_gate, self._lock:
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
                            self.call_from_thread(
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
                        if done % 5 == 0 or done == total:
                            self.call_from_thread(
                                self.notify_progress,
                                f"scanning names: {done}/{total} ({slot.display})",
                            )
                    if stuck:
                        # The selector is not landing. Do not adopt partial
                        # names: they would all describe the same sound.
                        self.call_from_thread(
                            self.notify_status,
                            f"{group.label}: the unit stopped changing sound "
                            f"(no card fitted?); its names were not saved",
                            refused=True,
                        )
                        continue
                    if names:
                        self.call_from_thread(self._adopt_scanned, group.id, names)
                    if stopped:
                        break
        finally:
            self._exchange.release()
            self.call_from_thread(self._finish_scan, done, total, stopped)

    def _finish_scan(self, done: int, total: int, stopped: bool) -> None:
        """Say how the scan ended, and let `S` start another."""
        self._scanning = False
        if stopped:
            self.notify_status(f"scan stopped after {done}/{total} names")
        else:
            self.notify_status(f"scan finished: {done} names read from the unit")

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
                    "the scan that follows puts names back."
                ),
                rebuild,
            )

        self.push_screen(
            ConfirmScreen(
                "Discard the whole name catalog and rebuild it from the "
                "synth?\n\n"
                "Every name is forgotten and read back: Internal silently, "
                "and Card/ROM banks have no dump request so their names "
                "cannot be recovered. Favourites, ratings, tags and notes "
                "are untouched."
            ),
            warn,
        )

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
                search_names=True,
                search_tags=True,
                search_notes=True,
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
            self.call_from_thread(self.notify_status, msg, refused=True)
            return
        self.call_from_thread(self._show_info, record)

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

The unit shows sounds as 001-080 and MIDI sends 000-079. The slot pane
shows both, plus the group selector in `sel` -- the SQ-R's replacement
for the siblings' LSB/MSB columns. A drum kit shows `—` there.

Names

`r` reads all eighty internal sounds in one silent message and takes
their names out of it. The same read runs quietly at startup, so a fresh
install shows names rather than blanks; what it reads is kept beside the
catalog for next time. The other three groups have no dump request of
any kind, so their names come from a catalog or not at all.

Keys

  up/down   move; this never sends anything
  tab       switch pane
  enter     select on the unit -- selector first, then the sound
  r         read the internal names (silent, one message)
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
  write. The unit takes it into its Edit Preset buffer and its display
  says "Press ENTER to save new preset": **storing it is a front panel
  act**, and nothing this program does stores a preset.

  `z` puts the previous preset back, the whole of it. The editors are
  behind the write gate, so arm it with `w` before editing.
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
