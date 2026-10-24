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

"""Does the SQ-R take an inbound **sound** dump?

The editor can write presets -- ``SqBridge.send_preset`` has been measured
on the unit -- but the sound path is new, and this tool is what measures
it. The question is narrow: **when a sound dump is sent to the unit, does
the internal bank afterwards hold what was sent?**

The order is the point, and it is read-only first, then one minimal write
that is put back:

1. **snapshot** -- read all eighty INT sounds and keep a copy;
2. **send slot 00 unchanged** -- select INT slot 1 and send slot 00's own
   record back, then read the bank and see whether anything moved;
3. **send slot 00 with one byte changed** -- the same, with a single byte
   of the name flipped, so that "accepted" and "ignored" can be told
   apart; then restore slot 00's original record and read back;
4. **whole-bank send** -- the type-03 All Sounds Dump of the snapshot to
   INT, then read the bank and report whether it is unchanged.

**What it writes, and what it does not.** Every write goes to the **INT
bank (RAM)**. Card A and Card B are not dumpable -- the unit will not
hand a card back as a bank -- so there is no cheap read-back for a card
and **this probe sends nothing to one**: every number below is about RAM.
No front-panel button is pressed and no virtual-button command is sent;
the only control messages are program changes, which select the
destination the way the measured preset rule says to ("select the
destination, then send the dump"). The last thing the tool does is put
the snapshot back, so the run leaves the bank where it found it.

An unchanged read-back is honest evidence of nothing on its own: the
payload is the bank's own bytes, so an ignored dump and an accepted one
read back the same. The changed byte in step 3 is what decides.

Usage::

    python3 tools/probe_sound_write.py                   # dry run
    python3 tools/probe_sound_write.py --yes
    python3 tools/probe_sound_write.py --yes --port "X:1"

The run log is written to ``/tmp/opencode/sqrex/probe_sound_write.log``
and the snapshot to ``/tmp/opencode/sqrex/probe_sound_snapshot_before.bin``.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sq import banks
from sq import config as cfg
from sq import messages as m
from sq.bridge import SqBridge
from vinsynlib.cli import (
    add_common_arguments,
    channel_of,
    make_parser,
    validate_common,
)

#: Where the run's artefacts go. Both are run artefacts rather than
#: fixtures: the committed fixture in ``tests/fixtures`` is the one the
#: test suite reads, and these two are what a bench run leaves behind.
OUT_DIR = Path("/tmp/opencode/sqrex")  # noqa: S108 - a fixed, documented path
SNAPSHOT_PATH = OUT_DIR / "probe_sound_snapshot_before.bin"
LOG_PATH = OUT_DIR / "probe_sound_write.log"

#: How long to let the unit settle after a write before asking it what it
#: holds. The preset probe waited half a second for the redraw a dump
#: causes, and this is that half second. The bank read that follows is
#: ten seconds of wire on its own, so the settle is not the waiting.
SETTLE = 0.5

#: Said before every bank read: thirty-two kilobytes at MIDI's 3125 bytes
#: a second is ten and a half seconds, and a bench that prints nothing
#: for eleven seconds looks hung.
READ_NOTE = "reading the internal bank (about 11 s)..."

#: Where a sound's name sits, and how big it is. The one byte this probe
#: changes is the first byte of the name: it is inside the record, it is
#: the byte an owner would notice, and whether it comes back is the whole
#: question of whether a sound dump lands.
NAME_OFFSET = m.NAME_OFFSET


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
class State:
    """What the probe knows about the unit's bank as it goes.

    ``last_seen`` is the most recent read of the internal bank, and
    ``pending`` says whether a write has gone out since that read. The
    pair is what lets the restore decide whether the last read still
    describes the unit: a write with no read after it means the bank has
    to be read again before anything can be said about it.

    ``touched`` is which slots have been written since that read, so
    that a restore whose read fails still knows what to send back. The
    bytes it sends are the snapshot's own -- which came from the unit --
    so sending them again cannot lose anything the unit had.
    """

    #: The bank as last read, or ``None`` if it has never been read.
    last_seen: Optional[Dict[int, bytes]] = None
    #: Whether a write has gone out since :attr:`last_seen`.
    pending: bool = False
    #: The slots written since :attr:`last_seen`, ascending.
    touched: List[int] = field(default_factory=list)

    def wrote(self, slots: List[int]) -> None:
        """Record that ``slots`` are about to be written.

        Called **before** the send rather than after: a send that fails
        half way through has still written something, and the restore
        should err towards re-sending bytes that came from the unit.
        """
        self.pending = True
        for slot in slots:
            if slot not in self.touched:
                self.touched.append(slot)
        self.touched.sort()

    def read(self, bank: Dict[int, bytes]) -> None:
        """File ``bank`` as the unit's current state."""
        self.last_seen = bank
        self.pending = False
        self.touched = []


