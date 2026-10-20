# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# ensqsqed is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 2 of the License, or (at your option) any later
# version.
#
# ensqsqed is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.

"""The preset editors, wired into the browser.

**Every byte here comes from the committed fixture.**  The fake unit
answers with the sequence blob and the preset the real instrument sent,
and the all-presets dump is built out of that same preset -- so a test
that asserts on the bytes is asserting on the unit's own data and not
on something invented here.  Nothing is asserted about a *name*: the
names in a preset are the unit's, and a test that pinned one would
break the day the unit is refilled.

**No MIDI port is ever opened.**  The fake is the device, in the same
way the demo device is.
"""

from pathlib import Path
from typing import Callable, List, Optional, Tuple

import pytest
from textual.widgets import DataTable

from ensqsqed.app import EnsqsqedApp
from ensqsqed.multi import MultiScreen, PresetListScreen
from sq import catalog as cat
from sq.bridge import AllPresetsDump, CurrentPreset, PresetSent
from sq.presets import split_single_dump

#: The dump the unit sent on the bench: 48 bytes of sequence data and
#: the 168-byte preset behind them.
FIXTURE = Path(__file__).parent / "fixtures" / "single_seq_default.bin"

#: How many presets the unit holds.
PRESET_COUNT = 80

#: The size of one preset, in bytes.
PRESET_BYTES = 168


def _fixture() -> Tuple[bytes, bytes]:
    """The fixture's sequence blob and its preset."""
    return split_single_dump(FIXTURE.read_bytes())


SEQ_BLOB, PRESET_RAW = _fixture()


def _all_dump() -> AllPresetsDump:
    """An all-presets dump built from the fixture's own preset.

    Eighty copies of the fixture's preset, with the two tables the
    parser reads: the track table pointing at the fixture's sequence
    blob, and the preset table pointing at the eighty presets.

    **The numbers are the real ones.**  A word entry of the preset table
    is the offset of that slot's preset counted from the start of the
    table, which is 208 bytes of table before the first preset -- so the
    entry of slot *n* is 208 + 168 * n.  The parser is exercised the
    way the unit exercises it, not the way a made-up number would.
    """
    words = b"".join(
        (208 + index * PRESET_BYTES).to_bytes(2, "big") for index in range(PRESET_COUNT)
    )
    words += b"\x00" * (200 - len(words))
    track_table = bytearray(400)
    # One used sequence: the fixture's preset plays sequence 0.
    track_table[0:4] = (0x3B4).to_bytes(4, "big")
    return AllPresetsDump(
        count=PRESET_COUNT,
        alloc=b"\x00" * 8,
        track_table=bytes(track_table),
        track_blobs=(SEQ_BLOB,),
        preset_table=b"\x00" * 8 + words,
        preset_area_start=0,
        presets_raw=(PRESET_RAW,) * PRESET_COUNT,
        globals=b"\x00" * 21,
    )


class FakeMultiDevice:
    """A unit that answers the preset requests and keeps what it was sent.

    **The read-back returns what was sent**, byte for byte, because that
    is what the real unit does while an Edit Preset is live: see
    ``docs/PRESET-FRAMING.md`` section 9.3.
    """

    def __init__(self) -> None:
        self.description = "fake SQ-R (no MIDI port)"
        self.channel = 0
        #: Every send, in order: ``(sequence data, preset)``.
        self.sent: List[Tuple[bytes, bytes]] = []
        #: How many times the preset was read.
        self.reads = 0
        self.current = CurrentPreset(seq_blob=SEQ_BLOB, preset_raw=PRESET_RAW)
        self.dump = _all_dump()

    def read_current_preset(self, *, timeout: Optional[float] = None) -> CurrentPreset:
        self.reads += 1
        return self.current

    def read_all_presets(self, *, timeout: Optional[float] = None) -> AllPresetsDump:
        self.reads += 1
        return self.dump

    def send_preset(
        self,
        seq_blob: bytes,
        preset_raw: bytes,
        *,
        verify: bool = True,
        timeout: Optional[float] = None,
    ) -> PresetSent:
        self.sent.append((seq_blob, preset_raw))
        self.current = CurrentPreset(seq_blob=seq_blob, preset_raw=preset_raw)
        return PresetSent(
            acked=True,
            verified=verify,
            diff_offsets=(),
            read_back=self.current,
        )


@pytest.fixture
def device() -> FakeMultiDevice:
    return FakeMultiDevice()


@pytest.fixture
def app(device: FakeMultiDevice) -> EnsqsqedApp:
    return EnsqsqedApp(device, catalog=cat.empty())


