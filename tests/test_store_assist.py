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

"""The store assist on the MULTI screen.

**The measured model is the test's model.**  A send stores into the slot
the unit is selected to -- there is no addressing -- so the tests never
pretend the backup's slot number is the destination.  The bytes that go
out are the backup's own 168, address bytes included, and the unit's
read-back is what decides whether the store landed.

**No hardware, and no name assertions.**  The unit on the other end of
the wire is the fake from ``tests/test_preset_send.py``'s pattern: it
answers the alert with an acknowledgement and the read request with an
alert and a dump, and it keeps what it was sent.  A preset's name is
the unit's to write; nothing here pins one.

The backup is the investigation's shape: stamped directories under a
root, the newest read, and the preset table's mapping -- slot 0 is
array 10, slots 70 through 79 are arrays 0 through 9 -- used to find
the bytes.  A test that indexed ``presets_raw`` with a slot number
would be testing the bug this feature exists to avoid.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, List, Optional

import pytest
from textual.widgets import Input, Static

from ensqsqed.app import EnsqsqedApp
from ensqsqed.multi import MultiScreen, NumberPrompt
from sq import bridge as b
from sq import catalog as cat
from sq import messages as m
from vinsynlib.ui.confirm import ConfirmScreen

FIXTURE = Path(__file__).parent / "fixtures" / "single_seq_default.bin"

#: The captured dump: 48 bytes of sequence data and the 168-byte preset.
DUMP = FIXTURE.read_bytes()
BLOB = DUMP[:48]
PRESET = DUMP[48:]

#: A backed-up preset that is not the current one: byte 34 (track one's
#: volume) says 99 rather than the fixture's 127, and the first two
#: bytes are slot 70's address word.  A send must carry both.
_backup = bytearray(PRESET)
_backup[0:2] = b"\x46\x0b"
_backup[34] = 99
BACKUP_RAW = bytes(_backup)

#: The number of presets the all-dump holds.
PRESET_COUNT = 80


def _dump_bytes(slot_zero: bytes) -> bytes:
    """An all-presets dump whose slot 0 is ``slot_zero``.

    The preset table is the unit's shape: slot ``i``'s word is the
    offset of its preset from the start of the table.  The array index
    is ``(slot + 10) % 80``, the bench dump's own cyclic shift, so slot
    0 lives at array 10 and slots 70-79 at arrays 0-9.  Every other
    preset is the fixture's.
    """
    presets = [bytearray(PRESET) for _ in range(PRESET_COUNT)]
    presets[10] = bytearray(slot_zero)
    table = bytearray(208)
    for slot in range(PRESET_COUNT):
        index = (slot + 10) % PRESET_COUNT
        word = (208 + index * len(PRESET)).to_bytes(2, "big")
        table[8 + slot * 2 : 10 + slot * 2] = word
    return (
        b"\x00" * 408
        + bytes(table)
        + b"".join(bytes(preset) for preset in presets)
        + b"\x00" * 21
    )


def _write_backup(root: Path, stamp: str, slot_zero: bytes) -> None:
    """One backup run's directory, with its all-presets dump."""
    directory = root / stamp
    directory.mkdir(parents=True)
    (directory / "all_presets_dump.bin").write_bytes(_dump_bytes(slot_zero))


class _FakeIn:
    """A MIDI input that hands out the messages it was given."""

    def __init__(self) -> None:
        self.inbox: List[bytes] = []

    def ignore_types(self, **kwargs) -> None:
        return None

    def get_message(self):
        if not self.inbox:
            return None
        return (self.inbox.pop(0), 0)