@dataclass
class Findings:
    """What the probe learned, for the summary."""

    #: Slots that differed from the snapshot after the unchanged send.
    #: ``None`` means the step did not get far enough to say.
    unchanged_slots: Optional[List[int]] = None
    #: Whether the one changed byte came back changed.
    change_visible: Optional[bool] = None
    #: Where slot 00 differed from the snapshot after the changed send.
    change_offsets: List[int] = field(default_factory=list)
    #: Slots that differed from the snapshot after slot 00 was restored.
    restore_slots: Optional[List[int]] = None
    #: Slots that differed from the snapshot after the whole-bank send.
    whole_slots: Optional[List[int]] = None


def name_of(record: bytes) -> str:
    """The sound's name, or ``"?"`` if the record cannot give one."""
    try:
        return m.sound_name(record)
    except ValueError:
        # ProtocolError is a ValueError; a short or malformed record is
        # not a reason to lose the run over a display string.
        return "?"


def diff_slots(expected: Dict[int, bytes], actual: Dict[int, bytes]) -> List[int]:
    """The slots whose record is not the expected one, ascending.

    A slot the read-back does not hold at all counts as differing: a
    missing slot is not a match, and skipping it would report a partial
    read as an unchanged bank.
    """
    return [slot for slot in sorted(expected) if actual.get(slot) != expected[slot]]


def describe_slots(slots: List[int]) -> str:
    """The slots, in words rather than as a bare list of numbers."""
    if not slots:
        return "none"
    shown = ", ".join(f"{slot:02d}" for slot in slots[:16])
    if len(slots) > 16:
        shown += f", ... and {len(slots) - 16} more"
    return f"{len(slots)} ({shown})"


def byte_offsets(left: bytes, right: bytes) -> List[int]:
    """The offsets at which two records differ."""
    if len(left) != len(right):
        return [-1]
    return [index for index, (a, b) in enumerate(zip(left, right)) if a != b]


def describe_offsets(offsets: List[int]) -> str:
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
    group = banks.group("INT")
    print("PROBE SOUND WRITE -- A BENCH TOOL")
    print("=" * 60)
    print("This tool sends sound dumps TO the unit and reads the internal")
    print("bank back to see whether they landed.")
    print("")
    print("It checks the INT bank only. Card A and Card B are not dumpable,")
    print("so a card has no cheap read-back and nothing here is sent to one:")
    print("every result below is about RAM.")
    print("")
    print("It presses no front-panel button and sends no virtual-button")
    print("command. The only control messages are program changes, which")
    print("select the destination before the dump -- the rule the preset")
    print("write was measured by. The last thing it does is put the")
    print("snapshot back, so the run leaves the bank where it found it.")
    print("")
    print(f"  port        : {port}")
    print(f"  channel     : {channel + 1} (MIDI channel)")
    print(
        f"  bank        : {group.id} ({group.label}), "
        f"{banks.SOUNDS_PER_GROUP} sounds of {m.SOUND_BYTES} bytes"
    )
    print("  writes      : up to 4 -- slot 00 unchanged, slot 00 with one")
    print("                byte changed, slot 00 restored, the whole bank")
    print(f"  snapshot    : {SNAPSHOT_PATH}")
    print(f"  log         : {LOG_PATH}")
    print("")
    print("Leave the front panel alone while it runs. Selecting a sound")
    print("changes what the unit is on, so its display will move.")


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


