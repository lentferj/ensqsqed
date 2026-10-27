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
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

from sq import banks
from sq import catalog as cat
from sq import config
from sq import dumps
from sq import importer
from sq import lib
from sq import messages as m
from sq import preset_labels
from sq import presets as p
from sq import sqs
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
                # The unit's display is two digits, counted from zero,
                # and the same number is the program change it sends.
                f"{item.program_change:02d}",
                str(item.bank),
                catalog.display_name(item.bank_id, item.number),
            ]
        )
    print(f"{entry.label} — {entry.count} sounds\n")
    print(_table(rows, ["#", "bank", "name"]))
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
    rows = [[f"{n:02d}", text.rstrip()] for n, text in sorted(names.items())]
    print(f"Internal — {len(names)} sounds\n")
    print(_table(rows, ["#", "name"]))


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
            f"\nThe unit shows this sound as {target.program_change:02d}; "
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
    if sqs.is_sqs_path(args.file) or lib.is_lib_path(args.file):
        source = importer.read_import(args.file)
        print(f"{args.file}: {source.summary()}")
        rows = [
            [f"{sound.index + 1:03d}", str(sound.index), sound.name]
            for sound in source.sounds
        ]
        print()
        print(_table(rows, ["shown", "PC", "name"]))
        return
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


def _cmd_multi(device, args) -> None:
    """Multi-mode setup, and why a channel is silent.

    **The preset is a performance setup, not a sound**: eight tracks
    with their own sound, mix, key range and MIDI settings, one effect
    that all eight share, and a pile that says which are selected or
    layered.  In MULTI mode **every** track with a channel receives;
    the pile does not gate the receiver.  A track is silent internally
    when its MIDI status is *EXT*, and that is what ``Status`` below
    means by OFF.

    The table is the panel's own layout: the eight tracks, their MIDI
    channel and status, the sound each one plays, and the mix and key
    parameters beside them.  The effect and its parameters follow,
    because all eight tracks share it.

    ``--preset N`` reads one of the eighty stored presets instead of
    the one the unit is on, which takes the whole all-presets dump and
    so takes about twelve seconds.  ``--out FILE`` writes the raw
    dump -- the wire bytes of the alert and the dump, which is what
    ``send`` takes.
    """
    catalog = cat.load(getattr(args, "catalog", None))
    if args.preset is None:
        current = device.read_current_preset()
        preset = p.Preset.from_bytes(current.preset_raw)
        seq_blob = current.seq_blob
        where = "current preset (edit buffer)"
    else:
        print(
            "reading all eighty presets; that takes about twelve seconds...",
            file=sys.stderr,
        )
        dump = device.read_all_presets()
        preset, seq_blob = preset_from_dump(dump, args.preset)
        where = f"preset slot {args.preset}"
    print(multi_report(preset, where, catalog))
    if args.out:
        wire = preset_wire(seq_blob, preset.to_bytes(), device.channel)
        _write_file(args.out, wire)
        print(f"wrote {len(wire)} bytes to {args.out}")


def _cmd_presets(device, args) -> None:
    """Dump the presets off the unit (silent).

    **A read.**  Nothing is sent but the dump request, and nothing on
    the unit changes.

    The current preset is written as the two messages the unit sends
    for it: the alert that says how much sequence data is coming, and
    the dump itself.  ``--all`` writes the all-presets dump instead,
    which is one message and about 36 KB on the wire -- and which
    ``send`` will refuse, because a whole memory is not a preset.

    The default file names are ``preset-current.syx`` and
    ``presets-all.syx``.
    """
    if args.all:
        dump = device.read_all_presets()
        path = args.out or DEFAULT_ALL_PRESETS_FILE
        wire = m.frame(
            m.Function.ALL_SEQUENCES_DUMP,
            m.to_nibbles(dump.to_bytes()),
            device=device.channel,
        )
        _write_file(path, wire)
        print(f"wrote {len(wire)} bytes to {path} ({dump.count} presets)")
        return
    current = device.read_current_preset()
    path = args.out or DEFAULT_PRESET_FILE
    wire = preset_wire(current.seq_blob, current.preset_raw, device.channel)
    _write_file(path, wire)
    print(f"wrote {len(wire)} bytes to {path}")