async def _wait(predicate: Callable[[], bool], pilot, *, tries: int = 200) -> bool:
    """Wait for something that happens on a worker."""
    for _ in range(tries):
        if predicate():
            return True
        await pilot.pause()
    return False


def _focus(table: DataTable, row: int, key: str) -> None:
    """Put the table's cursor on one cell.

    By key rather than by index: the columns are rebuilt when the view
    changes, and a test that counted them would have to know how many
    there are.
    """
    for index, column in enumerate(table.columns.values()):
        if column.key == key:
            table.move_cursor(row=row, column=index)
            return
    raise AssertionError(f"no column {key!r} in the table")


def _volume_cell(screen: MultiScreen) -> str:
    """What the volume cell of the first track says."""
    table = screen.query_one("#track-table", DataTable)
    return str(table.get_cell("t1", "volume"))


async def _open_mix_view(screen: MultiScreen, pilot) -> DataTable:
    """The editor, on the MIX columns, with the cursor on the volume."""
    screen.action_toggle_view()  # PRG -> MIDI
    screen.action_toggle_view()  # MIDI -> MIX
    table = screen.query_one("#track-table", DataTable)
    _focus(table, 0, "volume")
    await pilot.pause()
    return table


class TestTheGateClosed:
    """The write gate is the family's: locked unless it is armed."""

    async def test_the_editor_still_opens(self, app: EnsqsqedApp, device):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("m")
            assert await _wait(lambda: app._multi_screen is not None, pilot)
            assert isinstance(app.screen, MultiScreen)

    async def test_an_edit_is_refused_and_nothing_is_sent(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("m")
            assert await _wait(lambda: app._multi_screen is not None, pilot)
            screen = app._multi_screen
            assert screen is not None
            await _open_mix_view(screen, pilot)
            before = _volume_cell(screen)
            await pilot.press("minus")
            await pilot.pause()
            # The cell did not move and nothing reached the unit.
            assert _volume_cell(screen) == before
            assert device.sent == []

    async def test_a_change_made_after_the_gate_was_closed_is_put_back(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        """The gate is asked again for every change.

        A modal screen cuts the app's binding chain, so `w` cannot be
        pressed while the editor is open -- but the app's own check
        stands between two changes anyway, and a change refused by it
        has to leave the cell as it was.
        """
        async with app.run_test() as pilot:
            await pilot.pause()
            app._gate.armed = True
            await pilot.press("m")
            assert await _wait(lambda: app._multi_screen is not None, pilot)
            screen = app._multi_screen
            assert screen is not None
            await _open_mix_view(screen, pilot)
            before = _volume_cell(screen)
            app._gate.armed = False
            await pilot.press("minus")
            await pilot.pause()
            assert _volume_cell(screen) == before
            assert device.sent == []


class TestTheGateArmed:
    """Armed, the change goes out and the undo brings it back."""

    async def test_the_change_is_sent_as_a_whole_preset(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("w")
            await pilot.pause()
            assert app._gate.armed
            await pilot.press("m")
            assert await _wait(lambda: app._multi_screen is not None, pilot)
            screen = app._multi_screen
            assert screen is not None
            await _open_mix_view(screen, pilot)
            await pilot.press("minus")
            assert await _wait(lambda: len(device.sent) == 1, pilot)
            seq_blob, preset_raw = device.sent[0]
            # The sequence data goes with it, unchanged: this protocol
            # has no field-level write, so one changed byte means one
            # whole dump.
            assert seq_blob == SEQ_BLOB
            assert len(preset_raw) == PRESET_BYTES
            assert preset_raw != PRESET_RAW
            # And only the volume changed: the fixture's track one holds
            # 127, and the nudge took it down by one.
            assert preset_raw[:34] == PRESET_RAW[:34]
            assert preset_raw[34] == PRESET_RAW[34] - 1
            assert preset_raw[35:] == PRESET_RAW[35:]

    async def test_undo_sends_the_original_bytes_back(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("w")
            await pilot.pause()
            await pilot.press("m")
            assert await _wait(lambda: app._multi_screen is not None, pilot)
            screen = app._multi_screen
            assert screen is not None
            await _open_mix_view(screen, pilot)
            await pilot.press("minus")
            assert await _wait(lambda: len(device.sent) == 1, pilot)
            await pilot.press("z")
            assert await _wait(lambda: len(device.sent) == 2, pilot)
            # The whole original 216 bytes go back, not just the byte
            # that changed.
            assert device.sent[1] == (SEQ_BLOB, PRESET_RAW)
            assert device.sent[1][0] + device.sent[1][1] == FIXTURE.read_bytes()

    async def test_undo_puts_the_cell_back_too(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        """The table is a picture of the model, and the model is the truth.

        A cell that still reads the undone value is the same lie a
        refused change leaves behind.
        """
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("w")
            await pilot.pause()
            await pilot.press("m")
            assert await _wait(lambda: app._multi_screen is not None, pilot)
            screen = app._multi_screen
            assert screen is not None
            await _open_mix_view(screen, pilot)
            before = _volume_cell(screen)
            await pilot.press("minus")
            assert await _wait(lambda: len(device.sent) == 1, pilot)
            assert _volume_cell(screen) != before
            await pilot.press("z")
            assert await _wait(lambda: len(device.sent) == 2, pilot)
            assert _volume_cell(screen) == before

    async def test_an_undo_is_a_write_and_the_gate_says_no(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        """The undo sends a preset, so the gate is asked about it too.

        The change it undoes went out through the gate, but the gate can
        have been locked since -- and the undo can be pressed in the
        browser, where ``w`` is a live key.
        """
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("w")
            await pilot.pause()
            await pilot.press("m")
            assert await _wait(lambda: app._multi_screen is not None, pilot)
            screen = app._multi_screen
            assert screen is not None
            await _open_mix_view(screen, pilot)
            await pilot.press("minus")
            assert await _wait(lambda: len(device.sent) == 1, pilot)
            # Leave the editor and lock the gate.
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("w")
            await pilot.pause()
            assert not app._gate.armed
            await pilot.press("z")
            await pilot.pause()
            # Nothing was sent, and the record is still there: the
            # preset it holds is still the one the unit has.
            assert len(device.sent) == 1
            assert len(app._undo_log) == 1


class TestOnePresetPerSession:
    """`m` and `x` are two views of one preset."""

    async def test_the_fx_editor_opens_over_the_preset_already_in_hand(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("m")
            assert await _wait(lambda: app._multi_screen is not None, pilot)
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("x")
            assert await _wait(lambda: app._fx_screen is not None, pilot)
            # The preset was read once: the second editor is over the
            # session's preset, not over a second reading of the unit.
            assert device.reads == 1


class TestTheEffectEditor:
    """`x`: the same preset, its effect."""

    async def test_an_effect_change_is_sent(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("w")
            await pilot.pause()
            await pilot.press("x")
            assert await _wait(lambda: app._fx_screen is not None, pilot)
            screen = app._fx_screen
            assert screen is not None
            table = screen.query_one("#fx-table", DataTable)
            # The first row is the effect's type, which is a list of
            # names; the second is a number, and a number is what a
            # nudge changes.
            table.move_cursor(row=1)
            await pilot.pause()
            await pilot.press("plus")
            assert await _wait(lambda: len(device.sent) == 1, pilot)
            seq_blob, preset_raw = device.sent[0]
            assert seq_blob == SEQ_BLOB
            assert len(preset_raw) == PRESET_BYTES
            assert preset_raw != PRESET_RAW
            # Only the effect's own bytes changed: the effect block is
            # the last eleven bytes of the preset.
            assert preset_raw[:152] == PRESET_RAW[:152]
            assert preset_raw[152:] != PRESET_RAW[152:]


class TestThePresetList:
    """`p`: the eighty presets the unit holds."""

    async def test_a_picked_preset_is_loaded_into_the_editor(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("p")
            assert await _wait(lambda: isinstance(app.screen, PresetListScreen), pilot)
            await pilot.press("enter")
            assert await _wait(lambda: app._multi_screen is not None, pilot)
            assert app._edit_preset is not None
            # The editor is over the preset that was picked.
            assert app._edit_preset.to_bytes() == PRESET_RAW
            assert app._edit_slot == 0
            # **Loading is not writing.**  The unit was not touched.
            assert device.sent == []

    async def test_the_list_carries_the_units_numbers(self, app: EnsqsqedApp):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("p")
            assert await _wait(lambda: isinstance(app.screen, PresetListScreen), pilot)
            screen = app.screen
            assert isinstance(screen, PresetListScreen)
            table = screen.query_one("#preset-table", DataTable)
            assert table.row_count == PRESET_COUNT
            # The unit counts its preset locations from one and the
            # dump's slots from zero.
            assert table.get_cell("0", "slot") == "001"
            assert table.get_cell("79", "slot") == "080"
