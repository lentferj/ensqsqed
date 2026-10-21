# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# The layout is transcribed from Ensoniq's SQ-1 MIDI System Exclusive
# Implementation Specification, Appendix A: section 4.8 (the preset and
# its track structure), section 5.1 and 5.2 (the effect structure and
# its six parameter sets), and notes 3, 5, 6 and 7.  The rack's own
# wording for the values comes from the SQ-R Plus owner's manual, part 3
# (Presets) and part 4 (Effekte).  Every numeric claim was checked
# against dumps the instrument produced; see docs/PRESET-FRAMING.md.
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

"""A preset is 168 bytes, and 112 of them are eight tracks of fourteen
bytes each.

The SQ-R's preset is a **performance setup**, not a sound: eight tracks
with their own sound, mix, key range and MIDI settings, one effect that
all eight share, and a "pile" that says which of the eight tracks are
switched on.  This module is the parse of that, and it is deliberately
the *bytes* rather than the panel: a preset read from the unit is
written back unchanged unless somebody edits it, and every conversion
between the unit's storage and the panel's wording happens in one
place, here, where it can be pointed at.

**Bit 7 of a name byte is not part of the name.**  The specification says
so (Note 3): preset, sequence and song names are plain eight-bit ASCII,
but the top bit of each byte "is reserved for track mute status and
should not be used or changed".  Every preset in the dumps this project
has carries its top bit set on the first nine name bytes -- the ones
that spell the name -- and clear on the rest, so a name that has been
read back and written back with its bits shifted would come back mute.
:attr:`Preset.name` therefore reads the bytes with that bit cleared and
:attr:`Preset.name`'s writer puts the *text* into the low seven bits and
leaves the unit's flag bits where they were.

**Three things here are not the specification's, and they are marked as
such.**

*Track byte 6 is 0x80 in every track of every dump this project has*
(640 tracks in ``all_seq.bin``), where the specification calls the byte
reserved.  It is stored and restored verbatim and never normalised: the
unit put a bit there, this project does not know what it means, and
guessing would be worse than keeping it.

*The pile, not the MIDI status, is where a track's on/off state lives.*
The specification's track structure has no off state at all -- its
status byte says BOTH, LOCAL, MIDI or *EXT* -- while the manual's panel
shows a track as "—" when it is off.  In the dumps, the tracks the
panel would show as off have full parameters and a MIDI status of
LOCAL, exactly like the track that is sounding; the only thing that
tells them apart is the pile byte, which is zero for them.  So
:attr:`Preset.is_active` and :attr:`Preset.set_active` work on the pile,
and the specification's *EXT* status is left alone: no dump here
exercises BOTH, MIDI or *EXT* either, and an off track is not one of
them.

*Byte 8 packs three fields into one byte*: status in bits 7 and 6,
pressure in bits 5 and 4, the MIDI channel in bits 3 to 0.  Two fields
of two bits and one of four fill the byte exactly, and every track in
every dump agrees -- the fixture's tracks read 0x50 to 0x57 (LOCAL,
KEY, channels 0 to 7) and 0x5a and 0x5b (LOCAL, KEY, channels 10 and
11).

**What is empirical and what is not.**  The layout, the field ranges,
the packing and the sound-number word are the specification's
(§4.8, §4.8.1, §4.8.2 and Note 5).  The panel's wording for the
values -- Volume 0..99, Pan SOUND or -98..+99, Timbre 00..99, Release
-64..+64, Output -DRY-/-FX1-/-FX2-/VOICE-/CONTROL-, MIDI Status
BOTH/LOCAL/MIDI, Pressure NONE/KEY/CHANNEL, Sustain ON/OFF, MIDI
Program 001..128, KeyRange A0..C8, Transpose +-4 octaves and +-11
semitones -- is the German manual's, part 3.  The thirteen effect names
are the manual's too, part 4; the specification spells some of them
differently and the spelling it uses is kept in a comment beside each.

One conversion could not be settled by the documentation and is
settled by the rule this module states instead: **the pan**.  The
specification's Note 7 says a parameter stored as 0..127 and shown as
0..99 is converted through a table of 128 entries, and that for a
negative internal value the sign bit is ignored to get the table index.
That makes the pan byte a sign-and-magnitude pair, with 0x80 -- which
would otherwise be a "-0" -- reserved for the panel's word SOUND.  The
manual prints the panel's range as -98..+99 while the table reaches -99
at the hard left; no dump this project has holds anything but SOUND, so
the difference of one is at an end nothing exercises.  See
:func:`pan_display`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, List, NamedTuple, Optional, Tuple

__all__ = [
    "TRACK_BYTES",
    "PRESET_BYTES",
    "EFFECT_BYTES",
    "SEQUENCE_BLOB_BYTES",
    "DISPLAY_TABLE",
    "TRACK_RANGES",
    "NOTE_NAMES",
    "OUTPUT_NAMES",
    "STATUS_NAMES",
    "PRESSURE_NAMES",
    "SUSTAIN_NAMES",
    "EFFECTS",
    "MIX_SOURCES",
    "PARAM_SETS",
    "EFFECT_SETS",
    "PAN_SOUND",
    "note_name",
    "display_value",
    "stored_value",
    "pan_display",
    "pan_stored",
    "sound_word",
    "split_single_dump",
    "Track",
    "EffectParam",
    "Effect",
    "Preset",
]

#: The length of a track's parameter structure (§4.8.1).
TRACK_BYTES = 14

#: The length of a preset (§4.8.2).
PRESET_BYTES = 168

#: The length of the effect structure: ten parameter bytes and the effect
#: number (§5.1).
EFFECT_BYTES = 11

#: The length of the sequence-data blob that precedes the preset in a
#: single sequence dump (§4.6).  The blob begins with its own length as a
#: long word, so a reader does not have to take this on trust.
SEQUENCE_BLOB_BYTES = 48

#: Note 7's conversion table: the internal value 0..127 as the panel
#: prints it, 0..99.  It is the same table for every parameter that is
#: stored 0..127 and shown 0..99, and for the half of a signed
#: parameter that lies below zero -- there the sign is taken from the
#: sign bit and the table index from the rest of the byte.
#:
#: The specification prints the table as eight lines of sixteen, and so
#: does this, because a table that is laid out differently is a table
#: that has to be checked twice.
_DISPLAY_TABLE_TEXT = """
    00,01,01,02,03,04,04,05,06,07,08,08,09,10,11,12
    12,13,14,15,15,16,17,18,19,19,20,21,22,22,23,24
    25,26,26,27,28,29,29,30,31,32,33,33,34,35,36,36
    37,38,39,40,40,41,42,43,44,44,45,46,47,47,48,49
    50,51,51,52,53,54,54,55,56,57,58,58,59,60,61,62
    62,63,64,65,65,66,67,68,69,69,70,71,72,72,73,74
    75,76,76,77,78,79,79,80,81,82,83,83,84,85,86,86
    87,88,89,90,90,91,92,93,94,94,95,96,97,97,98,99