def _cmd_send(device, args) -> None:
    """WRITE a preset dump into the unit.  **This changes the unit.**

    The file is the wire bytes of a Single Sequence Dump message -- the
    type-04 message -- which is what ``presets --out`` writes and what
    the unit itself sends.

    **What it stores is the selected slot's track/pile range (32-151).**
    The unit takes the dump into its working buffer and stores that range
    in the slot the unit is on; the name (2-17) is the unit's own and is
    never taken from a dump, and the effect block (152-162) is outside
    that commit.  Press ENTER/SAVE on the unit to store the whole working
    buffer, effect included; the unit keeps its own name either way.

    The dump is read back and compared byte for byte, so a refusal and
    a mismatch are both reported rather than assumed away.
    """
    loaded = read_preset_file(args.file)
    if loaded.device != device.channel:
        print(
            f"note: the file was captured on channel {loaded.device + 1}; "
            f"the unit is on channel {device.channel + 1}"
        )
    if not args.yes:
        print(f"about to send {args.file} to the unit.")
        print(
            "The unit will store the track/pile range in the selected slot; "
            "the name stays the unit's own.  Press ENTER/SAVE on the unit "
            "to store the effect block too."
        )
        if input("proceed? [y/N] ").strip().lower() not in ("y", "yes"):
            print("nothing sent")
            return
    try:
        result = device.send_preset(loaded.seq_blob, loaded.preset_raw, verify=True)
    except b.PresetRefused as exc:
        raise SystemExit(f"error: {exc}") from None
    except b.PresetMismatch as exc:
        offsets = ", ".join(str(offset) for offset in exc.diff_offsets[:8])
        print(f"mismatch: {exc}", file=sys.stderr)
        raise SystemExit(
            f"error: the unit did not hold what was sent; "
            f"{len(exc.diff_offsets)} byte(s) differ at offset {offsets}"
        ) from None
    if result.verified:
        print("sent: the unit took the preset, and holds it byte for byte.")
    else:
        print("sent: the unit took the preset; the read-back was not compared.")
    print(
        "That stored the track/pile range; the name is the unit's own.  "
        "Press ENTER/SAVE on the unit to store the effect block too."
    )


def _cmd_backup(device, args) -> None:
    """Backup sounds and/or presets from the unit.

    Backs up the selected sound groups (INT, CARDA, CARDB) to files in the
    backup directory.  The user can select which groups and what content to
    back up (sounds, presets, or all content).

    The backup is written as raw SysEx bytes (or a Standard MIDI File if
    --format midi is selected).
    """
    from vinsynlib.cli import validate_common

    # The unit is already connected by the caller (main). Re-running
    # _connect here would discard the (possibly faked) device.
    validate_common(args)

    # Determine groups to back up
    groups = args.groups
    if "ALL" in groups:
        groups = ["INT", "CARDA", "CARDB"]

    # Determine content to back up
    content = args.content

    import os
    import time

    backup_root = config.load_backup_root()
    out_format = args.format
    stamp = time.strftime("%Y%m%d-%H%M%S")

    # Full backup via command 05.
    if content == "all" and "INT" in groups and hasattr(device, "read_everything"):
        everything = device.read_everything()
        if args.out is not None:
            main_file = args.out
        else:
            ext = "mid" if out_format == "midi" else "syx"
            main_file = os.path.join(backup_root, f"everything_{stamp}.{ext}")

        if out_format == "syx":
            main_data = everything.wire()
        else:
            frames = [everything.sounds_wire] + list(
                dumps.split_sysex(everything.presets_wire)
            )
            main_data = _smf_from_frames(frames)
        _write_file(main_file, main_data)
        print(
            f"{main_file}: sounds wire {len(everything.sounds_wire)} bytes, "
            f"presets wire {len(everything.presets_wire)} bytes, "
            f"total {len(main_data)} bytes"
        )

        main_dir = os.path.dirname(main_file) or "."
        if content in ("presets", "all"):
            labels_source = preset_labels.default_path()
            if os.path.exists(labels_source):
                labels = preset_labels.load(labels_source)
                labels_file = os.path.join(main_dir, f"preset_labels_{stamp}.json")
                preset_labels.save(labels, labels_file)
                print(f"backed up {len(labels)} local preset label(s) to {labels_file}")
        for group in ("CARDA", "CARDB"):
            if group not in groups:
                continue
            try:
                cdata = device.backup_sounds(group)
                if out_format == "syx":
                    cwire = m.frame(
                        m.Function.ALL_SOUNDS_DUMP,
                        m.to_nibbles(cdata),
                        device=device.channel,
                    )
                else:
                    cframe = m.frame(
                        m.Function.ALL_SOUNDS_DUMP,
                        m.to_nibbles(cdata),
                        device=device.channel,
                    )
                    cwire = _smf_from_frames([cframe])
                cfile = os.path.join(
                    main_dir,
                    f"{group.lower()}_sounds_{stamp}."
                    + ("mid" if out_format == "midi" else "syx"),
                )
                _write_file(cfile, cwire)
                print(f"backed up {group} sounds to {cfile} ({len(cdata)} bytes)")
            except Exception as exc:
                print(f"failed to backup {group} sounds: {exc}", file=sys.stderr)

        return

    # Determine output (per-group path)
    out_file = args.out
    if out_file is None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if len(groups) == 1:
            out_file = os.path.join(
                backup_root, f"{groups[0].lower()}_{content}_{stamp}.syx"
            )
        else:
            out_file = os.path.join(backup_root, f"all_{stamp}.syx")

    # Ensure output has correct extension
    if out_format == "midi":
        base, _ = os.path.splitext(out_file)
        out_file = base + ".mid"

    successful = 0
    failed = 0

    try:
        for group in groups:
            if content in ("sounds", "all"):
                try:
                    data = device.backup_sounds(group)
                    frame = m.frame(
                        m.Function.ALL_SOUNDS_DUMP,
                        m.to_nibbles(data),
                        device=device.channel,
                    )
                    wire = frame if out_format == "syx" else _smf_from_frames([frame])
                    _write_file(out_file, wire)
                    successful += 1
                    print(f"backed up {group} sounds to {out_file} ({len(data)} bytes)")
                except (ValueError, Exception) as e:
                    failed += 1
                    print(f"failed to backup {group} sounds: {e}", file=sys.stderr)

            if content in ("presets", "all"):
                try:
                    data = device.backup_presets(group)
                    frame = m.frame(
                        m.Function.ALL_SEQUENCES_DUMP,
                        m.to_nibbles(data),
                        device=device.channel,
                    )
                    wire = frame if out_format == "syx" else _smf_from_frames([frame])
                    _write_file(out_file, wire)
                    successful += 1
                    print(
                        f"backed up {group} presets to {out_file} ({len(data)} bytes)"
                    )
                except ValueError as e:
                    failed += 1
                    print(f"failed to backup {group} presets: {e}", file=sys.stderr)

    except Exception as e:
        failed += 1
        print(f"backup error: {e}", file=sys.stderr)

    if content in ("presets", "all"):
        labels_source = preset_labels.default_path()
        if os.path.exists(labels_source):
            labels = preset_labels.load(labels_source)
            labels_file = os.path.join(
                os.path.dirname(out_file) or ".",
                f"preset_labels_{stamp}.json",
            )
            preset_labels.save(labels, labels_file)
            print(f"backed up {len(labels)} local preset label(s) to {labels_file}")

    if successful:
        print(f"backup complete: {successful} success, {failed} failed")
    else:
        print(f"backup failed: {failed} error(s)", file=sys.stderr)


