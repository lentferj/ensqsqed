# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# The requests are transcribed as data from Ensoniq's MIDI System
# Exclusive Implementation Specification v1.43, section 3.1. The transport
# is ported from the sibling p2ked, x5ded, kwsed, rxved, nanosyned and
# emorphed projects.
#
# ensqsqed is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 2 of the License, or (at your option)
# any later version.
#
# ensqsqed is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.

"""Talking to an SQ-R.

**Verified against a real SQ-R on 2026-10-20**, on base channel 11: the
Single Sound Dump Request returned 415 bytes and the Internal Sound Banks
Dump Request returned all eighty sounds with their names. The unit was
cabled **crosswise** on a multi-port interface -- its MIDI IN on the
interface's output 2, its MIDI OUT on input 1 -- which is why
:meth:`SqBridge.autodetect` now listens on every input of an interface
while each of its outputs sends, rather than assuming a port answers on
itself. Before that change the sweep could not find this rig at all, even
though every request was correct.

Two things the specification gives that shape the code:

* **A dump request is a command message.** Message type ``00``, then a
  command type of 01-05 which is *data* and therefore nibble-ized. The
  unit receives these and never sends them, so anything of that shape on
  the wire came from a computer.
* **The unit answers errors.** An error message (type ``01``) carries
  NAK, ACK or "invalid button number". A NAK means busy or
  unintelligible, and is worth far more than waiting out a timeout --
  which is why :meth:`request` raises on one instead of ignoring it.

There is no request for a sound *by number*. The protocol has one for the
current sound and one for the whole internal bank, and nothing in
between. So reading one particular sound means selecting it first, which
makes the instrument play; :meth:`read_current_sound` says so and
:meth:`read_internal_bank` is the one to prefer.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from sq import banks
from sq import config as cfg
from sq import messages as m
from vinsynlib import midi

__all__ = [
    "SqBridge",
    "DeviceError",
    "DeviceRefused",
    "list_ports",
    "likely_ports",
    "DEFAULT_TIMEOUT",
    "PROBE_TIMEOUT",
    "BANK_TIMEOUT",
    "BUTTON_GAP",
]


class DeviceError(midi.DeviceError):
    """Something about the connection, not about the music.

    The library's class, so a probe that finds nothing is recognised by
    :func:`vinsynlib.midi.open_remembered_or_swept` and the sweep it falls
    back to happens -- with an announcement, rather than as a launch that
    quietly takes the slow path.
    """


class DeviceRefused(RuntimeError):
    """The unit said no, which is different from saying nothing.

    Deliberately *not* a :class:`DeviceError` here, as it was not before:
    every ``except DeviceError`` in this program means "the connection is
    unusable", and a refusal to one request does not make it so. The
    library does treat a refusal as something the remembered-port probe
    should give up on, which is why the probe wraps it.
    """


DEFAULT_TIMEOUT = 2.0
PROBE_TIMEOUT = 0.6
#: An internal bank dump is 32647 bytes; at MIDI's 3125 bytes a second
#: that is ten and a half seconds. This is arithmetic, not generosity.
BANK_TIMEOUT = 25.0

#: Between a button press and its release. The specification asks for
#: "approximately 2-300 msecs" between button commands, which is a range
#: this program takes the middle of. Nothing here presses buttons; the
#: constant exists so that anything which later does has a documented
#: number rather than inventing one.
BUTTON_GAP = 0.150


def _rtmidi():
    try:
        import rtmidi
    except ImportError as exc:  # pragma: no cover
        raise DeviceError(
            "python-rtmidi is not installed, so no MIDI port can be "
            "opened. ensqsqed still reads .syx and .mid files without it."
        ) from exc
    return rtmidi


def list_ports() -> Tuple[List[str], List[str]]:
    rtmidi = _rtmidi()
    midi_in, midi_out = rtmidi.MidiIn(), rtmidi.MidiOut()
    try:
        return list(midi_in.get_ports()), list(midi_out.get_ports())
    finally:
        midi_in.delete()
        midi_out.delete()


def likely_ports() -> List[str]:
    """Ports whose name suggests the instrument. Only an ordering."""
    _, outs = list_ports()
    wanted = ("sq-r", "sqr", "ensoniq", "sq1", "sq-1", "sq2", "sq-2", "ks32", "ks-32")
    return [p for p in outs if any(w in p.lower() for w in wanted)]


class SqBridge:
    """One open pair of ports, and the requests this program makes."""

    def __init__(
        self,
        midi_in,
        midi_out,
        *,
        channel: int = 0,
        description: str = "",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._in = midi_in
        self._out = midi_out
        #: The unit's **base MIDI channel**, 0-15. It is the fifth byte of
        #: every message, and a request carrying the wrong one is ignored
        #: in silence.
        self.channel = channel
        self.description = description
        self.timeout = timeout
        self.port: Optional[str] = None
        self.recv_port: Optional[str] = None
        try:
            self._in.ignore_types(sysex=False, timing=True, active_sense=True)
        except AttributeError:  # a test double
            pass

    @classmethod
    def open(
        cls,
        port: str,
        *,
        recv_port: Optional[str] = None,
        channel: int = 0,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> "SqBridge":
        rtmidi = _rtmidi()
        ins, outs = list_ports()
        listen = recv_port or port
        if port not in outs:
            raise DeviceError(f"no output port named {port!r}; have {outs}")
        if listen not in ins:
            raise DeviceError(f"no input port named {listen!r}; have {ins}")
        midi_out, midi_in = rtmidi.MidiOut(), rtmidi.MidiIn()
        try:
            midi_out.open_port(outs.index(port))
            midi_in.open_port(ins.index(listen))
        except Exception:
            for client in (midi_in, midi_out):
                try:
                    client.close_port()
                    client.delete()
                except Exception:
                    pass
            raise
        bridge = cls(
            midi_in,
            midi_out,
            channel=channel,
            description=f"{port} -> {listen}",
            timeout=timeout,
        )
        bridge.port = port
        bridge.recv_port = listen
        return bridge

    def close(self) -> None:
        for client in (self._in, self._out):
            try:
                client.close_port()
                client.delete()
            except Exception:
                pass

    def __enter__(self) -> "SqBridge":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # --- the wire -----------------------------------------------------------

    def _drain(self) -> None:
        while self._in.get_message() is not None:
            pass

    def request(
        self, outgoing: bytes, wanted: int, *, timeout: Optional[float] = None
    ) -> m.Packet:
        """Send a request and wait for the dump that answers it.

        ``wanted`` is the **message type** of the reply, never of the
        request: a request is itself a valid Ensoniq message and comes
        back on any chain with a soft thru, as several of the sibling
        instruments' chains have.

        An error message aborts the wait rather than being ignored until
        the clock runs out. A NAK is the one piece of diagnosis this
        protocol offers that most of the siblings' do not.
        """
        self._drain()
        self._out.send_message(list(outgoing))
        deadline = time.time() + (self.timeout if timeout is None else timeout)
        while time.time() < deadline:
            message = self._in.get_message()
            if message is None:
                time.sleep(0.002)
                continue
            packet = m.parse(message[0])
            if packet is None:
                continue
            if packet.device != self.channel:
                continue
            if packet.function == m.Function.ERROR and packet.payload:
                code = m.from_nibbles(packet.payload)[:1]
                value = code[0] if code else 0xFF
                if value != m.ErrorCode.ACK:
                    raise DeviceRefused(f"the unit refused: {m.ErrorCode.name(value)}")
                continue
            if packet.function != wanted:
                continue
            if not packet.size_is_right:
                continue
            return packet
        raise TimeoutError(self._silence_means(wanted))

    def _silence_means(self, wanted: int) -> str:
        """Why nothing came back. Never picks one reason."""
        return (
            f"no {m.Function.name(wanted)} came back. The unit did not "
            f"refuse it either, so this means one of: powered off, the "
            f"wrong port, the wrong base MIDI channel (ensqsqed is using "
            f"{self.channel + 1}), System Exclusive reception switched off "
            f"in the System bank, or a chain that carries requests out but "
            f"not replies back."
        )

    # --- reading, all of it silent -----------------------------------------

    def read_internal_bank(
        self, *, timeout: Optional[float] = None
    ) -> Dict[int, bytes]:
        """All 80 internal sounds. Read-only, and **makes no sound**.

        One request, one 32647-byte reply, eighty sounds. This is the one
        to use: the alternative reaches a single sound only, and only the
        one the instrument is already on.
        """
        packet = self.request(
            m.internal_banks_request(channel=self.channel),
            m.Function.ALL_SOUNDS_DUMP,
            timeout=BANK_TIMEOUT if timeout is None else timeout,
        )
        out: Dict[int, bytes] = {}
        for number in range(banks.SOUNDS_PER_GROUP):
            chunk = packet.payload[
                number * m.SOUND_BYTES_PACKED : (number + 1) * m.SOUND_BYTES_PACKED
            ]
            out[number] = m.from_nibbles(chunk)
        return out

    def read_names(self, *, timeout: Optional[float] = None) -> Dict[int, str]:
        """Every internal sound's name. Silent."""
        return {
            number: m.sound_name(record)
            for number, record in self.read_internal_bank(timeout=timeout).items()
        }

    def read_current_sound(self, *, timeout: Optional[float] = None) -> bytes:
        """The sound the unit is on, edits included. Silent **in itself**.

        The request makes no sound. Reaching a *particular* sound with it
        does, because the only way to change which sound is current is to
        select one -- so a caller walking the bank this way would play
        eighty sounds. :meth:`read_internal_bank` exists so that nobody
        has to.
        """
        packet = self.request(
            m.single_sound_request(channel=self.channel),
            m.Function.SINGLE_SOUND_DUMP,
            timeout=timeout,
        )
        return m.from_nibbles(packet.payload)

    # --- selecting, which is the one thing that changes what plays --------

    def select(self, target: "banks.Slot", *, with_effect: bool = False) -> None:
        """Select ``target`` on the unit. **This changes what is playing.**

        Always the group selector first, then the sound: the two are
        separate program changes on this instrument, and the second means
        whatever the last selector said. A drum kit's program change is
        absolute, so it gets no selector.

        ``with_effect`` puts program change 123 first, which asks the unit
        to bring the next sound's effect too (MULTI mode). The manual is
        explicit that the selector goes *between* 123 and the real program
        change, which is the order :func:`sq.banks.select_messages` emits.
        """
        for message in banks.select_messages(
            target, self.channel, with_effect=with_effect
        ):
            self._out.send_message(list(message))

    # --- discovery ----------------------------------------------------------

    @classmethod
    def connect(
        cls,
        *,
        config_path: str = cfg.DEFAULT_PATH,
        remembered: Optional[Tuple[str, str]] = None,
        scan: bool = False,
        channel: Optional[int] = None,
        on_try: Optional[Callable[[str], None]] = None,
    ) -> "SqBridge":
        """Open the unit, sweeping every port only when that is necessary.

        **The normal path is one Single Sound Dump Request, to the port
        remembered in ``config_path``.** That is not only an optimisation,
        and on this instrument the arithmetic is worth stating:
        :meth:`autodetect` sweeps every port at each of the sixteen base
        channels, which on a rig with thirty ports is about five minutes
        before it gives up. A sweep also pings every piece of hardware on
        the chain once per launch, and once the answer is known there is
        nothing left to learn from asking again.

        So: use the remembered port if there is one, sweep if there is not,
        and sweep on request. ``scan=True`` is what ``--scan`` reaches,
        which is what is needed after the unit moves to another port --
        USB re-enumeration renumbers ALSA clients out from under a saved
        name.

        A remembered port that does not answer falls back to the sweep, but
        **it says so first**. The guess is gone, and the announcement is
        what keeps the fallback from looking like the fast path having
        worked -- which is how a slow launch quietly becomes the normal one.
        What the sweep then finds is written back, so the launch after this
        one is fast again.

        The policy itself is :func:`vinsynlib.midi.open_remembered_or_swept`,
        written and tested once for the whole family; this is the SQ-R's
        half of it, which is what a Single Sound Dump Request on a named
        port means and where the answer is filed.

        ``remembered`` defaults to what ``config_path`` holds. It is a
        parameter so a caller can pass a pair it has already read, and so a
        test can say what the file says without writing one.

        ``channel`` defaults to the base channel remembered in
        ``config_path``. That is the unit's SysEx device byte as well as the
        channel a program change goes out on, so a launch that remembers the
        unit on channel 11 must probe on 11 rather than sweep from 1.
        """
        remembered = (
            remembered if remembered is not None else cfg.load_port_pair(config_path)
        )
        if channel is None:
            channel = cfg.load_channel(config_path)
        if channel is None:
            channel = 0
        base_channel: int = channel

        def probe(send: str, recv: str) -> "SqBridge":
            """One round trip to the named port. Raises if nothing answers."""
            try:
                bridge = cls.open(
                    send, recv_port=recv, channel=base_channel, timeout=PROBE_TIMEOUT
                )
            except Exception as exc:
                raise DeviceError(str(exc)) from exc
            try:
                bridge.read_current_sound(timeout=PROBE_TIMEOUT)
            except Exception as exc:
                # DeviceRefused is deliberately not a DeviceError, so it is
                # caught here and re-raised as one: a unit that answers "no"
                # is not the instrument on this port either, and the policy
                # decides what to do about that.
                bridge.close()
                raise DeviceError(
                    f"no Single Sound Dump came back on {send!r}: {exc}"
                ) from exc
            bridge.timeout = DEFAULT_TIMEOUT
            return bridge

        def remember(bridge: "SqBridge") -> None:
            if bridge.port and bridge.recv_port:
                cfg.save_ports(bridge.port, bridge.recv_port, config_path)

        return midi.open_remembered_or_swept(
            probe=probe,
            sweep=lambda try_hook: cls.autodetect(
                timeout=PROBE_TIMEOUT, on_try=try_hook, start_channel=base_channel
            ),
            remembered=remembered,
            scan=scan,
            remember=remember,
            on_try=on_try,
        )

    @classmethod
    def autodetect(
        cls,
        *,
        timeout: float = PROBE_TIMEOUT,
        on_try: Optional[Callable[[str], None]] = None,
        start_channel: int = 0,
    ) -> "SqBridge":
        """Find an SQ-R by asking each output port while listening on every
        input **of the same interface**, on each base channel, for the
        sound it is on.

        The single-sound request is the probe rather than the bank
        request: 415 bytes against 32647, and equally silent.

        **The input and output port names need not match.** A unit cabled
        crosswise on a multi-port interface -- MIDI IN on the interface's
        output 2, MIDI OUT on its input 1 -- answers on a different port
        from the one the request went out on. The old sweep paired each
        port with *itself*, so that rig could never be found. This one
        opens the inputs of one interface at a time and polls all of them
        while each of that interface's outputs sends, so the pair is
        discovered rather than assumed.

        Opening *every* input on the machine at once is not an option: each
        ``MidiIn`` is an ALSA sequencer client, and a 55-port bench
        exhausts the slots ("Cannot allocate memory"). Grouping by client
        name keeps the count to one interface's ports, usually four or
        eight.

        **This is slow, and the arithmetic is worth knowing before you
        wait for it.** Sixteen base channels times every output port times
        the probe timeout: on a rig with thirty ports that is several
        minutes before it gives up. The channel given by ``--device-channel``
        (``start_channel``) is tried first, so a unit set to a non-default
        channel is found in the first pass; the others follow. Passing
        ``--port`` and ``--recv-port`` skips all of it, and the CLI
        remembers both once something answers.
        """
        ins, outs = list_ports()
        hinted = set(likely_ports())
        groups = cls._interface_groups(ins, outs)
        # A group whose output is named like the instrument first.
        ordered = sorted(
            groups.items(),
            key=lambda item: 0 if any(p in hinted for p in item[1]["outs"]) else 1,
        )

        for channel in ((start_channel + i) % 16 for i in range(16)):
            for _name, group in ordered:
                pair = cls._probe_interface(group, ins, outs, channel, timeout, on_try)
                if pair is None:
                    continue
                send, recv = pair
                # The pair is known now: reopen it through the ordinary
                # constructor so the returned bridge owns its ports and the
                # caller can close them normally.
                bridge = cls.open(
                    send, recv_port=recv, channel=channel, timeout=timeout
                )
                bridge.timeout = DEFAULT_TIMEOUT
                return bridge

        raise DeviceError(
            "no SQ-R answered a Single Sound Dump Request on any output "
            "port, on any of the sixteen base channels, with the inputs of "
            "its own interface listening. Check the cable, that the unit is "
            "on, that System Excl is ON in its System bank, and that a MIDI "
            "cable runs from the unit's MIDI OUT back to this computer -- a "
            "unit that plays but never answers has only one direction "
            "cabled. If the unit's input and output are on two different "
            "interfaces, pass --port and --recv-port explicitly."
        )

    @staticmethod
    def _interface_groups(
        ins: List[str], outs: List[str]
    ) -> Dict[str, Dict[str, List[str]]]:
        """Group port names by ALSA client (interface).

        A crosswise cable can only plausibly land on another port of the
        same interface, so that is the unit of search. ``Midi Through`` is
        skipped: it is the kernel's loopback, not an instrument.
        """
        groups: Dict[str, Dict[str, List[str]]] = {}
        for kind, names in (("ins", ins), ("outs", outs)):
            for name in names:
                if "Midi Through" in name:
                    continue
                client = name.split(":", 1)[0]
                groups.setdefault(client, {"ins": [], "outs": []})[kind].append(name)
        return groups

    @classmethod
    def _probe_interface(
        cls,
        group: Dict[str, List[str]],
        ins: List[str],
        outs: List[str],
        channel: int,
        timeout: float,
        on_try: Optional[Callable[[str], None]],
    ) -> Optional[Tuple[str, str]]:
        """Probe one interface's outputs, listening on all its inputs.

        Returns ``(send_port, recv_port)`` for the first pair that answered
        a Single Sound Dump on ``channel``, or ``None``. The inputs are
        opened once for the whole interface, so a reply is caught whichever
        of its inputs the unit is cabled to.
        """
        rtmidi = _rtmidi()
        request = m.single_sound_request(channel=channel)
        listeners = cls._open_listeners(rtmidi, group["ins"], ins)
        try:
            for send in group["outs"]:
                if on_try is not None:
                    on_try(f"{send} (channel {channel + 1})")
                cls._drain_listeners(listeners)
                if not cls._send_probe(rtmidi, outs, send, request):
                    continue
                found = cls._await_dump(listeners, channel, timeout)
                if found is not None:
                    return send, found
            return None
        finally:
            cls._close_listeners(listeners)

    @classmethod
    def _open_listeners(
        cls, rtmidi: Any, recvs: List[str], ins: List[str]
    ) -> Dict[str, Any]:
        """Open every input of one interface, best-effort."""
        listeners: Dict[str, Any] = {}
        for recv in recvs:
            listener = cls._open_listener(rtmidi, ins, recv)
            if listener is not None:
                listeners[recv] = listener
        return listeners

    @staticmethod
    def _drain_listeners(listeners: Dict[str, Any]) -> None:
        """Throw away anything buffered from an earlier send."""
        for listener in listeners.values():
            while listener.get_message() is not None:
                pass

    @staticmethod
    def _close_listeners(listeners: Dict[str, Any]) -> None:
        for listener in listeners.values():
            try:
                listener.close_port()
            except Exception:
                pass
            try:
                listener.delete()
            except Exception:
                pass

    @staticmethod
    def _open_listener(rtmidi: Any, ins: List[str], name: str) -> Any:
        """Open one input for the sweep, or ``None`` if it cannot be had."""
        listener = None
        try:
            listener = rtmidi.MidiIn(queue_size_limit=65536)
            listener.open_port(ins.index(name))
            # SysEx must not be filtered; timing and active sensing are
            # noise for a dump request.
            listener.ignore_types(sysex=False, timing=True, active_sense=True)
            return listener
        except Exception:
            if listener is not None:
                try:
                    listener.delete()
                except Exception:
                    pass
            return None

    @staticmethod
    def _send_probe(rtmidi: Any, outs: List[str], send: str, request: bytes) -> bool:
        """Open ``send`` and put ``request`` on the wire.

        Returns whether it went out. The output is closed again either way,
        so only one output at a time is held open during the sweep.
        """
        port_out = None
        try:
            port_out = rtmidi.MidiOut()
            port_out.open_port(outs.index(send))
            port_out.send_message(list(request))
            return True
        except Exception:
            return False
        finally:
            if port_out is not None:
                try:
                    port_out.close_port()
                except Exception:
                    pass
                try:
                    port_out.delete()
                except Exception:
                    pass

    @staticmethod
    def _await_dump(
        listeners: Dict[str, Any], channel: int, timeout: float
    ) -> Optional[str]:
        """Poll the open inputs for the dump that answers ``channel``.

        Returns the input port name it arrived on, or ``None``. Foreign
        SysEx and the request itself echoed back by a soft-thru are
        ignored: only a Single Sound Dump with the right device byte and
        the right payload size counts.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            for name, listener in listeners.items():
                message = listener.get_message()
                if message is None:
                    continue
                packet = m.parse(message[0])
                if packet is None:
                    continue
                if packet.function != m.Function.SINGLE_SOUND_DUMP:
                    continue
                if packet.device != channel:
                    continue
                if not packet.size_is_right:
                    continue
                return name
            time.sleep(0.002)
        return None
