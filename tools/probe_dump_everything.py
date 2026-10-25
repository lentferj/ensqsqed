#!/usr/bin/env python3
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
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.

"""Bench probe: send the spec's "Dump Everything Request" (command 05) once.

**This is a probe, not a feature.**  The request is documented (SQ-1 SysEx
specification section 3.1.6) but has never been verified on an SQ-R, and
this program does not use it.  The probe sends it exactly once, captures
everything that comes back, and writes the frames verbatim.

What it writes into the output directory:

``request.syx``
    The nine bytes that were sent.
``frames/NNN_typeXX_lenNNNN.syx``
    Every received SysEx frame, verbatim, numbered in arrival order.
``all_frames.syx``
    The same frames concatenated, for a single drag-and-drop file.
``capture.mid``
    A format-0 Standard MIDI File carrying every frame as a SysEx event.
``capture.json``
    Arrival order, offsets, message type, function, length and time.
``summary.txt``
    A human-readable log.

**Safety:** one request, no retries, no other message.  A request is
read-only; it asks the unit to dump, and the only risk is the unit
answering with more than the program expects, which is why every byte is
kept rather than parsed away.  The link is first checked with the
harmless Single Sound Dump Request (command 01); if that does not answer,
nothing else is sent.

Usage::

    python tools/probe_dump_everything.py
    python tools/probe_dump_everything.py --port "..." --recv-port "..." --channel 10
    python tools/probe_dump_everything.py --seconds 120 --out /tmp/dump05
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sq import bridge as b
from sq import config as cfg
from sq import messages as m

#: How long to listen for the answer, in seconds.  All-sounds is ~11 s and
#: all-presets ~12 s; "everything" can only be larger, so the default is
#: generous.  A request is silent when it is not understood; the timeout is
#: how that is learned.
DEFAULT_SECONDS = 90.0

#: The harmless request used to prove the rig answers before the unknown
#: one is sent.  It is the current sound, 408 payload bytes.
PROBE_SECONDS = 3.0


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _default_out() -> Path:
    return Path(cfg.load_backup_root()) / f"{_stamp()}_dump_everything"


def _varlen(value: int) -> bytes:
    """A Standard MIDI File variable-length quantity."""
    if value < 0:
        raise ValueError("a variable-length quantity cannot be negative")
    out = bytearray([value & 0x7F])
    value >>= 7
    while value:
        out.insert(0, 0x80 | (value & 0x7F))
        value >>= 7
    return bytes(out)


def _smf(frames: List[Tuple[float, bytes]], ticks_per_quarter: int = 96) -> bytes:
    """A format-0 MIDI file with one SysEx event per captured frame."""
    events = bytearray()
    last_ms = 0.0
    for at, frame in frames:
        # One tick per 10 ms keeps the relative timing visible without
        # inventing a musical tempo.
        delta = max(0, round((at - last_ms) / 10.0))
        last_ms = at
        payload = (
            frame[1:-1] if frame[:1] == b"\xf0" and frame[-1:] == b"\xf7" else frame
        )
        events += _varlen(delta)
        events += b"\xf0" + _varlen(len(payload)) + payload + b"\xf7"
    events += b"\x00\xff\x2f\x00"  # end of track
    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, ticks_per_quarter)
    return header + b"MTrk" + struct.pack(">I", len(events)) + bytes(events)


def _parse(frame: bytes) -> Optional[m.Packet]:
    try:
        return m.parse(frame)
    except Exception:
        return None


def _describe(frame: bytes) -> Dict[str, Any]:
    packet = _parse(frame)
    info: Dict[str, Any] = {"length": len(frame), "head": frame[:10].hex(" ")}
    if packet is None:
        info["kind"] = "not an Ensoniq SQ message"
        return info
    info["kind"] = m.Function.name(packet.function)
    info["function"] = packet.function
    info["device"] = packet.device
    if packet.function == m.Function.COMMAND:
        try:
            data = m.from_nibbles(packet.payload)
            info["command"] = data[0] if data else None
            if data and data[0] in (
                m.CommandType.SINGLE_SEQUENCE_ALERT,
                m.CommandType.ALL_SEQUENCE_ALERT,
            ):
                alert = b.PresetAlert.from_bytes(data)
                info["alert"] = {
                    "command": alert.command,
                    "size": alert.size,
                    "flag": alert.flag,
                    "count": alert.count,
                }
        except Exception as exc:  # a probe records, it does not judge
            info["command_error"] = str(exc)
    if packet.function == m.Function.ERROR and packet.payload:
        try:
            code = m.from_nibbles(packet.payload)[:1]
            info["error"] = m.ErrorCode.name(code[0] if code else 0xFF)
        except Exception as exc:
            info["error_parse"] = str(exc)
    return info


def _connect(args: argparse.Namespace) -> b.SqBridge:
    if args.port:
        return b.SqBridge.open(
            args.port,
            recv_port=args.recv_port or args.port,
            channel=args.channel,
        )
    pair = cfg.load_port_pair()
    if pair is None:
        raise SystemExit(
            "no remembered port; pass --port and --recv-port, or run the "
            "browser once so the port is remembered"
        )
    return b.SqBridge.open(pair[0], recv_port=pair[1], channel=args.channel)


def _collect(
    link: b.SqBridge, seconds: float, *, started: float
) -> List[Tuple[float, bytes, Dict[str, Any]]]:
    """Read every message until the clock runs out.  No judgement."""
    out: List[Tuple[float, bytes, Dict[str, Any]]] = []
    deadline = started + seconds
    while time.monotonic() < deadline:
        message = link._in.get_message()
        if message is None:
            time.sleep(0.002)
            continue
        raw = bytes(message[0])
        if not raw:
            continue
        out.append((time.monotonic(), raw, _describe(raw)))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", help="MIDI output port (default: remembered)")
    parser.add_argument("--recv-port", help="MIDI input port (default: remembered)")
    parser.add_argument("--channel", type=int, default=None, help="base channel, 0-15")
    parser.add_argument(
        "--seconds", type=float, default=DEFAULT_SECONDS, help="how long to listen"
    )
    parser.add_argument("--out", help="output directory")
    parser.add_argument(
        "--no-probe",
        action="store_true",
        help="do not check the link with a single-sound request first",
    )
    args = parser.parse_args(argv)

    channel = cfg.load_channel() if args.channel is None else args.channel
    if not 0 <= channel <= 15:
        raise SystemExit(f"channel {channel} is outside 0-15")
    args.channel = channel

    out_dir = Path(args.out) if args.out else _default_out()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "frames").mkdir(exist_ok=True)

    link = _connect(args)
    log_lines: List[str] = []
    try:
        log_lines.append(f"port      {link.port}")
        log_lines.append(f"recv_port {link.recv_port}")
        log_lines.append(f"channel   {link.channel} (displayed {link.channel + 1})")
        log_lines.append(f"out       {out_dir}")

        if not args.no_probe:
            request = m.single_sound_request(channel=link.channel)
            (out_dir / "probe_request.syx").write_bytes(request)
            link._drain()
            link._out.send_message(list(request))
            started = time.monotonic()
            got = _collect(link, PROBE_SECONDS, started=started)
            log_lines.append(f"probe: {len(got)} message(s) in {PROBE_SECONDS:.0f} s")
            if not got:
                log_lines.append("probe: NOTHING CAME BACK -- command 05 not sent")
                (out_dir / "summary.txt").write_text("\n".join(log_lines) + "\n")
                print("\n".join(log_lines))
                return 2
            for at, _raw, info in got:
                log_lines.append(
                    f"  probe +{at - started:7.3f}s "
                    f"{info['length']:6d} B  {info.get('kind')}"
                )

        request = m.command_message(
            m.CommandType.DUMP_EVERYTHING_REQUEST, channel=link.channel
        )
        (out_dir / "request.syx").write_bytes(request)
        link._drain()
        started = time.monotonic()
        link._out.send_message(list(request))
        log_lines.append(f"sent command 05: {request.hex(' ')}")

        captured = _collect(link, args.seconds, started=started)
        log_lines.append(
            f"command 05: {len(captured)} message(s) in {args.seconds:.0f} s"
        )

        records: List[Dict[str, Any]] = []
        frames: List[Tuple[float, bytes]] = []
        all_bytes = bytearray()
        for index, (at, raw, info) in enumerate(captured):
            name = (
                f"{index:03d}_type{info.get('function', -1):02x}_len{len(raw):06d}.syx"
                if "function" in info
                else f"{index:03d}_other_len{len(raw):06d}.syx"
            )
            (out_dir / "frames" / name).write_bytes(raw)
            all_bytes += raw
            frames.append((at, raw))
            record = {
                "index": index,
                "offset": index,
                "seconds": round(at - started, 6),
                **info,
                "file": f"frames/{name}",
            }
            records.append(record)
            log_lines.append(
                f"  +{at - started:8.3f}s {len(raw):6d} B  {info.get('kind')}"
                + (f"  alert={info['alert']}" if "alert" in info else "")
                + (f"  error={info['error']}" if "error" in info else "")
            )

        (out_dir / "all_frames.syx").write_bytes(bytes(all_bytes))
        (out_dir / "capture.mid").write_bytes(_smf(frames))
        (out_dir / "capture.json").write_text(
            json.dumps(
                {
                    "port": link.port,
                    "recv_port": link.recv_port,
                    "channel": link.channel,
                    "request": request.hex(" "),
                    "seconds": args.seconds,
                    "messages": records,
                },
                indent=2,
            )
            + "\n"
        )
        summary = "\n".join(log_lines) + "\n"
        (out_dir / "summary.txt").write_text(summary)
        print(summary)
        if not captured:
            print(
                "Nothing came back.  Command 05 is not answered by this unit "
                "(or the link carries requests out but not replies back).",
                file=sys.stderr,
            )
            return 3
        return 0
    finally:
        link.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