def _cmd_restore(device, args) -> None:
    """Restore sounds and/or presets to the unit from a backup file.

    Reads a backup file created by ``backup`` and writes the data to the
    unit.  The restore operation changes the unit's edit buffer and may
    require the user to press ENTER on the unit's panel to store the changes.

    Supported formats: .syx (SysEx) and .mid (Standard MIDI File).

    If the backup contains sounds (whole bank or single sounds), the import
    flow is used.  If it contains presets (all-presets dump), the preset
    handshake is used.  If the file is a single preset written by
    ``presets --out``, the original restore path is used.
    """
    import os
    from sq import dumps as d
    from sq import importer

    filepath = args.file
    if not os.path.exists(filepath):
        raise SystemExit(f"error: backup file not found: {filepath}")

    # A local-label sidecar: JSON, app-side, no unit contact.
    if filepath.lower().endswith(".json"):
        labels = preset_labels.load(filepath)
        if not args.yes:
            print(f"about to replace the local preset labels from {filepath}.")
            if input("proceed? [y/N] ").strip().lower() not in ("y", "yes"):
                print("nothing done")
                return
        preset_labels.save(labels)
        print(
            f"restored {len(labels)} local preset label(s) from {filepath}; "
            "the unit is untouched."
        )
        return

    if sqs.is_sqs_path(filepath):
        raise SystemExit(
            "error: restore from a .sqs file is not supported; use "
            "`sqcli read` and import in ensqsqed, or restore to Internal "
            "from the app"
        )

    if lib.is_lib_path(filepath):
        raise SystemExit(
            "error: restore from a .lib file is not supported; use "
            "`sqcli read` and import in ensqsqed, or restore to Internal "
            "from the app"
        )

    # Read the backup file
    try:
        library = d.read_file(filepath)
    except Exception as exc:
        raise SystemExit(f"error: cannot read backup file {filepath}: {exc}") from None

    # Determine what is in the backup
    has_sounds = library.is_full_bank

    if has_sounds:
        # --- Backup-format file: restore sounds and/or presets ---
        # Connect to the unit
        from vinsynlib.cli import validate_common

        validate_common(args)

        # --- Restore sounds ---
        if has_sounds:
            if not args.yes:
                print(
                    f"about to restore the sounds in {filepath} into Internal "
                    "(RAM). The unit's own SAVE SOUND is still needed, and a "
                    "card must be copied from Internal on the panel."
                )
                if input("proceed? [y/N] ").strip().lower() not in ("y", "yes"):
                    print("nothing sent")
                    return
            source = importer.read_import(filepath)
            try:
                existing = device.read_internal_bank()
                assignments = {sound.index: sound.raw for sound in source.sounds}
                merged = importer.merge_bank(existing, assignments)
                device.send_sound_bank("INT", merged)
            except Exception as exc:
                raise SystemExit(f"error: restore failed: {exc}") from None
            print(
                f"restored {source.summary()} into Internal (RAM) — press "
                "SAVE SOUND on the unit to store it; copy to a card from "
                "Internal on the panel."
            )

        # Done with backup-format file; return early.  Preset data is not
        # represented by Library: a raw all-presets file and a sequence
        # dump need the cooperative per-slot store, which the app owns.
        return

    # --- Fall through: this is a single-preset file (written by presets --out) ---
    # Read the preset file using the dedicated reader
    loaded = read_preset_file(filepath)
    if loaded.device != device.channel:
        print(
            f"note: the file was captured on channel {loaded.device + 1}; "
            f"the unit is on channel {device.channel + 1}"
        )
    if not args.yes:
        print(f"about to send {filepath} to the unit.")
        print(
            "The unit will hold it as its Edit Preset; storing it is "
            "ENTER on the unit's own panel."
        )
        if input("proceed? [y/N] ").strip().lower() not in ("y", "yes"):
            print("nothing sent")
            return
    current = device.read_current_preset()
    if not b.preset_needs_write(current.preset_raw, loaded.preset_raw):
        print(
            "the selected slot already matches the file in every byte a dump "
            "can write; nothing sent."
        )
        return
    try:
        result = device.send_preset(loaded.seq_blob, loaded.preset_raw, verify=True)
    except b.PresetRefused as exc:
        raise SystemExit(f"error: {exc}") from None
    except b.PresetMismatch as exc:
        offsets = ", ".join(str(offset) for offset in exc.diff_offsets[:8])
        print(f"mismatch: {exc}", file=sys.stderr)
        raise SystemExit(
            f"error: the unit did not hold what was sent; "
            f"{len(exc.diff_offsets)} byte(s) differ at offset {offsets}"
        ) from None
    if result.verified:
        print("sent: the unit took the preset, and holds it byte for byte.")
    else:
        print("sent: the unit took the preset; the read-back was not compared.")
    if result.kept_preset_offsets:
        offsets = ", ".join(str(o) for o in result.kept_preset_offsets)
        print(
            f"note: the unit kept its own values at preset offset(s) {offsets} "
            f"(name, tempo/effect regions or inactive-track programs)."
        )
    print("That is the Edit Preset: press ENTER on the unit to store it.")


