# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#

"""The startup meter: visible, orange while waiting, green when ready."""

from __future__ import annotations

from textual.widgets import Static

from ensqsqed.app import EnsqsqedApp
from sq import catalog as cat
from sq import preset_cache


class _Device:
    def read_names(self):
        return {}

    def read_all_presets(self):
        return None


async def test_fresh_caches_mean_no_startup_read_and_no_gap(
    tmp_path, monkeypatch
) -> None:
    names = tmp_path / "names.json"
    names.write_text("{}")
    app = EnsqsqedApp(_Device(), catalog=cat.empty(), read_names_at_startup=True)
    app._live_names_path = str(names)
    app._all_dump = object()  # a cache is already loaded
    monkeypatch.setattr(preset_cache, "age_seconds", lambda path=None: 0.0)

    async with app.run_test() as pilot:
        await pilot.pause()
        app._startup_begin()
        assert app._startup_phases == []
        assert app._startup_active is False


async def test_the_startup_label_is_orange_while_waiting() -> None:
    app = EnsqsqedApp(None, catalog=cat.empty())
    async with app.run_test() as pilot:
        await pilot.pause()
        app._show_startup("waiting for the unit")
        await pilot.pause()
        label = app.query_one("#startup-label", Static)
        assert "waiting for the unit" in str(label.render())
        assert label.has_class("waiting")
        assert not label.has_class("ready")
        assert not app.query_one("#startup").has_class("hidden")


async def test_the_startup_label_turns_green_when_ready() -> None:
    app = EnsqsqedApp(None, catalog=cat.empty())
    async with app.run_test() as pilot:
        await pilot.pause()
        app._startup_phases = ["only phase"]
        app._startup_active = True
        app._startup_done = 0
        app._show_startup("reading only phase")
        app._startup_step_done()
        await pilot.pause()
        label = app.query_one("#startup-label", Static)
        assert "ready" in str(label.render())
        assert label.has_class("ready")
        assert not label.has_class("waiting")


async def test_the_meter_names_the_next_phase_not_just_waiting() -> None:
    app = EnsqsqedApp(None, catalog=cat.empty())
    async with app.run_test() as pilot:
        await pilot.pause()
        app._startup_phases = ["Internal names", "the 80 presets"]
        app._startup_active = True
        app._startup_done = 1
        app._show_startup("waiting for the next read")
        await pilot.pause()
        label = app.query_one("#startup-label", Static)
        assert "waiting for the next read" in str(label.render())

        app._startup_step(app._startup_phase_text(" (about 12 s)"))
        await pilot.pause()
        assert "reading the 80 presets (step 2 of 2" in str(label.render())
