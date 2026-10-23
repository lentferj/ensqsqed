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

"""The write gate, in a module of its own so the editors can hold it.

**One gate, one place.**  A modal screen cuts the app's binding chain, so
``w`` cannot reach the browser's action while an editor is open.  The
sibling p2ked gives its editors the gate itself rather than a copy of its
state, and this is the same shape: the screen toggles the object the
app's write path checks, so the note under the table can never describe
a state the gate is not in.

The module exists because the app imports the screens and the screens
would otherwise have to import the app back.  Keeping the gate here
leaves both directions one-way.
"""

from __future__ import annotations

__all__ = ["EditGate"]


class EditGate:
    """Whether writes to the unit are allowed, and what to say about it.

    Locked by default.  The family's ``--allow-write`` starts it armed and
    ``w`` toggles it in the browser or in either editor; either way the
    status line says which state it is in, so the answer to "why did my
    edit not send?" is on screen rather than in a keybinding manual.

    **The gate is asked twice.**  A screen reads the state when it is
    opened and again for every change: the gate can be closed between two
    changes, and a change that cannot be sent is not a change.  This is
    the shape the sibling p2ked uses for its bursts.
    """

    def __init__(self, *, armed: bool = False) -> None:
        self.armed = armed

    def toggle(self) -> bool:
        """Flip the gate and say what it is now."""
        self.armed = not self.armed
        return self.armed

    def describe(self) -> str:
        """The gate's state, in the family's words."""
        return "write gate ARMED" if self.armed else "write gate locked"

    def refuse(self, what: str) -> str:
        """The message for a change refused because the gate is locked."""
        return (
            f"{what} not sent: the write gate is locked. Press w to arm it, "
            "or start with --allow-write."
        )
