# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# The frame layout and the dump sizes are derived from System Exclusive
# dumps produced by the instrument itself. Ensoniq's own MIDI
# specification for this family was a printed document supplied on request
# and is not available; see DISCLAIMER.md.
#
# ensqsqed is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 2 of the License, or (at your option)
# any later version.

"""The memory map and the selector scheme. Every fixture is invented."""

import pytest

from sq import banks
from sq import dumps
from sq import messages as m


class TestTheMemoryMap:
    def test_there_are_340_sounds(self):
        assert banks.TOTAL_SOUNDS == 340
        assert banks.SOUNDS_PER_GROUP == 80
        assert banks.DRUM_KITS == 20

    def test_a_group_is_eight_banks_of_ten(self):
        assert (banks.BANKS_PER_GROUP * banks.SOUNDS_PER_BANK
                == banks.SOUNDS_PER_GROUP)
        assert banks.slot("INT", 1).bank == 0
        assert banks.slot("INT", 11).bank == 1
        assert banks.slot("INT", 80).bank == 7

    def test_drum_kits_are_banks_eight_and_nine(self):
        """Counted from their own program change, not from zero: kit 1
        answers to 80, which is bank 8's first slot."""
        assert banks.slot("DRUM", 1).bank == 8 + 80 // banks.SOUNDS_PER_BANK
        assert banks.slot("DRUM", 1).program_change == 80

    def test_only_internal_can_be_dumped(self):
        assert [g.id for g in banks.readable_groups()] == ["INT"]


class TestTheTwoNumbers:
    """The unit counts from 1 and MIDI counts from 0, and the manual says
    so itself. They are separate attributes and nothing converts."""

    def test_they_differ_by_one_for_a_sound(self):
        first = banks.slot("INT", 1)
        assert first.number == 1
        assert first.program_change == 0
        last = banks.slot("ROM", 80)
        assert (last.number, last.program_change) == (80, 79)

    def test_a_drum_kit_is_not_offset_the_same_way(self):
        """Its program change is absolute, so the relationship changes."""
        kit = banks.slot("DRUM", 1)
        assert kit.number == 1
        assert kit.program_change == banks.DRUM_FIRST_PC == 80


class TestReachingASound:
    """A program change means nothing on its own on this instrument."""

    def test_a_group_selector_goes_first(self):
        out = banks.select_messages(banks.slot("CARDA", 5), channel=0)
        assert [list(msg) for msg in out] == [[0xC0, 126], [0xC0, 4]]

    def test_every_group_has_its_own_selector(self):
        seen = {g.id: g.selector for g in banks.groups() if g.selector}
        assert seen == {"INT": 124, "ROM": 125, "CARDA": 126, "CARDB": 127}
        assert len(set(seen.values())) == 4

    def test_a_drum_kit_gets_no_selector(self):
        """Sending one would leave the unit in a group nobody asked for."""
        out = banks.select_messages(banks.slot("DRUM", 3))
        assert [list(msg) for msg in out] == [[0xC0, 82]]

    def test_the_effect_command_comes_before_the_selector(self):
        out = banks.select_messages(banks.slot("ROM", 1), with_effect=True)
        assert [msg[1] for msg in out] == [
            banks.EFFECT_WITH_NEXT_PC, 125, 0]

    def test_the_channel_lands_in_the_status_byte(self):
        out = banks.select_messages(banks.slot("INT", 1), channel=9)
        assert all(msg[0] == 0xC9 for msg in out)

    def test_a_program_change_needs_a_group_to_mean_anything(self):
        assert banks.lookup(4, "ROM").group_id == "ROM"
        assert banks.lookup(4, "CARDB").group_id == "CARDB"

    def test_a_drum_program_change_means_the_same_everywhere(self):
        for group_id in ("INT", "ROM", "CARDA", "CARDB"):
            assert banks.lookup(84, group_id).group_id == "DRUM"


