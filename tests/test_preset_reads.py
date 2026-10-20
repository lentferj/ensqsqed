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
# ensqsqed is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
# more details.

"""Reading presets: the framing, and the sentences when it does not work.

**No hardware here.** The two dumps this parses were captured from a real
SQ-R Plus and committed as ``tests/fixtures/all_seq.bin`` and
``tests/fixtures/single_seq_default.bin``, and the wire is faked with two
small objects that answer a request the way the unit does: an alert
first, then the dump.

What is pinned here is the *layout*, because that is the part that is
easy to get quietly wrong. A preset dump has no checksum and no length
of its own beyond the alert that announces it, so the only way to know
that a parser is reading the right bytes is to put the dump back
together from the parsed parts and compare. That is what
``test_all_presets_dump_rebuilds_byte_for_byte`` does, and it is the
test that would notice if the parser started believing the alert's size
where it should believe the count.

The layout these tests pin is the one in ``docs/PRESET-FRAMING.md``,
which was written against the same fixture. Nothing here asserts on a
name: the names in the fixture are factory defaults, and a name is not
part of the contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from sq import bridge as b
from sq import messages as m

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> bytes:
    """The bytes of one captured dump."""
    return (FIXTURES / name).read_bytes()


#: The alert of the captured all-presets dump: command 07, size 0x10D4,
#: count 0x50 (80 defined presets).
ALL_ALERT = b.PresetAlert(
    command=m.CommandType.ALL_SEQUENCE_ALERT, size=0x10D4, count=80
)

#: The alert of the captured single-preset dump: command 06, size 0x30
#: (48 bytes of sequence data), flag 0 (a sequence, not a song).
ONE_ALERT = b.PresetAlert(
    command=m.CommandType.SINGLE_SEQUENCE_ALERT, size=0x30, flag=0
)


def _rebuild(dump: b.AllPresetsDump) -> bytes:
    """The all-presets dump, byte for byte, out of the parsed fields.

    The one wrinkle is the track data: ``track_blobs`` is in *table*
    order, and the unit does not lay its sequences out in that order,
    so each blob is placed at the offset its table entry gives.
    """
    header = b.TRACK_ALLOC_BYTES + b.TRACK_TABLE_BYTES
    region = bytearray(dump.preset_area_start - b.PRESET_TABLE_BYTES - header)
    blobs = iter(dump.track_blobs)
    for index in range(len(dump.track_table) // 4):
        offset = int.from_bytes(dump.track_table[index * 4 : index * 4 + 4], "big")
        if not offset:
            continue
        blob = next(blobs)
        region[offset - header : offset - header + len(blob)] = blob
    return (
        dump.alloc
        + dump.track_table
        + bytes(region)
        + dump.preset_table
        + b"".join(dump.presets_raw)
        + dump.globals
    )


class _FakeIn:
    """A MIDI input that hands out the messages it was given."""

    def __init__(self) -> None:
        self.inbox: List[bytes] = []
        self.ignored = False

    def ignore_types(self, **kwargs) -> None:
        self.ignored = True

    def get_message(self):
        if not self.inbox:
            return None
        return (self.inbox.pop(0), 0)


class _FakeOut:
    """A MIDI output that answers the request the way the unit does.

    The replies are delivered when the request goes out, not before:
    the bridge drains its input before sending, and a unit that had
    already answered would look like a unit that answered something
    else.
    """

    def __init__(self, port: _FakeIn, replies: List[bytes]) -> None:
        self.port = port
        self.replies = list(replies)
        self.sent: List[bytes] = []

    def send_message(self, data) -> None:
        self.sent.append(bytes(data))
        self.port.inbox.extend(self.replies)


def _bridge(replies: List[bytes], *, timeout: float = 0.05):
    """A bridge whose unit answers with ``replies``."""
    port = _FakeIn()
    sink = _FakeOut(port, replies)
    return b.SqBridge(port, sink, channel=0, timeout=timeout), sink


def _alert_message(alert: b.PresetAlert) -> bytes:
    """The alert as the unit puts it on the wire."""
    return m.frame(m.Function.COMMAND, alert.wire_payload(), device=0)


def _dump_message(function: int, data: bytes) -> bytes:
    """A dump as the unit puts it on the wire."""
    return m.frame(function, m.to_nibbles(data), device=0)


# --- the all-presets dump ----------------------------------------------------


def test_all_presets_dump_from_the_fixture():
    """The captured dump parses into the layout the doc describes."""
    data = _fixture("all_seq.bin")
    dump = b.AllPresetsDump._from_reply(data, ALL_ALERT)

    assert dump.count == 80
    # The one rule the doc gives: the presets sit 21 + 168 * count
    # bytes before the end of the dump.
    assert dump.preset_area_start == len(data) - 21 - 168 * 80 == 4516
    assert len(dump.presets_raw) == 80
    assert all(len(preset) == 168 for preset in dump.presets_raw)
    assert len(dump.globals) == 21
    assert dump.globals == data[-21:]
    assert dump.alloc == data[:8]
    assert dump.track_table == data[8:408]
    assert dump.preset_table == data[4308:4516]


def test_all_presets_blobs_are_the_sequences_the_table_points_at():
    """Eighty blobs: seventy empty sequences and ten songs."""
    data = _fixture("all_seq.bin")
    dump = b.AllPresetsDump._from_reply(data, ALL_ALERT)

    assert len(dump.track_blobs) == 80
    sizes = [len(blob) for blob in dump.track_blobs]
    # The table's first seventy entries are the empty sequences, its
    # last ten the songs. The blob says how long it is, in its own
    # first four bytes.
    assert sizes[:70] == [48] * 70
    assert sizes[70:] == [54] * 10
    for blob in dump.track_blobs:
        assert int.from_bytes(blob[:4], "big") == len(blob)


def test_all_presets_dump_rebuilds_byte_for_byte():
    """The parsed fields put the dump back together, unchanged.

    This is the framing regression test: if the parser ever believes
    the alert's size where the count belongs, or takes the presets
    from the wrong end, this is what notices.
    """
    data = _fixture("all_seq.bin")
    dump = b.AllPresetsDump._from_reply(data, ALL_ALERT)

    assert _rebuild(dump) == data


def test_all_presets_slot_offsets_come_from_the_word_table():
    """A slot's preset is where the word table says it is.

    The table's entries are offsets from the start of the table's own
    allocation variables, and the ten song presets sit at the front of
    the preset area while their slots are the last ten.
    """
    data = _fixture("all_seq.bin")
    dump = b.AllPresetsDump._from_reply(data, ALL_ALERT)

    offsets = dump.slot_offsets()
    assert len(offsets) == 80
    assert offsets[0] == 10
    assert offsets[70] == 0
    for slot, physical in offsets.items():
        # The preset in that place names that slot in its first byte.
        assert dump.presets_raw[physical][0] == slot


def test_all_presets_needs_room_for_its_own_layout():
    """A dump too short for its own count is refused, with both numbers."""
    data = _fixture("all_seq.bin")[:1000]
    with pytest.raises(b.DeviceError) as caught:
        b.AllPresetsDump._from_reply(data, ALL_ALERT)
    assert "1000" in str(caught.value)
    assert "80" in str(caught.value)


def test_all_presets_refuses_an_alert_that_is_not_its_own():
    """The single-preset alert is not an all-presets alert."""
    with pytest.raises(b.DeviceError):
        b.AllPresetsDump._from_reply(_fixture("all_seq.bin"), ONE_ALERT)


# --- the current preset ------------------------------------------------------


def test_current_preset_from_the_fixture():
    """The captured single dump is a 48-byte blob and a 168-byte preset."""
    data = _fixture("single_seq_default.bin")
    assert len(data) == 216

    current = b.CurrentPreset.from_reply(data, ONE_ALERT.size)
    assert len(current.seq_blob) == 48
    assert len(current.preset_raw) == 168
    assert current.seq_blob == data[:48]
    assert current.preset_raw == data[48:]
    # The blob begins with its own length, and the preset follows it.
    assert int.from_bytes(current.seq_blob[:4], "big") == 48


def test_current_preset_refuses_a_size_that_does_not_add_up():
    """The alert's size and the dump's length have to agree."""
    data = _fixture("single_seq_default.bin")
    with pytest.raises(b.DeviceError) as caught:
        b.CurrentPreset.from_reply(data, 54)
    message = str(caught.value)
    assert "216" in message
    assert "54" in message


# --- the alert ---------------------------------------------------------------


def test_alert_parsing_of_a_single_preset_alert():
    """Command 06: a size and a sequence/song flag."""
    raw = bytes([0x06, 0x00, 0x00, 0x00, 0x30, 0x00])
    alert = b.PresetAlert.from_bytes(raw)

    assert alert.command == m.CommandType.SINGLE_SEQUENCE_ALERT
    assert alert.size == 0x30
    assert alert.size == 48
    assert alert.flag == 0
    assert alert.count == 0
    assert not alert.is_song


def test_alert_parsing_of_an_all_presets_alert():
    """Command 07: a size and a count of defined presets."""
    raw = bytes([0x07, 0x00, 0x00, 0x10, 0xD4, 0x50])
    alert = b.PresetAlert.from_bytes(raw)

    assert alert.command == m.CommandType.ALL_SEQUENCE_ALERT
    assert alert.size == 0x10D4
    assert alert.size == 4308
    assert alert.count == 0x50
    assert alert.count == 80
    assert alert.flag == 0


def test_alert_parsing_of_the_wire_form():
    """The wire form is nibble-ized, and parses to the same alert."""
    alert = b.PresetAlert(
        command=m.CommandType.SINGLE_SEQUENCE_ALERT, size=0x30, flag=0
    )
    wire = alert.wire_payload()
    assert len(wire) == 12

    assert b.PresetAlert.parse(wire) == alert
    assert (
        b.PresetAlert.parse(
            b.PresetAlert(command=7, size=0x10D4, count=80).wire_payload()
        ).count
        == 80
    )


def test_alert_parsing_refuses_something_that_is_not_an_alert():
    """A command message that is not an alert is not one."""
    with pytest.raises(b.DeviceError):
        b.PresetAlert.from_bytes(bytes([0x03, 0x00, 0x00, 0x00, 0x30, 0x00]))
    with pytest.raises(b.DeviceError):
        b.PresetAlert.from_bytes(bytes([0x06, 0x00, 0x00]))


# --- the wire ----------------------------------------------------------------


def test_read_current_preset_takes_the_alert_and_the_dump():
    """The whole exchange: request out, alert and dump back."""
    data = _fixture("single_seq_default.bin")
    bridge, sink = _bridge(
        [
            _alert_message(ONE_ALERT),
            _dump_message(m.Function.SINGLE_SEQUENCE_DUMP, data),
        ]
    )

    current = bridge.read_current_preset()

    assert current.preset_raw == data[48:]
    assert current.seq_blob == data[:48]
    # The request is the single-sequence dump request, nibble-ized.
    assert sink.sent == [
        m.command_message(m.CommandType.SINGLE_SEQUENCE_REQUEST, channel=0)
    ]


def test_read_all_presets_takes_the_alert_and_the_dump():
    """The whole exchange for the eighty presets."""
    data = _fixture("all_seq.bin")
    bridge, sink = _bridge(
        [
            _alert_message(ALL_ALERT),
            _dump_message(m.Function.ALL_SEQUENCES_DUMP, data),
        ]
    )

    dump = bridge.read_all_presets()

    assert dump.count == 80
    assert dump.preset_area_start == 4516
    assert len(dump.presets_raw) == 80
    assert sink.sent == [
        m.command_message(m.CommandType.ALL_SEQUENCE_REQUEST, channel=0)
    ]


def test_a_refusal_is_a_refusal():
    """A NAK says no, which is different from saying nothing."""
    bridge, _sink = _bridge(
        [m.frame(m.Function.ERROR, m.to_nibbles([m.ErrorCode.NAK]), device=0)]
    )

    with pytest.raises(b.DeviceRefused) as caught:
        bridge.read_current_preset()

    assert "NAK" in str(caught.value)
    # It is also a device error: the exchange stopped half way.
    assert isinstance(caught.value, b.DeviceError)


def test_silence_is_a_timeout():
    """Nothing coming back says what nothing coming back means."""
    bridge, _sink = _bridge([])

    with pytest.raises(TimeoutError) as caught:
        bridge.read_current_preset(timeout=0.05)

    message = str(caught.value)
    assert "Single Sequence Dump" in message
    # The sentence names the causes rather than picking one.
    assert "powered off" in message
    assert "wrong base MIDI channel" in message


def test_the_own_request_coming_back_is_not_the_alert():
    """A soft thru echoes the request; the command byte tells them apart."""
    data = _fixture("single_seq_default.bin")
    echo = m.command_message(m.CommandType.SINGLE_SEQUENCE_REQUEST, channel=0)
    bridge, _sink = _bridge(
        [
            echo,
            _alert_message(ONE_ALERT),
            _dump_message(m.Function.SINGLE_SEQUENCE_DUMP, data),
        ]
    )

    current = bridge.read_current_preset()

    assert current.preset_raw == data[48:]
