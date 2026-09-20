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