# --- the preset, as the panel shows it -------------------------------------

#: The file ``presets`` writes when it is not told otherwise.
DEFAULT_PRESET_FILE = "preset-current.syx"

#: The file ``presets --all`` writes when it is not told otherwise.
DEFAULT_ALL_PRESETS_FILE = "presets-all.syx"

#: An empty sequence: its own length word and nothing else.  This is what
#: the unit's own single-preset dump held, and it is the fallback for a
#: preset whose sequence cannot be paired out of the all-presets dump.
EMPTY_SEQUENCE = b"\x00\x00\x00\x30" + b"\x00" * 44

#: The one line about MULTI, under the table.
MULTI_NOTE = (
    "MULTI: each of the eight tracks receives on its own MIDI channel, so "
    "the unit answers eight channels at once, and a track that is off does "
    "not receive at all. MIDI Mode=MULTI is a setting in the unit's System "
    "bank -- there is no message for it -- and an edited preset is stored "
    "by pressing ENTER on the unit."
)


class PresetFile(NamedTuple):
    """What a preset dump file holds.

    The two halves of a Single Sequence Dump, and the channel the file
    was captured on: the sequence data and the 168-byte preset, and the
    unit's base MIDI channel, which is the device byte of the message.
    """

    #: The sequence data, exactly as the unit sent it.
    seq_blob: bytes
    #: The 168-byte preset, exactly as the unit sent it.
    preset_raw: bytes
    #: The base MIDI channel the message was captured on, 0-15.
    device: int


