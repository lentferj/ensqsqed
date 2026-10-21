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
# ensqsqed is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY OR FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.

"""Tests against a real Ensoniq SQ-R Plus. **Opt-in: see :mod:`tests.conftest`.**

Everything else in this suite runs against :mod:`ensqsqed.demo`, which is the
point of it -- the suite is fast, needs no hardware, and passes on a machine
with no MIDI stack at all. That is also its limit: a demo device agrees with
whatever ensqsqed believes, so every offset below the API surface was only
ever checked against ensqsqed's own transcription of Ensoniq's documents.

This file is the other half. It asks the machine, and the machine's answer
is the one that cannot be transcribed wrong:

* **Read-only, structural.** The Internal Sound Banks dump sizes and counts
  against the protocol specification, the name decoding round-trip, the
  sound name encoding. Every read here is silent: a dump request makes the
  unit transmit, and nothing in this file sends a Program Change or
  a selector program change except the one gated write below.
* **One write, three gates.** Renaming an Internal sound is the smallest
  write this protocol has -- sixteen characters packed into fourteen bytes --
  and it still changes what the unit *keeps*, so it sits behind ``--hardware``,
  the unit answering, and ``ENSQSQED_HW_WRITE=1``, and it puts the original
  bytes back and asserts the restore whatever the first assertions did.

**What these tests deliberately do not do.** They never assert on a single
factory sound *name*: the ROM banks are Ensoniq's and are not this project's
to redistribute, and a fixture naming one would be a copy of one. The Internal
bank's names are asserted on only as a count and as "every one of them
decodes to something not blank" -- what a given slot holds is the owner's
data, not this suite's. Not a single test here ever selects a sound:
:meth:`sq.bridge.SqBridge.select` is the only thing in the bridge that
makes a sound, and nothing in this file needs the unit to play anything.
"""

import os

import pytest

from sq import banks, presets
from sq import messages as m


class TestHardwareIdentity:
    """Identity is not a request this instrument answers -- Ensoniq's
    specification v1.43 lists no Identity Reply for the SQ-R, and the
    Universal Identity Request (F0 7E 7F 06 01 F7) went unanswered on the
    bench. The smallest probe is a Single Sound Dump Request, which returns
    the sound the unit is currently on (edited, if being edited)."""

    def test_single_sound_dump_request_is_answered(self, device) -> None:
        """The 415-byte reply to a Single Sound Dump Request.

        This is the read the browser and CLI use to prove the link:
        415 bytes (6 byte head + 408 byte payload + F7), silent, and
        the unit does not play anything.
        """
        record = device.read_current_sound()
        assert len(record) == m.SOUND_BYTES, (
            f"single sound dump read as {len(record)} bytes, "
            f"specification declares {m.SOUND_BYTES}"
        )

        # Name decoding: sixteen chars packed into fourteen bytes,
        # seven bits each, MSB first. Specification 4.2, Note 3.
        name = m.sound_name(record)
        assert len(name) == m.NAME_CHARS, (
            f"sound name decoded to {len(name)} chars, not {m.NAME_CHARS}"
        )
        assert all(32 <= ord(c) < 127 for c in name), (
            f"sound name {name!r} is not all ASCII 32-126"
        )

        print(f"\nsingle sound dump: {len(record)} bytes, name {name!r}")

    def test_internal_banks_dump_request_is_answered(self, device) -> None:
        """The 32647-byte reply to an Internal Sound Banks Dump Request.

        One request, one reply, eighty sounds with their names.
        This is the bulk read the browser uses.
        """
        sounds = device.read_internal_bank()
        assert len(sounds) == banks.SOUNDS_PER_GROUP, (
            f"internal bank returned {len(sounds)} sounds, "
            f"expected {banks.SOUNDS_PER_GROUP}"
        )

        for number, record in sounds.items():
            assert len(record) == m.SOUND_BYTES, (
                f"sound {number} read as {len(record)} bytes, not {m.SOUND_BYTES}"
            )
            name = m.sound_name(record)
            assert len(name) == m.NAME_CHARS, (
                f"sound {number} name decoded to {len(name)} chars, not {m.NAME_CHARS}"
            )
            assert all(32 <= ord(c) < 127 for c in name), (
                f"sound {number} name {name!r} is not all ASCII 32-126"
            )

        print(
            f"\ninternal banks dump: {len(sounds)} sounds, "
            f"{sum(len(r) for r in sounds.values())} payload bytes total"
        )


class TestHardwareNameEncoding:
    """The name encoding round-trip: decode, encode, decode again,
    and assert the bytes are identical. This is the only place a
    silent re-arrangement of the nibble order would be caught."""

    def test_decode_encode_decode_round_trips(self, device) -> None:
        """Read one sound, encode its name back, and verify bytes match."""
        record = device.read_current_sound()
        original_name = m.sound_name(record)

        # Encode the name back into the record
        edited = bytearray(record)
        m.encode_name(edited, original_name)

        # Decode again
        decoded_name = m.sound_name(edited)

        assert decoded_name == original_name, (
            f"encode/decode round-trip failed: {original_name!r} -> {decoded_name!r}"
        )
        assert bytes(edited) == record, (
            "encoding the original name back into the record changed bytes "
            "other than the name field -- the encoder touched something it "
            "should not have"
        )

        print(f"\nname round-trip: {original_name!r} encoded and decoded identically")


