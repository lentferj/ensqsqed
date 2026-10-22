<!-- SPDX-License-Identifier: GPL-2.0-or-later -->
<!-- SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors -->

# The SQ-R Plus's virtual buttons

What the unit accepts, what it refuses, what was tried with what it accepts,
and why this project drives no front panel.

Measured on one Ensoniq SQ-R Plus (firmware family SQ-1/SQ-2/KS-32/SQ-R),
base MIDI channel 11, on 2026-10-21. Every number below comes from a
recorded run; the runs are listed in *Evidence* at the end.

**The verdict in three sentences.** The SQ-R does have virtual buttons, and
it does answer an unknown button number with SysEx error 02 ("invalid button
number"). It accepts exactly its own eighteen — the presses 0–13 and 16–19,
the releases the same numbers plus 96 — and refuses everything else, the
whole SQ-1 button table included. Among the eighteen no gesture that
reliably switches a MULTI track on and off was found, so **the live MIDI
switch for a track stays on the unit's front panel** (Select Sound, then the
Track buttons) and this project does not drive the panel.

---

## 1. The mechanism

The SQ-1 MIDI Specification, §3.1.1, describes a command message of type
`00` that simulates a front-panel button press: *"EXT can simulate button
presses from the front panel of the SQ-1 by sending this command."* The
button number is the payload; a **press** is the number itself, a **release**
is the number plus 96.

The wire form, from the specification's own worked example (the Left arrow,
logical button 25):

```
press   25  →  F0 0F 06 00 00 00 00 00 01 09 F7
release 25  →  F0 0F 06 00 00 00 00 00 07 09 F7
```

`sq/messages.py::virtual_button` builds exactly these bytes, and a test
pins them (`tests/test_banks.py`, "the specification's own worked example").

**Error 02 is the oracle.** A button number that does not exist is
answered with the specification's error message, code `02` = "invalid button
number": *"The button number in the preceding virtual button BUTTON message
doesn't correspond to any real button number"* (SQ-1 MIDI Specification,
Appendix A, p. 5). The bridge turns every non-ACK code into a refusal, so
the sentence on screen is *"the unit refused: invalid button number"*.

**Silence means accepted.** A button that exists produces no reply at all.
So a probe is one line: send the number, wait; a refusal means the number
does not exist, silence means it does. That is what makes the sweep below a
closed-loop measurement rather than a guess.

---

## 2. The accepted set

Sweep of 0…127 (40/41/42 never sent — they are the SQ-1's transport keys —
and 28 probed separately, because it is Enter/Save and has its own
protocol). Source: `~/temp/tasks/H1_result.json`, verdict
`ACCEPTED_NUMBERS_36`.

| numbers | what the unit did |
|---|---|
| 0–13 | **accepted** (press) |
| 14, 15 | **refused** — the hole |
| 16–19 | **accepted** (press) |
| 20–39 | **refused** — this is the whole SQ-1 table (Select Sounds 20 … Compare 29, Track 1–8 30–37) |
| 40–42 | **never sent** — the SQ-1's transport keys (Record / Play / Stop), deliberately not probed |
| 43–95 | **refused** |
| 96–109, 112–115 | **accepted** — the +96 release forms of 0–13 and 16–19 |
| everything else, including 110, 111, 116–127 | **refused** |

**Enter/Save is not among them.** The number 28 was probed on its own, with
the deliberate three-press store protocol, and the unit refused it
(`BUTTON28_REFUSED`). The plan's "double-click ENTER/SAVE stores to the
same slot" is therefore not reachable from here either.

**The count matches the panel.** The SQ-R has exactly eighteen physical
buttons — System, Effects, Track, Track On/Off | Select Sound, Select Preset,
Edit Sound, Edit Track | Enter/Save, Bank Select, YES, NO | Copy Effect,
Compare | Left, Right, Up, Down (German manual, Teil 1, pp. 1-4…1-7) — and
the unit accepts exactly eighteen *button* numbers (thirty-six accepted
values, counting the +96 release forms). The SQ-1's table is not used by the
SQ-R at all, which the specification explicitly allows: *"All messages
which are not common to both machines will be ignored"* (SQ-1 Musician's
Manual, p. 2).

