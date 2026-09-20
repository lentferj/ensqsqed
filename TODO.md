<!-- SPDX-License-Identifier: GPL-2.0-or-later -->
<!-- SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors -->

# TODO

What is open. *How* anything was resolved goes in
`docs/RESOLUTION_NOTES.md`.

## Status, 2026-09-20 (first session)

The protocol is transcribed from Ensoniq's specification v1.43 and
checked against 116 dump files: header, message types, sizes, nibble
order and the name encoding all agree. Sound names decode. 25 tests, all
synthetic.

**Nothing has been tried against hardware** -- the SQ-R was not patched
into the MIDI bay. Items 1 and 2 are both that.

## 1. The bridge has never talked to the instrument

**Status:** open. `sq/bridge.py` exists and is unexercised.

It was written once the specification turned up, because the dump
requests are documented (section 3.1.2-3.1.6) and transcribing a
documented message is not the same as inventing one. What it needs, in
order, when the SQ-R is patched in:

* `sqcli` against `read_current_sound` -- 415 bytes, the smallest probe
* `read_internal_bank` -- one request, eighty sounds, and if that works
  the browser is essentially done
* `autodetect`, whose sweep over sixteen base channels times every port
  has never been run and may be too slow to be pleasant

**Blocked on:** the SQ-R being patched in.

## 2. The four captured command messages — RESOLVED

**Status:** closed by the specification, 2026-09-20.

They are virtual button presses: message type `00`, command type `00`,
then button 9 down and button 9 up (9 + 96, the documented offset).

That reading was inferred before the specification surfaced and is now
confirmed by its own worked example on page 4, which gives the Left
Arrow -- logical button 25 -- as `... 00 00 00 01 09 F7` down and
`... 07 09 F7` up. `messages.virtual_button` generates both byte for
byte, which is pinned by a test.

**And the inference that went with it was wrong.** Having decoded them as
button presses, this project concluded that the family had no dump
request and that an editor drove the front panel instead. Section 3.1.2
to 3.1.6 documents five of them. Nothing here presses buttons; the
function exists only so the capture is explicable.

## 3. Presets and sequences are not read

**Status:** open, by choice for now.

The unit holds 80 presets (performance setups) and a sequencer. Message
types `04` and `05` carry sequences, under a handshake protocol with ACK
and NAK and a one-second timeout. The manual describes saving presets
over SysEx but no message type for them was seen in any of the 116 files.

ensqsqed browses sounds. Presets would be a second pane; sequences are a
different program.

## 4. The browser has no TUI yet

**Status:** open.

`sqcli` does the reading and the arithmetic. The Textual browser the
siblings have -- two panes, favourites, tags, notes, the F cycle -- is
not written. The shared modules it needs (`favorites`, `config`,
`catalog`) are already ported and working.

**Blocked on:** nothing but the writing. It should wait for item 1, so
that `⏎` has something to send to.

## 5. `tools/extract_catalog.py` is not written

**Status:** open.

A dump decodes its own names, so a catalog is less important here than in
the siblings -- but ROM, Card A and Card B cannot be dumped at all, so
their names have to come from somewhere. The same shape as nanosyned's:
`--syx` for what can be read, `--manual` for what cannot.

## 6. The MidiQuest `.sqs` format is unread — shared across the projects

**Status:** open, and not specific to this instrument.

The SQ-R folder holds `.sqs` files beside the `.syx` ones, as every other
instrument's folder does. They are MidiQuest's own bank format: an
IFF-style chunked file with `LIST`, `FORM`, `BANK`, `CHNL`, `SHDR`,
`FILE`, `TRAN`, `RECV`, `XTRA`, `MPIN`, `MPOT`, `BODY`, `AUDI`, `BDEF`
and `TDEF` chunks, sizes big-endian, contents not the raw SysEx.

Decoding it once would serve rxved, kwsed, p2ked, x5ded, nanosyned and
this one alike.

**Blocked on:** nothing but the decoding.

---

# External code review — GLM-5.3-Flash (2026-09-20)

**Status:** findings only, no code changed. A full read of `sq/`,
`ensqsqed/`, `tests/`, `docs/` and the project metadata; the test suite was
run (33 tests, all passing). Everything below was verified at its site
first-hand. What the TODO already tracks — the bridge never having met the
instrument (item 1), the unwritten catalog tool (item 5), the unread `.sqs`
format (item 6) — is not repeated as a finding. Where a finding is the same
porting artifact this project shares with emorphed, the emorphed TODO's
external-review section (same date) documents the corrected version; the
sibling's cleanup can be copied rather than re-derived.

*Every finding below was re-verified at its site before being acted on.
Eight of the nine held. **ER-4 does not: the manual answers it, and the
code was already right.***

## ER-1 — `catalog.py` was the raw Korg-era port — ALREADY FIXED

**Confirmed, and fixed the day before this review was read**, as part of
acting on emorphed's. All three projects that came off the same `sed`
were rewritten then. The review's diagnosis was exactly right, including
that this instrument's version needed a *different* argument from
emorphed's: there the unit hands over every name in one exchange, here
only the internal 80 can be read at all.