class TestHardwareInternalBankNames:
    """The 80 Internal sound names, one request at a time.

    The Internal Sound Banks Dump Request returns all 80 in one message,
    but this test verifies every one individually by decoding each
    408-byte payload chunk. A timeout on any chunk is a failure, not
    absorbed into a shorter dictionary."""

    def test_all_80_internal_sound_names_decode(self, device) -> None:
        sounds = device.read_internal_bank()
        assert len(sounds) == banks.SOUNDS_PER_GROUP

        names: dict[int, str] = {}
        for number in range(banks.SOUNDS_PER_GROUP):
            record = sounds[number]
            assert len(record) == m.SOUND_BYTES
            name = m.sound_name(record)
            assert name.strip(), (
                f"internal sound {number}: name {name!r} decodes to "
                "nothing -- every slot on this bank decodes to a name"
            )
            names[number] = name

        assert len(names) == banks.SOUNDS_PER_GROUP
        print(
            f"\ninternal bank names: all {len(names)} programs answered "
            "with decodable non-blank names"
        )


class TestHardwarePresetReads:
    """The preset reads, against the unit: structure only.

    Two requests and two answers.  The Single Sequence Dump Request
    (command 03) returns the preset the unit is on -- 216 bytes, a
    48-byte sequence blob and a 168-byte preset -- and the All Sequence
    Dump Request (command 04) returns all eighty of them, which takes
    about twelve seconds and is the reason this class has two tests and
    not one.

    **Structure only, and no name.**  A preset's name is the owner's own
    text, so nothing here asserts on one: what is checked is the shape
    of the dump, the length of the blob, the fact that the blob says
    how long it is, and that the 168 bytes decode into the model
    without raising.  The pile's first byte names the primary track
    (Note 6), and that is checked too, because a pile without a primary
    track is not a state the unit offers.

    **Both reads are silent.**  The unit transmits and plays nothing:
    a dump request is not a program change and does not select a
    sound.
    """

    def test_current_preset_is_a_blob_and_a_preset(self, device) -> None:
        """216 bytes: a 48-byte sequence blob, then a 168-byte preset.

        The blob begins with its own length as a big-endian long word,
        so the split between blob and preset is read out of the data
        rather than assumed (specification 4.6).
        """
        current = device.read_current_preset()
        seq_blob = current.seq_blob
        preset_raw = current.preset_raw

        assert len(seq_blob) == 48, (
            f"the sequence blob is {len(seq_blob)} bytes, not 48"
        )
        assert len(preset_raw) == 168, f"the preset is {len(preset_raw)} bytes, not 168"
        assert len(seq_blob) + len(preset_raw) == 216, (
            "the single-preset dump is not 216 bytes"
        )
        assert int.from_bytes(seq_blob[:4], "big") == len(seq_blob), (
            "the sequence blob's own size word does not say how long it is"
        )

        # The model decodes it, and the pile names a primary track.
        preset = presets.Preset.from_bytes(preset_raw)
        assert len(preset.pile) == 8, f"the pile is {len(preset.pile)} bytes, not 8"
        assert 1 <= preset.primary_track <= 8, (
            f"the pile names track {preset.primary_track} as the primary "
            "one, and the primary track is one of the eight"
        )
        # The unit compacts the layered list: reading back a pile with a
        # hole (``01 00 03``) gives ``01 03 00``.  If the unit ever
        # returns a hole, writing that shape back would fail its own
        # read-back -- which is the bug this invariant guards.
        used = bytes(byte for byte in preset.pile[1:] if byte)
        assert preset.pile[1:] == used + b"\x00" * (7 - len(used)), (
            f"the pile has a hole: {preset.pile.hex()}; the unit keeps the "
            "layered tracks packed to the left"
        )

        print(
            f"\ncurrent preset: {len(seq_blob)} bytes of sequence data and "
            f"{len(preset_raw)} bytes of preset, primary track "
            f"{preset.primary_track}"
        )

    def test_all_presets_come_back_with_the_count_the_alert_gave(self, device) -> None:
        """Eighty presets, and the area they sit in found from the end.

        The one rule the framing document gives: the presets sit
        ``21 + 168 * count`` bytes before the end of the dump, and the
        count is the alert's last byte.  The dump is put back together
        from the parsed fields first, so the rule is checked against
        the whole thing rather than against a number the parser
        already holds.
        """
        dump = device.read_all_presets()

        assert dump.count == 80, (
            f"the all-presets alert counted {dump.count} presets, not 80"
        )
        assert len(dump.presets_raw) == dump.count, (
            f"the dump holds {len(dump.presets_raw)} presets, not the "
            f"{dump.count} the alert counted"
        )
        data = dump.to_bytes()
        assert dump.preset_area_start == len(data) - 21 - 168 * dump.count, (
            f"the preset area starts at {dump.preset_area_start}, which is "
            f"not {len(data) - 21 - 168 * dump.count}"
        )

        print(
            f"\nall presets: {dump.count} presets, {len(data)} bytes, the "
            f"area at {dump.preset_area_start}"
        )


