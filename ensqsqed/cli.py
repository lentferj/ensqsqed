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
# WARRANTY; without even the implied warranty of MERCHANTABILITY OR FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.

"""``sqcli`` -- the SQ-R's memory, and what it takes to reach a sound."""

from __future__ import annotations

import argparse
import sys
from typing import Callable, Dict, List, Optional, Sequence

from sq import banks
from sq import catalog as cat
from sq import config
from sq import dumps
from sq import messages as m
from sq import bridge as b
from vinsynlib.cli import (
    add_common_arguments,
    channel_of,
    make_parser,
    validate_common,
)
from vinsynlib.spec import (
    EXIT_OK,
)

__all__ = ["main", "build_parser"]


def _table(rows: Sequence[Sequence[str]], headers: Sequence[str]) -> str:
    if not rows:
        return "(nothing)"
    widths = [
        max(len(str(r[i])) for r in (*rows, headers)) for i in range(len(headers))
    ]
    out = [
        "  ".join(str(h).ljust(w) for h, w in zip(headers, widths)).rstrip(),
        "  ".join("-" * w for w in widths),
    ]
    for row in rows:
        out.append("  ".join(str(c).ljust(w) for c, w in zip(row, widths)).rstrip())
    return "\n".join(out)


def _cmd_ports(_device, _args) -> None:
    """List MIDI ports on this host."""
    from vinsynlib.midi import list_ports, render_ports

    ins, outs = list_ports()
    print(
        render_ports(
            ins,
            outs,
            note=(
                "An SQ-R has no USB of its own and answers through whatever "
                "interface it is cabled behind."
            ),
        )
    )


def _cmd_banks(_device, _args) -> None:
    """The bank table (no device needed)."""
    rows = []
    for entry in banks.groups():
        rows.append(
            [
                entry.id,
                entry.label,
                str(entry.count),
                "-" if entry.selector is None else str(entry.selector),
                "yes" if entry.readable else "no",
            ]
        )
    print(_table(rows, ["id", "name", "sounds", "selector PC", "dumpable"]))
    print(
        f"\n{banks.TOTAL_SOUNDS} sounds. The selector is a *program "
        f"change*, not a Bank Select:\nsend it once and every later "
        f"program change means that group, until the next one."
    )
    print("The drum kits need none -- their program changes are absolute.")


def _cmd_list(_device, args) -> None:
    """One bank's slots (no device needed)."""
    entry = banks.group(args.group)
    catalog = cat.load(getattr(args, "catalog", None))
    rows = []
    for item in banks.slots(entry.id):
        rows.append(
            [
                f"{item.number:03d}",
                str(item.program_change),
                str(item.bank),
                catalog.display_name(item.bank_id, item.program_change),
            ]
        )
    print(f"{entry.label} — {entry.count} sounds\n")
    print(_table(rows, ["shown", "PC", "bank", "name"]))
    print(f"\n{entry.note}")


def _cmd_find(_device, args) -> None:
    """Search names (no device needed)."""
    catalog = cat.load(getattr(args, "catalog", None))
    hits = catalog.search(args.name)
    if not hits:
        print(f"nothing matching {args.name!r}", file=sys.stderr)
        return
    rows = []
    for bank_id, number, name, differs in hits:
        try:
            slot = banks.slot(bank_id, number)
        except LookupError:
            continue
        rows.append(
            [
                bank_id,
                f"{number:03d}",
                name + (" *" if differs else ""),
                str(slot.program_change),
                str(slot.bank),
            ]
        )
    print(_table(rows, ["bank", "#", "name", "PC", "bank"]))
    if any(h[3] for h in hits):
        print(
            "\n* the unit and the catalog disagree about this "
            "slot; the unit's name is shown"
        )


def _cmd_resolve(_device, _args) -> None:
    """What does this MSB/LSB/PC select? (no device needed)."""
    # Note: SQ-R doesn't use Bank Select (MSB/LSB), so this command
    # is limited to what the instrument supports.
    # The spec says this command needs no device, so we implement it
    # with what we have.
    raise SystemExit(
        "error: this instrument has no Bank Select (MSB/LSB); "
        "a program change means what the last group selector said"
    )


def _favorites(args):
    from sq import favorites as favmod

    return favmod.Favorites(getattr(args, "favorites", None))


