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
# FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License
# for more details.

"""Sending a preset: the handshake, and what happens when it does not work.

**No hardware here.** The bytes that go out are compared with the ones
the bench probe captured (``docs/PRESET-FRAMING.md`` section 9), and
the unit on the other end of the wire is a small object that reads the
message that went out and answers it: an acknowledgement for the
alert, and an alert and a dump for the read request. That is the whole
protocol, and faking it is what makes the failure paths testable at
all -- a NAK and a silence cannot be asked for of a real unit without
breaking it.

What is pinned here:

* the **alert's bytes**, because they are what the unit reads first
  and a wrong size in them means the dump is never looked at;
* the **order** -- the dump goes out only after the acknowledgement,
  and never at all after a refusal;
* the **nibble-ization** of the dump, which is the same message the
  unit itself sends;
* the **verification**, which compares the bytes that were sent with
  the bytes that come back, and says which offsets differ when they
  do not match;
* the **retry**, which asks again from the alert when the read-back
  does not match -- the unit measured on 2026-10-22 accepted
  byte-identical exchanges two times out of two before panel
  navigation and one out of ten after it, with no cause to name, and
  asking again is the app's answer to a receiver like that.

Nothing here asserts on a name: the names in the fixture are factory
defaults, and a name is not part of the contract.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import List, Optional

import pytest

from sq import bridge as b
from sq import messages as m

FIXTURES = Path(__file__).parent / "fixtures"

#: The captured single-sequence dump: a 48-byte sequence blob and a
#: 168-byte preset, 216 bytes in all.
DUMP = (FIXTURES / "single_seq_default.bin").read_bytes()

#: The two halves of it, which are what the send path takes.
BLOB = DUMP[:48]
PRESET = DUMP[48:]

#: The alert the bench probe captured for that dump: command 06, the
#: size of the sequence data as a big-endian long word (0x30 = 48),
#: and the sequence/song flag (0x00 = sequence).
PROBE_ALERT = bytes([0x06, 0x00, 0x00, 0x00, 0x30, 0x00])

#: The two timing constants, as the code ships them.
#:
#: They are captured here, at import, because the fixture below
#: shortens the settle for the tests' sake: the number under test is
#: the one the program ships with, not the one the tests run with.
SHIPPED_SETTLE = b.PRESET_SETTLE
SHIPPED_ACK_WINDOW = b.ACK_WINDOW


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


class _FakeUnit:
    """A unit that answers the way the one on the bench answers.

    It is not a recording: it reads the message that went out and
    answers it, so the alert gets an acknowledgement and the read
    request gets an alert and a dump -- the two the send path has to
    tell apart.
    """

    def __init__(
        self,
        *,
        ack: Optional[bool] = True,
        read_back: Optional[bytes] = None,
        echo: bool = False,
        alert_size: int = 48,
    ) -> None:
        #: Whether the alert is acknowledged. ``None`` means the unit
        #: says nothing inside the window, which is the silence path.
        self.ack = ack
        #: The 216 bytes the unit will read back.
        self.read_back = DUMP if read_back is None else read_back
        #: Whether the unit's messages come back, as they do on a
        #: chain with a soft thru.
        self.echo = echo
        #: The size the unit's own alert carries: the length of the
        #: sequence data it is about to send.
        self.alert_size = alert_size
        #: The dumps the unit was sent, de-nibbled.
        self.received: List[bytes] = []
        #: For each dump received, whether the acknowledgement had
        #: already gone out. The dump must never come first.
        self.acked_first: List[bool] = []
        self.ack_sent = False

    def answer(self, message: bytes, inbox: List[bytes]) -> None:
        """Answer one message from the program."""
        if self.echo:
            inbox.append(message)
        packet = m.parse(message)
        if packet is None:
            return
        if packet.function == m.Function.SINGLE_SEQUENCE_DUMP:
            self.acked_first.append(self.ack_sent)
            self.received.append(m.from_nibbles(packet.payload))
            return
        if packet.function != m.Function.COMMAND:
            return
        body = m.from_nibbles(packet.payload)
        if not body:
            return
        command = body[0]
        if command == m.CommandType.SINGLE_SEQUENCE_ALERT:
            if self.ack is None:
                return
            code = m.ErrorCode.ACK if self.ack else m.ErrorCode.NAK
            self.ack_sent = True
            inbox.append(m.frame(m.Function.ERROR, m.to_nibbles([code]), device=0))
            return
        if command == m.CommandType.SINGLE_SEQUENCE_REQUEST:
            inbox.append(
                m.frame(
                    m.Function.COMMAND,
                    b.PresetAlert(
                        command=m.CommandType.SINGLE_SEQUENCE_ALERT,
                        size=self.alert_size,
                        flag=0,
                    ).wire_payload(),
                    device=0,
                )
            )
            inbox.append(
                m.frame(
                    m.Function.SINGLE_SEQUENCE_DUMP,
                    m.to_nibbles(self.read_back),
                    device=0,
                )
            )


class _FakeOut:
    """A MIDI output that answers through the unit."""

    def __init__(self, port: _FakeIn, unit: _FakeUnit) -> None:
        self.port = port
        self.unit = unit
        self.sent: List[bytes] = []

    def send_message(self, data) -> None:
        self.sent.append(bytes(data))
        self.unit.answer(bytes(data), self.port.inbox)


class FakeExchangeBridge:
    """A bridge, and the unit at the other end of it.

    The unit answers as it is told to: an acknowledgement or a refusal
    for the alert, and a dump for the read request.
    """

    def __init__(self, unit: _FakeUnit, *, timeout: float = 0.05) -> None:
        self.port = _FakeIn()
        self.sink = _FakeOut(self.port, unit)
        self.unit = unit
        self.bridge = b.SqBridge(self.port, self.sink, channel=0, timeout=timeout)

    def sent(self) -> List[bytes]:
        """Every message that went out, in order."""
        return list(self.sink.sent)


@pytest.fixture(autouse=True)
def _no_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    """The settle is half a second on the bench; the tests do not wait.

    The constant is not changed in the code: it is the probe's, and the
    probe measured that it is needed. Here it is only a wait.
    """
    monkeypatch.setattr(b, "PRESET_SETTLE", 0.0)


# --- the alert ----------------------------------------------------------------


def test_the_alert_is_the_one_the_probe_captured():
    """Command 06, the size of the sequence data, and the flag."""
    exchange = FakeExchangeBridge(_FakeUnit())

    exchange.bridge.send_preset(BLOB, PRESET, verify=False)

    alert = exchange.sent()[0]
    packet = m.parse(alert)
    assert packet is not None
    assert packet.function == m.Function.COMMAND
    body = m.from_nibbles(packet.payload)
    # The exact six bytes the bench captured.
    assert body == PROBE_ALERT
    # And the alert the module builds says the same thing.
    assert b.PresetAlert.parse(packet.payload) == b.PresetAlert(
        command=m.CommandType.SINGLE_SEQUENCE_ALERT, size=48, flag=0
    )


def test_the_alert_size_counts_the_sequence_data_only():
    """48 bytes, not the 216 of the whole dump.

    The size is what the unit reads to know how much is coming, and it
    is the sequence data alone that it counts -- the preset follows
    behind it without being announced.
    """
    exchange = FakeExchangeBridge(_FakeUnit())

    exchange.bridge.send_preset(BLOB, PRESET, verify=False)

    packet = m.parse(exchange.sent()[0])
    assert packet is not None
    alert = b.PresetAlert.parse(packet.payload)
    assert alert.size == len(BLOB) == 48
    assert alert.size != len(BLOB) + len(PRESET)
    assert alert.flag == 0
    assert not alert.is_song


# --- the dump -----------------------------------------------------------------


def test_the_dump_goes_after_the_acknowledgement():
    """The ACK comes first, and the dump carries both halves."""
    exchange = FakeExchangeBridge(_FakeUnit())

    exchange.bridge.send_preset(BLOB, PRESET, verify=False)

    # Two messages went out: the alert and the dump.
    assert len(exchange.sent()) == 2
    dump = m.parse(exchange.sent()[1])
    assert dump is not None
    assert dump.function == m.Function.SINGLE_SEQUENCE_DUMP
    # The payload is nibble-ized: 432 bytes for 216 bytes of data.
    assert len(dump.payload) == 432
    assert m.from_nibbles(dump.payload) == BLOB + PRESET
    # And the unit had the acknowledgement before the dump arrived.
    assert exchange.unit.acked_first == [True]
    assert exchange.unit.received == [BLOB + PRESET]


def test_the_dump_is_the_message_the_unit_itself_sends():
    """Type 04, the same bytes in the same order, on the base channel."""
    exchange = FakeExchangeBridge(_FakeUnit())

    exchange.bridge.send_preset(BLOB, PRESET, verify=False)

    # The very message the unit puts on the wire as the answer to a
    # Single Sequence Dump Request -- frame and all.
    assert exchange.sent()[1] == m.frame(
        m.Function.SINGLE_SEQUENCE_DUMP,
        m.to_nibbles(BLOB + PRESET),
        device=0,
    )


# --- the refusals and the silences --------------------------------------------


def test_a_nak_is_a_refusal_and_nothing_is_sent():
    """A NAK means the dump does not go."""
    exchange = FakeExchangeBridge(_FakeUnit(ack=False))

    with pytest.raises(b.PresetRefused) as caught:
        exchange.bridge.send_preset(BLOB, PRESET, verify=False)

    message = str(caught.value)
    assert "refused" in message
    assert "NAK" in message
    # It is also a device error: the exchange stopped half way.
    assert isinstance(caught.value, b.DeviceError)
    # The alert went out; the dump did not.
    assert len(exchange.sent()) == 1
    assert exchange.unit.received == []


def test_silence_after_the_alert_is_a_timeout(monkeypatch: pytest.MonkeyPatch):
    """Nothing came back for the alert: a timeout that says so."""
    monkeypatch.setattr(b, "ACK_WINDOW", 0.2)
    exchange = FakeExchangeBridge(_FakeUnit(ack=None))

    with pytest.raises(TimeoutError) as caught:
        exchange.bridge.send_preset(BLOB, PRESET, verify=False)

    message = str(caught.value)
    assert "no ACK came back" in message
    assert "Single Sequence Dump Alert" in message
    assert "0.2 s" in message
    # The sentence names the causes rather than picking one.
    assert "powered off" in message
    assert "wrong base MIDI channel" in message
    # The alert went out; the dump did not.
    assert len(exchange.sent()) == 1
    assert exchange.unit.received == []


def test_the_acknowledgement_window_is_the_specifications_second():
    """The window is a second, and it is not this program's to choose."""
    assert SHIPPED_ACK_WINDOW == 1.0


