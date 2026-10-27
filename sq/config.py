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

"""The local settings cache: which port answered, which channel, and
where the backups live.

Now a binding of :class:`vinsynlib.config.Settings` rather than a second
copy of it. The behaviour, including the traps it is written around, is
the family's and lives in one place:

* A TOML boolean is not a channel. ``isinstance(True, int)`` is true, so a
  hand-edited ``channel = true`` used to come back as ``True``, pass every
  range check, and then ``0xC0 | True`` is MIDI channel 2 -- a wrong
  channel being not an error anywhere.
* Everything written is escaped. A port name is whatever ALSA reports, and
  a quote in one produces a file that is not TOML, which the writer then
  refuses to overwrite: a cache that never heals.
* Only :class:`OSError` is swallowed -- a read-only directory or a full
  disk, where forgetting a preference beats refusing to run.

What is added here is the name. One string decides what the one warning
message says, and it has to be right: several of these programs can be
open in one session, and a user who is told a file could not be parsed has
to be able to tell which one is unhappy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from vinsynlib.config import DEFAULT_PATH, UNSET, Settings

__all__ = [
    "DEFAULT_BACKUP_ROOT",
    "DEFAULT_PATH",
    "load_backup_root",
    "load_channel",
    "save_channel",
    "load_last_port",
    "save_last_port",
    "load_last_recv_port",
    "save_last_recv_port",
    "load_port_pair",
    "save_ports",
    "load_device_id",
    "save_device_id",
    "load_ui_state",
    "save_ui_state",
]

#: Where the backup runs put their stamped directories.
#:
#: A backup run writes ``<stamp>/all_presets_dump.bin`` and the sidecars
#: beside it; the store assist reads the newest stamp under this root.
#: The location is the bench's own (``~/temp/sqr_backup``), and it is a
#: key in the settings file so a user whose backups live elsewhere can
#: say so without editing the program.
DEFAULT_BACKUP_ROOT = str(Path.home() / "temp" / "sqr_backup")

_settings = Settings("ensqsqed", DEFAULT_PATH)

#: Module-level aliases, so every existing call site reads the same as it
#: did before: ``config.load_channel(path)``.
load_channel = _settings.load_channel
save_channel = _settings.save_channel
load_last_port = _settings.load_port
save_last_port = _settings.save_port
load_last_recv_port = _settings.load_recv_port
save_last_recv_port = _settings.save_recv_port
load_device_id = _settings.load_device_id
save_device_id = _settings.save_device_id
#: Both ports at once, in one write. What the connection policy saves after a
#: sweep: a half-written pair is a pair where the second half is lost.
save_ports = _settings.save_ports


def load_port_pair(path: Optional[str] = None) -> Optional[Tuple[str, str]]:
    """The remembered port pair, or None.

    :meth:`vinsynlib.config.Settings.load_ports` answers only when *both*
    keys are present, because for most of the family a half-remembered pair
    is not a pair. Here it would also be wrong in the other direction: this
    tool has always written ``recv_port`` only when it differed from
    ``port``, so every config written before the remembered-port path existed
    would read as nothing remembered and the next launch would sweep every
    port again -- the exact thing the pair is for. One remembered output port
    is a complete pair unless the file says otherwise.
    """
    send = _settings.load_port(path)
    if not send:
        return None
    return (send, _settings.load_recv_port(path) or send)


def load_backup_root(path: Optional[str] = None) -> str:
    """The directory to look under for backups.

    ``backup_root`` is a path, and only a non-empty string is one: a
    hand-edited ``backup_root = 7`` falls back to
    :data:`DEFAULT_BACKUP_ROOT` rather than being joined onto as if it
    were a path.  The default is the bench's ``~/temp/sqr_backup``, so a
    settings file that has never heard of the key still finds the
    backups that are there.
    """
    data = _settings.read(path)[0]
    value = data.get("backup_root")
    if isinstance(value, str) and value.strip():
        return value
    return DEFAULT_BACKUP_ROOT


def load_ui_state(path: Optional[str] = None) -> Dict[str, Any]:
    """Load UI state: bank index, slot cursor, view mode, categories,
    rating filter, the remembered import folder, and the single-import
    opt-in choices."""
    data = _settings.read(path)[0]
    result: Dict[str, Any] = {}
    if "bank_index" in data and isinstance(data["bank_index"], int):
        result["bank_index"] = data["bank_index"]
    if "slot_cursor" in data and isinstance(data["slot_cursor"], int):
        result["slot_cursor"] = data["slot_cursor"]
    if "view_mode" in data and isinstance(data["view_mode"], str):
        result["view_mode"] = data["view_mode"]
    if "categories" in data and isinstance(data["categories"], list):
        result["categories"] = data["categories"]
    # `isinstance(True, int)` is True, so the bool check comes first or
    # `rating_stars = true` loads as a one-star filter -- the same trap the
    # channel setting has, on the one key where it would look like a filter
    # rather than like a wrong MIDI channel.
    stars = data.get("rating_stars")
    if type(stars) is int and 0 <= stars <= 5:
        result["rating_stars"] = stars
        at_least = data.get("rating_at_least")
        result["rating_at_least"] = at_least is True
    # A TOML boolean is not an integer, and `isinstance(True, int)` is True:
    # check the bool before anything that could widen it, or a `1` in the
    # file would come back as a preference the user never set. `type(...) is
    # bool` is the narrowest check and the one the comment above the stars
    # key warns about.
    dismissed = data.get("scan_offer_dismissed")
    if type(dismissed) is bool:
        result["scan_offer_dismissed"] = dismissed
    # The import folder is a path, so only a string is accepted;
    # `import_folder = true` is a hand edit, not a folder, and is
    # ignored rather than joined onto as if it were a directory.
    import_folder = data.get("import_folder")
    if isinstance(import_folder, str):
        result["import_folder"] = import_folder
    # The single-import choices are booleans.  A hand-edited 1 is not the
    # same as a checkbox the user ticked, so reject anything that is not a
    # real bool rather than letting TOML's integer widen it.
    for key in (
        "import_store_permanently",
        "import_autoload_preview",
        "import_autoplay_preview",
    ):
        value = data.get(key)
        if type(value) is bool:
            result[key] = value
    return result


def save_ui_state(
    path: Optional[str] = None,
    *,
    bank_index: Any = UNSET,
    slot_cursor: Any = UNSET,
    view_mode: Any = UNSET,
    categories: Any = UNSET,
    rating_stars: Any = UNSET,
    rating_at_least: Any = UNSET,
    scan_offer_dismissed: Any = UNSET,
    import_folder: Any = UNSET,
    import_store_permanently: Any = UNSET,
    import_autoload_preview: Any = UNSET,
    import_autoplay_preview: Any = UNSET,
) -> None:
    """Save UI state (bank position, slot cursor, view mode, categories).

    Anything left at :data:`UNSET` is not passed on, so a caller that saves
    the view does not also rewrite the bank position. Passing ``None`` for a
    rating key is *not* the same thing: it removes it, which is what clearing
    a filter has to do -- the file otherwise keeps the old value and the
    filter comes back on the next launch.
    """
    changes: Dict[str, Any] = {}
    for key, value in (
        ("bank_index", bank_index),
        ("slot_cursor", slot_cursor),
        ("view_mode", view_mode),
        ("categories", categories),
        ("rating_stars", rating_stars),
        ("rating_at_least", rating_at_least),
        ("scan_offer_dismissed", scan_offer_dismissed),
        ("import_folder", import_folder),
        ("import_store_permanently", import_store_permanently),
        ("import_autoload_preview", import_autoload_preview),
        ("import_autoplay_preview", import_autoplay_preview),
    ):
        if value is not UNSET:
            changes[key] = value
    if changes:
        _settings.update(path, **changes)
