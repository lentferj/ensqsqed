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
from typing import Dict, List, Optional

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Header, Input, Label, Static

from sq import banks
from sq import catalog as cat
from sq import messages as msg


__all__ = ["EnsqsqedApp", "main", "build_parser", "KEY_HINTS", "wrap_blocks"]

_SEP = " · "

#: What `F` steps through. The same three the sibling rxved and p2ked use,
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

KEY_HINTS = (
    "↑↓ move",
    "tab pane",
    "⏎ select on the unit",
    "r read names",
    "/ search",
    "[ ] channel",
    "c set channel",
    "f favourite",
    "F favourites view",
    "t tags",
    "n note",
    "i device",
    "? help",
    "q quit",
)


def wrap_blocks(blocks, width: int, sep: str = _SEP) -> str:
    """Pack hints into lines no wider than ``width``.

    Ported from the siblings. Breaks happen only between blocks, so a hint
    is never split mid-label. The point is that the legend wraps instead of
    truncating: a footer that silently drops its last keys teaches the user
    those keys do not exist.
    """
    lines, current = [], ""
    for block in blocks:
        candidate = block if not current else current + sep + block
        if width and len(candidate) > width and current:
            lines.append(current)
            current = block
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines)


class KeyHints(Static):
    """The legend, wrapped to the window rather than truncated."""

    DEFAULT_CSS = "KeyHints { height: auto; background: $panel; padding: 0 1; }"

    def on_mount(self) -> None:
        self._render_hints()

    def on_resize(self) -> None:
        self._render_hints()

    def _render_hints(self) -> None:
        # NOT named _render: Widget._render is Textual's own, and shadowing
        # it makes every layout pass ask a None for its height.
        self.update(wrap_blocks(KEY_HINTS, max(self.size.width - 2, 20)))


