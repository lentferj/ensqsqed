<!-- SPDX-License-Identifier: GPL-2.0-or-later -->
<!-- SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors -->

# What this project knows, and how well

ensqsqed is not affiliated with Ensoniq. "Ensoniq", "SQ-R", "SQ-1" and
"KS-32" are their names, used to say which instrument this talks to.

## From Ensoniq's specification

**MIDI System Exclusive Implementation Specification, version 1.43**, for
the SQ-1, SQ-1+, SQ-1+/32, SQ-2, SQ-2/32 and KS-32. It states that the
SQ-1 Model ID in the header "is also recognized by the SQ-R in order to
allow transfer of common messages between an SQ-1 and an SQ-R", and that
messages not common to both are ignored.

- The header `F0 0F 06 00 <base channel> <message type>`, where `0F` is
  Ensoniq, `06` the SQ family and `00` the SQ-1 Model ID.
- Message types: `00` command, `01` error, `02` single sound (408 data
  bytes), `03` all sounds (408 × 80 = 32640), `04`/`05` sequences.
- Eight-bit bytes travel as two nibbles, **high nibble first**.
- A sound is **204 bytes**; its name is **sixteen ASCII characters packed
  into fourteen bytes at offset 177**, seven bits each.
- Command type `00` is **virtual button presses** — an editor drives the
  front panel remotely rather than sending a dump request.
- Error codes: `00` NAK, `01` ACK, `02` invalid button number.

## From the owner's manual

Ensoniq's SQ-R Bedienungsanleitung:

- 340 sounds: 80 Internal, 80 ROM, 80 Card A, 80 Card B, 20 drum kits.
- The program-change-as-group-selector scheme, and that program changes
  125–128 as displayed are 124–127 on the wire.
- That the unit's own numbering runs 001–128 while MIDI sends 000–127.
- That transfers are started from the front panel, and that card sounds
  must be loaded into Internal before they can be dumped.

## Verified against real dumps

116 `.syx` and `.mid` files from three different people's instruments:
74 all-sounds dumps and 8 single sounds, every one of them exactly the
size the specification gives. All 80 names in a bank decode to clean
ASCII at the documented offset.

## Checked without hardware, as far as that goes

The specification prints a worked example: the Left Arrow button, logical
number 25, pressed and released. `messages.virtual_button` generates both
messages **byte for byte identically**. That exercises the header, the
nibble order and the button offset together, and it is the only
end-to-end check available without the instrument.

The firmware ROMs were examined too, and the result is worth stating
because it is a negative one. The pair interleaves into a valid 68000
image -- upper ROM the high byte, a vector table with SSP 0 and PC
`0x00C10102`, so the ROM is mapped near `0x00C10000` -- but a targeted
search for the System Exclusive parser found only candidates that cannot
be confirmed without disassembling at the right base address. The
firmware neither corroborates nor contradicts the specification at the
depth examined.

## Not verified, because the instrument was not reachable

**Nothing here has been sent to an SQ-R.** The SQ-R was not
patched into the MIDI bay during this work. So:

- No message has been *sent* to an SQ-R by this program.
- The Identity Request reply is documented but was not observed.
- Whether this particular SQ-R Plus answers everything the SQ-1
  specification describes is not established; the specification itself
  says the two share only their common messages.

`sq/bridge.py` exists and is transcription, not experience.

## Deliberately not done

`ensqsqed` never presses the instrument's buttons. `virtual_button`
exists so that a capture in the owner's files is explicable, and nothing
calls it: driving a front panel blind, with no display to read back, is a
different program.

## Ensoniq's content is not redistributed here

No sound, preset or drum-kit name from the instrument appears in this
repository. The generated catalog is gitignored and every fixture is
invented.

## AI assistance

Written with AI assistance (Claude). The protocol work was done against
Ensoniq's specification and 116 real dump files; every claim above is
marked with which of those it rests on, and the one thing that was
*inferred before the specification turned up* — the nibble order — turned
out to be wrong, which is recorded in `docs/RESOLUTION_NOTES.md` rather
than quietly corrected.
