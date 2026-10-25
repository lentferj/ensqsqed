# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#

"""The remote store: the warning, the key, and the green verification."""

from __future__ import annotations

from typing import List

from textual.app import App
from textual.widgets import Static

from ensqsqed.app import EnsqsqedApp
from ensqsqed.fx import FxScreen
from ensqsqed.multi import MultiScreen
from sq import catalog as cat
from sq.bridge import PresetCommit
from sq.presets import Effect, Preset


class _Host(App[None]):
    def __init__(self, screen) -> None:
        super().__init__()
        self._screen = screen

    def on_mount(self) -> None:
        self.push_screen(self._screen)


def _preset() -> Preset:
    preset = Preset()
    preset.effect = Effect(
        number=0,
        params=bytes([0x38, 0x06, 0x20, 0x10, 0x25, 0x00, 0x8F, 0x54, 0x20, 0x20]),
    )
    return preset


def _commit(
    slot: int, working: bytes, stored: bytes, after: bytes | None = None
) -> PresetCommit:
    return PresetCommit(
        slot=slot,
        neighbour=(slot + 1) % 80,
        stored_raw=stored,
        working_raw=working,
        after_raw=stored if after is None else after,
    )


# --- the app's completion handler --------------------------------------------


def test_a_matched_commit_is_reported_green() -> None:
    app = EnsqsqedApp(None, catalog=cat.empty())
    seen: List[tuple] = []
    app._say_to_editor = lambda message, **kw: seen.append((message, kw))  # type: ignore[assignment]
    raw = b"\x00" * 168

    app._commit_preset_done(10, None, _commit(10, raw, raw))

    assert seen
    assert seen[0][1].get("success") is True
    assert "verified" in seen[0][0]
    assert "P10" in seen[0][0]


def test_a_differing_commit_is_reported_as_a_refusal() -> None:
    app = EnsqsqedApp(None, catalog=cat.empty())
    seen: List[tuple] = []
    app._say_to_editor = lambda message, **kw: seen.append((message, kw))  # type: ignore[assignment]
    working = bytearray(168)
    working[34] = 99  # track 1 volume: a byte a commit does write
    stored = bytes(168)

    app._commit_preset_done(10, None, _commit(10, bytes(working), stored))

    assert seen
    assert seen[0][1].get("refused") is True
    assert "not verified" in seen[0][0]
    assert "34" in seen[0][0]


def test_a_commit_that_only_looks_right_in_the_dump_is_refused() -> None:
    """The reloaded preset is the second witness.

    A slot's dump entry can look right while the preset the unit reloads
    from it does not; the panel works from the reload.  A commit that
    only the dump confirms is not reported as verified.
    """
    app = EnsqsqedApp(None, catalog=cat.empty())
    seen: List[tuple] = []
    app._say_to_editor = lambda message, **kw: seen.append((message, kw))  # type: ignore[assignment]
    working = bytearray(168)
    working[34] = 99
    stored = bytes(working)
    after = bytearray(stored)
    after[34] = 0

    app._commit_preset_done(10, None, _commit(10, bytes(working), stored, bytes(after)))

    assert seen
    assert seen[0][1].get("refused") is True
    assert "reloaded" in seen[0][0]
    assert "34" in seen[0][0]


def test_a_unit_kept_track_status_byte_does_not_refuse_the_commit() -> None:
    """Offset 68 is track 3's Status/Pressure/channel.

    The commit copies the live engine's track records, and the Track-pair
    chord writes that byte live.  The on/off arrangement still landed, so
    the commit is a success with a note about the unit's own value, not a
    refusal over a status word the app cannot write through the commit.
    """
    app = EnsqsqedApp(None, catalog=cat.empty())
    seen: List[tuple] = []
    app._say_to_editor = lambda message, **kw: seen.append((message, kw))  # type: ignore[assignment]
    working = bytearray(168)
    working[68] = 0x52  # track 3: Status LOCAL, Pressure KEY, channel 2
    stored = bytearray(working)
    stored[68] = 0x12  # the live engine kept Status BOTH

    app._commit_preset_done(
        10, None, _commit(10, bytes(working), bytes(stored), bytes(stored))
    )

    assert seen
    assert seen[0][1].get("success") is True
    assert "offset(s) 68" in seen[0][0]
    assert "track 3" in seen[0][0]
    assert "BOTH" in seen[0][0]


def test_an_ext_stored_status_is_called_out() -> None:
    """A kept *EXT* is a warning, because it silences voices after a reload."""
    app = EnsqsqedApp(None, catalog=cat.empty())
    seen: List[tuple] = []
    app._say_to_editor = lambda message, **kw: seen.append((message, kw))  # type: ignore[assignment]
    working = bytearray(168)
    working[68] = 0x52
    stored = bytearray(working)
    stored[68] = 0xD2  # Status *EXT

    app._commit_preset_done(
        10, None, _commit(10, bytes(working), bytes(stored), bytes(stored))
    )

    assert seen
    assert seen[0][1].get("success") is True
    assert "*EXT*" in seen[0][0]
    assert "silences" in seen[0][0]