def read_back(
    bridge: SqBridge, journal: Journal, title: str, state: State
) -> Optional[Dict[int, bytes]]:
    """Read the internal bank back and file it as the unit's current state.

    Returns the bank, or ``None`` if it could not be read. Either way the
    probe carries on to the next step it can run, because a run that
    stops at the first timeout says nothing about the steps behind it.
    """
    journal.note("")
    journal.note(f"=== {title} ===")
    time.sleep(SETTLE)
    journal.note(READ_NOTE)
    try:
        bank = bridge.read_internal_bank()
    except Exception as exc:
        journal.note(f"the read failed: {exc}")
        return None
    state.read(bank)
    return bank


def step_snapshot(
    bridge: SqBridge, journal: Journal, state: State
) -> Optional[Dict[int, bytes]]:
    """Step 1: read all eighty INT sounds and keep a copy of them.

    Nothing is sent but the request, and nothing is written: this is the
    only copy of what the unit held before the tool touched it, so it has
    to come first.
    """
    bank = read_back(
        bridge,
        journal,
        "step 1: snapshot the internal bank (read-only)",
        state,
    )
    if bank is None:
        return None
    journal.note(f"{len(bank)} sound(s) read")
    short = [
        slot for slot, record in sorted(bank.items()) if len(record) != m.SOUND_BYTES
    ]
    if short:
        journal.note(
            f"UNEXPECTED: {describe_slots(short)} do not hold {m.SOUND_BYTES} bytes"
        )
    expected = list(range(banks.SOUNDS_PER_GROUP))
    if sorted(bank) != expected:
        journal.note(
            f"UNEXPECTED: the bank holds slots {describe_slots(sorted(bank))}, "
            f"not 00-{banks.SOUNDS_PER_GROUP - 1:02d}"
        )
    journal.note(f"slot 00 name: {name_of(bank[0])!r}" if 0 in bank else "")
    journal.note(f"slot 01 name: {name_of(bank[1])!r}" if 1 in bank else "")
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_bytes(
            b"".join(bank[slot] for slot in expected if slot in bank)
        )
    except OSError as exc:
        journal.note(f"could not write the snapshot: {exc}")
        return bank
    journal.note(f"snapshot written to {SNAPSHOT_PATH}")
    return bank


def step_send_unchanged(
    bridge: SqBridge,
    journal: Journal,
    snapshot: Dict[int, bytes],
    findings: Findings,
    state: State,
) -> Optional[Dict[int, bytes]]:
    """Step 2: send slot 00's own record back, byte for byte.

    The destination is INT slot 1, which is what slot 00 is: the dump
    goes to the selected slot, so the selection is part of the write.
    Both come from ``SqBridge.send_sound_slots`` -- the path the import
    feature itself takes -- rather than from a hand-built frame, because
    what is being measured is that path.
    """
    journal.note("")
    journal.note("=== step 2: send slot 00's own record back, unchanged ===")
    journal.note(
        "selecting INT slot 1 and sending a Single Sound Dump of the "
        "snapshot's slot 00 (SqBridge.send_sound_slots)"
    )
    state.wrote([0])
    try:
        bridge.send_sound_slots("INT", {0: snapshot[0]}, verify=False)
    except Exception as exc:
        journal.note(f"the send failed: {exc}")
        return None
    journal.note(f"sent: slot 00, {m.SOUND_BYTES} bytes, unchanged")

    bank = read_back(bridge, journal, "step 2 (continued): read the bank back", state)
    if bank is None:
        return None
    changed = diff_slots(snapshot, bank)
    findings.unchanged_slots = changed
    journal.note(f"slots differing from the snapshot: {describe_slots(changed)}")
    if 0 in bank:
        journal.note(
            f"slot 00 against the snapshot: "
            f"{describe_offsets(byte_offsets(snapshot[0], bank[0]))}"
        )
    if not changed:
        journal.note(
            "byte-identical. An unchanged send cannot tell an accepted "
            "dump from an ignored one -- the payload is the bank's own "
            "bytes -- so step 3's changed byte is what decides."
        )
    return bank


