# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# ensqsqed is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 2 of the License, or (at your option)
# any later version.

"""Writing sounds: the bridge path and the demo mirror."""

from __future__ import annotations

from typing import List, Optional

import pytest

from ensqsqed.demo import DemoDevice
from sq import banks
from sq import bridge as b
from sq import messages as m


class _FakeIn:
    def __init__(self) -> None:
        self.inbox: List[bytes] = []

    def ignore_types(self, **kwargs) -> None:
        return None

    def get_message(self):
        if not self.inbox:
            return None
        return (self.inbox.pop(0), 0)


class _FakeUnit:
    def __init__(self, *, mismatch_slots: Optional[set] = None) -> None:
        self.current_group = "INT"
        self.selected_slot = 0
        self.mismatch_slots = set(mismatch_slots or ())
        self.stored: dict = {
            "INT": {i: bytes([i & 0xFF]) * 204 for i in range(banks.SOUNDS_PER_GROUP)},
            "CARDA": {
                i: bytes([100 + (i & 0xFF)]) * 204
                for i in range(banks.SOUNDS_PER_GROUP)
            },
            "CARDB": {
                i: bytes([(150 + i) % 256]) * 204 for i in range(banks.SOUNDS_PER_GROUP)
            },
        }
        self.received_all: List[bytes] = []
        self.received_single: List[bytes] = []

    def _read_int(self) -> dict:
        out = {}
        for i, rec in self.stored["INT"].items():
            if i in self.mismatch_slots:
                changed = bytearray(rec)
                changed[0] = (changed[0] + 1) % 256
                out[i] = bytes(changed)
            else:
                out[i] = rec
        return out

    def answer(self, message: bytes, inbox: List[bytes]) -> None:  # noqa: C901
        data = bytes(message)
        # Track group/slot selection from raw program changes.
        if len(data) == 2 and (data[0] & 0xF0) == 0xC0:
            pc = data[1]
            if pc in (124, 125, 126, 127):
                self.current_group = {
                    124: "INT",
                    125: "ROM",
                    126: "CARDA",
                    127: "CARDB",
                }[pc]
            elif 0 <= pc < banks.SOUNDS_PER_GROUP:
                self.selected_slot = pc
            return

        packet = m.parse(message)
        if packet is None:
            return
        if packet.function == m.Function.SINGLE_SOUND_DUMP:
            self.received_single.append(bytes(message))
            raw = m.from_nibbles(packet.payload)
            if self.current_group in self.stored:
                self.stored[self.current_group][self.selected_slot] = raw
            return
        if packet.function == m.Function.ALL_SOUNDS_DUMP:
            self.received_all.append(bytes(message))
            payload = m.from_nibbles(packet.payload)
            if self.current_group in self.stored:
                for i in range(banks.SOUNDS_PER_GROUP):
                    chunk = payload[i * m.SOUND_BYTES : (i + 1) * m.SOUND_BYTES]
                    self.stored[self.current_group][i] = chunk
            return
        if packet.function != m.Function.COMMAND:
            return
        body = m.from_nibbles(packet.payload)
        if not body:
            return
        command = body[0]
        if command == 0x02:  # INTERNAL_BANKS_REQUEST
            bank = self._read_int()
            payload = b"".join(
                m.to_nibbles(bank[i]) for i in range(banks.SOUNDS_PER_GROUP)
            )
            inbox.append(m.frame(m.Function.ALL_SOUNDS_DUMP, payload, device=0))
            return
        if command == 0x01:  # SINGLE_SOUND_REQUEST
            rec = self.stored[self.current_group][self.selected_slot]
            if (
                self.current_group == "INT"
                and self.selected_slot in self.mismatch_slots
            ):
                changed = bytearray(rec)
                changed[0] = (changed[0] + 1) % 256
                rec = bytes(changed)
            inbox.append(
                m.frame(m.Function.SINGLE_SOUND_DUMP, m.to_nibbles(rec), device=0)
            )
            return


class _FakeOut:
    def __init__(self, port: _FakeIn, unit: _FakeUnit) -> None:
        self.port = port
        self.unit = unit
        self.sent: List[bytes] = []

    def send_message(self, data) -> None:
        self.sent.append(bytes(data))
        self.unit.answer(bytes(data), self.port.inbox)


def _device(unit: _FakeUnit) -> b.SqBridge:
    port = _FakeIn()
    bridge = b.SqBridge(port, _FakeOut(port, unit), channel=0, timeout=0.05)
    bridge._out._gap = 0.0
    return bridge


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(b, "SCAN_SETTLE", 0.0)


def test_invalid_group_raises() -> None:
    unit = _FakeUnit()
    bridge = _device(unit)
    for bad in ("ROM", "DRUM", "NOPE"):
        with pytest.raises(ValueError):
            bridge.send_sound_slots(bad, {0: bytes(204)})
        with pytest.raises(ValueError):
            bridge.send_sound_bank(bad, [bytes(204)] * 80)
        with pytest.raises(ValueError):
            bridge.read_bank(bad)
        with pytest.raises(ValueError):
            bridge.read_bank_names(bad)


def test_send_sound_slots_writes_and_verifies_int() -> None:
    unit = _FakeUnit()
    bridge = _device(unit)
    rec0 = bytes([0xAB]) * 204
    rec5 = bytes([0xCD]) * 204
    assignments = {0: rec0, 5: rec5}
    result = bridge.send_sound_slots("INT", assignments)
    assert result.group == "INT"
    assert result.slots_written == (0, 5)
    assert result.verified is True
    assert result.diff_slots == ()
    assert result.read_back is not None
    assert result.read_back[0] == rec0
    assert result.read_back[5] == rec5
    assert len(unit.received_single) == 2


def test_mismatch_reported_in_diff_slots() -> None:
    unit = _FakeUnit(mismatch_slots={0})
    bridge = _device(unit)
    rec0 = bytes([0xAB]) * 204
    result = bridge.send_sound_slots("INT", {0: rec0})
    assert result.verified is False
    assert result.diff_slots == (0,)


def test_read_bank_int_returns_80() -> None:
    unit = _FakeUnit()
    bridge = _device(unit)
    bank = bridge.read_bank("INT")
    assert len(bank) == 80
    assert all(len(v) == 204 for v in bank.values())


def test_read_bank_carda_scans() -> None:
    unit = _FakeUnit()
    bridge = _device(unit)
    bank = bridge.read_bank("CARDA")
    assert len(bank) == 80
    assert all(len(v) == 204 for v in bank.values())


def test_demo_send_sound_slots_updates_banks() -> None:
    demo = DemoDevice()
    rec = bytes([0xEF]) * 204
    demo.send_sound_slots("INT", {0: rec})
    assert demo.read_bank("INT")[0] == rec


def test_read_bank_names_int() -> None:
    unit = _FakeUnit()
    bridge = _device(unit)
    names = bridge.read_bank_names("INT")
    assert len(names) == 80
    assert all(isinstance(v, str) for v in names.values())


def test_send_sound_bank_int() -> None:
    unit = _FakeUnit()
    bridge = _device(unit)
    records = [bytes([i + 1]) * 204 for i in range(80)]
    result = bridge.send_sound_bank("INT", records)
    assert result.slots_written == tuple(range(80))
    assert result.verified is True
    assert result.diff_slots == ()
    assert len(unit.received_all) == 1
