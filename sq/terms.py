# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
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

"""What the Ensoniq calls things, declared once.

**The instrument's word is the instrument's.** The SQ-R's own display and
manual say SOUND and GROUP, so those are the words every string a user
reads uses here -- the list view, the pane headings, the help, the README.

The wire's word is different and stays in the code that builds wire
messages: on the wire this is a *program change*, and reaching one sound
means sending a selector program change that stays in force until the
next one. That is not a naming exercise -- it is the thing that bites
first on this instrument -- so it stays in ``sq.banks`` where it is
visible.
"""

from __future__ import annotations

from vinsynlib.terms import Terminology, register

__all__ = ["TERMS"]

TERMS = Terminology(
    app_name="ensqsqed",
    sound="sound",
    container="group",
    device="Ensoniq SQ-R Plus",
)

#: Registered at import so the family's registry knows what this tool calls
#: its concepts. Registration rather than a monkeypatched module global: a
#: library cannot know the name of the program using it, and passing it in
#: is honest where patching is global.
register(TERMS)
