# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
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

"""The one sentence about MIDI Mode that the preset screens share.

The unit does not report its System-bank MIDI Mode over SysEx.  The
all-presets dump's global block is not a System dump either: when the
unit moved from MULTI to POLY, and from base channel 10 to 11, all 21
bytes stayed the same (measured 2026-10-27; see
``docs/PRESET-FRAMING.md`` §7).  So a screen cannot honestly say "the
unit is in POLY now"; it can say what POLY or OMNI does to a preset,
which is the confusing part in the first place:

Only track 1 runs.  The other seven tracks are not silent because the
preset says OFF — they are not used in those modes, and the panel does
not show them.  Edits to them still go through; they appear once the
unit is set to MULTI.
"""

from __future__ import annotations

__all__ = ["MIDI_MODE_HINT"]

#: Keep this one string: the preset list, both editors and the remote
#: window should never disagree about why tracks 2-8 are not shown.
MIDI_MODE_HINT = (
    "MIDI Mode must be MULTI on the unit for tracks 2\u20138 to run. "
    "In POLY or OMNI, only track 1 runs and the panel shows that track "
    "alone; edits to the others still go through and show in MULTI."
)
