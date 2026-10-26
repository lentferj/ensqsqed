# SPDX-License-Identifier: GPL-2.0-or-later
#
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# ensqsqed is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 2 of the License, or (at your option)
# any later version.
#
# ensqsqed is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
# more details.

"""``sqcli read`` of a SoundQuest bank, and the restore refusal.

The banks are synthetic single-sound blocks wrapped in the IFF container
``sq.sqs`` reads; no captured data is used.  ``read`` is an offline
command, so connecting at all would be the failure: the fake ``_connect``
raises if it is called.
"""

from __future__ import annotations

import struct
from types import SimpleNamespace
from typing import Iterable, Optional

import pytest

from ensqsqed import cli
from sq import messages as m


def _record(name: str) -> bytes:
    record = bytearray(m.SOUND_BYTES)
    m.encode_name(record, name)
    return bytes(record)


def _single_block(record: Optional[bytes]) -> bytes:
    if record is None:
        return bytes(415)
    body = m.frame(m.Function.SINGLE_SOUND_DUMP, m.to_nibbles(record), device=3)
    assert len(body) == 415
    return body


def _bank(*records: Optional[bytes], slots: int = 20) -> bytes:
    body = bytearray(415 * slots)
    for index, record in enumerate(records):
        body[index * 415 : (index + 1) * 415] = _single_block(record)
    return bytes(body)


def _build_sqs(bodies: Iterable[bytes]) -> bytes:
    forms = b""
    for body in bodies:
        chunk = b"BODY" + struct.pack(">I", len(body)) + body
        if len(body) & 1:
            chunk += b"\x00"
        form_content = b"BANK" + chunk
        if len(form_content) & 1:
            form_content += b"\x00"
        forms += b"FORM" + struct.pack(">I", len(form_content)) + form_content
    list_content = b"    " + forms
    if len(list_content) & 1:
        list_content += b"\x00"
    return b"LIST" + struct.pack(">I", len(list_content)) + list_content


def _write_sqs(tmp_path, bodies: Iterable[bytes], name: str = "bank.sqs"):
    path = tmp_path / name
    path.write_bytes(_build_sqs(bodies))
    return path


def _no_connect(*_args, **_kwargs):
    raise AssertionError("read must not open a MIDI port")


def test_read_sqs_prints_the_names_table_without_connecting(
    tmp_path, monkeypatch, capsys
):
    path = _write_sqs(tmp_path, [_bank(_record("Zz001"), _record("Zz002"))])
    monkeypatch.setattr(cli, "_connect", _no_connect)

    assert cli.main(["read", str(path)]) == 0

    out = capsys.readouterr().out
    assert "Zz001" in out
    assert "Zz002" in out
    assert "shown" in out
    assert "PC" in out
    assert "name" in out
    assert str(path) in out


def test_read_sqs_reports_duplicates_once(tmp_path, monkeypatch, capsys):
    repeated = _record("Zz003")
    path = _write_sqs(
        tmp_path,
        [_bank(repeated, _record("Zz004")), _bank(None, repeated)],
    )
    monkeypatch.setattr(cli, "_connect", _no_connect)

    assert cli.main(["read", str(path)]) == 0

    out = capsys.readouterr().out
    assert out.count("Zz003") == 1
    assert "Zz004" in out


def test_read_malformed_sqs_exits_with_an_error(tmp_path, monkeypatch, capsys):
    path = tmp_path / "broken.sqs"
    path.write_bytes(b"")
    monkeypatch.setattr(cli, "_connect", _no_connect)

    with pytest.raises(SystemExit) as raised:
        cli.main(["read", str(path)])

    assert str(raised.value).startswith("error:")
    assert ".sqs" in str(raised.value)


def test_restore_refuses_a_sqs_file_before_reading_it(tmp_path, monkeypatch):
    path = _write_sqs(tmp_path, [_bank(_record("Zz005"))])
    fake = SimpleNamespace(channel=0, close=lambda: None)
    monkeypatch.setattr(cli, "_connect", lambda *_a, **_k: fake)

    with pytest.raises(SystemExit) as raised:
        cli.main(["restore", "--yes", str(path)])

    said = str(raised.value)
    assert said.startswith("error:")
    assert ".sqs" in said
    assert "not supported" in said
    assert "sqcli read" in said
