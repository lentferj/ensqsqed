# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# The frame layout is derived from System Exclusive dumps produced by the
# instrument itself and from Ensoniq's own MIDI specification; see
# docs/PRESET-FRAMING.md.  Ensoniq's specification for this family was a
# printed document supplied on request and is not available; see
# DISCLAIMER.md.
#
# ensqsqed is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 2 of the License, or (at your option)
# any later version.

"""The preset model against the dumps the instrument produced.

Every fixture here is a real dump: ``single_seq_default.bin`` is a single
sequence dump of the unit on the desk, ``all_seq.bin`` is every preset it
holds.  The tests are about structure -- what the bytes mean -- and never
about the text a name happens to carry.
"""

import pathlib

import pytest

from sq import presets

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _single_dump() -> bytes:
    return (FIXTURES / "single_seq_default.bin").read_bytes()


def _all_dump() -> bytes:
    return (FIXTURES / "all_seq.bin").read_bytes()


class TestTheRoundTrip:
    """A preset read from the unit is written back unchanged."""

    def test_the_preset_comes_back_byte_for_byte(self):
        raw = _single_dump()[48:216]
        assert presets.Preset.from_bytes(raw).to_bytes() == raw

    def test_the_sequence_blob_comes_back_byte_for_byte(self):
        """The blob in front of the preset is the sequence data, and it
        is not the preset's business -- but it must survive a split."""
        raw = _single_dump()
        blob, preset = presets.split_single_dump(raw)
        assert blob == raw[:48]
        assert preset == raw[48:]
        assert presets.Preset.from_bytes(preset).to_bytes() == preset

    def test_every_preset_in_the_all_dump_comes_back_byte_for_byte(self):
        data = _all_dump()
        count = 80
        start = len(data) - 21 - 168 * count
        for number in range(count):
            raw = data[start + number * 168 : start + (number + 1) * 168]
            assert presets.Preset.from_bytes(raw).to_bytes() == raw

    def test_a_track_comes_back_byte_for_byte(self):
        raw = _single_dump()[48:216]
        for number in range(8):
            chunk = raw[32 + number * 14 : 32 + (number + 1) * 14]
            assert presets.Track.from_bytes(chunk).to_bytes() == chunk


class TestTheFixture:
    """What the dump actually holds."""

    def test_the_preset_starts_after_the_sequence_blob(self):
        blob, preset = presets.split_single_dump(_single_dump())
        assert len(blob) == 48
        assert len(preset) == 168

    def test_the_name_keeps_its_flag_bits(self):
        """Bit 7 of a name byte is the unit's, not the name's."""
        raw = _single_dump()[48:216]
        name_bytes = raw[2:18]
        assert name_bytes != bytes(16)
        assert any(byte & 0x80 for byte in name_bytes)
        assert presets.Preset.from_bytes(raw).name_bytes == name_bytes

    def test_the_name_is_the_bytes_without_their_flags(self):
        raw = _single_dump()[48:216]
        preset = presets.Preset.from_bytes(raw)
        masked = bytes(byte & 0x7F for byte in raw[2:18])
        assert preset.name == masked.decode("ascii", "replace")
        assert all(32 <= byte < 127 for byte in masked)

    def test_renaming_leaves_the_flags_where_they_were(self):
        raw = _single_dump()[48:216]
        preset = presets.Preset.from_bytes(raw)
        before = preset.name_bytes
        preset.name = "Something else"
        after = preset.name_bytes
        assert [byte & 0x80 for byte in after] == [byte & 0x80 for byte in before]
        assert bytes(byte & 0x7F for byte in after) == b"Something else  "
        assert preset.name == "Something else  "

    def test_the_reserved_byte_is_kept_as_it_stands(self):
        """Byte 6 is 0x80 in every track of every dump, and the
        specification calls it reserved.  It is stored, not normalised."""
        raw = _single_dump()[48:216]
        for number in range(8):
            chunk = raw[32 + number * 14 : 32 + (number + 1) * 14]
            assert chunk[6] == 0x80
            assert presets.Track.from_bytes(chunk).reserved == 0x80
            assert presets.Track.from_bytes(chunk).to_bytes()[6] == 0x80

    def test_the_tempo_of_an_empty_preset_is_kept(self):
        """Ten presets in the all-dump hold 0, where the manual says
        25..250.  A parser that refused them would refuse the
        instrument's memory."""
        data = _all_dump()
        start = len(data) - 21 - 168 * 80
        tempos = {data[start + number * 168 + 30] for number in range(80)}
        assert tempos == {0, 120}
        assert presets.Preset.from_bytes(data[start : start + 168]).tempo == 0

    def test_the_pile_of_the_factory_preset(self):
        raw = _single_dump()[48:216]
        preset = presets.Preset.from_bytes(raw)
        assert preset.pile == b"\x01" + b"\x00" * 7
        assert preset.primary_track == 1
        assert preset.is_active(1)
        assert not any(preset.is_active(number) for number in range(2, 9))


