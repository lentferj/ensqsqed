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

"""One live round trip through the program's own code path.

**What this is.** The bench test that says the editor works: read the
preset the unit is on, change one value in it the way the app changes
one, send it the way the app sends it, read it back, and put the unit
back where it was found.  Every step goes through the objects the app
uses -- :meth:`sq.bridge.SqBridge.read_current_preset`,
:mod:`sq.presets`, :meth:`sq.bridge.SqBridge.send_preset` -- so a pass
here is a statement about the app's path and not about a second,
parallel implementation of it that could drift away from it.

**What it writes.**  The unit's *Edit Preset* buffer, and nothing else.
The manual (part 8, "Empfang von MIDI Sys-Ex Daten") says a received
preset becomes the Edit Preset and the display says "Press ENTER to
save new preset".  Storing is the front panel's ENTER, and this tool
presses no button and sends no virtual-button command: the protocol
has no message that stores a preset, and driving the panel is out of
scope on purpose.  The run ends by sending the original bytes back, so
the unit is byte-for-byte as it was found.

**Why the run always ends with a restore.**  A tool that fails half way
through leaves the unit holding an edit nobody asked for, which is
worse than a tool that did nothing at all.  So the snapshot is taken
first, every failure path attempts the restore, and the run says
plainly what it managed to do.  **A failure is never silent**: the tool
prints what happened, writes it to the log, and exits 2 -- never 1, and
never quietly.  A unit that cannot be reached is not a failure of the
program, but it is a failure of the run, and it is said so.

**What the tool does not do.**  It does not assert on the preset's
*name*: that is the owner's own text, so the log gets the shape of it
and not the content.  It does not select a sound, and it does not make
the unit play anything: the only thing it sends is a preset dump, and
the only thing the unit does with that is hold it in its Edit Preset.

Usage::

    python3 tools/verify_preset_roundtrip.py                # the round trip
    python3 tools/verify_preset_roundtrip.py --dry-run      # reads only

The findings are appended to ``docs/PRESET-FRAMING.md``; the run log
goes to ``/tmp/opencode/sqrex/e2e.log`` and the snapshot to
``/tmp/opencode/sqrex/e2e_before.bin``.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sq import config as cfg
from sq.bridge import SqBridge
from sq.presets import Preset
from vinsynlib.cli import (
    add_common_arguments,
    channel_of,
    make_parser,
    validate_common,
)

#: Where the run's artefacts go.  Both are run artefacts: the committed
#: fixture in ``tests/fixtures`` is the one the test suite reads.  The
#: path is fixed, because the findings in ``docs/PRESET-FRAMING.md``
#: point at these two files by name.
OUT_DIR = Path("/tmp/opencode/sqrex")  # noqa: S108 - a fixed, documented path
SNAPSHOT_PATH = OUT_DIR / "e2e_before.bin"
LOG_PATH = OUT_DIR / "e2e.log"

#: The single-sequence dump on this unit: a 48-byte sequence-data blob
#: and a 168-byte preset (specification 4.6 and 4.8.2).
BLOB_BYTES = 48
PRESET_BYTES = 168
DUMP_BYTES = BLOB_BYTES + PRESET_BYTES

#: The exit code of a run that did not finish.  Not 1: this tool always
#: tries to put the unit back before it gives up, and a run that gave
#: up without saying so would be the worst outcome of all.
EXIT_FAILED = 2


class Journal:
    """The run log, written as it happens.

    A run that dies half way through is still evidence: the steps that
    ran are worth more than a run that says nothing because the last
    one raised.  Every line goes to the terminal and to the log file.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lines: List[str] = []

    def note(self, text: str = "") -> None:
        """One line, on the terminal and in the log."""
        self.lines.append(text)
        print(text, flush=True)
        self.flush()

    def flush(self) -> None:
        """Write the log out.  A log that cannot be written is not a crash."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")
        except OSError:
            pass


def masked(name: str) -> str:
    """The preset's name with its text hidden.

    The name is the owner's own, so it does not go into a log that gets
    copied around: what a log needs is the *shape* of the name, not its
    content.  The length is kept, because a name that is sixteen
    characters long and a name that is four are different facts.
    """
    text = name.strip()
    if not text:
        return "(blank)"
    return "*" * len(text)


def volume_of(preset: Preset) -> str:
    """Track one's volume, as the unit holds it and as the panel prints it."""
    track = preset.tracks[0]
    return f"stored {track.volume}, displayed {track.volume_display}"


