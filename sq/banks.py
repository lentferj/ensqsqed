# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# The memory layout and the program-change scheme are transcribed as data
# from Ensoniq's SQ-R Bedienungsanleitung, part 3 ("Programmwechsel im
# SQ-R") and part 1 ("Anwahl von Sounds").
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

"""The 340 sounds, and the reason a program change is not a program change.

Four groups of 80, plus 20 drum kits that are always there:

===========  ====  ==========================================
group        n     what it is
===========  ====  ==========================================
INT          80    RAM. The only group the unit will dump.
ROM          80    Fixed. Cannot be overwritten.
CARD A       80    A memory card's first bank.
CARD B       80    A memory card's second bank.
DRUM         20    In ROM, reachable from anywhere.
===========  ====  ==========================================

**This instrument has no Bank Select.** It has Bank Select *controllers*
-- 0 and 32 appear in its implementation chart -- but those are for the
sequencer's own use. Choosing between INT, ROM, CARD A and CARD B is done
with **program changes**, and that is the single fact this module exists
to get right:

===================  ==========================================
MIDI program change  what it does
===================  ==========================================
0-79                 selects a sound *in the current group*
80-99                selects one of the 20 drum kits, always
123                  the next program change brings its effect
124                  from now on, program changes mean INT
125                  from now on, program changes mean ROM
126                  from now on, program changes mean CARD A
127                  from now on, program changes mean CARD B
===================  ==========================================

So a program change means nothing on its own. It means whatever the last
group-selecting program change said it means, and that may have been sent
by somebody else, or by nobody -- the unit powers up in some group and
stays there. A program that sends 42 and reports "selected sound 42" is
guessing. :func:`select_messages` always sends the group first, for the
same reason the sibling projects always send Bank Select before Program
Change.

**The manual counts from 1 and MIDI counts from 0**, and the manual says
so itself: "Alle SQ-R Programmwechsel werden von 001 bis 128 angezeigt und
verarbeitet, über MIDI jedoch als 000 bis 127 gesendet." So the group
selectors are displayed 125-128 and sent as 124-127. :class:`Slot` keeps
``number`` and ``program_change`` apart and never converts silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

__all__ = [
    "Group",
    "Slot",
    "GROUPS",
    "SOUNDS_PER_GROUP",
    "DRUM_KITS",
    "TOTAL_SOUNDS",
    "BANKS_PER_GROUP",
    "SOUNDS_PER_BANK",
    "DRUM_FIRST_PC",
    "EFFECT_WITH_NEXT_PC",
    "PRESETS",
    "group",
    "groups",
    "group_ids",
    "by_selector",
    "slots",
    "slot",
    "lookup",
    "readable_groups",
    "select_messages",
]

#: Each group is eight banks of ten.
BANKS_PER_GROUP = 8
SOUNDS_PER_BANK = 10
SOUNDS_PER_GROUP = BANKS_PER_GROUP * SOUNDS_PER_BANK  # 80

#: Banks 8 and 9, twenty kits, in ROM and reachable from any group.
DRUM_KITS = 20

#: The program change the first drum kit answers to.
DRUM_FIRST_PC = 80

#: Sent before a sound's program change, in MULTI mode, to bring that
#: sound's effect along with it. Displayed 124 on the unit.
EFFECT_WITH_NEXT_PC = 123

#: The unit also holds 80 presets, which are performance setups rather
#: than sounds. ensqsqed does not browse them; it knows the number so that
#: a dump holding them can be recognised rather than mistaken for sounds.
PRESETS = 80


@dataclass(frozen=True)
class Group:
    """One of the four groups of 80, or the drum kits."""

    id: str
    label: str
    #: The program change that makes following program changes mean this
    #: group. ``None`` for the drum kits, which need no selector: their
    #: program changes are absolute.
    selector: Optional[int]
    count: int
    #: Whether the unit will dump it. Only INT, and even then only after
    #: the front panel is told to send -- see :mod:`sq.bridge`.
    readable: bool
    note: str = ""


#: In the order the front panel steps through them.
GROUPS: Tuple[Group, ...] = (
    Group(
        "INT",
        "Internal",
        124,
        SOUNDS_PER_GROUP,
        readable=True,
        note="RAM. The only group that can be dumped, and the only one "
        "that can be overwritten.",
    ),
    Group(
        "ROM",
        "ROM",
        125,
        SOUNDS_PER_GROUP,
        readable=False,
        note="Fixed in the instrument.",
    ),
    Group(
        "CARDA",
        "Card A",
        126,
        SOUNDS_PER_GROUP,
        readable=False,
        note="A memory card's first bank. Empty if no card is in.",
    ),
    Group(
        "CARDB",
        "Card B",
        127,
        SOUNDS_PER_GROUP,
        readable=False,
        note="A memory card's second bank.",
    ),
    Group(
        "DRUM",
        "Drum kits",
        None,
        DRUM_KITS,
        readable=False,
        note="Banks 8 and 9. In ROM, and reachable from any group "
        "without a selector: their program changes are absolute.",
    ),
)

TOTAL_SOUNDS = sum(g.count for g in GROUPS)  # 340

_BY_ID: Dict[str, Group] = {g.id: g for g in GROUPS}
_BY_SELECTOR: Dict[int, Group] = {
    g.selector: g for g in GROUPS if g.selector is not None
}


@dataclass(frozen=True)
class Slot:
    """One sound in one group."""

    group: Group
    #: What the unit's display shows, counting from 1.
    number: int
    #: What goes on the wire, counting from 0. Never derived from
    #: ``number`` at the point of use, and never the other way round.
    program_change: int

    @property
    def group_id(self) -> str:
        return self.group.id

    @property
    def bank_id(self) -> str:
        """The favourites store and the catalog key on this."""
        return self.group.id

    @property
    def key(self) -> str:
        return f"{self.group.id}:{self.number:03d}"

    @property
    def display(self) -> str:
        return f"{self.group.label} {self.number:03d}"

    @property
    def bank(self) -> int:
        """Which of the group's eight banks this sound sits in, 0-7.

        The front panel's Left/Right cursor moves by this. For the drum
        kits it is 8 or 9, which is what the manual calls them.
        """
        if self.group.id == "DRUM":
            return BANKS_PER_GROUP + self.program_change // SOUNDS_PER_BANK
        return self.program_change // SOUNDS_PER_BANK

    @property
    def needs_selector(self) -> bool:
        """Whether reaching this sound means changing group first."""
        return self.group.selector is not None


def group(group_id: str) -> Group:
    try:
        return _BY_ID[group_id]
    except KeyError:
        raise LookupError(
            f"no group {group_id!r}; have {', '.join(g.id for g in GROUPS)}"
        ) from None


def groups() -> Tuple[Group, ...]:
    return GROUPS


def group_ids() -> List[str]:
    return [g.id for g in GROUPS]


def readable_groups() -> List[Group]:
    """The groups the unit will dump. There is one."""
    return [g for g in GROUPS if g.readable]


def by_selector(program_change: int) -> Group:
    """The group a selector program change switches to."""
    try:
        return _BY_SELECTOR[program_change]
    except KeyError:
        raise LookupError(
            f"program change {program_change} is not a group selector; "
            f"the selectors are "
            f"{', '.join(str(g.selector) for g in GROUPS if g.selector)}"
        ) from None


def slots(group_id: str) -> List[Slot]:
    entry = group(group_id)
    if entry.id == "DRUM":
        # A drum kit's program change is absolute: kit 1 answers to 80.
        return [Slot(entry, n + 1, DRUM_FIRST_PC + n) for n in range(entry.count)]
    return [Slot(entry, n + 1, n) for n in range(entry.count)]


def slot(group_id: str, number: int) -> Slot:
    entry = group(group_id)
    if not 1 <= number <= entry.count:
        raise LookupError(
            f"{entry.label} holds {entry.count} sounds, numbered from 1, "
            f"so {number} is not one of them"
        )
    return slots(group_id)[number - 1]


def lookup(program_change: int, current_group: str = "INT") -> Optional[Slot]:
    """What a program change addresses, given the group in force.

    ``current_group`` is not optional in spirit even though it has a
    default: without it the question has no answer, and a caller that does
    not know which group the unit is in should say so rather than assume.
    """
    if DRUM_FIRST_PC <= program_change < DRUM_FIRST_PC + DRUM_KITS:
        return slot("DRUM", program_change - DRUM_FIRST_PC + 1)
    if 0 <= program_change < SOUNDS_PER_GROUP:
        return slot(current_group, program_change + 1)
    return None


def select_messages(
    target: Slot, channel: int = 0, *, with_effect: bool = False
) -> List[bytes]:
    """Every MIDI message needed to actually land on ``target``.

    The group selector first, then the sound -- always both, even when the
    unit is believed to be in the right group already. Believing costs
    nothing to check and the belief is usually wrong: the front panel can
    change group while nobody is looking, and the unit powers up in
    whichever group it was left in.

    A drum kit needs no selector because its program change is absolute,
    and sending one would be worse than pointless: it would leave the unit
    in a group the caller did not ask for.

    **The effect command goes first, and the manual says so explicitly.**
    Program change 124 as displayed means "the next program change brings
    its effect", which reads as though a group selector in between would
    consume it. It does not: "Dieser spezielle Programmwechsel 124 hat
    keinen Einfluss darauf, wie andere Programmwechsel verstanden werden.
    Wird einer der 'Steuer'-Programmwechsel fuer den gleichen Sound
    gebraucht, senden Sie diesen **zwischen dem 124er und dem
    eigentlichen Programmwechsel**." -- send the selector *between* the
    124 and the real program change. That is this order.
    """
    if not 0 <= channel <= 15:
        raise ValueError(f"channel {channel} is outside 0-15")
    out: List[bytes] = []
    status = 0xC0 | channel
    if with_effect:
        out.append(bytes([status, EFFECT_WITH_NEXT_PC]))
    if target.needs_selector:
        out.append(bytes([status, target.group.selector]))
    out.append(bytes([status, target.program_change]))
    return out