def test_a_kept_program_byte_does_not_refuse_the_commit() -> None:
    """Offset 55 is track 2's ``sustain<<7 | program`` byte.

    The firmware's store saves and restores those eight bytes from the
    slot's own values (``$C128FA``), so a sent program/sustain is never
    adopted.  The on/off arrangement can still land; the commit reports
    the kept byte instead of refusing.
    """
    app = EnsqsqedApp(None, catalog=cat.empty())
    seen: List[tuple] = []
    app._say_to_editor = lambda message, **kw: seen.append((message, kw))  # type: ignore[assignment]
    working = bytearray(168)
    working[55] = 0x81  # track 2: sustain on, program 1
    stored = bytearray(working)
    stored[55] = 0x80  # the slot kept program 0

    app._commit_preset_done(
        10, None, _commit(10, bytes(working), bytes(stored), bytes(stored))
    )

    assert seen
    assert seen[0][1].get("success") is True
    assert "offset(s) 55" in seen[0][0]
    assert "track 2 program 0" in seen[0][0]


def test_a_pile_that_did_not_land_names_the_live_pile() -> None:
    """Offset 144 is the pile's first byte.

    A dump changes the working preset; the store commits the unit's live
    pile, which the app cannot set.  The refusal must say that instead of
    calling the receiver flaky.
    """
    app = EnsqsqedApp(None, catalog=cat.empty())
    seen: List[tuple] = []
    app._say_to_editor = lambda message, **kw: seen.append((message, kw))  # type: ignore[assignment]
    working = bytearray(168)
    working[144] = 0x02  # the app wants track 2 as the first ON track
    stored = bytearray(working)
    stored[144] = 0x01  # the live pile stayed track 1

    app._commit_preset_done(
        10, None, _commit(10, bytes(working), bytes(stored), bytes(stored))
    )

    assert seen
    assert seen[0][1].get("refused") is True
    assert "ON list" in seen[0][0]
    assert "pile stayed" in seen[0][0]
    assert "02 00" in seen[0][0]
    assert "01 00" in seen[0][0]
    assert "flaky" not in seen[0][0]


def test_the_gate_must_be_armed() -> None:
    app = EnsqsqedApp(None, catalog=cat.empty())
    seen: List[tuple] = []
    app._say_to_editor = lambda message, **kw: seen.append((message, kw))  # type: ignore[assignment]
    app._write_gate.armed = False

    app._commit_preset()

    assert seen
    assert seen[0][1].get("refused") is True
    assert "write gate" in seen[0][0]


# --- the effect editor --------------------------------------------------------


async def test_the_effect_editor_warns_and_offers_the_key() -> None:
    calls: List[bool] = []
    screen = FxScreen(
        _preset(),
        on_apply=lambda preset, description: None,
        on_commit=lambda: calls.append(True),
    )
    app = _Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        note = screen.query_one("#fx-store-note", Static)
        text = str(note.render())
        # Note: ctrl+s is no longer available in FxScreen - store via multi window
        assert "Store the preset by re-selecting its slot" in text
        assert "re-selecting" in text
        # FxScreen no longer has ctrl+s binding
        assert not any(binding.key == "ctrl+s" for binding in screen.BINDINGS)
        await pilot.press("ctrl+s")
        await pilot.pause()
    # FxScreen points at the multi window's key and does not store by
    # itself, so the key is inert here rather than a second write path.
    assert calls == []


async def test_a_green_status_is_set_for_a_verified_store() -> None:
    screen = FxScreen(_preset(), on_apply=lambda preset, description: None)
    app = _Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.say("P10 committed and verified", ok=True)
        await pilot.pause()
        status = screen.query_one("#fx-status", Static)
        assert status.has_class("ok")
        assert not status.has_class("refused")
        screen.say("nope", refused=True)
        await pilot.pause()
        assert status.has_class("refused")
        assert not status.has_class("ok")


# --- the multi editor ---------------------------------------------------------


async def test_the_multi_editor_warns_and_offers_the_key() -> None:
    calls: List[bool] = []
    screen = MultiScreen(
        _preset(),
        on_apply=lambda preset, description: None,
        on_undo=lambda count: None,
        on_sound_picked=lambda index, number: None,
        on_commit=lambda: calls.append(True),
    )
    app = _Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        note = screen.query_one("#multi-store-note", Static)
        text = str(note.render())
        assert "ctrl+s" in text
        assert "Enter/Save" in text
        assert any(binding.key == "ctrl+s" for binding in screen.BINDINGS)
        await pilot.press("ctrl+s")
        await pilot.pause()
    assert calls == [True]