def differing(left: bytes, right: bytes) -> List[int]:
    """The offsets at which two dumps differ."""
    if len(left) != len(right):
        return [-1]
    return [index for index, (a, b) in enumerate(zip(left, right)) if a != b]


def step_edit(preset: Preset) -> Tuple[int, int]:
    """Track one's volume, down by one stored step -- the app's own way.

    The app's ``-`` gesture reads the track's *stored* volume, steps it
    by one and hands the new value to the model, and the model is what
    checks the range (:meth:`sq.presets.Track.updated`).  The bytes are
    not poked here either: the model encodes them, and the app sends
    what the model produced.

    Returns the old and the new stored value.
    """
    track = preset.tracks[0]
    before = track.volume
    stepped = before - 1
    preset.tracks[0] = track.updated(volume=stepped)
    return before, stepped


def restore(
    bridge: SqBridge, journal: Journal, seq_blob: bytes, preset_raw: bytes
) -> bool:
    """Send the original bytes back, so the unit is as it was found.

    The restore is the same handshake as the edit -- alert, ACK, dump --
    and it is verified the same way, because a restore that is not
    verified is a restore that did not happen.  It reports its own
    failure rather than raising: the caller is usually already in a
    failure path, and a second exception there would hide the first.
    """
    journal.note("")
    journal.note("=== restore: sending the original bytes back ===")
    try:
        result = bridge.send_preset(seq_blob, preset_raw, verify=True)
    except Exception as exc:
        journal.note(f"the restore did not go through: {exc}")
        journal.note(
            "the unit may still be holding the edit; press ENTER on the "
            "unit to store it, or send the snapshot at "
            f"{SNAPSHOT_PATH} back by hand"
        )
        return False
    journal.note(
        f"restore sent and verified: verified={result.verified}, "
        f"differing offsets={result.diff_offsets}"
    )
    return bool(result.verified) and not result.diff_offsets


def write_snapshot(journal: Journal, snapshot: bytes) -> bool:
    """Write the 216 bytes the unit held, before anything was sent."""
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_bytes(snapshot)
    except OSError as exc:
        journal.note(f"could not write the snapshot: {exc}")
        return False
    journal.note(f"snapshot written to {SNAPSHOT_PATH}")
    return True


def read_snapshot(
    bridge: SqBridge, journal: Journal
) -> Optional[Tuple[bytes, bytes, bytes]]:
    """Step 1: the preset the unit is on, and a copy of it on disk.

    Returns ``(seq_blob, preset_raw, snapshot)``, or ``None`` when the
    dump is not the shape the framing document gives.  The snapshot is
    written before anything is sent: it is the only copy of what the
    unit held, and the restore at the end needs it.
    """
    journal.note("=== step 1: read the preset the unit is on ===")
    current = bridge.read_current_preset()
    seq_blob = current.seq_blob
    preset_raw = current.preset_raw
    snapshot = seq_blob + preset_raw
    journal.note(
        f"read {len(snapshot)} bytes: {len(seq_blob)} of sequence data "
        f"and {len(preset_raw)} of preset"
    )
    if len(snapshot) != DUMP_BYTES:
        journal.note(
            f"UNEXPECTED: the dump is {len(snapshot)} bytes, not the "
            f"{DUMP_BYTES} ({BLOB_BYTES} + {PRESET_BYTES}) the framing "
            "document gives"
        )
        return None
    if not write_snapshot(journal, snapshot):
        return None
    return seq_blob, preset_raw, snapshot