class TestTheSoundNumber:
    """Note 5: the bank code is in bits 14 and 15, the sound number in
    the low byte."""

    def test_the_word_is_the_bank_code_and_the_number(self):
        assert presets.sound_word("INT", 1) == 0x0000
        assert presets.sound_word("ROM", 1) == 0x4000
        assert presets.sound_word("CARDA", 1) == 0x8000
        assert presets.sound_word("CARDB", 1) == 0xC000

    def test_the_number_is_counted_from_zero_in_the_byte(self):
        """The manual counts sounds from 001, the byte holds 000."""
        assert presets.sound_word("INT", 1) & 0xFF == 0
        assert presets.sound_word("INT", 80) & 0xFF == 79
        assert presets.sound_word("INT", 80) == 0x004F

    def test_the_word_is_two_bytes_big_endian(self):
        track = presets.Track(sound_number=presets.sound_word("CARDB", 42))
        assert track.to_bytes()[0:2] == b"\xc0\x29"

    def test_the_drum_kits_live_at_eighty_to_ninety_nine(self):
        assert presets.sound_word("DRUM", 1) & 0xFF == 80
        assert presets.sound_word("DRUM", 20) & 0xFF == 99
        assert presets.sound_word("DRUM", 20) == 0x0063

    def test_the_group_and_the_slot_come_back_out_of_the_word(self):
        for group in ("INT", "ROM", "CARDA", "CARDB"):
            for slot in (1, 2, 40, 80):
                track = presets.Track(sound_number=presets.sound_word(group, slot))
                assert track.group == group
                assert track.slot == slot

    def test_a_drum_kit_ignores_the_bank_code(self):
        track = presets.Track(sound_number=presets.sound_word("DRUM", 3))
        assert track.group == "DRUM"
        assert track.slot == 3
        assert presets.Track(sound_number=0xC050).group == "DRUM"
        assert presets.Track(sound_number=0xC050).slot == 1

    def test_a_refused_group_or_slot_is_refused_in_words(self):
        with pytest.raises(ValueError, match="no sound group"):
            presets.sound_word("CARDC", 1)
        with pytest.raises(ValueError, match="outside 1-80"):
            presets.sound_word("INT", 81)
        with pytest.raises(ValueError, match="outside 1-20"):
            presets.sound_word("DRUM", 21)


class TestByteEight:
    """Byte 8 holds the MIDI status, the pressure type and the MIDI
    channel."""

    def test_the_three_fields_share_the_byte(self):
        for status in range(4):
            for pressure in range(3):
                for channel in (0, 7, 15):
                    track = presets.Track(
                        status=status, pressure=pressure, midi_channel=channel
                    )
                    data = track.to_bytes()
                    assert data[8] == (status << 6) | (pressure << 4) | channel
                    back = presets.Track.from_bytes(data)
                    assert (back.status, back.pressure, back.midi_channel) == (
                        status,
                        pressure,
                        channel,
                    )

    def test_the_extreme_channel_survives(self):
        track = presets.Track(midi_channel=15)
        assert track.to_bytes()[8] & 0x0F == 15
        assert presets.Track.from_bytes(track.to_bytes()).midi_channel == 15

    def test_the_extreme_status_and_pressure_survive(self):
        track = presets.Track(status=3, pressure=2, midi_channel=15)
        assert track.to_bytes()[8] == 0xEF
        back = presets.Track.from_bytes(track.to_bytes())
        assert (back.status, back.pressure) == (3, 2)

    def test_the_fixture_agrees(self):
        """Every track of the single dump is LOCAL, KEY, and a channel
        of its own."""
        raw = _single_dump()[48:216]
        seen = []
        for number in range(8):
            chunk = raw[32 + number * 14 : 32 + (number + 1) * 14]
            track = presets.Track.from_bytes(chunk)
            assert track.status == 1
            assert track.pressure == 1
            seen.append(track.midi_channel)
        assert seen == [10, 11, 2, 3, 4, 5, 6, 7]

    def test_the_all_dump_agrees(self):
        data = _all_dump()
        start = len(data) - 21 - 168 * 80
        for number in range(80):
            raw = data[start + number * 168 : start + (number + 1) * 168]
            for track_number in range(8):
                chunk = raw[32 + track_number * 14 : 32 + (track_number + 1) * 14]
                track = presets.Track.from_bytes(chunk)
                assert track.status == 1
                assert track.pressure == 1
                assert 0 <= track.midi_channel <= 15


