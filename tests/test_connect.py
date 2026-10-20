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

"""How a launch finds the unit: the remembered port, and the sweep.

Ported in the same shape as the sibling x5ded's and rxved's
tests/test_connect.py. These exist because `SqBridge.autodetect` sweeps
every MIDI port on the machine at each of the sixteen base channels on
every single launch -- about five minutes on a thirty-port rig -- against a
config.toml that had been recording the answer since before the file
existed: it was written, and then ignored.

What is tested here is the *wiring*, not the policy: the policy itself is
`vinsynlib.midi.open_remembered_or_swept`, and its own tests live with it.
These prove that ensqsqed hands it the right probe, the right remembered
pair, and the right answer to --scan.

No hardware and no MIDI ports here: `open` and `autodetect` are stubbed with
what they would have found.
"""

from __future__ import annotations

import pytest

from sq import banks
from sq import bridge as b
from sq import config as cfg

SQ_PORT = "Midi Out: Ensoniq SQ-R 1 28:0"
OTHER_PORT = "Midi Out: Scarlett 18i8 USB MIDI 1 24:0"
SPLIT_IN = "Midi In: Something Else 1 30:0"


class _FakeBridge:
    """Stands in for an opened bridge. Closes are counted, not needed."""

    def __init__(self, port, recv, *, answers):
        self.port = port
        self.recv_port = recv
        #: False models a port that stays silent, which on this protocol is
        #: indistinguishable from a unit that is off.
        self.answers = answers
        self.channel = 0
        self.timeout = b.DEFAULT_TIMEOUT
        self.closed = False

    def read_current_sound(self, *, timeout=None):
        if not self.answers:
            raise TimeoutError("no SINGLE_SOUND_DUMP came back")
        return [0] * 128

    def close(self):
        self.closed = True


class _Rig:
    """Records what was opened, what answered, and whether a sweep happened.

    `answering` maps a port name to whether an SQ-R answers there. `ports` is
    what the host actually has, which is not the same thing -- a remembered
    name can still be a port with nothing behind it, and that is the case
    --scan exists for.
    """

    def __init__(self, answering, *, ports=None):
        self.answering = answering
        self.ports = ports if ports is not None else list(answering)
        self.opened = []
        self.tried = []
        self.swept = 0
        self.made = []

    def install(self, monkeypatch):
        rig = self

        def fake_open(
            cls,
            /,
            port,
            *,
            recv_port=None,
            channel=0,
            **rest,
        ):
            # `rest` is `timeout=...`, a discovery timeout, deliberately
            # dropped: the test that cares asserts on bridge.timeout after
            # the call rather than on what went in. Asserting the shape of it
            # is how a stub stops quietly accepting a call that changed.
            assert set(rest) <= {"timeout"}, f"unexpected kwargs: {rest}"
            rig.opened.append(port)
            bridge = _FakeBridge(
                port, recv_port or port, answers=bool(rig.answering.get(port))
            )
            bridge.channel = channel
            rig.made.append(bridge)
            return bridge

        def fake_autodetect(
            cls, *, timeout=b.PROBE_TIMEOUT, on_try=None, start_channel=0
        ):
            # Mirrors the real one's shape -- start at start_channel on every port
            # first, then wrap around -- so the widening is exercised rather
            # than stubbed away.
            rig.swept += 1
            channel_order = [(start_channel + i) % 16 for i in range(16)]
            for channel in channel_order:
                for name in rig.ports:
                    if on_try is not None:
                        on_try(f"{name} (channel {channel + 1})")
                    rig.tried.append(name)
                    if rig.answering.get(name):
                        return fake_open(cls, name, channel=channel)
            raise b.DeviceError("nothing answered")

        monkeypatch.setattr(b, "list_ports", lambda: (list(rig.ports), list(rig.ports)))
        monkeypatch.setattr(b, "likely_ports", lambda: [])
        monkeypatch.setattr(b.SqBridge, "open", classmethod(fake_open))
        monkeypatch.setattr(b.SqBridge, "autodetect", classmethod(fake_autodetect))
        return rig

    def unclosed(self):
        """Bridges that were opened and never closed."""
        return [x.port for x in self.made if not x.closed]


def _remembered(tmp_path, send=SQ_PORT, recv=""):
    """A config.toml holding one remembered port pair."""
    path = str(tmp_path / "config.toml")
    cfg.save_ports(send, recv or send, path)
    return path


# --- the normal path: trust the config ---------------------------------------


