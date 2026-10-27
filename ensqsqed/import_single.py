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

"""The single-preset import: two panes, and one target slot per source sound.

**The screen is a map, not a write.**  The left pane is the file: every
sound it holds, by its zero-based position (``00``..``79``), then the
``target`` column it is being assigned, then its name.  The target is the
second column and is drawn as a styled cell, because it is the one value
the user is typing and it must not be mistaken for part of the name.
The right pane is the bank the app has just read: the slot numbers the
unit prints and the names it holds.
The user fills in as many targets as they like, and the screen answers
``(group id, {source index: target slot})``.  It opens no port, starts no
worker and holds no device; the app owns the confirmations, the write
gate, the merged bank and the send.

**A target is unique because the bank is.**  Two source sounds cannot
land in one slot: the second send would overwrite the first and the user
would be told they had imported both.  The duplicate is refused while
the assignment is being made, and :func:`sq.importer.validate_assignments`
is asked again when ``ctrl+s`` starts the import, because the map is the
thing that ships and both checks should agree.  Out-of-range targets are
refused the same way, in the same place.

**The bank on the right is Internal (RAM).**  Only Internal accepts a
SysEx write; a card is copied from Internal on the front panel.  The
screen still exposes :meth:`SingleImportScreen.set_bank` for a caller
that has read a bank, but the import path no longer changes target.

**Names are bytes, and a square bracket is not markup.**  Sound names
come off a disk that may hold anything, so every name is escaped on the
way into a table cell and every path or summary is drawn with markup
off.  A corrupt name is still a row somebody has to be able to look at.

``⏎`` on a source row, or a digit typed while the left table has focus,
opens a number prompt for the target slot.  ``w`` toggles the app's write
gate and redraws the gate line, so a locked gate can be armed without
losing the map.  ``ctrl+s`` starts the import, and is refused while the
gate is locked.  ``escape`` and ``q`` answer ``None``.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence, Tuple

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Checkbox, DataTable, Label, Static

from ensqsqed.gate import EditGate
from ensqsqed.multi import NumberPrompt
from sq import banks, importer

__all__ = ["BANK_LABELS", "WRITABLE_BANKS", "SingleImportScreen"]

#: The groups a sound bank can be written into over SysEx.  Only Internal
#: (RAM) accepts one; a card is copied from Internal on the front panel.
#: ``ROM`` and ``DRUM`` are not writable at all, so none is offered.
WRITABLE_BANKS: Tuple[str, ...] = ("INT",)

#: The panel's own words for each writable group.  The screen shows these;
#: the answer is always the group id.
BANK_LABELS: Dict[str, str] = {
    "INT": "Internal (RAM)",
    "CARDA": "Card A",
    "CARDB": "Card B",
}

#: The source pane's table.
SOURCE_TABLE_ID = "source-table"

#: The target bank's table.
TARGET_TABLE_ID = "target-table"

#: The line that names the bank being imported into.
BANK_LABEL_ID = "bank-label"

#: The line that says whether the write gate is armed.
GATE_NOTE_ID = "gate-note"

#: Opt-in: after the edit-buffer import, store each sound permanently with
#: the unit's SAVE SOUND dialog (three Enter/Save presses).
STORE_CHECKBOX_ID = "store-permanently"

#: The target cell's style: the one value the user is typing, drawn so
#: it stands apart from the row it belongs to.
TARGET_STYLE = "bold yellow"

#: What the bottom line says the keys do.
HINT = "⏎ assign · 0-9 type · w gate · ctrl+s start · esc close"


def _literal(text: str) -> str:
    """``text`` as markup that renders as exactly itself.

    A sound's name is bytes the file wrote, and a name holding a square
    bracket would otherwise be read as markup -- a crash in a label and
    a lie in a table cell.  The backslash is Textual's own escape.
    """
    return text.replace("[", "\\[")


class SingleImportScreen(ModalScreen[Optional[Tuple[str, Dict[int, int]]]]):
    """Assign file sounds to slots in one writable bank.

    ``sounds`` is ``(source index, name)`` in file order; the index is the
    key of the assignment map and the number is shown zero-based, as the
    panel prints it.  ``listing`` is the target bank as ``(slot, name)``,
    already read by the app, and ``target`` is the group id that read came
    from.

    ``gate`` is the app's own write gate, held rather than copied so
    ``w`` here toggles the object the write path checks.  When there is
    none, ``ctrl+s`` does not consult one; the app is then the only place
    the gate can be armed.

    The answer is ``(target, assignments)`` with ``assignments`` mapping
    a **source index** to a **target slot**, both zero-based.  The app
    merges that map into the bank it read, so slots the user did not
    touch keep the bank's own sounds.
    """

    DEFAULT_CSS = """
    SingleImportScreen { align: center middle; }
    SingleImportScreen > Vertical {
        width: 100; height: 88%; max-height: 90%;
        border: thick $accent; background: $surface; padding: 1 2;
    }
    SingleImportScreen .title { height: auto; text-style: bold; }
    SingleImportScreen .path { height: auto; color: $text-muted; }
    SingleImportScreen .summary { height: auto; }
    SingleImportScreen Horizontal { height: 1fr; }
    SingleImportScreen #source-pane { width: 46; }
    SingleImportScreen #target-pane { width: 1fr; padding-left: 1; }
    SingleImportScreen .pane-title { height: auto; text-style: bold; }
    SingleImportScreen DataTable { height: 1fr; }
    SingleImportScreen .status { height: auto; color: $text; padding: 0 1; }
    /* The family's alarm, on this screen's own line. */
    SingleImportScreen .status.refused {
        background: $error; color: $text; text-style: bold;
    }
    /* The write gate's state, on this screen's own line. */
    SingleImportScreen .gate { height: auto; color: $text-muted; }
    SingleImportScreen Checkbox { height: auto; }
    SingleImportScreen .hint { height: auto; color: $text-muted; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", "Cancel"),
        # `w` is the app's key, served here because a modal screen cuts
        # the app's binding chain: the gate could otherwise only be armed
        # by closing the map, throwing the assignments away.
        Binding("w", "toggle_gate", "Write gate"),
        Binding("ctrl+s", "start_import", "Start import"),
        # Enter is NOT bound here.  A focused DataTable consumes it for
        # its own row selection, so it arrives as RowSelected instead;
        # see on_data_table_row_selected.
    ]

    # The ten digits, added after the list rather than into it: the list
    # has to keep the type the base class declares.
    BINDINGS += [
        Binding(str(digit), f"assign_digit('{digit}')", "Type", show=False)
        for digit in range(10)
    ]

    def __init__(
        self,
        *,
        path: str,
        summary: str,
        sounds: Sequence[Tuple[int, str]],
        target: str,
        listing: Sequence[Tuple[int, str]],
        on_bank_change: Optional[Callable[[str], None]] = None,
        gate: Optional[EditGate] = None,
    ) -> None:
        super().__init__()
        #: The file the user picked, as it was named to the app.
        self._path = path
        #: One sentence about what the file holds.
        self._summary = summary
        #: ``(source index, name)``, in the order the file holds them.
        self._sounds: List[Tuple[int, str]] = list(sounds)
        #: Names by source index, for the sentences about assignments.
        self._names: Dict[int, str] = dict(self._sounds)
        #: The group id the import will be written into.
        self._target = target
        #: The target bank's rows, as the app last read them.
        self._listing: List[Tuple[int, str]] = list(listing)
        #: ``on_bank_change(group_id)`` -- the app reads the bank and
        #: calls :meth:`set_bank` with fresh rows.  ``None`` means there
        #: is nobody to ask, and ``b`` clears the stale listing instead.
        self._on_bank_change = on_bank_change
        #: The app's own gate, when there is an app.  Held rather than
        #: copied so ``w`` toggles the object the write path checks, and
        #: the note line can be redrawn from it.
        self._gate = gate
        #: The assignment map the screen is building:
        #: source index -> target slot, both zero-based.  A plain dict so
        #: the answer is a copy and the screen keeps its own.
        self._assignments: Dict[int, int] = {}
        #: Opt-in permanent store after the edit-buffer import.  The app
        #: reads this before the screen is gone; default is edit-buffer
        #: only, the non-destructive path.
        self._store_permanently = False

    # --- reading -------------------------------------------------------

    @property
    def target(self) -> str:
        """The group id the screen would answer with now."""
        return self._target

    @property
    def assignments(self) -> Dict[int, int]:
        """A copy of the map the screen has built so far."""
        return dict(self._assignments)

    def _bank_text(self) -> str:
        """The target bank in the panel's own words."""
        label = BANK_LABELS.get(self._target, self._target)
        return f"Target bank: {label}  ·  b changes it"

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        """Remember the permanent-store choice for the app callback."""
        if event.checkbox.id == STORE_CHECKBOX_ID:
            self._store_permanently = bool(event.value)

    @property
    def store_permanently(self) -> bool:
        """Whether the user asked to save the imported sounds permanently."""
        return self._store_permanently

    def _gate_text(self) -> str:
        """The write gate's state, in the family's own words.

        Read from the gate itself, never from a copy: the state can
        change on the app's own keys, and a line that went on saying
        "locked" after ``w`` had armed it would be worse than silence.
        """
        if self._gate is None:
            return "Write gate: not held by this screen"
        return (
            "Write gate: ARMED" if self._gate.armed else "Write gate: locked (press w)"
        )

    def _index_at(self, row: int) -> Optional[int]:
        """The source index of a table row, or ``None``."""
        if not 0 <= row < len(self._sounds):
            return None
        return self._sounds[row][0]

    # --- layout --------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Import single sounds", classes="title")
            # Paths and summaries are data, not markup: a file called
            # ``take[1].syx`` must not lose its bracket or break the
            # screen.
            yield Static(_literal(self._path), classes="path", markup=False)
            yield Static(_literal(self._summary), classes="summary", markup=False)
            with Horizontal():
                with Vertical(id="source-pane"):
                    yield Label("File sounds  ·  # name target", classes="pane-title")
                    yield DataTable(
                        id=SOURCE_TABLE_ID,
                        cursor_type="row",
                        zebra_stripes=True,
                    )
                with Vertical(id="target-pane"):
                    yield Label(
                        self._bank_text(),
                        id=BANK_LABEL_ID,
                        classes="pane-title",
                    )
                    yield DataTable(
                        id=TARGET_TABLE_ID,
                        cursor_type="row",
                        zebra_stripes=True,
                    )
            # The gate's state belongs on this screen: the app's status
            # line is the dimmed backdrop, exactly where nobody looks
            # after `ctrl+s` has been refused.
            yield Static(self._gate_text(), id=GATE_NOTE_ID, classes="gate")
            yield Checkbox(
                "Store permanently (SAVE SOUND x3)",
                value=False,
                id=STORE_CHECKBOX_ID,
            )
            # The screen's own status line.  The app's `#status` is the
            # dimmed backdrop behind this modal, which is exactly where
            # nobody is looking for a refusal.
            yield Static("", classes="status")
            yield Static(HINT, classes="hint", markup=False)

    def on_mount(self) -> None:
        source = self.query_one(f"#{SOURCE_TABLE_ID}", DataTable)
        source.add_column("#", key="num", width=4)
        # The target is the second column: it sits beside the number the
        # user is assigning from, not out past a name of unknown length.
        source.add_column("target", key="target", width=8)
        source.add_column("name", key="name")
        for index, name in self._sounds:
            source.add_row(
                f"{index:02d}",
                Text("--", style=TARGET_STYLE),
                _literal(name),
                key=str(index),
            )
        target = self.query_one(f"#{TARGET_TABLE_ID}", DataTable)
        target.add_column("slot", key="slot", width=6)
        target.add_column("name", key="name")
        self._fill_target()
        source.focus()

    def _fill_target(self) -> None:
        """Draw the right pane from the listing the app last handed in.

        Guarded because a worker can finish after the screen it was
        started from has closed: the answer is the screen's, not the
        widget's, and a closed screen has nothing to redraw.
        """
        try:
            table = self.query_one(f"#{TARGET_TABLE_ID}", DataTable)
        except Exception:  # pragma: no cover - the screen closed under us
            return
        table.clear()
        for slot, name in self._listing:
            table.add_row(f"{slot:02d}", _literal(name), key=str(slot))

    def _show_bank(self) -> None:
        """Redraw the bank label, if the screen is still there."""
        try:
            self.query_one(f"#{BANK_LABEL_ID}", Label).update(self._bank_text())
        except Exception:  # pragma: no cover - the screen closed under us
            return

    def set_bank(self, target: str, listing: Sequence[Tuple[int, str]]) -> None:
        """Adopt a bank the app has just read.

        The app calls this from its reload worker, with the group id it
        read and the rows that came back.  The assignments are **not**
        cleared: they are target slots in the new bank just as they were
        in the old one, and throwing them away would undo the user's
        work because they looked at another bank.
        """
        self._target = target
        self._listing = list(listing)
        self._show_bank()
        self._fill_target()

    # --- assigning a target slot ---------------------------------------

    def on_data_table_row_selected(self, event) -> None:
        """``⏎`` on a source row: ask for its target slot.

        The right pane's rows are not editable here -- they are the
        bank's own listing -- so an Enter there does nothing.  Enter
        arrives as a message rather than a binding because a focused
        DataTable consumes the key for its own row selection.
        """
        if event.data_table.id != SOURCE_TABLE_ID:
            return
        index = self._index_at(event.data_table.cursor_row)
        if index is not None:
            self._ask_target(index)

    def action_assign_digit(self, digit: str) -> None:
        """A digit on the left table starts that slot number.

        The spreadsheet gesture: the number is two keystrokes and a
        dialog for each of them is a step nobody wants.  The digit is
        the prompt's seed, so the number wanted is one keystroke away.
        """
        try:
            table = self.query_one(f"#{SOURCE_TABLE_ID}", DataTable)
        except Exception:  # pragma: no cover - the screen closed under us
            return
        if not table.has_focus:
            return
        index = self._index_at(table.cursor_row)
        if index is None:
            return
        self._ask_target(index, seed=digit)

    def _ask_target(self, index: int, seed: str = "") -> None:
        """The target-slot prompt for one source sound."""

        def done(text: Optional[str]) -> None:
            if text is None or not text.strip():
                return
            self._assign(index, text.strip())

        self.app.push_screen(
            NumberPrompt("target slot (00-79)", seed),
            done,
        )

    def _assign(self, index: int, text: str) -> None:
        """Check one typed target slot and record it.

        The checks are here, before the map changes, so a refusal leaves
        the row exactly as it was.  The duplicate check names the other
        sound, because "already assigned" without saying to what is a
        refusal the user cannot act on.
        """
        name = self._names.get(index, f"source {index:02d}")
        try:
            slot = int(text)
        except ValueError:
            self.say(f"{text!r} is not a number", refused=True)
            return
        if not 0 <= slot < banks.SOUNDS_PER_GROUP:
            self.say(
                f"target slot {slot} is outside 00-{banks.SOUNDS_PER_GROUP - 1:02d}",
                refused=True,
            )
            return
        for other, claimed in self._assignments.items():
            if claimed == slot and other != index:
                other_name = self._names.get(other, f"source {other:02d}")
                self.say(
                    f"target slot {slot:02d} is already assigned to "
                    f"“{_literal(other_name)}” — target slots must be unique",
                    refused=True,
                )
                return
        self._assignments[index] = slot
        self._draw_target(index, slot)
        self.say(f"“{_literal(name)}” → {slot:02d}")

    def _draw_target(self, index: int, slot: int) -> None:
        """Put one assignment in the source table's target cell.

        A ``Text`` rather than a string, so the cell carries its own
        style and stands out from the row's number and name.
        """
        try:
            table = self.query_one(f"#{SOURCE_TABLE_ID}", DataTable)
            table.update_cell(
                str(index),
                "target",
                Text(f"{slot:02d}", style=TARGET_STYLE),
            )
        except Exception:  # pragma: no cover - the screen closed under us
            return

    # --- the bank ------------------------------------------------------

    def action_toggle_gate(self) -> None:
        """``w`` here: the app's own gate, toggled where it is shown.

        The gate is the app's, handed in rather than copied, so this
        toggles the one the write path checks.  The map is untouched:
        arming the gate must not cost the user the assignments they have
        already made.
        """
        if self._gate is None:
            self.say("this screen has no write gate to toggle", refused=True)
            return
        self._gate.toggle()
        self._refresh_gate()
        self.say(self._gate.describe())

    def _refresh_gate(self) -> None:
        """Redraw the gate line, if the screen is still there."""
        try:
            self.query_one(f"#{GATE_NOTE_ID}", Static).update(self._gate_text())
        except Exception:  # pragma: no cover - the screen closed under us
            return

    # --- starting, and closing -----------------------------------------

    def action_start_import(self) -> None:
        """``ctrl+s``: validate the map and answer it, or say why not.

        The write gate is checked first: a map that cannot be sent is not
        worth confirming, and the refusal says which key arms it.  The
        screen stays open on every refusal, so nothing typed is lost.

        The screen validates the shape of the map and stays open on a
        refusal; the app validates the write gate and asks the two
        confirmations after this screen has closed.  An empty map is
        refused here because there is nothing to confirm.
        """
        if self._gate is not None and not self._gate.armed:
            self.say("write gate locked — press w to arm it", refused=True)
            return
        if not self._assignments:
            self.say(
                "assign at least one target slot before starting the import",
                refused=True,
            )
            return
        problems = importer.validate_assignments(self._assignments)
        if problems:
            self.say("; ".join(problems), refused=True)
            return
        self.dismiss((self._target, dict(self._assignments)))

    def action_cancel(self) -> None:
        """``escape`` and ``q``: nothing was assigned."""
        self.dismiss(None)

    def say(self, message: str, *, refused: bool = False) -> None:
        """One sentence on this screen's own status line.

        ``[b]refused[/b] · `` in front of a refusal, because the
        difference between a "no" and news is carried by the words and
        the weight as well as the colour.  Guarded like every other
        update: a prompt callback can outlive the screen.
        """
        try:
            status = self.query_one(".status", Static)
        except Exception:  # pragma: no cover - the screen closed under us
            return
        prefix = "[b]refused[/b] · " if refused else ""
        status.update(prefix + message)
        status.set_class(refused, "refused")
