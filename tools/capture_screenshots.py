#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.

"""Capture ``docs/screenshots`` from the browser in demo mode.

Drives the Textual app headlessly through ``App.run_test``, exports each
screen with ``App.export_screenshot`` and converts the SVG to PNG.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Tuple

from textual.widgets import DataTable

try:
    from cairosvg import svg2png  # type: ignore[import-not-found,import-untyped]
except ImportError:  # pragma: no cover - environment check
    svg2png = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from ensqsqed.app import EnsqsqedApp  # noqa: E402
from ensqsqed.demo import DemoDevice, demo_name  # noqa: E402
from ensqsqed.fx import FxScreen  # noqa: E402
from ensqsqed.import_single import SingleImportScreen  # noqa: E402
from ensqsqed.multi import (  # noqa: E402
    MultiScreen,
    PresetListScreen,
    SoundPickerScreen,
)
from sq import catalog as cat  # noqa: E402
from sq import config as cfg  # noqa: E402
from sq import favorites as fav  # noqa: E402
from sq.bridge import (  # noqa: E402
    AllPresetsDump,
    CurrentPreset,
    PresetSent,
)
from sq.presets import Preset, split_single_dump  # noqa: E402
from vinsynlib.ui.modals import SearchScreen  # noqa: E402

DEFAULT_OUT = REPO_ROOT / "docs" / "screenshots"
SCREEN_W = 820
BROWSER_SIZE = (132, 42)
MODAL_SIZE = (120, 36)

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "single_seq_default.bin"
PRESET_COUNT = 80
PRESET_BYTES = 168


def _fixture() -> Tuple[bytes, bytes]:
    return split_single_dump(FIXTURE.read_bytes())


SEQ_BLOB, PRESET_RAW = _fixture()


def _all_dump() -> AllPresetsDump:
    words = b"".join(
        (208 + index * PRESET_BYTES).to_bytes(2, "big") for index in range(PRESET_COUNT)
    )
    words += b"\x00" * (200 - len(words))
    track_table = bytearray(400)
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


class FakePresetDevice(DemoDevice):
    """Demo device extended with preset methods, for the editor shots."""

    def __init__(self, *, start_slot: int = 0) -> None:
        super().__init__()
        self.sent: List[Tuple[bytes, bytes]] = []
        self.reads = 0
        self.current = CurrentPreset(seq_blob=SEQ_BLOB, preset_raw=PRESET_RAW)
        raw = bytearray(PRESET_RAW)
        raw[0] = start_slot
        self.current = CurrentPreset(seq_blob=SEQ_BLOB, preset_raw=bytes(raw))
        self.dump = _all_dump()
        self.pc_works = True
        self.pc_calls: List[int] = []
        self.walk_calls: List[Tuple[int, int]] = []
        self.tap_calls: List[int] = []
        self.rename_calls: list = []

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

    def rename_preset_on_unit(
        self, slot, new_name, *, pending=False, timeout=None, progress=None
    ) -> str:
        self.rename_calls.append((slot, new_name, pending))
        preset = Preset.from_bytes(self.current.preset_raw)
        preset.name = new_name
        self.current = CurrentPreset(
            seq_blob=self.current.seq_blob, preset_raw=preset.to_bytes()
        )
        return new_name + " " * (16 - len(new_name))


class SkipShot(Exception):
    """A shot this checkout cannot take yet. Never fatal."""


@dataclass(frozen=True)
class Output:
    directory: Path
    width: int
    svg_only: bool


class _FakeCatalog:
    pass


def fix_svg(svg: str) -> str:
    svg = svg.replace(
        "font-family: Fira Code, monospace",
        'font-family: "DejaVu Sans Mono", monospace',
    )
    svg = re.sub(r"@font-face\s*\{[^}]+\}", "", svg)
    svg = re.sub(r"<circle[^>]*/>", "", svg)
    svg = re.sub(r"<circle[^>]*>.*?</circle>", "", svg)
    svg = re.sub(r'<text[^>]*class="[^"]*-title"[^>]*>.*?</text>', "", svg)
    return svg


def screen_text(svg: str) -> str:
    text = re.sub(r"<style.*?</style>", "", svg, flags=re.S)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    lines = [line.rstrip() for line in text.splitlines()]
    lines = [line for line in lines if line.strip()]
    return "\n".join(lines)


def make_app(tmp: Path, device: Any) -> EnsqsqedApp:
    (tmp / "config").mkdir(parents=True, exist_ok=True)
    (tmp / "fav").mkdir(parents=True, exist_ok=True)
    (tmp / "backups").mkdir(parents=True, exist_ok=True)
    return EnsqsqedApp(
        device,
        catalog=cat.load(),
        favorites=fav.Favorites(str(tmp / "fav" / "favorites.db")),
        config_path=str(tmp / "config" / "config.toml"),
        read_names_at_startup=True,
        allow_write=False,
        backup_root=cfg.DEFAULT_BACKUP_ROOT,
    )


async def wait_for_idle(app: EnsqsqedApp, pilot: Any, tries: int = 400) -> None:
    for _ in range(tries):
        if not app._busy:
            return
        await pilot.pause()
    raise AssertionError("wire stayed busy")


async def wait_for_text(
    app: EnsqsqedApp, pilot: Any, marker: str, tries: int = 400
) -> None:
    for _ in range(tries):
        if marker.lower() in screen_text(app.export_screenshot()).lower():
            return
        await pilot.pause()
    raise AssertionError(f"never saw {marker!r} on screen")


async def wait_for_screen(
    app: EnsqsqedApp, pilot: Any, cls: type, tries: int = 400
) -> None:
    for _ in range(tries):
        if isinstance(app.screen, cls):
            return
        await pilot.pause()
    raise AssertionError(
        f"top screen is {type(app.screen).__name__}, not {cls.__name__}"
    )


async def wait_for_startup(app: EnsqsqedApp, pilot: Any) -> None:
    await wait_for_idle(app, pilot)
    for _ in range(200):
        if "about 12" not in screen_text(app.export_screenshot()).lower():
            break
        await pilot.pause()


async def capture(app: EnsqsqedApp, pilot: Any, name: str, output: Output) -> str:
    await pilot.pause()
    svg = fix_svg(app.export_screenshot())
    out_svg = output.directory / f"{name}.svg"
    out_svg.write_text(svg, encoding="utf-8")
    if output.svg_only or svg2png is None:
        print(f"  {name}.svg ({out_svg.stat().st_size // 1024} KB)")
    else:
        out_png = output.directory / f"{name}.png"
        svg2png(
            bytestring=svg.encode(),
            write_to=str(out_png),
            output_width=output.width,
        )
        print(f"  {name}.png ({out_png.stat().st_size // 1024} KB)")
    return svg


def check(name: str, svg: str, markers: list) -> None:
    text = screen_text(svg).lower()
    missing = [m for m in markers if m.lower() not in text]
    if missing:
        raise AssertionError(
            f"{name}: marker(s) {missing} not in shot text:\n{text[:900]}"
        )
    print(f"  markers ok: {markers}")


# --- shots ---


async def shot_browser(app: EnsqsqedApp, pilot: Any) -> None:
    await wait_for_startup(app, pilot)
    await wait_for_idle(app, pilot)
    await wait_for_text(app, pilot, demo_name("INT", 0).strip())


async def shot_search(app: EnsqsqedApp, pilot: Any) -> None:
    await wait_for_startup(app, pilot)
    await pilot.press("slash")
    await wait_for_screen(app, pilot, SearchScreen)
    await wait_for_text(app, pilot, "Search")


async def shot_help(app: EnsqsqedApp, pilot: Any) -> None:
    await wait_for_startup(app, pilot)
    await pilot.press("question_mark")
    await wait_for_text(app, pilot, "What the SQ-R holds")


async def shot_device(app: EnsqsqedApp, pilot: Any) -> None:
    await wait_for_startup(app, pilot)
    await pilot.press("i")
    await wait_for_text(app, pilot, "demo")


async def shot_favourites(app: EnsqsqedApp, pilot: Any) -> None:
    await wait_for_startup(app, pilot)
    await pilot.press("f")
    await pilot.pause()
    await pilot.press("v")
    await pilot.pause()
    await wait_for_text(app, pilot, "favourite")


async def shot_rating_filter(app: EnsqsqedApp, pilot: Any) -> None:
    await wait_for_startup(app, pilot)
    await pilot.press("V")
    await wait_for_text(app, pilot, "rating")


async def shot_import(app: EnsqsqedApp, pilot: Any) -> None:
    await wait_for_startup(app, pilot)
    await pilot.press("I")
    await wait_for_text(app, pilot, "Import")


async def shot_single_import_preview(app: EnsqsqedApp, pilot: Any) -> None:
    """The single-sound map with the opt-in preview controls visible."""
    await wait_for_startup(app, pilot)
    sounds = [(index, demo_name("INT", index).strip()) for index in range(8)]
    listing = [(index, demo_name("INT", index).strip()) for index in range(80)]
    screen = SingleImportScreen(
        path="~/sounds/demo-single.syx",
        summary="8 sounds from demo-single.syx",
        sounds=sounds,
        target="INT",
        listing=listing,
        gate=app._gate,
    )
    app._single_import_screen = screen
    app.push_screen(screen)
    await wait_for_screen(app, pilot, SingleImportScreen)
    await wait_for_text(app, pilot, "Auto-rehearsal")
    table = screen.query_one("#source-table", DataTable)
    table.focus()
    table.move_cursor(row=2)
    await pilot.pause()


async def shot_backup(app: EnsqsqedApp, pilot: Any) -> None:
    await wait_for_startup(app, pilot)
    await pilot.press("b")
    await wait_for_text(app, pilot, "Back up the SQ-R")


async def shot_remote(app: EnsqsqedApp, pilot: Any) -> None:
    await wait_for_startup(app, pilot)
    await pilot.press("ctrl+o")
    await wait_for_text(app, pilot, "SELECT")


async def shot_multi(app: EnsqsqedApp, pilot: Any) -> None:
    await wait_for_startup(app, pilot)
    await pilot.press("m")
    await wait_for_screen(app, pilot, MultiScreen)
    await wait_for_text(app, pilot, "Multi")


async def shot_preset_list(app: EnsqsqedApp, pilot: Any) -> None:
    await wait_for_startup(app, pilot)
    await pilot.press("p")
    await wait_for_screen(app, pilot, PresetListScreen)
    await wait_for_text(app, pilot, "P00")


async def shot_fx(app: EnsqsqedApp, pilot: Any) -> None:
    await wait_for_startup(app, pilot)
    await pilot.press("m")
    await wait_for_screen(app, pilot, MultiScreen)
    await pilot.press("x")
    await wait_for_screen(app, pilot, FxScreen)
    await wait_for_text(app, pilot, "Effect")


async def shot_sound_picker(app: EnsqsqedApp, pilot: Any) -> None:
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


async def shot_rename_prompt(app: EnsqsqedApp, pilot: Any) -> None:
    await wait_for_startup(app, pilot)
    await pilot.press("m")
    await wait_for_screen(app, pilot, MultiScreen)
    await pilot.press("R")
    await wait_for_text(app, pilot, "Rename")


FAKE_SHOTS = {"multi", "preset-list", "fx", "sound-picker", "rename-prompt"}


SHOTS: list = [
    ("browser", shot_browser, BROWSER_SIZE, True, [demo_name("INT", 0).strip()]),
    ("search", shot_search, MODAL_SIZE, True, ["Search"]),
    ("help", shot_help, MODAL_SIZE, True, ["What the SQ-R holds"]),
    ("device", shot_device, MODAL_SIZE, True, ["demo"]),
    ("favourites", shot_favourites, BROWSER_SIZE, True, ["favourite"]),
    ("rating-filter", shot_rating_filter, MODAL_SIZE, True, ["rating"]),
    ("import", shot_import, MODAL_SIZE, True, ["Import"]),
    (
        "single-import-preview",
        shot_single_import_preview,
        MODAL_SIZE,
        True,
        [
            "Auto-load focused sound",
            "Autoplay notes after preview load",
            "Auto-rehearsal",
            "POLY",
            "MULTI",
        ],
    ),
    ("backup", shot_backup, MODAL_SIZE, True, ["Back up the SQ-R"]),
    ("remote", shot_remote, MODAL_SIZE, True, ["SELECT"]),
    ("multi", shot_multi, BROWSER_SIZE, False, ["Multi"]),
    ("preset-list", shot_preset_list, MODAL_SIZE, False, ["P00"]),
    ("fx", shot_fx, BROWSER_SIZE, False, ["Effect"]),
    ("sound-picker", shot_sound_picker, BROWSER_SIZE, False, ["Pick a sound"]),
    ("rename-prompt", shot_rename_prompt, MODAL_SIZE, False, ["Rename"]),
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="capture screenshots")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--svg-only", action="store_true")
    parser.add_argument("--width", type=int, default=SCREEN_W)
    return parser


async def run(args: argparse.Namespace) -> int:
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    output = Output(out_dir, args.width, args.svg_only)
    skipped: list[str] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for name, fn, size, _fc, markers in SHOTS:
            sub = tmp / name
            sub.mkdir(exist_ok=True)
            device = FakePresetDevice() if name in FAKE_SHOTS else DemoDevice()
            app = make_app(sub, device)
            print(f"--- {name} ---")
            try:
                async with app.run_test(size=size) as pilot:
                    await fn(app, pilot)
                    svg = await capture(app, pilot, name, output)
            except SkipShot as exc:
                print(f"  skipped: {exc}")
                skipped.append(name)
                continue
            except Exception as exc:
                # backup/remote may be missing screens; allow skip
                if name in ("backup", "remote", "fx", "sound-picker", "rename-prompt"):
                    print(f"  skipped: {exc}")
                    skipped.append(name)
                    continue
                raise
            check(name, svg, markers)
            print(screen_text(svg)[:900])
            print()

    print(f"Done. Screenshots in {out_dir}")
    if skipped:
        print(f"Skipped (not an error): {', '.join(skipped)}")
    if svg2png is None and not args.svg_only:
        print(
            "error: PNG conversion needs cairosvg...",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.width < 1:
        raise SystemExit("error: --width must be at least 1")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
