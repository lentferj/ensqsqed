# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# ensqsqed is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 2 of the License, or (at your option)
# any later version.

"""Committing a preset by re-selecting it: the helper and the walk."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sq import bridge as b

PRESET_LEN = 168


def _preset(value: int = 0) -> bytearray:
    return bytearray([value] * PRESET_LEN)


# ---------------------------------------------------------------------------
# preset_commit_matches
# ---------------------------------------------------------------------------


def test_preset_commit_matches_true_when_only_name_and_address_differ():
    expected = _preset(0)
    stored = _preset(0)
    # The address bytes and the name are the unit's own.
    for offset in (0, 1, 2, 5, 17):
        expected[offset] = 0x11
        stored[offset] = 0x22
    assert b.preset_commit_matches(bytes(expected), bytes(stored)) is True


def test_preset_commit_matches_false_when_a_track_or_effect_number_differs():
    # Measured 2026-10-25: sound, channel and effect number do persist, so
    # a difference there is a failed store, not a kept field.  Track byte
    # 9 (offset 41, program/sustain) is the exception: the firmware store
    # saves and restores it from the slot's own values.
    for offset in (32, 40, 162):
        expected = _preset(0)
        stored = _preset(0)
        expected[offset] = 0x11
        stored[offset] = 0x22
        assert b.preset_commit_matches(bytes(expected), bytes(stored)) is False


def test_preset_commit_matches_ignores_a_kept_track_program_byte():
    # The store restores every track byte 9 from the slot's own values
    # (`$C128FA`), so it is not a field a commit can write.
    expected = _preset(0)
    stored = _preset(0)
    expected[41] = 0x81
    stored[41] = 0x80
    assert b.preset_commit_matches(bytes(expected), bytes(stored)) is True


def test_preset_commit_matches_false_when_a_volume_byte_differs():
    expected = _preset(0)
    stored = _preset(0)
    expected[34] = 0x55
    stored[34] = 0x66
    assert b.preset_commit_matches(bytes(expected), bytes(stored)) is False


def test_preset_commit_matches_false_when_an_effect_param_differs():
    expected = _preset(0)
    stored = _preset(0)
    expected[152] = 0x39
    stored[152] = 0x38
    assert b.preset_commit_matches(bytes(expected), bytes(stored)) is False


def test_preset_commit_matches_raises_on_wrong_length():
    with pytest.raises(ValueError):
        b.preset_commit_matches(bytes(PRESET_LEN - 1), bytes(PRESET_LEN))
    with pytest.raises(ValueError):
        b.preset_commit_matches(bytes(PRESET_LEN), bytes(PRESET_LEN + 1))


# ---------------------------------------------------------------------------
# commit_current_preset
# ---------------------------------------------------------------------------


class _DummyPort:
    def get_message(self):
        return None

    def send_message(self, data):
        pass

    def ignore_types(self, **kwargs):
        pass


def _make_bridge():
    return b.SqBridge(_DummyPort(), _DummyPort(), channel=0, timeout=0.05)


def test_commit_current_preset_walk_sequence_and_stored_source():
    slot = 5
    neighbour = 6
    working = bytearray(PRESET_LEN)
    working[0] = slot
    working[34] = 0x12
    working[152] = 0x39
    working_bytes = bytes(working)

    stored = bytearray(working_bytes)
    # The name (and address bytes) are the unit's own; everything else
    # is expected to match after a commit.
    stored[2] = 0x99
    stored_bytes = bytes(stored)

    bridge = _make_bridge()

    calls = []

    def fake_walk(target_slot, *, known_slot, **kwargs):
        calls.append((target_slot, known_slot))
        return target_slot

    bridge.select_preset_by_walk = fake_walk
    bridge.read_current_preset = lambda *a, **k: b.CurrentPreset(
        seq_blob=b"", preset_raw=working_bytes
    )
    bridge.read_all_presets = lambda *a, **k: SimpleNamespace(
        slot_offsets=lambda: {slot: 0},
        presets_raw=(stored_bytes,),
    )

    result = bridge.commit_current_preset(slot=slot, neighbour=neighbour, timeout=0.1)

    assert calls == [(neighbour, slot), (slot, neighbour)]
    assert result.slot == slot
    assert result.neighbour == neighbour
    assert result.stored_raw == stored_bytes
    assert result.working_raw == working_bytes
    assert result.after_raw == working_bytes
    assert result.matched is True


def test_commit_current_preset_matched_false_when_stored_effect_differs():
    slot = 7
    neighbour = 8
    working = bytearray(PRESET_LEN)
    working[0] = slot
    working[152] = 0x39
    working_bytes = bytes(working)

    stored = bytearray(working_bytes)
    stored[152] = 0x38  # effect reverted
    stored_bytes = bytes(stored)

    bridge = _make_bridge()

    calls = []

    def fake_walk(target_slot, *, known_slot, **kwargs):
        calls.append((target_slot, known_slot))
        return target_slot

    bridge.select_preset_by_walk = fake_walk
    bridge.read_current_preset = lambda *a, **k: b.CurrentPreset(
        seq_blob=b"", preset_raw=working_bytes
    )
    bridge.read_all_presets = lambda *a, **k: SimpleNamespace(
        slot_offsets=lambda: {slot: 0},
        presets_raw=(stored_bytes,),
    )

    result = bridge.commit_current_preset(slot=slot, neighbour=neighbour)

    assert result.matched is False


def test_commit_current_preset_derives_slot_from_working():
    slot = 9
    working = bytearray(PRESET_LEN)
    working[0] = slot
    working_bytes = bytes(working)
    stored_bytes = bytes(working)

    bridge = _make_bridge()
    calls = []

    def fake_walk(target_slot, *, known_slot, **kwargs):
        calls.append((target_slot, known_slot))
        return target_slot

    bridge.select_preset_by_walk = fake_walk
    bridge.read_current_preset = lambda *a, **k: b.CurrentPreset(
        seq_blob=b"", preset_raw=working_bytes
    )
    bridge.read_all_presets = lambda *a, **k: SimpleNamespace(
        slot_offsets=lambda: {slot: 0},
        presets_raw=(stored_bytes,),
    )

    result = bridge.commit_current_preset(timeout=0.1)

    assert result.slot == slot
    assert result.neighbour == (slot + 1) % 80
    assert calls == [((slot + 1) % 80, slot), (slot, (slot + 1) % 80)]