def _cmd_fav(_device, args) -> None:
    """The favourites database."""
    favorites = _favorites(args)
    catalog = cat.load(getattr(args, "catalog", None))
    try:
        if args.add:
            group_id, number = _parse_slot(args.add)
            name = catalog.display_name(group_id, number)
            existing = favorites.get(group_id, number)
            if existing is None:
                rating, tags, note = 0, "", ""
            else:
                rating, tags, note = existing.rating, existing.tags, existing.note
            if args.rating is not None:
                rating = _rating_stars(args.rating, "--rating")
            if args.tags is not None:
                tags = args.tags
            if args.note is not None:
                note = args.note
            favorites.add(
                group_id, number, name=name, rating=rating, tags=tags, note=note
            )
            print(f"added {group_id} {number:03d}")
            return
        if args.remove:
            group_id, number = _parse_slot(args.remove)
            if favorites.remove(group_id, number):
                print(f"removed {group_id} {number:03d}")
            else:
                print(f"{group_id} {number:03d} was not a favourite", file=sys.stderr)
            return
        if args.rate:
            group_id, number = _parse_slot(args.rate[0])
            if favorites.get(group_id, number) is None:
                raise SystemExit(
                    f"error: {group_id} {number:03d} is not a favourite; add it first"
                )
            stars = _rating_stars(args.rate[1], "--rate")
            favorites.set_rating(group_id, number, stars)
            print(f"rated {group_id} {number:03d} {stars}")
            return
        if args.tag:
            rows = favorites.with_tag(args.tag)
        elif args.search:
            rows = favorites.search(args.search)
        else:
            rows = favorites.all(order=args.order)
        if args.rating_at_least is not None:
            stars = _rating_stars(args.rating_at_least, "--rating-at-least")
            rows = [row for row in rows if row.rating >= stars]
        if args.rating_exactly is not None:
            stars = _rating_stars(args.rating_exactly, "--rating-exactly")
            rows = [row for row in rows if row.rating == stars]
        if not rows:
            print("no favourites", file=sys.stderr)
            return
        table = []
        for fav in rows:
            try:
                slot = banks.slot(fav.bank_id, fav.number)
                wire = [str(slot.program_change), str(slot.bank)]
            except LookupError:
                wire = ["?", "?"]
            table.append(
                [
                    fav.bank_id,
                    f"{fav.number:03d}",
                    fav.name or catalog.display_name(fav.bank_id, fav.number),
                    *wire,
                    str(fav.rating),
                    fav.tags,
                    fav.note,
                ]
            )
        print(
            _table(
                table,
                ["bank", "#", "name", "PC", "bank", "rating", "tags", "note"],
            )
        )
    finally:
        favorites.close()


def _cmd_tags(_device, args) -> None:
    """Tags in use, with counts."""
    favorites = _favorites(args)
    try:
        counts = favorites.tags()
    finally:
        favorites.close()
    if not counts:
        print("no tags", file=sys.stderr)
        return
    print(_table([[tag, str(n)] for tag, n in counts.items()], ["tag", "count"]))


def _cmd_names(device, _args) -> None:
    """Read a bank's names off the unit (silent)."""
    names = device.read_names()
    rows = [
        [f"{n + 1:03d}", str(n), text.rstrip()] for n, text in sorted(names.items())
    ]
    print(f"Internal — {len(names)} sounds\n")
    print(_table(rows, ["shown", "PC", "name"]))


def _cmd_current(device, _args) -> None:
    """What the unit is playing (silent)."""
    record = device.read_current_sound()
    print(f"port            {device.description}")
    print(f"base channel    {device.channel + 1}")
    print(f"current sound   {m.sound_name(record).rstrip()!r} ({len(record)} bytes)")
    print(
        "\nThat is the sound the unit is on, edited if it is being "
        "edited.\nThere is no request for a sound by number."
    )


