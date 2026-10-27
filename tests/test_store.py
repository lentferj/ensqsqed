# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# The wire protocol is transcribed as data from E-mu's own MIDI
# Specification, printed in full as pages 225-250 of the Morpheus
# Operation Manual (E-mu Systems, 1993), and checked against 2176 preset
# dumps the instrument produced. The transport layer is ported from the
# sibling p2ked, x5ded, kwsed, rxved and nanosyned projects.
#
# ensqsqed is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 2 of the License, or (at your option)

"""The favourites store, the config and the catalog. All invented data.

These three modules are ported from the sibling projects and carry this
family's most subtle behaviour -- the upsert that preserves ``added``,
the soft delete, the LIKE escaping, the TOML quoting. None of it was
pinned here until an outside review pointed out that the largest module
in the project had no test at all.
"""

import sqlite3

import pytest

from sq import catalog as cat
from sq import config
from sq.favorites import Favorites


@pytest.fixture
def store(tmp_path):
    with Favorites(str(tmp_path / "f.db")) as db:
        yield db


class TestTheSlotIsTheIdentity:
    def test_a_favourite_survives_a_rename(self, store):
        store.add("INT", 7, name="Invented One")
        store.add("INT", 7, name="Renamed Entirely")
        assert len(store) == 1
        assert store.get("INT", 7).name == "Renamed Entirely"

    def test_the_added_date_is_preserved_across_an_edit(self, store):
        first = store.add("INT", 7, name="Invented")
        again = store.add("INT", 7, name="Invented", rating=4)
        assert again.added == first.added, (
            "re-adding is an edit, not a re-acquisition; the date is the "
            "one ordering the user cannot reconstruct"
        )

    def test_groups_are_separate_spaces(self, store):
        """INT 5 and ROM 5 are different slots with the same number."""
        store.add("INT", 5, name="In Internal")
        store.add("ROM", 5, name="In ROM")
        assert len(store) == 2
        assert store.get("ROM", 5).name == "In ROM"


class TestUnfavouritingKeepsWhatTheUserTyped:
    """`f` in a browser unfavourites without asking, so a rating, tags, a
    note and the original date must survive it."""

    def test_a_toggle_cycle_keeps_everything(self, store):
        store.add("INT", 5, name="Invented", rating=4, tags="a, b", note="hello")
        before = store.get("INT", 5)

        assert store.toggle("INT", 5) is False
        assert store.get("INT", 5) is None
        assert store.toggle("INT", 5) is True

        after = store.get("INT", 5)
        assert (after.rating, after.tags, after.note) == (4, "a, b", "hello")
        assert after.added == before.added

    def test_an_unfavourited_slot_is_not_a_favourite_by_any_route(self, store):
        store.add("INT", 5, name="Invented", rating=3)
        store.remove("INT", 5)
        assert len(store) == 0
        assert store.keys() == set()
        assert store.all() == []
        assert store.search("Invented") == []
        assert "INT:005" not in store

    def test_what_is_still_held_can_be_seen(self, store):
        store.add("INT", 5, name="Invented", rating=3)
        store.remove("INT", 5)
        assert [f.rating for f in store.dormant()] == [3]

    def test_forget_really_deletes(self, store):
        store.add("INT", 5, name="Invented", rating=3)
        store.remove("INT", 5)
        assert store.forget("INT", 5) is True
        assert store.dormant() == []

    def test_a_v1_database_upgrades_without_losing_rows(self, tmp_path):
        path = str(tmp_path / "old.db")
        db = sqlite3.connect(path)
        db.executescript(
            """
            CREATE TABLE favorites (
                bank_id TEXT NOT NULL, number INTEGER NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                rating INTEGER NOT NULL DEFAULT 0,
                tags TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                added REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (bank_id, number));
            """
        )
        db.execute(
            "INSERT INTO favorites (bank_id, number, name, rating) "
            "VALUES ('INT', 7, 'Invented', 5)"
        )
        db.execute("PRAGMA user_version = 1")
        db.commit()
        db.close()

        with Favorites(path) as store:
            kept = store.get("INT", 7)
            assert kept is not None, "an existing row must stay a favourite"
            assert kept.rating == 5


class TestAnnotationsSurviveEachOther:
    def test_setting_a_rating_keeps_the_tags_and_note(self, store):
        store.add("INT", 1, name="Invented", tags="pad", note="keep me")
        store.set_rating("INT", 1, 5)
        row = store.get("INT", 1)
        assert (row.rating, row.tags, row.note) == (5, "pad", "keep me")

    def test_a_rating_has_a_range(self, store):
        store.add("INT", 1)
        with pytest.raises(ValueError, match="0-5"):
            store.set_rating("INT", 1, 6)


class TestSearchWildcardsAreLiteral:
    """% and _ are LIKE wildcards, and a search for one must not match
    everything."""

    def test_a_percent_matches_only_a_percent(self, store):
        store.add("INT", 1, name="Invented One")
        store.add("INT", 2, name="Ten % Off")
        assert [f.number for f in store.search("%")] == [2]

    def test_an_underscore_matches_only_an_underscore(self, store):
        store.add("INT", 1, name="Invented One")
        store.add("INT", 2, name="under_score")
        assert [f.number for f in store.search("_")] == [2]


class TestNamesCanBeRefreshed:
    def test_it_relabels_and_counts_what_changed(self, store):
        store.add("INT", 0, name="")
        store.add("INT", 1, name="Already Right")
        changed = store.refresh_names(
            lambda bank, number: "Already Right" if number else "From Unit"
        )
        assert changed == 1
        assert store.get("INT", 0).name == "From Unit"


