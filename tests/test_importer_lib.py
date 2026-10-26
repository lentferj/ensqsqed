# SPDX-License-Identifier: GPL-2.0-or-later
#
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# ensqsqed is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version.
#
# ensqsqed is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
# more details.

"""The import path for SoundDiver ``.LIB`` libraries, built synthetically.

Every file here is invented: 204-byte records with made-up names, wrapped
in the chunked container ``sq.lib`` reads.  Nothing is captured, and no
library is opened beyond the temporary directory.
"""

from __future__ import annotations

import struct
from typing import Iterable

import pytest

from sq import banks, importer
from sq import messages as m


def _record(name: str) -> bytes:
    """An invented 204-byte record carrying ``name``."""
    record = bytearray(m.SOUND_BYTES)
    for index in range(m.SOUND_BYTES):
        record[index] = (index * 37 + 11) & 0xFF
    m.encode_name(record, name)
    return bytes(record)


def _tnel(name: str, record: bytes) -> bytes:
    """One TNEL chunk reconstructing ``record``."""
    payload = record[:177] + record[191:204]
    body = (
        b"\x00\xff\x00\x00"
        + b"\x01\x02\x03\x04"
        + b"\x05\x06\x07\x08"
        + b"\x01"
        + bytes([len(name)])
        + name.encode("ascii")
        + b"\x03\x81\x41\x53\x51\x00"
        + payload
        + b"\x00" * 6
    )
    return b"TNEL" + struct.pack("<I", len(body)) + body


def _container(chunks: bytes) -> bytes:
    content = b"BLSS" + chunks
    return b"MROF" + struct.pack("<I", len(content)) + content


def _lib_name(record: bytes) -> str:
    """The TNEL name for ``record``: its own, within the decoder's 9-16."""
    base = m.sound_name(record).rstrip()[:16]
    return base.ljust(max(9, len(base)))


def _write_lib(tmp_path, records: Iterable[bytes], name: str = "library.lib"):
    path = tmp_path / name
    path.write_bytes(
        _container(b"".join(_tnel(_lib_name(record), record) for record in records))
    )
    return path


def _all_sounds_message() -> bytes:
    """A valid .syx the dispatch test can hand to the ordinary reader."""
    payload = b"".join(
        m.to_nibbles(_record(f"Zz{index:03d}"))
        for index in range(banks.SOUNDS_PER_GROUP)
    )
    return m.frame(m.Function.ALL_SOUNDS_DUMP, payload)


class TestDispatch:
    def test_a_lib_file_is_read_by_the_lib_decoder(self, tmp_path, monkeypatch):
        path = _write_lib(tmp_path, [_record("Zz001")])
        calls = []

        def called(name: str):
            calls.append(name)
            raise ValueError("the branch reached the decoder")

        monkeypatch.setattr(importer.lib, "read_lib", called)

        with pytest.raises(ValueError, match="branch reached"):
            importer.read_import(str(path))
        assert calls == [str(path)]

    def test_a_syx_file_never_touches_the_lib_decoder(self, tmp_path, monkeypatch):
        path = tmp_path / "invented.syx"
        path.write_bytes(_all_sounds_message())

        def refuse(_path: str):
            raise AssertionError("the .lib decoder was called for a .syx file")

        monkeypatch.setattr(importer.lib, "read_lib", refuse)

        source = importer.read_import(str(path))

        assert source.kinds == ("all-sounds",)
        assert len(source.sounds) == banks.SOUNDS_PER_GROUP


class TestFlattening:
    def test_every_record_is_collected_in_file_order(self, tmp_path):
        path = _write_lib(
            tmp_path,
            [_record("Zz001"), _record("Zz002"), _record("Zz003")],
        )

        source = importer.read_import(str(path))

        assert source.kinds == ("sounddiver",)
        assert source.device is None
        assert [sound.index for sound in source.sounds] == [0, 1, 2]
        assert [sound.name for sound in source.sounds] == ["Zz001", "Zz002", "Zz003"]
        assert [sound.raw for sound in source.sounds] == [
            _record("Zz001"),
            _record("Zz002"),
            _record("Zz003"),
        ]

    def test_the_name_is_stripped_of_the_record_padding(self, tmp_path):
        path = _write_lib(tmp_path, [_record("Zz020")])

        source = importer.read_import(str(path))

        assert source.sounds[0].name == "Zz020"
        assert len(source.sounds[0].raw) == m.SOUND_BYTES


class TestDeduplication:
    def test_an_identical_record_twice_is_one_row(self, tmp_path):
        repeated = _record("Zz030")
        path = _write_lib(tmp_path, [repeated, _record("Zz031"), repeated])

        source = importer.read_import(str(path))

        assert len(source.sounds) == 2
        assert [sound.name for sound in source.sounds] == ["Zz030", "Zz031"]
        assert [sound.index for sound in source.sounds] == [0, 1]

    def test_two_records_that_differ_by_one_byte_are_both_kept(self, tmp_path):
        one = _record("Zz040")
        other = bytearray(one)
        other[-1] = 0x01
        path = _write_lib(tmp_path, [one, bytes(other)])

        source = importer.read_import(str(path))

        assert len(source.sounds) == 2


class TestNoSounds:
    def test_an_empty_container_is_a_value_error(self, tmp_path):
        path = tmp_path / "empty.lib"
        path.write_bytes(_container(b""))

        with pytest.raises(ValueError, match="holds no sounds"):
            importer.read_import(str(path))

    def test_a_malformed_file_is_a_value_error(self, tmp_path):
        path = tmp_path / "broken.lib"
        path.write_bytes(b"MROF" + struct.pack("<I", 100) + b"BLSS")

        with pytest.raises(ValueError):
            importer.read_import(str(path))