def _panel_name(names: Sequence[str], value: int) -> str:
    """The panel's word for an enumerated value, or a question mark.

    A dump is a fact about the instrument's memory, so a value outside
    the documented range is shown as unknown rather than refused: the
    rest of the table is still worth reading.
    """
    return names[value] if 0 <= value < len(names) else "?"


def track_plays(preset: p.Preset, number: int) -> bool:
    """Whether a track plays internal voices.

    Two fields decide it: the pile (selected or layered) and the MIDI
    status.  A track out of the pile but still LOCAL receives and plays
    in MULTI mode, so the pile alone is not the answer.
    """
    return preset.is_active(number) and (
        preset.tracks[number - 1].status != p.EXTERNAL_STATUS
    )


def pile_mask(preset: p.Preset) -> str:
    """The panel's layer mask: ``123-56-8``.

    One character per track -- its number when the pile names it, a
    dash when it does not.  This is the *layering* mask the manual
    prints above the preset name (part 3, "MULTI mode"); a layered track
    can still be *EXT* and therefore silent.  Whether it plays is
    :func:`track_plays`.
    """
    return "".join(
        str(number) if preset.is_active(number) else "-"
        for number in range(1, len(preset.tracks) + 1)
    )


def sound_cell(track: p.Track, catalog: Optional[cat.Catalog] = None) -> str:
    """The sound a track plays, as the panel shows it.

    The group's label, the sound's number as the panel's display counts
    it (two digits, from zero), and the name the catalog has for it when
    the catalog knows it.  The catalog keys on the one-based number the
    ``Track.slot`` property reports, which is not the displayed number.
    The name is the catalog's, not the unit's: this is a read-only
    command and the unit's own names are read by ``names``.
    """
    group_id = track.group
    try:
        label = banks.group(group_id).label
    except LookupError:
        label = group_id
    number = track.slot
    program_change = (
        banks.DRUM_FIRST_PC + number - 1 if group_id == "DRUM" else number - 1
    )
    cell = f"{label} {program_change:02d}"
    if catalog is None:
        return cell
    name = catalog.display_name(group_id, number)
    return f"{cell} {name}" if name else cell


def track_rows(
    preset: p.Preset, catalog: Optional[cat.Catalog] = None
) -> List[List[str]]:
    """The preset's eight tracks, one row each.

    The columns are the panel's: the track, whether it plays internal
    voices, its MIDI channel, its MIDI status, its MIDI program, the
    sound it plays, and the mix and key parameters the panel shows
    beside them.

    **Two of those columns are about the same track and are not the
    same thing.**  ``Status`` is whether it plays: in the pile **and**
    not *EXT*.  ``Status-midi`` is the status byte itself: BOTH, LOCAL,
    MIDI or *EXT*.  A track can be layered and *EXT* -- it appears in
    the layer mask and still shows ``OFF`` here.
    """
    rows = []
    for number, track in enumerate(preset.tracks, start=1):
        rows.append(
            [
                str(number),
                "ON" if track_plays(preset, number) else "OFF",
                str(track.midi_channel_display),
                _panel_name(p.STATUS_NAMES, track.status),
                str(track.midi_program_display),
                sound_cell(track, catalog),
                str(track.volume_display),
                track.pan_label,
                f"{track.key_low_name}-{track.key_high_name}",
                f"{track.transpose_octave:+d}/{track.transpose_semitone:+d}",
                _panel_name(p.OUTPUT_NAMES, track.output),
            ]
        )
    return rows


def _effect_value(effect: p.Effect, row: p.EffectParam) -> str:
    """One effect parameter as the panel prints it, or a question mark."""
    try:
        return str(effect.display(row))
    except ValueError:
        return "?"


def effect_mix(preset: p.Preset) -> Tuple[str, str]:
    """The effect's two mix values, as the panel shows them.

    Every effect has them, and they are printed with the effect's name
    rather than with the parameters: they are the one part of the
    effect block that does not change with the effect's number.
    """
    mixes: Dict[str, str] = {}
    for row in preset.effect.rows:
        if row.key in ("fx1_mix", "fx2_mix"):
            mixes[row.key] = _effect_value(preset.effect, row)
    return mixes.get("fx1_mix", "-"), mixes.get("fx2_mix", "-")


def effect_rows(preset: p.Preset) -> List[List[str]]:
    """The effect's parameter rows, as the panel lists them.

    The rows come from the parameter set the effect's number selects
    (specification section 5.2).  The two mix values are not in this
    list: they are printed with the effect's name.
    """
    return [
        [row.label, _effect_value(preset.effect, row)]
        for row in preset.effect.rows
        if row.key not in ("fx1_mix", "fx2_mix")
    ]