class _FakeUnit:
    """A unit that answers the preset exchange and echoes what it sent.

    It keeps the last dump it received as its current preset, so a
    verified send reads back what went out.  ``verify_offsets`` is the
    flakiness in miniature: each value is one byte returned changed on
    the next read after a send, and an empty list means the read-back
    matches.  The bench measured the same bytes accepted on one try and
    refused on the next (2026-10-22).
    """

    def __init__(
        self,
        *,
        verify_offsets: Optional[List[int]] = None,
        start_slot: int = 0,
        slot: Optional[int] = None,
        current_slot: Optional[int] = None,
        known_slot: Optional[int] = None,
    ) -> None:
        if slot is not None:
            start_slot = slot
        if current_slot is not None:
            start_slot = current_slot
        if known_slot is not None:
            start_slot = known_slot
        self.slot = start_slot % 80
        self.current = DUMP
        self.received: List[bytes] = []
        self.reads = 0
        self.after_send = False
        self.verify_offsets = list(verify_offsets or [])
        self.button_presses = 0
        self.button_messages: List[bytes] = []

    def answer(self, message: bytes, inbox: List[bytes]) -> None:  # noqa: C901
        packet = m.parse(message)
        if packet is None:
            return
        if packet.function == m.Function.COMMAND:
            body = m.from_nibbles(packet.payload)
            if body and body[0] == m.CommandType.VIRTUAL_BUTTON:
                self.button_messages.append(bytes(message))
                button = body[1] if len(body) > 1 else 0
                if button == 5:
                    self.button_presses += 1
                    self.slot = (self.slot - 1) % 80
                return
        if packet.function == m.Function.SINGLE_SEQUENCE_DUMP:
            body = m.from_nibbles(packet.payload)
            self.received.append(body)
            self.current = body
            self.after_send = True
            return
        if packet.function != m.Function.COMMAND:
            return
        body = m.from_nibbles(packet.payload)
        if not body:
            return
        command = body[0]
        if command == m.CommandType.SINGLE_SEQUENCE_ALERT:
            inbox.append(
                m.frame(
                    m.Function.ERROR,
                    m.to_nibbles([m.ErrorCode.ACK]),
                    device=0,
                )
            )
            return
        if command != m.CommandType.SINGLE_SEQUENCE_REQUEST:
            return
        self.reads += 1
        if self.after_send:
            self.after_send = False
            data = self.current
            offset = self.verify_offsets.pop(0) if self.verify_offsets else None
            if offset is not None:
                changed = bytearray(data)
                changed[offset] = (changed[offset] + 1) % 256
                data = bytes(changed)
        else:
            preset = bytearray(PRESET)
            preset[0] = self.slot
            data = bytes(BLOB + bytes(preset))
        inbox.append(
            m.frame(
                m.Function.COMMAND,
                b.PresetAlert(
                    command=m.CommandType.SINGLE_SEQUENCE_ALERT,
                    size=len(data) - len(PRESET),
                    flag=0,
                ).wire_payload(),
                device=0,
            )
        )
        inbox.append(
            m.frame(
                m.Function.SINGLE_SEQUENCE_DUMP,
                m.to_nibbles(data),
                device=0,
            )
        )


class _FakeOut:
    """A MIDI output that answers through the fake unit."""

    def __init__(self, port: _FakeIn, unit: _FakeUnit) -> None:
        self.port = port
        self.unit = unit

    def send_message(self, data) -> None:
        self.unit.answer(bytes(data), self.port.inbox)


@pytest.fixture(autouse=True)
def _no_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bench settle is half a second; the tests do not wait."""
    monkeypatch.setattr(b, "PRESET_SETTLE", 0.0)


def _device(unit: _FakeUnit) -> b.SqBridge:
    """The real bridge, over the fake unit and its fake ports."""
    port = _FakeIn()
    return b.SqBridge(port, _FakeOut(port, unit), channel=0, timeout=0.05)


def _app(unit: _FakeUnit, backup_root: Path) -> EnsqsqedApp:
    return EnsqsqedApp(
        _device(unit),
        catalog=cat.empty(),
        backup_root=str(backup_root),
    )


async def _wait(predicate: Callable[[], bool], pilot, *, tries: int = 200) -> bool:
    """Wait for something that happens on a worker."""
    for _ in range(tries):
        if predicate():
            return True
        await pilot.pause()
    return False


def _status(screen) -> str:
    """What the editor's own status line says."""
    return str(screen.query_one(".status", Static).content)


async def _open_multi(app: EnsqsqedApp, pilot) -> MultiScreen:
    """Arm the gate and open the eight-track editor."""
    await pilot.press("w")
    await pilot.pause()
    await pilot.press("m")
    assert await _wait(lambda: app._multi_screen is not None, pilot)
    screen = app._multi_screen
    assert screen is not None
    return screen


async def _ask_for_slot(app: EnsqsqedApp, pilot, number: int = 0) -> MultiScreen:
    """Press ``ctrl+shift+s`` and answer the slot prompt.

    ``number`` is the unit's own preset number, ``P00`` through
    ``P79`` -- the same number the unit's display shows.
    """
    screen = app.screen
    assert isinstance(screen, MultiScreen)
    await pilot.press("ctrl+shift+s")
    await pilot.pause()
    prompt = app.screen
    assert isinstance(prompt, NumberPrompt)
    prompt.query_one("#value", Input).value = str(number)
    await pilot.press("enter")
    await pilot.pause()
    assert isinstance(app.screen, ConfirmScreen)
    return screen