class TestTheConfigIsWrittenSafely:
    """A quote in a port name once made the file unparsable for good."""

    def test_a_quoted_port_name_round_trips(self, tmp_path):
        path = str(tmp_path / "config.toml")
        nasty = 'Odd "quoted"\tport\\with slash'
        config.save_last_port(nasty, path)
        assert config.load_last_port(path) == nasty

    def test_the_cache_heals(self, tmp_path):
        path = str(tmp_path / "config.toml")
        config.save_last_port('has a " in it', path)
        config.save_last_port("a sane port", path)
        assert config.load_last_port(path) == "a sane port"

    def test_the_device_id_round_trips(self, tmp_path):
        path = str(tmp_path / "config.toml")
        config.save_device_id(12, path)
        assert config.load_device_id(path) == 12

    def test_a_device_id_of_true_is_not_device_one(self, tmp_path):
        """isinstance(True, int) is True, and device 1 is a real device."""
        path = str(tmp_path / "config.toml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("device_id = true\n")
        assert config.load_device_id(path) is None

    def test_the_recv_port_round_trips(self, tmp_path):
        """Wired into the CLI only after a review pointed out it was
        exported and never called."""
        path = str(tmp_path / "config.toml")
        config.save_last_recv_port("some input port", path)
        assert config.load_last_recv_port(path) == "some input port"

    def test_the_import_folder_round_trips(self, tmp_path):
        """The picker remembers where the last import file came from."""
        path = str(tmp_path / "config.toml")
        config.save_ui_state(path=path, import_folder="/music/syx")
        assert config.load_ui_state(path)["import_folder"] == "/music/syx"

    def test_single_import_options_round_trip(self, tmp_path):
        path = str(tmp_path / "config.toml")
        config.save_ui_state(
            path=path,
            import_store_permanently=True,
            import_autoload_preview=False,
            import_autoplay_preview=True,
        )
        state = config.load_ui_state(path)
        assert state["import_store_permanently"] is True
        assert state["import_autoload_preview"] is False
        assert state["import_autoplay_preview"] is True

    def test_single_import_options_must_be_booleans(self, tmp_path):
        """A hand-edited 1 is not a checkbox the user ticked."""
        path = str(tmp_path / "config.toml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(
                "import_store_permanently = 1\n"
                "import_autoload_preview = true\n"
                'import_autoplay_preview = "yes"\n'
            )
        state = config.load_ui_state(path)
        assert "import_store_permanently" not in state
        assert state["import_autoload_preview"] is True
        assert "import_autoplay_preview" not in state

    def test_an_import_folder_that_is_not_a_string_is_ignored(self, tmp_path):
        """`import_folder = true` is a hand edit, not a directory."""
        path = str(tmp_path / "config.toml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("import_folder = true\n")
        assert "import_folder" not in config.load_ui_state(path)


class TestTheCatalogKeysOnGroupNumbers:
    def test_a_name_is_found_by_region_and_number(self):
        c = cat.Catalog([cat.Entry("ROM", 5, "Invented")])
        assert c.name("ROM", 5) == "Invented"
        assert c.name("INT", 5) is None, "groups are separate spaces"

    def test_a_live_name_wins_and_the_difference_is_visible(self):
        c = cat.Catalog([cat.Entry("INT", 5, "Printed")])
        c.set_live_name("INT", 5, "Saved Over")
        assert c.display_name("INT", 5) == "Saved Over"
        assert c.differs("INT", 5)

    def test_search_carries_the_disagreement(self):
        c = cat.Catalog([cat.Entry("INT", 5, "Printed")])
        c.set_live_name("INT", 5, "Saved Over")
        assert c.search("saved") == [("INT", 5, "Saved Over", True)]

    def test_one_bad_row_does_not_cost_the_others(self, tmp_path):
        import json

        path = str(tmp_path / "cat.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "banks": {
                        "INT": [
                            {"pc": "not a number", "name": "Bad"},
                            {"pc": 1, "name": "Invented"},
                        ]
                    }
                },
                handle,
            )
        assert len(cat.load(path)) == 1


class TestABoolIsNotANumber:
    """`isinstance(True, int)` is True, and every range check passes for
    it. A hand-edited `channel = true` came back as `True`, and
    `0xC0 | True` is `0xC1` -- MIDI channel 2 when channel 1 was asked
    for. A wrong channel is not an error anywhere: the instrument plays
    nothing, or something else does.

    The device-ID guard was written first and this one was missed, one
    function away in the same file, in all seven projects. That is the
    reason this test walks *every* loader rather than naming two.
    """

    def _loaders(self):
        import inspect

        import importlib

        module = importlib.import_module("sq.config")
        for name, fn in vars(module).items():
            if not name.startswith("load_") or not callable(fn):
                continue
            hints = getattr(fn, "__annotations__", {})
            if "int" in str(hints.get("return", "")):
                yield name, fn

    def test_there_are_numeric_loaders_to_check(self):
        assert list(self._loaders()), "the search matched nothing"

    def test_no_numeric_loader_accepts_true(self, tmp_path):
        offenders = []
        for name, fn in self._loaders():
            key = name[len("load_") :]
            path = str(tmp_path / f"{key}.toml")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(f"{key} = true\n")
            if fn(path) is not None:
                offenders.append(name)
        assert not offenders, f"{offenders} accept a TOML boolean as a number"

    def test_no_numeric_loader_accepts_an_absurd_value(self, tmp_path):
        offenders = []
        for name, fn in self._loaders():
            key = name[len("load_") :]
            path = str(tmp_path / f"{key}.toml")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(f"{key} = 9999\n")
            if fn(path) is not None:
                offenders.append(name)
        assert not offenders, f"{offenders} accept an out-of-range number"