def step_mutate(
    bridge: SqBridge,
    journal: Journal,
    snapshot: Dict[int, bytes],
    findings: Findings,
    state: State,
) -> Optional[Dict[int, bytes]]:
    """Step 3: change one byte of slot 00's name, send it, read back.

    One byte, one bit of it, at the first byte of the name: a change the
    read-back either carries or does not. What the unit does with a name
    on the way in is not assumed -- the preset path keeps its own name
    bytes -- which is exactly why the byte is read back rather than
    trusted.
    """
    journal.note("")
    journal.note("=== step 3: send slot 00 with exactly one byte changed ===")
    original = snapshot[0]
    changed = bytearray(original)
    offset = NAME_OFFSET
    before = changed[offset]
    after_value = before ^ 0x01
    changed[offset] = after_value
    journal.note(
        f"byte {offset} (the first byte of the name): "
        f"{before:#04x} -> {after_value:#04x}"
    )
    journal.note(
        f"name as sent: {name_of(bytes(changed))!r} (snapshot: {name_of(original)!r})"
    )
    state.wrote([0])
    try:
        bridge.send_sound_slots("INT", {0: bytes(changed)}, verify=False)
    except Exception as exc:
        journal.note(f"the send failed: {exc}")
        return None
    journal.note(f"sent: slot 00, {m.SOUND_BYTES} bytes, one byte changed")

    bank = read_back(bridge, journal, "step 3 (continued): read the bank back", state)
    if bank is None:
        return None
    findings.change_offsets = byte_offsets(original, bank.get(0, b""))
    if 0 not in bank:
        findings.change_visible = False
        journal.note("slot 00 is missing from the read-back")
        return bank
    held = bank[0][offset] if offset < len(bank[0]) else None
    findings.change_visible = held == after_value
    journal.note(
        f"byte {offset} in the re-read: "
        + (
            f"{held:#04x} (sent {after_value:#04x})"
            if isinstance(held, int)
            else "absent"
        )
    )
    journal.note(
        f"slot 00 against the snapshot: {describe_offsets(findings.change_offsets)}"
    )
    journal.note(
        "the change IS visible in the re-read"
        if findings.change_visible
        else "the change is NOT visible in the re-read"
    )
    return bank


def step_restore_slot(
    bridge: SqBridge,
    journal: Journal,
    snapshot: Dict[int, bytes],
    findings: Findings,
    state: State,
) -> Optional[Dict[int, bytes]]:
    """Step 3 (restored): send slot 00's original record and read back."""
    journal.note("")
    journal.note("=== step 3 (restored): send slot 00's original record ===")
    state.wrote([0])
    try:
        bridge.send_sound_slots("INT", {0: snapshot[0]}, verify=False)
    except Exception as exc:
        journal.note(f"the restore send failed: {exc}")
        return None
    journal.note(f"sent: slot 00, the original {m.SOUND_BYTES} bytes")

    bank = read_back(bridge, journal, "step 3 (restored): read the bank back", state)
    if bank is None:
        return None
    changed = diff_slots(snapshot, bank)
    findings.restore_slots = changed
    journal.note(f"slots differing from the snapshot: {describe_slots(changed)}")
    if 0 in bank:
        journal.note(
            f"slot 00 against the snapshot: "
            f"{describe_offsets(byte_offsets(snapshot[0], bank[0]))}"
        )
    journal.note(
        "slot 00 is back to the snapshot"
        if 0 not in changed
        else "slot 00 is NOT back to the snapshot"
    )
    return bank


