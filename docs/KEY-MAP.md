<!-- SPDX-License-Identifier: GPL-2.0-or-later -->
<!-- SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors -->

# The front-panel key map (firmware-derived)

What a virtual button number means, read from the SQ-R 32 Plus OS 2.03
image (`sqr203_os.bin`, 68000 big-endian, base `$C00000`) and validated
against presses measured on the unit. This document supersedes the
hypotheses in `docs/BUTTON-MAP.md` §2–3; every claim below is either a
**firmware fact** (an address and the code there), a **measurement**
(what the unit did), or an **inference** (marked as such).

Method: `m68k-linux-gnu-objdump` and Ghidra 11.1.2 headless
decompilation. The image was reconstructed from the two EPROMs — UPPER
= even bytes, LOWER = odd bytes, `ENSONIQ` found interleaved at the same
offset in both dumps — and the 68000 vector table at `$C00000` steps by
four, which confirms the reconstruction.

## 0. How a key press reaches an action

A virtual-button frame (SysEx command type 0, payload = button number)
and a physical panel key land in the **same** dispatcher. The chain
(firmware fact):

```
$C0E96C  command-0 receive: 0..13 -> $C0FDF4 (press) / $C0FE14 (release)
                              16..19 -> $C0FE32 (tag 22 -> sequencer task $FFFFFB66)
                              14/15  -> SysEx error 02
$C0FDF4  posts {tag=$B006, key} to the UI task $FFFFFB3A
$C0FE14  posts {tag=$B00C, key} to the UI task
UI task  $C0AECC parses, $C0AEE4 dispatches; tag $B006 -> $C0B006, $B00C -> $C0B00C
$C0B006 / $C0B00C -> $C0B014 -> $C0B01C
$C0B01C   d0 = word at $FFFFDB1C[key*2]   (the soft-key table, bit 0 = flag)
          a2 = d0; $C1093A bank-fixes a2 (high word := 0x00C0); jmp (a2)
```

So **virtual button N == soft-key index N, 0..13, one to one**; the
action word is a code pointer in bank `0xC0`. Buttons 16–19 are
re-routed to the sequencer task and are not soft keys. The arrows the
panel actually uses for cursors are inside 0–13.

## 1. The screens and their key tables

A screen is installed by `FUN_00C0B09E` from a four-byte screen record:

| field | meaning |
|---|---|
| `record+0` | index into the action-enable mask table `$C1C552` (longwords; bit k = key k enabled) |
| `record+1` | index into the second mask table `$C1C502` (used by the press/release test) |
| `record+2` | screen id → word in `$C1C466`, a packed action-table offset (bank `0xC0`) |
| `record+3` | flags (bit 7: a display-table pointer follows the action words) |

The installer copies **one action word per enabled key** into
`$FFFFDB1C`; the tables are packed by the mask, not fixed fourteen
words. The screen records themselves form an 85-entry table at
`$C0AC04`–`$C0AD54`; the screen-id table at `$C1C466` has 78 ids
(offsets `0xA9F4`–`0xAC02`).

The **main preset display** (the idle screen showing `PSET nn`, the
track-status row and the preset name) is:

* record at **`$C0AC20`** = `00 00 07 8B` → mask 0 (`$C1C552[0]` =
  `0x3FFF`, all fourteen keys enabled), press mask `$C1C502[0]` =
  `0x3FFF`, screen id **7** → action table **`$C0AA44`**.

## 2. The main screen's key table

Action words at `$C0AA44`, in key order (firmware fact); the "panel
key" column mixes measurement and inference as marked.

