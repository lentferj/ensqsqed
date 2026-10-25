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

"""Bench probe: can the panel's save dialog be driven by virtual buttons?

The dump's automatic store writes only preset offsets 32-151, so an effect
block change (152-163) plays but is not stored until the panel's ENTER/SAVE
dialog runs.  This probe makes exactly such a change -- one effect parameter
-- then taps the Enter/Save button (9) a few times, reads the stored slot
back, and reports whether the effect landed.  It then restores the slot's
original bytes and runs the same sequence again, so the unit is left as it
was found.

A full all-presets dump is read before anything is written and compared
against a second read at the end, so collateral changes to other slots are
visible rather than assumed away.

Usage::

    python tools/probe_save_sequence.py --slot 10
    python tools/probe_save_sequence.py --slot 10 --presses 4 --gap 0.6
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Dict, Optional

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


def _stored(link: b.SqBridge, slot: int) -> bytes:
    dump = link.read_all_presets()
    preset, _ = cli.preset_from_dump(dump, slot)
    return preset.to_bytes()


def _all_slots(link: b.SqBridge) -> Dict[int, bytes]:
    dump = link.read_all_presets()
    out: Dict[int, bytes] = {}
    for slot in dump.slot_offsets():
        preset, _ = cli.preset_from_dump(dump, slot)
        out[slot] = preset.to_bytes()
    return out


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slot", type=int, default=10)
    parser.add_argument("--presses", type=int, default=4)
    parser.add_argument("--gap", type=float, default=0.6)
    parser.add_argument("--port")
    parser.add_argument("--recv-port")
    parser.add_argument("--channel", type=int, default=None)
    parser.add_argument(
        "--keep",
        action="store_true",
        help="leave the test effect on the slot (default: restore the original)",
    )
    args = parser.parse_args(argv)

    channel = cfg.load_channel() if args.channel is None else args.channel
    args.channel = channel
    if not 0 <= args.slot < 80:
        raise SystemExit("--slot is 0..79")
    if not 1 <= args.presses <= 12:
        raise SystemExit("--presses is 1..12")

    link = _connect(args)
    try:
        print(f"port {link.port} -> {link.recv_port}, channel {channel}")
        print("reading all 80 stored presets...")
        before_all = _all_slots(link)
        original = before_all[args.slot]

        dump = link.read_all_presets()
        preset, seq_blob = cli.preset_from_dump(dump, args.slot)
        print(f"P{args.slot:02d} stored effect: {preset.effect.params.hex(' ')}")

        # One effect parameter, chosen to differ from what is there.
        params = bytearray(preset.effect.params)
        old = params[0]
        params[0] = (old + 1) % 128
        preset.effect = type(preset.effect)(
            number=preset.effect.number, params=bytes(params)
        )
        test_raw = preset.to_bytes()
        print(
            f"test: effect param 0 {old} -> {params[0]}; "
            f"the dump writes the working preset but not the stored effect"
        )

        current = link.read_current_preset()
        known_slot = current.preset_raw[0] if current.preset_raw else 0
        link.select_preset_by_walk(args.slot, known_slot=known_slot)
        link.send_preset(seq_blob, test_raw, verify=True)

        after_send_working = link.read_current_preset()
        after_send_stored = _stored(link, args.slot)
        print(
            "after the dump: working effect "
            f"{Preset.from_bytes(after_send_working.preset_raw).effect.params.hex(' ')}"
            f" / stored effect "
            f"{Preset.from_bytes(after_send_stored).effect.params.hex(' ')}"
        )
        if after_send_stored[152:164] != original[152:164]:
            print(
                "unexpected: the dump already changed the stored effect; "
                "this probe cannot separate the save sequence from the dump."
            )

        for index in range(args.presses):
            print(f"  tap {index + 1}/{args.presses}: button 9 (Enter/Save)")
            link.tap_button(b.ENTER_SAVE_BUTTON)
            time.sleep(args.gap)

        after_save_stored = _stored(link, args.slot)
        saved = after_save_stored[152:164] != original[152:164]
        print(
            f"after {args.presses} tap(s): stored effect "
            f"{Preset.from_bytes(after_save_stored).effect.params.hex(' ')}"
        )
        print(
            "RESULT:",
            "the save sequence stored the effect"
            if saved
            else "the effect was NOT stored",
        )

        other_changes = {
            slot: raw
            for slot, raw in _all_slots(link).items()
            if slot != args.slot and raw != before_all.get(slot)
        }
        if other_changes:
            print(
                "WARNING: other slots changed: "
                + ", ".join(f"P{slot:02d}" for slot in sorted(other_changes))
            )
        else:
            print("no other slot changed")

        if not args.keep:
            print("restoring P10's original bytes and running the sequence again...")
            current = link.read_current_preset()
            known_slot = current.preset_raw[0] if current.preset_raw else 0
            link.select_preset_by_walk(args.slot, known_slot=known_slot)
            link.send_preset(seq_blob, original, verify=True)
            for _ in range(args.presses):
                link.tap_button(b.ENTER_SAVE_BUTTON)
                time.sleep(args.gap)
            restored = _stored(link, args.slot)
            print(
                "restored:",
                "yes" if restored == original else "NO -- compare the backup",
                f"(effect {Preset.from_bytes(restored).effect.params.hex(' ')})",
            )
        return 0 if saved else 1
    finally:
        link.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