def step_whole_bank(
    bridge: SqBridge,
    journal: Journal,
    snapshot: Dict[int, bytes],
    findings: Findings,
    state: State,
) -> Optional[Dict[int, bytes]]:
    """Step 4: the type-03 All Sounds Dump of the whole snapshot to INT.

    The payload is the bank's own bytes, so an unchanged read-back here
    means the message was taken without disturbing anything -- it cannot
    mean the write landed, because landing and ignoring look alike when
    the data already matches. What this step does show is whether the
    large message is accepted at all: refused, dropped or garbled would
    all show up in the read-back or in the send itself.
    """
    journal.note("")
    journal.note("=== step 4: whole-bank type-03 send of the snapshot to INT ===")
    journal.note(
        "SqBridge.send_sound_bank: the INT selector program change, then "
        f"one All Sounds Dump of {banks.SOUNDS_PER_GROUP} sounds"
    )
    state.wrote(list(range(banks.SOUNDS_PER_GROUP)))
    try:
        result = bridge.send_sound_bank("INT", snapshot, verify=False)
    except Exception as exc:
        journal.note(f"the send failed: {exc}")
        return None
    journal.note(
        f"sent: group {result.group}, "
        f"{len(result.slots_written)} slot(s) addressed "
        "(the bridge does not say whether the large message went out or "
        "fell back to per-slot sends; both write all eighty)"
    )

    bank = read_back(bridge, journal, "step 4 (continued): read the bank back", state)
    if bank is None:
        return None
    changed = diff_slots(snapshot, bank)
    findings.whole_slots = changed
    journal.note(f"slots differing from the snapshot: {describe_slots(changed)}")
    if not changed:
        journal.note(
            "the bank is unchanged: the type-03 message was taken without "
            "an error and without disturbing a single slot. Whether it "
            "wrote anything cannot be shown by this payload, which is "
            "the bank's own bytes -- see the summary."
        )
    else:
        journal.note(
            "the bank is NOT what was sent: "
            f"{describe_slots(changed)} differ from the snapshot"
        )
    return bank


def send_back(
    bridge: SqBridge,
    journal: Journal,
    snapshot: Dict[int, bytes],
    slots: List[int],
    state: State,
) -> None:
    """Send the snapshot's own bytes for ``slots``, then read back to confirm.

    The bytes came from the unit in the first place, so putting them
    back cannot lose anything it held. The read-back is what says
    whether the unit came back; when that read fails too, the send is
    reported as sent-but-unverified rather than as restored, because
    "sent" and "restored" are different claims.
    """
    state.wrote(slots)
    try:
        bridge.send_sound_slots(
            "INT", {slot: snapshot[slot] for slot in slots}, verify=False
        )
    except Exception as exc:
        journal.note(f"the restore send failed: {exc}")
        journal.note("the unit was NOT restored.")
        return
    journal.note(f"sent: {describe_slots(slots)}, the snapshot's own bytes")
    journal.note(READ_NOTE)
    try:
        after = bridge.read_internal_bank()
    except Exception as exc:
        journal.note(f"the read-back failed: {exc}")
        journal.note("the restore was sent but could NOT be verified.")
        return
    state.read(after)
    left = diff_slots(snapshot, after)
    if left:
        journal.note(f"the unit still differs at {describe_slots(left)}; NOT restored")
    else:
        journal.note("the unit is back to the snapshot")


def restore(
    bridge: SqBridge,
    journal: Journal,
    snapshot: Dict[int, bytes],
    state: State,
) -> None:
    """Leave the unit as it was found. Best effort, and never raises.

    The snapshot is the copy of what the unit held before this run, so
    putting it back is what makes the run repeatable. Only the slots that
    actually differ are sent -- a run that changed nothing writes
    nothing -- and the read-back after the restore is what says whether
    the unit came back.

    The one case this does not give up on is a bank that cannot be read:
    the slots this run wrote are then sent back blind, with the
    snapshot's own bytes, because those bytes came from the unit and
    sending them again cannot make anything worse.
    """
    journal.note("")
    journal.note("=== restore: leave the unit as it was found ===")
    current = state.last_seen
    if current is None or state.pending:
        journal.note(READ_NOTE)
        try:
            current = bridge.read_internal_bank()
        except Exception as exc:
            journal.note(f"the bank could not be read back: {exc}")
            if state.touched:
                journal.note(
                    f"this run wrote {describe_slots(state.touched)}; sending "
                    "the snapshot's own bytes for them blind, because those "
                    "bytes came from the unit and sending them again loses "
                    "nothing."
                )
                send_back(bridge, journal, snapshot, state.touched, state)
            else:
                journal.note(
                    "nothing this run wrote is waiting to be put back, so "
                    "there is nothing to send."
                )
            return
        state.read(current)
    changed = diff_slots(snapshot, current)
    if not changed:
        journal.note("the bank matches the snapshot; nothing to send")
        return
    journal.note(f"slots differing from the snapshot: {describe_slots(changed)}")
    send_back(bridge, journal, snapshot, changed, state)


