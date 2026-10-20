# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# ensqsqed is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# ensqsqed is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE. See the GNU General Public License for more details.

"""The CLI's preset commands: the table, the files, and the gate.

**No hardware here.** The two dumps these commands read were captured
from a real SQ-R Plus and committed as ``tests/fixtures/all_seq.bin``
and ``tests/fixtures/single_seq_default.bin``, and the unit behind
them is a fake: one class, with the three methods the commands call.

What is pinned here is the *shape* of the output and the *bytes* of the
files. A preset's name is not part of the contract -- the names in the
fixtures are factory defaults -- so nothing here asserts on one.

The gate is pinned too, because it is the one thing in this file that
changes an instrument: ``send`` must not write without an answer.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

import pytest

from ensqsqed import cli
from sq import bridge as b
from sq import messages as m
from sq import presets as p

FIXTURES = Path(__file__).parent / "fixtures"

#: The captured single-preset dump: a 48-byte sequence blob and a
#: 168-byte preset.
SINGLE = (FIXTURES / "single_seq_default.bin").read_bytes()

#: The captured all-presets dump: eighty presets and the memory around
#: them.
ALL = (FIXTURES / "all_seq.bin").read_bytes()

#: The alert the captured all-presets dump was announced by.
ALL_ALERT = b.PresetAlert(
    command=m.CommandType.ALL_SEQUENCE_ALERT, size=0x10D4, count=80
)


def _single_preset() -> p.Preset:
    """The preset of the captured single-preset dump."""
    return p.Preset.from_bytes(p.split_single_dump(SINGLE)[1])


class FakeDevice:
    """The bridge, without a unit behind it.

    It answers the two reads the preset commands make, and it records
    what was sent -- and it can be told to refuse, or to come back with
    something other than what was sent, because both of those are
    outcomes the command has to report rather than assume away.
    """

    def __init__(self, channel: int = 10) -> None:
        self.channel = channel
        #: Every ``(sequence data, preset)`` pair that was sent.
        self.sent: List[Tuple[bytes, bytes]] = []
        #: Whether the next send is refused.
        self.refuse = False
        #: The offsets a next send comes back differing at.
        self.mismatch: Optional[Tuple[int, ...]] = None
        self.closed = False

    def read_current_preset(self) -> b.CurrentPreset:
        blob, raw = p.split_single_dump(SINGLE)
        return b.CurrentPreset(seq_blob=blob, preset_raw=raw)

    def read_all_presets(self) -> b.AllPresetsDump:
        return b.AllPresetsDump._from_reply(ALL, ALL_ALERT)

    def send_preset(
        self, seq_blob: bytes, preset_raw: bytes, *, verify: bool = True
    ) -> b.PresetSent:
        if self.refuse:
            raise b.PresetRefused(
                "the unit refused the preset dump: NAK -- busy, or the "
                "message was unintelligible"
            )
        self.sent.append((seq_blob, preset_raw))
        if self.mismatch is not None:
            raise b.PresetMismatch(
                "the unit did not read back what was sent",
                b.PresetSent(
                    acked=True,
                    verified=False,
                    diff_offsets=self.mismatch,
                    read_back=None,
                ),
            )
        return b.PresetSent(acked=True, verified=True, diff_offsets=(), read_back=None)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def device(monkeypatch: pytest.MonkeyPatch) -> FakeDevice:
    """A fake unit behind the CLI's one seam for opening one."""
    fake = FakeDevice()
    monkeypatch.setattr(cli, "_connect", lambda *args, **kwargs: fake)
    return fake


# --- the table ---------------------------------------------------------------


def test_the_multi_table_has_the_panel_columns() -> None:
    """Eight rows, and the columns the panel shows."""
    preset = _single_preset()
    rows = cli.track_rows(preset)

    assert len(rows) == 8
    assert [row[0] for row in rows] == [str(number) for number in range(1, 9)]
    assert all(len(row) == 11 for row in rows)


def test_the_report_leads_with_the_header_and_the_table() -> None:
    """The report is a header line, then the table."""
    preset = _single_preset()
    report = cli.multi_report(preset, "current preset (edit buffer)")
    lines = report.splitlines()

    assert lines[0].startswith("current preset (edit buffer)")
    header = [line for line in lines if line.startswith("Track ")]
    assert header, report
    assert header[0].split() == [
        "Track",
        "Status",
        "Channel",
        "Status-midi",
        "Program",
        "Sound",
        "Vol",
        "Pan",
        "Key",
        "Transpose",
        "Output",
    ]


