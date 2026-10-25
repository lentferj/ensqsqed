#!/usr/bin/env python3
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

"""Bench probe: change one preset's name in a type-04 file and send it back.

The unit's dump path commits only preset offsets 32-151, so the name
(offsets 2-17) is expected to be kept by the unit.  This probe tests that
with one slot: it reads the all-presets dump, takes the slot's own preset
and sequence, writes the original and a renamed single-preset file, selects
the slot, sends the renamed file with the real handshake, and reads the
name back.

Nothing but the name is changed, so the slot's own content is idempotent.
The two files are kept beside the capture for later analysis.

Usage::

    python tools/probe_rename_slot.py --slot 10 --name TEST-10
    python tools/probe_rename_slot.py --slot 10 --name TEST-10 --dry-run
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ensqsqed import cli
from sq import bridge as b
from sq import config as cfg
from sq.presets import Preset


def _connect(args: argparse.Namespace) -> b.SqBridge:
    if args.port:
        return b.SqBridge.open(
            args.port, recv_port=args.recv_port or args.port, channel=args.channel
        )
    pair = cfg.load_port_pair()
    if pair is None:
        raise SystemExit("no remembered port; pass --port/--recv-port")
    return b.SqBridge.open(pair[0], recv_port=pair[1], channel=args.channel)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slot", type=int, default=10, help="zero-based preset slot")
    parser.add_argument("--name", default="TEST-10", help="new name (16 chars max)")
    parser.add_argument("--port")
    parser.add_argument("--recv-port")
    parser.add_argument("--channel", type=int, default=None)
    parser.add_argument(
        "--dry-run", action="store_true", help="write the files, send nothing"
    )
    parser.add_argument("--out", help="output directory for the .syx files")
    args = parser.parse_args(argv)

    if not 0 <= args.slot < 80:
        raise SystemExit("--slot is 0..79")
    channel = cfg.load_channel() if args.channel is None else args.channel
    args.channel = channel

    out_dir = (
        Path(args.out)
        if args.out
        else Path(cfg.load_backup_root()) / time.strftime("%Y%m%d-%H%M%S_rename")
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    link = _connect(args)
    try:
        print(f"port {link.port} -> {link.recv_port}, channel {channel}")
        print("reading all 80 presets (about 12 s)...")
        dump = link.read_all_presets()
        preset, seq_blob = cli.preset_from_dump(dump, args.slot)
        old_name = preset.name.strip()
        print(f"P{args.slot:02d} is {old_name!r}, sequence {len(seq_blob)} B")

        original_raw = preset.to_bytes()
        original_file = out_dir / f"P{args.slot:02d}_original.syx"
        original_file.write_bytes(
            cli.preset_wire(seq_blob, original_raw, channel=link.channel)
        )

        preset.name = args.name[:16]
        renamed_raw = preset.to_bytes()
        renamed_file = out_dir / f"P{args.slot:02d}_{args.name[:16]}.syx"
        renamed_file.write_bytes(
            cli.preset_wire(seq_blob, renamed_raw, channel=link.channel)
        )
        print(f"wrote {original_file}")
        print(f"wrote {renamed_file}")

        if args.dry_run:
            print("dry run: nothing sent")
            return 0

        # The destination is always the unit's current selection.
        current = link.read_current_preset()
        known_slot = current.preset_raw[0] if current.preset_raw else 0
        print(f"walking from slot {known_slot} to slot {args.slot}...")
        link.select_preset_by_walk(args.slot, known_slot=known_slot)
        print("sending the renamed single-preset dump...")
        link.send_preset(seq_blob, renamed_raw, verify=True)
        after = link.read_current_preset()
        new_name = Preset.from_bytes(after.preset_raw).name.strip()
        print(f"read back: {new_name!r}")
        if new_name == args.name[:16].strip():
            print("RESULT: the unit took the new name.")
            return 0
        print(
            "RESULT: the unit kept its own name.  This confirms the commit "
            "radius (offsets 32-151): a dump cannot rename a preset."
        )
        return 1
    finally:
        link.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