def _cmd_select(device, args) -> None:
    """Select a slot ON THE UNIT, or print what a DAW has to send.

    The SQ-R has no Bank Select. Reaching a sound takes **two program
    changes**: the group selector (124-127) and then the sound's own
    program change. A drum kit takes one, because its program change is
    absolute. ``--dry-run`` prints exactly those bytes and sends nothing,
    which is what a DAW's program-change list has to contain.
    """
    try:
        target = banks.slot(args.group, args.number)
    except LookupError as exc:
        raise SystemExit(f"error: {exc}")
    channel = args.reach_channel - 1
    messages = banks.select_messages(target, channel, with_effect=args.with_effect)
    if args.dry_run:
        print(f"to reach {target.display} on MIDI channel {args.reach_channel}:\n")
        for message in messages:
            pc = message[1]
            what = _describe_program_change(pc)
            print(
                f"  {' '.join(f'{b:02X}' for b in message)}"
                f"   program change {pc:3}   {what}"
            )
        print(
            f"\nThe unit shows this sound as {target.number:03d}; "
            f"MIDI calls it {target.program_change}. Send the messages in "
            f"this order, with no Bank Select (CC0/CC32)."
        )
        return
    if not args.yes:
        print(f"about to select {target.display} on MIDI channel {args.reach_channel}.")
        print("This changes what the instrument is playing.")
        if input("proceed? [y/N] ").strip().lower() not in ("y", "yes"):
            print("nothing sent")
            return
    for msg in messages:
        device._out.send_message(list(msg))
    if target.needs_selector:
        how = f"program change {target.group.selector} then {target.program_change}"
    else:
        how = f"program change {target.program_change}, absolute"
    if args.with_effect:
        how = f"program change {banks.EFFECT_WITH_NEXT_PC} first, then " + how
    print(f"selected {target.display} ({how}) on channel {args.reach_channel}")


def _describe_program_change(pc: int) -> str:
    """What one program change does on this instrument."""
    if pc == banks.EFFECT_WITH_NEXT_PC:
        return "bring the next sound's effect too (MULTI mode)"
    try:
        return f"select the {banks.by_selector(pc).label} group"
    except LookupError:
        if pc >= banks.DRUM_FIRST_PC:
            return "drum kit (absolute, needs no group)"
    return "the sound"


def _cmd_read(_device, args) -> None:
    """Read a dump file (no hardware)."""
    library = dumps.read_file(args.file)
    print(f"{args.file}: {library.summary()}")
    names = library.names()
    if names:
        rows = [
            [f"{n + 1:03d}", str(n), text.strip()] for n, text in sorted(names.items())
        ]
        print()
        print(_table(rows, ["shown", "PC", "name"]))


def _cmd_status(device, _args) -> None:
    """Ask the unit who it is (silent)."""
    record = device.read_current_sound()
    print(f"port            {device.description}")
    print(f"base channel    {device.channel + 1}")
    print(f"current sound   {m.sound_name(record).rstrip()!r} ({len(record)} bytes)")
    print(
        "\nThat is the sound the unit is on, edited if it is being "
        "edited.\nThere is no request for a sound by number."
    )


def _cmd_hardware(device, _args) -> None:
    """What the unit reports about itself."""
    # The SQ-R doesn't have an Identity Reply, but we can at least
    # show the port and channel.
    print(f"port            {device.description}")
    print(f"base channel    {device.channel + 1}")
    print("note: this instrument has no Identity Reply")


def _cmd_channels(device, _args) -> None:
    """Every MIDI channel, and what it selects."""
    # The SQ-R is single-timbral on one base channel.
    print(f"base channel    {device.channel + 1}")
    print("This instrument is single-timbral; other channels are not used.")


def _cmd_multi(device, _args) -> None:
    """Multi-mode setup, and why a channel is silent."""
    # The SQ-R doesn't have a multi-mode in the same sense as other instruments.
    print("This instrument does not have a multi-mode with multiple parts.")


def _rating_stars(value: object, flag: str) -> int:
    try:
        stars = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        raise SystemExit(f"error: {flag} wants 0-5, not {value!r}")
    if not 0 <= stars <= 5:
        raise SystemExit(f"error: {flag} wants 0-5, not {stars}")
    return stars


def _parse_slot(text: str) -> tuple:
    """``INT:29`` or ``INT/29`` or ``INT 29`` -> ``("INT", 29)``."""
    for separator in (":", "/", " "):
        if separator in text:
            bank_id, _, number = text.partition(separator)
            try:
                return bank_id.strip(), int(number)
            except ValueError:
                break
    raise SystemExit(
        f"error: {text!r} is not a slot; write it as GROUP:NUMBER, e.g. INT:29"
    )


