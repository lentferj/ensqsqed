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


def _effect_params() -> bytes:
    """The ten parameter bytes of the captured preset's effect.

    The unit's own bytes, not invented ones: the shared bytes have to
    hold something in both halves for a partial write to be able to
    disturb either of them.
    """
    return _single_dump()[48:216][152:163][:10]


def _another_value(effect, row):
    """A value for this row that it does not hold now.

    **Every step of it is the model's.**  A row that names its values
    is stepped to the next name; a row that numbers them is stepped to
    a raw value the row can encode back to something else.  The
    answer is the *displayed* value, because that is what
    :meth:`sq.presets.Effect.with_value` takes.
    """
    current = effect.raw(row)
    if row.names is not None:
        for index, name in enumerate(row.names):
            if index != current:
                return name
        raise AssertionError(f"{row.key} has only one value")
    limit = (row.mask >> row.shift) + 1
    for raw in range(limit):
        try:
            shown = row.displayed(raw)
        except ValueError:
            continue
        if not isinstance(shown, int):
            continue
        if row.encode(shown) == current:
            continue
        return shown
    raise AssertionError(f"{row.key} has no other value")


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
        assert 4 in preset.pile[1:]
        preset.set_active(4, False)
        assert not preset.is_active(4)
        assert preset.pile == b"\x01" + b"\x00" * 7

    def test_the_layered_tracks_are_a_compact_ordered_list(self):
        """The unit packs them, and keeps the order they were layered in."""
        preset = presets.Preset.from_bytes(_single_dump()[48:216])
        preset.set_active(3, True)
        preset.set_active(2, True)
        assert preset.pile == bytes([1, 3, 2, 0, 0, 0, 0, 0])
        assert preset.is_active(2)
        assert preset.is_active(3)

    def test_switching_off_a_middle_track_leaves_no_hole(self):
        """The bench's failure: ``01 00 03`` is not what the unit stores.

        The unit compacts it to ``01 03``, so a write that leaves the
        hole fails its own read-back and the track appears to stay on.
        """
        preset = presets.Preset.from_bytes(_single_dump()[48:216])
        preset.set_active(2, True)
        preset.set_active(3, True)
        preset.set_active(2, False)
        assert preset.pile == bytes([1, 3, 0, 0, 0, 0, 0, 0])
        assert not preset.is_active(2)
        assert preset.is_active(3)

    def test_switching_off_the_last_layered_track_keeps_the_others(self):
        preset = presets.Preset.from_bytes(_single_dump()[48:216])
        preset.set_active(2, True)
        preset.set_active(3, True)
        preset.set_active(3, False)
        assert preset.pile == bytes([1, 2, 0, 0, 0, 0, 0, 0])
        assert preset.is_active(2)

    def test_a_hole_in_a_parsed_pile_is_compacted_by_the_next_write(self):
        preset = presets.Preset.from_bytes(_single_dump()[48:216])
        preset.pile = bytes([1, 0, 4, 0, 0, 0, 0, 0])
        preset.set_active(5, True)
        assert preset.pile == bytes([1, 4, 5, 0, 0, 0, 0, 0])

    def test_any_track_can_be_switched_off_including_the_first(self):
        """There is no mandatory primary track.

        The owner disabled all eight tracks on the panel; nothing in the
        manual or on the bench makes the first pile entry stay on.  The
        pile is the ON list, and removing its last entry leaves all
        zeros.
        """
        preset = presets.Preset.from_bytes(_single_dump()[48:216])
        preset.set_active(1, False)
        assert preset.pile == b"\x00" * 8
        assert not any(preset.is_active(number) for number in range(1, 9))
        assert preset.primary_track == 0

    def test_the_last_track_can_be_switched_off_too(self):
        preset = presets.Preset.from_bytes(_single_dump()[48:216])
        for number in range(1, 9):
            preset.set_active(number, True)
        assert preset.pile == bytes(range(1, 9))
        for number in range(1, 9):
            preset.set_active(number, False)
        assert preset.pile == b"\x00" * 8

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

    def test_a_track_is_copied_with_the_new_value(self):
        """The change is a copy: the track it was asked about is whole."""
        track = presets.Track(volume=64, midi_channel=3)
        changed = track.updated(volume=65, midi_channel=4)
        assert (changed.volume, changed.midi_channel) == (65, 4)
        assert (track.volume, track.midi_channel) == (64, 3)

    def test_the_sustain_switch_is_a_bit_and_is_set_as_one(self):
        track = presets.Track(sustain=True)
        off = track.updated(sustain=False)
        assert off.sustain is False
        assert off.to_bytes()[9] & 0x80 == 0
        assert track.updated(sustain=False).to_bytes()[9] == track.to_bytes()[9] - 0x80

    def test_a_dump_that_is_not_a_dump_is_refused(self):
        with pytest.raises(ValueError, match="single sequence dump"):
            presets.split_single_dump(b"\x00\x00\x00")

    def test_a_field_of_the_wrong_length_is_refused(self):
        preset = presets.Preset.from_bytes(_single_dump()[48:216])
        preset.pile = b"\x01" + b"\x00" * 6
        with pytest.raises(ValueError, match="track pile is 8 bytes"):
            preset.to_bytes()

    def test_a_dump_whose_size_word_disagrees_with_it_is_refused(self):
        """The blob says how long it is, and the two have to agree."""
        raw = bytearray(_single_dump())
        raw[3] = 0x31
        with pytest.raises(ValueError, match="49 bytes of sequence data"):
            presets.split_single_dump(bytes(raw))

    def test_a_tempo_that_is_not_a_number_is_refused(self):
        preset = presets.Preset.from_bytes(_single_dump()[48:216])
        with pytest.raises(TypeError, match="tempo takes a whole number"):
            preset.tempo = "120"

    def test_a_preset_with_the_wrong_number_of_tracks_is_refused(self):
        preset = presets.Preset.from_bytes(_single_dump()[48:216])
        preset.tracks = preset.tracks[:7]
        with pytest.raises(ValueError, match="a preset has 8 tracks"):
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

    def test_a_stored_value_outside_the_table_is_refused(self):
        """The table has 128 entries and no more."""
        with pytest.raises(ValueError, match="value 128 is outside 0-127"):
            presets.display_value(128)
        with pytest.raises(ValueError, match="value -1 is outside 0-127"):
            presets.display_value(-1)

    def test_a_printed_value_outside_the_table_is_refused(self):
        """The printed side is 0..99, and the refusal says so."""
        with pytest.raises(ValueError, match="value 100 is outside 0-99"):
            presets.stored_value(100)
        with pytest.raises(ValueError, match="value -1 is outside 0-99"):
            presets.stored_value(-1)