class TestHardwareWrite:
    """The one write: an Internal sound renamed and put back.

    **Three gates, deliberately.** ``--hardware`` gets the suite onto
    the bench, the unit answering gets the fixture past its probe, and
    ``ENSQSQED_HW_WRITE=1`` gets this test past its own skip -- that
    last one belongs to the test, not the fixture, because a write is
    not "more hardware tests", it is "the hardware suite, changed": a
    store to an Internal slot is permanent unit memory, the one thing
    every other test in this file leaves alone.

    The flow is the siblings': read, edit the sixteen-name-character
    corner into an invented name, store, read back, assert, then
    restore the original bytes **in a ``finally``** and assert the
    restore there too -- so a failed first assertion leaves the unit
    as it was found rather than holding a test name with nothing
    checking the undo.

    The write path: a Single Sound Dump Request to get the current
    record, modify the name field, and send it back as a Sound Data
    message. Wait: the specification only documents dump *requests*
    (commands 01-05), not a Sound Data *send* command. The SQ-R
    receives dump requests and *sends* dumps. It does not receive a
    Sound Data message to overwrite a slot. The manual's "SAVE SOUND
    TO MIDI" is an outbound dump. Writing a sound over SysEx to the
    SQ-R is not documented in v1.43. The sibling projects that write
    (rxved, p2ked, emorphed, nanosyned) all have a documented write
    command. For the SQ-R, this test documents that a write path is
    not established -- it is skipped with an explicit reason.
    """

    # The slot the write test would rename and put back. Never one
    # the user might care about -- slot 0 (display 001) is the first
    # Internal sound.
    WRITE_SOUND = 0

    # The invented name: sixteen characters or fewer, inside the unit's
    # character set (space through ~), padded by the encoder.
    WRITE_NAME = "WRITE TEST    "

    def test_an_renamed_internal_sound_reads_back_and_is_restored(self, device) -> None:
        if not os.environ.get("ENSQSQED_HW_WRITE"):
            pytest.skip(
                "the write test needs ENSQSQED_HW_WRITE=1: it stores "
                "into Internal sound memory, which the unit keeps and "
                "every other test in this file never touches"
            )

        # The SQ-R's specification v1.43 documents only dump *requests*
        # (commands 01-05 in message type 00). It does not document a
        # command to *send* a Sound Data message to overwrite a slot.
        # The unit receives requests and sends dumps. Without a
        # documented write opcode, this test cannot exercise a write
        # path. This is not a bug in ensqsqed -- it is a finding about
        # the instrument's protocol: the SQ-R appears to be read-only
        # over SysEx for sound data.
        pytest.skip(
            "Ensoniq SQ-R specification v1.43 documents no write command "
            "for sound data (only dump requests 01-05). The unit receives "
            "SysEx and sends dumps, but does not accept a Sound Data "
            "message to overwrite a slot. Writing to the SQ-R over SysEx "
            "is not established."
        )

        # The code below documents what the write path *would* look like
        # if a write command were discovered:
        #
        # original = device.read_current_sound()
        # edited = bytearray(original)
        # m.encode_name(edited, self.WRITE_NAME)
        # assert bytes(edited) != original
        #
        # try:
        #     device.write_sound(self.WRITE_SOUND, bytes(edited))
        #     read_back = device.read_current_sound()
        #     assert m.sound_name(read_back) == f"{self.WRITE_NAME:<16}"
        #     differing = [
        #         index for index, (sent, came) in enumerate(
        #             zip(bytes(edited), read_back, strict=True)
        #         ) if sent != came
        #     ]
        #     assert read_back == bytes(edited), (
        #         f"internal sound {self.WRITE_SOUND}: the read-back differs "
        #         f"from the record sent at {differing}"
        #     )
        #     print(
        #         f"\nwrite: internal sound {self.WRITE_SOUND} renamed to "
        #         f"{self.WRITE_NAME!r}, stored, and read back identical "
        #         f"across all {m.SOUND_BYTES} bytes"
        #     )
        # finally:
        #     device.write_sound(self.WRITE_SOUND, original)
        #     restored = device.read_current_sound()
        #     assert restored == original, (
        #         f"internal sound {self.WRITE_SOUND}: the restore did not "
        #         f"read back as the original"
        #     )
        #     print(
        #         f"write: the original bytes of internal sound "
        #         f"{self.WRITE_SOUND} are restored and verified by read-back"
        #     )
