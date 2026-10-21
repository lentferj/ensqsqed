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

The unit shows sounds as 001–080 and MIDI sends 000–079. Those are
separate attributes and nothing converts between them silently.

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
  sends the selector first, and the slot pane shows both numbers the unit
  displays (001-080) and MIDI sends (000-079). A `sel` column carries the
  group selector — this instrument's replacement for the siblings' LSB/MSB
  columns — and shows `—` for a drum kit, whose program change is absolute.

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
  hands the chosen one to the editors.
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
and `on`, whether the track is in the pile — then:

| group | columns | what the panel calls them |
|---|---|---|
| **sound** | `spot`, `name`, `ch`, `program` | the sound — group and slot — its name, the **MIDI channel** it receives on (1-16), and the program change it answers to (1-128); the channel is here because eight tracks on eight channels is what MULTI mode is for |
| **MIDI** | `status`, `press`, `sustain` | Status BOTH/LOCAL/MIDI/*EXT*, Pressure NONE/KEY/CHANNEL, and the sustain switch |
| **MIX** | `vol`, `pan`, `timbre`, `rel`, `out` | Volume 0-99, Pan SOUND or -98..+99, Timbre 0-99, Release -64..+64, Output DRY/FX1/FX2/VOICE/CONTROL |
| **KEY** | `low`, `high`, `transp` | the key range as note names (A0..C8) and the transpose (octave and semitone) |

`⏎` edits the cell under the cursor — a list to pick from when the values are
words, a number to type when they are numbers — a digit starts that number,
`+`/`-` nudge it, and `space` switches the whole track on or off. A track's
on/off state lives in the **pile**, not the MIDI status byte: the status byte
says BOTH/LOCAL/MIDI/*EXT* and has no off at all, while the panel shows a track
as `—` when it is off. The primary track cannot be switched off.

The effect editor lists the thirteen effects by the panel's own names (CONCERT
REVERB … CMPRSS+DIST+VERB), then the parameters of the active set, the FX1/FX2
mix, and the modulation destination/source/amount. The rows come from the
model, not from a hand-written list, so a byte that holds two parameters (the
modulation destination and source share one) is two rows that each mask the bits
they own.

**A change is sent as a whole preset** — the sequence data and the 168 bytes of
the preset — because this protocol has no field-level write. The unit takes it
into its **Edit Preset** buffer and its display says *"Press ENTER to save new
preset"*. **Storing it is a front-panel act, and nothing this program does
stores a preset**: there is no SysEx message that stores one, and the editors
are deliberately fenced off from the front panel. The write gate (`w`) must be
armed first, and every change is read back and compared byte for byte — a
refused or unmatched write is reported, not assumed.

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
sqcli send preset-current.syx --yes   # WRITE it into the unit's Edit Preset
```

**What this deliberately does not do.** It does not store a preset to a memory
slot: a stored slot changes only when somebody presses ENTER on the unit, and
the editors never press it. It does not decode the 21-byte global block, the
*EXT* MIDI status, or the trailing bytes of the ten 54-byte sequence blobs — the
dumps do not exercise them, and `docs/RESOLUTION_NOTES.md` says so. And it does
not change what the unit is playing by moving the cursor: reading is silent.

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
