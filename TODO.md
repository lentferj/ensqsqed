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

## ER-1 — `catalog.py` is the raw Korg-era port, unadapted

**Status:** open, documentation, but the worst kind: it reads as authority.

The module docstring is the pre-rewrite sibling text, verbatim, and none of
it fits this instrument:

* Lines 21-22: printed names come from "the **X5 family**" instrument
  definition files, generated into "``p2k/data/catalog.json``" — two other
  projects' names, and this project's actual path is
  ``sq/data/catalog.json``.
* Line 30: "An SQ-R will not name one program on request; it dumps all
  **hundred** at once." Wrong twice over: there are **340** sounds in 80s,
  and the protocol has **no name-list message at all** — the only names
  SysEx can deliver are the internal bank's 80, in a 32,647-byte dump. For
  ROM, Card A and Card B the unit can supply nothing, which is why item 5
  exists. The whole "working from the unit is cheap" argument the
  (since-rewritten) emorphed version makes does not hold here.
* Lines 114-119: "Reading all **640** presets of one ROM off a unit on
  **2026-09-12** and comparing them with **Korg's own patch list**" — an
  event from p2ked: wrong count, wrong manufacturer, and a date that
  predates this repo's first session. And for this instrument the example
  is impossible: ROM cannot be read over SysEx at all.
* "see :meth:`disagreement` and **ensqsqed/app.py**" — no `app.py` exists
  (item 4).
* The printed-vs-live disagreement machinery (writable → "saved over the
  factory preset", else → "the printed list is wrong") is meaningful only
  for **INT** — the sole group that is both readable and writable here.
  Every other group can have at most a printed name.

Emorphed's `emu/catalog.py` now carries the corrected shape (it says
explicitly that nothing in its project has yet compared the two sources);
that rewrite is the template for fixing this one.

## ER-2 — the bridge is unreachable from the CLI

**Status:** open, and it changes what item 1's first bench step will be.

TODO item 1 says the first probe is "`sqcli` against `read_current_sound`"
— but no `sqcli` command touches the bridge. The parser takes no `--port`,
there is no `names`/`status`/`reach-current` command, and `main` constructs
no device: every command is offline (`groups`, `list`, `reach`, `read`).
Emorphed's CLI, written the same day, wires its bridge with `--port`,
`--recv-port`, `--device-id`, `autodetect`, and `names`/`status`/`select`
commands; ensqsqed's dropped all of it, and `README.md`'s "Running it"
reflects that — so item 1's plan is not executable as written. Either wire
the bridge in (the emorphed CLI is the working shape) or amend item 1 to
say the wiring is part of it.

## ER-3 — `config` is imported but never used; `favorites` is imported by nothing

**Status:** open.

`ensqsqed/cli.py` imports `sq.config` and never calls it — no channel or
port is loaded or saved, so the entire module (162 lines, with the
carefully-reasoned `_toml_string` escaping) is dead in this repo right now.
`sq/favorites.py` (601 lines, the largest module) is not imported by
anything at all and has no tests: `tests/test_banks.py` covers `banks`,
`dumps` and `messages` only. Both are ported ahead of the TUI (item 4),
which is a legitimate decision — but it should be *recorded* as such
somewhere cheaper than a reviewer finding it, and neither module's
docstrings say their front end does not exist yet. `favorites.py` still
promises "``--favorites`` on both front ends" and describes the browser
redrawing 128-line banks.

## ER-4 — `--with-effect` composes before the group selector

**Status:** open, a semantics question for the bench, not necessarily a
bug.

`banks.select_messages` sends `[PC 123, selector PC, sound PC]` when a
target in another group is reached with `with_effect=True`. But PC 123
means "the **next** program change brings its effect" — and the next
program change after 123 is the *selector* (124-127), not the sound. Two
readings are possible: the effect flag is consumed harmlessly by the
group-selector PC and never reaches the sound, or group-selector program
changes are not "sound" program changes and the flag carries to the sound
after. The code does not say which it believes, and `sqcli reach
--with-effect` prints the sequence with the effect first without flagging
the question. The alternative order — selector first, then 123, then the
sound — is equally plausible. Settle it from the manual's MULTI-mode
section or at the bench (item 1), and note the answer at
`select_messages`.

## ER-5 — SMF SysEx continuation events are not handled

**Status:** open, a limitation of the `.mid` reader's headline feature.

`dumps.sysex_from_smf` walks `MTrk` chunks and reads every
`F0 <varlen> …` event — but Standard MIDI File also carries *continued*
System Exclusive as `F7 <varlen> …` events, and 32,647-byte dumps are
exactly the size sequencers split that way. A continuation-split
all-sounds dump would arrive as fragments: the `F0` part fails
`size_is_right` and counts as `unknown`, the `F7` fragments parse as
`None` and count as `foreign`, and a real bank dump is silently
misread as noise. The docstring's "walks the tracks properly" overclaims.
The reader's own rationale ("a `.mid` in this collection held a message
its `.syx` did not") is the argument for closing this: the interesting
messages are precisely the ones that arrived via sequencer, and those are
the ones that get split. The repair is the usual one: treat an `F7` event
whose payload does not start with a status byte as a continuation of the
previous `F0` event.

## ER-6 — `messages.py`'s SPDX header contradicts the module docstring

**Status:** open, documentation.

The header block still says "Ensoniq's own MIDI specification for this
family was a printed document supplied on request and **is not
available**". The module docstring, TODO item 2 and RESOLUTION_NOTES §1
all say the opposite: the spec (v1.43, Appendix A of the SQ-1 Musician's
Manual) was found, used, and settled the nibble order. The header predates
the find. Fixing it matters because the header is what a licensee or a
future reader sees first — and "there is no specification" is now the one
claim in the file that is false.

## ER-7 — the TODO's test count has drifted

**Status:** open, trivial.

The status line says "25 tests, all synthetic"; there are 33, all passing.
(Morphcli's sibling had the same drift in the other direction.) Cheap to
recount at the next edit.

## ER-8 — `frame()` validates the wrong range for the device byte

**Status:** open, small.

Every docstring establishes the fifth-byte-as-base-channel as 0-15
("dumps from three instruments carry 0x00, 0x0A and 0x0D"), and the bridge
sweeps exactly 0-15 — but `messages.frame` accepts `0-0x7F`. Emorphed's
equivalent enforces 0-15. Also, `parse()` does not check the unknown byte
at index 3: the docstring says it "is carried rather than interpreted",
but the code neither carries nor validates it — it just skips it. If the
byte is ever shown to matter, both ends need to change; until then the
docstring should say "ignored", which is what the code does.

## ER-9 — minor

* `banks.by_selector` is missing from `__all__` while `sqcli reach` calls
  it. Same class as emorphed's `signed` omission.
* `likely_ports` matches "sq1"/"sq-1" but not "sq2"/"sq-2"/"ks32" — the
  SQ-2 and KS-32 speak the same header per the specification, and would be
  found anyway by the sweep; the hint list is just incomplete.
* `--channel` on `reach` has no help text and no 1-16 validation — an
  out-of-range value is only saved by `select_messages` raising ValueError,
  which reads as an accident rather than a contract.

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
