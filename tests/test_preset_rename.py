# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors

"""Renaming a preset on the unit: the button sequence and the method."""

from __future__ import annotations

import pytest

from sq import bridge as b


def test_preset_name_presses_small_example():
    current = "Preset-79"
    target = "Qreset-79"
    presses = b.preset_name_presses(current, target, pending=False)
    exp = [9, 7]
    cur = (current + " " * 16)[:16]
    tgt = (target + " " * 16)[:16]
    for pos in range(16):
        delta = ord(tgt[pos]) - ord(cur[pos])
        if delta > 0:
            exp += [7] * delta
        elif delta < 0:
            exp += [5] * (-delta)
        if pos < 15:
            exp.append(8)
    exp += [9, 9]
    assert presses == exp
    assert presses[:4] == [9, 7, 7, 8]


def test_preset_name_presses_pending_skips_yes():
    # With no character to change, the first post-prompt press is Right;
    # the non-pending path inserts Yes (7) before it.
    pending = b.preset_name_presses("A", "A", pending=True)
    nonpending = b.preset_name_presses("A", "A", pending=False)
    assert pending[:2] == [9, 8]
    assert nonpending[:3] == [9, 7, 8]
    assert nonpending[1:] == [7] + pending[1:]


def test_preset_name_presses_validation_non_ascii():
    with pytest.raises(ValueError):
        b.preset_name_presses("abc", "ab\xc3", pending=False)


def test_rename_validation_slot_and_name():
    class _Port:
        def get_message(self):
            return None

        def send_message(self, data):
            pass

        def ignore_types(self, **kwargs):
            pass

    bridge = b.SqBridge(_Port(), _Port(), channel=0, timeout=0.05)
    with pytest.raises(ValueError):
        bridge.rename_preset_on_unit(80, "X")
    with pytest.raises(ValueError):
        bridge.rename_preset_on_unit(0, "a" * 17)
    with pytest.raises(ValueError):
        bridge.rename_preset_on_unit(0, "bad\x01name")


def test_rename_preset_on_unit_fake():
    PRESET_LEN = 168
    raw = bytearray(PRESET_LEN)
    raw[0] = 3
    name = "OldName"
    nb = (name + " " * 16)[:16].encode("ascii")
    for i in range(16):
        raw[2 + i] = nb[i] | (0x80 if i < 8 else 0x00)
    raw_bytes = bytes(raw)

    class _Port:
        def get_message(self):
            return None

        def send_message(self, data):
            pass

        def ignore_types(self, **kwargs):
            pass

    bridge = b.SqBridge(_Port(), _Port(), channel=0, timeout=0.05)
    taps = []
    bridge.tap_button = lambda number, hold=0.12: taps.append(number)
    walks = []

    def walk(target, known_slot, force_reload=False, check_every=8, settle=0.4):
        walks.append((target, known_slot))
        return target

    bridge.select_preset_by_walk = walk

    new_name = "NewName"
    new_raw = bytearray(raw_bytes)
    nn = (new_name + " " * 16)[:16].encode("ascii")
    for i in range(16):
        new_raw[2 + i] = (new_raw[2 + i] & 0x80) | nn[i]
    new_raw[0] = 3

    reads = iter(
        [
            b.CurrentPreset(seq_blob=b"", preset_raw=raw_bytes),
            b.CurrentPreset(seq_blob=b"", preset_raw=bytes(new_raw)),
        ]
    )

    def read_current(timeout=None):
        return next(reads)

    bridge.read_current_preset = read_current

    progress_calls = []
    result = bridge.rename_preset_on_unit(
        3,
        new_name,
        pending=False,
        progress=lambda pos, total: progress_calls.append((pos, total)),
    )
    assert result == (new_name + " " * (16 - len(new_name)))
    assert taps[0] == 9
    assert walks == [(4, 3), (3, 4)]
    assert len(progress_calls) == 16