## ER-2 — the bridge was unreachable from the CLI — FIXED

**Confirmed.** `sqcli` had no `--port`, constructed no device and had no
online command, so item 1's plan -- "`sqcli` against
`read_current_sound`" -- was not executable as written.

The CLI now takes `--port`, `--recv-port`, `--channel` and `--config`,
remembers what answered, tries the remembered port before sweeping, and
has two online commands: `status`, which reads the sound the unit is on
and so proves the link with the smallest possible message, and `names`,
which reads all eighty internal names.

## ER-3 — `config` unused, `favorites` untested and unimported — FIXED

**Confirmed.** `config` was imported and never called; `favorites` (601
lines) was imported by nothing.

`config` is now used by the wiring above. `favorites` is covered by
`tests/test_store.py`, which is the suite written for emorphed after the
same finding there -- the upsert preserving `added`, the soft delete and
v1 migration, `toggle`'s restore, `_merge`, LIKE escaping,
`refresh_names`, TOML quoting and the catalog's disagreement reporting.
The docstrings promising "``--favorites`` on both front ends" and
describing a browser are corrected.

## ER-4 — `--with-effect` composes before the group selector — REJECTED

**The code is right and the manual says so explicitly.** The review
raises a fair question -- program change 124 means "the *next* program
change brings its effect", so a selector in between looks as though it
would swallow the flag -- and then the manual answers it, in the same
section that introduces 124:

> Dieser spezielle Programmwechsel 124 hat keinen Einfluss darauf, wie
> andere Programmwechsel verstanden werden. Wird einer der
> "Steuer"-Programmwechsel fuer den gleichen Sound gebraucht, senden Sie
> diesen **zwischen dem 124er und dem eigentlichen Programmwechsel**.

Send the selector *between* the 124 and the real program change -- which
is `[124, selector, sound]`, the order `select_messages` already emits.
No bench step needed. The citation is now at `select_messages`, which is
where the question arises.

## ER-5 — SMF SysEx continuation events were not handled — FIXED

**Confirmed, and it was the `.mid` reader's headline feature that it
broke.** A Standard MIDI File may split a long SysEx across a continuing
`F7` event, and an all-sounds dump is 32647 bytes -- exactly the size
sequencers split. Unjoined, the `F0` fragment failed its length check and
counted as unknown while the `F7` fragments did not parse at all, so a
good bank dump read as noise.

`sysex_from_smf` now joins them, and leaves alone an `F7` event whose
first byte is a status byte, which is an escaped raw event rather than a
continuation. The files in the collection still read exactly as before.

## ER-6 — the SPDX header said the specification does not exist — FIXED

**Confirmed, and it was the one false claim in the file.** The header
predated finding the specification; the docstring, item 2 and
RESOLUTION_NOTES §1 all said the opposite. It now credits v1.43 and the
116 dump files, which is what a licensee reads first.

## ER-7 — the test count had drifted — FIXED

25 in the text, 33 in the repository. Now 55, and recounted.

## ER-8 — `frame()` validated the wrong range — FIXED

**Confirmed**: `0-0x7F` where every docstring and the bridge's sweep say
the byte is a base MIDI channel, 0-15. Now 0-15, with the reason.

The second half is fixed the other way round, as the review suggested:
`parse` skips the byte at index 3 rather than carrying it, so the comment
now says the byte is the SQ-1 Model ID and that it is skipped -- because
describing it as "carried" was the inaccuracy.

## ER-9 — minor — ALL FIXED

* `by_selector` is exported; `sqcli reach` calls it.
* `likely_ports` now also matches sq2/sq-2/ks32, which the specification
  says speak the same header.
* `--channel` has help text and a 1-16 check, in both its senses -- the
  global one and `reach`'s -- rather than relying on `select_messages`
  raising, which read as an accident.

**One thing the review did not raise, found while fixing ER-2:**
`autodetect` sweeps sixteen base channels across every bidirectional
port. On this rig that is about five minutes before it gives up. It is
documented at the point of use now, with the arithmetic, and `--port`
plus `--channel` is the fast path the CLI remembers for next time.

## What was checked and found sound

For the record, since this is the part a future reviewer would redo: the
program-change model (selectors 124-127, kits 80-99, drum `bank` 8-9,
`lookup` refusing to assume a group) is the clearest statement of this
instrument's addressing in the collection, and `Slot.number` vs
`program_change` are kept apart exactly where emorphed's equivalents were
not; `read_file` deciding `.syx` vs `.mid` by content (`MThd`) rather than
extension is right and matches the collection's actual messiness;
`request()` raising `DeviceRefused` on a NAK — diagnosis most of the
siblings' protocols do not offer — is the right escalation; `size_is_right`
as the only structural check is honest about a format with no checksum;
`BUTTON_GAP = 0.150` existing unused-with-a-reason is the right way to
record a number nobody currently needs; and `from_nibbles_low_first`
surviving as a named, test-compared wrong answer is exactly how a
two-hour debugging session should be preserved. The `main()` catch list
includes `OSError`, which `morphcli read` lacks — worth copying back
across when the CLI grows its first hardware command.
