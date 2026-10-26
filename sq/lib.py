# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# ensqsqed is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version.

"""Decoder for SoundDiver ``.LIB`` files."""

from __future__ import annotations

from typing import List

from sq import messages as m

__all__ = ["read_lib", "is_lib_path"]

_SEPARATOR = b"\x03\x81\x41\x53\x51\x00"
_MIN_NAME_LEN = 9
_MAX_NAME_LEN = 16
_PAYLOAD_LEN = 190


def is_lib_path(path: str) -> bool:
    """Whether ``path`` names a ``.lib`` file (case-insensitive)."""
    return str(path).lower().endswith(".lib")


def _parse_tnel(body: bytes) -> bytes:
    if len(body) < 14 + _MIN_NAME_LEN + 6 + _PAYLOAD_LEN:
        raise ValueError("TNEL body is too short")

    name_len = body[13]
    if not (_MIN_NAME_LEN <= name_len <= _MAX_NAME_LEN):
        raise ValueError(
            f"TNEL name length {name_len} is outside {_MIN_NAME_LEN}..{_MAX_NAME_LEN}"
        )

    name_offset = 14
    if name_offset + name_len + 6 + _PAYLOAD_LEN > len(body):
        raise ValueError("TNEL body is too short for name, separator and payload")

    name_bytes = body[name_offset : name_offset + name_len]
    if any(byte < 32 or byte > 126 for byte in name_bytes):
        raise ValueError("TNEL name contains non-printable bytes")
    try:
        name = name_bytes.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("TNEL name is not ASCII") from exc

    separator_offset = name_offset + name_len
    if body[separator_offset : separator_offset + 6] != _SEPARATOR:
        raise ValueError("TNEL separator mismatch")

    payload_offset = separator_offset + 6
    payload = body[payload_offset : payload_offset + _PAYLOAD_LEN]
    if len(payload) < _PAYLOAD_LEN:
        raise ValueError("TNEL payload is shorter than 190 bytes")

    record = bytearray(m.SOUND_BYTES)
    record[:177] = payload[:177]
    record[191:204] = payload[177:190]
    try:
        m.encode_name(record, name)
    except m.ProtocolError as exc:
        raise ValueError(str(exc)) from exc

    if len(record) != m.SOUND_BYTES:
        raise ValueError(
            f"reconstructed record is {len(record)} bytes, expected {m.SOUND_BYTES}"
        )
    return bytes(record)


def read_lib(path: str) -> List[bytes]:
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise ValueError(f"cannot read .lib file: {exc}") from exc

    if len(data) < 12:
        raise ValueError("not a valid .lib file: truncated header")

    if data[:4] != b"MROF":
        raise ValueError("not a valid .lib file: missing MROF container")

    size = int.from_bytes(data[4:8], "little")
    if size < 4 or 8 + size > len(data):
        raise ValueError("truncated MROF container in .lib file")

    if data[8:12] != b"BLSS":
        raise ValueError("not a valid .lib file: bad form type")

    bound = 8 + size
    records: List[bytes] = []
    pos = 12
    while pos < bound:
        if pos + 8 > bound:
            raise ValueError("truncated chunk header in .lib file")
        chunk_id = data[pos : pos + 4]
        chunk_size = int.from_bytes(data[pos + 4 : pos + 8], "little")
        content_start = pos + 8
        content_end = content_start + chunk_size
        if content_end > bound or content_end > len(data):
            raise ValueError("chunk overflow in .lib file")

        body = data[content_start:content_end]
        if chunk_id == b"TNEL":
            records.append(_parse_tnel(body))

        pos = content_end

    return records