def summarise(journal: Journal, findings: Findings) -> None:
    """The findings, and the limits of what they show."""
    journal.note("")
    journal.note("=== summary ===")
    if findings.unchanged_slots is None:
        journal.note("the unchanged send was not completed")
    else:
        journal.note(
            "slots differing from the snapshot after an unchanged send of "
            f"slot 00: {describe_slots(findings.unchanged_slots)}"
        )
    if findings.change_visible is None:
        journal.note("the changed send was not completed")
    else:
        journal.note(
            "the changed byte in the re-read: "
            + ("visible" if findings.change_visible else "NOT visible")
            + f" ({describe_offsets(findings.change_offsets)})"
        )
    if findings.restore_slots is None:
        journal.note("the slot 00 restore was not completed")
    else:
        journal.note(
            "slots differing from the snapshot after the slot 00 restore: "
            f"{describe_slots(findings.restore_slots)}"
        )
    if findings.whole_slots is None:
        journal.note("the whole-bank send was not completed")
    else:
        journal.note(
            "slots differing from the snapshot after the whole-bank "
            f"type-03 send: {describe_slots(findings.whole_slots)}"
        )

    journal.note("")
    journal.note("What that means, and what it does not:")
    if findings.change_visible is True:
        journal.note(
            "* the changed byte came back changed, so the unit takes an "
            "inbound Single Sound Dump and the read-back verifies it."
        )
    elif findings.change_visible is False:
        journal.note(
            "* the changed byte did NOT come back changed, so nothing here "
            "shows the unit took the dump; an unchanged read-back would "
            "not change that."
        )
    journal.note(
        "* an unchanged read-back cannot tell an accepted dump from an "
        "ignored one, because the payload is the bank's own bytes. The "
        "changed byte is the only thing that separates the two."
    )
    journal.note(
        "* only INT was checked. Card A and Card B are not dumpable, so a "
        "card has no cheap read-back and this probe sends nothing to one."
    )
    journal.note(
        "* no front-panel button was pressed and no virtual-button "
        "command was sent; the control messages were program changes "
        "only, which is what selects the destination."
    )


def run(bridge: SqBridge, journal: Journal) -> int:
    """The four steps, in the order they have to happen in.

    Returns 0 when every step ran, 1 when a step could not. Whatever
    happens -- a refusal, a silence, a crash in here -- the snapshot goes
    back to the unit before the bridge is closed.
    """
    findings = Findings()
    state = State()
    snapshot = step_snapshot(bridge, journal, state)
    if snapshot is None:
        return 1
    status = 1
    try:
        status = steps(bridge, journal, snapshot, findings, state)
    finally:
        restore(bridge, journal, snapshot, state)
    summarise(journal, findings)
    return status


def steps(
    bridge: SqBridge,
    journal: Journal,
    snapshot: Dict[int, bytes],
    findings: Findings,
    state: State,
) -> int:
    """Steps 2 to 4. Returns 0 when all of them ran, 1 when one could not."""
    if step_send_unchanged(bridge, journal, snapshot, findings, state) is None:
        return 1
    if step_mutate(bridge, journal, snapshot, findings, state) is None:
        return 1
    if step_restore_slot(bridge, journal, snapshot, findings, state) is None:
        return 1
    if step_whole_bank(bridge, journal, snapshot, findings, state) is None:
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The family's options, then this tool's own."""
    parser = make_parser(
        "probe_sound_write",
        "Find out whether the SQ-R accepts an inbound sound dump: send "
        "one to INT and read the bank back. Bench tool; opens a real "
        "MIDI port.",
        epilog=(
            "examples:\n"
            "  python3 tools/probe_sound_write.py                  # dry run\n"
            "  python3 tools/probe_sound_write.py --yes\n"
            '  python3 tools/probe_sound_write.py --yes --port "X:1"'
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