**Which button is which number is not established.** The number-to-button
assignment is a hypothesis (`~/temp/tasks/R1_button_hunt.md` §3, hypothesis
A), not a measurement. What is measured is the set, the hole at 14 and 15,
and the release offset of +96.

---

## 3. What the accepted buttons did

Blind single presses, one per accepted number, each followed by a read of
the working preset and of the current sound. Source:
`~/temp/tasks/H2a_result.json`.

| class | buttons | what it did |
|---|---|---|
| no visible change | 1, 2, 3, 4 | nothing readable changed |
| preset selection or edit | 0, 6, 7, 8, 9–13, 16–19 | the unit changed preset, or its selection moved |
| preset bytes changed, name the same | 5 | a parameter changed |
| sound cursor | none | no button moved the sound selection |

Buttons 6, 7 and 8 moved the unit's selection to Preset-70, Preset-72 and
Preset-02 in turn; from button 9 onwards the unit sat on Preset-02 and
stayed there.

**2026-10-23's controlled walks (H4/H4b/H4c/H4d)** revise this table
substantially. Each press was followed by a read of the working preset;
the read-back's name *and* its own address bytes say where the unit is:

| number | what the controlled press did |
|---|---|
| **5** | **Select Preset — the owner watched the Preset Select display light up — and it steps the selection one slot DOWN (mod 80), 5/5 reliable: P79→78→77→76→75, verified by name and address bytes each time** |
| 0, 1, 4, 6, 7, 9 | no selection change (4 pressed three times, 6/7 twice — mode/screen changes at most) |
| 9 held + 10 (YES) / 11 (NO) | nothing (the manual's bank-jump combo, tried over the wire — H4d) |
| 5 held | no autorepeat (a 1.5 s hold = 0 extra steps — the repeat is the panel's key scan, not the OS handler) |
| 16–19 | **inert over SysEx, even in Preset Select mode** (owner-observed at P79); firmware: `$C0FE32` posts message tag 22 to the *sequencer* task (`$FFFFFB66`), not the UI task — the virtual arrows cannot act as the UI's cursor keys |
| 2, 3, 8, 10, 11, 12, 13 | not re-pressed over the wire (the Track pair, Enter/Save's dialog, YES/NO's command execution, Copy Effect — the hazards) |

The H2a walk's "buttons 6, 7 and 8 moved the selection to Preset-70/72/02"
reads as the save dialog: button 8 is Enter/Save, the dialog's slot cursor
was what moved, and the walk's later presses moved that cursor — which is
where the contamination came from. The button-number→key identification
is now anchored by the Select-Preset display observation (button 5).

**What the map means for selection:** button 5 (−1, mod 80) is the *only*
remote selection step; any target is ≤ 79 presses away, each step
verifiable by reading the working preset (command 03), whose seqno byte
(preset offset 0) is the current slot. There is no "+1" on the wire: the
arrows are dead, and the program-change gate (`$FFFFD6BA`) has one `tstb`
and no writer in OS 2.03 (`RE_sysex.md`) — dead code.

**This probing contaminated the unit's memory.** See *Contamination* below.

---

## 4. The chord experiment

The manual's gesture, German SQ-R Bedienungsanleitung, Teil 3, 3-6:

> „Wenn Sie beide Track Tasten im MULTI Mode drücken, wechseln Sie damit den
> Track Status zwischen \*OFF\* und LOCAL."

*When you press both Track buttons in MULTI mode, you switch the track
status between \*OFF\* and LOCAL.*

The plan's route was to press the two Track buttons together over the wire
and watch a status byte move.

**What happened.** The pair **(2,3)** — the two numbers the panel inventory
puts on the two Track buttons — hit exactly **once**: the working preset's
pile went from `01 02` to `01 00`, i.e. track 2 left the layer list, and the
status bytes did not move at all. The undo — the same chord again — did not
put it back: after it the pile still read `01 00` and the preset hash still
differed from the baseline.

**How often.** Across the three sessions that tried it — H2a's sweep over
the adjacent pairs, H2c's bare chord plus its sixteen screen hunts, H2d's
twenty-five join-gap chords and ten hold chords — it hit **once in roughly
sixty attempts**.

**What that one hit says.** A *pile* change is the **layering** gesture
(manual 3-5: „Drücken Sie beide Track Tasten gleichzeitig für jedes Track,
das gelayert werden soll" — press both Track buttons at once for every track
that is to be layered), not the *status* gesture of 3-6. So the single
success reached the Track buttons on the wrong screen; the status toggle was
never achieved, and H2c's screen hunt found no button that reaches it
(`NO_STATUS_TOGGLE_FOUND`).

**The limits of the claim.** One hit in sixty is not a gesture; it is a
coincidence the firmware may have produced by any of the eighteen
combinations. Nothing here says the pair (2,3) *is* the Track buttons —
the assignment is a hypothesis — and nothing here says a status toggle is
impossible. What it says is that **no reliable gesture was found**, and this
project ships no macro that depends on one.

---

## 5. The timing sweep

If the both-buttons gesture needs simultaneity, the join gap decides
whether it lands. The sweep: for each join gap, five chords, each followed
by the identical undo chord; a chord counts only when the working preset
changed *and* the undo restored it byte for byte. Source:
`~/temp/tasks/H2d_result.json`, verdict `NO_RELIABLE_GESTURE`.

| join gap | attempts | complete round-trips |
|---|---|---|
| 0.005 s | 5 | **0** |
| 0.010 s | 5 | **0** |
| 0.015 s | 5 | **0** |
| 0.020 s | 5 | **0** |
| 0.030 s | 5 | **0** |
| hold variant (0.30 s hold, both orders) | 5 + 5 | **0 + 0** |

**No gap works.** From a 5 ms join to a 300 ms hold — the specification
recommends 2–300 ms between button commands — not one of the thirty-five
chords produced a complete round-trip.

Two further negatives from the same sessions:

- **A program change did not take.** `PC 0` on the base channel was meant to
  put the unit back on Preset-00; it wanted Preset-00 and got Preset-02
  (`phaseA_program_change`, `took: false`).
- **Single presses are inert as a toggle.** No accepted number, pressed
  alone, changed a track's status (H2a's classification above, H2c's
  sixteen-button screen hunt).

---

## 6. The other routes

**CC7 (channel volume) is one-way.** Measured cleanly on a live-audible
track (`~/temp/tasks/H2e_result.json`, verdict `CC7_ONE_WAY`):

| step | RMS |
|---|---|
| reference (no CC7) | 32.94 |
| CC7 = 0 (muted) | 5.20 |
| CC7 = 127 | 6.74 |
| program change re-selecting the same preset | 8.03 |
| CC7 = 127 again | 5.86 |

The mute lands and stays landed: neither the volume message nor re-selecting
the preset brought the sound back. So CC7 is a one-way latch on this unit
and not a mute anybody can undo.

**The status byte cannot be set by a dump either.** Two dead ends from the
earlier bench session, both with a byte-for-byte read-back behind them
(`~/temp/sqr_multi_onoff_problem.txt` §5.7 and §5.8):

- The **name bytes' mute bit** (bit 7 of the preset name, the
  specification's "track mute status") is rewritten by the unit: a preset
  sent with the bit cleared reads back with the bit set.
- **Track byte 6** (the specification's reserved byte, always `0x80`) is
  write-protected: sending `0x00` reads back changed.

**The live track switch is the panel's.** Select Sound, then the Track
buttons. That is the switch that decides whether a MULTI track plays, and
the protocol does not expose it.

---

## 7. What this means for the project

- **No remote macros.** The plan's `PanelDriver` (toggle a track, store the
  edit, discard it) is not built. Every one of its macros would be built on
  a gesture that was measured to be unreliable or absent.
- **The write path stays where it is:** one whole-preset dump
  (`SqBridge.send_preset`), byte-verified — and it lands in the unit's
  currently selected slot's memory: the unit stores it (the display shows
  *Edited Preset* transiently; no panel ENTER/SAVE is needed for a dump).
