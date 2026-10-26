# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# ensqsqed is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 2 of the License, or (at your option)
# any later version.
"""Decoder for SoundQuest ``.sqs`` files.

The format was determined by analysing the owner's own ``.sqs`` files (see
``/tmp/opencode/sqs-analysis/REPORT.md``): a top-level IFF ``LIST``
container holding one or more ``FORM ... BANK`` records, each of which
carries a ``BODY`` chunk whose payload is either concatenated Ensoniq
SysEx messages or SoundQuest-native data.

The 32648-byte ``BODY`` whose first bytes are *not* ``F0 0F 06`` is
SoundQuest-native data (an 8-byte header plus 80 slots of 408 nibble
bytes). Its records do not match the instrument's SysEx layout and cannot
be safely decoded into sounds, so that body is deliberately ignored rather
than guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from sq import messages as m

__all__ = ["Bank", "read_sqs", "is_sqs_path"]

_SLOTS = 80
_PACKED = m.SOUND_BYTES_PACKED  # 408
_FRAME = 415


@dataclass(frozen=True)
class Bank:
    """One decoded bank from an ``.sqs`` file."""

    label: str
    kind: str
    slots: Tuple[Optional[bytes], ...]  # exactly 80; None for empty


def is_sqs_path(path: str) -> bool:
    """Whether ``path`` names an ``.sqs`` file (case-insensitive)."""
    return str(path).lower().endswith(".sqs")


def _is_all_zero(data: bytes) -> bool:
    return all(byte == 0 for byte in data)


def _decode_all_sounds(body: bytes) -> Optional[Bank]:
    packet = m.parse(body[:32647])
    if packet is None or not packet.is_all_sounds or not packet.size_is_right:
        raise ValueError("invalid all-sounds SysEx frame in .sqs BODY")
    payload = packet.payload
    if len(payload) != _SLOTS * _PACKED:
        raise ValueError("invalid all-sounds payload size in .sqs BODY")
    slots: List[Optional[bytes]] = [None] * _SLOTS
    decoded_any = False
    for index in range(_SLOTS):
        chunk = payload[index * _PACKED : (index + 1) * _PACKED]
        if _is_all_zero(chunk):
            continue
        slots[index] = m.from_nibbles(chunk)
        decoded_any = True
    if not decoded_any:
        return None
    return Bank(label="", kind="", slots=tuple(slots))


def _decode_single_sounds(body: bytes, block_count: int) -> Optional[Bank]:
    if len(body) == 416:
        blocks = [body[:_FRAME]]
    else:
        blocks = [body[i * _FRAME : (i + 1) * _FRAME] for i in range(block_count)]
    if any(len(block) != _FRAME for block in blocks):
        raise ValueError("truncated single-sound block in .sqs BODY")
    slots: List[Optional[bytes]] = [None] * _SLOTS
    decoded_any = False
    for index, block in enumerate(blocks):
        if _is_all_zero(block):
            continue
        packet = m.parse(block)
        if packet is None or not packet.is_single_sound or not packet.size_is_right:
            raise ValueError("invalid single-sound SysEx frame in .sqs BODY")
        slots[index] = m.from_nibbles(packet.payload)
        decoded_any = True
    if not decoded_any:
        return None
    return Bank(label="", kind="", slots=tuple(slots))


def _decode_body(body: bytes) -> Optional[Bank]:
    size = len(body)
    if size == 32648:
        if body[:3] != b"\xf0\x0f\x06":
            # SoundQuest-native body: deliberately ignored.
            return None
        return _decode_all_sounds(body)
    if size == 33200:
        return _decode_single_sounds(body, 80)
    if size == 8300:
        return _decode_single_sounds(body, 20)
    if size == 416:
        return _decode_single_sounds(body, 1)
    # Unknown BODY variant: skip.
    return None


def _banks_in_form(data: bytes, form_offset: int, bound: int) -> List[Bank]:
    result: List[Bank] = []
    p = form_offset + 12
    while p < bound:
        while p < bound and not (
            p + 4 <= len(data) and all(32 <= c < 127 for c in data[p : p + 4])
        ):
            p += 1
        if p >= bound:
            break
        if p + 8 > bound:
            raise ValueError("truncated chunk header in .sqs file")
        chunk_id = data[p : p + 4]
        chunk_size = int.from_bytes(data[p + 4 : p + 8], "big")
        content_start = p + 8
        content_end = content_start + chunk_size
        if content_end > bound or content_end > len(data):
            raise ValueError("truncated chunk body in .sqs file")
        padded_end = min(content_end + (chunk_size & 1), bound)
        content = data[content_start:content_end]
        if chunk_id == b"BODY":
            bank = _decode_body(content)
            if bank is not None:
                result.append(bank)
        p = padded_end
    return result


def read_sqs(path: str) -> List[Bank]:
    """Read an ``.sqs`` file and return its decoded banks in file order."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise ValueError(f"cannot read .sqs file: {exc}") from exc

    if not data:
        raise ValueError("empty .sqs file")
    if len(data) < 12 or data[:4] != b"LIST":
        raise ValueError("not a valid .sqs file: missing LIST container")
    list_size = int.from_bytes(data[4:8], "big")
    if list_size < 4 or 8 + list_size > len(data):
        raise ValueError("truncated LIST container in .sqs file")

    form_offsets: List[int] = []
    pos = 0
    while True:
        index = data.find(b"FORM", pos)
        if index < 0:
            break
        if index + 12 <= len(data) and data[index + 8 : index + 12] == b"BANK":
            form_offsets.append(index)
        pos = index + 4
    if not form_offsets:
        raise ValueError("no FORM ... BANK records found in .sqs file")

    banks: List[Bank] = []
    for fi, form_offset in enumerate(form_offsets):
        bound = form_offsets[fi + 1] if fi + 1 < len(form_offsets) else len(data)
        banks.extend(_banks_in_form(data, form_offset, bound))
    return banks
