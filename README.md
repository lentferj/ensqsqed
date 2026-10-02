<!-- SPDX-License-Identifier: GPL-2.0-or-later -->
<!-- SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors -->

# ensqsqed

A terminal browser for the **Ensoniq SQ-R Plus** — its 340 sounds, and
the reason a program change on this machine is not a program change.

Sibling to [rxved](../rxved) (Roland XV-2020), [kwsed](../kwsed) (Korg
Wavestation SR), [p2ked](../p2ked) (E-MU Proteus 2000),
[x5ded](../x5ded) (Korg X5D/X5DR) and [nanosyned](../nanosyned) (Alesis
NanoSynth), and it follows their conventions.

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
`sqcli reach` shows exactly what has to go out to land on one sound, and
the browser always sends the selector first.

The unit shows sounds as 001–080 and MIDI sends 000–079. Those are
separate attributes and nothing converts between them silently.

## Running it

```
python -m ensqsqed.cli groups            # the groups and their selectors
python -m ensqsqed.cli list ROM          # every sound in a group
python -m ensqsqed.cli reach CARDB 42    # what it takes to select one
python -m ensqsqed.cli read dump.syx     # read a .syx or .mid dump
python -m ensqsqed.cli status            # ask the unit what it is on
python -m ensqsqed.cli names             # all 80 internal names (silent)
```

`status` and `names` need the instrument. Pass `--port` and `--channel`
the first time; both are remembered. Without them the sweep tries sixteen
base channels on every port, which takes minutes.

`read` takes both, and deliberately: a `.mid` in this collection held a
message its matching `.syx` did not.

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