| key | action | what the code does (decompiled) | panel key (measured 2026-10-23 unless marked) |
|----:|---|---|---|
| 0 | `$C0B82C` | global/system parameter page | **System** (Master Tune / Global Bend page shown) |
| 1 | `$C0B6BE` | returns screen record `$C0AC24` (sid 8) | **Effects** (the sound/effects page, ROM 09 "Analog Power") |
| 2 | `$C0B9C8` | saves `$FFFFD930`, loads the edit buffer, returns `$C0AC44` (sid 16 = VOICES editor) | **Edit Sound** |
| 3 | `$C0BB64` | sets up an edit object at `$FFFFDC40` | not Edit Track (see below); showed the VOICES page in one test |
| 4 | `$C0BC74` | select-sound flow → `$C0AC50`/`$C0AC4C` (sid 18) | **Select Sound** (sound-select page) |
| 5 | `$C0B776` | installs preset screen record `$FFFFAD18` | **Down** (preset: previous slot, −1, 5/5: P79→78→77→76→75) |
| 6 | `$C0B722` | current part value, key 6 = tens digit −1 | **Left** (System page: previous page, wraps to the last; preset: tens −1, P00→P70) |
| 7 | `$C0B770` | installs preset screen record `$FFFFAD14` | **Up** (preset: next slot, +1: P79→P00) |
| 8 | `$C0B722` | same handler, key 8 = tens digit +1 | **Right** (System page: next page; preset: tens +1, P70→P00) |
| 9 | `$C0BD38`-family | the Enter/Save action | **Enter/Save** (opens the "Replace edit preset?" copy prompt) |
| 10 | `$C0BD38` | confirm/menu family | **Track L** (moves the blinking track cursor backwards: 1→8→7→6, measured) |
| 11 | `$C0B39E` | sets `$FFFFDBFE=$D214`, returns `$C0AC58` (sid 20) | ambiguous: showed the store hint once, the System menu another time |
| 12 | `$C0BD38` | same function, key 12 → `$C0ACA0` (sid 36) | **NO** — and Down on **any** editable field (owner-verified on the System page: left/right switch rows, up/down change values) |
| 13 | `$C0B3F0` | returns `$C0AC6C` (sid 24) | **Compare** (the effect display: Chorus Rate/Depth) |

**The measured facts that corrected the first inferences.** Button 2
opened the `EditVoice=VOICES` editor (the Edit Sound LED lit); button 4
the sound-select page; button 0 the Master Tune/Global Bend page;
button 9 the "Replace edit preset?" prompt, which the manual names as
the copy action's first screen — i.e. Enter/Save; button 10 moves the
track cursor **backwards** (1→8→7→6), which is the Track L behaviour.
The four arrows are **5 = Down, 6 = Left, 7 = Up, 8 = Right**: on the
System page left/right switch pages (6 wraps to the last) and up/down
change the value; on the preset display 5/7 step the preset (down/up in
the list, P79→78… and P79→P00) and 6/8 change the tens digit of the
preset number (P00→P70, P70→P00) — the digit under the cursor.

**The Track-key chord works, and it is the live mute.** Buttons 10 and
12 are the two Track keys; a single press moves the panel's track
cursor (10 backwards, 12 forwards), and both pressed **together** mute
or unmute the track under the cursor: the display's track number turns
into "-" and that track's notes fall silent (owner-verified, 2026-10-23).
The chord writes **nothing** into the preset bytes (pile and statuses
unchanged), which matches the firmware: the gate is the RAM mask
`$FFFFD718`, so this is the one way to change what the unit plays
without touching the stored preset. The app sends this chord in
`SqBridge.toggle_live_mute()`; because the panel's cursor is not
readable, the action toggles whichever track the panel points at, and
the app says so.

**Two corrections from the owner (2026-10-23).** Button 3 is **not**
Edit Track: pressing the physical Edit Track key from the preset
display opens `Track 1 ON / Status = LOCAL`, while button 3 showed the
`EditVoice=VOICES` page. And "Compare" has no dedicated mode: on a
preset it is the **Select Preset** key (toggles original/edit preset),
and on a sound it is the blinking **Edit Sound** LED (manual Teil 3 and
Teil 2/4). The dedicated Compare key's action is the effect display
above.

**YES/Up = button 7.** The manual treats "Yes/No" and "Up/Down" as the
same pair ("Mit den Up/Down (bzw. Yes/No) Tasten"), and on the unit the
up function is button 7: it raises the value on an editable field (the
System page) and steps the preset up in the list. The down side is
button 5 (Down), with button 12 (NO) acting as down on editable fields
too.

**Still unassigned:** Bank Select, Edit Track, Select Preset and Copy
Effect. The remaining candidate numbers are 3, 11 and 16–19.

## 3. The live-track gate and the Track keys

### 3.1 The mask the voices are gated on

The note-start path checks a 32-bit bitmask before sounding a voice
(firmware fact):

