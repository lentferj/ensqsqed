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

"""``sqcli`` -- the SQ-R's memory, and what it takes to reach a sound."""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional, Sequence

from sq import banks
from sq import catalog as cat
from sq import config
from sq import dumps
from sq import messages as m

__all__ = ["main", "build_parser"]


def _table(rows: Sequence[Sequence[str]], headers: Sequence[str]) -> str:
    if not rows:
        return "(nothing)"
    widths = [max(len(str(r[i])) for r in (*rows, headers))
              for i in range(len(headers))]
    out = ["  ".join(str(h).ljust(w) for h, w in zip(headers, widths)).rstrip(),
           "  ".join("-" * w for w in widths)]
    for row in rows:
        out.append("  ".join(str(c).ljust(w)
                             for c, w in zip(row, widths)).rstrip())
    return "\n".join(out)


def _cmd_groups(args) -> None:
    rows = []
    for entry in banks.groups():
        rows.append([entry.id, entry.label, str(entry.count),
                     "-" if entry.selector is None else str(entry.selector),
                     "yes" if entry.readable else "no"])
    print(_table(rows, ["id", "name", "sounds", "selector PC", "dumpable"]))
    print(f"\n{banks.TOTAL_SOUNDS} sounds. The selector is a *program "
          f"change*, not a Bank Select:\nsend it once and every later "
          f"program change means that group, until the next one.")
    print("The drum kits need none -- their program changes are absolute.")


def _cmd_list(args) -> None:
    entry = banks.group(args.group)
    catalog = cat.load(getattr(args, "catalog", None))
    rows = []
    for item in banks.slots(entry.id):
        rows.append([f"{item.number:03d}", str(item.program_change),
                     str(item.bank),
                     catalog.display_name(item.bank_id, item.program_change)])
    print(f"{entry.label} — {entry.count} sounds\n")
    print(_table(rows, ["shown", "PC", "bank", "name"]))
    print(f"\n{entry.note}")


def _cmd_reach(args) -> None:
    """What it takes to land on one sound. Prints, sends nothing."""
    try:
        target = banks.slot(args.group, args.number)
    except LookupError as exc:
        raise SystemExit(f"error: {exc}")
    messages = banks.select_messages(target, args.channel - 1,
                                     with_effect=args.with_effect)
    print(f"to reach {target.display} on channel {args.channel}:\n")
    for message in messages:
        pc = message[1]
        what = "sound"
        if pc == banks.EFFECT_WITH_NEXT_PC:
            what = "bring the next sound's effect too (MULTI mode)"
        else:
            try:
                what = f"select the {banks.by_selector(pc).label} group"
            except LookupError:
                if pc >= banks.DRUM_FIRST_PC:
                    what = "drum kit (absolute, needs no group)"
        print(f"  {' '.join(f'{b:02X}' for b in message)}"
              f"   program change {pc:3}   {what}")
    print(f"\nThe unit shows this sound as {target.number:03d}; "
          f"MIDI calls it {target.program_change}.")


def _cmd_read(args) -> None:
    """Read a .syx or .mid the instrument produced. No hardware."""
    library = dumps.read_file(args.file)
    print(f"{args.file}: {library.summary()}")
    names = library.names()
    if names:
        rows = [[f"{n + 1:03d}", str(n), text.strip()]
                for n, text in sorted(names.items())]
        print()
        print(_table(rows, ["shown", "PC", "name"]))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sqcli",
        description="Browse an Ensoniq SQ-R Plus's memory map. Nothing "
                    "here opens a MIDI port.")
    parser.add_argument("--catalog", help="path to a generated catalog")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("groups", help="the four groups, the drums, the selectors")
    sp = sub.add_parser("list", help="every sound in a group")
    sp.add_argument("group", choices=banks.group_ids())
    sp = sub.add_parser("reach", help="what it takes to select one sound")
    sp.add_argument("group", choices=banks.group_ids())
    sp.add_argument("number", type=int)
    sp.add_argument("--channel", type=int, default=1)
    sp.add_argument("--with-effect", action="store_true",
                    help="bring the sound's effect too (MULTI mode)")
    sp = sub.add_parser("read", help="read a .syx or .mid dump")
    sp.add_argument("file")
    return parser


COMMANDS = {"groups": _cmd_groups, "list": _cmd_list, "reach": _cmd_reach,
            "read": _cmd_read}


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        COMMANDS[args.command](args)
    except (LookupError, ValueError, OSError) as exc:
        raise SystemExit(f"error: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
