# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# ensqsqed is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 2 of the License, or (at your option)
# any later version.

"""Synthetic tests for the SoundQuest .sqs decoder."""

from __future__ import annotations

import os
import struct

import pytest

from sq import messages as m
from sq import sqs


def _make_record(name: str) -> bytes:
    data = bytearray(204)
    for i, ch in enumerate(name):
        if i < 204:
            data[i] = ord(ch)
    return bytes(data)


def _single_block(record: bytes | None) -> bytes:
    if record is None:
        return bytes(415)
    body = m.frame(m.Function.SINGLE_SOUND_DUMP, m.to_nibbles(record), device=3)
    assert len(body) == 415
    return body


def build_sqs(bodies) -> bytes:
    forms = b""
    for body in bodies:
        chunk = b"BODY" + struct.pack(">I", len(body)) + body
        if len(body) & 1:
            chunk += b"\x00"
        form_content = b"BANK" + chunk
        if len(form_content) & 1:
            form_content += b"\x00"
        form = b"FORM" + struct.pack(">I", len(form_content)) + form_content
        forms += form
    list_content = b"    " + forms
    if len(list_content) & 1:
        list_content += b"\x00"
    return b"LIST" + struct.pack(">I", len(list_content)) + list_content


def _write(path, data):
    with open(path, "wb") as f:
        f.write(data)


def test_is_sqs_path():
    assert sqs.is_sqs_path("foo.SQS")
    assert sqs.is_sqs_path("foo.sqs")
    assert not sqs.is_sqs_path("foo.mid")


def test_single_sound_33200(tmp_path):
    record = _make_record("Inv000")
    body = bytearray(33200)
    block = _single_block(record)
    body[2 * 415 : 3 * 415] = block
    body = bytes(body)
    data = build_sqs([body])
    path = str(tmp_path / "test_33200.sqs")
    _write(path, data)
    banks = sqs.read_sqs(path)
    assert len(banks) == 1
    b0 = banks[0]
    assert len(b0.slots) == 80
    assert b0.slots[2] == record
    assert all(b0.slots[i] is None for i in range(80) if i != 2)
    os.remove(path)


def test_single_sound_8300(tmp_path):
    record = _make_record("Inv001")
    body = bytearray(8300)
    block = _single_block(record)
    body[1 * 415 : 2 * 415] = block
    body = bytes(body)
    data = build_sqs([body])
    path = str(tmp_path / "test_8300.sqs")
    _write(path, data)
    banks = sqs.read_sqs(path)
    assert len(banks) == 1
    b0 = banks[0]
    assert b0.slots[1] == record
    assert b0.slots[0] is None
    assert b0.slots[20] is None
    os.remove(path)


def test_single_sound_416(tmp_path):
    record = _make_record("Inv002")
    block = _single_block(record)
    body = block + b"\x00"
    assert len(body) == 416
    data = build_sqs([body])
    path = str(tmp_path / "test_416.sqs")
    _write(path, data)
    banks = sqs.read_sqs(path)
    assert len(banks) == 1
    b0 = banks[0]
    assert b0.slots[0] == record
    os.remove(path)


def test_all_sounds_32648(tmp_path):
    records = [None] * 80
    records[5] = _make_record("Inv003")
    payload = bytearray()
    for r in records:
        if r is None:
            payload += bytes(408)
        else:
            payload += m.to_nibbles(r)
    frame_bytes = m.frame(m.Function.ALL_SOUNDS_DUMP, payload, device=1)
    assert len(frame_bytes) == 32647
    body = frame_bytes + b"\x00"
    assert len(body) == 32648
    data = build_sqs([body])
    path = str(tmp_path / "test_all.sqs")
    _write(path, data)
    banks = sqs.read_sqs(path)
    assert len(banks) == 1
    b0 = banks[0]
    assert b0.slots[5] == records[5]
    assert b0.slots[0] is None
    os.remove(path)


def test_native_32648_ignored(tmp_path):
    body = bytes(32648)
    data = build_sqs([body])
    path = str(tmp_path / "test_native.sqs")
    _write(path, data)
    banks = sqs.read_sqs(path)
    assert banks == []
    os.remove(path)


def test_multiple_banks_order(tmp_path):
    r1 = _make_record("Inv010")
    r2 = _make_record("Inv011")
    b1 = bytearray(33200)
    b1[0:415] = _single_block(r1)
    b2 = bytearray(8300)
    b2[0:415] = _single_block(r2)
    data = build_sqs([bytes(b1), bytes(b2)])
    path = str(tmp_path / "test_multi.sqs")
    _write(path, data)
    banks = sqs.read_sqs(path)
    assert len(banks) == 2
    assert banks[0].slots[0] == r1
    assert banks[1].slots[0] == r2
    os.remove(path)


def test_gaps_between_forms_tolerated(tmp_path):
    r = _make_record("Inv020")
    body = bytearray(416)
    body[:415] = _single_block(r)
    body = bytes(body)
    chunk = b"BODY" + struct.pack(">I", len(body)) + body
    if len(body) & 1:
        chunk += b"\x00"
    form_content = b"BANK" + chunk
    if len(form_content) & 1:
        form_content += b"\x00"
    form = b"FORM" + struct.pack(">I", len(form_content)) + form_content
    list_content = b"    " + form + b"\x00" * 16 + form
    if len(list_content) & 1:
        list_content += b"\x00"
    data = b"LIST" + struct.pack(">I", len(list_content)) + list_content
    path = str(tmp_path / "test_gap.sqs")
    _write(path, data)
    banks = sqs.read_sqs(path)
    assert len(banks) == 2
    os.remove(path)


def test_malformed_empty(tmp_path):
    path = str(tmp_path / "test_empty.sqs")
    _write(path, b"")
    with pytest.raises(ValueError):
        sqs.read_sqs(path)
    os.remove(path)


def test_malformed_no_form(tmp_path):
    path = str(tmp_path / "test_noform.sqs")
    data = b"LIST" + struct.pack(">I", 4) + b"    "
    _write(path, data)
    with pytest.raises(ValueError):
        sqs.read_sqs(path)
    os.remove(path)


def test_malformed_truncated_body(tmp_path):
    body = b"\x00" * 10
    chunk = b"BODY" + struct.pack(">I", 416) + body
    form_content = b"BANK" + chunk
    if len(form_content) & 1:
        form_content += b"\x00"
    form = b"FORM" + struct.pack(">I", len(form_content)) + form_content
    list_content = b"    " + form
    if len(list_content) & 1:
        list_content += b"\x00"
    data = b"LIST" + struct.pack(">I", len(list_content)) + list_content
    path = str(tmp_path / "test_trunc.sqs")
    _write(path, data)
    with pytest.raises(ValueError):
        sqs.read_sqs(path)
    os.remove(path)


def test_malformed_bad_single_frame(tmp_path):
    body = bytearray(415)
    body[0] = 0xF0
    body[10] = 0x01
    body = bytes(body)
    data_body = body + b"\x00"
    data = build_sqs([data_body])
    path = str(tmp_path / "test_badframe.sqs")
    _write(path, data)
    with pytest.raises(ValueError):
        sqs.read_sqs(path)
    os.remove(path)


def test_native_32648_non_f0_ignored(tmp_path):
    body = bytes([0x00] * 8 + [0x01]) + bytes(32648 - 9)
    assert body[:3] != b"\xf0\x0f\x06"
    data = build_sqs([body])
    path = str(tmp_path / "test_native2.sqs")
    _write(path, data)
    banks = sqs.read_sqs(path)
    assert banks == []
    os.remove(path)
