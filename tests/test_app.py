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

"""The browser. Every fixture is invented; no MIDI port is ever opened."""

import ast
import os

import pytest

from sq import banks
from sq import catalog as cat
from sq import favorites as fav
from ensqsqed.app import VIEW_ALL_FAVOURITES, EnsqsqedApp
from ensqsqed.demo import DemoDevice


@pytest.fixture
def app():
    return EnsqsqedApp(DemoDevice(), catalog=cat.empty())


class TestStartup:
    async def test_both_panes_fill(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one("#bank-table").row_count == len(banks.GROUPS)
            assert (app.query_one("#slot-table").row_count
                    == banks.SOUNDS_PER_GROUP)


class TestCursorIsSilent:
    """The property that decides whether this can sit open next to a
    running sequencer."""

    async def test_moving_in_the_slot_pane_sends_nothing(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#slot-table").focus()
            for _ in range(12):
                await pilot.press("down")
            await pilot.press("end")
            await pilot.pause()
            assert app.device.selected_log == []

    async def test_moving_between_groups_sends_nothing(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#bank-table").focus()
            for _ in range(len(banks.GROUPS) + 2):
                await pilot.press("down")
            await pilot.pause()
            assert app.device.selected_log == []

    async def test_reading_names_sends_no_program_change(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("r")
            for _ in range(20):
                await pilot.pause()
            assert app.device.selected_log == []


class TestSelectionSendsTheGroupFirst:
    """A program change means nothing on this instrument until a selector
    has said which group it is about."""

    async def test_enter_selects(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#slot-table").focus()
            await pilot.press("enter")
            for _ in range(8):
                await pilot.pause()
            assert len(app.device.selected_log) == 1

    async def test_a_drum_kit_is_selected_without_a_selector(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            app._fill("DRUM")
            await pilot.pause()
            app.query_one("#slot-table").focus()
            await pilot.press("enter")
            for _ in range(8):
                await pilot.pause()
            slot, _ = app.device.selected_log[0]
            assert slot.group_id == "DRUM"
            assert not slot.needs_selector
            assert len(banks.select_messages(slot)) == 1

    async def test_a_card_sound_is_selected_with_one(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            app._fill("CARDB")
            await pilot.pause()
            app.query_one("#slot-table").focus()
            await pilot.press("enter")
            for _ in range(8):
                await pilot.pause()
            slot, _ = app.device.selected_log[0]
            assert slot.needs_selector
            assert [msg[1] for msg in banks.select_messages(slot)] == [
                slot.group.selector, slot.program_change]

    async def test_two_selections_in_a_row_send_one(self):
        app = EnsqsqedApp(DemoDevice(latency=0.5), catalog=cat.empty())
        async with app.run_test() as pilot:
            await pilot.pause()
            app.query_one("#slot-table").focus()
            await pilot.pause()
            app.action_select()
            app.action_select()
            for _ in range(60):
                await pilot.pause()
            assert len(app.device.selected_log) == 1


class TestOnlyInternalCanBeRead:
    async def test_internal_fills_the_catalog(self, app):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("r")
            for _ in range(30):
                await pilot.pause()
            assert app.catalog.live_name("INT", 1) is not None

    async def test_the_other_groups_are_refused_before_anything_is_sent(
            self, app):
        """They have no dump request of any kind."""
        async with app.run_test() as pilot:
            await pilot.pause()
            for group_id in ("ROM", "CARDA", "CARDB", "DRUM"):
                app._fill(group_id)
                await pilot.pause()
                await pilot.press("r")
                for _ in range(6):
                    await pilot.pause()
                assert app.catalog.live_name(group_id, 1) is None

    async def test_the_dump_numbers_from_zero_and_the_unit_from_one(
            self, app):
        """The catalog keys on what the unit shows."""
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("r")
            for _ in range(30):
                await pilot.pause()
            assert app.catalog.live_name("INT", 0) is None, (
                "sound 0 of the dump is shown as 001")
            assert app.catalog.live_name("INT", 80) is not None


class TestFavouritesActOnTheRowNotThePane:
    async def test_f_uses_the_rows_own_group(self, tmp_path):
        store = fav.Favorites(str(tmp_path / "f.db"))
        store.add("CARDA", 5, name="Invented", rating=3)
        app = EnsqsqedApp(DemoDevice(), catalog=cat.empty(), favorites=store)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.view_mode = VIEW_ALL_FAVOURITES
            app._fill("INT")                 # pane elsewhere; row is CARDA 5
            await pilot.pause()
            assert [(e.bank_id, e.number) for e in app._current] == [
                ("CARDA", 5)]
            app.query_one("#slot-table").focus()
            await pilot.press("f")
            for _ in range(4):
                await pilot.pause()
            assert store.get("INT", 5) is None, "favourited the wrong group"
            assert store.get("CARDA", 5) is None
        store.close()


class TestStructuralRules:
    def _workers(self):
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "ensqsqed", "app.py")
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if any((isinstance(d, ast.Call)
                    and getattr(d.func, "id", "") == "work")
                   or getattr(d, "id", "") == "work"
                   for d in node.decorator_list):
                yield node

    def test_there_are_workers_to_check(self):
        assert list(self._workers())

    def test_no_worker_touches_the_store_or_the_catalog(self):
        offenders = []
        for node in self._workers():
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Attribute)
                        and inner.attr in ("favorites", "catalog")
                        and isinstance(inner.value, ast.Name)
                        and inner.value.id == "self"):
                    offenders.append(node.name)
        assert not offenders, (
            f"worker(s) {sorted(set(offenders))} reach self.favorites or "
            f"self.catalog; workers do MIDI and hand off")

    def test_no_worker_sets_the_busy_flag_itself(self):
        offenders = []
        for node in self._workers():
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Assign)
                        and any(isinstance(t, ast.Attribute)
                                and t.attr == "_busy" for t in inner.targets)
                        and getattr(inner.value, "value", None) is True):
                    offenders.append(node.name)
        assert not offenders, f"worker(s) {offenders} set _busy themselves"