def test_the_settle_is_the_probe_s_half_second():
    """The unit redraws its screen when it takes the dump, and the probe
    waited half a second for it.  That half second is the number here."""
    assert SHIPPED_SETTLE == 0.5


def test_the_settle_and_the_window_are_two_different_numbers():
    """The settle is the probe's; the window is the specification's."""
    assert SHIPPED_SETTLE != SHIPPED_ACK_WINDOW


# --- the verification ---------------------------------------------------------


def test_a_verified_send_reads_back_what_was_sent():
    """The unit holds the bytes that were sent, and says so."""
    exchange = FakeExchangeBridge(_FakeUnit())

    result = exchange.bridge.send_preset(BLOB, PRESET, verify=True)

    assert result.acked is True
    assert result.verified is True
    assert result.diff_offsets == ()
    assert result.read_back is not None
    assert result.read_back.seq_blob == BLOB
    assert result.read_back.preset_raw == PRESET


def test_without_verification_nothing_is_read_back():
    """No verification means no read request either."""
    exchange = FakeExchangeBridge(_FakeUnit())

    result = exchange.bridge.send_preset(BLOB, PRESET, verify=False)

    assert result.verified is False
    assert result.read_back is None
    assert result.diff_offsets == ()
    # Two messages went out: the alert and the dump. No read request.
    assert len(exchange.sent()) == 2
    assert m.parse(exchange.sent()[-1]) is not None
    assert m.parse(exchange.sent()[-1]).function == m.Function.SINGLE_SEQUENCE_DUMP