class TestMessages:
    def test_a_frame_carries_the_ensoniq_header(self):
        framed = m.frame(m.Function.ALL_SOUNDS_DUMP, [0], device=13)
        assert framed[:6] == bytes([0xF0, 0x0F, 0x06, 0x00, 13, 0x03])
        assert framed[-1] == 0xF7

    def test_nibbles_round_trip(self):
        data = bytes(range(256))
        assert m.from_nibbles(m.to_nibbles(data)) == data

    def test_a_sound_is_408_on_the_wire_and_204_decoded(self):
        assert m.SOUND_BYTES_PACKED == 408
        assert len(m.from_nibbles(bytes(408))) == m.SOUND_BYTES == 204

    def test_a_dump_of_the_wrong_size_is_not_that_dump(self):
        """There is no checksum, so size is the only structural check."""
        short = m.parse(m.frame(m.Function.SINGLE_SOUND_DUMP, [0] * 10))
        assert short.function == m.Function.SINGLE_SOUND_DUMP
        assert not short.size_is_right

    def test_a_foreign_message_parses_as_none(self):
        assert m.parse(bytes([0xF0, 0x43, 0x00, 0xF7])) is None

    def test_a_name_is_sixteen_characters_in_fourteen_bytes(self):
        """Seven bits each, MSB first, at offset 177. Specification
        section 4.2 and Note 3."""
        assert m.NAME_CHARS * 7 == m.NAME_BYTES * 8
        record = bytearray(m.SOUND_BYTES)
        # "Invented" centred, the way the instrument centres its own
        text = "   Invented     "
        acc = bits = 0
        out = bytearray()
        for char in text:
            acc = (acc << 7) | ord(char)
            bits += 7
            while bits >= 8:
                bits -= 8
                out.append((acc >> bits) & 0xFF)
        record[m.NAME_OFFSET:m.NAME_OFFSET + m.NAME_BYTES] = out
        assert m.sound_name(record) == text

    def test_the_wrong_nibble_order_is_silent_not_fatal(self):
        """Which is why the order is stated rather than assumed."""
        data = bytes(range(204))
        wire = m.to_nibbles(data)
        assert m.from_nibbles(wire) == data
        wrong = m.from_nibbles_low_first(wire)
        assert len(wrong) == len(data), "same length, so nothing complains"
        assert wrong != data


def _vlq(value):
    """A Standard MIDI File variable-length quantity."""
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.insert(0, (value & 0x7F) | 0x80)
        value >>= 7
    return bytes(out)


def _bank_message(device=0):
    payload = m.to_nibbles(bytes(m.SOUND_BYTES)) * banks.SOUNDS_PER_GROUP
    return m.frame(m.Function.ALL_SOUNDS_DUMP, payload, device=device)


class TestReadingDumps:
    def test_a_full_bank_is_recognised(self):
        library = dumps.read_bytes(_bank_message(device=13))
        assert library.is_full_bank
        assert len(library.sounds) == 80
        assert library.device == 13

    def test_a_single_sound_is_kept_apart(self):
        raw = m.frame(m.Function.SINGLE_SOUND_DUMP,
                      m.to_nibbles(bytes(m.SOUND_BYTES)))
        library = dumps.read_bytes(raw)
        assert len(library.singles) == 1
        assert not library.sounds

    def test_an_unknown_opcode_is_counted_not_dropped(self):
        raw = m.frame(m.Function.SHORT_COMMAND, [0, 0, 0, 9])
        library = dumps.read_bytes(raw)
        assert library.unknown == 1

    def test_a_truncated_tail_does_not_stop_the_rest(self):
        library = dumps.read_bytes(_bank_message() + b"\xf0\x0f\x06")
        assert library.is_full_bank

    def test_a_midi_file_is_walked_properly(self):
        """In an SMF the F0 is followed by a length, not by the body."""
        import struct

        message = _bank_message()
        count = len(message) - 1          # everything after the F0
        body = b"\x00\xf0" + _vlq(count) + message[1:]
        track = b"MTrk" + struct.pack(">I", len(body)) + body
        smf = b"MThd" + struct.pack(">I", 6) + b"\x00\x00\x00\x01\x00\x60" \
            + track
        library = dumps.read_bytes(smf, smf=True)
        assert library.is_full_bank
