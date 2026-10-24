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

"""ensqsqed keeps the contract the other eight projects in the family keep.

Every check below is the same one, run against this program rather than
written out again here. The reasons are in docs/UX-SPEC.md section 5 and
in the library: what drifted was never a bug anyone chose, it was a copy
that nobody had a second copy to compare against.
"""

from ensqsqed.app import KEY_HINTS, EnsqsqedApp, build_parser
from sq.terms import TERMS
from vinsynlib import conformance


def test_flags_match_the_family() -> None:
    # A tuple rather than the set in the template: check_flags takes a
    # Sequence, and mypy is right that a set is not one.
    assert not conformance.check_flags(
        build_parser(),
        required=("port", "scan", "recv-port", "demo", "config", "catalog"),
    )


def test_keys_match_the_family() -> None:
    # select=False, and the reason is in ensqsqed/app.py: a focused
    # DataTable consumes Enter for its own row selection, so it arrives as
    # RowSelected and calls action_select from there. The key works and the
    # legend promises it; what it cannot be is a BINDINGS entry, because
    # binding it would mean the App taking Enter from the table and every
    # other table key with it.
    problems = conformance.check_bindings(
        EnsqsqedApp, favourites=True, channel=True, select=False
    )
    # One deliberate divergence: ensqsqed moves the favourites view off the
    # family's `F` and onto `v`. `f` writes the favourites database and `F`
    # does not, so the shift-pair left a write one slip away from a read;
    # `v` has no such twin. The family asks a tool that renames a shared key
    # to say why in its own test, and these two lines are the whole of the
    # divergence -- anything else appearing here is a real drift.
    assert set(problems) == {
        "F is not bound; the family binds it to cycle_favorites",
        "cycle_favorites is not reachable from F; it is bound to v",
    }, "\n".join(problems)


# These are the hints this tool adds beyond the family's shared legend.
# They are declared here so the conformance check can verify the legend
# contains exactly the shared blocks plus these declared extras, in the
# correct order.
#
# `I import` leads the list: it is the newest and most destructive
# surface -- it replaces whole banks -- so the eye should find it before
# the editors. It is capital `I`, which the family does not use, so it
# cannot be mistaken for the shared `i device` key. The three preset-editor
# keys follow, the way the sibling nanosyned puts its own edit keys first:
# they are this tool's headline, and the family leaves the one place for a
# tool's own hints -- after `n note`, before `i device` -- for exactly
# that. `m` opens the eight-track editor, `x` the effect editor over the
# same preset, and `p` the list of the eighty presets the unit holds. None
# of the three is a family key, so none of them can be mistaken for one;
# the family asks for a tool that renames a shared key to say why in its
# own test, and none of these renames one.
_EXTRAS = (
    "I import",
    "m multi",
    "x fx",
    "p preset list",
    "ctrl+o remote",
    "S scan device",
    "R rebuild catalog",
    "* rating",
    "V rating filter",
    "C category",
    "z undo",
    "Z undo all",
    "w write gate",
)


def test_the_legend_matches_the_family() -> None:
    problems = conformance.check_legend(KEY_HINTS, extras=_EXTRAS)
    # `v favourites view` is our divergence from the family's `F favourites view`.
    # The family asks a tool that renames a shared key to say why in its own test,
    # and these two lines are the whole of the divergence.
    assert set(problems) == {
        "the legend is missing 'F favourites view'",
        "the legend shows 'v favourites view', which is not in the family's",
    }, "\n".join(problems)


def test_the_vocabulary_is_declared() -> None:
    assert not conformance.check_terms(TERMS)
