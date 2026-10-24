# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# The wire protocol is transcribed as data from Ensoniq's MIDI System
# Exclusive Implementation Specification version 1.43, printed as
# Appendix A of the SQ-1 Musician's Manual, and checked against 116 dump
# files produced by three instruments. The transport layer is ported from
# the sibling p2ked, x5ded, kwsed, rxved, nanosyned and emorphed projects.
#
# ensqsqed is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 2 of the License, or (at your option)

"""An SQ-R that is not there.

**Every name here is invented**, from a word pool chosen to be obviously
not Ensoniq's, so that a screenshot taken in demo mode cannot be mistaken
for a real machine's contents. It opens no MIDI port and constructs no
bridge, so it is safe beside a session driving hardware.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from sq import banks
from sq import bridge as b
from sq import messages as m

__all__ = ["DemoDevice", "demo_name"]

_HEADS = (
    "Tin",
    "Glass",
    "Chalk",
    "Copper",
    "Velvet",
    "Cork",
    "Willow",
    "Pewter",
    "Basalt",
    "Linen",
    "Amber",
    "Slate",
)
_TAILS = (
    "Bell",
    "Drone",
    "Wash",
    "Sweep",
    "Hum",
    "Comb",
    "Gust",
    "Loom",
    "Pluck",
    "Shine",
)


def demo_name(group_id: str, number: int) -> str:
    """An invented name, stable for a given slot, sixteen characters."""
    seed = sum(ord(c) for c in group_id) * 7 + number * 13
    head = _HEADS[seed % len(_HEADS)]
    tail = _TAILS[(seed // len(_HEADS)) % len(_TAILS)]
    # Centred with spaces, the way this instrument centres its own.
    text = f"{head} {tail}"[: m.NAME_CHARS]
    pad = m.NAME_CHARS - len(text)
    return " " * (pad // 2) + text + " " * (pad - pad // 2)


def _record(group_id: str, number: int) -> bytes:
    record = bytearray(m.SOUND_BYTES)
    m.encode_name(record, demo_name(group_id, number))
    # A byte with bit 7 set, past the name, so the nibble packing is
    # actually exercised. A fake whose data were all 7-bit would let a
    # broken pack/unpack pass every test.
    record[-1] = 0x80 | (number & 0x7F)
    return bytes(record)


class DemoDevice:
    """Answers the subset of the protocol ensqsqed speaks."""

    def __init__(self, *, latency: float = 0.0, channel: int = 0) -> None:
        self.description = "demo (no MIDI ports opened)"
        self.channel = channel
        self.timeout = 1.0
        self.latency = latency
        self.port: Optional[str] = None
        self.recv_port: Optional[str] = None
        self.selected: Optional[banks.Slot] = None
        #: ``(slot, channel)`` for every selection, in order.
        self.selected_log: List[Tuple[banks.Slot, int]] = []
        self._closed = False
        self._banks: Dict[str, Dict[int, bytes]] = {
            group_id: {n: _record(group_id, n) for n in range(banks.SOUNDS_PER_GROUP)}
            for group_id in ("INT", "CARDA", "CARDB")
        }

    def close(self) -> None:
        self._closed = True

    def _tick(self) -> None:
        if self._closed:
            raise RuntimeError(
                "this demo device is closed; a real bridge would be a "
                "closed port and would fail less clearly"
            )
        if self.latency:
            time.sleep(self.latency)

    # --- reading ------------------------------------------------------------

    def read_internal_bank(
        self, *, timeout: Optional[float] = None
    ) -> Dict[int, bytes]:
        self._tick()
        return dict(self._banks["INT"])

    def read_names(self, *, timeout: Optional[float] = None) -> Dict[int, str]:
        return {
            n: m.sound_name(r)
            for n, r in self.read_internal_bank(timeout=timeout).items()
        }

    def read_current_sound(self, *, timeout: Optional[float] = None) -> bytes:
        """Whatever was last selected, or Internal 1 if nothing has been."""
        self._tick()
        slot = self.selected
        if slot is None:
            return self._banks["INT"][0]
        group_id = slot.group_id
        if group_id not in self._banks:
            return _record(group_id, slot.number - 1)
        return self._banks[group_id][slot.number - 1]

    def _sound_at(self, sounds, index: int) -> bytes:
        if isinstance(sounds, dict):
            return sounds[index]
        return sounds[index]

    def read_bank(
        self, group_id: str, *, timeout: Optional[float] = None
    ) -> Dict[int, bytes]:
        if group_id not in b.WRITABLE_SOUND_GROUPS:
            raise ValueError(
                f"{group_id!r} is not a writable sound group; the writable "
                f"groups are {', '.join(b.WRITABLE_SOUND_GROUPS)}."
            )
        self._tick()
        if group_id == "INT":
            return dict(self._banks["INT"])
        out: Dict[int, bytes] = {}
        for slot_number in range(banks.SOUNDS_PER_GROUP):
            self.select(banks.slot(group_id, slot_number + 1))
            time.sleep(b.SCAN_SETTLE)
            out[slot_number] = self.read_current_sound(timeout=timeout)
        return out

    def read_bank_names(
        self, group_id: str, *, timeout: Optional[float] = None
    ) -> Dict[int, str]:
        if group_id not in b.WRITABLE_SOUND_GROUPS:
            raise ValueError(
                f"{group_id!r} is not a writable sound group; the writable "
                f"groups are {', '.join(b.WRITABLE_SOUND_GROUPS)}."
            )
        return {
            number: m.sound_name(record).rstrip()
            for number, record in self.read_bank(group_id, timeout=timeout).items()
        }

    def send_sound_slots(
        self,
        group_id: str,
        assignments: Any,
        *,
        verify: bool = True,
        timeout: Optional[float] = None,
    ) -> b.SoundBankWrite:
        if group_id not in b.WRITABLE_SOUND_GROUPS:
            raise ValueError(
                f"{group_id!r} is not a writable sound group; the writable "
                f"groups are {', '.join(b.WRITABLE_SOUND_GROUPS)}."
            )
        self._tick()
        if not isinstance(assignments, dict):
            assignments = {
                index: assignments[index] for index in range(len(assignments))
            }
        for slot_number in sorted(assignments):
            self.select(banks.slot(group_id, slot_number + 1))
            self._banks[group_id][slot_number] = assignments[slot_number]
        slots_written = tuple(sorted(assignments))
        if group_id == "INT":
            read_back = self.read_bank(group_id, timeout=timeout) if verify else None
            return b.SoundBankWrite(
                group=group_id,
                slots_written=slots_written,
                verified=bool(verify),
                diff_slots=(),
                read_back=read_back,
            )
        return b.SoundBankWrite(
            group=group_id,
            slots_written=slots_written,
            verified=False,
            diff_slots=(),
            read_back=None,
        )

    def send_sound_bank(
        self,
        group_id: str,
        sounds: Any,
        *,
        verify: bool = True,
        timeout: Optional[float] = None,
    ) -> b.SoundBankWrite:
        if group_id not in b.WRITABLE_SOUND_GROUPS:
            raise ValueError(
                f"{group_id!r} is not a writable sound group; the writable "
                f"groups are {', '.join(b.WRITABLE_SOUND_GROUPS)}."
            )
        self._tick()
        for index in range(banks.SOUNDS_PER_GROUP):
            self._banks[group_id][index] = self._sound_at(sounds, index)
        slots_written = tuple(range(banks.SOUNDS_PER_GROUP))
        if group_id == "INT":
            read_back = self.read_bank(group_id, timeout=timeout) if verify else None
            return b.SoundBankWrite(
                group=group_id,
                slots_written=slots_written,
                verified=bool(verify),
                diff_slots=(),
                read_back=read_back,
            )
        return b.SoundBankWrite(
            group=group_id,
            slots_written=slots_written,
            verified=False,
            diff_slots=(),
            read_back=None,
        )

    # --- the one thing that makes a sound ----------------------------------

    def select(self, target: banks.Slot) -> None:
        """Select a sound. **This changes what the unit is playing.**"""
        self._tick()
        # Same refusals as the real path, so a demo cannot accept what the
        # instrument would not.
        banks.select_messages(target, self.channel)
        self.selected = target
        self.selected_log.append((target, self.channel))
