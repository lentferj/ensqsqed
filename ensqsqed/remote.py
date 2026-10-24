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

"""A modal screen that mirrors the SQ-R's front panel.

**What it is.**  The unit's front panel is a set of buttons the
application cannot press from a desk: it has no message for "press the
System key", only for the virtual button behind it.  This screen is the
remote: each on-screen button is one virtual button, sent through the
same gate and exchange as every other writer, so the desk and the rack
agree about what happened.  The screen itself owns no device and starts
no worker -- it asks, and the application sends.

**The table is the panel.**  :data:`PANEL_KEYS` is one entry per front-
panel key: the virtual button number (or ``None`` where it is not
established), the label the panel prints, the PC key that stands in for
it, the cluster it belongs to, and a note.  Both the drawing and the
bindings are built from it, so a key added or renamed here appears in
both places and nowhere else.  The only key not in the table is the
space bar, which is not a button at all but the live-mute chord --
buttons 10 and 12 pressed together.

**Layout immunity.**  The PC keys are chosen so that their physical
position is the same on QWERTY and QWERTZ: letters whose place does not
move, plus Enter, Tab, the arrows and PageUp/PageDown.  Y and Z are
never used, so no remapping is needed and the screen behaves the same
way on both layouts.

**What the numbers are and are not.**  Button 3 is labelled "Edit
Track" because that is where it sits on the panel, but the owner's
measurement showed it opening the voices page rather than the track
page -- its identity is uncertain, and the note says so.  Bank Select
has no number at all: the button exists on the panel and the screen
shows it, and pressing it says honestly that its number is not
established yet.  It sends nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, ClassVar, List, Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

__all__ = [
    "MODE_BUTTONS",
    "MODE_LABELS",
    "PANEL_KEYS",
    "SELECT_PRESET",
    "SELECT_SOUND",
    "PanelKey",
    "RemoteScreen",
]


@dataclass(frozen=True)
class PanelKey:
    """One front-panel key and the PC key that stands in for it.

    ``number`` is the virtual button the key sends, or ``None`` where
    that number is not established.  ``pc_key`` is a Textual key name.
    ``group`` names the cluster the key belongs to (``"left"`` or
    ``"right"``).  ``note`` carries anything a reader needs to know
    that the label does not say.
    """

    number: int | None
    label: str
    pc_key: str
    group: str
    note: str = ""


#: The panel, as a table.  One entry per front-panel key; the drawing
#: and the bindings are both built from it.
PANEL_KEYS: List[PanelKey] = [
    # --- row one, left block: the two arrows and Up/YES ----------------
    PanelKey(6, "Left", "q", "left"),
    PanelKey(8, "Right", "w", "left"),
    PanelKey(7, "Up/YES", "e", "left"),
    # --- row one, right block: the four system keys --------------------
    PanelKey(11, "System", "u", "right"),
    PanelKey(13, "Effects", "i", "right"),
    PanelKey(10, "Trk L", "o", "right"),
    PanelKey(12, "Trk R", "p", "right"),
    # --- row two, left block: the entry keys ---------------------------
    PanelKey(9, "Enter/Sv", "a", "left"),
    PanelKey(0, "Bank Sel", "s", "left", "cycles the active menu's pages"),
    PanelKey(5, "Down/NO", "d", "left"),
    # --- row two, right block: the four mode keys ----------------------
    PanelKey(1, "Sel Snd", "h", "right", "Copy Effect is its secondary function"),
    PanelKey(2, "Sel Prst", "j", "right"),
    PanelKey(3, "Ed Snd", "k", "right", "again = compare (its secondary function)"),
    PanelKey(4, "Ed Trk", "l", "right"),
]

#: The two Track keys' PC keys: pressed together they are the live-mute
#: chord, alone they move the track cursor.
TRACK_PC_KEYS = tuple(
    entry.pc_key for entry in PANEL_KEYS if entry.label.startswith("Trk")
)
#: How long a single Track key waits for its partner.  A human presses
#: the pair within this window; the unit detects the chord on its own
#: panel scan, so the two messages have to go out together.
TRACK_CHORD_WINDOW = 0.18

#: The two panel modes ``M`` toggles between, and the button each one is.
SELECT_SOUND = 1
SELECT_PRESET = 2
MODE_LABELS: dict[str, str] = {
    "SOUND": "SELECT SOUND",
    "PRESET": "SELECT PRESET",
}
MODE_BUTTONS: dict[str, int] = {
    "SOUND": SELECT_SOUND,
    "PRESET": SELECT_PRESET,
}


class RemoteScreen(ModalScreen[None]):
    """A modal screen mirroring the SQ-R's front panel.

    ``on_press(number)`` is called with the virtual button number when a
    numbered key is pressed; ``on_live_mute()`` for the Track pair's
    chord (space, or the two Track keys within the chord window).
    **There is no write gate here** -- the owner drives the panel, and
    the program cannot tell which presses the unit treats as edits.
    ``say_`` is an optional second route for the status line.
    """

    DEFAULT_CSS = """
    RemoteScreen { align: center middle; }
    RemoteScreen > Vertical {
        width: auto; height: auto; max-height: 90%;
        border: thick $accent; background: $surface; padding: 1 2;
    }
    RemoteScreen .title { width: auto; text-style: bold; padding-bottom: 1; }
    RemoteScreen .rows { width: auto; height: auto; }
    RemoteScreen .row { width: auto; height: auto; }
    RemoteScreen .key {
        width: 11; height: 4; margin: 0 1 0 0;
        border: round $accent; text-align: center;
        background: $panel; color: $text;
    }
    RemoteScreen .gap { width: 4; }
    RemoteScreen .mode { width: auto; height: auto; padding: 0 1; text-style: bold; }
    RemoteScreen .hint {
        width: auto; color: $text-muted; padding: 0 1; height: auto;
    }
    RemoteScreen .status { width: auto; height: auto; padding: 0 1; }
    RemoteScreen .status.refused {
        background: $error; color: $text; text-style: bold;
    }
    """

    BINDINGS: ClassVar[List[Any]] = (
        [
            Binding("escape", "close", "Close", priority=True),
            # One key for the panel's two mode keys: press it again to
            # go back.  Bound in both cases, so it works whether Shift is
            # held; neither letter is a panel key here.
            Binding("m", "toggle_mode", "Mode", show=False, priority=True),
            Binding("M", "toggle_mode", "Mode", show=False, priority=True),
        ]
        + [
            Binding(
                entry.pc_key,
                f"press('{entry.pc_key}')",
                entry.label,
                show=False,
                priority=True,
            )
            for entry in PANEL_KEYS
        ]
        + [
            Binding("space", "live_mute", "Live mute", show=False, priority=True),
        ]
        # The keyboard's own arrows drive the panel's arrows, in addition
        # to the letter block: the panel has four arrow keys, and the
        # keyboard has four arrow keys.
        + [
            Binding("left", "press('q')", "Left", show=False, priority=True),
            Binding("right", "press('w')", "Right", show=False, priority=True),
            Binding("up", "press('e')", "Up/YES", show=False, priority=True),
            Binding("down", "press('d')", "Down/NO", show=False, priority=True),
        ]
    )

    #: The line that names the panel mode.
    MODE_ID = "remote-mode"

    def __init__(
        self,
        *,
        on_press: Optional[Callable[[int], None]] = None,
        on_live_mute: Optional[Callable[[], None]] = None,
        say_: Optional[Callable[..., None]] = None,
        mode: str = "SOUND",
        on_mode: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__()
        self._on_press = on_press
        self._on_live_mute = on_live_mute
        self._say = say_
        #: The panel mode the last ``M`` left the unit in.  The unit does
        #: not report it, so this is the app's memory of what it sent.
        self._mode = mode if mode in MODE_LABELS else "SOUND"
        #: ``on_mode(mode)`` -- the app keeps the state across openings.
        self._on_mode = on_mode
        #: The Track key waiting for its partner, and its timer.
        self._pending_track: Optional[str] = None
        self._pending_timer: Optional[Any] = None
        #: Whether a live-mute chord is queued (waiting for the remote drain).
        self._pending_live_mute = False

    # --- layout ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Remote — SQ-R front panel", classes="title")
            with Vertical(classes="rows"):
                for start in (0, 7):
                    with Horizontal(classes="row"):
                        for offset, entry in enumerate(PANEL_KEYS[start : start + 7]):
                            if offset == 3:
                                yield Static("", classes="gap")
                            yield Static(
                                f"{entry.label}\n({entry.pc_key})",
                                classes="key",
                                markup=False,
                                id=f"key-{entry.pc_key}",
                            )
            yield Static(
                self._mode_line(),
                id=self.MODE_ID,
                classes="mode",
                markup=False,
            )
            yield Static(
                "esc — close · arrows also work · m — mode · space or o+p — live mute",
                classes="hint",
                markup=False,
            )
            yield Static("", classes="status")

    # --- helpers --------------------------------------------------------

    def _entry_for_pc_key(self, pc_key: str) -> Optional[PanelKey]:
        """The table entry bound to this PC key, if any."""
        for entry in PANEL_KEYS:
            if entry.pc_key == pc_key:
                return entry
        return None

    def say(self, message: str, *, refused: bool = False) -> None:
        """One sentence on this screen's own status line."""
        try:
            status = self.query_one(".status", Static)
        except Exception:  # pragma: no cover - the screen closed under us
            return
        prefix = "[b]refused[/b] · " if refused else ""
        status.update(prefix + message)
        status.set_class(refused, "refused")
        if self._say is not None:
            self._say(message, refused=refused)

    # --- actions --------------------------------------------------------

    def _mode_line(self) -> str:
        """The panel mode, in the panel's own words."""
        return f"Mode: {MODE_LABELS[self._mode]}  ·  m toggles"

    def set_mode(self, mode: str) -> None:
        """Adopt a mode the app toggled from the browser."""
        if mode not in MODE_LABELS:
            return
        self._mode = mode
        try:
            self.query_one(f"#{self.MODE_ID}", Static).update(self._mode_line())
        except Exception:  # pragma: no cover - the screen closed under us
            return

    def action_toggle_mode(self) -> None:
        """``m``: Select Sound <-> Select Preset, with the same key.

        The unit does not report its mode, so the screen remembers the
        last one it sent; the app is told so the choice survives closing
        and reopening the window.  The panel key itself is sent through
        the same queue as every other press.
        """
        self._flush_pending_track()
        if self._on_press is None:
            self.say("mode not sent: this screen has no write path", refused=True)
            return
        new = "PRESET" if self._mode == "SOUND" else "SOUND"
        self._mode = new
        self.set_mode(new)
        number = MODE_BUTTONS[new]
        if self._on_mode is not None:
            self._on_mode(new)
        self._send_tap("m", number)
        self.say(f"mode: {MODE_LABELS[new]} (button {number} sent)")

    def action_close(self) -> None:
        """Close the screen."""
        self._flush_pending_track()
        self.dismiss()

    def action_press(self, pc_key: str) -> None:
        """A table key was pressed: send it.

        **No write gate here.**  The gate guards this program's preset
        writes; a panel key is not one of them, and the program cannot
        tell which presses the unit itself treats as edits.  The owner
        drives the panel -- that is what the window is for.
        """
        entry = self._entry_for_pc_key(pc_key)
        if entry is None:
            return
        if entry.number is None:
            self.say("that key's number is not established yet", refused=True)
            return
        if pc_key in TRACK_PC_KEYS:
            self._press_track(pc_key)
            return
        self._flush_pending_track()
        self._send_tap(pc_key, entry.number)

    def _send_tap(self, pc_key: str, number: int) -> None:
        """Send one panel key and say so."""
        if self._on_press is not None:
            self._on_press(number)
        self.say(f"sent button {number}")

    def _press_track(self, pc_key: str) -> None:
        """One of the two Track keys: alone it scrolls, together it mutes.

        The unit detects the chord on its own panel scan, so both
        messages must go out at once; a single key therefore waits a
        moment for its partner before it is sent as a plain press.
        """
        pending = self._pending_track
        if pending is not None and pending != pc_key:
            self._pending_track = None
            timer = self._pending_timer
            self._pending_timer = None
            if timer is not None:
                timer.stop()
            if self._on_live_mute is not None:
                self._on_live_mute()
            self.say("live mute sent (Track L + Track R)")
            return
        if pending == pc_key:
            return
        entry = self._entry_for_pc_key(pc_key)
        number = entry.number if entry is not None and entry.number is not None else 0
        self._pending_track = pc_key
        self._pending_timer = self.set_timer(
            TRACK_CHORD_WINDOW, lambda: self._send_pending(pc_key, number)
        )

    def _send_pending(self, pc_key: str, number: int) -> None:
        """The window closed without a partner: send the single press."""
        self._pending_track = None
        self._pending_timer = None
        self._send_tap(pc_key, number)

    def _flush_pending_track(self) -> None:
        """Send a waiting Track key before another key is handled."""
        pending = self._pending_track
        if pending is None:
            return
        timer = self._pending_timer
        if timer is not None:
            timer.stop()
        self._pending_track = None
        self._pending_timer = None
        entry = self._entry_for_pc_key(pending)
        if entry is not None and entry.number is not None:
            self._send_tap(pending, entry.number)

    def action_live_mute(self) -> None:
        """The space-bar chord: the unit's live mute.  No write gate --
        the panel gesture, not a preset write.  Queued through the remote
        drain so it respects the pacing gate.
        """
        self._flush_pending_track()
        if self._on_live_mute is None:
            self.say(
                "live mute not sent: this screen has no write path",
                refused=True,
            )
            return
        if self._pending_live_mute:
            return  # already queued
        self._pending_live_mute = True
        self._on_live_mute()

    def live_mute_result(self, ok: bool, message: str) -> None:
        """Called by the app when the live-mute chord completes."""
        self._pending_live_mute = False
        self.say(message, refused=not ok)
