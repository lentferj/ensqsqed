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

## 8. Dump requests exist after all, and the wrong conclusion that preceded them

This project first concluded that the SQ family had no dump request: the
owner's manual documents transfers only as something started from the
front panel, and the four unexplained messages in a capture decoded as
virtual button presses. From those two facts it inferred that an editor
drove the panel remotely because there was nothing else to drive.

The inference was wrong, and it was wrong in the most ordinary way: two
true observations, one plausible story, no check.

Specification section 3.1.2 to 3.1.6 documents five dump requests, as
command types 01 to 05 of a message-type-00 command message:

| command type | request |
|---|---|
| 01 | Single Sound Dump Request |
| 02 | Internal Sound Banks Dump Request |
| 03 | Single Sequence Dump Request |
| 04 | All Sequence Dump Request |
| 05 | Dump Everything Request |

with the note that "the command type is the only byte in these commands"
and — easy to miss and fatal to miss — that it "is considered data and
must be nybble-ized". Sent as a plain byte, command type 02 would arrive
as command type 0, which is virtual buttons, with a stray argument.

So `internal_banks_request()` is nine bytes:
``F0 0F 06 00 cc 00 00 02 F7``, and it returns all eighty internal
sounds in one 32647-byte reply. `sq/bridge.py` was written once this was
known.

## 9. The one check available without the instrument

Page 4 prints a worked example — the Left Arrow, logical button 25,
pressed and released:

```
F0 0F 06 00 00  00  00 00  01 09  F7    button down
F0 0F 06 00 00  00  00 00  07 09  F7    button up
```

`messages.virtual_button(25)` and `virtual_button(25, down=False)`
generate exactly those bytes. That single comparison exercises the
header, the nibble order (25 is 0x19, which is `01 09` high nibble
first) and the button-up offset of 96 all at once. If any of the three
were wrong it would not match.

It is pinned by a test, and it is the closest thing to hardware
verification this project has.

## 10. The firmware, and a negative result worth recording

The v2.03 ROM pair was examined to see whether it could corroborate the
protocol independently.

**What it established:** the two files interleave into a valid 68000
image with the upper ROM supplying the high byte of each word. The
vector table is well formed — initial SSP `0x00000000`, initial PC
`0x00C10102`, then a run of handlers at `0x00C1065C` and every four
bytes after it — which places the ROM near `0x00C10000` in the address
map. The ROMs are unequal in size (64 kB and 128 kB), so only the first
64 kB interleaves as code and the remainder of the upper ROM is
something else.

**What it did not establish:** anything about System Exclusive. Searching
for the header bytes finds matches only inside what is plainly sound
parameter data. Searching for word-aligned `CMPI.B` against `0x0F`,
`0x06`, `0xF0` and `0xF7` yields a handful of candidates that cannot be
told from data without disassembling at the correct base address, and
the start of the image is data rather than code, so file offsets do not
map simply onto the addresses the vector table points at.

Verifying the parser properly is a disassembly project. It was not
undertaken, because the protocol already agrees byte for byte with the
manufacturer's own worked example and the only thing firmware analysis
could add is a second opinion on a settled question. The ROM layout is
recorded here so that anyone who does take it up starts further along.

## 11. MULTI mode (presets) & FX — how it was established (2026-10-21)

The SQ-R's presets and their effect had not been read before this. This
section records how the framing was measured, what the parsers now rely on,
and what remains unverified. Everything here was probed on one real SQ-R
Plus, base channel 11, on 2026-10-20. The byte-accurate contract the
parsers implement is `docs/PRESET-FRAMING.md`; that document is the
authority, and this one adds the *why* and the probes behind it.

**Probe chronology.** Three read-only probes, in order, established the read
path.

1. **T1 — the all-dump layout (offline).** `tests/fixtures/all_seq.bin`
   (17977 bytes) was decoded without hardware and mapped field by field;
   `docs/PRESET-FRAMING.md` is the result. It confirms the one-line rule the
   parsers use: `preset_area_start = len(dump) − 21 − 168·count`, where
   `count` is the last byte of the alert that precedes the dump. For this
   fixture that is `17977 − 21 − 168·80 = 4516`. A second fixture,
   `tests/fixtures/single_seq_default.bin` (216 bytes), pins the single-preset
   layout: a 48-byte sequence-data blob followed by the 168-byte preset.
2. **T2 — the edit-buffer semantics (hardware).**
   `tools/probe_preset_write.py` read the current preset, sent it back
   unchanged, changed one byte, sent again, and restored. Its run log is
   `/tmp/opencode/sqrex/probe_write.log`, its snapshot
   `/tmp/opencode/sqrex/probe_snapshot_before.bin`. It established the write
   handshake and what a read-back returns (below). Its findings are written
   into `docs/PRESET-FRAMING.md` §9.
3. **T12 — live end-to-end** was planned but had not run when this section
   was written; `/tmp/opencode/sqrex/e2e.log` does not exist yet. It is the
   one remaining confirmation, and it is not a blocker for the read path,
   which the two fixtures already pin.