class TestThePublicSurface:
    """Every public name of :mod:`sq.presets` is used by one of these.

    The list is the module's own ``__all__``, so a name added to the
    module without a test here is a gap in the sweep rather than a
    silent omission.
    """

    def test_every_public_name_exists(self):
        for name in presets.__all__:
            assert hasattr(presets, name), name

    def test_the_sizes_are_the_specifications(self):
        assert presets.TRACK_BYTES == 14
        assert presets.PRESET_BYTES == 168
        assert presets.EFFECT_BYTES == 11
        assert presets.SEQUENCE_BLOB_BYTES == 48

    def test_the_panel_words_are_the_specifications(self):
        assert len(presets.NOTE_NAMES) == 12
        assert presets.OUTPUT_NAMES == ("DRY", "FX1", "FX2", "VOICE", "CONTROL")
        assert presets.STATUS_NAMES == ("BOTH", "LOCAL", "MIDI", "*EXT*")
        assert presets.PRESSURE_NAMES == ("NONE", "KEY", "CHANNEL", "OFF")
        assert presets.SUSTAIN_NAMES == ("ON", "OFF")

    def test_the_track_ranges_are_the_specifications(self):
        ranges = presets.TRACK_RANGES
        assert ranges["sound_number"] == (0, 0xFFFF)
        assert ranges["volume"] == (0, 127)
        assert ranges["pan"] == (-128, 127)
        assert ranges["timbre"] == (0, 127)
        assert ranges["release"] == (-64, 63)
        assert ranges["output"] == (0, 4)
        assert ranges["status"] == (0, 3)
        assert ranges["pressure"] == (0, 3)
        assert ranges["midi_channel"] == (0, 15)
        assert ranges["sustain"] == (0, 1)
        assert ranges["midi_program"] == (0, 127)
        assert ranges["key_low"] == (21, 108)
        assert ranges["key_high"] == (21, 108)
        assert ranges["transpose"] == (-59, 59)

    def test_every_effect_number_has_a_parameter_set(self):
        assert set(presets.EFFECT_SETS) == set(range(len(presets.EFFECTS)))
        for name in presets.EFFECT_SETS.values():
            assert name in presets.PARAM_SETS
            assert presets.PARAM_SETS[name]


