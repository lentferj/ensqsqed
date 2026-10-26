# SPDX-License-Identifier: GPL-2.0-or-later
#
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# ensqsqed is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation as published by the Free Software Foundation; either
# version 2 of the License, or (at your option) later version.
#
# ensqsqed is distributed in the hope that it will be distributed under the
# terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# later version.

"""Read a user's sound file, and shape it for an import.

A file the user hands the importer is either an **all-sounds dump** -- the
32647-byte ``.syx``, or the Standard MIDI File that carries the same SysEx,
that an instrument answers to "send the internal bank" -- or a **single
sound**, the 204-byte record the front panel's "save sound to MIDI" puts
aside.  :mod:`sq.dumps` already reads both and decides which one a file
holds; this module is the import-shaped rest of the journey: one
:class:`SourceSound` per sound, in file order, with a name read back off
the record and the device the dump came from.

A SoundQuest ``.sqs`` bank and a SoundDiver ``.LIB`` library are not
dumps: they are read by their own decoders and flattened into the same
list.  A ``.LIB`` file contributes only its sound records; SoundDiver's
global settings and other data are not part of an import.

Names are the instrument's own wording, read with
:func:`sq.messages.sound_name` and stripped of its centering padding.  The
instrument pads short names with spaces, and that padding is display
convention rather than content, so it is stripped here.  A record whose
name this program cannot read at all is not a bad file -- it is just a
sound whose name is not known, and it is filed under ``?``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from sq import banks, dumps, lib, sqs
from sq import messages as m

__all__ = [
    "SourceSound",
    "ImportSource",
    "read_import",
    "validate_assignments",
    "merge_bank",
]


@dataclass(frozen=True)
class SourceSound:
    """One sound from a file, at its 0-based position in that file."""

    index: int  # 0-based position in the file
    name: str
    raw: bytes  # 204 bytes


@dataclass(frozen=True)
class ImportSource:
    """What a file the user picked turned out to hold."""

    path: str
    sounds: List[SourceSound]
    device: Optional[int]
    kinds: Tuple[str, ...]

    @property
    def is_full_bank(self) -> bool:
        return len(self.sounds) == banks.SOUNDS_PER_GROUP

    def summary(self) -> str:
        if self.is_full_bank:
            return f"{len(self.sounds)} sounds (full bank) from {self.path}"
        noun = "sound" if len(self.sounds) == 1 else "sounds"
        return f"{len(self.sounds)} {noun} from {self.path}"


def _name_of(raw: bytes) -> str:
    """The sound's name, stripped of padding; ``?`` when it is not known."""
    try:
        return m.sound_name(raw).rstrip()
    except m.ProtocolError:
        return "?"


def _read_sqs_import(path: str) -> ImportSource:
    """Read a SoundQuest ``.sqs`` file into one flat import list.

    Every non-empty slot of every bank is collected in file order.  A
    repeated favourite is one row, not two: the bytes are compared exactly,
    so an identical record is skipped.  The banks have no device of their
    own, and the sounds were selected rather than dumped from one bank, so
    ``device`` is :data:`None` and ``kinds`` says where they came from.
    """
    sounds: List[SourceSound] = []
    seen: Set[bytes] = set()
    for bank in sqs.read_sqs(path):
        for raw in bank.slots:
            if raw is None or raw in seen:
                continue
            seen.add(raw)
            sounds.append(SourceSound(index=len(sounds), name=_name_of(raw), raw=raw))
    if not sounds:
        raise ValueError(f"{path} holds no sounds this program recognises")
    return ImportSource(path=path, sounds=sounds, device=None, kinds=("sqs",))


def _read_lib_import(path: str) -> ImportSource:
    """Read a SoundDiver ``.lib`` library into one flat import list.

    The decoder returns 204-byte records in file order.  An identical
    record is one row, not two: the bytes are compared exactly.  A
    library has no device of its own -- the sounds were collected rather
    than dumped from one bank -- so ``device`` is :data:`None`, and
    ``kinds`` says where they came from.  Only sound records are read;
    SoundDiver's settings and global data are not an import.
    """
    sounds: List[SourceSound] = []
    seen: Set[bytes] = set()
    for raw in lib.read_lib(path):
        if raw in seen:
            continue
        seen.add(raw)
        sounds.append(SourceSound(index=len(sounds), name=_name_of(raw), raw=raw))
    if not sounds:
        raise ValueError(f"{path} holds no sounds this program recognises")
    return ImportSource(path=path, sounds=sounds, device=None, kinds=("sounddiver",))


def read_import(path: str) -> ImportSource:
    """Read a dump file into source sounds, ready for an import.

    An all-sounds dump (80 sounds) wins when the file has one; a file
    that only carries single sounds is read as such and enumerated from
    0.  A SoundQuest ``.sqs`` file or a SoundDiver ``.lib`` library is
    read by its own decoder first, and its records are flattened into one
    list.  A file that carries neither is an error, not an empty bank:
    the user pointed at something that was not a sound file, and an empty
    import would overwrite nothing and confirm nothing, which is a lie.
    """
    if sqs.is_sqs_path(path):
        return _read_sqs_import(path)
    if lib.is_lib_path(path):
        return _read_lib_import(path)
    library = dumps.read_file(path)
    if library.sounds:
        sounds = [
            SourceSound(index=number, name=_name_of(record), raw=record)
            for number, record in sorted(library.sounds.items())
        ]
        kinds: Tuple[str, ...] = ("all-sounds",)
    elif library.singles:
        sounds = [
            SourceSound(index=index, name=_name_of(record), raw=record)
            for index, record in enumerate(library.singles)
        ]
        kinds = ("single-sound",)
    else:
        raise ValueError(f"{path} holds no sounds this program recognises")
    return ImportSource(
        path=path,
        sounds=sounds,
        device=library.device,
        kinds=kinds,
    )


def validate_assignments(assignments: Dict[int, int]) -> List[str]:
    """The human sentences that say why an assignment map is not shippable.

    An empty list means the assignments are good to go.  The function
    does not know the source, so it can only check the shape of the map:
    a target slot outside 0-79, and a target number claimed twice.
    Whether the sources exist is a question for the file the map came
    from, and is checked when that file was read.
    """
    problems: List[str] = []
    claimed: Dict[int, int] = {}
    for source, target in sorted(assignments.items()):
        if not 0 <= target < banks.SOUNDS_PER_GROUP:
            problems.append(
                f"target slot {target} is outside 0-{banks.SOUNDS_PER_GROUP - 1}"
            )
            continue
        if target in claimed:
            problems.append(f"target slot {target} is assigned to more than one sound")
        claimed[target] = source
    return problems


def merge_bank(
    existing: Dict[int, bytes], assignments: Dict[int, bytes]
) -> Dict[int, bytes]:
    """The bank after an import: the existing one, with the assigned slots written.

    Shallow copy, then overwrite.  Slots the assignments do not touch are
    the bank's own, and are preserved -- that is the whole point of
    merging rather than replacing: a single-sound import must not wipe
    the rest of the target bank it was not asked about.
    """
    merged = dict(existing)
    merged.update(assignments)
    return merged