**The alert that precedes a dump carries a size and, for the all-dump, a
count.** For the all-sequence dump the alert is command 07 and its body
`00 00 10 D4 50` means size 4308 and count 80. The "size" is not the raw
sequence payload (3900 bytes) but the offset at which the track-data region
ends — the specification's "size of the sequence data segment" includes the
408-byte header. The parser must not use the size to find the preset area;
it uses the count only. For the single-sequence alert (command 06, body
`00 00 00 30 00`) the size is 48 — the sequence-data segment only, not the
168-byte preset that follows it in the type-04 message. Both are measured,
not read.

**The track-data table's constant C is 0.** Section 4.7 of the
specification says its entries are "based on the beginning of track data
minus 8", which reads like a non-zero base. The fixture resolves it: entry
value + 0 = absolute file offset, and all 80 non-zero entries land on a
plausible self-describing blob (70 × 48 B, 10 × 54 B). Bases 8, 400, 408
and 416 give no valid blobs. The parser uses C = 0.

**A track's on/off state lives in the pile, not the MIDI status byte.** The
specification's track structure has no off state at all — byte 8's status
field says BOTH, LOCAL, MIDI or *EXT*. The manual's panel shows a track as
`—` when it is off. In all 80 presets, tracks 2–8 carry full parameter data
(sound, volume, channel, …) **and the same status byte as the sounding track
1** — LOCAL (1) — so the status byte cannot be where OFF lives. The pile is:
track 1's pile byte is 1, tracks 2–8's are 0, and 0 is the panel's `—`. The
parsers therefore read and write the on/off state through the pile, and the
status value *EXT* (3) is left alone — no dump exercises it.

**The layered entries are a compact list, not fixed slots.** Confirmed on the
bench (2026-10-21): a preset sent with pile `01 00 03` reads back as `01 03 00`,
so the unit packs the layered track numbers to the left. Writing a hole makes
the read-back differ and the write is refused, which is the "track on works,
track off does not" report: turning off a track below another layered one left
a hole. `Preset.set_active` removes the number and shifts the rest left
(and appends when turning on); a hole read from a third-party dump is compacted
by the next write. The name bytes' bit 7 is not a writable per-track mute —
the unit restores those bits on receipt.

**The pan rule is stated by the model, not settled by the documents.** Note 7
says a value stored 0–127 and shown 0–99 is converted through a 128-entry
table, and that for a negative internal value the sign bit is ignored to get
the table index. That makes the pan byte a sign-and-magnitude pair, with
0x80 — a "-0" the panel has no use for — reserved for the word SOUND. So
0x80 is SOUND, any other top-bit byte is a left pan (0x81 = −1, 0xFF = −99),
and a top-bit-clear byte is a right pan (0x00 = 0, 0x7F = +99). The manual
prints the panel's range as −98..+99 while the table reaches −99 at the hard
left; no dump this project has holds anything but SOUND, so the difference
of one is at an end nothing exercises. `pan_display` / `pan_stored` state
the rule.

**The modulation destination/source nibble split was settled by the
fixture.** The specification prints the destination and source as two rows
at one offset and does not say which half of the byte each takes. Its mask
for the destination ("XXXx") says high bits, and the fixture agrees: the
effect byte at that offset is `0x25` in every preset, which read high-first
is destination 2 (FX1-MIX) and source 5 (MODPEDAL), and read the other way is
an out-of-range destination 5. The destination takes the high four bits and
the source the low four; the first row of any shared byte takes the high
bits.

**Write path and Edit-Preset semantics (T2).** A preset is received as the
specification's two-message handshake: the alert (command 06, size = the
sequence-data length only, flag 0 = sequence), then the ACK, then the
type-04 dump of `[sequence data][168-byte preset]`. The unit answers the
alert with an ACK in about **eleven milliseconds** every time (nine
handshakes over three runs), well inside the specification's one-second
window, and never with a NAK or silence. After the send, command 03 returns
the **Edit Preset**, not the stored slot: a one-byte change (track 1 volume
127→126) was visible in the read-back, and the restore returned the original
bytes byte for byte. So apply-then-verify is meaningful, but **the
verification does not mean the preset is stored** — the unit is holding it in
a buffer and the display says "Press ENTER to save new preset". Storing is a
front-panel act; there is no message that does it, and the app says so. A
read before any write is the stored preset; a read after one is the Edit
Preset.

**MULTI mode is a System-bank setting on the unit.** The SQ-R has no SysEx
write for system parameters, so MIDI Mode=MULTI cannot be set from here;
the screens say so. The manual's Part 3 gives the receive rule: each track
that is on receives on its own MIDI channel; never put a track on the base
channel while Preset Prog is ON; one track per channel, and the lowest track
number wins a channel.

**Open questions.**

- The 21-byte trailing global block is decoded only as bytes; its field
  layout is undocumented, and the single fixture's values are not enough to
  map it. It is preserved verbatim.
- MIDI status *EXT* (3) is spec-defined but unverified by any dump.
- The ten 54-byte sequence blobs carry ten trailing bytes (`00 00 00 30 00
  00 00 06 80 e9`) identical in all ten; their meaning is unknown.
- No dump exercises BOTH/MIDI status, NONE/CHANNEL pressure, a layered pile
  beyond one primary track, or any pan other than SOUND.
- Storing a preset to a memory slot still needs the unit's ENTER; driving the
  front panel via virtual buttons is deliberately out of scope.