def test_a_read_back_that_differs_is_an_error_naming_the_offsets():
    """A mismatch is a failure, and it says where."""
    changed = bytearray(DUMP)
    # Offset 82 is track one's volume, counted from the first byte of
    # the sequence data -- the framing document's own numbering.
    changed[82] = (changed[82] + 1) % 256
    exchange = FakeExchangeBridge(_FakeUnit(read_back=bytes(changed)))

    with pytest.raises(b.DeviceError) as caught:
        exchange.bridge.send_preset(BLOB, PRESET, verify=True)

    message = str(caught.value)
    assert "82" in message
    assert isinstance(caught.value, b.PresetMismatch)
    # The whole tuple is on the exception, not only the first few.
    assert caught.value.diff_offsets == (82,)
    assert caught.value.result.diff_offsets == (82,)
    assert caught.value.result.verified is False


def test_the_offsets_are_counted_from_the_first_byte_of_the_sequence_data():
    """Dump offsets, not preset offsets."""
    changed = bytearray(DUMP)
    changed[0] = (changed[0] + 1) % 256
    exchange = FakeExchangeBridge(_FakeUnit(read_back=bytes(changed)))

    with pytest.raises(b.DeviceError) as caught:
        exchange.bridge.send_preset(BLOB, PRESET, verify=True)

    assert isinstance(caught.value, b.PresetMismatch)
    assert caught.value.diff_offsets == (0,)