def _cmd_dump(device, args) -> None:
    """Dump a bank or preset off the unit (silent)."""
    if args.dump_command == "bank":
        # Dump the internal bank
        _ = device.read_internal_bank()
        # Actually we need the raw message... the bridge returns parsed data.
        # We'll just write what we read.
        print("Bank dump read (not yet written to file)")
    elif args.dump_command == "sound":
        # Dump a single sound
        # We'd need to select it first then read... this is complex.
        print("Single sound dump not yet implemented")
    else:
        raise SystemExit(f"error: unknown dump command {args.dump_command}")


#: Commands that need no instrument at all.
OFFLINE = {
    "ports",
    "banks",
    "list",
    "find",
    "resolve",
    "fav",
    "favorites",
    "tags",
    "read",
}


COMMANDS: Dict[str, Callable] = {
    "ports": _cmd_ports,
    "banks": _cmd_banks,
    "groups": _cmd_banks,
    "list": _cmd_list,
    "find": _cmd_find,
    "search": _cmd_find,
    "resolve": _cmd_resolve,
    "fav": _cmd_fav,
    "favorites": _cmd_fav,
    "tags": _cmd_tags,
    "names": _cmd_names,
    "current": _cmd_current,
    "select": _cmd_select,
    "reach": _cmd_select,
    "read": _cmd_read,
    "syx": _cmd_read,
    "status": _cmd_status,
    "hardware": _cmd_hardware,
    "config": _cmd_hardware,
    "inquire": _cmd_hardware,
    "channels": _cmd_channels,
    "multi": _cmd_multi,
    "dump": _cmd_dump,
    # "send": _cmd_send,  # Not implemented yet
}


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser(
        "sqcli",
        "Browse an Ensoniq SQ-R Plus's memory map.",
        distribution="ensqsqed",
    )

    # Add all canonical flags for a browser
    add_common_arguments(
        parser,
        port=True,
        scan=True,
        recv_port=True,
        channel=True,
        device_channel=True,
        device_id=True,
        exclusive_channel=False,  # SQ-R doesn't have this
        timeout=True,
        catalog=True,
        config=True,
        favorites=True,
        yes=True,
        allow_write=False,  # editor-only flag
        demo=True,
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # ports
    sub.add_parser("ports", help="MIDI ports on this host")

    # banks (with aliases: groups, regions, roms)
    sp = sub.add_parser(
        "banks",
        aliases=["groups", "regions", "roms"],
        help="the bank table (no device needed)",
    )

    # list
    sp = sub.add_parser("list", help="one bank's slots (no device needed)")
    sp.add_argument("group", choices=banks.group_ids())

    # find (with alias: search)
    sp = sub.add_parser(
        "find", aliases=["search"], help="search names (no device needed)"
    )
    sp.add_argument("name")

    # resolve
    sp = sub.add_parser(
        "resolve", help="what does this MSB/LSB/PC select? (no device needed)"
    )
    sp.add_argument("msb", type=int)
    sp.add_argument("lsb", type=int)
    sp.add_argument("pc", type=int, help="program change, 0-based (wire)")

    # fav (with alias: favorites)
    sp = sub.add_parser("fav", aliases=["favorites"], help="the favourites database")
    sp.add_argument("--add", metavar="GROUP:N")
    sp.add_argument("--remove", metavar="GROUP:N")
    sp.add_argument("--rating", type=int, default=None)
    sp.add_argument(
        "--rate",
        nargs=2,
        metavar=("GROUP:N", "0-5"),
        help="rate a favourite you already have, leaving tags and note alone",
    )
    sp.add_argument("--tags", default=None)
    sp.add_argument("--note", default=None)
    sp.add_argument("--tag", default=None, help="list favourites with this tag")
    sp.add_argument("--search", default=None)
    ratings = sp.add_mutually_exclusive_group()
    ratings.add_argument(
        "--rating-at-least",
        type=int,
        default=None,
        metavar="0-5",
        help="list favourites rated this or higher",
    )
    ratings.add_argument(
        "--rating-exactly",
        type=int,
        default=None,
        metavar="0-5",
        help="list favourites rated exactly this (0 = not rated yet)",
    )
    sp.add_argument(
        "--order", default="added", choices=["added", "rating", "bank", "name"]
    )

    # tags
    sub.add_parser("tags", help="tags in use, with counts")

    # names
    sub.add_parser("names", help="read a bank's names off the unit (silent)")

    # current
    sub.add_parser("current", help="what the unit is playing (silent)")

    # select (with alias: reach)
    sp = sub.add_parser("select", aliases=["reach"], help="select a slot ON THE UNIT")
    sp.add_argument("group", choices=banks.group_ids())
    sp.add_argument("number", type=int)
    sp.add_argument(
        "--channel",
        type=int,
        default=1,
        dest="reach_channel",
        help="MIDI channel to address, 1-16",
    )
    sp.add_argument(
        "--with-effect",
        action="store_true",
        help="bring the sound's effect too (MULTI mode)",
    )
    sp.add_argument(
        "--dry-run",
        action="store_true",
        help="print the program changes a DAW must send; send nothing",
    )

    # read (with alias: syx)
    sp = sub.add_parser("read", aliases=["syx"], help="read a dump file (no hardware)")
    sp.add_argument("file")

    # status
    sub.add_parser("status", help="ask the unit who it is (silent)")

    # hardware (with aliases: config, inquire)
    sp = sub.add_parser(
        "hardware",
        aliases=["config", "inquire"],
        help="what the unit reports about itself",
    )

    # channels
    sub.add_parser("channels", help="every MIDI channel, and what it selects")

    # multi
    sub.add_parser("multi", help="multi-mode setup, and why a channel is silent")

    # dump
    sp = sub.add_parser("dump", help="dump a bank or preset off the unit (silent)")
    dsub = sp.add_subparsers(dest="dump_command", required=True)
    dp = dsub.add_parser("bank", help="dump the internal bank")
    dp = dsub.add_parser("sound", help="dump a single sound")
    dp.add_argument("group", choices=banks.group_ids())
    dp.add_argument("number", type=int)

    # send (write) - not implemented yet, but declared per spec
    sp = sub.add_parser("send", help="WRITE a dump into the unit")
    sp.add_argument("file")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = getattr(args, "config", None) or config.DEFAULT_PATH

    # The family's range checks, before anything is opened.
    validate_common(args)
    # On this instrument the unit's base channel and the channel a
    # program change is sent on are the same number: the SQ-R is
    # single-timbral and its SysEx device byte *is* that channel. So
    # --device-channel wins, --channel is the fallback, then the value
    # remembered from last time. Reading only --channel here is how
    # `ensqsqed --device-channel 11` came to sweep for channel 1.
    if getattr(args, "device_channel", None) is not None:
        channel = args.device_channel - 1
    else:
        channel = channel_of(args, default=config.load_channel(config_path)) or 0
    reach_channel = getattr(args, "reach_channel", None)
    if reach_channel is not None and not 1 <= reach_channel <= 16:
        raise SystemExit("error: --channel is 1-16")

    device = None
    # `reach INT 42 --dry-run` is a setup aid: it prints what to enter in a
    # DAW, so it must work with the unit switched off.
    dry_run = args.command in ("select", "reach") and bool(
        getattr(args, "dry_run", False)
    )
    if args.command not in OFFLINE and not dry_run:
        try:
            if args.port:
                device = b.SqBridge.open(
                    args.port,
                    recv_port=(
                        args.recv_port or config.load_last_recv_port(config_path)
                    ),
                    channel=channel,
                )
            else:
                device = b.SqBridge.connect(
                    config_path=config_path,
                    channel=channel,
                    scan=args.scan,
                    on_try=lambda n: print(f"  probing {n}...", file=sys.stderr),
                )
            try:
                port = getattr(device, "port", None)
                if port:
                    config.save_last_port(port, config_path)
                    config.save_channel(device.channel, config_path)
                recv = getattr(device, "recv_port", None)
                if recv and recv != getattr(device, "port", None):
                    config.save_last_recv_port(recv, config_path)
            except OSError:
                pass
        except Exception as exc:
            raise SystemExit(f"error: {exc}")

    try:
        COMMANDS[args.command](device, args)
    except (LookupError, TimeoutError, ValueError, RuntimeError, OSError) as exc:
        raise SystemExit(f"error: {exc}")
    finally:
        if device is not None:
            device.close()
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
