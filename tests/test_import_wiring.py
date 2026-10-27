# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# ensqsqed is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 2 of the License, or (at your option)
# any later version.
#
# This file is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
# more details.

"""The import, driven through the browser against the demo unit.

The file is a synthetic All-Sounds dump with invented names, built with the
same frame the unit sends.  Nothing here touches hardware: ``DemoDevice``
holds the banks, so a write is observable and the two flows can be followed
from the keypress to the read-back.  What is pinned is the wiring -- the
screens arrive in order, both confirmations are asked, the write gate is
checked, and the bank the demo holds afterwards is the file's bytes.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Callable

from textual.widgets import Checkbox, DataTable, Input, Static

from ensqsqed.app import EnsqsqedApp
from ensqsqed.demo import DemoDevice
from ensqsqed.file_picker import FilePickerScreen
from ensqsqed.import_bank import BankImportScreen
from ensqsqed.import_single import SingleImportScreen
from ensqsqed.multi import NumberPrompt
from sq import banks, importer
from sq import catalog as cat
from sq import messages as m
from vinsynlib.ui.confirm import ConfirmScreen
from vinsynlib.ui.modals import ChoiceScreen

#: Invented names: the repo never carries the instrument's own.
NAMES = [f"Zz{index:03d}" for index in range(banks.SOUNDS_PER_GROUP)]


def _record(index: int) -> bytes:
    """One invented 204-byte sound, distinct from its neighbours."""
    record = bytearray(m.SOUND_BYTES)
    m.encode_name(record, NAMES[index])
    # A byte with bit 7 set, past the name, so the nibble packing is
    # exercised the way the demo's own records exercise it.
    record[-1] = 0x80 | (index & 0x7F)
    return bytes(record)


def _write_dump(tmp_path: Path) -> Path:
    """A one-message All-Sounds dump holding the whole invented bank."""
    payload = b"".join(
        m.to_nibbles(_record(index)) for index in range(banks.SOUNDS_PER_GROUP)
    )
    path = tmp_path / "invented-bank.syx"
    path.write_bytes(m.frame(m.Function.ALL_SOUNDS_DUMP, payload))
    return path


def _app(device: DemoDevice) -> EnsqsqedApp:
    """The browser over the demo, with no database or config path."""
    return EnsqsqedApp(device, catalog=cat.empty())


async def _wait(predicate: Callable[[], bool], pilot, *, tries: int = 200) -> bool:
    """Wait for something that happens on a worker."""
    for _ in range(tries):
        if predicate():
            return True
        await pilot.pause()
    return False


def _status(app: EnsqsqedApp) -> str:
    """What the browser's status line says."""
    return str(app.query_one("#status", Static).content)


async def _choose_kind(app: EnsqsqedApp, pilot, index: int) -> None:
    """Press ``I`` and pick the whole-bank or single-sound flow."""
    await pilot.press("I")
    await pilot.pause()
    assert isinstance(app.screen, ChoiceScreen)
    for _ in range(index):
        await pilot.press("down")
        await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()
    assert isinstance(app.screen, FilePickerScreen)


async def _pick_file(app: EnsqsqedApp, pilot, path: Path) -> None:
    """Pick ``path`` in the file browser (the temp dir holds one file)."""
    picker = app.screen
    assert isinstance(picker, FilePickerScreen)
    table = picker.query_one(f"#{FilePickerScreen.TABLE_ID}", DataTable)
    table.focus()
    # ``..`` is first, then directories, then files: the lone file is last.
    table.move_cursor(row=table.row_count - 1)
    await pilot.press("enter")
    await pilot.pause()


async def _assign(pilot, screen: SingleImportScreen, row: int, text: str) -> None:
    """Drive Enter on a source row, type the target, and submit it."""
    table = screen.query_one("#source-table", DataTable)
    table.focus()
    table.move_cursor(row=row)
    await pilot.press("enter")
    await pilot.pause()
    prompt = pilot.app.screen
    assert isinstance(prompt, NumberPrompt), "the target prompt is up"
    prompt.query_one("#value", Input).value = text
    await pilot.press("enter")
    await pilot.pause()