def test_an_echo_of_our_own_messages_is_not_the_read_back():
    """A soft thru returns what we sent; the drain throws it away.

    The alert and the dump come back on any chain with a soft thru,
    and the dump is the same message type as the answer to a read
    request. The read-back therefore drains before it asks, and the
    echo never gets to be mistaken for the answer.
    """
    exchange = FakeExchangeBridge(_FakeUnit(echo=True))

    result = exchange.bridge.send_preset(BLOB, PRESET, verify=True)

    assert result.verified is True
    assert result.read_back is not None
    assert result.read_back.seq_blob == BLOB
    # The echo did arrive; the drain is what made it harmless.
    assert len(exchange.sent()) == 3


# --- the retry -----------------------------------------------------------------


def _changed_dump(offset: int) -> bytes:
    """The fixture with one byte changed: a read-back that differs."""
    changed = bytearray(DUMP)
    changed[offset] = (changed[offset] + 1) % 256
    return bytes(changed)


class _FlakyUnit(_FakeUnit):
    """A unit whose read-back is a list of answers, taken in order.

    The last answer is reused once the list runs out. This is the
    measured flakiness in miniature: the same bytes and the same
    exchange, accepted on one attempt and coming back wrong on the
    next, with no cause to point at (2026-10-22: byte-identical
    replicas accepted 2/2 before panel navigation and 1/10 after,
    while the real method landed 5/5 in the same window).
    """

    def __init__(self, *, read_backs: List[bytes]) -> None:
        super().__init__()
        self.read_backs = list(read_backs)
        #: How many read requests the unit has answered.
        self.reads = 0

    def answer(self, message: bytes, inbox: List[bytes]) -> None:
        packet = m.parse(message)
        if (
            packet is not None
            and packet.function == m.Function.COMMAND
            and m.from_nibbles(packet.payload)[:1]
            == bytes([m.CommandType.SINGLE_SEQUENCE_REQUEST])
        ):
            self.reads += 1
            which = min(self.reads, len(self.read_backs))
            self.read_back = self.read_backs[which - 1]
        super().answer(message, inbox)


def test_the_retry_sends_again_when_the_first_read_back_differs():
    """One exchange came back wrong, the second did not: the retry is
    what turned the mismatch into the verified send."""
    unit = _FlakyUnit(read_backs=[_changed_dump(82), DUMP])
    exchange = FakeExchangeBridge(unit)

    result = exchange.bridge.send_preset_with_retry(BLOB, PRESET)

    assert result.acked is True
    assert result.verified is True
    assert result.diff_offsets == ()
    # Two read requests: the failed try's and the successful one's.
    assert unit.reads == 2
    # Two whole exchanges went out: alert, dump, read request, twice.
    assert len(exchange.sent()) == 6
    # And both dumps were the same bytes.
    dumps = [
        packet
        for packet in (m.parse(raw) for raw in exchange.sent())
        if packet is not None and packet.function == m.Function.SINGLE_SEQUENCE_DUMP
    ]
    assert len(dumps) == 2
    assert m.from_nibbles(dumps[0].payload) == BLOB + PRESET
    assert m.from_nibbles(dumps[1].payload) == BLOB + PRESET
    # And each dump came after its own acknowledgement.
    assert exchange.unit.acked_first == [True, True]


