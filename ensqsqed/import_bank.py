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

"""The whole-bank import preview: what is in the file, and where it goes.

**The screen is a question, not a write.**  It shows the file the user
picked, the one-line summary of what was found, every source sound by
number and name, and the bank the sounds will land in.  It sends
nothing itself -- no device, no worker -- and answers the app with the
chosen group id (or ``None`` when the user backs out).  The app owns the
next step: two confirmations, the exchange and the paced send.  Keeping
the split here is what makes the screen testable without a unit.

**Internal is the only SysEx write target.**  ``ROM``, ``DRUM`` and the
two cards cannot be written over SysEx; a card is copied from Internal
on the front panel.  The panel's own words are used (``Internal (RAM)``)
and the group id is what the screen answers, because that is what the
bridge takes.

**The gate is held, not copied.**  When the app hands its
:class:`~ensqsqed.gate.EditGate` in, this screen toggles that object and
refuses to answer while it is locked, so the key that starts the import
says why it did nothing instead of closing the screen and losing the
user's place.  The app still re-asks the gate before it writes: the gate
can be closed between this screen and the wire.  A screen with no gate
(a unit test, or a caller that checks the gate itself) simply answers.

**A name is bytes the file wrote, and it is never markup.**  A sound
name may contain a square bracket; a table cell rendered as Rich markup
would read it as a tag and either swallow it or crash.  Names are
escaped on the way into the table, the same way :mod:`ensqsqed.multi`
escapes preset names, and the path and summary are drawn with markup
off.  A corrupt name is still a row somebody has to be able to look at.

``w`` toggles the write gate; ``ctrl+s`` answers with Internal as the
target; ``escape`` and ``q`` answer ``None``.  The table scrolls, because
a full bank is eighty rows and a terminal is not.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Label, Static

from ensqsqed.gate import EditGate

__all__ = ["BANK_LABELS", "WRITABLE_BANKS", "BankImportScreen"]

#: The groups a sound bank can be written into over SysEx.  Only Internal
#: (RAM) accepts one; a card is copied from Internal on the front panel.
#: ``ROM`` and ``DRUM`` are not writable at all, so none is offered.
WRITABLE_BANKS: Tuple[str, ...] = ("INT",)

#: The panel's words for each writable group.  The screen shows these;
#: the answer is the group id.
BANK_LABELS: Dict[str, str] = {
    "INT": "Internal (RAM)",
    "CARDA": "Card A",
    "CARDB": "Card B",
}

#: What the bottom line says the keys do.
HINT = "ctrl+s start · esc / q cancel"


def _literal(text: str) -> str:
    """``text`` as markup that renders as exactly itself.

    A sound's name is bytes the file wrote, and a name holding a square
    bracket would otherwise be read as markup -- a crash in a label and
    a lie in a table cell.  The backslash is Textual's own escape.
    """
    return text.replace("[", "\\[")


class BankImportScreen(ModalScreen[Optional[str]]):
    """Preview an All-Sounds file and answer the target bank.

    The file is handed in already read: ``path`` and ``summary`` come
    from :class:`sq.importer.ImportSource`, and ``names`` is one name
    per source sound, in file order.  The screen neither opens the file
    nor talks to a unit.

    ``target`` is accepted for callers that still pass one; anything not
    in :data:`WRITABLE_BANKS` falls back to ``INT``.  There is no target
    choice: Internal is the only SysEx write target.

    ``gate`` is the app's own write gate, if the app hands it over.
    ``w`` toggles it and the note line says which state it is in;
    ``ctrl+s`` answers only while it is armed, and stays on screen with
    a refusal otherwise.  A screen constructed without a gate answers
    when asked, because the caller is the one checking the gate.

    ``ctrl+s`` answers with the group id shown on the target line, and
    ``escape``/``q`` answer ``None``.  ``⏎`` is **not** bound: the
    focused table would swallow it as a row selection, and the start key
    is the family's ``ctrl+s``.
    """

    DEFAULT_CSS = """
    BankImportScreen { align: center middle; }
    BankImportScreen > Vertical {
        width: 88; height: 80%; max-height: 90%;
        border: thick $accent; background: $surface; padding: 1 2;
    }
    BankImportScreen .title { height: auto; text-style: bold; }
    BankImportScreen .loading { height: auto; color: $text-muted; }
    BankImportScreen .path { height: auto; color: $text-muted; }
    BankImportScreen .summary { height: auto; }
    BankImportScreen DataTable { height: 1fr; }
    BankImportScreen .target { height: auto; padding-top: 1; text-style: bold; }
    BankImportScreen .gate { height: auto; text-style: bold; }
    BankImportScreen .note { height: auto; color: $text-muted; }
    BankImportScreen .status { height: auto; color: $text; padding: 0 1; }
    /* The family's alarm, on this screen's own line. */
    BankImportScreen .status.refused {
        background: $error; color: $text; text-style: bold;
    }
    BankImportScreen .hint { height: auto; color: $text-muted; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", "Cancel"),
        Binding("w", "toggle_gate", "Write gate"),
        # ``ctrl+s`` starts the import, like every other screen in the
        # family.  Enter is deliberately absent: a focused DataTable
        # consumes it for its own row selection, so it would look like a
        # start key that sometimes does nothing.
        Binding("ctrl+s", "confirm", "Start import"),
    ]

    #: The table's DOM id: a rule or a query that names one screen's
    #: table must not reach another's.
    TABLE_ID = "import-table"

    #: The target line's DOM id, updated in place when ``b`` answers.
    TARGET_ID = "import-target"

    #: The gate note's DOM id, updated in place when ``w`` is pressed.
    GATE_ID = "import-gate"

    def __init__(
        self,
        *,
        path: str,
        summary: str,
        names: Sequence[str],
        target: str = "INT",
        gate: Optional[EditGate] = None,
    ) -> None:
        super().__init__()
        #: The file the user picked, as it was named to the app.
        self._path = path
        #: One sentence about what the file holds.
        self._summary = summary
        #: One name per source sound, in file order.  A plain list, so a
        #: generator handed in by a caller is read once.
        self._names: List[str] = list(names)
        #: The group id the screen will answer with.  An unknown one is
        #: ignored: this screen only offers writable groups.
        self._target = target if target in WRITABLE_BANKS else "INT"
        #: The app's write gate, held rather than copied so this screen
        #: and the app's write path can never disagree.  ``None`` means
        #: nobody handed one over and this screen does not check it.
        self._gate = gate

    # --- reading -------------------------------------------------------

    @property
    def target(self) -> str:
        """The group id the screen would answer with now."""
        return self._target

    @property
    def gate(self) -> Optional[EditGate]:
        """The gate this screen holds, or ``None``."""
        return self._gate

    def _target_line(self) -> str:
        """The target bank in the panel's own words."""
        return f"Target bank: {BANK_LABELS[self._target]}  ·  b changes it"

    def _gate_line(self) -> str:
        """The gate's state, in the words the family uses.

        Without a gate the line says so instead of inventing a state:
        the app is the one that checks the gate in that case.
        """
        if self._gate is None:
            return "Write gate: not held by this screen"
        if self._gate.armed:
            return "Write gate: ARMED"
        return "Write gate: locked (press w)"

    # --- layout --------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Import whole bank", classes="title")
            # The file was read before this screen opened, so the line
            # goes as soon as the first sound is drawn.  It is here so
            # the screen is not a blank frame that looks stuck.
            yield Static(
                "Loading sounds from file…",
                classes="loading",
                markup=False,
            )
            # Paths and names are data, not markup: a file called
            # ``take[1].syx`` must not lose its bracket or break the
            # screen.  These two draw with markup off, so they take the
            # text verbatim; the table escapes instead, because a cell
            # is rendered as markup.
            yield Static(self._path, classes="path", markup=False)
            yield Static(self._summary, classes="summary", markup=False)
            yield DataTable(
                id=self.TABLE_ID,
                cursor_type="row",
                zebra_stripes=True,
            )
            yield Static(
                self._target_line(),
                id=self.TARGET_ID,
                classes="target",
                markup=False,
            )
            yield Static(
                self._gate_line(),
                id=self.GATE_ID,
                classes="gate",
                markup=False,
            )
            yield Static(
                "Importing loads each sound into the unit's edit buffer. "
                "Measured 2026-10-24: a dump does not store into the bank; "
                "the panel's SAVE SOUND is still needed. You will be asked "
                "to confirm twice.",
                classes="note",
                markup=False,
            )
            # The screen's own status line.  The app's `#status` is the
            # dimmed backdrop behind this modal, which is exactly where
            # nobody is looking for a refusal.
            yield Static("", classes="status")
            yield Static(HINT, classes="hint", markup=False)

    def on_mount(self) -> None:
        table = self.query_one(f"#{self.TABLE_ID}", DataTable)
        table.add_column("#", key="num", width=4)
        table.add_column("name", key="name")
        for index, name in enumerate(self._names):
            # The row number is zero-based, like every slot number the
            # panel prints; the key stays the bare index because that is
            # what the caller's names are addressed by.
            table.add_row(
                f"{index:02d}",
                _literal(name),
                key=str(index),
            )
        table.focus()

    def _show_target(self) -> None:
        """Redraw the target line, if the screen is still there.

        A worker or a dismissed choice list can outlive the screen it
        was opened from, so the lookup is guarded rather than assumed:
        the answer is the screen's, not the widget's.
        """
        try:
            self.query_one(f"#{self.TARGET_ID}", Static).update(self._target_line())
        except Exception:  # pragma: no cover - the screen closed under us
            return

    def _show_gate(self) -> None:
        """Redraw the gate note, if the screen is still there."""
        try:
            self.query_one(f"#{self.GATE_ID}", Static).update(self._gate_line())
        except Exception:  # pragma: no cover - the screen closed under us
            return

    # --- the gate ------------------------------------------------------

    def action_toggle_gate(self) -> None:
        """``w``: flip the write gate this screen holds.

        The object is flipped, not a copy of its state: the app's write
        path checks the same gate, so the note can never describe a
        state the gate is not in.  A screen with no gate says so rather
        than pretending to have flipped something.
        """
        if self._gate is None:
            self.say("this screen has no write gate to toggle", refused=True)
            return
        self._gate.toggle()
        self._show_gate()
        self.say(self._gate.describe())

    # --- answering -----------------------------------------------------

    def action_confirm(self) -> None:
        """``ctrl+s``: answer the target bank, or say why not.

        A locked gate keeps the screen open with the refusal on its own
        status line.  Dismissing would hand the app an answer it is
        about to throw away, and the user would lose the bank they
        chose and the place in the table for a state ``w`` fixes.
        """
        if self._gate is not None and not self._gate.armed:
            self.say("write gate locked — press w to arm it", refused=True)
            return
        self.dismiss(self._target)

    def action_cancel(self) -> None:
        """``escape`` and ``q``: nothing was imported."""
        self.dismiss(None)

    def say(self, message: str, *, refused: bool = False) -> None:
        """One sentence on this screen's own status line.

        ``[b]refused[/b] · `` in front of a refusal, because the
        difference between a "no" and news is carried by the words and
        the weight as well as the colour.  Guarded like every other
        update: a choice list's callback can outlive the screen.
        """
        try:
            status = self.query_one(".status", Static)
        except Exception:  # pragma: no cover - the screen closed under us
            return
        prefix = "[b]refused[/b] · " if refused else ""
        status.update(prefix + message)
        status.set_class(refused, "refused")
