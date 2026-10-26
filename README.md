<!-- SPDX-License-Identifier: GPL-2.0-or-later -->
<!-- SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors -->

# ensqsqed

A terminal browser for the **Ensoniq SQ-R Plus** — its 340 sounds, and
the reason a program change on this machine is not a program change.

Sibling to [rxved](../rxved) (Roland XV-2020), [kwsed](../kwsed) (Korg
Wavestation SR), [p2ked](../p2ked) (E-MU Proteus 2000),
[x5ded](../x5ded) (Korg X5D/X5DR) and [nanosyned](../nanosyned) (Alesis
NanoSynth), and it follows their conventions.

## Support this project

ensqsqed is free software and always will be. Nothing is behind a paywall, no
feature is withheld, and none of what follows changes that.

But if it has been useful — if it saved you an evening of mapping zones
by hand, or got a library onto a machine that had no way of reading it, or
**best of all, if it has your vintage instrument switched on and in use more often
than it was, and you are having more fun with it** — then please consider
supporting the work.

**Because here is what it has actually cost:**

- **Real machines on a real bench.** Much of what these tools know about these
  formats was measured on hardware rather than read anywhere, because for most
  of it there is nowhere to read it. That needs the machines — the instruments
  themselves — and it puts hours of wear on hardware that has been locked up and
  recovered more than once in the course of it. Some of these instruments were
  bought specifically to add and verify a format; the others were already here,
  because the person doing this is a vintage instrument enthusiast first and the
  projects exist because the instruments were in the room.
- **Dozens — realistically hundreds — of hours of human time.** Format
  reverse-engineering is slow: measure, be wrong, measure again. A single
  parameter law in this README can represent an evening at the bench.
- **AI assistance, which is a paid service**, used heavily throughout and not
  cheap at this volume.

**This is support, not a donation — and the distinction is a legal one, not a
turn of phrase.** The maintainer is based in Germany, where payments like these
are *not* `Spenden` in the tax sense: they count as **taxable income** for the
recipient and are **not tax-deductible** for the giver. So this section is
titled *Support*, deliberately, and no receipt for tax purposes can be issued.
(That is a statement of how it is handled here, not tax advice.)

