# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional, Tuple

import pytest

from ensqsqed import cli
from sq import bridge as b
from sq import dumps, preset_labels
from sq import messages as m
from sq import presets as p

FIXTURES = Path(__file__).parent / "fixtures"
SINGLE = (FIXTURES / "single_seq_default.bin").read_bytes()
ALL = (FIXTURES / "all_seq.bin").read_bytes()
ALL_ALERT = b.PresetAlert(
    command=m.CommandType.ALL_SEQUENCE_ALERT, size=0x10D4, count=80
)


class FakeDevice:
    def __init__(self, channel: int = 0) -> None:
        self.channel = channel
        self.sent: List[Tuple[bytes, bytes]] = []
        self.refuse = False
        self.verified = True
        self.mismatch: Optional[Tuple[int, ...]] = None
        self.kept_offsets: Tuple[int, ...] = ()
        self.closed = False
        self.read_everything_calls = 0
        self.backup_sounds_calls: List[str] = []
        self.backup_presets_calls: List[str] = []

    def read_current_preset(self) -> b.CurrentPreset:
        blob, raw = p.split_single_dump(SINGLE)
        return b.CurrentPreset(seq_blob=blob, preset_raw=raw)

    def read_everything(self, *, timeout=None) -> object:
        self.read_everything_calls += 1
        return self._everything

    def backup_sounds(self, group: str) -> bytes:
        self.backup_sounds_calls.append(group)
        if group == "INT":
            raise AssertionError("backup_sounds called for INT")
        raise RuntimeError(f"card boom {group}")

    def backup_presets(self, group: str) -> bytes:
        self.backup_presets_calls.append(group)
        if group == "INT":
            raise AssertionError("backup_presets called for INT")
        raise RuntimeError(f"card boom {group}")

    def send_preset(
        self, seq_blob: bytes, preset_raw: bytes, *, verify: bool = True
    ) -> b.PresetSent:
        if self.refuse:
            raise b.PresetRefused("refused")
        self.sent.append((seq_blob, preset_raw))
        if self.mismatch is not None:
            raise b.PresetMismatch(
                "mismatch",
                b.PresetSent(
                    acked=True,
                    verified=False,
                    diff_offsets=self.mismatch,
                    read_back=None,
                ),
            )
        return b.PresetSent(
            acked=True,
            verified=self.verified,
            diff_offsets=(),
            read_back=None,
            kept_preset_offsets=self.kept_offsets,
        )

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def sounds_frame():
    sounds = [bytes((i + j) % 256 for j in range(204)) for i in range(80)]
    decoded = b"".join(sounds)
    return m.frame(m.Function.ALL_SOUNDS_DUMP, m.to_nibbles(decoded), device=0), sounds


@pytest.fixture
def frames(sounds_frame):
    sounds_frame_bytes, _ = sounds_frame
    seq_data = ALL
    seq_frame = m.frame(m.Function.ALL_SEQUENCES_DUMP, m.to_nibbles(seq_data), device=0)
    alert = b.PresetAlert(
        command=m.CommandType.ALL_SEQUENCE_ALERT, size=0x10D4, count=80
    )
    alert_frame = m.frame(m.Function.COMMAND, alert.wire_payload(), device=0)
    return sounds_frame_bytes, alert_frame, seq_frame


def _make_stub(frames_tuple):
    sounds_frame, alert_frame, seq_frame = frames_tuple
    stub = SimpleNamespace()
    stub.sounds_wire = sounds_frame
    stub.presets_wire = alert_frame + seq_frame

    def wire():
        return sounds_frame + alert_frame + seq_frame

    stub.wire = wire
    return stub


@pytest.fixture
def device(monkeypatch, frames):
    fake = FakeDevice(channel=0)
    fake._everything = _make_stub(frames)
    monkeypatch.setattr(cli, "_connect", lambda *args, **kwargs: fake)
    return fake


