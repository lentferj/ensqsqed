# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# ensqsqed is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 2 of the License, or (at your option)
# any later version.

"""The Dump Everything Request: three frames, parsed as one."""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from sq import bridge as b
from sq import messages as m

FIXTURES = Path(__file__).parent / "fixtures"

SOUNDS = [bytes((i + j) % 256 for j in range(204)) for i in range(80)]
SOUNDS_DECODED = b"".join(SOUNDS)
SOUNDS_FRAME = m.frame(
    m.Function.ALL_SOUNDS_DUMP, m.to_nibbles(SOUNDS_DECODED), device=0
)

SEQ_DATA = (FIXTURES / "all_seq.bin").read_bytes()
SEQ_FRAME = m.frame(m.Function.ALL_SEQUENCES_DUMP, m.to_nibbles(SEQ_DATA), device=0)

ALL_ALERT = b.PresetAlert(
    command=m.CommandType.ALL_SEQUENCE_ALERT, size=0x10D4, count=80
)
ALERT_FRAME = m.frame(m.Function.COMMAND, ALL_ALERT.wire_payload(), device=0)


class _FakeIn:
    def __init__(self) -> None:
        self.inbox: List[bytes] = []

    def ignore_types(self, **kwargs) -> None:
        pass

    def get_message(self):
        if not self.inbox:
            return None
        return (self.inbox.pop(0), 0)


class _FakeOut:
    def __init__(self, port: _FakeIn, replies: List[bytes]) -> None:
        self.port = port
        self.replies = list(replies)
        self.sent: List[bytes] = []

    def send_message(self, data) -> None:
        self.sent.append(bytes(data))
        self.port.inbox.extend(self.replies)


def _bridge(replies: List[bytes], *, timeout: float = 0.05):
    port = _FakeIn()
    sink = _FakeOut(port, replies)
    return b.SqBridge(port, sink, channel=0, timeout=timeout), sink


def test_read_everything_parses_three_frames():
    bridge, sink = _bridge([SOUNDS_FRAME, ALERT_FRAME, SEQ_FRAME])

    result = bridge.read_everything(timeout=1.0)

    assert len(result.sounds) == 80
    assert result.sounds[0] == SOUNDS[0]
    assert result.presets.count == 80
    assert result.sounds_wire == SOUNDS_FRAME
    assert result.presets_wire == ALERT_FRAME + SEQ_FRAME
    assert result.wire() == SOUNDS_FRAME + ALERT_FRAME + SEQ_FRAME
    assert sink.sent == [
        m.command_message(m.CommandType.DUMP_EVERYTHING_REQUEST, channel=0)
    ]


def test_read_everything_alert_before_sounds():
    bridge, _sink = _bridge([ALERT_FRAME, SOUNDS_FRAME, SEQ_FRAME])

    result = bridge.read_everything(timeout=1.0)

    assert len(result.sounds) == 80
    assert result.presets.count == 80


def test_read_everything_refusal_raises():
    bridge, _sink = _bridge(
        [m.frame(m.Function.ERROR, m.to_nibbles([m.ErrorCode.NAK]), device=0)]
    )

    with pytest.raises(b.DeviceRefused):
        bridge.read_everything(timeout=1.0)


def test_read_everything_silence_raises_naming_all_sounds_dump():
    bridge, _sink = _bridge([])

    with pytest.raises(TimeoutError) as caught:
        bridge.read_everything(timeout=0.05)

    assert "All Sounds Dump" in str(caught.value)


def test_preset_needs_write():
    base = bytes([0] * 168)
    assert b.preset_needs_write(base, base) is False

    cand = bytearray(base)
    for off in (0, 1, 2, 5, 17, 30):
        cand[off] = 1
    assert b.preset_needs_write(base, bytes(cand)) is False

    # Inside the commit range every byte counts, including byte 9.
    cand9 = bytearray(base)
    cand9[55] = 1
    assert b.preset_needs_write(base, bytes(cand9)) is True

    cand2 = bytearray(base)
    cand2[40] = 1
    assert b.preset_needs_write(base, bytes(cand2)) is True

    with pytest.raises(ValueError):
        b.preset_needs_write(base, base[:-1])


# --- send_preset kept offsets ----------------------------------------------


class _FakeUnit:
    def __init__(self, *, read_back=None, ack=True) -> None:
        self.ack = ack
        self.read_back = (
            (FIXTURES / "single_seq_default.bin").read_bytes()
            if read_back is None
            else read_back
        )
        self.received: List[bytes] = []

    def answer(self, message: bytes, inbox: List[bytes]) -> None:
        packet = m.parse(message)
        if packet is None:
            return
        if packet.function == m.Function.SINGLE_SEQUENCE_DUMP:
            self.received.append(m.from_nibbles(packet.payload))
            return
        if packet.function != m.Function.COMMAND:
            return
        body = m.from_nibbles(packet.payload)
        if not body:
            return
        if body[0] == m.CommandType.SINGLE_SEQUENCE_ALERT:
            if self.ack is None:
                return
            code = m.ErrorCode.ACK if self.ack else m.ErrorCode.NAK
            inbox.append(m.frame(m.Function.ERROR, m.to_nibbles([code]), device=0))
            return
        if body[0] == m.CommandType.SINGLE_SEQUENCE_REQUEST:
            alert = b.PresetAlert(
                command=m.CommandType.SINGLE_SEQUENCE_ALERT, size=48, flag=0
            )
            inbox.append(m.frame(m.Function.COMMAND, alert.wire_payload(), device=0))
            inbox.append(
                m.frame(
                    m.Function.SINGLE_SEQUENCE_DUMP,
                    m.to_nibbles(self.read_back),
                    device=0,
                )
            )


class _FakeOut2:
    def __init__(self, port: _FakeIn, unit: _FakeUnit) -> None:
        self.port = port
        self.unit = unit
        self.sent: List[bytes] = []

    def send_message(self, data) -> None:
        self.sent.append(bytes(data))
        self.unit.answer(bytes(data), self.port.inbox)


def test_send_preset_reports_kept_offsets(monkeypatch):
    monkeypatch.setattr(b, "PRESET_SETTLE", 0.0)
    dump = (FIXTURES / "single_seq_default.bin").read_bytes()
    blob = dump[:48]
    preset = dump[48:]

    read_back = bytearray(dump)
    for off in range(2, 18):
        read_back[48 + off] = (read_back[48 + off] + 1) % 256
    unit = _FakeUnit(read_back=bytes(read_back))
    port = _FakeIn()
    sink = _FakeOut2(port, unit)
    bridge = b.SqBridge(port, sink, channel=0, timeout=0.05)

    result = bridge.send_preset(blob, preset, verify=True)

    assert result.verified is True
    assert result.kept_preset_offsets == tuple(range(2, 18))

    # A writable-byte difference still raises.
    changed = bytearray(dump)
    changed[48 + 34] = (changed[48 + 34] + 1) % 256
    unit2 = _FakeUnit(read_back=bytes(changed))
    port2 = _FakeIn()
    sink2 = _FakeOut2(port2, unit2)
    bridge2 = b.SqBridge(port2, sink2, channel=0, timeout=0.05)

    with pytest.raises(b.PresetMismatch):
        bridge2.send_preset(blob, preset, verify=True)
