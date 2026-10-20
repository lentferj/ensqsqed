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
  do not match.

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