class TestThePan:
    """The pan is the one conversion the documentation does not
    settle, so the rule is stated and tested."""

    def test_sound_is_minus_128(self):
        assert presets.PAN_SOUND == -128
        assert presets.pan_display(-128) is None
        assert presets.pan_stored(None) == -128

    def test_the_rule_for_the_right_hand_side(self):
        assert presets.pan_display(0) == 0
        assert presets.pan_display(1) == 1
        assert presets.pan_display(0x7F) == 99

    def test_the_rule_for_the_left_hand_side(self):
        """The sign bit is the sign and the rest is the table index."""
        assert presets.pan_display(-1) == -99
        assert presets.pan_display(-127) == -1
        assert presets.pan_display(-128 + 1) == -1

    def test_the_rule_is_its_own_inverse(self):
        for stored in (0, 1, 63, 127, -1, -2, -127):
            shown = presets.pan_display(stored)
            assert shown is not None
            assert presets.pan_stored(shown) == stored

    def test_the_fixture_proves_only_sound(self):
        """Every track of every dump is at SOUND.  That is all the
        fixture says, and the rule above is the specification's."""
        raw = _single_dump()[48:216]
        for number in range(8):
            chunk = raw[32 + number * 14 : 32 + (number + 1) * 14]
            assert chunk[3] == 0x80
            track = presets.Track.from_bytes(chunk)
            assert track.pan == presets.PAN_SOUND
            assert track.pan_display is None
            assert track.pan_label == "SOUND"

    def test_the_all_dump_is_at_sound_too(self):
        data = _all_dump()
        start = len(data) - 21 - 168 * 80
        for number in range(80):
            raw = data[start + number * 168 : start + (number + 1) * 168]
            for track_number in range(8):
                chunk = raw[32 + track_number * 14 : 32 + (track_number + 1) * 14]
                assert chunk[3] == 0x80


class TestTheKeyRange:
    def test_the_ends_of_the_keyboard(self):
        assert presets.note_name(21) == "A0"
        assert presets.note_name(108) == "C8"
        assert presets.note_name(60) == "C4"

    def test_a_number_outside_the_keyboard_is_printed_as_a_number(self):
        assert presets.note_name(0) == "0"
        assert presets.note_name(127) == "127"

    def test_the_track_prints_its_range(self):
        track = presets.Track(key_low=21, key_high=108)
        assert track.key_low_name == "A0"
        assert track.key_high_name == "C8"


class TestTheTranspose:
    """The byte holds the transpose in semitones and the panel splits
    it into octaves and semitones."""

    def test_no_transpose_is_no_transpose(self):
        track = presets.Track(transpose=0)
        assert (track.transpose_octave, track.transpose_semitone) == (0, 0)

    def test_the_octave_edges(self):
        for octave in (-4, -3, -1, 1, 3, 4):
            track = presets.Track(transpose=octave * 12)
            assert track.transpose_octave == octave
            assert track.transpose_semitone == 0

    def test_the_semitone_edges(self):
        for semitone in (-11, -7, -1, 1, 7, 11):
            track = presets.Track(transpose=semitone)
            assert track.transpose_octave == 0
            assert track.transpose_semitone == semitone

    def test_the_widest_transpose_the_unit_allows(self):
        high = presets.Track(transpose=59)
        assert (high.transpose_octave, high.transpose_semitone) == (4, 11)
        low = presets.Track(transpose=-59)
        assert (low.transpose_octave, low.transpose_semitone) == (-4, -11)

    def test_the_split_survives_the_bytes(self):
        for total in (-59, -48, -12, -1, 0, 1, 12, 48, 59):
            track = presets.Track(transpose=total)
            back = presets.Track.from_bytes(track.to_bytes())
            assert back.transpose == total
            assert back.transpose_octave == track.transpose_octave
            assert back.transpose_semitone == track.transpose_semitone


