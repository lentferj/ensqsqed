# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# ensqsqed is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 2 of the License, or (at your option)
# any later version.

"""Wiring between the remote window and the device.

The screen itself is tested in ``tests/test_remote_screen.py``; here
the device-facing half is exercised with the fake unit from
``tests/test_store_assist.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Static

from ensqsqed.app import EnsqsqedApp
from ensqsqed.remote import RemoteScreen
from sq import catalog as cat
from sq import messages as m
from tests.test_store_assist import _device, _FakeUnit, _wait


def _events(unit: _FakeUnit) -> list[int]:
    out = []
    for raw in unit.button_messages:
        packet = m.parse(raw)
        assert packet is not None
        body = m.from_nibbles(packet.payload)
        assert body[0] == m.CommandType.VIRTUAL_BUTTON
        out.append(body[1])
    return out


def test_tap_button_sends_two_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    """One down frame, then the matching up frame."""
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    unit = _FakeUnit()
    device = _device(unit)

    device.tap_button(7)

    assert len(unit.button_messages) == 2
    assert _events(unit) == [7, 7 + m.CommandType.BUTTON_UP_OFFSET]


async def test_opening_the_remote_window_works(tmp_path: Path) -> None:
    unit = _FakeUnit()
    app = EnsqsqedApp(_device(unit), catalog=cat.empty())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert isinstance(app.screen, RemoteScreen)


async def test_pressing_the_system_key_sends_eleven(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    unit = _FakeUnit()
    app = EnsqsqedApp(_device(unit), catalog=cat.empty(), allow_write=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+o")
        await pilot.pause()
        await pilot.press("u")
        assert await _wait(lambda: len(unit.button_messages) >= 2, pilot)
        assert _events(unit) == [11, 11 + m.CommandType.BUTTON_UP_OFFSET]


async def test_the_remote_needs_no_write_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    unit = _FakeUnit()
    app = EnsqsqedApp(_device(unit), catalog=cat.empty(), allow_write=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+o")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, RemoteScreen)
        await pilot.press("u")
        assert await _wait(lambda: len(unit.button_messages) >= 2, pilot)
        assert _events(unit) == [11, 11 + m.CommandType.BUTTON_UP_OFFSET]


async def test_a_burst_of_presses_is_buffered_not_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two quick presses are queued and both reach the unit, in order."""
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    unit = _FakeUnit()
    app = EnsqsqedApp(_device(unit), catalog=cat.empty(), allow_write=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+o")
        await pilot.pause()
        await pilot.press("u")
        await pilot.press("u")
        assert await _wait(lambda: len(unit.button_messages) >= 4, pilot)
        assert _events(unit) == [
            11,
            11 + m.CommandType.BUTTON_UP_OFFSET,
            11,
            11 + m.CommandType.BUTTON_UP_OFFSET,
        ]
