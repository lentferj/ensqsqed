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
from textual.widgets import DataTable, Static

from ensqsqed.app import EnsqsqedApp
from ensqsqed.multi import MultiScreen, PresetListScreen, SoundPickerScreen
from sq import catalog as cat
from sq import favorites as fav
from sq.bridge import (
    AllPresetsDump,
    CurrentPreset,
    DeviceError,
    PresetMismatch,
    PresetSent,
)
from sq.presets import Preset, split_single_dump
from vinsynlib.ui.modals import ChoiceScreen
from vinsynlib.undo import Record

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
        #: What the next send raises, if anything.  A refusal and a
        #: mismatch are both outcomes the app has to report rather than
        #: assume away.
        self.fail: Optional[BaseException] = None
        #: What the next read raises, if anything.
        self.read_fail: Optional[BaseException] = None
        self.current = CurrentPreset(seq_blob=SEQ_BLOB, preset_raw=PRESET_RAW)
        self.dump = _all_dump()

    def read_current_preset(self, *, timeout: Optional[float] = None) -> CurrentPreset:
        self.reads += 1
        if self.read_fail is not None:
            raise self.read_fail
        return self.current

    def read_all_presets(self, *, timeout: Optional[float] = None) -> AllPresetsDump:
        self.reads += 1
        if self.read_fail is not None:
            raise self.read_fail
        return self.dump

    def send_preset(
        self,
        seq_blob: bytes,
        preset_raw: bytes,
        *,
        verify: bool = True,
        timeout: Optional[float] = None,
    ) -> PresetSent:
        if self.fail is not None:
            raise self.fail
        self.sent.append((seq_blob, preset_raw))
        self.current = CurrentPreset(seq_blob=seq_blob, preset_raw=preset_raw)
        return PresetSent(
            acked=True,
            verified=verify,
            diff_offsets=(),
            read_back=self.current,
        )


class SelectingMultiDevice(FakeMultiDevice):
    """A unit that can move its selection, with either path.

    ``pc_works`` decides whether the Program Change takes; when it does
    not, the app must fall back to the button walk.  The working preset's
    seqno byte is what both paths change, exactly as on the unit.
    """

    def __init__(self, *, pc_works: bool = True, start_slot: int = 5) -> None:
        super().__init__()
        self.pc_works = pc_works
        self.pc_calls: List[int] = []
        self.walk_calls: List[Tuple[int, int]] = []
        self.tap_calls: List[int] = []
        raw = bytearray(PRESET_RAW)
        raw[0] = start_slot
        self.current = CurrentPreset(seq_blob=SEQ_BLOB, preset_raw=bytes(raw))

    def tap_button(self, number: int, *, hold: float = 0.12) -> None:
        self.tap_calls.append(number)

    def _land(self, slot: int) -> CurrentPreset:
        raw = bytearray(self.current.preset_raw)
        raw[0] = slot
        self.current = CurrentPreset(
            seq_blob=self.current.seq_blob, preset_raw=bytes(raw)
        )
        return self.current

    def select_preset(self, slot: int, *, timeout=None) -> CurrentPreset:
        self.pc_calls.append(slot)
        return self._land(slot) if self.pc_works else self.current

    def select_preset_by_walk(
        self, target_slot: int, *, known_slot: int, **kwargs
    ) -> int:
        self.walk_calls.append((target_slot, known_slot))
        self._land(target_slot)
        return target_slot


class RenameMultiDevice(FakeMultiDevice):
    """A unit that can rename a preset on itself."""

    def __init__(self) -> None:
        super().__init__()
        self.rename_calls: list = []
        self.rename_fail: Optional[BaseException] = None

    def rename_preset_on_unit(
        self, slot, new_name, *, pending=False, timeout=None, progress=None
    ) -> str:
        if self.rename_fail is not None:
            raise self.rename_fail
        self.rename_calls.append((slot, new_name, pending))
        from sq.presets import Preset

        preset = Preset.from_bytes(self.current.preset_raw)
        preset.name = new_name
        self.current = CurrentPreset(
            seq_blob=self.current.seq_blob, preset_raw=preset.to_bytes()
        )
        return new_name + " " * (16 - len(new_name))