```
$C0FF42  movel $FFFFD718,%d0      ; live-track mask
         (swap or lsr by 16)      ; pick the lower/upper 16-bit half
         andw  %d0,%d3            ; the bit for this track
         bne   -> note on
         trap #4 ; rts            ; bit clear -> the note is silenced
$C18BAC  movel $FFFFD718,%d0 ... btst ... beq -> trap #4   ; DSP allocator
```

`$FFFFD718` is **the live on/off state** — bit (track−1) = audible. It
is read again at `$C0FFB2`, `$C10088`, `$C100B8` and by the sequence
player. It is separate from the stored 120-byte track record: the track
"status" byte in the preset (BOTH/LOCAL/MIDI/OFF) is MIDI routing, and
the owner verified by ear that setting it to OFF does not silence
anything, even after a reload.

### 3.2 How the live state is kept — the pile

The live on/off is materialised as the **pile**, an eight-byte list of
live/layered track numbers at `$FFFFD6E6` (pointer `$FFFFD6EA`), packed
into masks by `$C11BA2`. `$C11AEA`/`$C11B7A` add a track to the pile —
the panel's "layer a track live" operation — and the mask `$FFFFD718`
follows through `$C11A96`/`$C11A9C`. The store commit `$C12910` writes
the pile into preset bytes 144–151, which is why the pile *looks*
stored; but the live mask is RAM state the commit never writes back
into, so a dump cannot change it.

Known entry points that call the pile-add (firmware fact): the
Select Sound action (key 4 → `$C0BC74` → `$C11962` → `$C11AEA`), the
sound-audition confirm `$C0F404`, and the YES/NO confirm path
(`$C0BDA8` → `$C11B7A`, `$C0BDD6` → `$C11AEA`).

### 3.3 What is not the gate

`$FFFFDC83` is an eight-byte per-track **note-activity counter**,
incremented on Note On (`$C1118A`) and decremented on Note Off
(`$C11158`), cleared by All Notes Off; the track-status row displays it.
It drives the display, not the audio.

## 4. Verdict for the application

* **Navigation is bidirectional**: button 5 = Down (previous preset) and
  7 = Up (next preset), mod 80; buttons 6/8 (Left/Right) move the digit
  under the cursor (the preset's tens digit on the display). Any slot is
  at most 40 one-slot presses away — or far fewer with the tens digit —
  and a "reload" (leave and return) is two presses (`select_preset_by_walk`
  with `force_reload=True`).
* **A dump cannot change the live on/off**: it stores tracks and pile
  but never writes `$FFFFD718` (and the stored status/pile do not gate
  audio — ear-verified).
* **The live toggle IS drivable over SysEx.** The two Track keys are
  buttons 10 (left) and 12 (right); pressed **together** they toggle the
  track under the panel's cursor — the display's number turns into "-"
  and the track goes silent, with no preset bytes changed. The app
  sends that chord as `SqBridge.toggle_live_mute()`. The one limit is
  visible: the panel's cursor cannot be read back, so the action
  toggles whichever track the panel is on — the app must say that, and
  the owner aligns the panel cursor first.

## Appendix — addresses

| thing | address |
|---|---|
| command-0 receive dispatch | `$C0E96C` |
| button → soft-key post (press/release) | `$C0FDF4` / `$C0FE14` |
| arrow → sequencer post | `$C0FE32` |
| UI task loop / message parse | `$C0AF0E` / `$C0AECC` |
| press/release handlers | `$C0B006` / `$C0B00C` |
| soft-key lookup / execute | `$C0B040` (reads `$FFFFDB1C`) / `$C1095C` |
| screen installer | `$C0B09E` (masks `$C1C552`, table `$C1C466`) |
| screen records (85 × 4 bytes) | `$C0AC04` … `$C0AD54` |
| main screen record / action table | `$C0AC20` / `$C0AA44` |
| live-track mask (audio gate) | `$FFFFD718` (read at `$C0FF42`, `$C18BAC`) |
| pile (live layer list) | `$FFFFD6E6` (pointer `$FFFFD6EA`) |
| note-activity counter | `$FFFFDC83` |
| store commit | `$C15964` (copies 120 bytes, offsets 32–151) |