If the project saved you the work, you can support it through
**[GitHub Sponsors](https://github.com/sponsors/lentferj)** — the *Sponsor*
button at the top of the repository. Payment is handled entirely by GitHub and
Stripe, so bank and tax details are never handed to the person paying.

**Support is not expected, and it is not the only currency.**

- **Bug reports** — ideally with the bank, preset or disk image that produced
  them. A tool's failures are usually specific to one file rather than
  general, and without that file they are very hard to reproduce.
- **Confirmations from hardware that is not on this bench**, which matters more
  here than for a single-machine tool. These tools write for whole *families*
  of instruments, and the bench holds only a subset. Whether a variant accepts
  what we write is genuinely unknown, and several notes say "on this unit" for
  that reason. A "loads fine here too", or a "no, mine refuses it", is worth a
  great deal.
- **Corrections to the reverse-engineering notes.** The wrong turns are
  recorded next to the findings in `docs/RESOLUTION_NOTES.md` — retractions
  included, because a finding that was withdrawn is as useful as one that
  stood. If any of it is wrong in a way that is still costing someone time,
  saying so improves the record.

---

---



## AI assistance & human authorship

ensqsqed was built by its human author together with AI assistance. The
**ideas, the project vision, and every feature** came from the human author;
AI assisted with **writing the code and analyzing the binary formats**.
Crucially, the **reverse engineering rests on hands-on human work** — all testing
and verification on real hardware, creating the reference images/banks on those
instruments (disk saves, SysEx probes), and aural A/B comparison of presets —
which is what makes the results correct.
Full account in [DISCLAIMER.md](DISCLAIMER.md).

---
## ⚠️ Use at your own risk — back up first

 is provided **as is, with absolutely no warranty and no liability**
for data loss or **hardware damage**. You assume all risk. Full terms:
[DISCLAIMER.md](DISCLAIMER.md).

Before you use this software, **make good, current backups of all your files** —
and of any existing banks on your instrument and storage media. These tools can
write to hardware and storage media; a mistake, a bug, or untested output could
overwrite or corrupt data, or be rejected by hardware. Always test on a spare
unit or emulator **before** connecting irreplaceable equipment.

---

## The thing this exists to get right

The SQ-R has four groups of 80 sounds and 20 drum kits. It has no Bank
Select. **The group is chosen with a program change:**

| MIDI program change | what it does |
|---|---|
| 0–79 | a sound *in whatever group is in force* |
| 80–99 | one of the 20 drum kits, always |
| 123 | the next program change brings its effect too (MULTI) |
| 124 / 125 / 126 / 127 | from now on: Internal / ROM / Card A / Card B |

So a program change means nothing on its own. It means whatever the last
selector said — which may have been sent by somebody else, or by nobody.
`sqcli select INT 42 --dry-run` prints exactly what has to go out to land
on one sound, and the browser always sends the selector first.

The unit shows sounds as **00–79**, two digits and counted from zero —
measured on the unit, not taken from the manual's 001–128 wording. That is
also the program change it sends, so the display number and the wire number
are the same; nothing converts between them silently.

## Install

```sh
python3 -m venv .venv
source .venv/bin/activate
.venv/bin/pip install -e .
```

Needs Python 3.11+, `python-rtmidi` and `textual`, plus **vinsynlib** — the
shared base of this family of terminal instrument tools: the settings cache,
the keymap and legend, the command line and the port listing. It comes from
PyPI with the rest. To develop against a *working copy* of it instead of the
published one, install that alongside — it shadows the published version in
this environment only: `.venv/bin/pip install --no-deps -e ../vinsynlib`.

## Running it

```
python -m ensqsqed.cli groups            # the groups and their selectors
python -m ensqsqed.cli list ROM          # every sound in a group
python -m ensqsqed.cli select INT 42 --dry-run   # what a DAW must send
python -m ensqsqed.cli select INT 42 --yes      # send it to the unit
python -m ensqsqed.cli read dump.syx     # read a .syx or .mid dump
python -m ensqsqed.cli status            # ask the unit what it is on
python -m ensqsqed.cli names             # all 80 internal names (silent)
```

`status` and `names` need the instrument. Pass `--port` and `--device-channel`
the first time; both are remembered. If the unit's MIDI IN and MIDI OUT are
on different ports of a multi-port interface — a crosswise cable — also pass
`--recv-port`, or let the sweep find the pair (it listens on every input of
an interface while each output sends). Without a remembered pair the sweep
tries sixteen base channels on every port, which takes minutes; the channel
given by `--device-channel` is tried first.

`read` takes both, and deliberately: a `.mid` in this collection held a
message its matching `.syx` did not.

## What it does

ensqsqed is a terminal browser for the Ensoniq SQ-R Plus that displays
the two numbers that actually select a sound — the group selector (124-127)
and the program change (0-79) — on every row, plus a local favourites
database with ratings, tags and notes.

- **Two panes: groups on the left, that group's sounds on the right.**
  The SQ-R has no Bank Select. The group is chosen with a program change
  (124=Internal, 125=ROM, 126=Card A, 127=Card B), and every later program
  change means whatever that one said until the next. The browser always
  sends the selector first, and the slot pane's `#` is the number the unit
  displays — **00–79, two digits, counted from zero** — which is also the
  MIDI program change. A `sel` column carries the group selector — this
  instrument's replacement for the siblings' LSB/MSB columns — and shows
  `—` for a drum kit, whose program change is absolute.

- **Only Internal can be read.** `r` reads all eighty internal sounds in one
  silent message and takes their names out of it. The same read runs quietly
  at startup, so a fresh install shows names rather than blanks; what it
  reads is kept in a sidecar beside the catalog, so the next launch opens on
  them immediately. The other three groups have no dump request of any kind,
  so their names come from a catalog or not at all.

- **Favourites with ratings, tags and notes**, in a SQLite database in the
  platform's own application-data directory — so a `git clean` in a
  checkout cannot take it. Rate with `*` (0-5), filter with `V`, tag with
  `t`, note with `n`, cycle views with `v` (all slots / bank favourites /
  all favourites).

- **Category filter** (`C`): narrow the list by the instrument's patch
  categories. Ratings and categories stack: "soft pads I rated 4+" is one
  filter over another.

- **Read names off the synth** and they persist. `r` reads the Internal
  bank in one silent message; what it says is kept in a sidecar file beside
  the catalog so the browser opens showing your patches rather than the
  factory list. `S` learns the names of the groups the unit will not dump —
  ROM, Card A, Card B and the drum kits — by selecting each sound and
  reading back the current-sound record. That changes what the unit is
  playing while it runs, so it asks first, and a second `S` stops it. `R`
  empties the catalog (favourites, ratings, tags and notes are untouched).

- **Search** (`/`): case-insensitive substring search over printed names,
  live names, tags and notes, with checkboxes to choose which fields.

- **Undo/Redo** (`z`/`Z`): reverse the last change (favourite, rating, tag,
  note, edit) or all changes. Single timeline, so `z` reverses whichever
  came last.

- **Write gate** (`w` / `--allow-write`): locked by default. Arm it to
  enable destructive operations (future write path).

- **Persistent UI state**: bank position, slot cursor, view mode, rating
  filter and category filter are remembered between runs.

- **Keys** (the legend wraps, never truncates):

  ```
  ↑↓ move
  tab pane
  ⏎ select on the unit
  r read names
  / search
  [ ] channel
  c set channel
  f favourite
  v favourites view
  * rating
  V rating filter
  C category
  m multi tracks
  x effect
  p preset list
  I import
  M mode (sound / preset)
  t tags
  n note
  z undo
  Z undo all
  w write gate
  S scan device
  R rebuild catalog
  i device
  ? help
  q quit
  ```

## MULTI mode & presets

The browser edits sounds. A **preset** is the other thing the unit holds: a
performance setup of eight tracks — each with its own sound, mix, key range and
MIDI settings — and one effect that all eight share. The SQ-R's MULTI mode is
the panel's reading of that: each track that is on receives on its own MIDI
channel, and a track that is off does not receive at all. Three keys reach it.

- **`p` — the eighty presets.** Reads them all in one message (about twelve
  seconds, and it says so), lists them by the unit's own number and name, and
  **`⏎` selects the row's slot on the unit** before the editors open over it.
  The app sends Select Preset, then a Program Change; when the Program Change
  does not take (Preset Prog OFF, or the receiver ignores it), it falls back to
  stepping the selection with the panel's next/previous keys and verifies the
  slot byte. **The same bank is read once in the background after launch**
  (non-blocking, on a worker), so `p` usually opens on a warm list; a read
  older than a minute is taken again.
- **`m` — the eight-track editor.** The preset's eight tracks as a table.
  `⏎` on a track's sound opens a browser with **all five groups** — Internal,
  ROM, Card A, Card B and the drum kits — so a multi setup can mix sounds
  from anywhere the unit holds them, not just the bank the browser is on.
  `v` steps the same three favourites views the browser has: all sounds,
  this group's favourites, every favourite across groups (shown as
  `INT 003`, `CARDA 007`, …).