def test_when_the_retries_run_out_the_last_mismatch_is_the_one_raised():
    """Three tries, three mismatches -- and the offsets on the
    exception are the last read-back's, not the first try's."""
    unit = _FlakyUnit(
        read_backs=[_changed_dump(82), _changed_dump(83), _changed_dump(83)]
    )
    exchange = FakeExchangeBridge(unit)

    with pytest.raises(b.PresetMismatch) as caught:
        exchange.bridge.send_preset_with_retry(BLOB, PRESET, attempts=3)

    assert caught.value.diff_offsets == (83,)
    # Three tries were made, and three whole exchanges went out.
    assert unit.reads == 3
    assert len(exchange.sent()) == 9


def test_attempts_of_one_is_send_preset_with_nothing_added():
    """One try, one mismatch, raised. The helper does not retry."""
    unit = _FlakyUnit(read_backs=[_changed_dump(82), DUMP])
    exchange = FakeExchangeBridge(unit)

    with pytest.raises(b.PresetMismatch) as caught:
        exchange.bridge.send_preset_with_retry(BLOB, PRESET, attempts=1)

    assert caught.value.diff_offsets == (82,)
    assert unit.reads == 1
    # One exchange only: the alert, the dump, the read request.
    assert len(exchange.sent()) == 3


def test_attempts_below_one_is_refused_before_anything_is_sent():
    """A retry of no attempts is a mistake, and it is caught here."""
    exchange = FakeExchangeBridge(_FlakyUnit(read_backs=[DUMP]))

    with pytest.raises(ValueError) as caught:
        exchange.bridge.send_preset_with_retry(BLOB, PRESET, attempts=0)

    assert "at least one attempt" in str(caught.value)
    assert "0" in str(caught.value)
    assert exchange.sent() == []


def test_a_read_back_that_differs_only_in_the_units_own_regions_verifies():
    """The 2026-10-22 restore, in miniature: the store landed, and the
    read-back differed only at the offsets the unit rewrites on
    receipt.

    The real run: the owner's backup of P79 carried the factory
    channel bytes ``0x81``..``0x87`` at preset offsets 55 through 139
    step 14; the unit's read-back carried its own ``0x80`` there and
    matched everywhere else. The old comparison called that a
    mismatch three times and the assist refused a landed store. The
    comparison skips :data:`b.UNIT_KEPT_OFFSETS` now, so these
    read-backs verify.
    """
    read_back = bytearray(DUMP)
    for preset_offset in (55, 69, 83, 97, 111, 125, 139):
        read_back[48 + preset_offset] = (read_back[48 + preset_offset] + 1) % 256
    # The unit's other keepings, for good measure: the name bytes and
    # the address word.
    read_back[48 + 0] = (read_back[48 + 0] + 1) % 256
    read_back[48 + 1] = (read_back[48 + 1] + 1) % 256
    read_back[50] = (read_back[50] + 1) % 256
    read_back[48 + 30] = (read_back[48 + 30] + 1) % 256
    unit = _FlakyUnit(read_backs=[bytes(read_back)])
    exchange = FakeExchangeBridge(unit)

    result = exchange.bridge.send_preset_with_retry(BLOB, PRESET)

    assert result.verified is True
    assert result.diff_offsets == ()
    assert unit.reads == 1
    # One exchange: the mismatch regions bought no retry.
    assert len(exchange.sent()) == 3


def test_a_difference_outside_the_units_own_regions_still_mismatches():
    """The skip is for the unit's regions only: a real difference, on
    its own or beside the kept ones, is still a mismatch."""
    read_back = bytearray(DUMP)
    # Kept regions differ...
    read_back[48 + 55] = (read_back[48 + 55] + 1) % 256
    read_back[48 + 139] = (read_back[48 + 139] + 1) % 256
    # ...and so does byte 34, track one's volume, which the unit takes.
    read_back[48 + 34] = (read_back[48 + 34] + 1) % 256
    unit = _FlakyUnit(read_backs=[bytes(read_back)])
    exchange = FakeExchangeBridge(unit)

    with pytest.raises(b.PresetMismatch) as caught:
        exchange.bridge.send_preset_with_retry(BLOB, PRESET)

    assert caught.value.diff_offsets == (82,)


