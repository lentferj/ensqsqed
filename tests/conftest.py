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

"""The ``--hardware`` option, and the device fixture for the file that uses it.

**Nothing here runs unless you ask for it.** CLAUDE.md's hardware rule is
that only one session drives a unit at a time, so the suite that talks to
a real Ensoniq SQ-R Plus is opt-in twice over: the ``--hardware`` flag, and
the machine actually answering. ``make check`` -- and CI, which runs it --
never passes the flag, so ``tests/test_hardware.py`` skips there by design
rather than by accident. The environment variable covers the case of a
unit plugged into a runner nobody meant to address.

A skip is a skip, not a pass. ``-rs`` names the reason, so "the unit was
off" and "the offsets are wrong" never look like the same green.

This unit answers a request it cannot serve with silence, so a timeout
cannot say which of them it was: the unit may be powered off, on another
port (USB renumbers them), busy with another session, or have System
Exclusive reception off.
"""

import os
from collections.abc import Iterator

import pytest

from sq import bridge as b
from vinsynlib import midi

#: Why the option is spelled out rather than folded into ``-m hardware``.
HELP = (
    "run the tests that need a real Ensoniq SQ-R Plus on a MIDI port. "
    "They open the ports, so only one session may use them at a time. "
    "Without this flag they skip."
)


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("hardware", "tests against a real Ensoniq SQ-R Plus")
    group.addoption(
        "--hardware",
        action="store_true",
        default=False,
        help=HELP,
    )
    group.addoption(
        "--scan",
        action="store_true",
        default=False,
        help=(
            "sweep every MIDI port for the unit instead of using the "
            "remembered port pair, the way --scan does in the app"
        ),
    )


def _asked_for_hardware(config: pytest.Config) -> bool:
    """The flag, or the environment variable that says the same thing."""
    return bool(config.getoption("--hardware")) or bool(
        os.environ.get("ENSQSQED_HARDWARE")
    )


@pytest.fixture(scope="session")
def device(request: pytest.FixtureRequest) -> Iterator[b.SqBridge]:
    """A live :class:`sq.bridge.SqBridge`, or a skip saying why not.

    Session-scoped on purpose: one connection, one pair of ports, held for
    the whole run rather than opened and closed per test. The bridge
    learns the ports and base channel once.

    A failure to connect is a skip, never a failure. The reason lists
    every cause rather than picking one: the unit answers a request it
    cannot serve with silence, so a timeout does not say whether it was
    off, on another port (USB renumbers them), refusing System
    Exclusive, or cabled behind a chain that only carries requests out.
    """
    if not _asked_for_hardware(request.config):
        pytest.skip(
            "hardware tests need --hardware (or ENSQSQED_HARDWARE=1); the "
            "suite that talks to a real Ensoniq SQ-R Plus is opt-in because "
            "only one session may drive the unit"
        )

    try:
        connection = b.SqBridge.connect(scan=bool(request.config.getoption("--scan")))
    except (midi.MidiUnavailable, midi.DeviceNotFound, b.DeviceError) as exc:
        pytest.skip(
            f"no SQ-R answered ({exc}). The unit may be powered off, "
            "on another port (USB renumbers them), busy with another "
            "session, or have System Excl off in its System bank -- "
            "a request it cannot serve is answered with silence, so "
            "a timeout does not say which."
        )
    try:
        yield connection
    finally:
        connection.close()
