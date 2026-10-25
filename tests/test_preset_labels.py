# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# ensqsqed is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 2 of the License, or (at your option) any later
# version.

"""Tests for sq.preset_labels."""

import json

import pytest

from sq import preset_labels
from sq.preset_labels import MAX_LABEL, NAME, default_path, load, save, set_label


def test_load_missing(tmp_path):
    p = tmp_path / "nope.json"
    assert load(str(p)) == {}


def test_roundtrip(tmp_path):
    p = tmp_path / "labels.json"
    save({0: "My label"}, str(p))
    assert load(str(p)) == {0: "My label"}


def test_raw_string_keys(tmp_path):
    p = tmp_path / "labels.json"
    save({0: "My label", 17: "Bass"}, str(p))
    text = p.read_text()
    data = json.loads(text)
    assert isinstance(data, dict)
    assert "0" in data and "17" in data
    assert all(isinstance(k, str) for k in data)


def test_load_ignores_malformed(tmp_path):
    p = tmp_path / "labels.json"
    p.write_text("not json")
    assert load(str(p)) == {}

    p.write_text("[1, 2, 3]")
    assert load(str(p)) == {}

    longname = "A" * (MAX_LABEL + 5)
    p.write_text(
        json.dumps(
            {
                "80": "x",
                "-1": "y",
                "x": "z",
                "0": "   ",
                "1": "  hi  ",
                "2": longname,
            }
        )
    )
    res = load(str(p))
    assert 80 not in res
    assert -1 not in res
    assert 0 not in res
    assert res[1] == "hi"
    assert res[2] == "A" * MAX_LABEL
    assert all(isinstance(k, int) for k in res)


def test_set_label(tmp_path):
    original = {0: "old"}
    new = set_label(original, 0, "new")
    assert original == {0: "old"}
    assert new == {0: "new"}

    assert set_label({0: "a"}, 0, "b") == {0: "b"}
    assert set_label({0: "a"}, 0, "   ") == {}
    assert set_label({0: "a"}, 0, "") == {}

    long = "B" * (MAX_LABEL + 3)
    assert set_label({}, 1, long) == {1: "B" * MAX_LABEL}

    with pytest.raises(ValueError):
        set_label({}, -1, "x")
    with pytest.raises(ValueError):
        set_label({}, 80, "x")


def test_default_path():
    assert default_path().endswith(NAME)
    assert "ensqsqed" in default_path()


def test_save_parent_uncreatable(tmp_path):
    a_file = tmp_path / "a-file"
    a_file.write_text("x")
    p = a_file / "x.json"
    save({0: "hi"}, str(p))