class TestTheEffect:
    def test_there_are_thirteen_effects(self):
        assert len(presets.EFFECTS) == 13
        assert presets.EFFECTS[0] == "CONCERT REVERB"
        assert presets.EFFECTS[12] == "CMPRSS+DIST+VERB"

    def test_every_effect_has_a_parameter_set_with_rows(self):
        for number in range(len(presets.EFFECTS)):
            effect = presets.Effect(number=number)
            assert effect.parameter_set is not None
            assert len(effect.rows) >= 1
            assert effect.name == presets.EFFECTS[number]

    def test_the_six_parameter_sets_are_the_specifications_six(self):
        assert set(presets.PARAM_SETS) == {
            "Common Reverb",
            "MultiVerb",
            "Multi-Voice Chorus",
            "Phaser",
            "Rotary Speaker Simulator",
            "Guitar Effects",
        }

    def test_the_modulation_sources_are_sixteen(self):
        assert len(presets.MIX_SOURCES) == 16
        assert presets.MIX_SOURCES[0] == "KEYBOARD"
        assert presets.MIX_SOURCES[-1] == "*OFF*"

    def test_the_effect_block_comes_back_byte_for_byte(self):
        raw = _single_dump()[48:216][152:163]
        assert presets.Effect.from_bytes(raw).to_bytes() == raw

    def test_the_fixture_holds_the_first_effect(self):
        raw = _single_dump()[48:216]
        effect = presets.Effect.from_bytes(raw[152:163])
        assert effect.number == 0
        assert effect.name == "CONCERT REVERB"
        assert effect.parameter_set == "Common Reverb"

    def test_a_shared_byte_holds_two_parameters(self):
        """Byte 4 of the common reverb holds the destination in its
        high four bits and the source in its low four, and the fixture
        says so: 0x25 is destination 2 and source 5 that way, and an
        out-of-range destination the other way."""
        raw = _single_dump()[48:216]
        effect = presets.Effect.from_bytes(raw[152:163])
        rows = {row.key: row for row in effect.rows}
        assert effect.raw(rows["mod_destination"]) == 2
        assert effect.raw(rows["mod_source"]) == 5
        assert effect.display(rows["mod_destination"]) == "FX1-MIX"
        assert effect.display(rows["mod_source"]) == "MODPEDAL"

    def test_a_parameter_can_be_written_back(self):
        raw = _single_dump()[48:216]
        effect = presets.Effect.from_bytes(raw[152:163])
        rows = {row.key: row for row in effect.rows}
        changed = effect.with_value(rows["mod_destination"], "FX2-MIX")
        assert changed.raw(rows["mod_destination"]) == 3
        assert changed.raw(rows["mod_source"]) == 5
        assert changed.params[5:10] == effect.params[5:10]

    def test_a_value_outside_the_range_is_refused(self):
        raw = _single_dump()[48:216]
        effect = presets.Effect.from_bytes(raw[152:163])
        rows = {row.key: row for row in effect.rows}
        with pytest.raises(ValueError, match="outside"):
            effect.with_value(rows["decay_time"], 100)

    def test_every_row_prints_a_value_and_prints_it_again_the_same(self):
        """Note 7's table is many stored values to one printed number,
        so a value written back is not always the value that was read.
        What must hold is that it prints the same."""
        for name, rows in presets.PARAM_SETS.items():
            assert rows, f"{name} has no rows"
            for row in rows:
                for raw in range(256):
                    if raw & ~row.mask:
                        continue
                    if row.names is not None:
                        if raw >= len(row.names):
                            continue
                    elif not row.low <= row.internal(raw) <= row.high:
                        continue
                    shown = row.displayed(raw)
                    if isinstance(shown, str):
                        assert row.encode(shown) == raw
                        continue
                    assert row.displayed(row.encode(shown)) == shown

    def test_a_row_that_holds_a_number_it_cannot_print_is_refused(self):
        rows = {row.key: row for row in presets.PARAM_SETS["Common Reverb"]}
        with pytest.raises(ValueError, match="outside"):
            rows["mod_destination"].displayed(5)
        with pytest.raises(ValueError, match="outside"):
            rows["detune_rate"].displayed(120)

    def test_an_effect_with_the_wrong_number_of_bytes_is_refused(self):
        with pytest.raises(ValueError, match="parameter bytes"):
            presets.Effect(number=0, params=b"\x00" * 9)