- **`x` — the effect editor.** The preset's one effect.

The track editor's columns are grouped, and `tab` cycles the groups the way
rxved's does. Two columns are in every group — `trk`, the track's own number,
and `on`, the stored track marker — then:

| group | columns | what the panel calls them |
|---|---|---|
| **sound** | `spot`, `name`, `ch`, `program` | the sound — group and slot — its name, the **MIDI channel** it receives on (1-16), and the program change it answers to (1-128); the channel is here because eight tracks on eight channels is what MULTI mode is for |
| **MIDI** | `status`, `press`, `sustain` | Status BOTH/LOCAL/MIDI/*EXT*, Pressure NONE/KEY/CHANNEL/OFF, and the sustain switch |
| **MIX** | `vol`, `pan`, `timbre`, `rel`, `out` | Volume 0-99, Pan SOUND or -98..+99, Timbre 0-99, Release -64..+64, Output DRY/FX1/FX2/VOICE/CONTROL |
| **KEY** | `low`, `high`, `transp` | the key range as note names (A0..C8) and the transpose (octave and semitone) |

**The stored ON/OFF marker is pressure 3.** The owner's P20–P23 matrix settles
it: the pile is `01 00 00 00 00 00 00 00` in all four, while track byte 8's
pressure bits carry the state — pressure 1 (KEY) is the panel's track number,
pressure 3 is its `-`. The `on` column reads that marker, and `space` toggles
it between 1 and 3. This is a normal stored field, so the whole preset goes.
**Any track can be off, including track 1 and all eight at once.**

`⏎` edits the cell under the cursor — a list to pick from when the values are
words, a number to type when they are numbers — a digit starts that number,
and `+`/`-` nudge it.

**`ctrl+s` stores with the unit's Enter/Save ×3.** The manual's front-panel
store is Enter/Enter/Enter → *SAVED*; the app sends those three presses and
then walks to the neighbouring slot only to read the stored bytes back, walks
back and verifies the slot against the unit's edit buffer. `E` is not needed
and is gone.

**`R` renames on the unit.** The app asks for the new preset name — max 16
printable ASCII — and drives the unit's save-dialog name page (cursor buttons
and **Enter/Save**) and stores to the selected slot, storing the current edit
too if there is one. The app's edits are SysEx dumps, so the save dialog asks
`Replace edit preset?` and `R` loads the working preset first; if the panel
itself has an un-stored edit, store it with `ctrl+s` first. **`r`** remains
the app's local label for the preset, kept in the `preset_labels.json`
sidecar.

**`l` is the live switch.** The panel's two Track keys — buttons 10 and 12 —
pressed together mute or unmute the track under the panel's cursor, and that is
the control for what plays now. The app sends that chord; `o` and `p` send the
Track keys singly to move the panel's track cursor (the unit's display is the
cursor's readout, which the app cannot see). The chord must leave as one
gesture: the ordinary 300 ms pace between SysEx messages is seven panel scans,
and a chord sent through it is two cursor moves and no mute, so
`PacedOut.send_chord` sends the pair 2 ms apart. `docs/KEY-MAP.md` §3 and
`docs/BUTTON-MAP.md` have the firmware and the measurements.

The effect editor lists the thirteen effects by the panel's own names (CONCERT
REVERB … CMPRSS+DIST+VERB), then the parameters of the active set, the FX1/FX2
mix, and the modulation destination/source/amount. The rows come from the
model, not from a hand-written list, so a byte that holds two parameters (the
modulation destination and source share one) is two rows that each mask the bits
they own.

**A change is sent as a whole preset** — the sequence data and the 168 bytes of
the preset — because this protocol has no field-level write. The unit stores it
into its **currently selected slot** (the display shows *Edited Preset*
transiently; no panel ENTER/SAVE is needed for a dump — ENTER/SAVE is still
what stores *panel* edits). The write gate (`w`) must be armed first, and
every change is read back and compared byte for byte — a refused or unmatched
write is reported, not assumed. What the store writes (measured 2026-10-24):
the tracks and pile, and the effect **parameters** (offsets 152–161). The unit
keeps its own preset name; **re-selecting the preset** commits the live effect
parameters, while the panel's ENTER/SAVE dialog did not store a sent effect in
the test. `ctrl+s` performs that re-selection and verifies the read-back;
the confirmation is green.

**The send is live for the sound's levels; the track arrangement needs a reload.**
Ear-verified on the unit on 2026-10-23: a volume send is heard immediately
(127 audible, 60 "a lot lower", 20 silent, each after the verified send and
past any settling time), while the track on/off — the status byte set to
*OFF*, or a track removed from the pile — is **not** heard: all eight
tracks set to OFF still played, before and after a reload. Level-side
changes therefore reach the playing engine; the arrangement is stored and
takes effect when the preset is **reloaded**. A reload is a preset-select
event, which the app can force remotely: the reload action steps the
unit's selection through all 80 presets (about a minute, audibly) and
reselects it. (An earlier paragraph here quoted readings from an audio
capture that turned out to be broken — it reported the same saturated
level for audible and inaudible notes — so those numbers are withdrawn;
the 2026-10-21 "the send is live" record was right about the levels for
the wrong reason.)

**Storing is remote now.** The app sends that slot's backed-up bytes and
the unit stores them — the display's *Edited Preset* is transient and
the unit keeps its own name. Panel edits still go through the manual's
six steps: check that the Edit Preset is displayed, press
**ENTER/SAVE** ("Save Preset \<name\>?"), optionally edit the name with
the cursors, press **ENTER/SAVE**, pick the slot with the cursors,
press **ENTER/SAVE** again — the display says *SAVED*. A double-click on
ENTER/SAVE stores to the same slot in one step. The store path itself
is the whole-preset dump behind the write gate (`w`), byte-verified;
sending a stored slot's own bytes back is idempotent
safe. Feature B (permanent per-track on/off via stored bytes) is reachable
this way. Feature A — the **live** on/off — is reachable too, through the
unit's own gesture: the two Track keys as a chord (virtual buttons 10 and
12). The MULTI screen's `l` sends that chord; it mutes or unmutes the
track under the panel's cursor, and the app cannot read that cursor, so
the sentence says so rather than pretending it picks the track
(`docs/KEY-MAP.md` §3).

**A remote window (`ctrl+o`) mirrors the panel, ungated.** The left cluster holds
the arrows and the entry keys, the right cluster the
System/Effects/Track pair, the Select/Edit row and Copy/Compare. Every
button on screen is also a PC key in the same physical place, and the
mapping is layout-immune — only keys that sit identically on QWERTY and
QWERTZ are used (a 3x2 block Q/W/E over A/S/D beside a 4x2 block U/I/O/P over
H/J/K/L; Space for the live-mute chord). The four mode
keys are buttons 1-4 in panel order (Select Sound, Select Preset, Edit
Sound, Edit Track); Bank Select is 0, System is 11, Effects is 13, and
YES/NO are the Up/Down keys (7 and 5). Copy
Effect and Compare are secondary functions (of Select Sound and Edit
Sound), not keys of their own. **`m` (or the browser's `M`) toggles the unit
between SELECT SOUND and SELECT PRESET** — buttons 1 and 2 on one key. The
unit does not report which mode it is in, so the app remembers the last one it
sent, shows it in the header, and keeps it when the window is reopened. The
key map and the caveats are in `docs/KEY-MAP.md` §5.

`w` works **inside both editors** as well as in the browser — a modal screen
cuts the browser's key chain, so the editors serve it themselves and toggle
the one gate the write path checks. The editor's own note line says whether
the gate is armed, and a refusal is said on the editor's status line, not on
the dimmed browser behind it.

Two MULTI rules are worth knowing, and both live on the unit:

- **MIDI Mode=MULTI is a setting in the unit's System bank**, not a message
  this program can send. Set it on the unit; the screen says so where it
  matters.
- **Never put a track on the base channel while Preset Prog is ON**, and give
  one track per channel — the lowest track number wins a channel.

Command line:

```sh
sqcli multi                        # the eight tracks of the preset the unit is on
sqcli multi --preset 12            # one of the eighty stored presets (reads all of them)
sqcli multi --out p.syx            # write the raw dump (the wire bytes `send` takes)
sqcli presets                      # the current preset as .syx (silent)
sqcli presets --all                # all eighty, as .syx (silent)
sqcli send preset-current.syx --yes   # WRITE it into the unit's selected slot
```

**What this deliberately does not do.** It does not drive the front panel:
the unit's virtual buttons exist, but no reliable gesture among them was
found, so the live switch for a track stays on the instrument
(`docs/BUTTON-MAP.md`) — live on/off (Feature A) is front-panel only;
permanent per-track on/off (Feature B) goes through the cooperative store
above. It does not decode the 21-byte global block or the trailing bytes of
the ten 54-byte sequence blobs — the dumps do not exercise them, and
`docs/RESOLUTION_NOTES.md` says so. And it does not change what the unit is
playing by moving the cursor: reading is silent.

### Backing the unit up, and putting a backup back

**Take a backup before this program writes anything.** Two commands, both
silent reads:

```sh
sqcli presets --all --out presets-all.syx   # all eighty stored presets
sqcli multi --out current.syx               # the one the unit is on
```

`presets --all` writes one message, about 36 KB — the whole memory. `send`
will refuse that file, because a whole memory is not a preset; to put a
single slot back, take it out on its own:

```sh
sqcli multi --preset 12 --out slot-12.syx   # one slot, in the form `send` takes
```

**What a restore writes, and what it does not.** A dump stores the selected
slot's **track/pile range, offsets 32–151** — that includes each track's
pressure (the stored ON/OFF), so the arrangement is restored. It does **not**
write the effect block (152–162) or the name (2–17): the unit keeps its own
name, and the effect needs the panel's **ENTER/SAVE** (or the app's `ctrl+s`)
to be stored from the working buffer. A dump restore does not write the name —
the unit keeps its own — but the app can now rename on the unit with `R` in the
multi editor, which drives the unit's save dialog and stores to the selected
slot. The app's local preset labels are a sidecar (`preset_labels.json`), not
part of the unit dump, so a dump restore does not bring them back. A backup
taken while the unit is on a slot with an un-stored edit captures that slot's
working preset; take the backup with no un-stored edit, or store first. Any
restore of the all-presets dump must use
the dump's slot table (`slot = (idx + 70) % 80` on this unit), not
`presets_raw[slot]`.

**Local labels are backed up too.** A presets/all backup writes
`preset_labels_<stamp>.json` beside the dump (the app's backup screen and
`sqcli backup` both do). Restore it with `sqcli restore preset_labels_….json`;
that is app-side only and does not touch the unit. The unit's own preset names
are not written by a dump; a real rename of the unit is the app's `R` key in
the multi editor, which drives the save dialog. Local labels are a separate
sidecar from that name.

**Putting a single preset back is cooperative.** The owner selects the slot
on the unit's front panel; the app sends the backed-up bytes; the unit stores
the track/pile range. The effect block needs the panel's ENTER/SAVE:

1. `sqcli send slot-12.syx --yes` — the track/pile range is stored into the
   selected slot; the dump is read back and compared byte for byte
   (verify=True, bounded retry).
2. On the unit, panel edits still go the manual's way: **ENTER/SAVE** →
   confirm the name → pick the slot with the cursors → **ENTER/SAVE** → the
   display says *SAVED*.

The owner's `.sqs` / `.syx` files remain the restore source for the memory
as it was before the 2026-10-21 button-probe contamination
(`docs/BUTTON-MAP.md` §8).

**This unit's memory was changed by the button experiments of 2026-10-21, and
the owner should know it.** The blind button presses walked the unit's save
dialog and a store completed: slots 2, 70 and 72 now hold a near-copy of
Preset-00 (23 bytes each differ from the run's backup), slot 0 differs by two
bytes, and the unit's selection was left on Preset-02. The owner's own
`.sqs` / `.syx` files are the restore source; the run's backup of the memory
as it was at 20:35 that evening is in `~/temp/sqr_backup/20261021-203542/`.
The details are in `docs/BUTTON-MAP.md` §8.

## Importing banks and sounds

`I` opens a **file browser** (it remembers the folder you last loaded from,
never the file) and reads an **All-Sounds dump** — the 32647-byte `.syx`,
or the `.mid` that carries the same SysEx. It offers two flows over the
three writable groups: Internal (RAM), Card A or Card B. **`ctrl+s` starts
either flow**; the preview screens show a progress line, and a long bank
read or write is covered by a "please wait" screen.

- **Whole bank** previews every sound in the file by number and name, then
  sends the chosen bank.
- **Single sounds** opens a two-pane map. The left pane lists the file's
  sounds with a `target` slot beside each; `⏎` on a row (or typing a digit)
  asks for a target slot from `00` to `79`, and target slots must be unique —
  a duplicate or out-of-range slot is refused with a sentence and nothing is
  written. `b` switches the target bank and the right pane is read again;
  `ctrl+s` starts the import. Slots you do not assign are left alone.

**Nothing is sent without the write gate and two confirmations.** Arm the
gate with `w` (or launch with `--allow-write`). **`w` also works inside both
import windows**, so a locked gate says so on the window's own status line and
your file, bank and target assignments are kept; arm it there and press
`ctrl+s` again. The first confirmation names the bank and the number of
sounds; the second names what is overwritten. The gate is checked again
between the second answer and the wire.

**Measured limit (2026-10-24): a dump does not store into the bank.** On this
unit a type-02 Single Sound Dump sent to a selected Internal slot is accepted
and changes the **edit buffer** — the sound is live — but the stored bank is
unchanged; the panel's SAVE SOUND is still what stores it. The 32647-byte
type-03 All Sounds Dump is rejected by the ALSA port here, so the whole-bank
flow falls back to per-slot sends. The import therefore reports "loaded into
the edit buffer, not stored" rather than claiming the bank changed.

**After a whole-bank send the target bank is reread** — RAM by its one dump,
a card by the per-slot scan — so the browser shows what is now on the unit
without a restart.

**Only Internal RAM is readable**, so an INT write is compared byte for byte
and reports the difference; Card A and Card B are not dumpable, and their
reread is a slow per-slot scan (select each sound, read it back) that changes
what the unit is playing while it runs. Take a backup before importing over a
bank you care about.

The hardware suite pins the measured behaviour behind a second, destructive
gate:

```
ENSQSQED_HW_BANK_WRITE=1 .venv/bin/python -m pytest tests/test_hardware.py \
    -k SoundBankWrite --hardware
```

The edit-buffer write passes; "stored in the bank" is a **strict xfail** that
will flag the day the unit starts storing.

## What it knows

Ensoniq's **MIDI System Exclusive Implementation Specification v1.43**
covers the SQ-1 family and says the SQ-R recognises the same header.
`sq/messages.py` is transcribed from it and checked against 116 dump
files. See `DISCLAIMER.md` for what is verified and what is not, and
`docs/RESOLUTION_NOTES.md` for how each thing was established.

## Development checks

The toolchain is pinned in `requirements-dev.txt`. Create the virtual
environment with `--system-site-packages` so the system `python-rtmidi`
and `textual` are visible, then install and run everything:

```
python3 -m venv --system-site-packages .venv
make bootstrap        # project + pinned toolchain + pre-commit hooks
make check            # ruff, mypy, pytest + coverage, pip-audit, vulture,
                      # deptry and detect-secrets, in that order
```

`make help` lists the individual targets (`lint`, `format`, `typecheck`,
`test`, `audit`). `make check` fails on the first problem. It passes on the
current tree because every pre-existing finding is baselined or ignored
narrowly rather than switched off: see `docs/QUALITY_CHECKS.md` for what is
suppressed, why, and the few items worth fixing outright. Formatting is
enforced too — `make lint` runs `ruff format --check`, and `make format`
rewrites files when the check complains.

## Licence

GPL-2.0-or-later. AI assistance was used; see `DISCLAIMER.md`.
