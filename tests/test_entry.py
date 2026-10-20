# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.

"""The console-script entry points, and the message for a missing library."""

from __future__ import annotations

import builtins
from typing import Any

import pytest

from ensqsqed import entry

REQUIRED_BITS = (
    "error:",
    "vinsynlib",
    "pip install",
    "vinsynlib>=0.4.2",
)


def _hide_vinsynlib(monkeypatch: Any) -> None:
    """Make ``import vinsynlib`` raise ModuleNotFoundError, as it would."""
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "vinsynlib" or name.startswith("vinsynlib."):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def test_a_missing_library_is_diagnosed(monkeypatch: Any) -> None:
    _hide_vinsynlib(monkeypatch)
    problem = entry._diagnose("ensqsqed")
    assert problem is not None
    for bit in REQUIRED_BITS:
        assert bit in problem, bit


def test_the_message_goes_to_stderr_and_returns_one(
    monkeypatch: Any, capsys: Any
) -> None:
    _hide_vinsynlib(monkeypatch)
    assert entry.app() == 1
    captured = capsys.readouterr()
    assert "error: ensqsqed cannot start" in captured.err


def test_the_cli_entry_point_says_the_same_thing(monkeypatch: Any, capsys: Any) -> None:
    _hide_vinsynlib(monkeypatch)
    assert entry.cli() == 1
    assert "error: sqcli cannot start" in capsys.readouterr().err


def test_an_installed_library_is_reported_healthy(monkeypatch: Any) -> None:
    import vinsynlib

    monkeypatch.setattr(vinsynlib, "__version__", "0.4.2")
    assert entry._diagnose("ensqsqed") is None


def test_an_older_library_is_named_rather_than_ignored(
    monkeypatch: Any,
) -> None:
    import vinsynlib

    monkeypatch.setattr(vinsynlib, "__version__", "0.0.9")
    problem = entry._diagnose("ensqsqed")
    assert problem is not None
    assert "0.4.2" in problem
    assert "0.0.9" in problem


def test_a_library_that_cannot_report_a_version_reads_as_missing(
    monkeypatch: Any,
) -> None:
    """No ``__version__`` is the one case that must not be guessed at.

    The comparison lives here, in stdlib only, precisely so that a library
    too old to describe itself is diagnosed rather than crashed on.
    """
    import vinsynlib

    monkeypatch.delattr(vinsynlib, "__version__", raising=False)
    problem = entry._diagnose("ensqsqed")
    assert problem is not None
    assert "is not installed" in problem


def test_a_pre_release_at_the_floor_is_not_called_too_old() -> None:
    """A pre-release marker is not a version: ``0.4.2rc1`` is ``0.4.2``."""
    assert entry._release_parts("0.4.2rc1", 3) == (0, 4, 2)
    assert entry._release_parts("0.4.2", 3) == (0, 4, 2)
    # A component without leading digits makes the whole string unusable.
    assert entry._release_parts("0.4.x", 3) is None