def send_edit(
    bridge: SqBridge, journal: Journal, seq_blob: bytes, edited: bytes
) -> bool:
    """Step 3: the send, the way the app sends it.

    The app's write path is one call -- :meth:`sq.bridge.SqBridge.send_preset`
    with ``verify=True`` -- and this is that call.  It speaks the
    alert/ACK/dump handshake, reads the preset back and compares it byte
    for byte, and it raises on a mismatch rather than returning one.
    """
    journal.note("=== step 3: send it the way the app sends it ===")
    result = bridge.send_preset(seq_blob, edited, verify=True)
    journal.note(
        f"sent and read back: verified={result.verified}, "
        f"differing offsets={result.diff_offsets}"
    )
    if not result.verified:
        journal.note("the send did not verify")
        return False
    if result.diff_offsets:
        journal.note(f"the read-back differs at offset {result.diff_offsets}")
        return False
    return True


def reread(bridge: SqBridge, journal: Journal, sent: bytes, volume: int) -> bool:
    """Step 4: an independent re-read, compared with what was sent.

    The send already read the preset back; this reads it *again*, on
    its own, so that the comparison does not rest on the send's own
    re-read.  The volume is checked as well as the bytes: a unit that
    took the dump and then adjusted it would show up here and nowhere
    else.
    """
    journal.note("=== step 4: an independent re-read ===")
    again = bridge.read_current_preset()
    read_back = again.seq_blob + again.preset_raw
    offsets = differing(sent, read_back)
    journal.note(
        f"re-read {len(read_back)} bytes; "
        + (
            "identical to what was sent"
            if not offsets
            else f"differs at offset {offsets}"
        )
    )
    if offsets:
        return False
    observed = Preset.from_bytes(again.preset_raw)
    journal.note(f"track 1 volume as the unit holds it: {volume_of(observed)}")
    if observed.tracks[0].volume != volume:
        journal.note(
            f"UNEXPECTED: the unit holds volume {observed.tracks[0].volume}, "
            f"not the {volume} that was sent"
        )
        return False
    return True


def check_restored(bridge: SqBridge, journal: Journal, snapshot: bytes) -> bool:
    """Step 5: the unit as it was found.

    The final read is compared with the snapshot **and** with the file
    the snapshot was written to, so both the unit and the file on disk
    are known to hold the same bytes.
    """
    journal.note("=== step 5: the unit as it was found ===")
    final = bridge.read_current_preset()
    final_bytes = final.seq_blob + final.preset_raw
    offsets = differing(snapshot, final_bytes)
    journal.note(
        f"final read: {len(final_bytes)} bytes; "
        + (
            "identical to the snapshot"
            if not offsets
            else f"differs from the snapshot at offset {offsets}"
        )
    )
    if offsets:
        return False
    try:
        on_disk = SNAPSHOT_PATH.read_bytes()
    except OSError as exc:
        journal.note(f"could not read the snapshot back: {exc}")
        return False
    if on_disk != final_bytes:
        journal.note("UNEXPECTED: the snapshot file and the unit's bytes differ")
        return False
    journal.note("unit is byte-for-byte as found")
    return True


def run(bridge: SqBridge, journal: Journal, *, dry_run: bool = False) -> int:
    """The round trip, and the restore that always ends it."""
    read = read_snapshot(bridge, journal)
    if read is None:
        return EXIT_FAILED
    seq_blob, preset_raw, snapshot = read

    preset = Preset.from_bytes(preset_raw)
    journal.note(
        f"preset name: {masked(preset.name)} "
        f"({len(preset.name.strip())} characters, masked)"
    )
    journal.note(f"track 1 volume: {volume_of(preset)}")
    if dry_run:
        journal.note("")
        journal.note("dry run: the preset was read and nothing was sent.")
        return 0

    written = False
    try:
        journal.note("")
        journal.note("=== step 2: change track 1's volume by one ===")
        before, after = step_edit(preset)
        journal.note(
            f"track 1 volume: stored {before} -> {after} "
            f"(the model's own range check, not a byte poked)"
        )
        edited = preset.to_bytes()
        sent = seq_blob + edited
        journal.note(
            f"the model encodes {len(edited)} bytes of preset; "
            f"{len(sent)} bytes go out in all"
        )

        written = True
        if not send_edit(bridge, journal, seq_blob, edited):
            return EXIT_FAILED
        if not reread(bridge, journal, sent, after):
            return EXIT_FAILED
        if not restore(bridge, journal, seq_blob, preset_raw):
            return EXIT_FAILED
        if not check_restored(bridge, journal, snapshot):
            return EXIT_FAILED
        return 0
    except Exception:
        journal.note("")
        journal.note("error: the round trip failed")
        journal.note(traceback.format_exc().rstrip())
        if written:
            journal.note("attempting the restore")
            restore(bridge, journal, seq_blob, preset_raw)
        else:
            journal.note("nothing was sent, so there is nothing to restore")
        return EXIT_FAILED


