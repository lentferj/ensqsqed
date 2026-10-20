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

"""What a preset dump does to the SQ-R's Edit Preset buffer.

The question this tool answers is narrow, and it is the one the editor
depends on: **when a preset dump is sent to the unit, what does a Single
Sequence Dump Request (command 03) return afterwards?**

Three answers are possible, and they mean different things for an
"apply, then read back and compare" workflow:

* the **stored** preset -- the write went nowhere, and a read-back
  verifies nothing at all;
* the **Edit Preset** -- the write landed in the buffer the manual
  describes, and a read-back is a real verification of the bytes sent;
* something in between -- the buffer was entered, but the read returns a
  mixture, and no verification can be built on it.

The German manual (Part 8, "Empfang von MIDI Sys-Ex Daten") says what
the unit *does* with a received preset: the display shows "Press ENTER to
save new preset", the new preset becomes the Edit Preset, and the unit
switches to Preset Select Mode with the Edit Preset LED blinking. What
the manual does not say is what a dump request returns while that Edit
Preset is live, and that is what this tool measures.

**What it writes, and what it does not.** Every write here is a preset
dump into the **Edit Preset buffer**. Nothing is stored: storing needs
ENTER on the unit's front panel, and this tool presses no button and
sends no virtual-button command at all. The unit is left holding the
original bytes, because the last thing the tool does is send the
snapshot back -- so the run is idempotent, and a second run starts from
the same place the first one did.

**The order is the point.** The steps are snapshot, send unchanged,
re-read, send with one byte changed, re-read, restore, re-read. The
snapshot has to come first, because it is the only copy of what the unit
held before the tool touched it, and the restore has to come last, so
that the unit is where it was found.

Usage::

    python3 tools/probe_preset_write.py                    # dry run
    python3 tools/probe_preset_write.py --yes
    python3 tools/probe_preset_write.py --yes --port "X:1"

The findings are appended to ``docs/PRESET-FRAMING.md``; the raw run log
is written to ``/tmp/opencode/sqrex/probe_write.log`` and the snapshot
to ``/tmp/opencode/sqrex/probe_snapshot_before.bin``.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sq import config as cfg
from sq import messages as m
from sq.bridge import SqBridge
from vinsynlib.cli import (
    add_common_arguments,
    channel_of,
    make_parser,
    validate_common,
)

#: Where the run's artefacts go. Both are run artefacts: the committed
#: fixture in ``tests/fixtures`` is the one the test suite reads. The path
#: is fixed, because the findings in ``docs/PRESET-FRAMING.md`` point at
#: these two files by name.
OUT_DIR = Path("/tmp/opencode/sqrex")  # noqa: S108 - a fixed, documented path
SNAPSHOT_PATH = OUT_DIR / "probe_snapshot_before.bin"
LOG_PATH = OUT_DIR / "probe_write.log"

#: The specification's own window, quoted rather than invented: "If the
#: receiver does not respond within one second, the transmitter will send
#: the dump message anyway" (specification 3, Sequence Dump Protocol).
ACK_WINDOW = 1.0

#: How long to wait for a dump. The reply is 216 bytes, and the unit
#: redraws its screen before it answers.
DUMP_TIMEOUT = 15.0

#: The Single Sequence Dump payload: a sequence-data blob and one preset
#: (specification 4.6, 4.8). The blob's own size is its first long word,
#: so the split is read from the data rather than assumed -- but it is 48
#: on this unit, and the framing document says so.
BLOB_BYTES = 48
PRESET_BYTES = 168
DUMP_BYTES = BLOB_BYTES + PRESET_BYTES

#: The sequence/song flag of the alert: 0 is a sequence, 255 a song
#: (specification 3.1.7). This is a sequence dump.
SEQUENCE_FLAG = 0x00

#: Track 1's volume, in dump coordinates: the preset starts after the
#: sequence-data blob, track 1's parameters start at preset offset 32,
#: and the volume is the third byte of a track (specification 4.8.1).
TRACK1_VOLUME_OFFSET = BLOB_BYTES + 32 + 2

#: How long to let the unit settle after a dump before asking it what it
#: holds. The manual says the unit changes mode on receipt, and a mode
#: change is a screen redraw.
SETTLE = 0.5


class Journal:
    """The run log, written as it happens.

    A probe that dies half way through is still evidence: the steps that
    ran are worth more than a run that says nothing because the last one
    raised. Every line goes to the terminal and to the log file.
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
        """Write the log out. A log that cannot be written is not a crash."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")
        except OSError:
            pass


@dataclass
class Capture:
    """One answer from the unit: the alert that preceded it, and the dump."""

    #: The de-nibbled alert body (command byte, four size bytes, flag),
    #: or ``None`` if the unit sent no alert.
    alert: Optional[bytes]
    #: The de-nibbled payload of the type-04 message.
    dump: bytes


@dataclass
class SendResult:
    """What one write attempt did."""

    #: The error code the unit answered the alert with (1 = ACK,
    #: 0 = NAK), or ``None`` if the unit said nothing at all.
    code: Optional[int]
    #: How long the ACK took, in seconds.
    seconds: float
    #: Whether the dump was sent after the ACK.
    sent: bool
    #: Anything else worth knowing, in words.
    note: str = ""


@dataclass
class Findings:
    """What the probe learned, for the summary and for the document."""

    #: How long the ACK took, for each of the three writes.
    ack_unchanged: Optional[float] = None
    ack_changed: Optional[float] = None
    ack_restore: Optional[float] = None
    #: Where the re-read after the unchanged send differs from the
    #: snapshot. Empty means identical.
    unchanged_offsets: List[int] = field(default_factory=list)
    #: Whether the one changed byte came back changed.
    change_visible: Optional[bool] = None
    #: Where the re-read after the restore differs from the snapshot.
    restore_offsets: List[int] = field(default_factory=list)


def split_dump(dump: bytes) -> Tuple[bytes, bytes]:
    """The sequence-data blob and the preset, as the dump divides itself.

    The blob begins with its own size as a long word, and the preset
    follows it (specification 4.6: sequence data, then track data, then
    the 168-byte preset). The split is therefore read from the data
    rather than assumed, and the remainder is checked against the 168
    bytes the specification gives the preset.
    """
    if len(dump) < 4:
        raise ValueError(f"a dump of {len(dump)} bytes has no size word in it")
    size = struct.unpack(">I", dump[:4])[0]
    if size + PRESET_BYTES != len(dump):
        raise ValueError(
            f"the dump's own size word says {size} bytes of sequence data, "
            f"which leaves {len(dump) - size} bytes for a {PRESET_BYTES}-byte "
            f"preset; the two do not agree"
        )
    return dump[:size], dump[size:]


def read_dump(bridge: SqBridge, *, timeout: float = DUMP_TIMEOUT) -> Capture:
    """Command 03, and the alert that precedes its answer.

    The alert and the dump are one exchange and both matter: the alert
    carries the size of the sequence-data segment and the sequence/song
    flag, and it is the same alert the write path has to reproduce.

    The bridge's own :meth:`~sq.bridge.SqBridge.request` cannot be used
    here: it waits for one message type and drops the alert on the way
    past, and the sequence dump protocol needs both. So this reaches
    into the bridge's ports, which is what the probe is for -- the
    bridge does not speak this protocol yet.
    """
    bridge._drain()
    bridge._out.send_message(
        list(
            m.command_message(
                m.CommandType.SINGLE_SEQUENCE_REQUEST, channel=bridge.channel
            )
        )
    )
    deadline = time.monotonic() + timeout
    alert: Optional[bytes] = None
    while time.monotonic() < deadline:
        message = bridge._in.get_message()
        if message is None:
            time.sleep(0.002)
            continue
        packet = m.parse(message[0])
        if packet is None or packet.device != bridge.channel:
            continue
        if packet.function == m.Function.COMMAND:
            body = m.from_nibbles(packet.payload)
            if body and body[0] == m.CommandType.SINGLE_SEQUENCE_ALERT:
                alert = body
            continue
        if packet.function == m.Function.SINGLE_SEQUENCE_DUMP:
            return Capture(alert=alert, dump=m.from_nibbles(packet.payload))
    raise TimeoutError(
        "no Single Sequence Dump came back from the Single Sequence Dump "
        "Request. The unit did not refuse it either, so this means one "
        "of: powered off, the wrong port, the wrong base MIDI channel, "
        "System Exclusive reception switched off in the System bank, or a "
        "chain that carries requests out but not replies back."
    )


def send_alert(bridge: SqBridge, seq_blob: bytes) -> Tuple[Optional[int], float]:
    """The alert, and the wait for the answer to it.

    The alert is a command message whose command type is 06, carrying the
    size of the sequence data that will follow and the sequence/song flag
    (specification 3.1.7). The unit answers with an error message whose
    code is ACK (1) or NAK (0) (specification 3.2.1).

    Returns the code and how long it took, or ``(None, seconds)`` if the
    unit said nothing inside the one-second window the specification
    allows.
    """
    size = struct.pack(">I", len(seq_blob))
    bridge._out.send_message(
        list(
            m.command_message(
                m.CommandType.SINGLE_SEQUENCE_ALERT,
                [*size, SEQUENCE_FLAG],
                channel=bridge.channel,
            )
        )
    )
    started = time.monotonic()
    deadline = started + ACK_WINDOW
    while time.monotonic() < deadline:
        message = bridge._in.get_message()
        if message is None:
            time.sleep(0.001)
            continue
        packet = m.parse(message[0])
        if packet is None or packet.device != bridge.channel:
            continue
        if packet.function != m.Function.ERROR or not packet.payload:
            continue
        code = m.from_nibbles(packet.payload)[:1]
        return (code[0] if code else None), time.monotonic() - started
    return None, time.monotonic() - started


def send_dump(bridge: SqBridge, dump: bytes) -> None:
    """The type-04 message: the whole dump, nibble-ized.

    This is the message the unit itself sends as the answer to command
    03, so it is the same bytes in the same order -- the sequence-data
    blob first, then the 168-byte preset.
    """
    bridge._out.send_message(
        list(
            m.frame(
                m.Function.SINGLE_SEQUENCE_DUMP,
                m.to_nibbles(dump),
                device=bridge.channel,
            )
        )
    )


def write_preset(bridge: SqBridge, dump: bytes) -> SendResult:
    """The write path: alert, wait for the ACK, then the dump.

    The specification's handshake, in the specification's order: the
    transmitter announces the dump, the receiver answers ACK or NAK, and
    the dump follows only on an ACK (specification 3, Sequence Dump
    Protocol). A NAK means the dump is not acceptable, so it is not sent.
    """
    seq_blob, _preset = split_dump(dump)
    code, elapsed = send_alert(bridge, seq_blob)
    if code != m.ErrorCode.ACK:
        if code is None:
            note = (
                f"the unit said nothing within {ACK_WINDOW:.1f} s of the "
                "alert; the specification allows the dump to be sent anyway "
                "after that, but this probe does not send it"
            )
        else:
            note = f"the unit refused the dump: {m.ErrorCode.name(code)}"
        return SendResult(code=code, seconds=elapsed, sent=False, note=note)
    send_dump(bridge, dump)
    return SendResult(code=code, seconds=elapsed, sent=True)


def differences(left: bytes, right: bytes) -> List[int]:
    """The offsets at which two dumps differ."""
    if len(left) != len(right):
        return [-1]
    return [index for index, (a, b) in enumerate(zip(left, right)) if a != b]


def describe_difference(offsets: List[int]) -> str:
    """The differing offsets, in words rather than as a list of numbers."""
    if not offsets:
        return "identical"
    if offsets == [-1]:
        return "different lengths"
    shown = ", ".join(str(offset) for offset in offsets[:24])
    if len(offsets) > 24:
        shown += f", ... and {len(offsets) - 24} more"
    return f"{len(offsets)} byte(s) differ at offset {shown}"


def banner(port: str, channel: int) -> None:
    """What is about to happen, before anything is opened."""
    print("PROBE PRESET WRITE -- A BENCH TOOL")
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
    print("It does change what the unit shows and plays: the Edit Preset is")
    print("the preset you hear until Select Preset is pressed.")
    print("")
    print(f"  port        : {port}")
    print(f"  channel     : {channel + 1} (MIDI channel)")
    print("  writes      : 3 -- the snapshot, one changed byte, the restore")
    print(f"  snapshot    : {SNAPSHOT_PATH}")
    print(f"  log         : {LOG_PATH}")
    print("")
    print("Leave the front panel alone while it runs.")


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


def step_snapshot(bridge: SqBridge, journal: Journal) -> Optional[bytes]:
    """Step 1: read the current preset and keep a copy of it.

    Returns the 216-byte dump, or ``None`` if it could not be read or
    did not have the shape the framing document gives.
    """
    journal.note("=== step 1: snapshot (command 03) ===")
    capture = read_dump(bridge)
    dump = capture.dump
    journal.note(f"dump: {len(dump)} bytes")
    if capture.alert is None:
        journal.note("alert: none -- the unit sent no Single Sequence Dump Alert")
    else:
        journal.note(f"alert: {' '.join(f'{b:02X}' for b in capture.alert)}")
    try:
        seq_blob, preset_raw = split_dump(dump)
    except ValueError as exc:
        journal.note(f"UNEXPECTED: {exc}")
        return None
    journal.note(
        f"split: {len(seq_blob)} bytes of sequence data + "
        f"{len(preset_raw)} bytes of preset"
    )
    if capture.alert is not None and len(capture.alert) >= 6:
        alert_size = struct.unpack(">I", capture.alert[1:5])[0]
        journal.note(
            f"alert size field: {alert_size} "
            f"(the sequence-data segment is {len(seq_blob)} bytes)"
        )
    if len(dump) != DUMP_BYTES:
        journal.note(
            f"UNEXPECTED: the dump is {len(dump)} bytes, not the "
            f"{DUMP_BYTES} ({BLOB_BYTES} + {PRESET_BYTES}) the framing "
            f"document gives"
        )
        return None
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_bytes(dump)
    except OSError as exc:
        journal.note(f"could not write the snapshot: {exc}")
        return None
    journal.note(f"snapshot written to {SNAPSHOT_PATH}")
    return dump


def step_send_unchanged(
    bridge: SqBridge, journal: Journal, dump: bytes
) -> Optional[SendResult]:
    """Step 2: send the snapshot back, byte for byte."""
    journal.note("")
    journal.note("=== step 2: send the snapshot back, unchanged ===")
    result = write_preset(bridge, dump)
    if result.code != m.ErrorCode.ACK:
        journal.note(
            f"alert answered with {result.code} after {result.seconds:.3f} s "
            f"-- {result.note}. Nothing was sent; stopping."
        )
        return None
    journal.note(
        f"alert answered with ACK after {result.seconds:.3f} s; "
        f"the {len(dump)}-byte dump was sent"
    )
    return result


def step_read_back(
    bridge: SqBridge, journal: Journal, expected: bytes, title: str
) -> Optional[bytes]:
    """Read the current preset back and compare it with ``expected``.

    Returns the bytes the unit read back, or ``None`` if the read
    failed.
    """
    journal.note("")
    journal.note(f"=== {title} ===")
    time.sleep(SETTLE)
    after = read_dump(bridge).dump
    offsets = differences(expected, after)
    journal.note(f"re-read: {len(after)} bytes, {describe_difference(offsets)}")
    return after


def step_mutate(
    bridge: SqBridge, journal: Journal
) -> Optional[Tuple[bytes, int, int, SendResult]]:
    """Step 4: change track 1's volume by one, send it, and read back.

    The dump that is changed is read again first, not taken from the
    snapshot: what the unit holds now is what the change is applied to,
    and that is the thing the read-back is compared against.

    Returns the changed dump, the old value, the new value and what the
    write did, or ``None`` if the write failed.
    """
    journal.note("")
    journal.note("=== step 4: change track 1's volume by one, send, read back ===")
    time.sleep(SETTLE)
    current = read_dump(bridge).dump
    changed = bytearray(current)
    old = changed[TRACK1_VOLUME_OFFSET]
    new = old - 1 if old > 0 else old + 1
    if not 0 <= new <= 127:
        journal.note(f"track 1's volume is {old}, which cannot move by one")
        return None
    changed[TRACK1_VOLUME_OFFSET] = new
    journal.note(
        f"track 1 volume: {old} -> {new} at dump offset {TRACK1_VOLUME_OFFSET}"
    )
    result = write_preset(bridge, bytes(changed))
    if result.code != m.ErrorCode.ACK:
        journal.note(
            f"alert answered with {result.code} after {result.seconds:.3f} s "
            f"-- {result.note}. Nothing was sent."
        )
        return None
    journal.note(
        f"alert answered with ACK after {result.seconds:.3f} s; "
        f"the changed dump was sent"
    )
    return bytes(changed), old, new, result


def step_restore(
    bridge: SqBridge, journal: Journal, snapshot: bytes
) -> Optional[SendResult]:
    """Step 5: send the original snapshot back, so the unit is as found."""
    journal.note("")
    journal.note("=== step 5: restore the original snapshot ===")
    time.sleep(SETTLE)
    result = write_preset(bridge, snapshot)
    if result.code != m.ErrorCode.ACK:
        journal.note(
            f"restore alert answered with {result.code} after "
            f"{result.seconds:.3f} s -- {result.note}. Nothing was sent."
        )
        return None
    journal.note(
        f"restore alert answered with ACK after {result.seconds:.3f} s; "
        f"the original {len(snapshot)} bytes were sent"
    )
    return result


def summarise(
    journal: Journal, findings: Findings, changed_value: Optional[int]
) -> None:
    """The findings, in the form the document quotes."""
    journal.note("")
    journal.note("=== summary ===")
    if findings.ack_unchanged is not None:
        journal.note(
            f"ACK after an unchanged send: yes, after {findings.ack_unchanged:.3f} s"
        )
    journal.note(
        f"re-read after an unchanged send: "
        f"{describe_difference(findings.unchanged_offsets)}"
    )
    if findings.ack_changed is not None:
        journal.note(
            f"ACK after a changed send: yes, after {findings.ack_changed:.3f} s"
        )
    if findings.change_visible is not None:
        journal.note(
            f"the changed byte in the re-read: "
            f"{'visible' if findings.change_visible else 'not visible'}"
            + (f" (sent {changed_value})" if changed_value is not None else "")
        )
    if findings.ack_restore is not None:
        journal.note(f"ACK after the restore: yes, after {findings.ack_restore:.3f} s")
    journal.note(
        f"re-read after the restore: {describe_difference(findings.restore_offsets)}"
    )


def run(bridge: SqBridge, journal: Journal) -> int:
    """The five steps, in the order they have to happen in.

    Returns 0 when every step ran, 1 when a step could not.
    """
    findings = Findings()
    snapshot = step_snapshot(bridge, journal)
    if snapshot is None:
        return 1

    unchanged = step_send_unchanged(bridge, journal, snapshot)
    if unchanged is None:
        return 1
    findings.ack_unchanged = unchanged.seconds

    after_unchanged = step_read_back(
        bridge, journal, snapshot, "step 3: read it back (command 03)"
    )
    if after_unchanged is None:
        return 1
    findings.unchanged_offsets = differences(snapshot, after_unchanged)
    if findings.unchanged_offsets:
        journal.note("the re-read is NOT the snapshot as it was -- it changed")
    else:
        journal.note(
            "the re-read is byte-identical to the snapshot; an unchanged "
            "send cannot tell the stored preset from the Edit Preset"
        )

    mutated = step_mutate(bridge, journal)
    if mutated is None:
        step_restore(bridge, journal, snapshot)
        return 1
    changed_dump, _old, new, changed_send = mutated
    findings.ack_changed = changed_send.seconds

    after_changed = step_read_back(
        bridge, journal, changed_dump, "step 4 (continued): read it back"
    )
    if after_changed is None:
        step_restore(bridge, journal, snapshot)
        return 1
    findings.change_visible = after_changed[TRACK1_VOLUME_OFFSET] == new
    journal.note(
        f"the changed byte reads back as {after_changed[TRACK1_VOLUME_OFFSET]} "
        f"(sent {new}): "
        + (
            "the change IS visible in the re-read"
            if findings.change_visible
            else "the change is NOT visible"
        )
    )

    restore = step_restore(bridge, journal, snapshot)
    if restore is None:
        return 1
    findings.ack_restore = restore.seconds

    after_restore = step_read_back(
        bridge, journal, snapshot, "step 5 (continued): read it back"
    )
    if after_restore is None:
        return 1
    findings.restore_offsets = differences(snapshot, after_restore)
    if findings.restore_offsets:
        journal.note("the unit did NOT come back to the original bytes")
    else:
        journal.note("the unit is back to the original bytes")

    summarise(journal, findings, new)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The family's options, then this tool's own."""
    parser = make_parser(
        "probe_preset_write",
        "Find out what the unit's Edit Preset buffer holds after a preset "
        "dump is sent to it. Bench tool; opens a real MIDI port.",
        epilog=(
            "examples:\n"
            "  python3 tools/probe_preset_write.py                  # dry run\n"
            "  python3 tools/probe_preset_write.py --yes\n"
            '  python3 tools/probe_preset_write.py --yes --port "X:1"'
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
        yes=True,
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Open the unit, run the steps, write the log. Never raises."""
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_common(args)
    config_path = args.config or cfg.DEFAULT_PATH
    channel = channel_of(args, default=cfg.load_channel(config_path)) or 0
    port = args.port or (
        f"the port remembered in {config_path} (sweeping only if it does not answer)"
    )

    banner(port, channel)
    if not args.yes:
        print("")
        print("dry run: nothing was opened and nothing was sent.")
        print("Re-run with --yes to drive the unit.")
        return 0

    journal = Journal(LOG_PATH)
    bridge: Optional[SqBridge] = None
    try:
        bridge = open_bridge(args, channel, config_path)
        if bridge is None:
            journal.note("error: the unit did not open; nothing was sent.")
            return 1
        journal.note(f"port: {bridge.description}, base channel {bridge.channel + 1}")
        journal.note("")
        return run(bridge, journal)
    except Exception:
        journal.note("error: the probe failed")
        journal.note(traceback.format_exc().rstrip())
        return 1
    finally:
        if bridge is not None:
            bridge.close()


if __name__ == "__main__":
    raise SystemExit(main())