class TestThePile:
    """The off state lives in the pile, not in the MIDI status."""

    def test_a_track_is_switched_on_and_off_in_the_pile(self):
        preset = presets.Preset.from_bytes(_single_dump()[48:216])
        preset.set_active(4, True)
        assert preset.is_active(4)
        assert preset.pile[3] == 4
        preset.set_active(4, False)
        assert not preset.is_active(4)
        assert preset.pile[3] == 0

    def test_the_primary_track_stays_on(self):
        preset = presets.Preset.from_bytes(_single_dump()[48:216])
        with pytest.raises(ValueError, match="primary track"):
            preset.set_active(preset.primary_track, False)

    def test_a_track_outside_the_preset_is_refused(self):
        preset = presets.Preset.from_bytes(_single_dump()[48:216])
        with pytest.raises(ValueError, match="outside 1-8"):
            preset.is_active(9)
        with pytest.raises(ValueError, match="outside 1-8"):
            preset.set_active(0, True)


class TestTheRefusals:
    """A wrong length is a sentence, not a traceback."""

    def test_a_preset_of_the_wrong_length_is_refused(self):
        with pytest.raises(ValueError, match="a preset is 168 bytes"):
            presets.Preset.from_bytes(b"\x00" * 167)
        with pytest.raises(ValueError, match="a preset is 168 bytes"):
            presets.Preset.from_bytes(b"\x00" * 216)

    def test_a_track_of_the_wrong_length_is_refused(self):
        with pytest.raises(ValueError, match="a track is 14 bytes"):
            presets.Track.from_bytes(b"\x00" * 13)

    def test_an_effect_of_the_wrong_length_is_refused(self):
        with pytest.raises(ValueError, match="an effect is 11 bytes"):
            presets.Effect.from_bytes(b"\x00" * 10)

    def test_a_tempo_outside_the_range_is_refused(self):
        preset = presets.Preset.from_bytes(_single_dump()[48:216])
        with pytest.raises(ValueError, match="tempo 10 is outside 25-250"):
            preset.tempo = 10
        with pytest.raises(ValueError, match="tempo 300 is outside 25-250"):
            preset.tempo = 300

    def test_a_track_value_outside_the_range_is_refused(self):
        track = presets.Track()
        with pytest.raises(ValueError, match="volume 200 is outside 0-127"):
            track.updated(volume=200)
        with pytest.raises(ValueError, match="no field"):
            track.updated(nonsense=1)

    def test_a_dump_that_is_not_a_dump_is_refused(self):
        with pytest.raises(ValueError, match="single sequence dump"):
            presets.split_single_dump(b"\x00\x00\x00")

    def test_a_field_of_the_wrong_length_is_refused(self):
        preset = presets.Preset.from_bytes(_single_dump()[48:216])
        preset.pile = b"\x01" + b"\x00" * 6
        with pytest.raises(ValueError, match="track pile is 8 bytes"):
            preset.to_bytes()


class TestTheDisplayTable:
    """Note 7's table, and the conversions that hang off it."""

    def test_the_table_has_128_entries_and_runs_from_0_to_99(self):
        assert len(presets.DISPLAY_TABLE) == 128
        assert presets.DISPLAY_TABLE[0] == 0
        assert presets.DISPLAY_TABLE[-1] == 99
        assert list(presets.DISPLAY_TABLE) == sorted(presets.DISPLAY_TABLE)

    def test_a_stored_value_is_printed_as_the_table_says(self):
        assert presets.display_value(0) == 0
        assert presets.display_value(1) == 1
        assert presets.display_value(2) == 1
        assert presets.display_value(127) == 99

    def test_the_table_is_inverted_by_the_first_value_that_prints(self):
        assert presets.stored_value(0) == 0
        assert presets.stored_value(1) == 1
        assert presets.stored_value(99) == 127

    def test_the_fixture_prints_its_own_volume(self):
        raw = _single_dump()[48:216]
        track = presets.Track.from_bytes(raw[32:46])
        assert track.volume == 127
        assert track.volume_display == 99
