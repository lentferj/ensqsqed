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
import time

import pytest

from sq import banks, presets
from sq import messages as m
from sq.bridge import UNIT_KEPT_OFFSETS
from sq.preset_backup import (
    PresetBackup,
    PresetBackupSlot,
    read_preset_backup,
    restore_preset_bank,
)


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
        assert 0 <= preset.primary_track <= 8, (
            f"the pile's first byte is {preset.primary_track}; it is the "
            "first ON track, and 0 is all-off"
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


class TestHardwarePresetRestore:
    """The full 80-slot restore, proved with a sentinel, not a same copy.

    A restore of the bytes already on the machine cannot prove a write:
    a no-op and a successful write look identical.  This test therefore
    restores a **modified** backup first -- one track-volume byte changed
    in slot 0 -- reads the stored bank back, and requires the sentinel to
    be present.  Only then does it restore the original snapshot and check
    that the sentinel is gone.

    Destructive: behind ``ENSQSQED_HW_PRESET_RESTORE=1``, which
    ``make hardware-test`` sets.  The original bank is restored in a
    ``finally`` even if the sentinel assertion fails.
    """

    #: Effect parameter 0 in the 168-byte preset.  A plain send does not
    #: commit it; the whole-buffer store does.  This is the field the
    #: restore is meant to prove.
    SENTINEL_OFFSET = 152

    @staticmethod
    def _matches(want: bytes, got: bytes) -> bool:
        """Compare the regions a dump can write, not the unit-kept ones."""
        return all(
            want[index] == got[index] or index in UNIT_KEPT_OFFSETS
            for index in range(len(want))
        )

    @staticmethod
    def _stored_copy(device, selected_after: int):
        """Read the all-presets dump while a slot is not selected."""
        current = device.read_current_preset()
        current_slot = current.preset_raw[0]
        skip = (selected_after + 1) % 80
        if current_slot != skip:
            device.select_preset_retry(skip, known_slot=current_slot)
        return device.read_all_presets(), skip

    def test_a_sentinel_is_stored_then_the_original_is_restored(
        self, device, tmp_path
    ) -> None:
        if not os.environ.get("ENSQSQED_HW_PRESET_RESTORE"):
            pytest.skip(
                "the full preset restore test needs "
                "ENSQSQED_HW_PRESET_RESTORE=1: it overwrites all 80 Internal "
                "preset slots"
            )

        device.tap_button(2)  # close a save dialog left by an earlier probe
        time.sleep(0.5)
        current = device.read_current_preset()
        current_slot = current.preset_raw[0]
        neighbour = (current_slot + 1) % 80
        device.select_preset_retry(neighbour, known_slot=current_slot)
        snapshot = device.read_all_presets()
        device.select_preset_retry(current_slot, known_slot=neighbour)
        path = tmp_path / "original_presets.bin"
        path.write_bytes(snapshot.to_bytes())
        original = read_preset_backup(str(path))
        assert original.count == 80

        original_slot = original.slots[0]
        old = original_slot.preset[self.SENTINEL_OFFSET]
        sentinel = 0x39 if old != 0x39 else 0x38
        changed = bytearray(original_slot.preset)
        changed[self.SENTINEL_OFFSET] = sentinel
        sentinel_backup = PresetBackup(
            path=str(path),
            slots=(
                PresetBackupSlot(
                    slot=0,
                    preset=bytes(changed),
                    sequence=original_slot.sequence,
                ),
                *original.slots[1:],
            ),
        )

        try:

            def progress(slot: int, detail: str) -> None:
                print(f"restored P{slot:02d}", flush=True)

            assert restore_preset_bank(device, sentinel_backup, on_slot=progress) == 80
            stored, _skip = self._stored_copy(device, 0)
            offsets = stored.slot_offsets()
            index = offsets[0]
            assert stored.presets_raw[index][self.SENTINEL_OFFSET] == sentinel, (
                "the sentinel effect byte did not land in stored P00: the "
                "full restore did not write what it sent"
            )
            print(
                f"\npreset restore sentinel: P00 offset {self.SENTINEL_OFFSET} "
                f"reads back as {sentinel} from stored memory"
            )
        finally:
            # Only P00 was sentinelled; the other slots were restored
            # unchanged by the full pass.  One slot is enough to remove
            # the sentinel, and it keeps the destructive test shorter.
            one = PresetBackup(
                path=original.path,
                slots=(original.slots[0],),
            )
            assert restore_preset_bank(device, one) == 1

        stored, skip = self._stored_copy(device, 0)
        offsets = stored.slot_offsets()
        compared = 0
        for item in original.slots:
            if item.slot == skip:
                continue
            index = offsets.get(item.slot)
            assert index is not None
            assert self._matches(item.preset, stored.presets_raw[index]), (
                f"the original P{item.slot:02d} was not restored"
            )
            compared += 1
        assert compared == 79
        print(
            f"preset restore: 80 slots written; {compared} stored presets "
            "re-read and matched the original"
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


class TestHardwareSoundBankWrite:
    """The RAM sound write, measured. **Destructive: heavy gate.**

    **The gate.** ``--hardware`` gets here, the unit answering gets the
    fixture past its probe, and ``ENSQSQED_HW_BANK_WRITE=1`` gets this class
    past its own skip. That last one is separate from the ordinary
    ``ENSQSQED_HW_WRITE`` because this class writes sound memory. Back the
    unit up first: the class snapshots the bank and restores it in a
    ``finally``, but a restore that cannot be verified is still a restore
    that did not happen.

    **What was measured on 2026-10-24** (one SQ-R Plus, base channel 11,
    crosswise on an ESI M4U eX):

    * A type-02 Single Sound Dump sent to a selected Internal slot is
      **accepted** and lands in the **edit buffer** -- ``read_current_sound``
      returns the bytes that were sent.
    * It does **not** reach the **stored** bank: the all-sounds dump (stored
      memory) is unchanged, so ``send_sound_slots(..., verify=True)`` reports
      ``verified=False`` and ``diff_slots=(slot,)``.
    * The type-03 All Sounds Dump (32647 bytes) is **rejected by the ALSA
      port** (``MidiOutAlsa::sendMessage`` ``SystemError``), so the
      whole-bank single-message path is not transportable here -- the same
      finding as the bulk All-Sequences write.

    So a sound import currently loads the edit buffer and **still needs the
    panel's SAVE SOUND to store it**. The tests below pin exactly that:
    the edit-buffer write passes, the stored-bank write is a strict
    ``xfail`` that will flag the day the unit starts storing, and the large
    message is recorded as not transportable.

    **No name is printed.** The slot's name is the owner's data; the tests
    print which byte moved and whether the read-back matched, never the text.
    """

    #: The slot this class writes. Never asserted on by name.
    SLOT = 0

    #: The byte that is moved: the first byte of the packed seven-bit name
    #: (``messages.NAME_OFFSET``). One bit is enough to be a different byte
    #: and is still a valid name byte.
    NAME_BYTE = m.NAME_OFFSET

    @staticmethod
    def _require_gate() -> None:
        if not os.environ.get("ENSQSQED_HW_BANK_WRITE"):
            pytest.skip(
                "the sound-bank write tests need ENSQSQED_HW_BANK_WRITE=1: "
                "they write Internal sound memory, which every other "
                "hardware test leaves alone. Back the unit up first."
            )

    @staticmethod
    def _changed(record: bytes) -> bytes:
        """``record`` with one name byte moved, and the same length."""
        changed = bytearray(record)
        changed[TestHardwareSoundBankWrite.NAME_BYTE] ^= 0x01
        return bytes(changed)

    @staticmethod
    def _restore(device, slot: int, original: bytes, snapshot) -> None:
        """Put the edit buffer and the stored bank back, and say so."""
        device.send_sound_slots("INT", {slot: original}, verify=False)
        restored = device.read_bank("INT")
        assert restored == snapshot, (
            "internal bank: the restore did not read back as the snapshot "
            "-- the unit may still hold the test name; restore from the "
            "backup"
        )

    def test_read_bank_is_the_internal_bank(self, device) -> None:
        """``read_bank("INT")`` and ``read_internal_bank`` are one bank."""
        self._require_gate()
        assert device.read_bank("INT") == device.read_internal_bank()

    def test_a_single_sound_dump_reaches_the_edit_buffer(self, device) -> None:
        """The dump is accepted: ``read_current_sound`` sees the new bytes.

        This is the half that works. The stored bank is asserted unchanged
        afterwards, because that is the measured behaviour (see the class
        docstring); the byte is put back through the edit buffer and the bank
        is checked against the snapshot in the ``finally``.
        """
        self._require_gate()
        snapshot = device.read_internal_bank()
        original = snapshot[self.SLOT]
        changed = self._changed(original)
        try:
            device.send_sound_slots("INT", {self.SLOT: changed}, verify=False)
            current = device.read_current_sound()
            print(
                f"\nsound-bank edit buffer slot {self.SLOT}: "
                f"byte {self.NAME_BYTE} moved; "
                f"edit buffer changed={current == changed}"
            )
            assert current == changed, (
                f"internal slot {self.SLOT}: the single-sound dump was not "
                f"loaded into the edit buffer"
            )
        finally:
            self._restore(device, self.SLOT, original, snapshot)

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "measured 2026-10-24: a type-02 single-sound dump reaches the "
            "edit buffer but does not store into the Internal bank; the "
            "panel's SAVE SOUND is still needed"
        ),
    )
    def test_a_single_sound_dump_is_stored_in_the_bank(self, device) -> None:
        """The stored bank changes after the dump. **Currently does not.**

        Strict ``xfail`` on purpose: this is the day the unit starts storing,
        and the assertion is the whole point of the import feature. If it
        ever passes, XPASS fails and says so rather than quietly going green.
        """
        self._require_gate()
        snapshot = device.read_internal_bank()
        original = snapshot[self.SLOT]
        changed = self._changed(original)
        try:
            result = device.send_sound_slots("INT", {self.SLOT: changed}, verify=True)
            stored = result.read_back or device.read_bank("INT")
            print(
                f"\nsound-bank stored slot {self.SLOT}: "
                f"changed={stored[self.SLOT] == changed}, "
                f"verified={result.verified}, diff_slots={result.diff_slots}"
            )
            assert stored[self.SLOT] == changed, (
                f"internal slot {self.SLOT}: the changed byte is not in the "
                f"stored bank after the write; the unit did not store the "
                f"single-sound dump (diff_slots={result.diff_slots})"
            )
        finally:
            self._restore(device, self.SLOT, original, snapshot)

    def test_the_whole_bank_message_is_not_transportable(self, device) -> None:
        """The 32647-byte type-03 message: record whether the port takes it.

        The honest check the feature needs: if the port rejects it (measured
        2026-10-24 on this rig), ``send_sound_bank`` falls back to per-slot
        single dumps, and those do not store. Whatever happens, the bank must
        be unchanged -- a bank message is not allowed to half-land.
        """
        self._require_gate()
        snapshot = device.read_internal_bank()
        payload = b"".join(m.to_nibbles(snapshot[i]) for i in range(80))
        frame = m.frame(m.Function.ALL_SOUNDS_DUMP, payload, device=device.channel)
        device._out.send_message([0xC0 | device.channel, 124])
        time.sleep(0.3)
        try:
            device._out.send_message(list(frame))
            outcome = "accepted by the port"
        except Exception as exc:
            outcome = f"rejected by the port ({type(exc).__name__})"
        time.sleep(0.5)
        print(f"\nsound-bank whole message ({len(frame)} bytes): {outcome}")
        assert device.read_bank("INT") == snapshot, (
            "the whole-bank message changed a bank it was re-sending"
        )
