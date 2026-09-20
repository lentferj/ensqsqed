<!-- SPDX-License-Identifier: GPL-2.0-or-later -->
<!-- SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors -->

# Resolution notes

How each thing was established. `TODO.md` says what is open.

## 1. The specification exists, and very nearly did not

The SQ-R's owner's manual does not document System Exclusive. It says
where to get the document instead: write to Jacques Isler GmbH in
Rödermark, or Salzburg, or Zürich, enclose your full name and address,
and allow two to three weeks.

That document is not on Rainer Buchty's Ensoniq archive, which has the
ESQ-1, SQ-80 and TS-series specifications. It is not on sysexdb, whose
SQ-1/SQ-2/SQ-R page is a file repository with user-assigned category
labels — one file labelled "Data Dump Request" is described by its own
uploader as a sounds bank.

It turned up in the owner's own files, as Appendix A of the **SQ-1
Musician's Manual**: *MIDI System Exclusive Implementation Specification
version 1.43*, eighteen scanned pages. Everything in `sq/messages.py`
comes from it.

**The moral, for the next instrument:** four hours of inference produced
a model that was right about the sizes and wrong about the byte order.
The document was on the disk the whole time under a name that did not
say so.

## 2. What was inferred first, and what it cost

Before the specification appeared, this was derived from 116 dump files:

* the header `F0 0F 06 00 <device> <opcode>` — **right**
* the all-sounds dump is opcode `03`, 32640 bytes = 80 × 408 — **right**
* a single sound is opcode `02`, 408 bytes — **right**, and found only
  because a `.mid` held a message its matching `.syx` did not
* the device byte is the base MIDI channel — **right** (dumps carry
  0x00, 0x0A and 0x0D)
* 408 wire bytes is 204 decoded — **right**
* **the nibble order is low-first** — **wrong**

The last one is the interesting one. Decoding low-first yields 204 bytes
per sound, every value in range, no error anywhere, and names that are
noise. Two hours went into looking for a name in that noise: ASCII at
every byte offset, seven-bit fields at every bit offset, and a
repetition-pattern match against 118 SoundDiver entries whose ASCII
labels turned out to be SoundDiver's own rather than the instrument's.

The specification settles it in one line — *"H = Hi 4 bits of data byte
- transmitted first"* — and the names fall out immediately.

`from_nibbles_low_first` is kept, named, and tested, precisely so that
the wrong order has a name and a test that says it is silent rather than
fatal.

## 3. The name encoding

Specification Note 3: *"Sound names are sixteen ASCII characters long,
but are packed into 14 consecutive bytes in the sound structure."*

Sixteen sevens are 112 bits and fourteen eights are 112 bits, so there is
no padding. Section 4.2 puts the name at **offset 177** of the 204-byte
structure. Most significant bit first.

Checked over a real bank: 80 of 80 sounds decode to clean ASCII, centred
with spaces the way the instrument centres them on its display. The
padding is returned intact — stripping it here would be this program's
opinion rather than the instrument's.

## 4. The four mystery messages

Four 11-byte messages at the head of one capture:

```
F0 0F 06 00 0A 00 00 00 00 09 F7
F0 0F 06 00 0A 00 00 00 06 09 F7
```

Message type `00` is **Command Messages**, and command type `00` is
**Virtual Buttons**: the specification lets an external program simulate
front-panel button presses, with button *up* being the button number plus
96 (60h).

Read as nibble pairs, high first: command type `00`, then `09` — button
9, "unnamed bank 9" — and then `69` hex, 105, which is 9 + 96. A press
and a release. Somebody's editor pressing the bank 9 button, twice.

This is why the family has no obvious "dump request": an editor drives
the front panel instead.

Nothing in this program sends it.

## 5. A program change is not a program change

The manual, part 3: the last four program changes select which *group*
subsequent program changes address.

| displayed | on the wire | meaning |
|---|---|---|
| 125 | 124 | Internal |
| 126 | 125 | ROM |
| 127 | 126 | Card A |
| 128 | 127 | Card B |
| 124 | 123 | next program change brings its effect (MULTI only) |

and program changes 80–99 always reach the 20 drum kits, whatever group
is in force. The specification's Note 5 confirms the same four codes from
the other direction: an internal program number is a 16-bit word whose
top two bits are a *bank select code* with 0 = Internal, 1 = Permanent
ROM, 2 = Card A, 3 = Card B.

Two documents, two directions, same four groups. That is why
`banks.select_messages` always sends the selector first: the unit's group
is a mode somebody else may have changed.

## 6. The manual contradicts the arithmetic once

The manual says internal sounds "haben die Nummern 001 bis 079" — 79
sounds. Everything else says 80: eight banks of ten, "Die Soundgruppen
enthalten immer 80 Sounds", "Das sind 80 Sounds" for the dump, 340 total,
and the dump itself is 80 × 408 bytes. The specification agrees: "All 80
sounds in the first 8 internal RAM program banks".

So 001–080, and "079" is a typo. `banks.py` uses 80 and this note is why.

## 7. Rules carried in from the sibling projects

Arrived with the ported code and did not have to be rediscovered: the
favourites store is SQLite and refuses cross-thread use; TOML values are
escaped so a port name with a quote cannot poison the config; a bad
catalog row is skipped rather than fatal; `config.toml` is disposable and
gitignored while the favourites store is the user's own work; and the
manufacturer's names are never committed.
