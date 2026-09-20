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
from typing import Dict, List, Optional, Tuple

from sq import banks
from sq import messages as m

__all__ = ["DemoDevice", "demo_name"]

_HEADS = ("Tin", "Glass", "Chalk", "Copper", "Velvet", "Cork", "Willow",
          "Pewter", "Basalt", "Linen", "Amber", "Slate")
_TAILS = ("Bell", "Drone", "Wash", "Sweep", "Hum", "Comb", "Gust", "Loom",
          "Pluck", "Shine")


def demo_name(group_id: str, number: int) -> str:
    """An invented name, stable for a given slot, sixteen characters."""
    seed = sum(ord(c) for c in group_id) * 7 + number * 13
    head = _HEADS[seed % len(_HEADS)]
    tail = _TAILS[(seed // len(_HEADS)) % len(_TAILS)]
    # Centred with spaces, the way this instrument centres its own.
    text = f"{head} {tail}"[:m.NAME_CHARS]
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

    def close(self) -> None:
        self._closed = True

    def _tick(self) -> None:
        if self._closed:
            raise RuntimeError(
                "this demo device is closed; a real bridge would be a "
                "closed port and would fail less clearly")
        if self.latency:
            time.sleep(self.latency)

    # --- reading ------------------------------------------------------------

    def read_internal_bank(self, *, timeout: Optional[float] = None
                           ) -> Dict[int, bytes]:
        self._tick()
        return {n: _record("INT", n)
                for n in range(banks.SOUNDS_PER_GROUP)}

    def read_names(self, *, timeout: Optional[float] = None
                   ) -> Dict[int, str]:
        return {n: m.sound_name(r)
                for n, r in self.read_internal_bank(timeout=timeout).items()}

    def read_current_sound(self, *, timeout: Optional[float] = None) -> bytes:
        """Whatever was last selected, or Internal 1 if nothing has been."""
        self._tick()
        slot = self.selected
        if slot is None:
            return _record("INT", 0)
        return _record(slot.group_id, slot.number - 1)

    # --- the one thing that makes a sound ----------------------------------

    def select(self, target: banks.Slot) -> None:
        """Select a sound. **This changes what the unit is playing.**"""
        self._tick()
        # Same refusals as the real path, so a demo cannot accept what the
        # instrument would not.
        banks.select_messages(target, self.channel)
        self.selected = target
        self.selected_log.append((target, self.channel))