class TestTheStoreAssistRefuses:
    """The gates in front of the send: the write gate and the backup."""

    async def test_the_write_gate_closed_refuses_before_any_question(
        self, tmp_path: Path
    ):
        root = tmp_path / "backups"
        root.mkdir()
        _write_backup(root, "20260101-000000", BACKUP_RAW)
        unit = _FakeUnit()
        app = _app(unit, root)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("m")
            assert await _wait(lambda: app._multi_screen is not None, pilot)
            screen = app._multi_screen
            assert screen is not None
            await pilot.press("ctrl+shift+s")
            await pilot.pause()
            # The gate is closed, so no slot prompt opens and nothing
            # reaches the unit.
            assert app.screen is screen
            said = _status(screen)
            assert "the write gate is locked" in said
            assert "Press w" in said
            assert unit.received == []

    async def test_no_backup_is_an_honest_refusal(self, tmp_path: Path):
        root = tmp_path / "backups"
        root.mkdir()
        unit = _FakeUnit()
        app = _app(unit, root)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = await _open_multi(app, pilot)
            await pilot.press("ctrl+shift+s")
            await pilot.pause()
            assert app.screen is screen
            said = _status(screen)
            assert "no all_presets_dump.bin" in said
            assert "no backed-up bytes to send" in said
            assert unit.received == []

    async def test_the_slot_prompt_speaks_the_units_own_numbering(self, tmp_path: Path):
        """The prompt's numbers are the unit's ``P00`` through ``P79``.

        The owner reads the number off the unit's display, so the
        prompt speaks the unit's numbering: ``80`` is refused (P79 is
        the unit's last, and ``80`` is nothing), and the sentence says
        the range the way the unit shows it.
        """
        root = tmp_path / "backups"
        root.mkdir()
        _write_backup(root, "20260101-000000", BACKUP_RAW)
        unit = _FakeUnit()
        app = _app(unit, root)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = await _open_multi(app, pilot)
            await pilot.press("ctrl+shift+s")
            await pilot.pause()
            prompt = app.screen
            assert isinstance(prompt, NumberPrompt)
            prompt.query_one("#value", Input).value = "80"
            await pilot.press("enter")
            await pilot.pause()
            said = _status(screen)
            assert "P00 through P79" in said
            assert "not 80" in said
            assert unit.received == []


class TestTheStoreAssistSends:
    """The bytes, the confirmation, and what the unit says afterwards."""

    async def test_the_confirmation_is_required_before_any_send(self, tmp_path: Path):
        root = tmp_path / "backups"
        root.mkdir()
        _write_backup(root, "20260101-000000", BACKUP_RAW)
        unit = _FakeUnit()
        app = _app(unit, root)
        async with app.run_test() as pilot:
            await pilot.pause()
            await _open_multi(app, pilot)
            await _ask_for_slot(app, pilot)
            # The question is up and nothing has gone out.
            assert unit.received == []
            await pilot.press("n")
            await pilot.pause()
            assert unit.received == []

    async def test_the_newest_backup_is_sent_verbatim(self, tmp_path: Path):
        root = tmp_path / "backups"
        root.mkdir()
        # The older run holds the fixture's own bytes; the newer one
        # holds the sentinel.  Only the newest may be sent.
        _write_backup(root, "20260101-000000", PRESET)
        _write_backup(root, "20260102-000000", BACKUP_RAW)
        unit = _FakeUnit()
        app = _app(unit, root)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = await _open_multi(app, pilot)
            await _ask_for_slot(app, pilot)
            assert unit.received == []
            await pilot.press("y")
            assert await _wait(
                lambda: "stored in P00" in _status(screen),
                pilot,
            )
            # One whole preset went out: the current preset's sequence
            # data, then the backup's 168 bytes exactly -- address bytes
            # included, not stripped and not re-addressed.
            assert len(unit.received) == 1
            sent = unit.received[0]
            assert sent == BLOB + BACKUP_RAW
            assert sent[48:50] == b"\x46\x0b"
            assert len(sent[48:]) == 168
            # The read-back was asked for and matched. Reads: the
            # position read before the walk, the walk's own check-read
            # (the fake's start slot already matches the target, so one
            # chunk check), and the read after the walk for the
            # sequence data, plus the send's verification read.
            assert unit.reads == 5
            said = _status(screen)
            assert "P00" in said
            assert "the unit keeps its own name" in said
            assert "Preset Select Mode" in said

    async def test_a_first_mismatch_then_a_success_is_one_assist(self, tmp_path: Path):
        root = tmp_path / "backups"
        root.mkdir()
        _write_backup(root, "20260101-000000", BACKUP_RAW)
        unit = _FakeUnit(verify_offsets=[82])
        app = _app(unit, root)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = await _open_multi(app, pilot)
            await _ask_for_slot(app, pilot)
            await pilot.press("y")
            assert await _wait(
                lambda: "stored in P00" in _status(screen),
                pilot,
            )
            # The retry sent the same bytes again, and the second
            # read-back is the one that landed.
            assert len(unit.received) == 2
            assert unit.received[0] == BLOB + BACKUP_RAW
            assert unit.received[1] == BLOB + BACKUP_RAW
            assert unit.reads == 6

    async def test_an_exhausted_retry_names_the_last_offsets(self, tmp_path: Path):
        root = tmp_path / "backups"
        root.mkdir()
        _write_backup(root, "20260101-000000", BACKUP_RAW)
        unit = _FakeUnit(verify_offsets=[82, 83, 84])
        app = _app(unit, root)
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = await _open_multi(app, pilot)
            await _ask_for_slot(app, pilot)
            await pilot.press("y")
            assert await _wait(lambda: "not sent" in _status(screen), pilot)
            # Three whole exchanges went out; the last mismatch is the
            # one the sentence carries.
            assert len(unit.received) == 3
            assert all(sent == BLOB + BACKUP_RAW for sent in unit.received)
            said = _status(screen)
            assert "offset(s) 84" in said
            assert "not verified in the selected slot" in said