# --- the sizes ----------------------------------------------------------------


def test_a_preset_of_the_wrong_size_is_refused_before_anything_is_sent():
    """168 bytes, and the check happens here rather than on the unit."""
    exchange = FakeExchangeBridge(_FakeUnit())

    with pytest.raises(ValueError) as caught:
        exchange.bridge.send_preset(BLOB, PRESET[:-1], verify=False)

    assert "168" in str(caught.value)
    assert exchange.sent() == []


def test_sequence_data_that_disagrees_with_its_own_size_word_is_refused():
    """The blob says how long it is, and the two have to agree."""
    exchange = FakeExchangeBridge(_FakeUnit())
    wrong = bytearray(BLOB)
    wrong[3] = 0x31

    with pytest.raises(ValueError) as caught:
        exchange.bridge.send_preset(bytes(wrong), PRESET, verify=False)

    assert "48" in str(caught.value)
    assert "49" in str(caught.value)
    assert exchange.sent() == []


def test_sequence_data_shorter_than_its_own_size_word_is_refused():
    """Four bytes are the size word; fewer than that cannot be one."""
    exchange = FakeExchangeBridge(_FakeUnit())

    with pytest.raises(ValueError) as caught:
        exchange.bridge.send_preset(b"\x00\x00", PRESET, verify=False)

    assert "four bytes" in str(caught.value)
    assert exchange.sent() == []


def test_a_read_back_whose_sequence_data_is_a_different_length_is_an_error():
    """Not a byte that differs: a sequence that changed size.

    The comparison is byte for byte, and a sequence of another length
    cannot be compared byte for byte at all -- so it is refused with
    both lengths in the sentence.
    """
    shorter = DUMP[:44] + DUMP[48:]
    exchange = FakeExchangeBridge(_FakeUnit(read_back=shorter, alert_size=44))

    with pytest.raises(b.DeviceError) as caught:
        exchange.bridge.send_preset(BLOB, PRESET, verify=True)

    message = str(caught.value)
    assert "44 bytes" in message
    assert "48 bytes" in message
    # It is not a mismatch: nothing that differs was compared.
    assert not isinstance(caught.value, b.PresetMismatch)


# --- the name the plan gave it -----------------------------------------------


def test_the_plan_s_name_calls_the_same_method():
    """``send_current_preset`` is ``send_preset``."""
    exchange = FakeExchangeBridge(_FakeUnit())

    result = exchange.bridge.send_current_preset(BLOB, PRESET, verify=True)

    assert result.verified is True


def test_the_sent_result_says_what_happened() -> None:
    """The dataclass is frozen and says the four things it says."""
    result = b.PresetSent(acked=True, verified=True, diff_offsets=(), read_back=None)

    assert result.acked is True
    assert result.verified is True
    assert result.diff_offsets == ()
    assert result.read_back is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.acked = False  # type: ignore[misc]


# --- the preset walk -----------------------------------------------------------


class _WalkUnit(_FakeUnit):
    """A unit whose preset selection moves on buttons 5 and 7.

    Button 5 steps one slot down, button 7 one slot up (mod 80) -- the
    measured behaviour of the unit.  ``drop_presses`` names the 1-based
    presses that are swallowed: the message still arrives, but the slot
    does not move, which is what a dropped press looks like on the wire.
    """

    def __init__(
        self,
        *,
        start_slot: int = 5,
        drop_presses: Optional[set] = None,
        drop_all: bool = False,
    ) -> None:
        super().__init__()
        self.slot = start_slot % 80
        self.drop_presses = set(drop_presses or ())
        self.drop_all = drop_all
        self.press_count = 0
        self.buttons: List[int] = []

    def answer(self, message: bytes, inbox: List[bytes]) -> None:
        packet = m.parse(message)
        if packet is not None and packet.function == m.Function.COMMAND:
            body = m.from_nibbles(packet.payload)
            if body and body[0] == m.CommandType.VIRTUAL_BUTTON:
                button = body[1] if len(body) > 1 else 0
                if button in (5, 7):
                    self.buttons.append(button)
                    self.press_count += 1
                    if not self.drop_all and self.press_count not in self.drop_presses:
                        self.slot = (self.slot + (1 if button == 7 else -1)) % 80
                return
            if body and body[0] == m.CommandType.SINGLE_SEQUENCE_REQUEST:
                read_back = bytearray(DUMP)
                read_back[48] = self.slot
                self.read_back = bytes(read_back)
        super().answer(message, inbox)