def test_the_mask_is_the_panels_own() -> None:
    """One character per track: its number when it is on, a dash when not."""
    preset = _single_preset()
    mask = cli.pile_mask(preset)

    assert len(mask) == 8
    assert set(mask) <= set("12345678-")
    for number in range(1, 9):
        assert (mask[number - 1] == "-") is not preset.is_active(number)


def test_the_mask_follows_the_pile() -> None:
    """A track that the pile names is on, and the mask says so."""
    preset = _single_preset()
    before = cli.pile_mask(preset)
    preset.set_active(3, True)
    after = cli.pile_mask(preset)

    assert before[2] == "-"
    assert after[2] == "3"
    assert after.count("-") == before.count("-") - 1


def test_the_effect_block_has_a_name_a_mix_and_its_parameters() -> None:
    """The effect, its two mixes, and the rows of its parameter set."""
    preset = _single_preset()
    rows = cli.effect_rows(preset)
    fx1, fx2 = cli.effect_mix(preset)

    assert preset.effect.name in p.EFFECTS
    assert rows, "the effect has a parameter set"
    assert all(len(row) == 2 for row in rows)
    assert "DECAY TIME" in [row[0] for row in rows]
    assert fx1 and fx2


def test_the_report_says_what_multi_is() -> None:
    """The footer says what MULTI is, and where the setting is made."""
    report = cli.multi_report(_single_preset(), "current preset (edit buffer)")

    assert "MULTI" in report
    assert "System bank" in report
    assert "ENTER" in report


def test_an_unknown_effect_number_is_not_fatal() -> None:
    """A number this project has no name for is shown, not refused."""
    preset = _single_preset()
    preset.effect = p.Effect(number=31, params=preset.effect.params)
    report = cli.multi_report(preset, "current preset (edit buffer)")

    assert "effect" in report


# --- the files ----------------------------------------------------------------


def test_the_wire_bytes_round_trip(tmp_path: Path) -> None:
    """What is written is what can be read back, byte for byte."""
    blob, raw = p.split_single_dump(SINGLE)
    path = tmp_path / "preset.syx"
    path.write_bytes(cli.preset_wire(blob, raw, channel=10))

    loaded = cli.read_preset_file(str(path))

    assert loaded.seq_blob == blob
    assert loaded.preset_raw == raw
    assert loaded.device == 10


def test_a_file_that_is_not_a_preset_dump_is_refused(tmp_path: Path) -> None:
    """The refusals say what is wrong, not that something went wrong."""
    good = cli.preset_wire(*p.split_single_dump(SINGLE), channel=10)

    cases = {
        "empty.syx": b"",
        "foreign.syx": bytes([0xF0, 0x7D, 0x06, 0x00, 0x00, 0x04]) + b"\x00" * 8,
        "other.syx": m.frame(m.Function.ALL_SOUNDS_DUMP, b"\x00" * 8),
        "short.syx": good[:40],
    }
    for name, data in cases.items():
        path = tmp_path / name
        path.write_bytes(data)
        with pytest.raises(SystemExit) as caught:
            cli.read_preset_file(str(path))
        assert "error:" in str(caught.value), name


def test_multi_writes_the_raw_dump(tmp_path: Path, device: FakeDevice) -> None:
    """``multi --out`` writes the wire bytes of the alert and the dump."""
    path = tmp_path / "current.syx"
    assert cli.main(["multi", "--out", str(path)]) == 0

    wire = path.read_bytes()
    assert wire[:3] == bytes([0xF0, 0x0F, 0x06])
    loaded = cli.read_preset_file(str(path))
    assert loaded.preset_raw == p.split_single_dump(SINGLE)[1]


def test_presets_writes_the_all_dump(tmp_path: Path, device: FakeDevice) -> None:
    """``presets --all`` writes the type-05 dump, wire bytes and all."""
    path = tmp_path / "all.syx"
    assert cli.main(["presets", "--all", "--out", str(path)]) == 0

    wire = path.read_bytes()
    assert wire[0] == 0xF0
    assert wire[1] == 0x0F
    assert wire[2] == 0x06
    assert wire[5] == m.Function.ALL_SEQUENCES_DUMP
    # The payload de-nibbles back to the dump the unit sent.
    assert m.from_nibbles(wire[6:-1]) == ALL