- **The store is cooperative now.** A dump does reach stored memory: the
  owner selects the slot on the panel, the app sends the slot's backed-up
  bytes, and the unit stores them into the selected slot (no panel
  ENTER/SAVE needed for a dump; the unit keeps its own name). Panel edits
  still need ENTER/SAVE, and the virtual ENTER/SAVE candidate (button 28)
  is still refused by this firmware.
- **The app says where the live switch is** after every track change, in the
  same words as this document: *the live MIDI switch for a track is on the
  unit's front panel (Select Sound, then the Track buttons)*.

---

## 8. Contamination — what the button probes did to the unit

**The unit's memory was changed by the probing, and the owner needs to know
it.** During the blind single presses of H2a the unit's save dialog was
walked and a store completed, and the result was:

| slot | what it holds now |
|---|---|
| 0 | the backup's bytes with **two bytes changed** |
| 2 | a near-copy of Preset-00 — **23 bytes** differ from the backup |
| 70 | a near-copy of Preset-00 — **23 bytes** differ from the backup |
| 72 | a near-copy of Preset-00 — **23 bytes** differ from the backup |

The unit's selection was left on **Preset-02**.

**The restore is the owner's (or the app's, cooperatively).** A slot's
backed-up bytes can now be written back the same way a send stores:
the owner selects the slot on the panel, the app sends the slot's
backed-up bytes, the unit stores them — no panel ENTER/SAVE needed for a
dump. For the button-probe contamination above the owner still uses their
own `.sqs` / `.syx` files through the unit's own dialog
(ENTER/SAVE, confirm the name, pick the slot, ENTER/SAVE again — the
manual's six steps, Teil 3, 3-3). The run's own backup of the memory as
it was at 20:35 that evening is in
`~/temp/sqr_backup/20261021-203542/` (all five artefacts: the all-presets
dump, the current preset, the current sequence blob, the slot offsets and
the internal sound bank).

---

## 9. Evidence

| file | what it holds |
|---|---|
| `~/temp/tasks/H0_result.json` | the first probe: the unit answers button messages with error 02 (`REFUSED_BY_UNIT`) |
| `~/temp/tasks/H1_result.json` | the E1 sweep of 0…127 (`ACCEPTED_NUMBERS_36`), the E8 addressing probe (`E8_NEGATIVE`), the Enter/Save probe (`BUTTON28_REFUSED`) |
| `~/temp/tasks/H2a_result.json` | the chord sweep, the single-press classification, the selection drift |
| `~/temp/tasks/H2c_result.json` | the status-toggle hunt (`NO_STATUS_TOGGLE_FOUND`) |
| `~/temp/tasks/H2d_result.json` | the join-gap sweep and the hold variant (`NO_RELIABLE_GESTURE`) |
| `~/temp/tasks/H2e_result.json` | the globals-byte drift, the clean CC7 round-trip (`CC7_ONE_WAY`) |
| `~/temp/tasks/H2f_result.json` | the deafness re-test (`DEAFNESS_REFUTED`) |
| `~/temp/tasks/R1_button_hunt.md` | the read-only hunt for a button table, and the SQ-R's physical button inventory |

The scripts that produced them are next to them: `h0_backup_gate.py`,
`h1_probe_store_and_buttons.py`, `h2a_chord_and_classify.py`,
`h2c_live_toggle.py`, `h2d_chord_sweep.py`, `h2e_clean_live_tests.py`,
`h2f_deafness_test.py`.

---

## 10. Sources

- Virtual buttons, the +96 offset, the 2–300 ms recommendation, the button
  table, the worked example: SQ-1 MIDI Specification, §2.1 and §3.1.1.
- Error code 02 "invalid button": SQ-1 MIDI Specification, §3.2.1 and
  Appendix A, p. 5. Mirrored in `sq/messages.py::ErrorCode`.
- "All messages which are not common to both machines will be ignored":
  SQ-1 Musician's Manual, p. 2.
- The SQ-R's panel and its eighteen buttons: German SQ-R Bedienungsanleitung,
  Teil 1, pp. 1-4…1-7.
- The both-Track-buttons status gesture (MULTI mode) and the layering
  gesture: German SQ-R Bedienungsanleitung, Teil 3, 3-5 and 3-6.
- The store dialog and the double-click tip: German SQ-R Bedienungsanleitung,
  Teil 3, 3-3.
- The MIDI status table (incoming MIDI plays local voices: BOTH yes, LOCAL
  yes, MIDI no, OFF no): German SQ-R Bedienungsanleitung, Teil 3, 3-12.