class TestSelectSendsTheSelectorFirst:
    """``SqBridge.select``, not just the message builder.

    The browser's Enter path calls this method, and it did not exist after
    the parity rewrite -- only the demo device had one, so the first real
    selection raised ``AttributeError`` in a worker. These pin the wire
    order without opening a port.
    """

    @staticmethod
    def _bridge(channel: int = 10):
        sent: list = []
        bridge = object.__new__(b.SqBridge)
        bridge._out = type(
            "_Out",
            (),
            {"send_message": lambda self, message: sent.append(list(message))},
        )()
        bridge.channel = channel
        return bridge, sent

    def test_internal_sends_selector_then_sound(self):
        bridge, sent = self._bridge()
        bridge.select(banks.slot("INT", 42))
        assert sent == [[0xCA, 124], [0xCA, 41]]

    def test_a_drum_kit_sends_one_absolute_change(self):
        bridge, sent = self._bridge()
        bridge.select(banks.slot("DRUM", 3))
        assert sent == [[0xCA, 82]], "a drum kit must get no group selector"

    def test_multi_puts_123_before_the_selector(self):
        bridge, sent = self._bridge()
        bridge.select(banks.slot("INT", 42), with_effect=True)
        assert sent == [[0xCA, 123], [0xCA, 124], [0xCA, 41]]

    def test_the_channel_is_the_one_the_bridge_holds(self):
        bridge, sent = self._bridge(channel=0)
        bridge.select(banks.slot("ROM", 5))
        assert sent == [[0xC0, 125], [0xC0, 4]]


class TestTheRememberedPort:
    def test_it_is_used_without_sweeping(self, monkeypatch, tmp_path):
        """One Single Sound Dump Request to the saved port, and nothing else.

        The whole point. `autodetect` is sixteen base channels times every
        port, which on a thirty-port rig is about five minutes.
        """
        path = _remembered(tmp_path)
        rig = _Rig({SQ_PORT: True, OTHER_PORT: False}).install(monkeypatch)

        b.SqBridge.connect(config_path=path)

        assert rig.swept == 0, "swept every port when the answer was on file"
        assert rig.opened == [SQ_PORT]

    def test_the_remembered_pair_is_both_halves(self, monkeypatch, tmp_path):
        """The input port is remembered too, so a unit on a separate
        interface does not fall back to probing the output name."""
        path = str(tmp_path / "config.toml")
        cfg.save_ports(OTHER_PORT, SPLIT_IN, path)
        _Rig({OTHER_PORT: True}).install(monkeypatch)

        bridge = b.SqBridge.connect(config_path=path)

        assert bridge.recv_port == SPLIT_IN

    def test_the_session_timeout_is_not_the_probe_timeout(self, monkeypatch, tmp_path):
        """A short discovery timeout must not become the working one.

        Otherwise every later request that does not name its own runs at the
        probe's value, and a slow but working connection looks like a timeout
        on every call.
        """
        path = _remembered(tmp_path)
        _Rig({SQ_PORT: True}).install(monkeypatch)

        bridge = b.SqBridge.connect(config_path=path)

        assert bridge.timeout == b.DEFAULT_TIMEOUT
        assert bridge.timeout != b.PROBE_TIMEOUT

    def test_it_does_not_rewrite_the_config(self, monkeypatch, tmp_path):
        """The file is this path's *input*.

        Rewriting it unchanged would make a plain launch mutate the thing it
        was asked to trust, and any later change to what is stored would be
        invisible in a diff.
        """
        path = _remembered(tmp_path)
        _Rig({SQ_PORT: True}).install(monkeypatch)

        b.SqBridge.connect(config_path=path)

        assert cfg.load_last_port(path) == SQ_PORT

    def test_a_dead_remembered_port_leaves_nothing_open(self, monkeypatch, tmp_path):
        path = _remembered(tmp_path)
        rig = _Rig({SQ_PORT: False, OTHER_PORT: True}).install(monkeypatch)

        b.SqBridge.connect(config_path=path)

        # The remembered one was closed on the way to falling back; the one
        # the sweep found is the caller's to close.
        assert rig.made[0].port == SQ_PORT
        assert rig.made[0].closed is True


# --- the fallback: sweep ----------------------------------------------------


