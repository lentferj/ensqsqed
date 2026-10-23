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
# ensqsqed is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
# more details.

"""The family's choice list, with one owner for the cursor.

**The shared screen and its table both moved the cursor.**  Textual runs
the ``on_key`` handlers of every class in the screen's MRO -- overriding
one does not replace the one above it -- and the focused ``DataTable``
then handles the same arrow key through its own bindings.  So ``down``
stepped ``_current`` by one *and* the table's highlight by two: the row
under the cursor was one ahead of the answer, and choosing
"8-VOICE.CHORUS" returned "WARM CHAMBER".

What is done here is to stop the key at this screen before either
handler sees it, and move both the model and the table exactly once.
The table still owns ``home``/``end``/paging; its ``RowHighlighted``
message puts ``_current`` back in step when it moves.

The frame is the family's: the shared screen leaves the vertical frame
to its caller.  The class lives outside :mod:`ensqsqed.multi` so the
effect editor and the browser's rating prompt use the same fixed list.
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical

from vinsynlib.ui.modals import ChoiceScreen

__all__ = ["ChoicePicker"]


class ChoicePicker(ChoiceScreen):
    """The shared choice list, with the cursor and `_current` in step."""

    DEFAULT_CSS = """
    ChoicePicker { align: center middle; }
    ChoicePicker > Vertical {
        width: 60; height: auto; max-height: 80%;
        border: thick $accent; background: $surface; padding: 1 2;
    }
    ChoicePicker DataTable { height: auto; max-height: 16; }
    """

    def compose(self) -> ComposeResult:
        """The shared screen's own children, in a frame."""
        with Vertical():
            yield from super().compose()

    def on_key(self, event: Any) -> None:
        """``up`` and ``down``: move once, and say so.

        ``event.stop()`` is what keeps the shared ``on_key`` and the
        table's own binding out of it; ``prevent_default`` alone is too
        late.  The other navigation keys are left to the table, whose
        ``RowHighlighted`` message realigns ``_current``.
        """
        key = event.key if hasattr(event, "key") else ""
        if key not in ("up", "down") or not self._choices:
            return
        event.stop()
        event.prevent_default()
        step = -1 if key == "up" else 1
        new = max(0, min(self._current + step, len(self._choices) - 1))
        if new == self._current:
            return
        self._current = new
        if self._table is not None:
            self._table.move_cursor(row=new)
            self._refresh_marker()

    def on_data_table_row_highlighted(self, event: Any) -> None:
        """``home``, ``end`` and paging moved the table: follow it."""
        if event.data_table is not self._table:
            return
        if event.cursor_row == self._current:
            return
        self._current = event.cursor_row
        self._refresh_marker()

    def action_confirm(self) -> None:
        """Answer with the row the cursor is on, which is the only truth."""
        if not self._choices:
            self.dismiss(None)
            return
        if self._table is not None:
            self._current = self._table.cursor_row
        self.dismiss(self._current)
