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

## 1. No bridge, because there was no instrument

**Status:** open. The largest missing piece.

`sq/bridge.py` does not exist. The siblings all have one; this does not,
because writing a transport against an instrument nobody can answer for
would be writing fiction. What it needs, in order:

* an Identity Request and its reply, which the specification documents in
  full (section 1.2) and which nothing has observed
* listening for a dump the user starts from the front panel, which is the
  documented route and needs no request at all
* only then, if it turns out one exists, a request

**Blocked on:** the SQ-R being patched in.

## 2. The four-byte command message is only half decoded

**Status:** open, and **not** to be closed by guessing.

Four 11-byte messages in one capture: message type `00`, payloads
`00 00 00 09` and `00 00 06 09`. Read as nibble pairs those are command
type `00` -- virtual buttons -- then button `09` and button `69` hex,
which is 105, which is 9 + 96, which the specification says is button 9
*released*. So they are somebody's editor pressing the "bank 9" button
twice.

That reading is consistent and still untested. Nothing sends it.

**Blocked on:** hardware, and on wanting it: driving a front panel
remotely is a different program from this one.

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
