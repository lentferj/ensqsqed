# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# ensqsqed is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version.

"""Synthetic tests for the SoundDiver ``.LIB`` decoder."""

from __future__ import annotations

import os
import struct
import tempfile

import pytest

from sq import lib
from sq import messages as m


def _record(name: str) -> bytes:
    """An invented 204-byte record carrying ``name``."""
    record = bytearray(m.SOUND_BYTES)
    # Fill non-name bytes deterministically so reconstruction is checkable.
    for index in range(m.SOUND_BYTES):
        record[index] = (index * 37 + 11) & 0xFF
    m.encode_name(record, name)
    return bytes(record)


def _tnel(name: str, payload: bytes, trailer: bytes = b"\x00" * 6) -> bytes:
    body = (
        b"\x00\xff\x00\x00"
        + b"\x01\x02\x03\x04"
        + b"\x05\x06\x07\x08"
        + b"\x01"
        + bytes([len(name)])
        + name.encode("ascii")
        + b"\x03\x81\x41\x53\x51\x00"
        + payload
        + trailer
    )
    return b"TNEL" + struct.pack("<I", len(body)) + body


def _container(chunks: bytes) -> bytes:
    content = b"BLSS" + chunks
    return b"MROF" + struct.pack("<I", len(content)) + content


def _payload_from_record(record: bytes) -> bytes:
    return record[:177] + record[191:204]


@pytest.mark.parametrize("name_len", [9, 12, 16])
def test_one_entry_reconstructs_record(name_len):
    name = "X" * name_len
    record = _record(name)
    payload = _payload_from_record(record)
    data = _container(_tnel(name, payload, b"\x00" * 6))

    fd, path = tempfile.mkstemp(suffix=".lib")
    os.write(fd, data)
    os.close(fd)
    try:
        result = lib.read_lib(path)
    finally:
        os.unlink(path)
    assert len(result) == 1
    assert result[0] == record
    assert len(result[0]) == 204


def test_two_entries():
    names = ["A" * 9, "B" * 12]
    records = [_record(n) for n in names]
    chunks = b"".join(
        _tnel(n, _payload_from_record(r), b"\x00" * 9 if i else b"\x00" * 6)
        for i, (n, r) in enumerate(zip(names, records))
    )

    fd, path = tempfile.mkstemp(suffix=".lib")
    os.write(fd, _container(chunks))
    os.close(fd)
    try:
        result = lib.read_lib(path)
    finally:
        os.unlink(path)
    assert result == records


def test_empty_container_returns_empty():
    data = _container(b"")

    fd, path = tempfile.mkstemp(suffix=".lib")
    os.write(fd, data)
    os.close(fd)
    try:
        assert lib.read_lib(path) == []
    finally:
        os.unlink(path)


def test_leading_spaces_preserved():
    name = "  Hi there  "  # 12? ensure length 12
    name = name.ljust(12)[:12]
    record = _record(name)
    payload = _payload_from_record(record)
    data = _container(_tnel(name, payload))

    fd, path = tempfile.mkstemp(suffix=".lib")
    os.write(fd, data)
    os.close(fd)
    try:
        result = lib.read_lib(path)
    finally:
        os.unlink(path)
    assert result[0] == record
    assert m.sound_name(result[0]) == name.ljust(16)


def test_is_lib_path():
    assert lib.is_lib_path("foo.LIB")
    assert lib.is_lib_path("foo.lib")
    assert not lib.is_lib_path("foo.syx")


def _write(data, tmp_path, name="t.lib"):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_bad_top_level_id(tmp_path):
    data = b"XROF" + b"\x00" * 8 + b"BLSS"
    with pytest.raises(ValueError):
        lib.read_lib(_write(data, tmp_path))


def test_bad_form_type(tmp_path):
    data = _container(b"")
    # corrupt form type
    data = b"MROF" + data[4:8] + b"XXXX" + data[12:]
    with pytest.raises(ValueError):
        lib.read_lib(_write(data, tmp_path))


def test_chunk_overflow(tmp_path):
    # TNEL chunk size claims more than available
    body = b"\x00" * 10
    chunk = b"TNEL" + struct.pack("<I", 1000) + body
    with pytest.raises(ValueError):
        lib.read_lib(_write(_container(chunk), tmp_path))


def test_name_len_out_of_range(tmp_path):
    name = "A" * 9
    record = _record(name)
    payload = _payload_from_record(record)
    # name_len 8
    bad_body = (
        b"\x00\xff\x00\x00"
        + b"\x00" * 8
        + b"\x01"
        + bytes([8])
        + (b"A" * 8)
        + b"\x03\x81\x41\x53\x51\x00"
        + payload
    )
    chunk = b"TNEL" + struct.pack("<I", len(bad_body)) + bad_body
    with pytest.raises(ValueError):
        lib.read_lib(_write(_container(chunk), tmp_path))


def test_non_printable_name(tmp_path):
    name = "A" * 9
    record = _record(name)
    payload = _payload_from_record(record)
    bad_name = b"A" * 8 + b"\x01"
    bad_body = (
        b"\x00\xff\x00\x00"
        + b"\x00" * 8
        + b"\x01"
        + bytes([9])
        + bad_name
        + b"\x03\x81\x41\x53\x51\x00"
        + payload
    )
    chunk = b"TNEL" + struct.pack("<I", len(bad_body)) + bad_body
    with pytest.raises(ValueError):
        lib.read_lib(_write(_container(chunk), tmp_path))


def test_separator_mismatch(tmp_path):
    name = "A" * 9
    record = _record(name)
    payload = _payload_from_record(record)
    bad_body = (
        b"\x00\xff\x00\x00"
        + b"\x00" * 8
        + b"\x01"
        + bytes([9])
        + name.encode()
        + b"\x00" * 6
        + payload
    )
    chunk = b"TNEL" + struct.pack("<I", len(bad_body)) + bad_body
    with pytest.raises(ValueError):
        lib.read_lib(_write(_container(chunk), tmp_path))


def test_payload_too_short(tmp_path):
    name = "A" * 9
    bad_body = (
        b"\x00\xff\x00\x00"
        + b"\x00" * 8
        + b"\x01"
        + bytes([9])
        + name.encode()
        + b"\x03\x81\x41\x53\x51\x00"
        + b"\x00" * 10
    )
    chunk = b"TNEL" + struct.pack("<I", len(bad_body)) + bad_body
    with pytest.raises(ValueError):
        lib.read_lib(_write(_container(chunk), tmp_path))


def test_truncated_header(tmp_path):
    data = b"MROF" + struct.pack("<I", 4) + b"BLSS"  # no chunks
    # remove last byte to truncate size? Instead make size too big
    data = b"MROF" + struct.pack("<I", 100) + b"BLSS"
    with pytest.raises(ValueError):
        lib.read_lib(_write(data, tmp_path))