class TestTheTrackReadings:
    """The panel's own words for a track's numbers."""

    def test_the_timbre_is_printed_through_the_table(self):
        assert presets.Track(timbre=64).timbre_display == 50
        assert presets.Track(timbre=127).timbre_display == 99

    def test_the_output_bus_is_named(self):
        assert presets.Track(output=0).output_name == "DRY"
        assert presets.Track(output=1).output_name == "FX1"
        assert presets.Track(output=4).output_name == "CONTROL"

    def test_the_midi_status_is_named(self):
        assert presets.Track(status=0).status_name == "BOTH"
        assert presets.Track(status=1).status_name == "LOCAL"
        assert presets.Track(status=2).status_name == "MIDI"
        assert presets.Track(status=3).status_name == "*EXT*"

    def test_the_midi_program_is_counted_from_one(self):
        assert presets.Track(midi_program=0).midi_program_display == 1
        assert presets.Track(midi_program=127).midi_program_display == 128

    def test_the_pressure_kind_is_named(self):
        assert presets.Track(pressure=0).pressure_name == "NONE"
        assert presets.Track(pressure=1).pressure_name == "KEY"
        assert presets.Track(pressure=2).pressure_name == "CHANNEL"
        assert presets.Track(pressure=3).pressure_name == "OFF"

    def test_the_sustain_switch_is_named(self):
        assert presets.Track(sustain=True).sustain_name == "ON"
        assert presets.Track(sustain=False).sustain_name == "OFF"

    def test_a_value_outside_the_list_is_refused(self):
        """A dump may hold what the unit wrote; the name does not exist."""
        with pytest.raises(ValueError, match="output 5 is outside 0-4"):
            _ = presets.Track(output=5).output_name
        with pytest.raises(ValueError, match="status 4 is outside 0-3"):
            _ = presets.Track(status=4).status_name
        with pytest.raises(ValueError, match="pressure 4 is outside 0-3"):
            _ = presets.Track(pressure=4).pressure_name

    def test_the_pan_label_is_the_word_or_the_number(self):
        assert presets.Track(pan=presets.PAN_SOUND).pan_label == "SOUND"
        assert presets.Track(pan=0).pan_label == "+0"
        assert presets.Track(pan=127).pan_label == "+99"
        assert presets.Track(pan=-1).pan_label == "-99"


class TestThePanRule:
    """The rule the module states, at every step of it.

    The rule is the module's own: 0x80 is SOUND, a byte with the top
    bit set is a pan to the left whose magnitude is the Note 7 table
    index, and a byte with the top bit clear is a pan to the right that
    is the table index itself.  These tests say exactly that, at the
    ends of the range, rather than that *some* number came out.
    """

    def test_sound_is_the_one_reserved_byte(self):
        assert presets.PAN_SOUND == -128
        assert presets.pan_display(-128) is None
        assert presets.pan_stored(None) == -128
        # Nothing else is SOUND.
        for stored in range(-127, 128):
            assert presets.pan_display(stored) is not None

    def test_the_right_hand_side_is_the_table_itself(self):
        for byte in range(0, 128):
            assert presets.pan_display(byte) == presets.DISPLAY_TABLE[byte]

    def test_the_left_hand_side_is_the_table_with_a_sign(self):
        """The byte's top bit is the sign; the other seven bits index.

        ``0x81`` is the byte 129, which the module stores as -127, and
        it reads -1: the table's first entry, negated.
        """
        for index in range(1, 128):
            assert presets.pan_display(index - 128) == -presets.DISPLAY_TABLE[index]

    def test_the_hard_ends_of_the_rule(self):
        assert presets.pan_display(0) == 0
        assert presets.pan_display(127) == 99
        assert presets.pan_display(-127) == -1
        assert presets.pan_display(-1) == -99

    def test_the_stored_side_is_the_same_rule_read_backwards(self):
        assert presets.pan_stored(0) == 0
        assert presets.pan_stored(99) == 127
        assert presets.pan_stored(-1) == -127
        assert presets.pan_stored(-99) == -1

    def test_every_value_the_stored_side_produces_reads_back(self):
        """The stored side is a rule, not a table: it inverts exactly."""
        for shown in range(-99, 100):
            stored = presets.pan_stored(shown)
            assert presets.pan_display(stored) == shown

    def test_a_pan_outside_the_range_is_refused(self):
        with pytest.raises(ValueError, match="pan 100 is outside -99-99"):
            presets.pan_stored(100)
        with pytest.raises(ValueError, match="pan -100 is outside -99-99"):
            presets.pan_stored(-100)


