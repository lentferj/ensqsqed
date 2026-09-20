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

**Nothing here has been run against an SQ-R.** The instrument was not
patched into the MIDI bay while it was written. Every message is
transcribed from Ensoniq's specification, and the framing was checked the
only way it could be without hardware: by generating the specification's
own worked example and comparing byte for byte. It matches.

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
from typing import Callable, Dict, List, Optional, Tuple

from sq import banks
from sq import messages as m

__all__ = ["SqBridge", "DeviceError", "DeviceRefused", "list_ports",
           "likely_ports", "DEFAULT_TIMEOUT", "PROBE_TIMEOUT",
           "BANK_TIMEOUT", "BUTTON_GAP"]


class DeviceError(RuntimeError):
    """Something about the connection, not about the music."""


class DeviceRefused(RuntimeError):
    """The unit said no, which is different from saying nothing."""


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
    except ImportError as exc:                      # pragma: no cover
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
    wanted = ("sq-r", "sqr", "ensoniq", "sq1", "sq-1", "sq2",
              "sq-2", "ks32", "ks-32")
    return [p for p in outs if any(w in p.lower() for w in wanted)]


class SqBridge:
    """One open pair of ports, and the requests this program makes."""

    def __init__(self, midi_in, midi_out, *, channel: int = 0,
                 description: str = "",
                 timeout: float = DEFAULT_TIMEOUT) -> None:
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
            self._in.ignore_types(sysex=False, timing=True,
                                  active_sense=True)
        except AttributeError:                      # a test double
            pass

    @classmethod
    def open(cls, port: str, *, recv_port: Optional[str] = None,
             channel: int = 0, timeout: float = DEFAULT_TIMEOUT) -> "SqBridge":
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
        bridge = cls(midi_in, midi_out, channel=channel,
                     description=f"{port} -> {listen}", timeout=timeout)
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

    def request(self, outgoing: bytes, wanted: int, *,
                timeout: Optional[float] = None) -> m.Packet:
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
                    raise DeviceRefused(
                        f"the unit refused: {m.ErrorCode.name(value)}")
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

    def read_internal_bank(self, *, timeout: Optional[float] = None
                           ) -> Dict[int, bytes]:
        """All 80 internal sounds. Read-only, and **makes no sound**.

        One request, one 32647-byte reply, eighty sounds. This is the one
        to use: the alternative reaches a single sound only, and only the
        one the instrument is already on.
        """
        packet = self.request(
            m.internal_banks_request(channel=self.channel),
            m.Function.ALL_SOUNDS_DUMP,
            timeout=BANK_TIMEOUT if timeout is None else timeout)
        out: Dict[int, bytes] = {}
        for number in range(banks.SOUNDS_PER_GROUP):
            chunk = packet.payload[number * m.SOUND_BYTES_PACKED:
                                   (number + 1) * m.SOUND_BYTES_PACKED]
            out[number] = m.from_nibbles(chunk)
        return out

    def read_names(self, *, timeout: Optional[float] = None
                   ) -> Dict[int, str]:
        """Every internal sound's name. Silent."""
        return {number: m.sound_name(record)
                for number, record in
                self.read_internal_bank(timeout=timeout).items()}

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
            m.Function.SINGLE_SOUND_DUMP, timeout=timeout)
        return m.from_nibbles(packet.payload)

    # --- discovery ----------------------------------------------------------

    @classmethod
    def autodetect(cls, *, timeout: float = PROBE_TIMEOUT,
                   on_try: Optional[Callable[[str], None]] = None
                   ) -> "SqBridge":
        """Find an SQ-R by asking each port, on each base channel, for the
        sound it is on.

        The single-sound request is the probe rather than the bank
        request: 415 bytes against 32647, and equally silent.

        **This is slow, and the arithmetic is worth knowing before you
        wait for it.** Sixteen base channels times every bidirectional
        port times the probe timeout: on a rig with thirty ports that is
        about five minutes before it gives up. Channel 1 is tried on every
        port first, so a unit left on the default is found in the first
        pass -- but a unit that is switched off is found never, slowly.
        Passing ``--port`` and ``--channel`` skips all of it, and the CLI
        remembers both once something answers.
        """
        hinted = likely_ports()
        _, outs = list_ports()
        candidates = [p for p in hinted + [p for p in outs if p not in hinted]
                      if "Midi Through" not in p]

        for channel in (0, *range(1, 16)):
            for port in candidates:
                if on_try is not None:
                    on_try(f"{port} (channel {channel + 1})")
                try:
                    bridge = cls.open(port, channel=channel, timeout=timeout)
                except Exception:
                    continue
                try:
                    bridge.read_current_sound(timeout=timeout)
                except Exception:
                    bridge.close()
                    continue
                bridge.timeout = DEFAULT_TIMEOUT
                return bridge
        raise DeviceError(
            "no SQ-R answered a Single Sound Dump Request on any port at "
            "any of the sixteen base channels. Check the cable, that the "
            "unit is on, that "
            "System Excl is ON in its System bank, and that the chain "
            "carries its replies back to this computer."
        )