"""

DISPLAY_TABLE: Tuple[int, ...] = tuple(
    int(part) for part in _DISPLAY_TABLE_TEXT.replace(",", " ").split()
)

#: The first stored value that prints as each of 0..99.  Several stored
#: values print the same number (1 and 2 both print 1), so the table has
#: to be inverted with a rule; the first one is the rule, and it is the
#: only one that round-trips a stored value unchanged.
_FIRST_INDEX: Tuple[int, ...] = tuple(
    DISPLAY_TABLE.index(value) for value in range(100)
)

#: The stored value that prints as each of 0..99 for a parameter stored
#: 0..15 (Note 7's second rule: the value is turned into an index in
#: 0..127 for the table above).  Sixteen stored values print sixteen
#: numbers, so most of the table has no entry.
_FIFTEEN_INDEX: Dict[int, int] = {
    DISPLAY_TABLE[value * 127 // 15]: value for value in range(16)
}

#: The twelve note names, sharp spelling, as the panel writes them.
NOTE_NAMES: Tuple[str, ...] = (
    "C",
    "C#",
    "D",
    "D#",
    "E",
    "F",
    "F#",
    "G",
    "G#",
    "A",
    "A#",
    "B",
)

#: The output bus a track is routed to (§4.8.1 offset 7, and the
#: manual's Mix Bank).  The panel prints them between dashes.
OUTPUT_NAMES: Tuple[str, ...] = ("DRY", "FX1", "FX2", "VOICE", "CONTROL")

#: The MIDI status of a track (§4.8.1 offset 8).  The fourth is the
#: specification's spelling of a status this project has never seen in a
#: dump.
STATUS_NAMES: Tuple[str, ...] = ("BOTH", "LOCAL", "MIDI", "*EXT*")

#: The kind of aftertouch a track listens for (§4.8.1 offset 8).
PRESSURE_NAMES: Tuple[str, ...] = ("NONE", "KEY", "CHANNEL")

#: Whether a track reacts to the sustain pedal (§4.8.1 offset 9).
SUSTAIN_NAMES: Tuple[str, ...] = ("ON", "OFF")

#: The thirteen effects, in the order the specification numbers them
#: (§5.1.1), with the names the rack's own panel uses (manual, part 4).
#:
#: ======================  ================================  ==========
#: number                  name here                        spec's name
#: ======================  ================================  ==========
#: 0                       CONCERT REVERB                   CONCERT HALL
#: 1                       HALL.REVERB                      HALL REVERB
#: 2                       ROOM.REVERB                      ROOM REVERB
#: 3                       WARM CHAMBER                     WARM CHAMBER
#: 4                       8-VOICE.CHORUS                   8-VOICE CHORUS
#: 5                       CHORUS & REVERB                  CHORUS+REVERB
#: 6                       FLANGER+REVERB.1                 FLANGER+REVERB 1
#: 7                       FLANGER+REVERB.2                 FLANGER+REVERB 2
#: 8                       PHASE SHIFTER                    PHASE SHIFTER
#: 9                       PHASER+REVERB                    PHASER+REVERB
#: 10                      ROTORY SPKR+VERB                 ROTORY SPKR+VERB
#: 11                      DIST+CHORUS+VERB                 DIST+CHORUS+VERB
#: 12                      CMPRSS+DIST+VERB                 CMPRSS+DIST+VERB
#: ======================  ================================  ==========
EFFECTS: Tuple[str, ...] = (
    "CONCERT REVERB",
    "HALL.REVERB",
    "ROOM.REVERB",
    "WARM CHAMBER",
    "8-VOICE.CHORUS",
    "CHORUS & REVERB",
    "FLANGER+REVERB.1",
    "FLANGER+REVERB.2",
    "PHASE SHIFTER",
    "PHASER+REVERB",
    "ROTORY SPKR+VERB",
    "DIST+CHORUS+VERB",
    "CMPRSS+DIST+VERB",
)

#: The effect modulation sources (§5.1.2), in the order the
#: specification numbers them.  The manual's list (part 4) spells the
#: sustain pedal SUS-PEDAL and the last entry OFF; the specification
#: writes both with asterisks and stops one letter short.
MIX_SOURCES: Tuple[str, ...] = (
    "KEYBOARD",
    "VELOCITY",
    "PRESSURE",
    "PITCHWHL",
    "MODWHEEL",
    "MODPEDAL",
    "XCONTROL",
    "SUS-PEDL",
    "TIMBRE",
    "RAMP-1",
    "RAMP-2",
    "RAMP-3",
    "RAMP-4",
    "RAMP-5",
    "RAMP-6",
    "*OFF*",
)

#: The pan value that means "the sound's own pan" -- the panel prints
#: the word SOUND, and the byte is 0x80, which under Note 7's rule would
#: otherwise be a "-0" the panel has no use for.
PAN_SOUND = -128


def note_name(key: int) -> str:
    """``21`` -> ``"A0"``.  The key range is unreadable as bare numbers
    and perfectly readable as notes.

    The keyboard runs from A0 (21) to C8 (108), which is the middle C
    octave naming the manual uses.  A number outside that is printed as
    the number, because a corrupt dump should still be readable.
    """
    if not 21 <= key <= 108:
        return str(key)
    return f"{NOTE_NAMES[key % 12]}{key // 12 - 1}"


def display_value(stored: int) -> int:
    """A stored 0..127 value as the panel prints it, through Note 7's
    table."""
    if not 0 <= stored <= 127:
        raise ValueError(f"value {stored} is outside 0-127")
    return DISPLAY_TABLE[stored]


def stored_value(display: int) -> int:
    """The stored value that prints as ``display``.

    Several stored values print the same number, so the table has to be
    inverted by a rule; the first stored value that prints the wanted
    number is the one used, because it is the only inversion that hands
    back a stored value unchanged.
    """
    if not 0 <= display <= 99:
        raise ValueError(f"value {display} is outside 0-99")
    return _FIRST_INDEX[display]


def pan_display(stored: int) -> Optional[int]:
    """The panel's pan for a stored value, or None for SOUND.

    **The rule this module uses**, because the documentation does not
    settle it and the fixtures cannot either:

    * 0x80 (-128) is SOUND.  The panel prints the word, and the track
      then follows the pan of its own sound program.
    * Any other byte with the top bit set is a pan to the left: the sign
      bit is the sign and the remaining seven bits are the index into
      Note 7's table, so 0x81 is -1 and 0xFF is -99.
    * A byte with the top bit clear is a pan to the right, and is the
      table index itself, so 0x00 is 0 and 0x7F is +99.

    The manual prints the panel's range as -98..+99 and the table
    reaches -99 at the hard left; the difference is one step at an end
    that no dump this project has holds.  Every track in every dump is
    at SOUND, which is all the fixture proves.
    """
    if stored == PAN_SOUND:
        return None
    byte = stored & 0xFF
    if byte & 0x80:
        return -DISPLAY_TABLE[byte & 0x7F]
    return DISPLAY_TABLE[byte]


def pan_stored(display: Optional[int]) -> int:
    """The stored pan that prints as ``display``; None means SOUND.

    The inverse of :func:`pan_display`, with the same rule.
    """
    if display is None:
        return PAN_SOUND
    if not -99 <= display <= 99:
        raise ValueError(f"pan {display} is outside -99-99")
    if display < 0:
        return (0x80 | _FIRST_INDEX[-display]) - 0x100
    return _FIRST_INDEX[display]


def sound_word(group: str, slot: int) -> int:
    """The 16-bit sound-number word for a group and a slot (Note 5).

    The word is ``ccrrrrrr nnnnnnnn``: the bank code in bits 14 and 15
    -- 0 internal, 1 the permanent ROM sounds, 2 card A, 3 card B -- the
    sound number in the low byte, and the six bits between them
    reserved.  The manual counts sounds from 001 and the byte holds 000,
    so the slot is one less than the number the panel prints.

    The drum kits are the numbers 80 to 99 and ignore the bank code: a
    kit is where it is, whichever group the rest of the word names.
    """
    if group == "DRUM":
        if not 1 <= slot <= 20:
            raise ValueError(f"slot {slot} is outside 1-20")
        return 80 + slot - 1
    codes = {"INT": 0, "ROM": 1, "CARDA": 2, "CARDB": 3}
    if group not in codes:
        raise ValueError(
            f"no sound group {group!r}; have {', '.join(sorted(codes))} and DRUM"
        )
    if not 1 <= slot <= 80:
        raise ValueError(f"slot {slot} is outside 1-80")
    return (codes[group] << 14) | (slot - 1)


def split_single_dump(data: bytes) -> Tuple[bytes, bytes]:
    """The sequence blob and the preset of a single sequence dump.

    §4.6 lays the message out as the sequence data and its header,
    followed by the 168-byte preset.  The blob starts with its own
    length as a long word, so the split is read out of the data rather
    than assumed.
    """
    if len(data) < 4:
        raise ValueError(f"a single sequence dump is at least 4 bytes, not {len(data)}")
    size = int.from_bytes(data[:4], "big")
    if len(data) != size + PRESET_BYTES:
        raise ValueError(
            f"a single sequence dump is {size} bytes of sequence data and "
            f"{PRESET_BYTES} bytes of preset, not {len(data)}"
        )
    return data[:size], data[size:]


#: The ranges the unit documents for a track's fields (§4.8.1).  Byte 6
#: and byte 13 are not in here: the specification calls them reserved,
#: and they are kept whatever they hold.
TRACK_RANGES: Dict[str, Tuple[int, int]] = {
    "sound_number": (0, 0xFFFF),
    "volume": (0, 127),
    "pan": (-128, 127),
    "timbre": (0, 127),
    "release": (-64, 63),
    "output": (0, 4),
    "status": (0, 3),
    "pressure": (0, 2),
    "midi_channel": (0, 15),
    "sustain": (0, 1),
    "midi_program": (0, 127),
    "key_low": (21, 108),
    "transpose": (-59, 59),
    "key_high": (21, 108),
}


@dataclass(frozen=True)
class Track:
    """One of a preset's eight tracks: the fourteen bytes of §4.8.1.

    **The fields are the unit's own values, not the panel's.**  A track
    read from a dump is a fact about the instrument's memory, and the
    bytes that hold it are stored as they came: the panel's wording is
    a reading of these numbers, offered as properties beside them, and
    never mixed into them.  That way a preset read from the unit and
    written back without an edit is byte for byte the preset that was
    read -- which is the whole point of a write path.

    The conversions the panel needs are properties:

    ==================  ============================================
    property             what it answers
    ==================  ============================================
    ``group``, ``slot``  which sound the track plays
    ``volume_display``   Volume 0..99
    ``pan_display``      the pan, or None for SOUND
    ``pan_label``        the pan as the panel prints it
    ``timbre_display``   Timbre 00..99
    ``midi_channel_display``  MIDI channel 1..16
    ``midi_program_display``  MIDI program 001..128
    ``key_low_name``, ``key_high_name``  the key range as notes
    ``transpose_octave``, ``transpose_semitone``  the transpose
    ``output_name``, ``status_name``, ``pressure_name``,
    ``sustain_name``     the panel's word for an enumerated value
    ==================  ============================================

    To change a value, make a copy with :meth:`updated`, which checks
    the new value against the range the unit documents.
    """

    #: The sound the track plays, as the 16-bit word of Note 5: the
    #: bank code in bits 14 and 15 and the sound number in the low
    #: byte.  :attr:`group` and :attr:`slot` read it.
    sound_number: int = 0
    #: Volume, 0..127 as stored and 0..99 as printed.
    volume: int = 127
    #: Pan, -128..127 as stored.  -128 is SOUND; see :func:`pan_display`.
    pan: int = PAN_SOUND
    #: Timbre, 0..127 as stored and 0..99 as printed.
    timbre: int = 0
    #: Release time adjustment, -64..63, the same stored and printed.
    #:
    #: The manual prints the panel's range as -64..+64 and the
    #: specification as -64..63.  A byte is a signed number over the
    #: whole of -128..127, so both fit, and the difference is one step
    #: at an end the specification does not allow to be reached.
    release: int = 0
    #: Byte 6, which the specification calls reserved and which the
    #: unit fills with 0x80 in every track of every dump this project
    #: has.  Stored and restored verbatim; never normalised.
    reserved: int = 0x80
    #: The output bus: 0..4, printed as -DRY-, -FX1-, -FX2-, VOICE or
    #: CONTROL.
    output: int = 4
    #: The MIDI status: 0..3, printed BOTH, LOCAL, MIDI or *EXT*.
    status: int = 1
    #: The aftertouch kind: 0..2, printed NONE, KEY or CHANNEL.
    pressure: int = 1
    #: The MIDI channel, 0..15 as stored and 1..16 as printed.
    midi_channel: int = 0
    #: Whether the track reacts to the sustain pedal.  Bit 7 of byte 9.
    sustain: bool = True
    #: The MIDI program, 0..127 as stored and 1..128 as printed.
    midi_program: int = 0
    #: The lowest key the track sounds, 21..108 (A0..C8).
    key_low: int = 21
    #: The transpose, in semitones, -59..59.
    transpose: int = 0
    #: The highest key the track sounds, 21..108 (A0..C8).
    key_high: int = 108
    #: Byte 13, which the specification calls reserved.  Stored and
    #: restored verbatim; it is zero in every dump this project has.
    reserved_tail: int = 0

    @classmethod
    def from_bytes(cls, data: bytes) -> "Track":
        """A track from its fourteen bytes.

        Nothing is checked here beyond the length: a dump is a fact
        about the instrument's memory, and a value outside a documented
        range is still what the instrument holds.  The ranges are
        checked where a value is chosen, in :meth:`updated`.
        """
        if len(data) != TRACK_BYTES:
            raise ValueError(f"a track is {TRACK_BYTES} bytes, not {len(data)}")
        return cls(
            sound_number=int.from_bytes(data[0:2], "big"),
            volume=data[2],
            pan=data[3] - 256 if data[3] & 0x80 else data[3],
            timbre=data[4],
            release=data[5] - 256 if data[5] & 0x80 else data[5],
            reserved=data[6],
            output=data[7],
            status=data[8] >> 6,
            pressure=(data[8] >> 4) & 0x03,
            midi_channel=data[8] & 0x0F,
            sustain=bool(data[9] & 0x80),
            midi_program=data[9] & 0x7F,
            key_low=data[10],
            transpose=data[11] - 256 if data[11] & 0x80 else data[11],
            key_high=data[12],
            reserved_tail=data[13],
        )

    def to_bytes(self) -> bytes:
        """The track's fourteen bytes, as the unit stores them."""
        return bytes(
            [
                (self.sound_number >> 8) & 0xFF,
                self.sound_number & 0xFF,
                self.volume & 0xFF,
                self.pan & 0xFF,
                self.timbre & 0xFF,
                self.release & 0xFF,
                self.reserved & 0xFF,
                self.output & 0xFF,
                (self.status << 6) | (self.pressure << 4) | (self.midi_channel & 0x0F),
                (0x80 if self.sustain else 0x00) | (self.midi_program & 0x7F),
                self.key_low & 0xFF,
                self.transpose & 0xFF,
                self.key_high & 0xFF,
                self.reserved_tail & 0xFF,
            ]
        )

    def updated(self, **changes: int) -> "Track":
        """This track with some fields changed, each one checked.

        The ranges are the unit's own (§4.8.1).  A value outside them
        is refused with a sentence, because a track that cannot be
        played is worth knowing about before it is written back.
        """
        for name, value in changes.items():
            bounds = TRACK_RANGES.get(name)
            if bounds is None:
                raise ValueError(f"a track has no field {name!r}")
            if not bounds[0] <= value <= bounds[1]:
                raise ValueError(f"{name} {value} is outside {bounds[0]}-{bounds[1]}")
        # The copy is made first and the fields are set on it afterwards,
        # so that a field whose type is not a plain number -- the
        # sustain switch -- can be written the way the caller means it.
        updated = replace(self)
        for name, value in changes.items():
            object.__setattr__(updated, name, value)
        return updated

    # --- the sound the track plays -----------------------------------------

    @property
    def group(self) -> str:
        """The sound group: INT, ROM, CARDA, CARDB or DRUM.

        Note 5 puts the bank code in bits 14 and 15 of the sound number
        -- 0 internal, 1 the permanent ROM sounds, 2 card A, 3 card B --
        but the numbers 80 to 99 are the permanent drum kits and ignore
        the bank code entirely.
        """
        number = self.sound_number & 0xFF
        if number >= 80:
            return "DRUM"
        return ("INT", "ROM", "CARDA", "CARDB")[(self.sound_number >> 14) & 0x03]

    @property
    def slot(self) -> int:
        """The sound's number inside its group, counted from 1.

        The manual counts sounds from 001, the unit stores them from 0.
        The drum kits are counted the same way, from 1 to 20.
        """
        number = self.sound_number & 0xFF
        if number >= 80:
            return number - 80 + 1
        return number + 1

    # --- the panel's readings ------------------------------------------------

    @property
    def volume_display(self) -> int:
        """Volume as the panel prints it, 0..99."""
        return DISPLAY_TABLE[self.volume & 0x7F]

    @property
    def pan_display(self) -> Optional[int]:
        """The pan as the panel prints it, or None for SOUND."""
        return pan_display(self.pan)

    @property
    def pan_label(self) -> str:
        """The pan as the panel prints it: the word SOUND, or a signed
        number."""
        shown = self.pan_display
        if shown is None:
            return "SOUND"
        return f"{shown:+d}"

    @property
    def timbre_display(self) -> int:
        """Timbre as the panel prints it, 0..99."""
        return DISPLAY_TABLE[self.timbre & 0x7F]

    @property
    def output_name(self) -> str:
        """The output bus as the panel prints it."""
        return _name_for(OUTPUT_NAMES, self.output, "output")

    @property
    def status_name(self) -> str:
        """The MIDI status as the panel prints it."""
        return _name_for(STATUS_NAMES, self.status, "status")

    @property
    def pressure_name(self) -> str:
        """The aftertouch kind as the panel prints it."""
        return _name_for(PRESSURE_NAMES, self.pressure, "pressure")

    @property
    def sustain_name(self) -> str:
        """The sustain pedal switch as the panel prints it."""
        return SUSTAIN_NAMES[0] if self.sustain else SUSTAIN_NAMES[1]

    @property
    def midi_channel_display(self) -> int:
        """The MIDI channel as the panel prints it, 1..16."""
        return self.midi_channel + 1

    @property
    def midi_program_display(self) -> int:
        """The MIDI program as the panel prints it, 1..128."""
        return self.midi_program + 1

    @property
    def key_low_name(self) -> str:
        """The lowest key of the range, as a note."""
        return note_name(self.key_low)

    @property
    def key_high_name(self) -> str:
        """The highest key of the range, as a note."""
        return note_name(self.key_high)

    @property
    def transpose_octave(self) -> int:
        """The transpose in octaves, -4..+4.

        The byte holds the transpose in semitones, and the panel splits
        it into octaves and semitones.  The split used here is the one
        that is unambiguous: the octave is the number of whole octaves
        in the value, rounded towards zero, and the semitone is what is
        left over -- so the semitone runs -11..+11 and the octave
        -4..+4, which is the pair of ranges the manual gives.
        """
        total = self.transpose
        octave = abs(total) // 12
        if total < 0:
            octave = -octave
        return octave

    @property
    def transpose_semitone(self) -> int:
        """The transpose in semitones, -11..+11, beside the octaves."""
        return self.transpose - 12 * self.transpose_octave