class ReportScreen(ModalScreen[None]):
    """A scrollable block of text."""

    DEFAULT_CSS = """
    ReportScreen { align: center middle; }
    ReportScreen > Vertical {
        width: 84; height: 80%; border: thick $accent;
        background: $surface; padding: 1 2;
    }
    ReportScreen .body { height: 1fr; overflow-y: auto; }
    """

    BINDINGS = [Binding("escape", "close", "Close"), Binding("q", "close", "Close")]

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self._title = title
        self._body = body

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(f"[b]{self._title}[/b]")
            yield Static(self._body, classes="body")
            yield Static("[dim]esc / q to close[/dim]")

    def action_close(self) -> None:
        self.dismiss(None)


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
        with Vertical():
            yield Label(self._prompt)
            yield Input(value=self._current, id="value")

    def on_mount(self) -> None:
        self.query_one("#value", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


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
    """

    BINDINGS = [
        # Enter is NOT bound here: a focused DataTable consumes it for its
        # own row selection, so it arrives as RowSelected instead.
        Binding("r", "read_names", "Read names"),
        Binding("left_square_bracket", "channel_down", "Channel -"),
        Binding("right_square_bracket", "channel_up", "Channel +"),
        Binding("c", "pick_channel", "Set channel"),
        Binding("f", "toggle_favorite", "Favourite"),
        Binding("F", "cycle_favorites", "Favourites view"),
        Binding("t", "edit_tags", "Tags"),
        Binding("n", "edit_note", "Note"),
        Binding("slash", "search", "Search"),
        Binding("i", "device_info", "Device"),
        Binding("question_mark", "help", "Help"),
        Binding("tab", "switch_pane", "Switch pane"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self, device, *, catalog=None, favorites=None, config_path: Optional[str] = None
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
        self.view_mode = VIEW_ALL
        self._lock = threading.RLock()
        self._bank_ids: List[str] = banks.group_ids()
        self._current_bank = self._bank_ids[0]
        self._current: List[banks.Slot] = []
        self._busy = False
        self._filling = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="panes"):
            yield DataTable(id="bank-table", cursor_type="row")
            yield DataTable(id="slot-table", cursor_type="row")
        yield Static("", id="detail")
        yield Static("", id="status")
        yield KeyHints()

    def on_mount(self) -> None:
        self.title = "ensqsqed"
        table = self.query_one("#bank-table", DataTable)
        for label, key in (("group", "bank"), ("n", "n"), ("", "src"), ("♥", "fav")):
            table.add_column(label, key=key)
        for bank_id in self._bank_ids:
            entry = banks.group(bank_id)
            marked = len(self._favorite_keys(bank_id))
            table.add_row(
                entry.label,
                str(entry.count),
                "" if entry.readable else "rom",
                # blank rather than "0": a column of zeroes is
                # noise, and the eye wants the few that differ
                str(marked) if marked else "",
                key=bank_id,
            )

        slots = self.query_one("#slot-table", DataTable)
        # The favourite marker leads the row rather than trailing it: it is
        # a gutter mark the eye runs down, and gluing it to the name would
        # carry it into anything that sorts or searches.
        for label, key in (("♥", "fav"), ("#", "num"), ("name", "name"), ("PC", "pc")):
            slots.add_column(label, key=key)
        self._fill(self._current_bank)
        table.focus()
        self.notify_status(
            f"{len(self.catalog)} names from the catalog"
            if self.catalog
            else "no catalog -- numbers only. Build one with "
            "tools/extract_catalog.py, or press r to read from the unit."
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
            return self._all_favourites()
        entries = banks.slots(bank_id)
        if self.view_mode == VIEW_BANK_FAVOURITES:
            marked = self._favorite_keys(bank_id)
            entries = [e for e in entries if e.number in marked]
        return entries

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
        self.notify_status(f"sending on MIDI channel {channel + 1}")

    def _remember_channel(self, channel: int) -> None:
        """Save it for next time. Only OSError is swallowed."""
        path = self._config_path
        if path is None:
            return
        from sq import config

        try:
            config.save_channel(channel, path)
        except OSError:
            pass

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
        now = self.favorites.toggle(bank_id, entry.number, name=name)
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
            self.favorites.set_tags(where[0], where[1], value)
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
            self.favorites.set_note(where[0], where[1], value)
            self._refill_keeping_place()
            self.notify_status("note set")

        self.push_screen(PromptScreen("Note:", existing.note), apply)

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
        self._refill_keeping_place()
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

    def _refill_keeping_place(self) -> None:
        table = self.query_one("#slot-table", DataTable)
        row = table.cursor_row or 0
        self._fill(self._current_bank)
        if self._current:
            table.move_cursor(row=min(row, len(self._current) - 1))

    def _fill(self, bank_id: str) -> None:
        table = self.query_one("#slot-table", DataTable)
        self._current_bank = bank_id
        self._current = self._visible(bank_id)
        marked = self._favorite_keys(bank_id)
        self._filling = True
        try:
            table.clear()
            for entry in self._current:
                name = self.catalog.display_name(entry.bank_id, entry.number)
                if self.catalog.differs(entry.bank_id, entry.number):
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
                    str(entry.program_change),
                    key=entry.key,
                )
        finally:
            self._filling = False
        self._detail(0)

    def _detail(self, row: int) -> None:
        if not self._current or row >= len(self._current):
            self.query_one("#detail", Static).update("")
            return
        entry = self._current[row]
        name = self.catalog.display_name(entry.bank_id, entry.number)
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
        self.query_one("#detail", Static).update(
            f"[b]{entry.display}[/b]  {name}   [dim]bank {entry.bank}[/dim]\n"
            f"{how} · {source}"
        )

    def notify_status(self, message: str, *, refused: bool = False) -> None:
        prefix = "[b]refused[/b] · " if refused else ""
        self.query_one("#status", Static).update(prefix + message)

    # --- navigation ---------------------------------------------------------

    def on_data_table_row_highlighted(self, event) -> None:
        if self._filling:
            return
        if event.data_table.id == "bank-table":
            row = event.cursor_row
            if 0 <= row < len(self._bank_ids):
                self._fill(self._bank_ids[row])
        elif event.data_table.id == "slot-table":
            self._detail(event.cursor_row)

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
        entry = self._current[row]
        # Set here, not in the worker. A @work(thread=True) method returns
        # at once and would set the flag on its own thread, so two quick
        # presses both passed this check before either worker ran.
        self._busy = True
        self._select_worker(entry)

    @work(thread=True)
    def _select_worker(self, entry: banks.Slot) -> None:
        """**MIDI only.** The caller set ``_busy``; this clears it."""
        try:
            with self._lock:
                self.device.select(entry)
        except Exception as exc:
            self.call_from_thread(self.notify_status, f"select: {exc}", refused=True)
            return
        finally:
            self._busy = False
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
        self._busy = True  # main thread; see action_select
        self._read_worker()

    @work(thread=True)
    def _read_worker(self) -> None:
        """**MIDI only** -- the catalog is updated on the main thread.

        One request, one 32647-byte reply, eighty sounds with their names
        inside it. The caller set ``_busy``; this clears it.
        """
        try:
            self.call_from_thread(
                self.notify_status,
                "asking for all eighty internal sounds in one message (silent)...",
            )
            with self._lock:
                names = self.device.read_names()
        except Exception as exc:
            self.call_from_thread(self.notify_status, f"read: {exc}", refused=True)
            return
        finally:
            self._busy = False
        self.call_from_thread(self._adopt_internal, names)

    def _adopt_internal(self, names) -> None:
        for number, text in names.items():
            # The dump numbers sounds from 0; the unit shows them from 1,
            # and the catalog keys on what the unit shows.
            self.catalog.set_live_name("INT", number + 1, text.rstrip())
        if self.favorites is not None:
            self.favorites.refresh_names(
                lambda bank, number: self.catalog.live_name(bank, number)
            )
        self._fill(self._current_bank)
        differing = sum(
            1 for number in names if self.catalog.differs("INT", number + 1)
        )
        self.notify_status(
            f"read {len(names)} internal name(s)"
            + (f"; {differing} differ from the catalog" if differing else "")
        )

    def action_search(self) -> None:
        def done(text) -> None:
            if not text:
                return
            hits = self.catalog.search(text)
            if not hits:
                self.notify_status(f"nothing matching {text!r}")
                return
            lines = [f"{len(hits)} match(es) for {text!r}", ""]
            for bank_id, program_change, name in hits[:400]:
                try:
                    entry = banks.slot(bank_id, program_change)
                except LookupError:
                    continue
                lines.append(f"{entry.display:<6} {name:<12} PC {entry.number:>3}")
            if len(hits) > 400:
                lines.append(f"... and {len(hits) - 400} more")
            self.push_screen(ReportScreen("Search", "\n".join(lines)))

        self.push_screen(PromptScreen("Search names:"), done)

    def action_device_info(self) -> None:
        self._info_worker()

    @work(thread=True)
    def _info_worker(self) -> None:
        """**MIDI only** -- the report is built on the main thread."""
        try:
            with self._lock:
                version = self.device.read_current_sound()
                configuration = None
        except Exception as exc:
            self.call_from_thread(self.notify_status, f"device: {exc}", refused=True)
            return
        self.call_from_thread(self._show_info, version, configuration)

    def _show_info(self, record, _unused) -> None:
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
        lines.append("The base channel is in every message. A request")
        lines.append("carrying the wrong one is ignored in silence, which")
        lines.append("looks exactly like a wrong cable.")
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
shows both.

Names

`r` reads all eighty internal sounds in one silent message and takes
their names out of it. The other three groups have no dump request of
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
  F         all slots / favourites here / every favourite
  t n       tags, note
  i         what the unit is playing
  q         quit
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ensqsqed", description="Browse an Ensoniq SQ-R Plus."
    )
    parser.add_argument("--port")
    parser.add_argument("--recv-port")
    parser.add_argument(
        "--demo", action="store_true", help="built-in demo device; opens no MIDI ports"
    )
    parser.add_argument(
        "--midi-channel", type=int, default=None, help="the unit's global channel, 1-16"
    )
    parser.add_argument("--catalog", default=None)
    parser.add_argument(
        "--config", default=None, help="settings cache (default: ./config.toml)"
    )
    parser.add_argument(
        "--favorites",
        default=None,
        help="favourites database (default: the per-platform data directory)",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    from sq import config as cfg

    config_path = getattr(args, "config", None) or cfg.DEFAULT_PATH
    if args.midi_channel is not None and not 1 <= args.midi_channel <= 16:
        sys.exit("error: --midi-channel is 1-16")

    if args.demo:
        from ensqsqed.demo import DemoDevice

        device = DemoDevice()
    else:
        from sq import bridge as b

        # The channel on the command line wins; otherwise the one used last
        # time, so it does not have to be given again every run.
        remembered = cfg.load_channel(config_path)
        try:
            channel = (
                (args.midi_channel - 1)
                if args.midi_channel
                else (remembered if remembered is not None else 0)
            )
            if args.port:
                device = b.SqBridge.open(
                    args.port,
                    recv_port=(args.recv_port or cfg.load_last_recv_port(config_path)),
                    channel=channel,
                )
            else:
                device = b.SqBridge.autodetect(
                    on_try=lambda n: print(f"  probing {n}...", file=sys.stderr)
                )
        except Exception as exc:
            sys.exit(f"error: {exc}")

        # Remember whatever channel is actually in use -- which after an
        # autodetect is the one the unit reported, not the one asked for.
        try:
            cfg.save_channel(getattr(device, "channel", 0), config_path)
            if getattr(device, "port", None):
                cfg.save_last_port(device.port, config_path)
            recv = getattr(device, "recv_port", None)
            if recv and recv != getattr(device, "port", None):
                cfg.save_last_recv_port(recv, config_path)
        except OSError:
            pass

    from sq.favorites import Favorites

    try:
        store = Favorites(args.favorites)
    except Exception as exc:
        # _migrate refuses a database written by a newer build, which is the
        # right refusal, and sqlite3 raises on a file that is not a database
        # at all. Every other startup failure here leaves by sys.exit; these
        # two used to print a traceback.
        raise SystemExit(f"error: {exc}")
    app = EnsqsqedApp(
        device, catalog=cat.load(args.catalog), favorites=store, config_path=config_path
    )
    try:
        app.run()
    finally:
        store.close()
        device.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