def test_backup_everything_syx(tmp_path, device, capsys):
    out = tmp_path / "everything.syx"
    rc = cli.main(["backup", "--groups", "ALL", "--content", "all", "--out", str(out)])
    assert rc == 0
    assert out.read_bytes() == device._everything.wire()
    # INT was not backed up via sounds/presets
    assert "INT" not in device.backup_sounds_calls
    assert "INT" not in device.backup_presets_calls


def test_backup_everything_midi(tmp_path, device, capsys):
    out = tmp_path / "everything.mid"
    rc = cli.main(
        [
            "backup",
            "--groups",
            "ALL",
            "--content",
            "all",
            "--out",
            str(out),
            "--format",
            "midi",
        ]
    )
    assert rc == 0
    data = out.read_bytes()
    assert data.startswith(b"MThd")
    assert b"MTrk" in data
    frames = dumps.sysex_from_smf(data)
    assert len(frames) == 3


def test_restore_noop(tmp_path, monkeypatch, capsys):
    blob, raw = p.split_single_dump(SINGLE)
    path = tmp_path / "preset.syx"
    path.write_bytes(cli.preset_wire(blob, raw, channel=0))

    fake = FakeDevice(channel=0)
    # read_current returns same as file
    fake.read_current_preset = lambda: b.CurrentPreset(seq_blob=blob, preset_raw=raw)
    monkeypatch.setattr(cli, "_connect", lambda *a, **k: fake)

    rc = cli.main(["restore", "--yes", str(path)])
    assert rc == 0
    assert fake.sent == []
    out = capsys.readouterr().out
    assert "nothing sent" in out


def test_restore_sends_and_reports_kept(tmp_path, monkeypatch, capsys):
    blob, raw = p.split_single_dump(SINGLE)
    candidate = bytearray(raw)
    candidate[34] ^= 1
    candidate_raw = bytes(candidate)
    path = tmp_path / "preset.syx"
    path.write_bytes(cli.preset_wire(blob, candidate_raw, channel=0))

    fake = FakeDevice(channel=0)
    fake.read_current_preset = lambda: b.CurrentPreset(seq_blob=blob, preset_raw=raw)
    fake.kept_offsets = (2, 3)
    monkeypatch.setattr(cli, "_connect", lambda *a, **k: fake)

    rc = cli.main(["restore", "--yes", str(path)])
    assert rc == 0
    assert len(fake.sent) == 1
    out = capsys.readouterr().out
    assert "offset(s) 2, 3" in out


def test_backup_writes_the_local_labels_next_to_the_dump(
    tmp_path, device, monkeypatch, capsys
):
    """The sidecar is copied for a presets/all backup, not for sounds."""
    source = tmp_path / "preset_labels.json"
    preset_labels.save({3: "BASS"}, str(source))
    monkeypatch.setattr(preset_labels, "default_path", lambda: str(source))
    out = tmp_path / "everything.syx"
    rc = cli.main(["backup", "--groups", "ALL", "--content", "all", "--out", str(out)])
    assert rc == 0
    labels = list(tmp_path.glob("preset_labels_*.json"))
    assert len(labels) == 1
    assert preset_labels.load(str(labels[0])) == {3: "BASS"}


def test_restore_a_labels_sidecar_without_the_unit(tmp_path, monkeypatch, capsys):
    """A .json restore is app-side only; it must not connect or touch the unit."""
    source = tmp_path / "labels.json"
    preset_labels.save({5: "PAD"}, str(source))
    target = tmp_path / "target.json"
    monkeypatch.setattr(preset_labels, "default_path", lambda: str(target))
    monkeypatch.setattr(
        cli,
        "_connect",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("connected")),
    )
    rc = cli.main(["restore", "--yes", str(source)])
    assert rc == 0
    assert preset_labels.load(str(target)) == {5: "PAD"}
    assert "unit is untouched" in capsys.readouterr().out
