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

"""The last all-presets dump, kept between launches.

An all-presets dump is about twelve seconds on the wire.  Without a cache
every launch either waits for it or refuses early actions while it runs;
with one, ``p`` opens at once from the previous launch's data and the
instrument is only asked again when the cache is old.

The bytes are the decoded dump -- the same 17977-byte blob command 04
returns -- not wire frames.  The header (the 21 global bytes and the
tables) is part of it, so ``AllPresetsDump._from_reply`` can rebuild the
parsed object without guessing.

Disposable, like the settings cache: deleting the file costs one read.
Only :class:`OSError` is swallowed on save; a cache that cannot be
written must not stop the program.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Optional

from vinsynlib.config import config_dir

__all__ = ["NAME", "default_path", "age_seconds", "load", "save"]

#: The cache file's name, beside ``preset_labels.json`` in the config dir.
NAME = "presets.cache"


def default_path() -> str:
    """The cache's location in the per-tool config directory."""
    return os.path.join(config_dir("ensqsqed"), NAME)


def age_seconds(path: Optional[str] = None) -> Optional[float]:
    """How old the cache is in seconds, or ``None`` when there is none."""
    target = Path(path) if path is not None else Path(default_path())
    try:
        return max(0.0, time.time() - target.stat().st_mtime)
    except OSError:
        return None


def load(path: Optional[str] = None) -> Optional[bytes]:
    """The cached dump, or ``None`` when it is missing or unreadable."""
    target = Path(path) if path is not None else Path(default_path())
    try:
        data = target.read_bytes()
    except OSError:
        return None
    return data or None


def save(data: bytes, path: Optional[str] = None) -> None:
    """Write the dump atomically; a cache that cannot be written is silent.

    Only :class:`OSError` is swallowed: a read-only config directory or a
    full disk should cost the next read, not the program.
    """
    target = Path(path) if path is not None else Path(default_path())
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "wb", dir=target.parent, delete=False, prefix=target.name + "."
        ) as handle:
            handle.write(data)
            temporary = Path(handle.name)
        os.replace(temporary, target)
    except OSError:
        return