@pytest.fixture
def device() -> FakeMultiDevice:
    return FakeMultiDevice()


@pytest.fixture
def app(device: FakeMultiDevice) -> EnsqsqedApp:
    return EnsqsqedApp(device, catalog=cat.empty())


@pytest.fixture
def rename_device() -> RenameMultiDevice:
    return RenameMultiDevice()


@pytest.fixture
def rename_app(rename_device: RenameMultiDevice) -> EnsqsqedApp:
    return EnsqsqedApp(rename_device, catalog=cat.empty())


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


def _status(screen) -> str:
    """What the editor's own status line says.

    The app's ``#status`` is the dimmed backdrop behind the modal, so
    a sentence written there is written where nobody is looking.
    """
    return str(screen.query_one(".status", Static).content)


def _app_status(app: EnsqsqedApp) -> str:
    """What the browser's own status line says."""
    return str(app.query_one("#status", Static).content)


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

        The editor's own `w` toggles the app's gate, so the state can
        change while the screen is open -- and the app's own check
        stands between two changes anyway.  A change refused by it has
        to leave the cell as it was.
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


class TestTheGateInTheEditor:
    """`w` in an editor is the app's key, and its sentences are the editor's.

    A modal screen cuts the app's binding chain, so before this the gate
    could only be armed by closing the editor -- and the refusal that
    said so was written on the dimmed browser behind the modal.
    """

    async def test_w_arms_the_gate_and_an_edit_goes_through(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("m")
            assert await _wait(lambda: app._multi_screen is not None, pilot)
            screen = app._multi_screen
            assert screen is not None
            await _open_mix_view(screen, pilot)
            await pilot.press("w")
            await pilot.pause()
            assert app._gate.armed
            assert "write gate ARMED" in _status(screen)
            await pilot.press("minus")
            assert await _wait(lambda: len(device.sent) == 1, pilot)

    async def test_w_locks_it_again_from_the_fx_editor(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("x")
            assert await _wait(lambda: app._fx_screen is not None, pilot)
            screen = app._fx_screen
            assert screen is not None
            note = screen.query_one(".note", Static)
            await pilot.press("w")
            await pilot.pause()
            assert app._gate.describe() == "write gate ARMED"
            assert "write gate ARMED" in str(note.content)
            await pilot.press("w")
            await pilot.pause()
            assert app._gate.describe() == "write gate locked"
            assert "write gate locked" in str(note.content)

    async def test_a_refusal_is_said_in_the_editor_not_behind_it(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        """The browser's status line is the dimmed backdrop; it stays quiet."""
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("x")
            assert await _wait(lambda: app._fx_screen is not None, pilot)
            screen = app._fx_screen
            assert screen is not None
            await pilot.press("down")
            await pilot.press("+")
            await pilot.pause()
            assert "the change not sent" in _status(screen)
            assert "the change not sent" not in _app_status(app)

    async def test_nothing_to_undo_is_said_in_the_editor(self, app: EnsqsqedApp):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("x")
            assert await _wait(lambda: app._fx_screen is not None, pilot)
            screen = app._fx_screen
            assert screen is not None
            await pilot.press("z")
            await pilot.pause()
            # The screen's own line is where the reader is looking; the
            # app's line also gets it through `say_`, but that one is
            # behind the modal.
            assert "nothing to undo" in _status(screen)

    async def test_an_undo_refused_by_the_gate_is_said_in_the_editor(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("m")
            assert await _wait(lambda: app._multi_screen is not None, pilot)
            screen = app._multi_screen
            assert screen is not None
            await _open_mix_view(screen, pilot)
            await pilot.press("w")
            await pilot.press("minus")
            assert await _wait(lambda: len(device.sent) == 1, pilot)
            # The gate is locked between the change and the undo.
            await pilot.press("w")
            await pilot.pause()
            assert not app._gate.armed
            await pilot.press("z")
            assert await _wait(lambda: "the undo not sent" in _status(screen), pilot)


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
            # Each opening re-reads the current preset: the panel can
            # have changed it between the two.
            assert device.reads == 2

    async def test_the_track_editor_opens_again_over_the_same_preset(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        """`m` twice reads the unit twice: the panel may have changed it."""
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("m")
            assert await _wait(lambda: app._multi_screen is not None, pilot)
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("m")
            assert await _wait(lambda: app._multi_screen is not None, pilot)
            assert device.reads == 2

    async def test_an_edit_made_on_the_panel_between_openings_is_seen(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        """The stale on/off trap: re-open must read the unit, not the copy."""
        changed = Preset.from_bytes(PRESET_RAW)
        changed.set_active(2, True)
        changed.tracks[1] = changed.tracks[1].updated(status=1)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("m")
            assert await _wait(lambda: app._multi_screen is not None, pilot)
            await pilot.press("escape")
            await pilot.pause()
            # The panel changed the unit while the editor was closed.
            device.current = CurrentPreset(
                seq_blob=SEQ_BLOB, preset_raw=changed.to_bytes()
            )
            await pilot.press("m")
            assert await _wait(lambda: app._multi_screen is not None, pilot)
            screen = app._multi_screen
            assert screen is not None
            assert app._edit_preset is not None
            assert app._edit_preset.is_active(2)
            assert app._edit_preset.tracks[1].status == 1


class TestTheRefusedWrite:
    """A write that did not land leaves the model as it was."""

    async def test_a_mismatch_puts_the_change_back(
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
            before = _volume_cell(screen)
            device.fail = PresetMismatch(
                "the unit did not read back what was sent: 1 byte(s) differ",
                PresetSent(
                    acked=True,
                    verified=False,
                    diff_offsets=(82,),
                    read_back=None,
                ),
            )
            await pilot.press("minus")
            assert await _wait(lambda: "not sent" in _status(screen), pilot)
            # The cell shows what the unit holds, not what was asked for.
            assert _volume_cell(screen) == before

    async def test_a_failure_puts_the_change_back(
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
            before = _volume_cell(screen)
            device.fail = TimeoutError("no Single Sequence Dump came back")
            await pilot.press("minus")
            assert await _wait(lambda: "not sent" in _status(screen), pilot)
            assert _volume_cell(screen) == before

    async def test_a_change_with_no_preset_loaded_is_refused(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        """There is nothing to send, and it is said so."""
        async with app.run_test() as pilot:
            await pilot.pause()
            preset = Preset.from_bytes(PRESET_RAW)
            app._apply_preset(preset, "track 1 volume 50→49", None)
            await pilot.pause()
            assert device.sent == []
            assert "no preset is loaded" in _app_status(app)

    async def test_a_read_that_fails_says_so(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        """A unit that does not answer is a refusal with a sentence."""
        device.read_fail = TimeoutError("no Single Sequence Dump came back")
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("m")
            assert await _wait(lambda: "preset: " in _app_status(app), pilot)
            # No editor was opened: there is nothing to edit.
            assert app._multi_screen is None
            assert app._preset_loaded is False


class TestTheSoundPicker:
    """The sound picker is the app's, and it writes the track's sound."""

    async def test_the_app_runs_the_picker_and_the_pick_is_sent(
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
            table = screen.query_one("#track-table", DataTable)
            _focus(table, 0, "spot")
            await pilot.press("enter")
            await pilot.pause()
            # The screen says which track wants a sound; the app runs its
            # own picker over every group, so ROM and the cards are
            # reachable from a track that is on Internal.
            assert isinstance(app.screen, SoundPickerScreen)
            await pilot.press("down")
            await pilot.press("down")
            await pilot.press("enter")
            assert await _wait(lambda: len(device.sent) == 1, pilot)
            preset = app._edit_preset
            assert preset is not None
            assert preset.tracks[0].sound_number == 2
            _seq_blob, preset_raw = device.sent[0]
            assert preset_raw[32:34] == b"\x00\x02"

    async def test_a_cancelled_picker_changes_nothing(
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
            table = screen.query_one("#track-table", DataTable)
            _focus(table, 0, "spot")
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, SoundPickerScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert device.sent == []
            preset = app._edit_preset
            assert preset is not None
            assert preset.tracks[0].sound_number == 0


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

    async def test_enter_selects_the_preset_on_the_unit(self):
        """Enter is a selection: Program Change first, read back, then open."""
        device = SelectingMultiDevice(start_slot=5)
        app = EnsqsqedApp(device, catalog=cat.empty())
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("p")
            assert await _wait(lambda: isinstance(app.screen, PresetListScreen), pilot)
            await pilot.press("enter")
            assert await _wait(lambda: app._multi_screen is not None, pilot)
            assert device.tap_calls == [2]
            assert device.pc_calls == [0]
            assert device.walk_calls == []
            assert app._edit_slot == 0
            assert app._edit_preset_raw[0] == 0
            assert "selected on the unit" in _app_status(app)

    async def test_picking_the_current_slot_sends_no_panel_key(self):
        """A pick of the slot the unit is already on is a read, nothing more.

        Sending Select Preset there reloads the preset and was what made
        a post-commit PSET01 show all-off again.
        """
        device = SelectingMultiDevice(start_slot=0)
        app = EnsqsqedApp(device, catalog=cat.empty())
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("p")
            assert await _wait(lambda: isinstance(app.screen, PresetListScreen), pilot)
            await pilot.press("enter")
            assert await _wait(lambda: app._multi_screen is not None, pilot)
            assert device.tap_calls == []
            assert device.pc_calls == []
            assert device.walk_calls == []
            assert app._edit_slot == 0

    async def test_a_program_change_that_does_not_take_walks_instead(self):
        """Preset Prog may be OFF or the PC may be ignored; the walk is the guard."""
        device = SelectingMultiDevice(pc_works=False, start_slot=5)
        app = EnsqsqedApp(device, catalog=cat.empty())
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("p")
            assert await _wait(lambda: isinstance(app.screen, PresetListScreen), pilot)
            await pilot.press("enter")
            assert await _wait(lambda: app._multi_screen is not None, pilot)
            assert device.tap_calls == [2]
            assert device.pc_calls == [0]
            assert device.walk_calls == [(0, 5)]
            assert app._edit_slot == 0

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
            # The unit's display counts its preset locations from
            # zero (P00 through P79, the owner's reading of the
            # display on 2026-10-22), and the dump's slots are counted
            # the same way.
            assert table.get_cell("0", "slot") == "P00"
            assert table.get_cell("79", "slot") == "P79"

    async def test_a_cancelled_list_loads_nothing(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        """A list that was not answered loads nothing and sends nothing."""
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("p")
            assert await _wait(lambda: isinstance(app.screen, PresetListScreen), pilot)
            await pilot.press("escape")
            await pilot.pause()
            assert app._edit_preset is None
            assert device.sent == []

    async def test_the_sentence_names_the_slot_the_preset_came_from(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        """The unit's own current preset has no slot, and says so."""
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("w")
            await pilot.pause()
            await pilot.press("p")
            assert await _wait(lambda: isinstance(app.screen, PresetListScreen), pilot)
            await pilot.press("enter")
            assert await _wait(lambda: app._multi_screen is not None, pilot)
            screen = app._multi_screen
            assert screen is not None
            await _open_mix_view(screen, pilot)
            await pilot.press("minus")
            assert await _wait(lambda: "into slot 1" in _status(screen), pilot)

    async def test_the_mode_note_does_not_invent_preset_prog(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        """The unit's Preset Prog is not readable, so it is not claimed.

        The owner's unit had Preset Prog ON while the old note said
        "Preset Prog is OFF (Sound mode)" -- a device state the app had
        never read.
        """
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("p")
            assert await _wait(lambda: isinstance(app.screen, PresetListScreen), pilot)
            screen = app.screen
            assert isinstance(screen, PresetListScreen)
            note = str(screen.query_one(".note", Static).content)
            assert "Preset Prog is OFF" not in note
            assert "does not report" in note
            assert "cannot read" in note or "Preset Prog" in note

    async def test_a_confirmed_preset_mode_says_it_is_the_last_mode_sent(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            app._remote_mode = "PRESET"
            app._remote_mode_confirmed = True
            await pilot.press("p")
            assert await _wait(lambda: isinstance(app.screen, PresetListScreen), pilot)
            screen = app.screen
            assert isinstance(screen, PresetListScreen)
            note = str(screen.query_one(".note", Static).content)
            assert "last sent Select Preset" in note
            assert "Preset Prog" in note
            assert "cannot read" in note
            assert "Preset Prog is OFF" not in note

    async def test_the_subtitle_does_not_claim_a_mode_before_m(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            assert "mode ?" in app.sub_title


class TestTheSentenceAfterASend:
    """What the app says after a send is a measurement, not a mood.

    The withdrawn claim -- that the unit does not play incoming MIDI
    while the Edit Preset is open, and that the change is heard only
    after the panel leaves Edit mode -- was confounded by the one-way
    *EXT* latch that ran before it.  The clean measurement says the
    send is live, the unit goes briefly deaf and answers again within
    about half a minute, and storing is the unit's own ENTER/SAVE.
    Both editors say it, and neither says the withdrawn claim.
    """

    async def test_the_track_editor_says_the_send_is_live(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            app._gate.armed = True
            await pilot.press("m")
            assert await _wait(lambda: app._multi_screen is not None, pilot)
            screen = app._multi_screen
            assert screen is not None
            await _open_mix_view(screen, pilot)
            await pilot.press("minus")
            assert await _wait(lambda: len(device.sent) == 1, pilot)
            assert await _wait(lambda: "sent." in _status(screen), pilot)
            said = _status(screen)
            # The send is live for levels; the arrangement waits for a
            # reload (the blackout claim was meter-based and is gone).
            assert "live for the sound's levels" in said
            assert "ctrl+r forces that reload remotely" in said
            # The remote store is the reselecting commit, not Enter/Save.
            assert "ctrl+s" in said
            assert "keeps its own preset name" in said
            # The withdrawn claims are gone.
            assert "does not play incoming MIDI" not in said
            assert "leave Edit mode" not in said
            assert "briefly deaf" not in said

    async def test_the_effect_editor_says_the_same(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            app._gate.armed = True
            await pilot.press("x")
            assert await _wait(lambda: app._fx_screen is not None, pilot)
            screen = app._fx_screen
            assert screen is not None
            table = screen.query_one("#fx-table", DataTable)
            table.move_cursor(row=1)
            await pilot.pause()
            await pilot.press("plus")
            assert await _wait(lambda: len(device.sent) == 1, pilot)
            assert await _wait(lambda: "sent." in _status(screen), pilot)
            said = _status(screen)
            assert "live for the sound's levels" in said
            assert "ctrl+r forces that reload remotely" in said
            assert "ctrl+s" in said
            assert "keeps its own preset name" in said
            assert "does not play incoming MIDI" not in said
            assert "briefly deaf" not in said


class TestTheUndoDispatch:
    """The undo log holds two kinds of record, and both must work.

    The preset editors push a **tuple** -- the whole preset as it was
    before the change -- and the browser's own favourite, tag, note and
    rating records are **dicts**.  The dispatcher tells them apart by
    the payload's type, so both have to keep working: the preset
    editors are newer, and a dispatch that only knew about the older
    shape would have quietly stopped undoing favourites.
    """

    async def test_a_preset_record_sends_the_whole_preset_back(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            app._gate.armed = True
            record = Record("track 1 volume 50→49", (SEQ_BLOB, PRESET_RAW))

            assert app._undo_apply(record) is True
            assert device.sent == [(SEQ_BLOB, PRESET_RAW)]

    async def test_a_preset_record_is_a_write_and_the_gate_says_no(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            app._gate.armed = False
            record = Record("track 1 volume 50→49", (SEQ_BLOB, PRESET_RAW))

            assert app._undo_apply(record) is False
            assert device.sent == []
            assert app._undo_reason is not None

    async def test_a_favourite_record_is_undone(
        self, device: FakeMultiDevice, tmp_path
    ):
        store = fav.Favorites(str(tmp_path / "f.db"))
        store.add("INT", 5, name="Invented", rating=3)
        app = EnsqsqedApp(device, catalog=cat.empty(), favorites=store)
        async with app.run_test() as pilot:
            await pilot.pause()
            record = Record(
                "favourite",
                {
                    "kind": "favourite",
                    "bank_id": "INT",
                    "number": 5,
                    "was": True,
                    "name": "Invented",
                },
            )

            assert app._undo_apply(record) is True
            assert store.get("INT", 5) is None
        store.close()

    async def test_a_tags_record_puts_the_old_tags_back(
        self, device: FakeMultiDevice, tmp_path
    ):
        store = fav.Favorites(str(tmp_path / "f.db"))
        store.add("INT", 5, name="Invented", tags="new")
        app = EnsqsqedApp(device, catalog=cat.empty(), favorites=store)
        async with app.run_test() as pilot:
            await pilot.pause()
            record = Record(
                "tags",
                {
                    "kind": "tags",
                    "bank_id": "INT",
                    "number": 5,
                    "old": "old",
                    "new": "new",
                },
            )

            assert app._undo_apply(record) is True
            restored = store.get("INT", 5)
            assert restored is not None
            assert restored.tags == "old"
        store.close()

    async def test_a_note_record_puts_the_old_note_back(
        self, device: FakeMultiDevice, tmp_path
    ):
        store = fav.Favorites(str(tmp_path / "f.db"))
        store.add("INT", 5, name="Invented", note="new")
        app = EnsqsqedApp(device, catalog=cat.empty(), favorites=store)
        async with app.run_test() as pilot:
            await pilot.pause()
            record = Record(
                "note",
                {
                    "kind": "note",
                    "bank_id": "INT",
                    "number": 5,
                    "old": "old",
                    "new": "new",
                },
            )

            assert app._undo_apply(record) is True
            restored = store.get("INT", 5)
            assert restored is not None
            assert restored.note == "old"
        store.close()

    async def test_a_rating_record_puts_the_old_rating_back(
        self, device: FakeMultiDevice, tmp_path
    ):
        store = fav.Favorites(str(tmp_path / "f.db"))
        store.add("INT", 5, name="Invented", rating=3)
        app = EnsqsqedApp(device, catalog=cat.empty(), favorites=store)
        async with app.run_test() as pilot:
            await pilot.pause()
            record = Record(
                "rating",
                {
                    "kind": "rating",
                    "bank_id": "INT",
                    "number": 5,
                    "old": 3,
                    "new": 0,
                },
            )

            assert app._undo_apply(record) is True
            restored = store.get("INT", 5)
            assert restored is not None
            assert restored.rating == 3
        store.close()

    async def test_a_record_of_an_unknown_kind_is_refused(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        """A record nobody knows about is not applied."""
        async with app.run_test() as pilot:
            await pilot.pause()
            record = Record("something else", {"kind": "nonsense"})

            assert app._undo_apply(record) is False
            assert device.sent == []


class TestTheUndoFromTheEditor:
    """`z` inside the editor is the app's undo, and it is a write."""

    async def test_capital_z_undoes_everything(self, app: EnsqsqedApp, device):
        """``Z`` is the app's undo-all, served by the editor's own key."""
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
            await pilot.press("Z")
            assert await _wait(lambda: len(device.sent) == 2, pilot)
            # The whole original 216 bytes go back.
            assert device.sent[1] == (SEQ_BLOB, PRESET_RAW)

    async def test_the_gate_is_asked_again_when_the_undo_is_pressed(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        """The change went out through the gate; the undo asks again.

        The gate can have been locked since the change went out, and
        the undo is a write like any other: it sends a preset, and a
        preset the unit refuses to take must not be sent at all.
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
            # The gate is locked between the change and the undo.
            app._gate.armed = False
            await pilot.press("z")
            assert await _wait(lambda: len(app._undo_log) == 1, pilot)
            # Nothing was sent, and the record is still there: the
            # preset it holds is still the one the unit has.
            assert len(device.sent) == 1
            assert len(app._undo_log) == 1

    async def test_an_undo_the_unit_refuses_keeps_its_record(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        """A refused undo stays on the log: the preset it holds is the
        unit's, and a retry has to be possible."""
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
            device.fail = TimeoutError("no Single Sequence Dump came back")
            await pilot.press("z")
            assert await _wait(lambda: len(app._undo_log) == 1, pilot)
            # Nothing more went out, and the record is still there.
            assert len(device.sent) == 1
            assert len(app._undo_log) == 1


class TestABurstOfEdits:
    """A burst of +/- presses is coalesced, not refused as "busy".

    The send takes a moment; a second press lands while the first is on
    the wire.  The sibling p2ked keeps one target slot for exactly this:
    the worker sends the last value next, and nothing is refused.
    """

    async def test_two_quick_bumps_both_land(
        self, app: EnsqsqedApp, device: FakeMultiDevice
    ):
        import time as clock

        original = device.send_preset

        def slow(*args, **kwargs):
            clock.sleep(0.2)
            return original(*args, **kwargs)

        device.send_preset = slow
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("x")
            assert await _wait(lambda: app._fx_screen is not None, pilot)
            screen = app._fx_screen
            assert screen is not None
            await pilot.press("w")  # arm the gate for this screen
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("+")
            await pilot.press("+")
            assert await _wait(lambda: len(device.sent) >= 2, pilot)
            said = _status(screen)
            assert "busy" not in said
            # The second press changed the value again before the first
            # send finished, so the two dumps differ.
            assert device.sent[0][1] != device.sent[1][1]


class TestRenameOnUnit:
    async def test_happy_path_through_r_key(
        self, rename_app: EnsqsqedApp, rename_device: RenameMultiDevice
    ):
        app = rename_app
        async with app.run_test() as pilot:
            await pilot.pause()
            app._gate.armed = True
            await pilot.press("m")
            assert await _wait(lambda: app._multi_screen is not None, pilot)
            screen = app._multi_screen
            assert screen is not None
            await pilot.press("R")
            await pilot.pause()
            await pilot.press("N", "E", "W")
            await pilot.press("enter")
            assert await _wait(lambda: "renamed on the unit" in _status(screen), pilot)
            assert rename_device.rename_calls == [(0, "NEW", False)]

    async def test_gate_refusal(
        self, rename_app: EnsqsqedApp, rename_device: RenameMultiDevice
    ):
        app = rename_app
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("m")
            assert await _wait(lambda: app._multi_screen is not None, pilot)
            screen = app._multi_screen
            assert screen is not None
            await pilot.press("R")
            await pilot.pause()
            await pilot.press("N", "E", "W")
            await pilot.press("enter")
            assert await _wait(
                lambda: (
                    "refused" in _status(screen).lower()
                    or "gate" in _status(screen).lower()
                ),
                pilot,
            )
            assert rename_device.rename_calls == []

    async def test_dirty_edit_uses_copy_path_and_clears(
        self, rename_app: EnsqsqedApp, rename_device: RenameMultiDevice
    ):
        app = rename_app
        async with app.run_test() as pilot:
            await pilot.pause()
            app._gate.armed = True
            await pilot.press("m")
            assert await _wait(lambda: app._multi_screen is not None, pilot)
            screen = app._multi_screen
            assert screen is not None
            app._edit_dirty = True
            app._rename_current_preset_on_unit("NewName")
            assert await _wait(lambda: "renamed on the unit" in _status(screen), pilot)
            # The app's own edits are dumps: the save dialog still asks
            # Replace edit preset?, so the copy path is the right one.
            assert rename_device.rename_calls[0][2] is False
            assert "current edit was stored too" in _status(screen)
            assert app._edit_dirty is False

    async def test_device_failure_keeps_dirty(
        self, rename_app: EnsqsqedApp, rename_device: RenameMultiDevice
    ):
        app = rename_app
        device = rename_device
        async with app.run_test() as pilot:
            await pilot.pause()
            app._gate.armed = True
            await pilot.press("m")
            assert await _wait(lambda: app._multi_screen is not None, pilot)
            screen = app._multi_screen
            assert screen is not None
            app._edit_dirty = True
            device.rename_fail = DeviceError("nope")
            app._rename_current_preset_on_unit("NewName")
            assert await _wait(
                lambda: (
                    "refused" in _status(screen).lower() or "nope" in _status(screen)
                ),
                pilot,
            )
            assert app._edit_dirty is True