class TestWholeBankImport:
    async def test_quitting_the_preview_returns_to_the_same_file_row(
        self, tmp_path: Path
    ):
        _write_dump(tmp_path)
        app = _app(DemoDevice())
        async with app.run_test() as pilot:
            await pilot.pause()
            app._import_folder = str(tmp_path)
            await _choose_kind(app, pilot, 0)
            picker = app.screen
            assert isinstance(picker, FilePickerScreen)
            table = picker.query_one(f"#{FilePickerScreen.TABLE_ID}", DataTable)
            table.focus()
            row = table.row_count - 1
            table.move_cursor(row=row)
            await pilot.press("enter")
            assert await _wait(lambda: isinstance(app.screen, BankImportScreen), pilot)

            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, FilePickerScreen)
            assert str(app.screen.folder) == str(tmp_path)
            reopened = app.screen.query_one(f"#{FilePickerScreen.TABLE_ID}", DataTable)
            assert reopened.cursor_row == row

    async def test_the_whole_bank_is_written_after_two_confirmations(
        self, tmp_path: Path
    ):
        path = _write_dump(tmp_path)
        source = importer.read_import(str(path))
        demo = DemoDevice()
        app = _app(demo)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("w")
            await pilot.pause()
            assert app._gate.armed

            app._import_folder = str(tmp_path)
            await _choose_kind(app, pilot, 0)
            await _pick_file(app, pilot, path)
            assert isinstance(app.screen, BankImportScreen)

            # ctrl+s on the preview answers the bank it shows.
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.press("y")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.press("y")

            # The write lands, and the bank is reread so the browser is
            # current (the import sentence is transient; the reread is the
            # last thing on the status line).
            assert await _wait(
                lambda: demo.read_bank("INT")[0] == source.sounds[0].raw,
                pilot,
            )
            assert await _wait(
                lambda: "Internal (RAM) reread" in _status(app),
                pilot,
            )
            assert app.catalog.live_name("INT", 1) == source.sounds[0].name

            written = demo.read_bank("INT")
            for sound in source.sounds:
                assert written[sound.index] == sound.raw

    async def test_a_closed_gate_refuses_before_any_confirmation(self, tmp_path: Path):
        path = _write_dump(tmp_path)
        demo = DemoDevice()
        before = demo.read_bank("INT")
        app = _app(demo)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert not app._gate.armed

            app._import_folder = str(tmp_path)
            await _choose_kind(app, pilot, 0)
            await _pick_file(app, pilot, path)
            assert isinstance(app.screen, BankImportScreen)

            await pilot.press("ctrl+s")
            await pilot.pause()

            # The preview stays open and says the gate is locked: the work
            # is not thrown away.
            assert isinstance(app.screen, BankImportScreen)
            assert not isinstance(app.screen, ConfirmScreen)
            said = str(app.screen.query_one(".status", Static).content)
            assert "write gate is locked" in said or "write gate locked" in said
            assert demo.read_bank("INT") == before