def test_the_walks_message_count_for_a_small_distance(monkeypatch: pytest.MonkeyPatch):
    """Two presses: four button messages and one check-read."""
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    unit = _WalkUnit(start_slot=2)
    exchange = FakeExchangeBridge(unit)

    result = exchange.bridge.select_preset_by_walk(0, known_slot=2)

    assert result == 0
    # Two press+release pairs, then one read of the working preset.
    assert len(exchange.sent()) == 2 * 2 + 1
    assert unit.press_count == 2


def test_a_dropped_press_is_detected_and_corrected(monkeypatch: pytest.MonkeyPatch):
    """One swallowed press shows up at the check-read and is re-synced."""
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    unit = _WalkUnit(start_slot=2, drop_presses={1})
    exchange = FakeExchangeBridge(unit)

    result = exchange.bridge.select_preset_by_walk(0, known_slot=2)

    assert result == 0
    # The first round pressed twice, the second round once more.
    assert unit.press_count == 3


def test_the_walk_takes_the_shorter_way_with_the_next_button(
    monkeypatch: pytest.MonkeyPatch,
):
    """P78 -> P02 is four "next" presses (button 7), not 76 "previous" ones."""
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    unit = _WalkUnit(start_slot=78)
    exchange = FakeExchangeBridge(unit)

    result = exchange.bridge.select_preset_by_walk(2, known_slot=78)

    assert result == 2
    assert unit.press_count == 4
    assert set(unit.buttons) == {b.PRESET_NEXT_BUTTON}


def test_force_reload_leaves_and_returns_with_two_presses(
    monkeypatch: pytest.MonkeyPatch,
):
    """A reload of the current slot is button 5 then button 7."""
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    unit = _WalkUnit(start_slot=9)
    exchange = FakeExchangeBridge(unit)

    result = exchange.bridge.select_preset_by_walk(9, known_slot=9, force_reload=True)

    assert result == 9
    assert unit.buttons == [b.PRESET_PREV_BUTTON, b.PRESET_NEXT_BUTTON]
    assert unit.press_count == 2


def test_the_live_mute_is_the_two_track_keys_together(
    monkeypatch: pytest.MonkeyPatch,
):
    """The chord is 10 down, 12 down, 10 up, 12 up -- and nothing else."""
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    unit = _WalkUnit(start_slot=3)
    exchange = FakeExchangeBridge(unit)

    exchange.bridge.toggle_live_mute()

    events = []
    for raw in exchange.sent():
        packet = m.parse(raw)
        assert packet is not None and packet.function == m.Function.COMMAND
        body = m.from_nibbles(packet.payload)
        assert body[0] == m.CommandType.VIRTUAL_BUTTON
        events.append(body[1])
    up = m.CommandType.BUTTON_UP_OFFSET
    assert events == [
        b.TRACK_LEFT_BUTTON,
        b.TRACK_RIGHT_BUTTON,
        b.TRACK_LEFT_BUTTON + up,
        b.TRACK_RIGHT_BUTTON + up,
    ]


def test_a_walk_that_cannot_converge_names_the_observed_slot(
    monkeypatch: pytest.MonkeyPatch,
):
    """A unit that never moves is reported where it stayed."""
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    unit = _WalkUnit(start_slot=5, drop_all=True)
    exchange = FakeExchangeBridge(unit)

    with pytest.raises(b.DeviceError) as caught:
        exchange.bridge.select_preset_by_walk(0, known_slot=5)

    assert isinstance(caught.value, b.PresetSelectionError)
    assert "P05" in str(caught.value)
    assert caught.value.observed_slot == 5