def multi_report(
    preset: p.Preset, where: str, catalog: Optional[cat.Catalog] = None
) -> str:
    """The whole ``multi`` report, as text.

    Four parts: the header line (which preset this is, its name, and
    the panel's track mask), the eight tracks as a table, the effect
    with its parameters, and the one line about MULTI.
    """
    lines = [
        f"{where}  {preset.name.strip()!r}  tracks {pile_mask(preset)}",
        "",
        _table(
            track_rows(preset, catalog),
            [
                "Track",
                "Status",
                "Channel",
                "Status-midi",
                "Program",
                "Sound",
                "Vol",
                "Pan",
                "Key",
                "Transpose",
                "Output",
            ],
        ),
        "",
        f"effect  {preset.effect.name or '(an effect this project has no name for)'}",
    ]
    fx1, fx2 = effect_mix(preset)
    lines.append(f"FX1 MIX {fx1}   FX2 MIX {fx2}")
    lines.append(_table(effect_rows(preset), ["parameter", "value"]))
    lines.append("")
    lines.append(MULTI_NOTE)
    return "\n".join(lines)


# --- the wire ---------------------------------------------------------------


def preset_wire(seq_blob: bytes, preset_raw: bytes, channel: int = 0) -> bytes:
    """The wire bytes of one preset: the alert, then the dump.

    This is what the unit sends when it answers a Single Sequence Dump
    Request, and it is what ``send`` takes.  The alert says how much
    sequence data is coming -- the sequence data only, not the preset --
    and the dump is that data followed by the 168-byte preset.
    """
    alert = b.PresetAlert(
        command=m.CommandType.SINGLE_SEQUENCE_ALERT,
        size=len(seq_blob),
        flag=b.SEQUENCE_FLAG,
    )
    return m.frame(m.Function.COMMAND, alert.wire_payload(), device=channel) + m.frame(
        m.Function.SINGLE_SEQUENCE_DUMP,
        m.to_nibbles(seq_blob + preset_raw),
        device=channel,
    )


def parse_preset_message(message: bytes) -> PresetFile:
    """The sequence data and the preset in one type-04 message.

    The checks are the message's own: the head is ``F0 0F 06``, the
    message type is 04 -- a Single Sequence Dump -- and the payload
    de-nibbles to the sequence data followed by 168 bytes of preset.
    The sequence data says how long it is in its own first four bytes,
    so nothing here has to assume it.
    """
    if len(message) < 7:
        raise SystemExit(
            f"error: a preset dump message is at least 7 bytes, not {len(message)}"
        )
    if message[0] != m.SYSEX_START or message[1] != m.ENSONIQ_ID:
        raise SystemExit(
            "error: this is not an Ensoniq message: it does not start with F0 0F"
        )
    if message[2] != m.SQ_FAMILY:
        raise SystemExit(
            "error: this is not an SQ message: it does not start with F0 0F 06"
        )
    if message[5] != m.Function.SINGLE_SEQUENCE_DUMP:
        raise SystemExit(
            f"error: this is a {m.Function.name(message[5])}, not a Single "
            f"Sequence Dump (message type 04)"
        )
    data = m.from_nibbles(message[6:-1])
    if len(data) < 4:
        raise SystemExit(
            f"error: the dump holds {len(data)} bytes; it cannot even hold "
            f"the size word of its sequence data"
        )
    size = int.from_bytes(data[:4], "big")
    if size < 4 or len(data) != size + p.PRESET_BYTES:
        raise SystemExit(
            f"error: the dump holds {len(data)} bytes; its own size word "
            f"says {size} bytes of sequence data, and a preset is "
            f"{p.PRESET_BYTES}, so {size + p.PRESET_BYTES} were expected"
        )
    return PresetFile(seq_blob=data[:size], preset_raw=data[size:], device=message[4])


def read_preset_file(path: str) -> PresetFile:
    """The sequence data and the preset in a dump file.

    The file holds the wire bytes of a Single Sequence Dump message --
    the type-04 message -- optionally preceded by the alert that
    announced it, which is what ``presets --out`` writes and what the
    unit itself sends.  A file holding something else is refused by
    name rather than guessed at.
    """
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise SystemExit(f"error: cannot read {path}: {exc}") from None
    messages = dumps.split_sysex(raw)
    if not messages:
        raise SystemExit(f"error: {path} holds no SysEx message")
    found = [
        message
        for message in messages
        if len(message) > 5 and message[5] == m.Function.SINGLE_SEQUENCE_DUMP
    ]
    if len(found) != 1:
        raise SystemExit(
            f"error: {path} holds {len(found)} Single Sequence Dump "
            f"messages; exactly one is needed"
        )
    return parse_preset_message(found[0])


