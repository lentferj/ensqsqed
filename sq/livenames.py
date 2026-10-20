# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# ensqsqed is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 2 of the License, or (at your option) any later
# version.
#
# ensqsqed is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.

"""Live names sidecar: where names read from the device are kept between runs.

The sidecar lives beside the catalog file and has the same name with a
`.livenames` extension. It is a simple JSON mapping from ``BANK:NNN`` keys
to names. Only :class:`OSError` is swallowed -- a read-only directory or a
full disk, where forgetting the names is better than refusing to run.
"""

from __future__ import annotations

import json
import os
from typing import Dict

__all__ = ["key", "path_for", "save", "apply_to"]

_SUFFIX = ".livenames"


def key(bank_id: str, number: int) -> str:
    """The key for a slot in the sidecar: ``BANK:NNN``."""
    return f"{bank_id}:{number:03d}"


def path_for(catalog_path: str) -> str:
    """The sidecar path for a given catalog path."""
    base, _ = os.path.splitext(catalog_path)
    return base + _SUFFIX


def save(names: Dict[str, str], path: str) -> None:
    """Merge ``names`` into the live-names sidecar at ``path``.

    Merged, not replaced: a scan reads one group at a time and each group
    is adopted as soon as it finishes, so a plain write would leave only
    the group scanned last. Existing keys survive unless this call names
    them again.
    """
    existing: Dict[str, str] = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                existing = {
                    k: v
                    for k, v in loaded.items()
                    if isinstance(k, str) and isinstance(v, str)
                }
        except (OSError, json.JSONDecodeError):
            existing = {}
    existing.update(names)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(existing, handle, ensure_ascii=False, sort_keys=True)
    except OSError:
        pass


def apply_to(catalog, path: str) -> int:
    """Apply live names from the sidecar to the catalog.

    Returns the number of names applied.
    """
    if not os.path.exists(path):
        return 0
    try:
        with open(path, encoding="utf-8") as handle:
            names = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(names, dict):
        return 0
    applied = 0
    for k, v in names.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        bank_id, _, number = k.partition(":")
        if bank_id and number.isdigit():
            catalog.set_live_name(bank_id, int(number), v)
            applied += 1
    return applied
