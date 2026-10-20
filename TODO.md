<!-- SPDX-License-Identifier: GPL-2.0-or-later -->
<!-- SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors -->

# TODO

What is open. *How* anything was resolved goes in
`docs/RESOLUTION_NOTES.md`.

## Status, 2026-10-21 (MULTI mode & presets)

The MULTI-mode feature landed on `feature/multi-and-fx`: the unit's 80
presets are read, browsed and edited (eight tracks and the one shared
effect), and written back to the unit's **Edit Preset** buffer behind the
write gate. Item 3 is closed by it. What remains is in that item's tail:
storing a preset to a memory slot still needs the unit's ENTER, because no
SysEx message stores one. `docs/RESOLUTION_NOTES.md` §11 records how the
framing was measured.

## Status, 2026-09-20 (first session)

The protocol is transcribed from Ensoniq's specification v1.43 and
checked against 116 dump files: header, message types, sizes, nibble
order and the name encoding all agree. Sound names decode. 25 tests, all
synthetic.

**Nothing has been tried against hardware** -- the SQ-R was not patched
into the MIDI bay. Items 1 and 2 are both that.

## 1. The bridge has never talked to the instrument — RESOLVED 2026-10-20

**Status:** closed. The bridge has now talked to a real SQ-R.

On 2026-10-20, on base channel 11, the following were confirmed against
hardware:

* `read_current_sound` — a Single Sound Dump Request returned the
  documented 415 bytes (204-byte payload once de-nibbled), and the name
  decoded (` Dynamic Grand`).
* `read_internal_bank` — an Internal Sound Banks Dump Request returned all
  eighty sounds with their names in one reply.
* `autodetect` — found the unit, after the fix below.

**The finding that mattered was the cabling, not the protocol.** The unit
was connected *crosswise* on an ESI M4U eX: its MIDI IN on the interface's
output 2, its MIDI OUT on input 1. The old sweep paired each port with
itself (`recv_port=port`), so it could never hear the reply. `autodetect`
now groups ports by ALSA client and listens on every input of an interface
while each of its outputs sends, so a crosswise pair is discovered instead
of assumed. Opening *all* inputs at once exhausts ALSA sequencer clients on
a large rig ("Cannot allocate memory"), which is why the sweep is per
interface rather than global.

`--device-channel` is also honoured now: it was parsed but ignored, so
`ensqsqed --device-channel 11` swept from channel 1. It is the channel the
unit's SysEx device byte uses, and on this single-timbral instrument that
is the same number as the send channel.

What remains open: a sound-write path. Ensoniq's specification v1.43
documents only dump *requests* (commands 01-05) — nothing that stores a
sound — so `tests/test_hardware.py` still skips its sound-write test with
that reason. A preset write path exists now (RESOLUTION_NOTES §11): it
sends a whole preset to the unit's **Edit Preset** buffer, and stores
nothing without the unit's ENTER.


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

## 3. Presets and sequences are not read — RESOLVED 2026-10-21

**Status:** closed. Presets are read and editable now; the framing is in
`docs/RESOLUTION_NOTES.md` §11 and the byte map in
`docs/PRESET-FRAMING.md`.

The unit holds 80 presets (performance setups) and a sequencer. Message
types `04` and `05` carry sequences, under a handshake protocol with ACK
and NAK and a one-second timeout. The manual describes saving presets over
SysEx but no message type for them was seen in any of the 116 files. That
was the gap: there is a message type, and it is 04/05, and the all-dump
layout is measured now.

`read_current_preset` / `read_all_presets` read them; `sqcli multi`, and
`m` / `x` / `p` in the browser, edit the eight tracks and the effect;
`sqcli send` writes an edited preset back. Two fixtures, captured on the
unit, pin the layout.

**What remains open: storing to a memory slot without the front panel.**
No SysEx message stores a preset; the unit holds the edit in its Edit Preset
buffer until somebody presses ENTER, and driving the front panel via virtual
buttons is deliberately out of scope. The 21-byte global block, the *EXT*
MIDI status and the 54-byte blobs' trailing bytes are also undecoded
(RESOLUTION_NOTES §11). Sequences themselves are still a different program.

## 4. The browser — DONE 2026-09-20

Two panes, the sibling shape: groups left, that group's sounds right.
Favourites with tags and notes, the three-way `F` cycle, search, the
channel keys, and a device screen that reports what the unit is playing,
since this instrument answers no identity request and has no version
message.

What is specific to this instrument, and is the reason it is worth
having: the slot pane shows **both** numbers -- the 001-080 the unit
displays and the 000-079 MIDI sends -- the detail line spells out the
group selector and the sound as two separate program changes, and `r`
refuses ROM, Card A, Card B and the drum kits before sending anything,
because none of them has a dump request at all.

`ensqsqed/demo.py` is new with it, and `messages.encode_name` -- the
counterpart to `sound_name`, which did not exist because the tests had
been packing bits by hand.

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

## ER-F1 — `load_channel` had the same bug as `load_device_id` — FIXED

**Confirmed, reproduced on the wire, and fixed in all seven projects.**

The review is right on every point, including the one that makes it
worth more than a type check. Reproduced here:

```
load_channel("channel = true") -> True
select_messages(slot, True)    -> 0xC1 0xC1
                               => MIDI channel 2, asked for 1
```

`isinstance(True, int)` is True and `0 <= True <= 15` is true, so the
value passed every downstream check and came out as a status byte for
the wrong channel. A wrong channel is not an error anywhere: the
instrument plays nothing, or something else does.

**rxved's was worse than reported.** Its loader lives in the bridge and
had `isinstance(value, int)` with **no range check at all**, so
`channel = 99` also passed and produced a status byte belonging to a
different MIDI message entirely. It now has both guards.

The review's diagnosis of *why* it happened is the useful part: the
device-ID guard was swept across six projects and the function one line
below it was not looked at. So the fix is not six more one-line guards
but a test that walks **every** loader whose return type mentions `int`
and feeds each one a boolean and an absurd number:

```
tests: TestABoolIsNotANumber
  test_there_are_numeric_loaders_to_check   (not vacuous)
  test_no_numeric_loader_accepts_true
  test_no_numeric_loader_accepts_an_absurd_value
```

It is in all seven projects, and reverting one guard was checked to make
it fail. The next loader that grows this shape gets caught without
anybody remembering to sweep.

`load_last_port` and `load_last_recv_port` were checked too and are
sound -- `bool` is not a `str`, so `isinstance(value, str)` already
excludes it. The two were made consistent about rejecting an empty
string while there.

