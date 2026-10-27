#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.

"""Capture ``docs/screenshots`` from the real SQ-R Plus, read-only.

Opens the hardware bridge once, takes four silent reads (internal names,
current sound, current preset, all eighty presets), closes the bridge, and
drives the Textual app headlessly against that in-memory snapshot.  No MIDI
port stays open during the capture, nothing is sent, the write gate stays
locked, and no shot confirms a write or picks a slot -- the unit's
selection and memory are untouched.

Usage::

    .venv/bin/python tools/capture_screenshots_hw.py
    .venv/bin/python tools/capture_screenshots_hw.py --output-dir /tmp/hwshots
    .venv/bin/python tools/capture_screenshots_hw.py --svg-only

The default ports and channel are this bench's wiring
(ESI M4U eX, base channel 11); override with ``--port``,
``--recv-port`` and ``--channel`` (0-based, as the bridge takes it).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from textual.widgets import DataTable

from ensqsqed.demo import DemoDevice
from ensqsqed.fx import FxScreen
from ensqsqed.multi import MultiScreen, PresetListScreen, SoundPickerScreen
from sq.bridge import AllPresetsDump, CurrentPreset
from tools.capture_screenshots import (
    BROWSER_SIZE,
    MODAL_SIZE,
    SCREEN_W,
    Output,
    SkipShot,
    capture,
    check,
    make_app,
    screen_text,
    wait_for_idle,
    wait_for_screen,
    wait_for_text,
)
from vinsynlib.ui.modals import SearchScreen

DEFAULT_PORT = "ESI M4U eX:ESI M4U eX MIDI 2 72:1"
DEFAULT_RECV_PORT = "ESI M4U eX:ESI M4U eX MIDI 1 72:0"
DEFAULT_CHANNEL = 10
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"


class HardwareSnapshotDevice(DemoDevice):
    """The real unit's state, snapshotted once, served without MIDI.

    Sound-bank methods come from :class:`DemoDevice` (never touched by
    the shots); every preset/noise-making method raises, so a shot that
    strayed onto the wire would fail loudly instead of writing.
    """

    def __init__(
        self,
        names: Dict[int, str],
        sound: bytes,
        current: CurrentPreset,
        dump: AllPresetsDump,
    ) -> None:
        super().__init__()
        self.description = "SQ-R Plus (hardware snapshot, read-only)"
        self._names = dict(names)
        self._sound = bytes(sound)
        self._current = current
        self._dump = dump

    def read_names(self, *, timeout: Optional[float] = None) -> Dict[int, str]:
        return dict(self._names)

    def read_internal_bank(
        self, *, timeout: Optional[float] = None
    ) -> Dict[int, bytes]:
        raise SkipShot("snapshot has names, not bank records")

    def read_current_sound(self, *, timeout: Optional[float] = None) -> bytes:
        return self._sound

    def read_current_preset(self, *, timeout: Optional[float] = None) -> CurrentPreset:
        return self._current

    def read_all_presets(self, *, timeout: Optional[float] = None) -> AllPresetsDump:
        return self._dump

    def select(self, target: Any) -> None:
        raise AssertionError("snapshot is read-only: refusing select")

    def send_preset(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("snapshot is read-only: refusing send")

    def select_preset(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("snapshot is read-only: refusing select")

    def select_preset_by_walk(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("snapshot is read-only: refusing walk")

    def tap_button(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("snapshot is read-only: refusing button")


def snapshot_from_hardware(
    port: str, recv_port: str, channel: int
) -> tuple[Dict[int, str], bytes, CurrentPreset, AllPresetsDump]:
    """Four silent reads, then the bridge is closed."""
    from sq.bridge import SqBridge

    bridge = SqBridge.open(port, recv_port=recv_port, channel=channel)
    try:
        names = bridge.read_names()
        print(f"  internal names: {len(names)}", flush=True)
        sound = bridge.read_current_sound()
        current = bridge.read_current_preset()
        print(f"  current slot: {current.preset_raw[0]}", flush=True)
        dump = bridge.read_all_presets()
        print(f"  all-presets dump: {dump.count} slots", flush=True)
        return names, sound, current, dump
    finally:
        bridge.close()


async def wait_for_startup(app: Any, pilot: Any, tries: int = 400) -> None:
    """Wait until the startup meter reports ready.

    The generic helper only waits out the "(about 12 s)" dump line, but
    the meter then moves through "(1/2 done)" phases; a shot taken
    then would freeze a progress line into the picture.
    """
    for _ in range(tries):
        text = screen_text(app.export_screenshot()).lower()
        if "startup: ready" in text:
            return
        await pilot.pause()
    raise AssertionError("startup never reported ready")


async def shot_browser(app: Any, pilot: Any, marker: str) -> None:
    await wait_for_startup(app, pilot)
    await wait_for_idle(app, pilot)
    await wait_for_text(app, pilot, marker)


async def shot_search(app: Any, pilot: Any) -> None:
    await wait_for_startup(app, pilot)
    await pilot.press("slash")
    await wait_for_screen(app, pilot, SearchScreen)
    await wait_for_text(app, pilot, "Search")


async def shot_help(app: Any, pilot: Any) -> None:
    await wait_for_startup(app, pilot)
    await pilot.press("question_mark")
    await wait_for_text(app, pilot, "What the SQ-R holds")


async def shot_device(app: Any, pilot: Any) -> None:
    await wait_for_startup(app, pilot)
    await pilot.press("i")
    await wait_for_text(app, pilot, "Ensoniq")


async def shot_favourites(app: Any, pilot: Any) -> None:
    await wait_for_startup(app, pilot)
    await pilot.press("f")
    await pilot.pause()
    await pilot.press("v")
    await pilot.pause()
    await wait_for_text(app, pilot, "favourite")


async def shot_rating_filter(app: Any, pilot: Any) -> None:
    await wait_for_startup(app, pilot)
    await pilot.press("V")
    await wait_for_text(app, pilot, "rating")


async def shot_import(app: Any, pilot: Any) -> None:
    await wait_for_startup(app, pilot)
    await pilot.press("I")
    await wait_for_text(app, pilot, "Import")


async def shot_backup(app: Any, pilot: Any) -> None:
    await wait_for_startup(app, pilot)
    await pilot.press("b")
    await wait_for_text(app, pilot, "Back up the SQ-R")


async def shot_remote(app: Any, pilot: Any) -> None:
    await wait_for_startup(app, pilot)
    await pilot.press("ctrl+o")
    await wait_for_text(app, pilot, "SELECT")


async def shot_multi(app: Any, pilot: Any) -> None:
    await wait_for_startup(app, pilot)
    await pilot.press("m")
    await wait_for_screen(app, pilot, MultiScreen)
    await wait_for_text(app, pilot, "Multi")


async def shot_preset_list(app: Any, pilot: Any, marker: str) -> None:
    await wait_for_startup(app, pilot)
    await pilot.press("p")
    await wait_for_screen(app, pilot, PresetListScreen)
    await wait_for_text(app, pilot, marker)


async def shot_fx(app: Any, pilot: Any) -> None:
    await wait_for_startup(app, pilot)
    await pilot.press("m")
    await wait_for_screen(app, pilot, MultiScreen)
    await pilot.press("x")
    await wait_for_screen(app, pilot, FxScreen)
    await wait_for_text(app, pilot, "Effect")


async def shot_sound_picker(app: Any, pilot: Any) -> None:
    await wait_for_startup(app, pilot)
    await pilot.press("m")
    await wait_for_screen(app, pilot, MultiScreen)
    screen = app._multi_screen
    if screen is None:
        raise AssertionError("multi screen missing")
    table = screen.query_one("#track-table", DataTable)
    for index, column in enumerate(table.columns.values()):
        if column.key == "spot":
            table.move_cursor(row=0, column=index)
            break
    await pilot.pause()
    await pilot.press("enter")
    await wait_for_screen(app, pilot, SoundPickerScreen)
    await wait_for_text(app, pilot, "Pick a sound")


async def shot_rename_prompt(app: Any, pilot: Any) -> None:
    await wait_for_startup(app, pilot)
    await pilot.press("m")
    await wait_for_screen(app, pilot, MultiScreen)
    await pilot.press("R")
    await wait_for_text(app, pilot, "Rename")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--svg-only", action="store_true")
    parser.add_argument("--width", type=int, default=SCREEN_W)
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--recv-port", default=DEFAULT_RECV_PORT)
    parser.add_argument("--channel", type=int, default=DEFAULT_CHANNEL)
    return parser


async def run(args: argparse.Namespace) -> int:
    from tools.capture_screenshots import svg2png

    names, sound, current, dump = snapshot_from_hardware(
        args.port, args.recv_port, args.channel
    )
    first_name = names[0].strip()
    print(f"  first internal name: {first_name!r}", flush=True)

    from sq.presets import Preset

    slot0_raw = dump.presets_raw[dump.slot_offsets()[0]]
    slot0_name = Preset.from_bytes(slot0_raw).name.strip()

    shots = [
        (
            "browser",
            lambda a, p: shot_browser(a, p, first_name),
            BROWSER_SIZE,
            [first_name],
        ),
        ("search", shot_search, MODAL_SIZE, ["Search"]),
        ("help", shot_help, MODAL_SIZE, ["What the SQ-R holds"]),
        ("device", shot_device, MODAL_SIZE, ["Ensoniq"]),
        ("favourites", shot_favourites, BROWSER_SIZE, ["favourite"]),
        ("rating-filter", shot_rating_filter, MODAL_SIZE, ["rating"]),
        ("import", shot_import, MODAL_SIZE, ["Import"]),
        ("backup", shot_backup, MODAL_SIZE, ["Back up the SQ-R"]),
        ("remote", shot_remote, MODAL_SIZE, ["SELECT"]),
        ("multi", shot_multi, BROWSER_SIZE, ["Multi"]),
        (
            "preset-list",
            lambda a, p: shot_preset_list(a, p, slot0_name),
            MODAL_SIZE,
            ["P00", slot0_name],
        ),
        ("fx", shot_fx, BROWSER_SIZE, ["Effect"]),
        ("sound-picker", shot_sound_picker, BROWSER_SIZE, ["Pick a sound"]),
        ("rename-prompt", shot_rename_prompt, MODAL_SIZE, ["Rename"]),
    ]

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    output = Output(out_dir, args.width, args.svg_only)
    skipped: list[str] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for name, fn, size, markers in shots:
            sub = tmp / name
            sub.mkdir(exist_ok=True)
            device = HardwareSnapshotDevice(names, sound, current, dump)
            app = make_app(sub, device)
            print(f"--- {name} ---", flush=True)
            try:
                async with app.run_test(size=size) as pilot:
                    await fn(app, pilot)
                    svg = await capture(app, pilot, name, output)
            except SkipShot as exc:
                print(f"  skipped: {exc}")
                skipped.append(name)
                continue
            check(name, svg, markers)
            print(screen_text(svg)[:600])
            print()

    print(f"Done. Screenshots in {out_dir}")
    if skipped:
        print(f"Skipped (not an error): {', '.join(skipped)}")
    if svg2png is None and not args.svg_only:
        print(
            "error: PNG conversion needs cairosvg, which is not installed. "
            "The SVGs above are complete.\n"
            "       .venv/bin/pip install -e '.[tools]'   (or pass --svg-only)",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.width < 1:
        raise SystemExit("error: --width must be at least 1")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