class TestSingleSoundImport:
    async def test_an_assigned_slot_is_written_and_the_pane_is_reread(
        self, tmp_path: Path
    ):
        path = _write_dump(tmp_path)
        source = importer.read_import(str(path))
        demo = DemoDevice()
        app = _app(demo)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("w")
            await pilot.pause()

            app._import_folder = str(tmp_path)
            await _choose_kind(app, pilot, 1)
            await _pick_file(app, pilot, path)
            # The target bank: Internal (RAM), the first row.
            assert await _wait(
                lambda: isinstance(app.screen, SingleImportScreen), pilot
            )
            screen = app.screen
            assert isinstance(screen, SingleImportScreen)

            await _assign(pilot, screen, 0, "05")
            table = screen.query_one("#source-table", DataTable)
            assert str(table.get_cell("0", "target")) == "05"

            await pilot.press("ctrl+s")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.press("y")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.press("y")

            assert await _wait(
                lambda: demo.read_bank("INT")[5] == source.sounds[0].raw,
                pilot,
            )
            # And the map is reopened over a fresh read: the old screen is
            # gone and the new right pane shows the imported name at 05.
            assert await _wait(
                lambda: (
                    isinstance(app.screen, SingleImportScreen)
                    and app.screen is not screen
                ),
                pilot,
            )
            fresh = app.screen
            assert isinstance(fresh, SingleImportScreen)
            target_table = fresh.query_one("#target-table", DataTable)
            assert target_table.get_cell("5", "name") == source.sounds[0].name
            assert "the pane was reread" in _status(app)

    async def test_manual_preview_sends_the_focused_sound_without_storing(
        self, tmp_path: Path
    ):
        path = _write_dump(tmp_path)
        source = importer.read_import(str(path))
        demo = DemoDevice()
        sends = []
        stores = []

        def send(group, writes, verify=True):
            sends.append((group, dict(writes)))
            return SimpleNamespace(verified=False, diff_slots=())

        def store(group, writes):
            stores.append((group, dict(writes)))
            return SimpleNamespace(verified=True, diff_slots=())

        demo.send_sound_slots = send
        demo.store_sound_slots = store
        app = _app(demo)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("w")
            await pilot.pause()
            app._import_folder = str(tmp_path)
            await _choose_kind(app, pilot, 1)
            await _pick_file(app, pilot, path)
            assert await _wait(
                lambda: isinstance(app.screen, SingleImportScreen), pilot
            )
            screen = app.screen
            assert isinstance(screen, SingleImportScreen)
            await _assign(pilot, screen, 0, "05")
            await pilot.press("l")

            def preview_status():
                return str(screen.query_one(".status", Static).content)

            assert await _wait(lambda: "preview loaded" in preview_status(), pilot)
            assert sends == [("INT", {5: source.sounds[0].raw})]
            assert stores == []
            assert "not stored" in preview_status()

    async def test_the_permanent_store_option_calls_the_store_primitive(
        self, tmp_path: Path
    ):
        path = _write_dump(tmp_path)
        source = importer.read_import(str(path))
        demo = DemoDevice()
        calls = []

        def store(group, writes):
            calls.append((group, dict(writes)))
            return SimpleNamespace(verified=True, diff_slots=())

        demo.store_sound_slots = store
        app = _app(demo)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("w")
            await pilot.pause()
            app._import_folder = str(tmp_path)
            await _choose_kind(app, pilot, 1)
            await _pick_file(app, pilot, path)
            assert await _wait(
                lambda: isinstance(app.screen, SingleImportScreen), pilot
            )
            screen = app.screen
            assert isinstance(screen, SingleImportScreen)
            checkbox = screen.query_one("#store-permanently", Checkbox)
            checkbox.value = True
            await pilot.pause()

            await _assign(pilot, screen, 0, "05")
            await pilot.press("ctrl+s")
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            await pilot.press("y")
            assert await _wait(lambda: len(calls) == 1, pilot)
            assert calls[0][0] == "INT"
            assert calls[0][1] == {5: source.sounds[0].raw}
            assert await _wait(lambda: "stored and verified" in _status(app), pilot)

    async def test_a_duplicate_target_is_refused(self, tmp_path: Path):
        path = _write_dump(tmp_path)
        demo = DemoDevice()
        before = demo.read_bank("INT")
        app = _app(demo)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("w")
            await pilot.pause()

            app._import_folder = str(tmp_path)
            await _choose_kind(app, pilot, 1)
            await _pick_file(app, pilot, path)
            assert await _wait(
                lambda: isinstance(app.screen, SingleImportScreen), pilot
            )
            screen = app.screen
            assert isinstance(screen, SingleImportScreen)

            # The first assignment lands.
            await _assign(pilot, screen, 0, "05")
            # The second row tries the same slot: the screen refuses while
            # the assignment is being made and stays open.
            await _assign(pilot, screen, 1, "05")
            assert app.screen is screen
            table = screen.query_one("#source-table", DataTable)
            assert str(table.get_cell("1", "target")) == "--"
            assert "already assigned" in str(
                screen.query_one(".status", Static).content
            )
            assert screen.assignments == {0: 5}

            # The map that ships is checked again on ctrl+s, so a duplicate
            # that got past the row guard is still refused, not written.
            screen._assignments[1] = 5
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert app.screen is screen
            refused = str(screen.query_one(".status", Static).content)
            assert "more than one sound" in refused
            assert demo.read_bank("INT") == before