class TestByteEightTheEdges:
    """Byte 8 at the edges of its three fields."""

    def test_channel_one_and_channel_sixteen(self):
        """The unit counts the channels from one and stores them from zero."""
        for shown, stored in ((1, 0), (16, 15)):
            track = presets.Track(midi_channel=stored)
            assert track.midi_channel_display == shown
            assert track.to_bytes()[8] & 0x0F == stored
            assert presets.Track.from_bytes(track.to_bytes()).midi_channel == stored

    def test_every_status_the_unit_offers(self):
        for stored, name in enumerate(presets.STATUS_NAMES):
            track = presets.Track(status=stored)
            assert track.status_name == name
            assert track.to_bytes()[8] >> 6 == stored
            assert presets.Track.from_bytes(track.to_bytes()).status == stored

    def test_every_pressure_the_unit_offers(self):
        for stored, name in enumerate(presets.PRESSURE_NAMES):
            track = presets.Track(pressure=stored)
            assert track.pressure_name == name
            assert (track.to_bytes()[8] >> 4) & 0x03 == stored
            assert presets.Track.from_bytes(track.to_bytes()).pressure == stored

    def test_the_three_together_at_their_widest(self):
        track = presets.Track(status=3, pressure=2, midi_channel=15)
        assert track.to_bytes()[8] == 0xEF
        back = presets.Track.from_bytes(track.to_bytes())
        assert (back.status, back.pressure, back.midi_channel) == (3, 2, 15)

    def test_the_three_together_at_their_narrowest(self):
        track = presets.Track(status=0, pressure=0, midi_channel=0)
        assert track.to_bytes()[8] == 0x00
        back = presets.Track.from_bytes(track.to_bytes())
        assert (back.status, back.pressure, back.midi_channel) == (0, 0, 0)


class TestThePileHelpers:
    """The pile is where the on/off state lives."""

    def test_the_primary_track_is_the_first_byte(self):
        preset = presets.Preset.from_bytes(_single_dump()[48:216])
        assert preset.pile[0] == preset.primary_track == 1

    def test_switching_the_primary_track_on_again_changes_nothing(self):
        preset = presets.Preset.from_bytes(_single_dump()[48:216])
        before = preset.pile
        preset.set_active(1, True)
        assert preset.pile == before
        assert preset.is_active(1)

    def test_every_other_track_can_be_switched_on_and_off(self):
        for number in range(2, 9):
            preset = presets.Preset.from_bytes(_single_dump()[48:216])
            preset.set_active(number, True)
            assert preset.is_active(number)
            assert number in preset.pile[1:]
            preset.set_active(number, False)
            assert not preset.is_active(number)
            assert preset.pile == b"\x01" + b"\x00" * 7

    def test_a_track_outside_the_preset_is_refused_by_both_doors(self):
        preset = presets.Preset.from_bytes(_single_dump()[48:216])
        for number in (0, 9, -1):
            with pytest.raises(ValueError, match="outside 1-8"):
                preset.is_active(number)
            with pytest.raises(ValueError, match="outside 1-8"):
                preset.set_active(number, True)


