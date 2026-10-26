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

"""The import path for SoundQuest ``.sqs`` files, built synthetically.

Every file here is invented: the banks are single-sound blocks carrying
names this repository made up, wrapped in the IFF container ``sq.sqs``
reads.  Nothing is captured, and no bank is opened beyond the temporary
directory.
"""

from __future__ import annotations

import struct
from typing import Iterable, Optional

import pytest

from sq import banks, importer
from sq import messages as m


def _record(name: str) -> bytes:
    """An invented 204-byte sound record, with its name in the real place."""
    record = bytearray(m.SOUND_BYTES)
    m.encode_name(record, name)
    return bytes(record)


def _single_block(record: Optional[bytes]) -> bytes:
    """One 415-byte single-sound frame, or a zero block for an empty slot."""
    if record is None:
        return bytes(415)
    body = m.frame(m.Function.SINGLE_SOUND_DUMP, m.to_nibbles(record), device=3)
    assert len(body) == 415
    return body


def _bank(*records: Optional[bytes], slots: int = 20) -> bytes:
    """A 415-byte-block BODY, with ``records`` written from slot 0."""
    body = bytearray(415 * slots)
    for index, record in enumerate(records):
        body[index * 415 : (index + 1) * 415] = _single_block(record)
    return bytes(body)


def _build_sqs(bodies: Iterable[bytes]) -> bytes:
    """The IFF LIST/FORM container the decoder expects, in file order."""
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


def _write_sqs(tmp_path, bodies: Iterable[bytes]):
    path = tmp_path / "favourites.sqs"
    path.write_bytes(_build_sqs(bodies))
    return path


def _all_sounds_message() -> bytes:
    """A valid .syx the dispatch test can hand to the ordinary reader."""
    payload = b"".join(
        m.to_nibbles(_record(f"Zz{index:03d}"))
        for index in range(banks.SOUNDS_PER_GROUP)
    )
    return m.frame(m.Function.ALL_SOUNDS_DUMP, payload)


class TestDispatch:
    def test_a_syx_file_never_touches_the_sqs_decoder(self, tmp_path, monkeypatch):
        path = tmp_path / "invented.syx"
        path.write_bytes(_all_sounds_message())

        def refuse(_path: str):
            raise AssertionError("the .sqs decoder was called for a .syx file")

        monkeypatch.setattr(importer.sqs, "read_sqs", refuse)

        source = importer.read_import(str(path))

        assert source.kinds == ("all-sounds",)
        assert len(source.sounds) == banks.SOUNDS_PER_GROUP

    def test_a_sqs_file_is_read_by_the_sqs_decoder(self, tmp_path, monkeypatch):
        path = _write_sqs(tmp_path, [_bank(_record("Zz001"))])
        calls = []

        def called(name: str):
            calls.append(name)
            raise ValueError("the branch reached the decoder")

        monkeypatch.setattr(importer.sqs, "read_sqs", called)

        with pytest.raises(ValueError, match="branch reached"):
            importer.read_import(str(path))
        assert calls == [str(path)]


class TestFlattening:
    def test_every_slot_of_every_bank_is_collected_in_file_order(self, tmp_path):
        first = _bank(None, _record("Zz001"), None, _record("Zz002"))
        second = _bank(_record("Zz003"), None, _record("Zz004"))
        path = _write_sqs(tmp_path, [first, second])

        source = importer.read_import(str(path))

        assert source.kinds == ("sqs",)
        assert source.device is None
        assert [sound.index for sound in source.sounds] == [0, 1, 2, 3]
        assert [sound.name for sound in source.sounds] == [
            "Zz001",
            "Zz002",
            "Zz003",
            "Zz004",
        ]
        assert [sound.raw for sound in source.sounds] == [
            _record("Zz001"),
            _record("Zz002"),
            _record("Zz003"),
            _record("Zz004"),
        ]

    def test_an_empty_bank_contributes_nothing(self, tmp_path):
        path = _write_sqs(
            tmp_path,
            [_bank(None, None), _bank(_record("Zz010"))],
        )

        source = importer.read_import(str(path))

        assert [sound.name for sound in source.sounds] == ["Zz010"]

    def test_the_name_is_read_from_the_real_record_and_stripped(self, tmp_path):
        path = _write_sqs(tmp_path, [_bank(_record("Zz020"))])

        source = importer.read_import(str(path))

        assert source.sounds[0].name == "Zz020"
        assert len(source.sounds[0].raw) == m.SOUND_BYTES


class TestDeduplication:
    def test_an_identical_favourite_in_two_banks_is_one_row(self, tmp_path):
        repeated = _record("Zz030")
        path = _write_sqs(
            tmp_path,
            [
                _bank(repeated, _record("Zz031")),
                _bank(None, repeated),
            ],
        )

        source = importer.read_import(str(path))

        assert len(source.sounds) == 2
        assert [sound.name for sound in source.sounds] == ["Zz030", "Zz031"]
        assert [sound.index for sound in source.sounds] == [0, 1]

    def test_an_identical_record_twice_in_one_bank_is_one_row(self, tmp_path):
        repeated = _record("Zz040")
        path = _write_sqs(tmp_path, [_bank(repeated, None, repeated)])

        source = importer.read_import(str(path))

        assert len(source.sounds) == 1
        assert source.sounds[0].raw == repeated

    def test_two_records_that_differ_by_one_byte_are_both_kept(self, tmp_path):
        one = _record("Zz050")
        other = bytearray(one)
        other[-1] = 0x01
        path = _write_sqs(tmp_path, [_bank(one, bytes(other))])

        source = importer.read_import(str(path))

        assert len(source.sounds) == 2


class TestNoSounds:
    def test_an_empty_file_is_a_value_error(self, tmp_path):
        path = tmp_path / "empty.sqs"
        path.write_bytes(b"")

        with pytest.raises(ValueError):
            importer.read_import(str(path))

    def test_a_valid_container_with_nothing_decodable_is_refused(self, tmp_path):
        # The 32648-byte SoundQuest-native body: a valid .sqs file whose
        # records are not the instrument's SysEx layout.
        path = _write_sqs(tmp_path, [bytes(32648)])

        with pytest.raises(ValueError, match="holds no sounds"):
            importer.read_import(str(path))
