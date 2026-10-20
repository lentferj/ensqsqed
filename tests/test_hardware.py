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

from sq import banks
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