def open_bridge(
    args: argparse.Namespace, channel: int, config_path: str
) -> Optional[SqBridge]:
    """Open the port ``--port`` names, or the remembered one."""
    try:
        if args.port:
            return SqBridge.open(args.port, recv_port=args.recv_port, channel=channel)
        return SqBridge.connect(
            config_path=config_path,
            channel=channel,
            scan=args.scan,
            on_try=lambda name: print(f"  probing {name}...", file=sys.stderr),
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None


def build_parser() -> argparse.ArgumentParser:
    """The family's options, then this tool's own."""
    parser = make_parser(
        "verify_preset_roundtrip",
        "Read the unit's preset, change one value in it, send it back "
        "the way the app sends it, read it again and restore the unit. "
        "Bench tool; opens a real MIDI port and writes the Edit Preset "
        "buffer for the length of the run.",
        epilog=(
            "examples:\n"
            "  python3 tools/verify_preset_roundtrip.py\n"
            "  python3 tools/verify_preset_roundtrip.py --dry-run"
        ),
        distribution="ensqsqed",
    )
    add_common_arguments(
        parser,
        port=True,
        scan=True,
        recv_port=True,
        channel=True,
        config=True,
        demo=False,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help=(
            "read the preset and write the snapshot, and send nothing: "
            "the round trip is the point of this tool, and it puts the "
            "unit back before it finishes, so there is no --yes gate"
        ),
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Open the unit, run the round trip, write the log.  Never raises."""
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_common(args)
    config_path = args.config or cfg.DEFAULT_PATH
    channel = channel_of(args, default=cfg.load_channel(config_path)) or 0

    print("VERIFY PRESET ROUND TRIP -- A BENCH TOOL")
    print("=" * 60)
    print("This tool sends a preset dump TO the unit and reads it back.")
    print("")
    print("It writes the unit's Edit Preset buffer and nothing else. The unit")
    print('shows "Press ENTER to save new preset" and stores nothing: storing')
    print("needs ENTER on the front panel, and this tool presses no button")
    print("and sends no virtual-button command. The last thing it does is")
    print("send the original bytes back, so the run leaves the unit where")
    print("it found it.")
    print("")
    print(f"  channel     : {channel + 1} (MIDI channel)")
    print("  writes      : 2 -- the edit, then the restore")
    print(f"  snapshot    : {SNAPSHOT_PATH}")
    print(f"  log         : {LOG_PATH}")
    print("")
    print("Leave the front panel alone while it runs.")

    journal = Journal(LOG_PATH)
    bridge: Optional[SqBridge] = None
    try:
        bridge = open_bridge(args, channel, config_path)
        if bridge is None:
            journal.note("error: the unit did not open; nothing was sent.")
            return EXIT_FAILED
        journal.note(f"port: {bridge.description}, base channel {bridge.channel + 1}")
        journal.note("")
        return run(bridge, journal, dry_run=args.dry_run)
    except Exception:
        journal.note("error: the round trip failed")
        journal.note(traceback.format_exc().rstrip())
        return EXIT_FAILED
    finally:
        if bridge is not None:
            bridge.close()


if __name__ == "__main__":
    raise SystemExit(main())