class TestTheEffectParamConversions:
    """A row's own refusals: what it will not store."""

    def _row(self, set_name: str, key: str):
        return next(row for row in presets.PARAM_SETS[set_name] if row.key == key)

    def test_a_word_given_to_a_numbered_row_is_refused(self):
        row = self._row("Common Reverb", "decay_time")
        with pytest.raises(ValueError, match="takes a number"):
            row.encode("forty")

    def test_an_index_is_accepted_by_a_named_row(self):
        """The index the field numbers the value by is the stored value."""
        row = self._row("Common Reverb", "mod_source")
        assert row.encode(7) == 7
        assert row.names is not None
        assert row.encode(row.names[7]) == 7

    def test_a_value_outside_the_range_is_refused(self):
        row = self._row("Common Reverb", "detune_rate")
        with pytest.raises(ValueError, match="detune_rate 120 is outside 0-99"):
            row.encode(120)

    def test_a_name_that_is_not_in_the_list_is_refused(self):
        row = self._row("Common Reverb", "mod_source")
        with pytest.raises(ValueError, match="has no value 'NOPE'"):
            row.encode("NOPE")

    def test_an_index_outside_the_list_is_refused(self):
        row = self._row("Common Reverb", "mod_source")
        with pytest.raises(ValueError, match="mod_source 99 is outside 0-15"):
            row.encode(99)

    def test_a_signed_row_outside_the_range_is_refused(self):
        row = self._row("MultiVerb", "feedback")
        with pytest.raises(ValueError, match="feedback 100 is outside -99-99"):
            row.encode(100)

    def test_the_internal_value_of_a_row_is_the_specifications_own(self):
        raw = _single_dump()[48:216][152:163]
        effect = presets.Effect.from_bytes(raw)
        rows = {row.key: row for row in effect.rows}
        assert effect.value(rows["mod_destination"]) == 2
        assert effect.value(rows["mod_source"]) == 5

    def test_a_value_a_sixteen_step_row_cannot_print_is_refused(self):
        """A row stored 0..15 prints sixteen numbers, and no others."""
        row = self._row("Common Reverb", "hf_damping")
        assert row.high == 15
        with pytest.raises(ValueError, match="cannot print 5; it is stored 0-15"):
            row.encode(5)

    def test_an_effect_number_this_project_has_no_name_for(self):
        """The bytes are kept; the name and the rows are not invented."""
        effect = presets.Effect(number=31, params=b"\x00" * 10)
        assert effect.name is None
        assert effect.parameter_set is None
        assert effect.rows == ()


class TestTheSharedBytes:
    """Two parameters in one byte, and each keeps its own bits.

    **The table of rows and bits is built from the module's own
    tables**, not written out here: a hand-written list would go stale
    the moment a row changed, and the whole point of the model is that
    the tables are the one place a mask or a shift is written down.
    """

    def test_the_rows_of_a_set_do_not_overlap(self):
        """Two rows of one byte must not claim the same bits."""
        for name, rows in presets.PARAM_SETS.items():
            by_offset: dict = {}
            for row in rows:
                by_offset.setdefault(row.offset, []).append(row)
            for offset, group in by_offset.items():
                used = 0
                for row in group:
                    assert used & row.mask == 0, (
                        f"{name}: {row.key} claims bits another row of byte "
                        f"{offset} already claims"
                    )
                    used |= row.mask

    @pytest.mark.parametrize("set_name", sorted(presets.PARAM_SETS))
    def test_changing_one_row_leaves_every_other_row_alone(self, set_name):
        """A row writes its own bits and nobody else's.

        For every row of every parameter set: write a different value
        through the model, and prove that every *other* row of the same
        set still reads what it read before -- bit for bit -- and that
        the bytes the other rows live in are untouched altogether.
        """
        rows = presets.PARAM_SETS[set_name]
        effect = presets.Effect(number=0, params=_effect_params())
        for row in rows:
            changed = effect.with_value(row, _another_value(effect, row))
            assert changed.raw(row) != effect.raw(row), (
                f"{set_name}: {row.key} did not change"
            )
            for other in rows:
                if other.key == row.key:
                    continue
                before = (effect.params[other.offset] >> other.shift) & other.mask
                after = (changed.params[other.offset] >> other.shift) & other.mask
                assert after == before, f"{set_name}: {row.key} disturbed {other.key}"
                if other.offset != row.offset:
                    # A different byte altogether: the whole byte is
                    # untouched.
                    untouched = (
                        changed.params[other.offset] == (effect.params[other.offset])
                    )
                    assert untouched, (
                        f"{set_name}: {row.key} disturbed the byte of {other.key}"
                    )

    def test_the_shared_bytes_are_the_ones_the_specification_names(self):
        """Byte four and byte six of the common reverb, and no others."""
        rows = presets.PARAM_SETS["Common Reverb"]
        shared = {}
        for row in rows:
            shared.setdefault(row.offset, []).append(row)
        pairs = {offset: group for offset, group in shared.items() if len(group) > 1}
        assert set(pairs) == {4, 6}
        assert [row.key for row in pairs[4]] == ["mod_destination", "mod_source"]
        assert [row.key for row in pairs[6]] == ["hf_damping", "hf_bandwidth"]