def test_the_live_mute_chord_leaves_as_one_gesture(
    monkeypatch: pytest.MonkeyPatch,
):
    """The two Track keys must reach the unit inside one key scan.

    The ordinary pace between messages is 300 ms.  Sent through it, the
    two downs are two single presses -- the cursor moves left, then
    right, and nothing is muted.  The chord's own frames therefore go
    together, while the hold between the downs and the ups is kept.
    """
    import time as clock

    sent: List[float] = []

    class _Port:
        def send_message(self, message) -> None:
            sent.append(clock.monotonic())

    port = _Port()
    bridge = b.SqBridge(_FakeIn(), port, channel=0)
    bridge._out = b.PacedOut(port, gap=0.05)

    bridge.toggle_live_mute(hold=0.06)

    assert len(sent) == 4
    assert sent[1] - sent[0] <= b.CHORD_GAP + 0.02, sent
    assert sent[3] - sent[2] <= b.CHORD_GAP + 0.02, sent
    assert sent[2] - sent[1] >= 0.04, sent


def test_store_current_edit_wraps_from_p79_to_p00(monkeypatch: pytest.MonkeyPatch):
    """P79's neighbour is P00; the walk wraps and comes home."""
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    unit = _WalkUnit(start_slot=79)
    exchange = FakeExchangeBridge(unit)

    result = exchange.bridge.store_current_edit()

    assert result.slot == 79
    assert result.neighbour == 0
    assert result.stored_raw == result.working_raw
    assert unit.slot == 79


def test_the_paced_output_leaves_a_gap_between_messages() -> None:
    """Two SysEx messages never leave closer than the gap."""
    import itertools
    import time as clock

    from sq.bridge import PacedOut

    sent: List[float] = []

    class _Port:
        def send_message(self, message):
            sent.append(clock.monotonic())

    paced = PacedOut(_Port(), gap=0.02)
    paced.send_message([0xF0, 0xF7])
    paced.send_message([0xF0, 0xF7])
    paced.send_message([0xF0, 0xF7])
    gaps = [b - a for a, b in itertools.pairwise(sent)]
    assert all(gap >= 0.02 for gap in gaps), gaps
    assert paced.throttled_seconds >= 0.03


def test_the_chord_group_is_paced_as_a_whole_gesture() -> None:
    """Inside the group the frames go together; outside it the gap stays."""
    import time as clock

    from sq.bridge import PacedOut

    sent: List[float] = []

    class _Port:
        def send_message(self, message):
            sent.append(clock.monotonic())

    paced = PacedOut(_Port(), gap=0.05)
    paced.send_chord([[0xF0], [0xF7]])
    paced.send_message([0xF0, 0xF7])

    assert len(sent) == 3
    assert sent[1] - sent[0] < 0.02, sent
    assert sent[2] - sent[1] >= 0.05, sent


def test_the_store_walks_the_dialog_and_verifies_by_reload(
    monkeypatch: pytest.MonkeyPatch,
):
    """Dump, then Enter/Save x3, a leave-and-return, and a verified read.

    The sequence is the manual's save steps driven blind; the read after
    the reload is the slot's stored bytes, which is what makes the
    verification mean anything.
    """
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    unit = _WalkUnit(start_slot=3)
    exchange = FakeExchangeBridge(unit)

    result = exchange.bridge.store_current_slot(BLOB, PRESET)

    assert result.verified is True
    buttons: List[int] = []
    for raw in exchange.sent():
        packet = m.parse(raw)
        if packet is None or packet.function != m.Function.COMMAND:
            continue
        body = m.from_nibbles(packet.payload)
        if body and body[0] == m.CommandType.VIRTUAL_BUTTON:
            buttons.append(body[1])
    up = m.CommandType.BUTTON_UP_OFFSET
    enter, prev, nxt = (
        b.ENTER_SAVE_BUTTON,
        b.PRESET_PREV_BUTTON,
        b.PRESET_NEXT_BUTTON,
    )
    assert buttons == [
        enter,
        enter + up,
        enter,
        enter + up,
        enter,
        enter + up,
        prev,
        prev + up,
        nxt,
        nxt + up,
    ]