class TestTheSweep:
    def test_it_runs_when_nothing_is_remembered(self, monkeypatch, tmp_path):
        path = str(tmp_path / "config.toml")  # never written: a first run
        rig = _Rig({OTHER_PORT: True}).install(monkeypatch)

        b.SqBridge.connect(config_path=path)

        assert rig.swept == 1
        assert OTHER_PORT in rig.tried

    def test_it_runs_when_asked_for(self, monkeypatch, tmp_path):
        """--scan: what is needed after the unit moves to a renumbered port.

        The saved name is still a port on this host -- it is what the unit
        was on -- and nothing answers there now, because the unit is
        somewhere else. `ports` says it exists; `answering` says what is
        behind it.
        """
        path = _remembered(tmp_path)
        rig = _Rig({OTHER_PORT: True}, ports=[SQ_PORT, OTHER_PORT]).install(monkeypatch)

        bridge = b.SqBridge.connect(config_path=path, scan=True)

        assert rig.swept == 1
        assert bridge.port == OTHER_PORT, "opened the saved port anyway"

    def test_scan_does_not_probe_the_remembered_port_first(self, monkeypatch, tmp_path):
        """--scan asks for the sweep, not for a check of the file and then
        the sweep."""
        path = _remembered(tmp_path)
        rig = _Rig({OTHER_PORT: True}, ports=[SQ_PORT, OTHER_PORT]).install(monkeypatch)

        b.SqBridge.connect(config_path=path, scan=True)

        assert rig.opened == [OTHER_PORT], "the saved port was probed first"

    def test_a_dead_remembered_port_falls_back_and_says_so(self, monkeypatch, tmp_path):
        """Not silently. A fallback that says nothing is indistinguishable
        from the fast path having worked, which is how a slow launch quietly
        becomes the normal one."""
        path = _remembered(tmp_path)
        rig = _Rig({SQ_PORT: False, OTHER_PORT: True}).install(monkeypatch)
        said = []

        bridge = b.SqBridge.connect(config_path=path, on_try=said.append)

        assert rig.swept == 1
        assert bridge.port == OTHER_PORT
        assert said[0] == f"{SQ_PORT} (remembered, did not answer)"

    def test_the_fast_path_says_nothing(self, monkeypatch, tmp_path):
        """One request, and silence about it."""
        path = _remembered(tmp_path)
        _Rig({SQ_PORT: True}).install(monkeypatch)
        said = []

        b.SqBridge.connect(config_path=path, on_try=said.append)

        assert said == []

    def test_it_remembers_what_it_found(self, monkeypatch, tmp_path):
        """The end the whole thing is for: one sweep, then never again."""
        path = str(tmp_path / "config.toml")
        _Rig({SQ_PORT: True}, ports=[OTHER_PORT, SQ_PORT]).install(monkeypatch)

        b.SqBridge.connect(config_path=path)

        assert cfg.load_last_port(path) == SQ_PORT

    def test_the_next_launch_does_not_sweep(self, monkeypatch, tmp_path):
        path = str(tmp_path / "config.toml")
        first = _Rig({SQ_PORT: True}, ports=[OTHER_PORT, SQ_PORT])
        first.install(monkeypatch)
        b.SqBridge.connect(config_path=path)
        assert first.swept == 1

        second = _Rig({SQ_PORT: True}, ports=[OTHER_PORT, SQ_PORT])
        second.install(monkeypatch)
        b.SqBridge.connect(config_path=path)

        assert second.swept == 0
        assert second.opened == [SQ_PORT]

    def test_nothing_answered_is_the_sweeps_own_error(self, monkeypatch, tmp_path):
        """The user needs the sweep's sentence, not the stale port's."""
        path = _remembered(tmp_path)
        _Rig({SQ_PORT: False, OTHER_PORT: False}).install(monkeypatch)

        with pytest.raises(b.DeviceError, match="nothing answered"):
            b.SqBridge.connect(config_path=path)

    def test_a_refusal_falls_back_rather_than_being_taken_as_the_unit(
        self, monkeypatch, tmp_path
    ):
        """`DeviceRefused` is deliberately NOT a `DeviceError` here, so the
        probe has to wrap it before the policy can see it. An SQ-R with
        System Excl off answers every request with an error message, which
        is not the same as answering with a sound."""
        path = _remembered(tmp_path)
        rig = _Rig({OTHER_PORT: True}, ports=[SQ_PORT, OTHER_PORT]).install(monkeypatch)

        def refuses(*, timeout=None):
            raise b.DeviceRefused("System Excl is off")

        monkeypatch.setattr(_FakeBridge, "read_current_sound", refuses)

        bridge = b.SqBridge.connect(config_path=path)

        assert not issubclass(b.DeviceRefused, b.DeviceError), "the premise"
        assert rig.swept == 1, "a refusal was not seen as 'did not answer'"
        assert bridge.port == OTHER_PORT

    def test_on_try_is_optional(self, monkeypatch, tmp_path):
        """`--demo` and library callers pass none, and that must not crash."""
        path = str(tmp_path / "config.toml")
        _Rig({OTHER_PORT: True}).install(monkeypatch)

        b.SqBridge.connect(config_path=path)

    def test_the_sweep_still_names_each_port_it_tries(self, monkeypatch, tmp_path):
        """The family's existing reporting, unchanged: one line per port
        tried, in `autodetect`'s own "  probing <port>..." style, which is
        built from the on_try the caller supplies."""
        path = str(tmp_path / "config.toml")
        _Rig({OTHER_PORT: True}, ports=[SQ_PORT, OTHER_PORT]).install(monkeypatch)
        said = []

        b.SqBridge.connect(config_path=path, on_try=said.append)

        assert said, "the sweep said nothing about what it was trying"
        assert said[0].startswith(SQ_PORT), "said so out of order"
        assert any(line.startswith(OTHER_PORT) for line in said)