def preset_from_dump(dump: b.AllPresetsDump, slot: int) -> Tuple[p.Preset, bytes]:
    """One preset slot out of an all-presets dump, and its sequence data.

    The preset table maps a slot to the preset's place in the dump
    (``docs/PRESET-FRAMING.md`` section 4), and the preset's own first
    byte says which sequence it plays; the track-data table then points
    at that sequence's bytes.  A slot the unit has not defined has no
    preset, and says so.
    """
    offsets = dump.slot_offsets()
    if slot not in offsets:
        raise SystemExit(f"error: the unit has no preset in slot {slot}")
    preset = p.Preset.from_bytes(dump.presets_raw[offsets[slot]])
    return preset, sequence_blob(dump, preset.seqno)


def sequence_blob(dump: b.AllPresetsDump, seqno: int) -> bytes:
    """The sequence data a sequence number names, out of an all-presets dump.

    The track-data table is a list of offsets into the dump, one per
    sequence, and the blob there says how long it is in its own first
    four bytes.  A sequence the unit does not have falls back to the
    empty sequence, which is what the unit's own single-preset dump
    held.
    """
    table = dump.track_table
    if not 0 <= seqno < len(table) // 4:
        return EMPTY_SEQUENCE
    offset = int.from_bytes(table[seqno * 4 : seqno * 4 + 4], "big")
    if offset == 0:
        return EMPTY_SEQUENCE
    used = [
        index
        for index in range(len(table) // 4)
        if int.from_bytes(table[index * 4 : index * 4 + 4], "big")
    ]
    position = used.index(seqno)
    if position >= len(dump.track_blobs):
        return EMPTY_SEQUENCE
    return dump.track_blobs[position]


def _smf_from_frames(frames: Sequence[bytes]) -> bytes:
    """A format-0 Standard MIDI File with one SysEx event per frame."""

    def _varlen(value: int) -> bytes:
        result = [value & 0x7F]
        value >>= 7
        while value:
            result.append((value & 0x7F) | 0x80)
            value >>= 7
        result.reverse()
        return bytes(result)

    events = bytearray()
    for frame in frames:
        if frame and frame[0] == 0xF0 and frame[-1] == 0xF7:
            payload = frame[1:-1]
        else:
            payload = frame
        events += b"\x00" + b"\xf0" + _varlen(len(payload)) + payload + b"\xf7"
    events += b"\x00\xff\x2f\x00"

    header = (
        b"MThd"
        + (6).to_bytes(4, "big")
        + (0).to_bytes(2, "big")
        + (1).to_bytes(2, "big")
        + (96).to_bytes(2, "big")
    )
    track = b"MTrk" + len(events).to_bytes(4, "big") + bytes(events)
    return header + track


def _write_file(path: str, data: bytes) -> None:
    """Write a file, and say so by name if it cannot be written."""
    try:
        with open(path, "wb") as handle:
            handle.write(data)
    except OSError as exc:
        raise SystemExit(f"error: cannot write {path}: {exc}") from None


def _preset_slot(text: str) -> int:
    """A preset slot number, 0-79, as argparse wants it."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"preset slot {text!r} is not a number"
        ) from None
    if not 0 <= value < banks.PRESETS:
        raise argparse.ArgumentTypeError(f"preset slot is 0-{banks.PRESETS - 1}")
    return value


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
    "regions": _cmd_banks,
    "roms": _cmd_banks,
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
    "presets": _cmd_presets,
    "backup": _cmd_backup,
    "restore": _cmd_restore,
    "dump": _cmd_dump,
    "send": _cmd_send,
}


def _inherit(
    parent: argparse.ArgumentParser, sp: argparse.ArgumentParser, dest: str
) -> None:
    """Let a family option follow the subcommand as well as precede it.

    argparse only accepts a parent parser's option *before* the
    subcommand, and ``sqcli send preset.syx --yes`` is the shape people
    actually type.  Re-declaring the option keeps the family's own help
    text, taken from the action just added.  ``SUPPRESS`` is the
    load-bearing part: without it the subparser's default would
    overwrite a value given before the subcommand.

    This is the sibling project's idiom (``nanosyned`` writes it the
    same way), not an invention here.
    """
    for action in parent._actions:
        if action.dest != dest:
            continue
        extra: dict = {
            "dest": action.dest,
            "default": argparse.SUPPRESS,
            "help": action.help,
        }
        # A switch must stay a switch: re-declaring --yes as a value
        # option is how `send x.syx --yes` came to want an argument.
        if action.nargs == 0:
            extra["action"] = "store_true" if action.const else "store_false"
        else:
            extra["type"] = action.type
            extra["metavar"] = action.metavar
        sp.add_argument(*action.option_strings, **extra)
        return


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
    sp = sub.add_parser("multi", help="multi-mode setup, and why a channel is silent")
    sp.add_argument(
        "--preset",
        type=_preset_slot,
        metavar="N",
        help=(
            "read preset slot N (0-79) out of all eighty, not the one the "
            "unit is on; this takes the whole all-presets dump, so it "
            "takes about twelve seconds"
        ),
    )
    sp.add_argument(
        "--out",
        "-o",
        metavar="FILE",
        help="write the raw dump to FILE (the wire bytes `send` takes)",
    )

    # presets
    sp = sub.add_parser("presets", help="dump the presets off the unit (silent)")
    sp.add_argument(
        "--all",
        action="store_true",
        help="dump all eighty presets, not the one the unit is on",
    )
    sp.add_argument(
        "--out",
        "-o",
        metavar="FILE",
        help=(
            "write the raw dump to FILE (default: preset-current.syx, or "
            "presets-all.syx)"
        ),
    )

    # backup
    sp = sub.add_parser("backup", help="backup sounds/presets from the unit")
    sp.add_argument(
        "--groups",
        "-g",
        metavar="GROUP",
        choices=["INT", "CARDA", "CARDB", "ALL"],
        nargs="+",
        default=["ALL"],
        help=(
            'sound groups to back up.  "ALL" backs up INT, CARDA, and CARDB. '
            'Multiple groups may be selected, e.g. "INT CARDA".'
        ),
    )
    sp.add_argument(
        "--content",
        "-c",
        metavar="CONTENT",
        choices=["sounds", "presets", "all"],
        default="sounds",
        help=("what to back up: sounds, presets, or all content"),
    )
    sp.add_argument(
        "--out",
        "-o",
        metavar="FILE",
        help=(
            "write the backup to FILE (default: stamp-named file under "
            "backup-root from config)"
        ),
    )
    sp.add_argument(
        "--format",
        "-f",
        metavar="FORMAT",
        choices=["syx", "midi"],
        default="syx",
        help=(
            "output format: .syx (SysEx) or .mid (Standard MIDI File); default is .syx"
        ),
    )

    # restore
    sp = sub.add_parser("restore", help="restore sounds/presets to the unit")
    sp.add_argument(
        "file",
        help="backup file to restore from (created by `backup`)",
    )
    # The gate may follow the command as well as precede it.
    _inherit(parser, sp, "yes")

    # dump
    sp = sub.add_parser("dump", help="dump a bank or preset off the unit (silent)")
    dsub = sp.add_subparsers(dest="dump_command", required=True)
    dp = dsub.add_parser("bank", help="dump the internal bank")
    dp = dsub.add_parser("sound", help="dump a single sound")
    dp.add_argument("group", choices=banks.group_ids())
    dp.add_argument("number", type=int)

    # send (write)
    sp = sub.add_parser("send", help="WRITE a dump into the unit")
    sp.add_argument(
        "file",
        help=(
            "a Single Sequence Dump (.syx), as `presets --out` writes it: "
            "the wire bytes of a type-04 message"
        ),
    )
    # The gate may follow the command as well as precede it.
    _inherit(parser, sp, "yes")

    return parser


def _connect(args, config_path: str, channel: int) -> b.SqBridge:
    """Open the unit.  The one place this program opens a MIDI port.

    A seam, and deliberately so: the tests replace this one function
    with a fake device, and the rest of the command line never learns
    that a port was not opened.
    """
    if args.port:
        return b.SqBridge.open(
            args.port,
            recv_port=(args.recv_port or config.load_last_recv_port(config_path)),
            channel=channel,
        )
    return b.SqBridge.connect(
        config_path=config_path,
        channel=channel,
        scan=args.scan,
        on_try=lambda n: print(f"  probing {n}...", file=sys.stderr),
    )


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
    # DAW, so it must work with the unit switched off.  A local-label
    # sidecar restore is app-side only and needs no unit either.
    dry_run = args.command in ("select", "reach") and bool(
        getattr(args, "dry_run", False)
    )
    labels_only = args.command == "restore" and str(args.file).lower().endswith(".json")
    if args.command not in OFFLINE and not dry_run and not labels_only:
        try:
            device = _connect(args, config_path, channel)
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