def test_presets_names_the_file_it_wrote(
    tmp_path: Path, device: FakeDevice, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without ``--out`` the file has the documented name."""
    monkeypatch.chdir(tmp_path)
    assert cli.main(["presets"]) == 0

    assert (tmp_path / cli.DEFAULT_PRESET_FILE).exists()


def test_multi_reads_a_stored_preset(
    device: FakeDevice, capsys: pytest.CaptureFixture
) -> None:
    """``--preset N`` reads the all-presets dump, and says which slot."""
    assert cli.main(["multi", "--preset", "3"]) == 0
    out = capsys.readouterr().out

    assert "preset slot 3" in out
    assert out.count("\n") > 8


def test_a_stored_preset_is_paired_with_its_own_sequence() -> None:
    """The preset's first byte names its sequence, and the table has it.

    This is the pairing ``docs/PRESET-FRAMING.md`` section 4 describes:
    the preset table says which preset a slot holds, and the track-data
    table says where that preset's sequence is.  The fixture holds
    seventy empty sequences and ten songs, so a song's preset must come
    back with the longer blob.
    """
    dump = b.AllPresetsDump._from_reply(ALL, ALL_ALERT)

    for slot in (0, 10, 70, 79):
        preset, blob = cli.preset_from_dump(dump, slot)
        assert preset.seqno == slot
        assert int.from_bytes(blob[:4], "big") == len(blob)
        assert blob == cli.sequence_blob(dump, preset.seqno)

    # The songs are the ten at the end, and their blobs are longer.
    assert len(cli.sequence_blob(dump, 0)) == 48
    assert len(cli.sequence_blob(dump, 79)) == 54


def test_multi_writes_a_stored_preset_with_its_sequence(
    tmp_path: Path, device: FakeDevice
) -> None:
    """What is written for a stored preset is a sendable dump."""
    path = tmp_path / "slot.syx"
    assert cli.main(["multi", "--preset", "70", "--out", str(path)]) == 0

    loaded = cli.read_preset_file(str(path))
    assert len(loaded.seq_blob) == 54
    assert len(loaded.preset_raw) == p.PRESET_BYTES


def test_a_preset_slot_out_of_range_is_refused() -> None:
    """The slot is checked before anything is opened."""
    with pytest.raises(SystemExit):
        cli.main(["multi", "--preset", "80"])


# --- the gate -----------------------------------------------------------------


def _dump_file(tmp_path: Path) -> Path:
    """A file holding one preset dump, as ``presets --out`` writes it."""
    path = tmp_path / "preset.syx"
    path.write_bytes(cli.preset_wire(*p.split_single_dump(SINGLE), channel=10))
    return path


def test_send_asks_before_it_writes(
    tmp_path: Path, device: FakeDevice, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No answer, no write."""
    path = _dump_file(tmp_path)
    monkeypatch.setattr("builtins.input", lambda *args: "n")

    assert cli.main(["send", str(path)]) == 0
    assert device.sent == []


def test_send_writes_when_it_is_confirmed(
    tmp_path: Path, device: FakeDevice, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An answer, and the preset goes."""
    path = _dump_file(tmp_path)
    monkeypatch.setattr("builtins.input", lambda *args: "y")

    assert cli.main(["send", str(path)]) == 0
    assert len(device.sent) == 1
    assert device.sent[0][1] == p.split_single_dump(SINGLE)[1]


def test_send_with_yes_does_not_ask(
    tmp_path: Path, device: FakeDevice, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--yes`` is the answer, and it is not asked for again."""
    path = _dump_file(tmp_path)

    def refuse(*args: object) -> str:
        raise AssertionError("the command asked, although --yes was given")

    monkeypatch.setattr("builtins.input", refuse)

    assert cli.main(["send", "--yes", str(path)]) == 0
    assert len(device.sent) == 1


def test_send_reports_a_refusal(
    tmp_path: Path, device: FakeDevice, capsys: pytest.CaptureFixture
) -> None:
    """A NAK is reported, and nothing is claimed to have been sent."""
    path = _dump_file(tmp_path)
    device.refuse = True

    with pytest.raises(SystemExit) as caught:
        cli.main(["send", "--yes", str(path)])

    assert "error:" in str(caught.value)
    assert device.sent == []


def test_send_reports_a_mismatch_with_its_offsets(
    tmp_path: Path, device: FakeDevice, capsys: pytest.CaptureFixture
) -> None:
    """A read-back that is not what was sent is named, offset by offset."""
    path = _dump_file(tmp_path)
    device.mismatch = (82, 90)

    with pytest.raises(SystemExit) as caught:
        cli.main(["send", "--yes", str(path)])

    message = str(caught.value)
    assert "error:" in message
    assert "82" in message
    assert "90" in message


def test_send_says_that_the_preset_is_not_stored(
    tmp_path: Path, device: FakeDevice, capsys: pytest.CaptureFixture
) -> None:
    """What it wrote is the Edit Preset, and storing it is the unit's act."""
    path = _dump_file(tmp_path)

    assert cli.main(["send", "--yes", str(path)]) == 0
    out = capsys.readouterr().out

    assert "Edit Preset" in out
    assert "ENTER" in out