def _name_for(names: Tuple[str, ...], value: int, what: str) -> str:
    """The name of an enumerated value, or a refusal."""
    if not 0 <= value < len(names):
        raise ValueError(f"{what} {value} is outside 0-{len(names) - 1}")
    return names[value]


class EffectParam(NamedTuple):
    """One parameter of an effect, as §5.2 prints it.

    The row shape is the family's (the same shape the sibling rxved
    uses for its effect blocks): an offset, a name, the range the value
    may take, a bias and the names of an enumerated value.

    ``low`` and ``high`` are the **internal** range, the one the
    specification prints in its "Internal" column.  The panel's number
    is derived from it, because the conversion is not always a
    constant: a value stored 0..127 is shown 0..99 through Note 7's
    table, a value stored 0..15 is shown through the same table by
    another route, and a signed value is shown with its sign from the
    sign bit.

    ``bias`` is the family's bias -- the number added to the printed
    value to reach the stored one.  **It is zero on every row here**,
    because the SQ-R's conversions are Note 7's table and not a
    constant offset; the field is where a constant offset would go,
    and it is here because the row shape is the family's.

    ``mask`` and ``shift`` say which bits of the byte the parameter
    occupies.  Several bytes hold two parameters at once -- the
    specification prints them as two rows with the same offset -- and
    they cannot be told apart without knowing the split.
    """

    offset: int
    key: str
    label: str
    low: int
    high: int
    bias: int
    names: Optional[Tuple[str, ...]]
    mask: int = 0xFF
    shift: int = 0

    def internal(self, raw: int) -> int:
        """The value as the specification's "Internal" column gives it."""
        if self.low < 0:
            return raw - 256 if raw & 0x80 else raw
        return raw

    def displayed(self, raw: int) -> int | str:
        """What the panel prints for this parameter.

        An enumerated parameter prints its name; a signed parameter
        prints its magnitude with a sign; everything else prints a
        number, through Note 7's table where the stored and the
        printed ranges differ.
        """
        if self.names is not None:
            if not 0 <= raw < len(self.names):
                raise ValueError(f"{self.key} {raw} is outside 0-{len(self.names) - 1}")
            return self.names[raw]
        internal = self.internal(raw)
        if not self.low <= internal <= self.high:
            raise ValueError(f"{self.key} {internal} is outside {self.low}-{self.high}")
        if self.low < 0:
            magnitude = DISPLAY_TABLE[raw & 0x7F]
            return -magnitude if raw & 0x80 else magnitude
        if self.high == 15:
            return DISPLAY_TABLE[raw * 127 // 15]
        if self.high == 127:
            return DISPLAY_TABLE[raw]
        return raw

    def encode(self, displayed: int | str) -> int:
        """The stored value that prints as ``displayed``."""
        if self.names is not None:
            return self._encode_name(displayed)
        if not isinstance(displayed, int):
            raise ValueError(f"{self.key} takes a number, not {displayed!r}")
        if self.low < 0:
            return self._encode_signed(displayed)
        if self.high == 15:
            return self._encode_fifteen(displayed)
        if self.high == 127:
            return self._encode_table(displayed)
        if not self.low <= displayed <= self.high:
            raise ValueError(
                f"{self.key} {displayed} is outside {self.low}-{self.high}"
            )
        return displayed

    def _encode_name(self, displayed: int | str) -> int:
        """The stored value of an enumerated parameter."""
        names = self.names or ()
        if isinstance(displayed, str):
            try:
                return names.index(displayed)
            except ValueError:
                raise ValueError(
                    f"{self.key} has no value {displayed!r}; it has {', '.join(names)}"
                ) from None
        if not 0 <= displayed < len(names):
            raise ValueError(f"{self.key} {displayed} is outside 0-{len(names) - 1}")
        return displayed

    def _encode_signed(self, displayed: int) -> int:
        """The stored value of a signed parameter."""
        if not -99 <= displayed <= 99:
            raise ValueError(f"{self.key} {displayed} is outside -99-99")
        if displayed < 0:
            return 0x80 | _FIRST_INDEX[-displayed]
        return _FIRST_INDEX[displayed]

    def _encode_fifteen(self, displayed: int) -> int:
        """The stored value of a parameter stored 0..15."""
        try:
            return _FIFTEEN_INDEX[displayed]
        except KeyError:
            raise ValueError(
                f"{self.key} cannot print {displayed}; it is stored 0-15"
            ) from None

    def _encode_table(self, displayed: int) -> int:
        """The stored value of a parameter shown through Note 7's table."""
        if not 0 <= displayed <= 99:
            raise ValueError(f"{self.key} {displayed} is outside 0-99")
        return _FIRST_INDEX[displayed]


def _p(
    offset: int,
    key: str,
    label: str,
    low: int,
    high: int,
    names: Optional[Tuple[str, ...]] = None,
    mask: int = 0xFF,
    shift: int = 0,
) -> EffectParam:
    """One row of a parameter set.  The bias is always zero here; see
    :class:`EffectParam`."""
    return EffectParam(offset, key, label, low, high, 0, names, mask, shift)


#: The six parameter sets of §5.2, by the names the specification gives
#: them, each a tuple of rows.
#:
#: The rows follow the specification's own order and wording.  Two
#: things are worth saying about the shared bytes, because the
#: specification prints them as two rows with one offset and the
#: document does not say which half of the byte each takes:
#:
#: * **The modulation destination takes the high four bits and the
#:   modulation source the low four.**  The specification's mask for
#:   the destination ("XXXx") says so, and the fixture agrees: the
#:   effect byte of every preset in every dump is 0x25 at that offset,
#:   which is destination 2 (FX1-MIX) and source 5 (MODPEDAL) read
#:   that way, and an out-of-range destination 5 read the other way.
#: * **The first row of a shared byte takes the high bits.**  That is
#:   the same rule, and it is what the rotary speaker's rows need:
#:   its modulation source has to be in the low four bits for the
#:   source to be the source everywhere.
PARAM_SETS: Dict[str, Tuple[EffectParam, ...]] = {
    # §5.2.1 Common Reverb -- effects 0 to 3.
    "Common Reverb": (
        _p(0, "decay_time", "DECAY TIME", 0, 127, mask=0x7F),
        _p(1, "low_decay", "LOW FREQUENCY DECAY", -128, 127),
        _p(2, "detune_rate", "DETUNE RATE", 0, 99, mask=0x7F),
        _p(3, "detune_depth", "DETUNE DEPTH", 0, 127, mask=0x7F),
        _p(
            4,
            "mod_destination",
            "MOD DESTINATION",
            0,
            4,
            ("DECAY", "DAMPING", "FX1-MIX", "FX2-MIX", "NOTHING"),
            mask=0xF0,
            shift=4,
        ),
        _p(4, "mod_source", "MOD SOURCE", 0, 15, MIX_SOURCES, mask=0x0F),
        _p(5, "mod_amount", "AMOUNT", -128, 127),
        _p(6, "hf_damping", "HF-DAMPING", 0, 15, mask=0xF0, shift=4),
        _p(6, "hf_bandwidth", "HF-BANDWIDTH", 0, 15, mask=0x0F),
        _p(7, "diffusion", "DIFFUSION", 0, 127, mask=0x7F),
        _p(8, "fx1_mix", "FX1 MIX", 0, 127, mask=0x7F),
        _p(9, "fx2_mix", "FX2 MIX", 0, 127, mask=0x7F),
    ),
    # §5.2.2 MultiVerb -- effects 5 to 7, 9 and 11.
    "MultiVerb": (
        _p(0, "decay_time", "DECAY TIME", 0, 127, mask=0x7F),
        _p(1, "feedback", "FEEDBACK", -128, 127),
        _p(2, "rate", "CHORUS/LFO RATE", 0, 99, mask=0x7F),
        _p(2, "invert", "INPUT INVERT", 0, 1, ("OFF", "ON"), mask=0x80, shift=7),
        _p(3, "depth", "CHORUS/LFO DEPTH", 0, 127, mask=0x7F),
        _p(
            4,
            "mod_destination",
            "MOD DESTINATION",
            0,
            9,
            (
                "DECAY",
                "DAMPING",
                "FX1-MIX",
                "FX2-MIX",
                "RATE",
                "DEPTH",
                "LEVEL",
                "FDBACK",
                "CENTER",
                "NOTHING",
            ),
            mask=0xF0,
            shift=4,
        ),
        _p(4, "mod_source", "MOD SOURCE", 0, 15, MIX_SOURCES, mask=0x0F),
        _p(5, "mod_amount", "AMOUNT", -128, 127),
        _p(6, "damping", "HF-DAMPING", 0, 15, mask=0xF0, shift=4),
        _p(6, "level", "LEVEL/MIX", 0, 15, mask=0x0F),
        _p(7, "center", "CENTER", 0, 127, mask=0x7F),
        _p(8, "fx1_mix", "FX1 MIX", 0, 127, mask=0x7F),
        _p(9, "fx2_mix", "FX2 MIX", 0, 127, mask=0x7F),
    ),
    # §5.2.3 Multi-Voice Chorus -- effect 4.  Byte 0 and byte 6 are
    # unused by the specification and are left alone.
    "Multi-Voice Chorus": (
        _p(1, "feedback", "FEEDBACK", -128, 127),
        _p(2, "rate", "CHORUS RATE", 0, 99, mask=0x7F),
        _p(3, "depth", "CHORUS DEPTH", 0, 127, mask=0x7F),
        _p(
            4,
            "mod_destination",
            "MOD DESTINATION",
            0,
            7,
            (
                "FX1-MIX",
                "FX2-MIX",
                "RATE",
                "DEPTH",
                "LEVEL",
                "FDBACK",
                "CENTER",
                "NOTHING",
            ),
            mask=0xF0,
            shift=4,
        ),
        _p(4, "mod_source", "MOD SOURCE", 0, 15, MIX_SOURCES, mask=0x0F),
        _p(5, "mod_amount", "AMOUNT", -128, 127),
        _p(7, "center", "CHORUS CENTER", 0, 127, mask=0x7F),
        _p(8, "fx1_mix", "FX1 MIX", 0, 127, mask=0x7F),
        _p(9, "fx2_mix", "FX2 MIX", 0, 127, mask=0x7F),
    ),
    # §5.2.4 Phaser -- effect 8.
    "Phaser": (
        _p(0, "cross_feedback", "STEREO CROSS FEEDBACK", -128, 127),
        _p(1, "feedback", "FEEDBACK", -128, 127),
        _p(2, "rate", "PHASER RATE", 0, 99, mask=0x7F),
        _p(2, "invert", "INPUT INVERT", 0, 1, ("OFF", "ON"), mask=0x80, shift=7),
        _p(3, "depth", "PHASER DEPTH", 0, 127, mask=0x7F),
        _p(
            4,
            "mod_destination",
            "MOD DESTINATION",
            0,
            7,
            (
                "FX1-MIX",
                "FX2-MIX",
                "RATE",
                "DEPTH",
                "LEVEL",
                "FDBACK",
                "CENTER",
                "NOTHING",
            ),
            mask=0xF0,
            shift=4,
        ),
        _p(4, "mod_source", "MOD SOURCE", 0, 15, MIX_SOURCES, mask=0x0F),
        _p(5, "mod_amount", "AMOUNT", -128, 127),
        _p(6, "level", "PHASER LEVEL", 0, 15, mask=0x0F),
        _p(7, "center", "PHASER CENTER", 0, 127, mask=0x7F),
        _p(8, "fx1_mix", "FX1 MIX", 0, 127, mask=0x7F),
        _p(9, "fx2_mix", "FX2 MIX", 0, 127, mask=0x7F),
    ),
    # §5.2.5 Rotary Speaker Simulator -- effect 10.  This effect has no
    # modulation destination and no amount: the manual gives it a speed
    # mode and a modulation source instead (§5.2.5, and the manual's
    # "SPEEDMODE" and "MODSRC" screens).
    "Rotary Speaker Simulator": (
        _p(0, "decay_time", "DECAY TIME", 0, 127, mask=0x7F),
        _p(2, "fast_speed", "FAST SPEED", 0, 99, mask=0x7F),
        _p(3, "rotor_depth", "ROTOR DEPTH", 0, 127, mask=0x7F),
        _p(
            4,
            "speed_mode",
            "SPEEDMODE",
            0,
            4,
            ("CONTIN", "SWITCH", "TOGGLE"),
            mask=0xF0,
            shift=4,
        ),
        _p(4, "mod_source", "MODSRC", 0, 15, MIX_SOURCES, mask=0x0F),
        _p(5, "slow_speed", "SLOW SPEED", 0, 99, mask=0x7F),
        _p(6, "hf_damping", "HF-DAMPING", 0, 15, mask=0x0F),
        _p(7, "rotor_center", "ROTOR CENTER", 0, 127, mask=0x7F),
        _p(8, "fx1_mix", "FX1 MIX", 0, 127, mask=0x7F),
        _p(9, "fx2_mix", "FX2 MIX", 0, 127, mask=0x7F),
    ),
    # §5.2.6 Guitar Effects -- effect 12.
    "Guitar Effects": (
        _p(0, "decay_time", "DECAY TIME", 0, 127, mask=0x7F),
        _p(1, "feedback", "FEEDBACK", -128, 127),
        _p(2, "flanger_rate", "FLANGE RATE", 0, 99, mask=0x7F),
        _p(3, "compression", "COMPRESSION", 0, 15, mask=0xF0, shift=4),
        _p(3, "input_level", "INPUT LEVEL", 0, 11, mask=0x0F),
        _p(
            4,
            "mod_destination",
            "MOD DESTINATION",
            0,
            5,
            ("DECAY", "DAMPING", "FX1-MIX", "FX2-MIX", "FDBACK", "NOTHING"),
            mask=0xF0,
            shift=4,
        ),
        _p(4, "mod_source", "MOD SOURCE", 0, 15, MIX_SOURCES, mask=0x0F),
        _p(5, "mod_amount", "AMOUNT", -128, 127),
        _p(6, "hf_damping", "HF-DAMPING", 0, 15, mask=0xF0, shift=4),
        _p(6, "output_level", "OUTPUT LEVEL", 0, 15, mask=0x0F),
        _p(7, "highpass", "HIPASS CUTOFF", 0, 15, mask=0xF0, shift=4),
        _p(7, "lowpass", "LOPASS CUTOFF", 0, 15, mask=0x0F),
        _p(8, "fx1_mix", "FX1 MIX", 0, 127, mask=0x7F),
        _p(9, "fx2_mix", "FX2 MIX", 0, 127, mask=0x7F),
    ),
}

#: Which parameter set each of the thirteen effects uses (§5.1.1).
EFFECT_SETS: Dict[int, str] = {
    0: "Common Reverb",
    1: "Common Reverb",
    2: "Common Reverb",
    3: "Common Reverb",
    4: "Multi-Voice Chorus",
    5: "MultiVerb",
    6: "MultiVerb",
    7: "MultiVerb",
    8: "Phaser",
    9: "MultiVerb",
    10: "Rotary Speaker Simulator",
    11: "MultiVerb",
    12: "Guitar Effects",
}


@dataclass(frozen=True)
class Effect:
    """A preset's effect: ten parameter bytes and the effect number.

    The number says which parameter set the bytes mean, and the
    specification is clear that the two go together: changing the
    number loads a whole new effect preset into the unit, and the
    remaining bytes take their default values.  So the bytes are kept
    as they are and the number is read with them -- a reader that
    guessed the meaning of the bytes without the number would be
    guessing.
    """

    #: Which of the thirteen effects this is, 0..12.
    number: int = 0
    #: The ten parameter bytes, as they are stored.
    params: bytes = b"\x00" * 10

    def __post_init__(self) -> None:
        # The parameter bytes are a fixed length: the effect structure
        # is ten bytes and the number is the eleventh.  A block of the
        # wrong length is a mistake in the caller, not a fact about the
        # instrument.
        if len(self.params) != EFFECT_BYTES - 1:
            raise ValueError(
                f"an effect has {EFFECT_BYTES - 1} parameter bytes, "
                f"not {len(self.params)}"
            )

    @classmethod
    def from_bytes(cls, data: bytes) -> "Effect":
        """An effect from its eleven bytes.

        The effect number lives in the low five bits of the last byte.
        The bits above them are reserved; every dump this project has
        holds zero there, and the model does not pretend to know what
        they mean.
        """
        if len(data) != EFFECT_BYTES:
            raise ValueError(f"an effect is {EFFECT_BYTES} bytes, not {len(data)}")
        return cls(number=data[10] & 0x1F, params=bytes(data[:10]))

    def to_bytes(self) -> bytes:
        """The effect's eleven bytes, as the unit stores them."""
        return self.params + bytes([self.number & 0x1F])

    @property
    def name(self) -> Optional[str]:
        """The effect's name, as the panel prints it, or None if the
        number is one the specification allows but this project has no
        name for."""
        if 0 <= self.number < len(EFFECTS):
            return EFFECTS[self.number]
        return None

    @property
    def parameter_set(self) -> Optional[str]:
        """The name of the parameter set this effect's bytes mean."""
        return EFFECT_SETS.get(self.number)

    @property
    def rows(self) -> Tuple[EffectParam, ...]:
        """The parameter rows for this effect, or nothing if the number
        is one this project has no parameter set for."""
        name = self.parameter_set
        if name is None:
            return ()
        return PARAM_SETS[name]

    def raw(self, row: EffectParam) -> int:
        """The stored bits of one parameter."""
        return (self.params[row.offset] & row.mask) >> row.shift

    def value(self, row: EffectParam) -> int:
        """The internal value of one parameter, as §5.2 prints it."""
        return row.internal(self.raw(row))

    def display(self, row: EffectParam) -> int | str:
        """What the panel prints for one parameter."""
        return row.displayed(self.raw(row))

    def with_value(self, row: EffectParam, displayed: int | str) -> "Effect":
        """A copy of this effect with one parameter set to a printed
        value."""
        raw = row.encode(displayed)
        params = bytearray(self.params)
        params[row.offset] = (params[row.offset] & ~row.mask) | (
            (raw << row.shift) & row.mask
        )
        return Effect(number=self.number, params=bytes(params))


def _exact(data: bytes, size: int, what: str) -> bytes:
    """Bytes of exactly this length, or a refusal.

    The fields the specification does not explain are kept as they
    came, and a field of the wrong length is a mistake in the caller
    rather than something to pad or cut quietly.
    """
    if len(data) != size:
        raise ValueError(f"{what} is {size} bytes, not {len(data)}")
    return data


@dataclass
class Preset:
    """A preset: the 168 bytes of §4.8.2.

    The fields are the unit's own, in the order the bytes come, and
    the ones the specification does not explain are kept verbatim: the
    two edit start points, the size of the sequence and the two
    reserved bytes at the end.

    **A preset read from the unit is written back unchanged.**  The
    name's flag bits (Note 3) are part of the name bytes and are never
    touched, the tracks keep their reserved byte as it stands, and the
    tempo is taken from the dump as it is -- ten empty presets in the
    all-dump hold 0, where the manual says 25..250, and a parser that
    refused them would refuse half the instrument's memory.
    """

    #: The sequence this preset plays, 0..99.
    seqno: int = 0
    #: The sequence flags, 0..99.
    flags: int = 0
    #: The name, sixteen bytes, with bit 7 of each byte carrying the
    #: unit's mute flag rather than part of the name.
    name_bytes: bytes = b"\x00" * 16
    #: The bar length, 0..999.
    bar_length: int = 0
    #: The time signature, sixteen bits.
    time_signature: int = 0
    #: The first edit start point, four bytes, kept as it stands.
    edit_start_1: bytes = b"\x00" * 4
    #: The second edit start point, four bytes, kept as it stands.
    edit_start_2: bytes = b"\x00" * 4
    #: The tempo, 25..250 when it is set by hand.
    tempo: int = 120
    #: The number of song steps, 0..255.
    song_steps: int = 0
    #: The eight tracks.
    tracks: List[Track] = field(default_factory=lambda: [Track() for _ in range(8)])
    #: The track pile: eight bytes saying which tracks are on.
    pile: bytes = b"\x01" + b"\x00" * 7
    #: The preset's effect.
    effect: Effect = field(default_factory=Effect)
    #: The size of the sequence, three bytes, kept as it stands.
    seq_size: bytes = b"\x00\x00\x00"
    #: The two reserved bytes at the end, kept as they stand.
    reserved: bytes = b"\x00\x00"

    def __setattr__(self, name: str, value: object) -> None:
        # The tempo is the one field the unit itself writes out of
        # range, so the check belongs to setting one by hand and not to
        # reading a dump; from_bytes puts the dumped value back
        # afterwards without going through here.
        if name == "tempo":
            if not isinstance(value, int):
                raise TypeError(f"tempo takes a whole number, not {value!r}")
            if not 25 <= value <= 250:
                raise ValueError(f"tempo {value} is outside 25-250")
        object.__setattr__(self, name, value)

    @classmethod
    def from_bytes(cls, data: bytes) -> "Preset":
        """A preset from its 168 bytes.

        Nothing but the length is checked: a dump is a fact about the
        instrument's memory, and the instrument writes values the
        documentation does not promise (a tempo of 0 on ten empty
        presets, a reserved byte of 0x80 in every track).  Those are
        kept, and a value that is set by hand is checked instead.
        """
        if len(data) != PRESET_BYTES:
            raise ValueError(f"a preset is {PRESET_BYTES} bytes, not {len(data)}")
        preset = cls(
            seqno=data[0],
            flags=data[1],
            name_bytes=bytes(data[2:18]),
            bar_length=int.from_bytes(data[18:20], "big"),
            time_signature=int.from_bytes(data[20:22], "big"),
            edit_start_1=bytes(data[22:26]),
            edit_start_2=bytes(data[26:30]),
            song_steps=data[31],
            tracks=[
                Track.from_bytes(
                    data[32 + n * TRACK_BYTES : 32 + (n + 1) * TRACK_BYTES]
                )
                for n in range(8)
            ],
            pile=bytes(data[144:152]),
            effect=Effect.from_bytes(data[152:163]),
            seq_size=bytes(data[163:166]),
            reserved=bytes(data[166:168]),
        )
        # The tempo comes back the way the unit wrote it, checked or
        # not: the check above is for a tempo somebody chooses.
        object.__setattr__(preset, "tempo", data[30])
        return preset

    def to_bytes(self) -> bytes:
        """The preset's 168 bytes, as the unit stores them."""
        if len(self.tracks) != 8:
            raise ValueError(f"a preset has 8 tracks, not {len(self.tracks)}")
        out = bytearray(PRESET_BYTES)
        out[0] = self.seqno & 0xFF
        out[1] = self.flags & 0xFF
        out[2:18] = _exact(self.name_bytes, 16, "a preset name")
        out[18:20] = self.bar_length.to_bytes(2, "big")
        out[20:22] = self.time_signature.to_bytes(2, "big")
        out[22:26] = _exact(self.edit_start_1, 4, "an edit start point")
        out[26:30] = _exact(self.edit_start_2, 4, "an edit start point")
        out[30] = self.tempo & 0xFF
        out[31] = self.song_steps & 0xFF
        for number, track in enumerate(self.tracks):
            out[32 + number * TRACK_BYTES : 32 + (number + 1) * TRACK_BYTES] = (
                track.to_bytes()
            )
        out[144:152] = _exact(self.pile, 8, "a track pile")
        out[152:163] = self.effect.to_bytes()
        out[163:166] = _exact(self.seq_size, 3, "a sequence size")
        out[166:168] = _exact(self.reserved, 2, "the reserved bytes")
        return bytes(out)

    @property
    def name(self) -> str:
        """The preset's name, as the panel prints it.

        Bit 7 of each name byte carries the unit's mute flag and is not
        part of the name, so the name is the bytes with that bit
        cleared.
        """
        return bytes(byte & 0x7F for byte in self.name_bytes).decode("ascii", "replace")

    @name.setter
    def name(self, value: str) -> None:
        """Rename the preset, leaving the unit's flag bits alone.

        The text is written into the low seven bits of each byte and
        the bits above them are kept: the flags are the unit's, not the
        name's, and renaming a preset must not silently clear them.
        """
        encoded = value.encode("ascii", "replace")[:16]
        padded = encoded + b" " * (16 - len(encoded))
        self.name_bytes = bytes(
            (byte & 0x7F) | (old & 0x80) for byte, old in zip(padded, self.name_bytes)
        )

    # --- the pile ------------------------------------------------------------

    @property
    def primary_track(self) -> int:
        """The number of the track the pile names as the primary one.

        Note 6: the pile's first byte holds the primary track's number.
        Every dump this project has holds 1 there, with the other seven
        bytes zero.
        """
        return self.pile[0]

    def is_active(self, number: int) -> bool:
        """Whether the panel would show this track as on.

        The off state lives here and not in the MIDI status, which the
        specification fills with BOTH, LOCAL, MIDI or *EXT* and has no
        off in it: in the dumps the tracks the panel shows as off have
        full parameters and the same LOCAL status as the track that is
        sounding, and the only thing that tells them apart is the pile.

        So a track is on when the pile names it: the primary track by
        the first byte, the layered tracks by the other seven.
        """
        if not 1 <= number <= 8:
            raise ValueError(f"track {number} is outside 1-8")
        if number == self.primary_track:
            return True
        return number in self.pile[1:]

    def set_active(self, number: int, active: bool) -> None:
        """Switch a track on or off, in the pile.

        **The layered tracks are a compact ordered list, not one slot
        per track number.**  The specification calls the pile an
        "ordered list of eight bytes with one byte per track", but the
        unit compacts it on receipt: sending ``01 00 03`` (track 3
        layered, track 2 absent) is read back as ``01 03 00``.  A write
        that leaves a hole therefore fails the read-back check and the
        change appears to be refused -- the track that was switched off
        goes on being shown as on.

        So switching a track off removes its number from the list and
        shifts the ones after it left; switching it on appends to the
        end of the list.  The primary track is named by the pile's
        first byte and stays on -- a preset without a primary track is
        not a state the unit offers.
        """
        if not 1 <= number <= 8:
            raise ValueError(f"track {number} is outside 1-8")
        if number == self.primary_track:
            if not active:
                raise ValueError("the primary track stays on")
            return
        layered = [track for track in self.pile[1:] if track]
        if active:
            if number in layered:
                return
            if len(layered) >= 7:
                raise ValueError("all seven layered tracks are already in the pile")
            layered.append(number)
        else:
            layered = [track for track in layered if track != number]
        self.pile = bytes([self.primary_track] + layered + [0] * (7 - len(layered)))
