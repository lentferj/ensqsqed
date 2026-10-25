# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# ensqsqed is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 2 of the License, or (at your option) any later
# version.

"""Local labels for preset slots.

The SQ-R firmware never writes a preset's name from a SysEx dump (it
commits only preset offsets 32-151; the name at 2-17 is kept by the unit).
The app can still let the user label presets locally, keyed by the
zero-based preset slot 0-79.
"""

from __future__ import annotations

import contextlib
import json
import os
from typing import Dict, Optional

from vinsynlib.config import config_dir

MAX_LABEL = 16
NAME = "preset_labels.json"

__all__ = ["MAX_LABEL", "NAME", "default_path", "load", "save", "set_label"]


def default_path() -> str:
    """Where the local labels live unless a path is given."""
    return os.path.join(config_dir("ensqsqed"), NAME)


def load(path: Optional[str] = None) -> Dict[int, str]:
    """Load labels from ``path`` (or the default), returning ``{}`` on failure."""
    target = path if path is not None else default_path()
    result: Dict[int, str] = {}
    try:
        with open(target, encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, ValueError, UnicodeDecodeError):
        return result
    if not isinstance(loaded, dict):
        return result
    for key, value in loaded.items():
        if not isinstance(key, str):
            continue
        try:
            slot = int(key)
        except (TypeError, ValueError):
            continue
        if not (0 <= slot <= 79):
            continue
        if not isinstance(value, str):
            continue
        name = value.strip()
        if not name:
            continue
        if len(name) > MAX_LABEL:
            name = name[:MAX_LABEL]
        result[slot] = name
    return result


def save(labels: Dict[int, str], path: Optional[str] = None) -> None:
    """Save ``labels`` to ``path`` (or the default), swallowing only OSError."""
    target = path if path is not None else default_path()
    payload: dict[str, str] = {}
    for slot, name in labels.items():
        if isinstance(slot, bool) or not isinstance(slot, int):
            continue
        if not (0 <= slot <= 79):
            continue
        if not isinstance(name, str):
            continue
        cleaned = name.strip()
        if not cleaned:
            continue
        if len(cleaned) > MAX_LABEL:
            cleaned = cleaned[:MAX_LABEL]
        payload[str(slot)] = cleaned
    parent = os.path.dirname(target)
    tmp = target + ".tmp"
    try:
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, target)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


def set_label(labels: Dict[int, str], slot: int, name: str) -> Dict[int, str]:
    """Return a new dict with ``slot`` labelled (or removed if empty)."""
    if isinstance(slot, bool) or not isinstance(slot, int) or not (0 <= slot <= 79):
        raise ValueError(f"slot must be in 0..79, got {slot!r}")
    result = dict(labels)
    cleaned = name.strip() if isinstance(name, str) else str(name).strip()
    if not cleaned:
        result.pop(slot, None)
    else:
        if len(cleaned) > MAX_LABEL:
            cleaned = cleaned[:MAX_LABEL]
        result[slot] = cleaned
    return result
