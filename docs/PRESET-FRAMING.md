# Preset Framing — `all_seq.bin` layout

This document is the byte-accurate map of `tests/fixtures/all_seq.bin` (17977 bytes,
reply type 05 to command 04, captured 2026-10-20 from a real Ensoniq SQ-R Plus).
It is the contract the `sq/presets.py` / `sq/bridge.py` parsers implement against.
Every numeric claim below has a "How to check" line; all were verified against the
fixture by ` /tmp/opencode/sqrex/verify_layout.py`.

Spec references: SQ-1 SysEx §3.1.7/3.1.8 (alerts), §4.6 (single sequence), §4.7
(all sequences), §4.8/§4.8.1/§4.8.2 (preset + track), §5.1/§5.2 (effects),
Notes 3/5/6/7. Rack semantics: German SQ-R Plus manual Part 2 (system bank) and
Part 3 (presets), Part 8 (SysEx storage).

Status legend: **verified** = checked against the fixture; **empirical** = observed
once on this unit, not guaranteed by spec; **unverified** = best guess, single
data point.

---

## 0. Alert 07 (preceding the dump)

File: `/tmp/opencode/sqrex/all_seq.bin.alert` = `07 00 00 10 D4 50` (6 bytes:
command byte + 5 payload bytes per §3.1.8).

| Field | Bytes | Value | Meaning (spec §3.1.8) |
|---|---|---|---|
| command | 07 | 07 | All Sequence Memory Dump Alert |
| size (4 bytes) | 00 00 10 D4 | 0x000010D4 = 4308 | "size of the sequence data segment" |
| count | 50 | 0x50 = 80 | number of defined sequence presets [2..100] |

**verified** — `python3 -c "d=open('/tmp/opencode/sqrex/all_seq.bin.alert','rb').read(); print(d[1:].hex(), d[5])"` → `000010d4 80`.

Empirical note: 4308 = 408 + 3900 = (alloc vars 8 + track table 400) + (track-data
payload size). I.e. the alert's "size" is the byte offset where the track-data
region ends / the preset table's alloc vars begin, not the raw payload size.
The raw track-data payload is 3900 bytes (see §2). The parser MUST NOT use the
alert size to locate the preset area; use the count (last byte) only.

---

## 1. Top-level layout (one-line rule)

```
preset_area_start = len(dump) - 21 - 168 * count
```

where `count` is alert 07's last byte (0x50 = 80 here). For this fixture:
`17977 - 21 - 168*80 = 4516` **verified**.

Full map:

| Offset range | Size | Contents |
|---|---|---|
| `[0:8]` | 8 | Track-data table alloc vars (empirical) |
| `[8:408]` | 400 | Track-data offset table: 100 × big-endian 32-bit |
| `[408:4308]` | 3900 | Track data: 80 sequence-data blobs (70 × 48 B + 10 × 54 B) |
| `[4308:4316]` | 8 | Preset table alloc vars (empirical) |
| `[4316:4516]` | 200 | Preset word table: 100 × big-endian 16-bit |
| `[4516:17956]` | 13440 | 80 presets × 168 B (contiguous) |
| `[17956:17977]` | 21 | Global parameters |

How to check: `python3 -c "
d=open('tests/fixtures/all_seq.bin','rb').read()
print(len(d), len(d)-21-168*80, 4516+80*168)"` → `17977 4516 17956`.

---

## 2. Track-data table (`[8:408]`)

100 big-endian 32-bit entries. **80 are non-zero** (indices 0..79); entries
80..99 are zero. **verified** — `sum(1 for i in range(100) if struct.unpack('>I',d[8+i*4:8+i*4+4])[0])` = 80.

Stride between consecutive used entries is 48 bytes for entries 0..69 and 54 bytes
for entries 70..79 (each blob is self-describing via its leading size long-word).

### Constant C (track table)

**C = 0.** Table entry value + 0 = absolute offset of that blob in the dump.
Verified on all 80 non-zero entries: every entry points at a location whose first
4 bytes are a plausible size (48 or 54) and `offset + size <= 4308`.

How to check:
```
python3 -c "
import struct
d=open('tests/fixtures/all_seq.bin','rb').read()
tbl=[struct.unpack('>I',d[8+i*4:8+i*4+4])[0] for i in range(100)]
bad=0
for v in tbl:
    if v==0: continue
    sz=struct.unpack('>I',d[v:v+4])[0]
    if not (1<=sz<=4308 and v+sz<=4308): bad+=1
print('used=',sum(1 for v in tbl if v),'bad=',bad)
"   # -> used= 80 bad= 0
```

Empirical note on the spec wording: §4.7 says entries are "based on the beginning
of track data minus 8 (i.e. the beginning of the Memory Allocation Variables for
this table)". The fixture resolves this to a base of 0 (the alloc vars at file
offset 0): entry value is already an absolute file offset. The alternative bases
(8, 400, 408, 416) give 0/80 valid blobs. **Believe the fixture: C = 0.**

---

## 3. The 48/54-byte sequence-data blob

Spec §4.6/§4.7: "The sequencer data format is not currently documented" — we
record only what the fixture shows. The blob begins with its own length as a
big-endian 32-bit long-word (spec's "Data Size" convention).

`tests/fixtures/single_seq_default.bin` (216 bytes): alert `06 00 00 00 30 00`
(size 0x30 = 48, flag 0 = sequence). Layout: `[48-byte blob][168-byte preset]`.
The blob is `00 00 00 30` followed by 44 zero bytes — i.e. an empty sequence.

In `all_seq.bin`:

- **70 blobs are 48 bytes**: `00 00 00 30` + 44 zero bytes. (empty sequences)
- **10 blobs are 54 bytes**: `00 00 00 36` + 40 zero bytes +
  `00 00 00 30 00 00 00 06 80 e9` (the last 10 bytes are identical in all 10).

Full 54-byte blob (all 10 identical):
```
00 00 00 36  00 00 00 00  00 00 00 00  00 00 00 00
00 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00
00 00 00 00  00 00 00 00  00 00 00 30  00 00 00 06
80 e9
```
How to check:
```
python3 -c "
import struct
d=open('tests/fixtures/all_seq.bin','rb').read()
tbl=[struct.unpack('>I',d[8+i*4:8+i*4+4])[0] for i in range(100)]
from collections import Counter
print(Counter(struct.unpack('>I',d[v:v+4])[0] for v in tbl if v))
"   # -> Counter({48: 70, 54: 10})
```

The blobs at table indices 70..79 are the 54-byte ones and are referenced by
preset slots 0..9 (whose `seq` field = 70..79). The 48-byte blobs (indices 0..69)
are referenced by preset slots 10..79. **verified**.

No further field-level meaning is assigned (undocumented).

---

## 4. Preset word table (`[4316:4516]`)

100 big-endian 16-bit entries. **80 are non-zero** (entries 0..79); entries
80..99 are zero. **verified**.

### Constant C (preset table)

**C = 4308.** Entry value + 4308 = absolute offset of the referenced 168-byte
preset. Verified on all 80 non-zero entries: each result lands inside the preset
area at `4516 + 168*k` for a unique `k` in 0..79.

How to check:
```
python3 -c "
import struct
d=open('tests/fixtures/all_seq.bin','rb').read()
pt=[struct.unpack('>H',d[4316+i*2:4316+i*2+2])[0] for i in range(100)]
nz=[v for v in pt if v]
print('nonzero=',len(nz))
hits=sum(1 for v in nz if (v+4308-4516)%168==0 and 0<=(v+4308-4516)//168<80)
print('hits=',hits)
"   # -> nonzero= 80 hits= 80
```

Semantics: entry `i` is the preset offset for sequence/preset slot `i`. Non-zero
means that slot is defined. The mapping is a cyclic shift by 10:
entry 0..69 → preset slots 10..79; entry 70..79 → preset slots 0..9 (because
those 10 presets carry `seq` 70..79). This is consistent with the preset's own
`seq` field (byte 0 of the preset) and the 54-byte blobs above. **verified.**

Alloc vars at `[4308:4316]` = `00 00 35 50 ff ff e0 7c` (empirical, not
interpreted).

---

## 5. Alloc vars `[0:8]`

`00 00 5b 6a 00 00 00 15` (empirical). The trailing byte `0x15 = 21` equals
the global-block size; treat as empirical, not spec fact. `0x5b6a = 23402` not
interpreted. **verified bytes only.**

---

## 6. The 168-byte preset (§4.8.2)

Offsets within a preset:

| Offset | Size | Field | Notes |
|---|---|---|---|
| 0 | 1 | Sequence Number | 0..99; which sequence blob this preset uses |
| 1 | 1 | Sequence Flags | |
| 2 | 16 | Name | ASCII, low 7 bits; bit 7 is the unit's fixed decoration (first 8 set, rest clear), **not** a mute flag; display = byte & 0x7F |
| 18 | 2 | Bar Length | big-endian 16-bit, 0..999 |
| 20 | 2 | Time Signature | 16-bit |
| 22 | 4 | Edit Start Point 1 | preserve verbatim |
| 26 | 4 | Edit Start Point 2 | preserve verbatim |
| 30 | 1 | Tempo | 25..250 (0 observed on 10 empty presets) |
| 31 | 1 | Number of Song Steps | 0..255 |
| 32 | 112 | Tracks 1..8 | 8 × 14 bytes (§4.8.1) |
| 144 | 8 | Track Pile | Note 6: ordered layer/selection list (byte 0 = first entry). **Not the track ON/OFF**; pressure 3 in track byte 8 is OFF. |
| 152 | 11 | Preset Effect | 10 bytes + effect number byte (§5.1) |
| 163 | 3 | Size of Sequence | 24-bit; preserve |
| 166 | 2 | Reserved | preserve |

Total 168. **verified** (single fixture and all 80 presets decode without error).

Name field: bit 7 is the unit's fixed decoration — all 80 presets have the first
8 name bytes with bit 7 set and the rest clear (`Preset-NN       `). It is **not**
a track mute flag; low 7 bits are the name (`bytes & 0x7F`). Never strip or alter
bit 7 on round-trip. A dump never stores this field (the commit copies 32-151);
renaming on the unit is a panel/name-page act, not a SysEx dump.

How to check (single fixture):
```
python3 -c "
d=open('tests/fixtures/single_seq_default.bin','rb').read()
p=d[48:48+168]
print('len',len(p))
print('name raw',p[2:18].hex())
print('name disp',bytes(b&0x7F for b in p[2:18]))
print('edit1',p[22:26].hex(),'edit2',p[26:30].hex())
print('seqsize+reserved',p[163:168].hex())
"   # -> len 168, edit1 00000000, edit2 00000000, seqsize+reserved 0000000000
```

### Track byte 8 (§4.8.1) — verified bit positions

```
byte8 = (midi_status << 6) | (pressure << 4) | (midi_channel & 0x0F)
```
- bits 7-6: MIDI Status — 0=BOTH, 1=LOCAL, 2=MIDI, 3=*EXT*
- bits 5-4: Pressure — 0=NONE, 1=KEY, 2=CHANNEL
- bits 3-0: MIDI Channel (0-based stored, 1..16 displayed)

**verified against all 640 tracks (80 presets × 8):**
- status (>>6) is 1 (LOCAL) for all 640 tracks.
- pressure ((>>4)&3) is 1 (KEY) for all 640 tracks.
- channel (&0x0F) is 0..7 for tracks 1..8 (displayed 1..8), plus 10/11 on two
  tracks (the unit's base channel 10/11, 0-based/1-based).

How to check:
```
python3 -c "
import collections
d=open('tests/fixtures/all_seq.bin','rb').read()
ps=4516
st=collections.Counter(); pr=collections.Counter(); ch=collections.Counter()
for k in range(80):
    p=d[ps+k*168:ps+(k+1)*168]
    for t in range(8):
        b=p[32+t*14+8]
        st[b>>6]+=1; pr[(b>>4)&3]+=1; ch[b&0x0F]+=1
print('status',dict(st),'pressure',dict(pr),'channel',dict(ch))
"   # -> status {1:640} pressure {1:640} channel {0:79,1:79,...,7:80,10:1,11:1}
```

### Track OFF resolution (UI-critical)

The fixture does **not** exercise BOTH/MIDI/*EXT* or NONE/CHANNEL — every track has
status=LOCAL and pressure=KEY. The owner's **P20–P23 matrix (2026-10-25)** does
settle where the OFF state lives:

| Preset | Byte 8 (t1…t8) | Pressure | Device |
|---|---|---|---|
| P20 | `70 51 72 53 74 55 76 57` | 3,1,3,1,3,1,3,1 | `-2-4-6-8` |
| P21 | `50 71 52 73 54 75 56 77` | 1,3,1,3,1,3,1,3 | `1-3-5-7-` |
| P22 | `70 71 72 73 54 55 56 57` | 3,3,3,3,1,1,1,1 | `----5678` |
| P23 | `50 51 52 53 74 75 76 77` | 1,1,1,1,3,3,3,3 | `1234----` |

The pile is `01 00 00 00 00 00 00 00` in **all four**. So:

- **Pressure 3 is the stored OFF marker** (the panel's `-`); pressure 1
  (KEY) is the printed track number.
- The pile is the separate layer/selection list, not the on/off state.
- The status byte is LOCAL throughout; `*EXT` is MIDI routing, not the audio
  gate.
- The live audio gate is the RAM mask `$FFFFD718` (`docs/KEY-MAP.md` §3).
  The stored marker is what a preset carries; the mask is what plays now.

The editor's `space` toggles pressure 1 ↔ 3, and `ctrl+s` stores the buffer
with the unit's Enter/Save ×3. The Track-pair chord (`l`) is the separate live
mask control. See `ensqsqed/multi.py::MultiScreen.action_toggle_track` and
`action_live_mute`.

**The layered list is compact (bench, 2026-10-21).** Bytes 1..7 are an *ordered
list* of the layered track numbers, packed to the left — not one slot per track
number. Sending `01 00 03` (track 3 layered, a hole where track 2 would sit) is
read back as `01 03 00`: the unit compacts it. A write that leaves a hole
therefore fails its own read-back comparison, which is why switching off a track
below another layered track looked like it did nothing (the app restored the
cell). `Preset.set_active` now removes the number and shifts the rest left, and
appends when switching a track on. The name field's bit 7 is **not** usable as a
per-track mute: the unit rewrites those bits on receipt (`d0 f2 …` came back
unchanged after clearing one), so this project neither reads nor writes them.

### Other track bytes (verified values)

| Byte | Spec field | Fixture value (all 640 tracks) |
|---|---|---|
| 0-1 | Sound Number (Note 5) | 0x0000..0x0007 (INT sounds 0..7) on default presets |
| 2 | Volume (0..127) | 127 |
| 3 | Pan (-128..127; -128=SOUND) | 128 (-128 = SOUND) |
| 4 | Timbre (0..127) | 0 |
| 5 | Release (-64..63) | 0 |
| 6 | reserved (spec) | 0x80 in all 640 tracks — preserve verbatim; meaning unknown |
| 7 | Output (0..4 DRY/FX1/FX2/VOICE/CONTROL) | 4 (CONTROL) |
| 9 | Sustain (bit 7) packed with MIDI Program (bits 6..0) | 0x80..0x87 (sustain ON, program 0..7) |
| 10 | Key Range Low (21..108) | 21 (A0) |
| 11 | Transposition (-59..59) | 0 |
| 12 | Key Range High (21..108) | 108 (C8) |
| 13 | reserved | 0 |

How to check byte 6 / 9 etc.:
```
python3 -c "
import collections
d=open('tests/fixtures/all_seq.bin','rb').read(); ps=4516
c=collections.Counter()
for k in range(80):
    p=d[ps+k*168:ps+(k+1)*168]
    for t in range(8):
        tr=p[32+t*14:32+(t+1)*14]
        c[tr[6]]+=1
print('byte6',dict(c))
"   # -> byte6 {128: 640}
```

---

## 7. Global parameters (last 21 bytes)

`[17956:17977]` = `00 00 00 00 10 d4 7f 00 02 00 02 02 02 01 00 00 01 00 00 00 35`
(21 bytes). **verified bytes.**

The spec (§4.7) and the German manual (Part 8) do **not** document the field
layout of this block. The block is also **not a dump of the System bank**:
on 2026-10-27 the unit was changed from MULTI to POLY and from base channel
10 to 11, the all-presets dump was read again, and all 21 bytes were
unchanged. In particular byte 8 is not MIDI Mode and byte 13 is not Base
Channel; those earlier candidates are refuted. The manual Part 2 documents
the system-bank parameters (MIDI Mode OMNI/POLY/MULTI/MONO A/MONO B; Base
Channel 1..16; Retransmit; Preset Prog; MIDI Loop/XCtrl 1..95;
Controllers/System Excl; Prog Change; All Note Off; Master Tune; Global
Bend) but gives no byte mapping. Best-effort guesses below are
single-data-point and **unverified**.

| Idx | Hex | Dec | Best guess (low confidence) |
|---|---|---|---|
| 0 | 00 | 0 | reserved / zero |
| 1 | 00 | 0 | reserved / zero |
| 2 | 00 | 0 | reserved / zero |
| 3 | 00 | 0 | reserved / zero |
| 4-5 | 10 d4 | 4308 | echoes alert size / track-data end offset (structural) |
| 6 | 7f | 127 | unknown (possibly master-tune offset or mask) |
| 7 | 00 | 0 | reserved / zero |
| 8 | 02 | 2 | **not MIDI Mode**: unchanged when the unit moved MULTI → POLY (2026-10-27); meaning unknown (other 2s at 10-12) |
| 9 | 00 | 0 | reserved / zero |
| 10 | 02 | 2 | unknown (same value as idx 8) |
| 11 | 02 | 2 | unknown (same value as idx 8) |
| 12 | 02 | 2 | unknown (same value as idx 8) |
| 13 | 01 | 1 | **not Base Channel**: unchanged when the base channel moved 10 → 11 (2026-10-27); meaning unknown |
| 14 | 00 | 0 | reserved / zero |
| 15 | 00 | 0 | reserved / zero |
| 16 | 01 | 1 | candidate: Preset Prog / Retransmit / a boolean flag |
| 17 | 00 | 0 | reserved / zero |
| 18 | 00 | 0 | reserved / zero |
| 19 | 00 | 0 | reserved / zero |
| 20 | 35 | 53 | candidate: XCtrl controller number (range 1..95) — unverified |

How to check bytes:
```
python3 -c "
d=open('tests/fixtures/all_seq.bin','rb').read()
g=d[-21:]
print(g.hex())
for i,b in enumerate(g): print(i,b)
"   # -> 00 00 00 00 10 d4 7f 00 02 00 02 02 02 01 00 00 01 00 00 00 35
```

No field-level claims here should be treated as spec fact. The parser should
preserve these 21 bytes verbatim on round-trip.

---

## 8. Effect block (preset bytes 152..162)

11 bytes per §5.1: 10 parameter bytes + effect number byte (byte 10 of the 10-byte
structure, i.e. preset offset 162). Six parameter sets (§5.2.1..5.2.6) selected by
effect number (0..12). Modulation sources (§5.1.2). Effect number mapping
(§5.1.1): 0 CONCERT HALL … 12 CMPRSS+DIST+VERB. The fixture's 80 presets all
carry effect bytes `38 06 20 10 25 00 8f 54 20 20 00` (effect number 0 =
CONCERT HALL). **verified bytes only.**

---

## 9. Edit-buffer semantics (T2, probed 2026-10-20)

What a preset dump *does* to the unit, measured on the bench with
`tools/probe_preset_write.py`. Everything in this section is **empirical**:
one unit, one day, three runs with the same result. The manual says what the
unit does with a received preset (Part 8, "Empfang von MIDI Sys-Ex Daten":
the display shows "Press ENTER to save new preset", the preset becomes the
**Edit Preset**, the unit switches to Preset Select Mode with the Edit Preset
LED blinking) but not what a dump request returns while that Edit Preset is
live. That is the gap this section closes.

### 9.1 The write path that works

A preset is received as the specification's two-message handshake — alert,
ACK, then the dump (§3.1.7, §3.2.1, §3.3.3) — and the unit takes it:

| step | message | bytes on the wire |
|---|---|---|
| 1 | command 06, Single Sequence Dump Alert | 6 head + 12 payload + 1 tail |
| 2 | error message 01, code 01 = ACK | 6 head + 2 payload + 1 tail |
| 3 | message type 04, Single Sequence Dump | 6 head + 432 payload + 1 tail |

The alert's body de-nibbles to `06 00 00 00 30 00`: command byte 06, the
sequence-data size as a big-endian long word (0x30 = 48), and the
sequence/song flag (0x00 = sequence). **The size counts the sequence-data
segment only** — the 168-byte preset is not in it — so the alert carries
`len(seq_blob)`, not the length of the whole dump. The type-04 message
carries all 216 bytes nibble-ized: `00 00 00 30 …` (48 bytes of sequence
data) followed by the 168-byte preset, which is the same order the unit
itself sends them in.

### 9.2 The ACK, and how fast it comes

**The ACK arrives, every time, in about eleven milliseconds.** Nine
handshakes over three runs: 0.011 s each. No NAK was ever observed, and
never silence inside the window.

The one-second window of §3 ("If the receiver does not respond within one
second, the transmitter will send the dump message anyway") exists for
*dumb* SysEx recorders that cannot answer. This unit is not one: it is
ready for the dump eleven milliseconds after the alert, which is about 1%
of the window. A writer may therefore wait a full second for the ACK and
still be prompt in practice; one tenth of a second would do.

The ACK is about the *message*, not about a change: the unchanged snapshot
was ACKed in the same eleven milliseconds as the edit.

### 9.3 What the read-back returns: the Edit Preset

| question | answer |
|---|---|
| Did the unit ACK the alert? | **yes**, in ~11 ms, every time |
| Does command 03 after a write return the stored slot or the working preset? | **the working preset** — the edited version while an edit is live (§9.6) |
| Is a one-byte change visible in the read-back? | **yes** |
| Does the restore come back clean? | **yes**, byte-identical |

The measurement, in order:

1. **Snapshot** — command 03 returned 216 bytes, preceded by the alert
   `06 00 00 00 30 00`; the blob's own long word said 48, and 48 + 168 = 216
   held. The bytes are the committed fixture
   `tests/fixtures/single_seq_default.bin`, byte for byte.
2. **Send unchanged** — alert → ACK (0.011 s) → the same 216 bytes. The
   read-back was byte-identical to the snapshot. *On its own this proves
   nothing*: an unchanged send cannot tell the stored preset from the Edit
   Preset, because they are the same bytes.
3. **Send with one byte changed** — track 1's volume (preset offset 34, dump
   offset 82) went from 127 to 126. The read-back returned **126**, and the
   other 215 bytes were exactly what had been sent.
4. **Restore** — the original 216 bytes were sent again; the read-back was
   byte-identical to the original. An independent read after the run also
   matched the committed fixture exactly, with track 1's volume back at 127.

Step 3 is the decisive one. **No button was pressed on the unit, so
nothing could have been stored** — storing needs ENTER on the front panel.
The read-back therefore returned the *working preset* — the edited version,
not the stored slot. Command 03 reads whatever the unit is working with;
when an edit is live, that is the edit (§9.6).

### 9.4 What this means for the app

* **Apply-then-verify is meaningful.** `send_current_preset()` followed by
  `read_current_preset()` compares the bytes that were sent, and a
  mismatch is a real failure rather than a formality. The bridge method
  that does it is `SqBridge.send_preset()` (also spelled
  `send_current_preset()`): it speaks the alert → ACK → dump handshake,
  waits the specification's one second for the ACK, and then re-reads and
  compares byte for byte.
* **The verification does not mean the preset is stored** — **superseded
  by §9.7 (2026-10-22)**: a send via the spec's exchange *is* stored in
  the currently selected slot's memory; the display's *Edited Preset* is
  transient and no panel ENTER/SAVE is needed for a dump. The read-back
  rule itself stands (command 03 returns the working preset).
* **A read taken before any write is the stored preset; a read taken after
  one is the working preset** — the edited version (§9.6). An editor that
  reads once and writes later must read again to see its own change — the
  two reads are not the same thing, even though the request is the same.
* **The undo record can hold the whole dump.** Re-sending the previous
  216 bytes restores the previous state exactly, as measured above.

### 9.5 The unit stops playing MIDI while the Edit Preset is open (2026-10-21) — **superseded in part, later the same day; superseded by §9.7 (2026-10-22) on stored memory**

Measured on the bench, with the unit in MULTI mode and its audio captured:

> **Status of this subsection.** The *measurements* below are kept as the
> record they are. The *conclusion* they were used for — that a dump leaves
> the unit deaf to incoming MIDI for good, and that a preset edit is audible
> only after ENTER/SAVE — **is withdrawn**: it was confounded by the one-way
> `*EXT*` latch experiments that ran before it. §9.6 has the clean
> measurement and the corrected account.

- With a track in the pile, LOCAL and on channel 12, a note on channel 12
  sounds (RMS ≈ 50); track 1 on the base channel also sounds.
- **One preset dump — even the preset that is already there, byte for
  byte — silences every incoming note**, base channel included (RMS ≈ 7,
  the noise floor). It was still silent 44 s later.
- MIDI Start, Stop, Continue, Song Select, clock, a program change, CC 123,
  All Sound Off and a System Reset did **not** bring playback back.
- Reading the preset back after the dump did not either. The front panel
  did (the unit was found sounding again after a panel action).

**What survives of this.** The dump does interrupt incoming MIDI for a while
(§9.6 measures how long: about five seconds, and the unit is back by about
thirty-five). What does **not** survive is the permanence: the unit recovers
by itself, and no MIDI message is needed to make it do so.

**What does not survive either** is the reading of this run as a property of
the *dump*. The run came after the `*EXT*` latch experiments, and the latch
— a track whose status was set to `*EXT*` and could not be brought back by a
later dump — silences the receiver on its own. The silence measured here was
therefore the latch, not the dump, and the two were not separated. §9.6
separates them.

**And the "*EXT* off is confirmed" paragraph below is withdrawn with it.**
With a preset whose track 2 is out of the pile and `*EXT`, the panel-left
state was silent on channel 12 while track 1 sounded. That is true of the
`*EXT*` latch — it does silence the track — but it was read as the dump's
doing, and it says nothing about whether a dump is audible. The `*EXT*`
status is a **one-way latch in the live map**: a later dump that restores
LOCAL does not bring the receiver back. That is why the MULTI editor writes
the pile alone and never writes `*EXT*` to switch a track off (commit
`2b390aa`).

The status line after every send says what the send did and where the live
switch is. A write path must not claim a live change it cannot observe.

How to check: run `python3 tools/probe_preset_write.py --yes` against the
unit. The run log is written to `/tmp/opencode/sqrex/probe_write.log` and
the snapshot to `/tmp/opencode/sqrex/probe_snapshot_before.bin`; the
committed fixture is what the run must reproduce.

### 9.6 The corrected account: the send is live, the blackout is temporary (2026-10-21, second session) — **superseded by §9.7 (2026-10-22) on stored memory**

The clean test (`~/temp/tasks/h2f_deafness_test.py`, result
`~/temp/tasks/H2f_result.json`, verdict `DEAFNESS_REFUTED`) separates the
dump from the latch. It is self-restoring: the bytes it sends are exactly
the bytes the unit is already working with, so it cannot change anything.

| step | what was measured | RMS |
|---|---|---|
| reference | a note on the sounding track's own channel, before any send | **32.65** |
| +5 s | the same note, five seconds after a byte-identical no-op dump | **5.72** (the noise floor) |
| +35 s | the same note, thirty-five seconds after the dump | **29.72** |

The unit was **deaf for a few seconds and then came back by itself**. The
final read-back matched the working bytes exactly
(`final_hash_matches_working: true`).

**So the model is:**

- **The only write path is the whole-preset dump** (`SqBridge.send_preset`),
  and it is byte-verified: the bytes that go out are read back and compared.
- **The dump lands in the unit's working preset** — the *Edit Preset*
  buffer — and **it is audible**: that is the buffer the unit plays from.
- **The unit goes briefly deaf to incoming MIDI after a send** (measured:
  the noise floor at +5 s) and **recovers by about 35 seconds** without any
  message being sent.
- **Storing is a front-panel act.** No SysEx message stores a preset to a
  slot; the store is the manual's six-step dialog on the unit (ENTER/SAVE →
  confirm the name → pick the slot → ENTER/SAVE → *SAVED*), or the
  double-click on ENTER/SAVE that stores to the same slot in one step —
  **superseded by §9.7 (2026-10-22)**: a send via the spec's exchange *is*
  stored in the currently selected slot's memory, and panel ENTER/SAVE is
  not needed for a dump. The six-step dialog remains what stores *panel*
  edits.
- **The live MIDI switch for a track is the unit's front panel** (Select
  Sound, then the Track buttons). It is not reachable over SysEx — see
  `docs/BUTTON-MAP.md`.

**Two protocol facts this session settled, and they belong here:**

- **The all-dump dumps STORED memory.** Command 04 returns the eighty
  stored slots, not the unit's working preset. The E8 probe proves it from
  the other side: a preset addressed to slot 1 and named `PROBE-STORE-01`
  never appeared in any slot of the all-dump, so the all-dump does not
  mirror the edit buffer (`~/temp/tasks/H1_result.json`, `E8_NEGATIVE`).
- **Command 03 returns the WORKING preset** — the stored bytes when no
  edit exists, the edited version once one does, whether the edit came from
  a dump or from the panel. §9.3's "the Edit Preset" is this same fact
  seen from the dump side; the general wording is the one to use.

**And the bytes that do not take, restated as measurements** (§9.5's
dead ends, all with a byte-for-byte read-back behind them):

| what | what the unit did |
|---|---|
| the name bytes' bit 7 | the unit's fixed decoration (first 8 set, rest clear), **not** a mute; a whole-preset dump keeps the unit's own name (the name field is never taken from a dump) |
| track byte 6 (the reserved byte, always `0x80`) | write-protected — `0x00` reads back changed |
| the pile | a compact ordered list: the primary track first, and the unit compacts holes (a sent `01 00 03` reads back `01 03 00`) |
| preset names | left-aligned and space-padded (the app strips the padding for display) |

**Rename is dialog-side, not dump-side.** A whole-preset dump keeps the unit's
own name — the name field is never taken from a dump — so the dump path cannot
rename it; the unit's save-dialog **name page** can, and the app's `R` key in
the multi editor drives that dialog.

The one byte that drifted in the 21-byte global block between the backup and
a later read — dump index 17957, value 0 → 2 — went 0 → 2 in the same run in
which the unit's selection moved from Preset-00 to Preset-02. That is a
coincidence worth a later probe, not an established meaning.

---

### 9.7 The dump stores (2026-10-22)

Measured on the unit today, 2026-10-22, with the owner at the panel
(`~/temp/tasks/H3*_result.json`, scripts `~/temp/tasks/h3*_*.py`, ledger
`~/temp/tasks/STATE.md`, H3 sections). This supersedes §9.5 and §9.6 where
they treat a dump as never reaching stored memory: today's session
falsified that. Their measurements stand; only the conclusion changes.

**The session.** A probe preset — the backup bytes of slot 70, a marker
name, and a sentinel volume at preset byte 34 (127 → 99) — was sent with
the spec's three-step exchange (alert command 06 → ACK error code 01 →
dump type 04, `SqBridge.send_preset`). Its address bytes were `46 0b`
(slot 70) while slot 0 was selected (`H3_result.json`).

- **The display.** Right after the send the unit showed *Edited Preset*.
  The owner then moved the selection to Preset-01 and back
  (`h3d_read_away.py`, `H3d_result.json`, verdict STORED). Slot 0's entry
  in the all-dump kept the sentinel — 99 at byte 34 where the backup had
  127 — while the unit sat on P-01; back on P-00 the display showed no
  asterisk and the sentinel was still present. The content survived a
  selection change, so a send via the spec's exchange is **stored in the
  currently selected slot's memory**; no panel ENTER/SAVE was involved.
- **No per-slot addressing.** The probe's address bytes `46 0b` were
  rewritten to the selected slot's own values (`00 0a` for slot 0): slot
  0's entry took the probe's content and slot 70's did not
  (`H3_result.json`, verdict `STORED_SOMEWHERE_ELSE_[0]`). The destination
  is always the current selection.
- **The name is never taken from a dump.** The read-back and the display
  kept "Preset-00", bit-7 decoration and all
  (`H3e_result.json`, `name_took: false`).
- **The dialog's name page writes the name; a dump's name is ignored.** The
  unit's save dialog edits the name on its name page, and the app's `R` drives
  that page.
- **Preset byte 30 and tracks 2–8 channel bytes (`0x80|n`) are rewritten**
  to the unit's current values; the probe's values there did not stick.
  Sending a slot's own backed-up bytes back is therefore idempotent for
  offsets 32–151 except the eight per-track byte-9s; the unit recomputes
  those from the live engine: the restore send passed `verify=True` and
  read back byte-identical
  (`H3b_result.json`, final `slot0_eq_backup: true`, pile `0102`, track 2
  ON).
- **The all-dump reflects the selected slot's current content.** Slot 0's
  entry showed the sentinel right after the send
  (`H3c_result.json`); unselected slots reflect their stored content.

**What the commit actually writes (the firmware's answer, 2026-10-23).**
The OS 2.03 image (read from the ROM, `~/temp/sqr_re/RE_sysex.md`) names
the mechanism: the one routine that commits the unsaved-preset latch is
`$C15964`; it copies the **live engine's 120-byte track-and-pile
snapshot** into the slot named by the selection variable (`$FFFFD930`)
-- offsets **32 through 151** only (`$C12800` walks the eight 14-byte
track records, `$C12910` appends the pile). The per-track byte 9
(`sustain<<7 | program`) is saved before the copy and restored after
(`$C128FA`), and recomputed from the engine's own field (`$C127A8`) --
so **all eight** track byte-9s (offsets 41, 55, 69, 83, 97, 111, 125,
139) keep the slot's own values. **What a reselect actually stores (measured 2026-10-25).** The earlier
"kept" list for the track sound words, the track channel byte (8) and the
effect number was **not reproducible**. A controlled test on P10 sent one
change at a time and read the slot back while it was **unselected**
(command 04 mirrors the working preset while the slot is selected):

| field | sent | working | stored (unselected) |
|---|---|---|---|
| track 1 sound word (32–33) | `00 00` → `00 04` | `00 04` | `00 04` |
| track 1 channel (byte 8 at 40) | `5a` → `5b` (channel 10 → 11) | `5b` | `5b` |
| effect number (162) | `04` → `05` | `05` | `05` |
| effect param 0 (152) | `38` → `39` | `39` | `39` |

Each was restored by sending the original bytes and re-selecting, and
each came back in stored memory. So a send + reselect **does** store the
track sound, the track channel and the effect number on this unit. The
earlier result was a receiver-state anomaly of the kind already recorded
in §9.7's flakiness note, not a write-protection rule.

The only fields this project has **not** seen change from a send are the
**name** (offsets 2–17) and the **address/sequence bytes** (0–1). The
name is the unit's own; the address bytes are rewritten to the selected
slot. Everything else in 0–167 is expected to stick, and the app's
commit verifier compares the whole preset except those two regions and
names any offset that did not — no field is predeclared as "panel-only".

The 2026-10-24 byte-9 observation still stands as a warning: a flaky
send can leave the inactive tracks' byte-9s at the engine's value. The
verification is what catches it; `preset_needs_write` now treats every
byte in 32–151 as writable, so a no-op skip cannot hide such a change.


The panel's Enter/Save dialog is **not** that commit path. With effect
param `3a` live, four taps of button 9 (Enter/Save) at 1.2 s closed
the dialog (a subsequent preset walk worked), but the stored P10 effect
read back as the old `38`. In this test the save dialog reverted the
sent effect; it did not store it.

Stored-read caveat: `read_all_presets()` mirrors the *working* preset
for the selected slot, so a stored read must be taken while the slot is
unselected (select another slot, then dump).

**What stays.** The panel's ENTER/SAVE is still what stores *panel* edits;
for a dump no ENTER is needed. The display's *Edited Preset* clears by
itself — observed after leaving and returning; no exact timing is claimed.
The brief post-send deafness and recovery by about 35 seconds from
§9.5/§9.6 stand as measured (the permanent-silence reading was the *EXT*
latch, already withdrawn in §9.6). Feature A (live per-track on/off)
remains front-panel only (`docs/BUTTON-MAP.md`).

**Flakiness (open).** Acceptance is not 100 %: byte-identical hand-rolled
replicas of the exchange landed 2/2 before panel navigation and 1/10 after
it (including one mangled 160-byte probe that landed), while
`send_preset` itself landed 5/5 in the same window
(`~/temp/tasks/STATE.md`, H3 sections). Cause unknown — unit receiver
state, USB timing, something else — uninvestigated. The app-side answer:
`send_preset`'s `verify=True` read-back plus `PresetMismatch` is the
guard, and a bounded retry wraps it
(`send_preset_with_retry` next to `send_preset` in `sq/bridge.py`). See
`TODO.md` for the open investigation item.

**How to check.** Send a preset with sentinel 99 at preset offset 34
while on slot K, move the selection away and back, and read the all-dump:
slot K's entry keeps 99 at byte 34; its name and address bytes are the
unit's own. The result files above are the record of that run.

### 9.8 The Dump Everything Request (command 05), verified 2026-10-24

Measured on the unit 2026-10-24, base channel 11 (0x0a), ports ESI M4U eX
MIDI 2 out / MIDI 1 in, with `tools/probe_dump_everything.py`. A dump
request is read-only: it changes no unit state.

Request: `F0 0F 06 00 0A 00 00 05 F7` (9 bytes; the command byte is
nibble-ised; `m.command_message(0x05)` produces exactly this).

Reply, in order, ~23 s total:

| time | type | frame on wire | decoded | contents |
|---|---|---|---|---|
| +10.45 s | 0x03 All Sounds Dump | 32647 B | 32640 payload (80 × 204) | all 80 internal sounds |
| +10.46 s | 0x00 command (alert 07) | 19 B | — | size 4308, count 80 |
| +22.96 s | 0x05 All Sequences Dump | 35961 B | 35954 payload (17977 decoded) | all presets/sequences |

The type-05 decoded bytes match the known all-presets layout:
`preset_area_start` 4516, 80 slots, cyclic shift by 10, slot 0 → preset
index 10 (§1, §4). The 21-byte global block is included in the type-05
half (§7).

So command 05 is the union of command 02 (all sounds) and command 04 (alert
+ all presets/sequences): it returns the sounds, then the alert, then the
sequences. It does not include cards or ROM/drum kits. Captured frames:
type-03 32647 B, alert 19 B, type-05 35961 B. Probe:
`tools/probe_dump_everything.py`.

---

## 10. Contradictions / notes vs. spec

1. Alert 07 "size" (0x10D4 = 4308) equals the track-data end offset, not the
   3900-byte payload size. Spec §3.1.8 calls it "size of the sequence data
   segment"; the fixture shows it includes the 408-byte header. Use count only.
2. Track table base C = 0, although §4.7's "beginning of track data minus 8"
   wording suggests a non-zero base. Fixture supports C = 0 (all 80 entries valid).
3. Track byte 6 (spec "reserved") is 0x80 in all 640 tracks, not 0. Preserve
   verbatim; meaning unknown.
4. Tempo (byte 30) is 0 on 10 presets (out of spec range 25..250) — likely
   empty/uninitialized presets; parser should not reject, preserve verbatim.
5. No preset in the fixture exercises MIDI Status values BOTH/MIDI/*EXT*, Pressure
   values NONE/CHANNEL, or layered piles beyond a single primary track. Those
   code paths are unverified by this fixture.
6. The spec says preset table has "100 word entries" — confirmed (200 bytes);
   only 80 non-zero (matches alert count).
7. The single-sequence alert's size field counts the sequence-data segment
   only (48), not the whole 216-byte dump — measured, §9.1.

---

## 11. Cross-references

- `tests/fixtures/single_seq_default.bin` — single sequence dump (48-byte blob +
  168-byte preset); confirms preset layout and edit-point/seq-size fields.
- `tests/fixtures/all_seq.bin` — full dump analyzed here.
- Throwaway verification script: `/tmp/opencode/sqrex/verify_layout.py`.
- `tools/probe_preset_write.py` — the edit-buffer probe (§9); run log at
  `/tmp/opencode/sqrex/probe_write.log`.
- `tools/verify_preset_roundtrip.py` — the end-to-end round trip (below); run
  log at `/tmp/opencode/sqrex/e2e.log`.
- German manual: `/tmp/opencode/sqrplusd.txt` (Part 2 system bank, Part 3
  presets, Part 8 SysEx storage).
- English spec: `SQ1-SysEx.md` §4.6–§4.8.2, §3.1.7/3.1.8.

---

## E2E round trip (T12, probed 2026-10-21)

One live round trip through the program's **own** code path, with
`tools/verify_preset_roundtrip.py`: read the preset the unit is on, change one
value through the model, send it with `SqBridge.send_preset(..., verify=True)`,
read it back independently, restore the original bytes and read once more.
Every step is `sq.bridge` and `sq.presets` — the app's objects — and not a
second implementation of them.

| step | what | result |
|---|---|---|
| read | command 03 | **216 bytes**: a 48-byte sequence blob and a 168-byte preset |
| snapshot | written to `/tmp/opencode/sqrex/e2e_before.bin` | 216 bytes |
| edit | track 1's volume, through `Track.updated` | stored **127 → 126** (displayed 99 → 98) |
| send | `send_preset(seq_blob, preset.to_bytes(), verify=True)` | `verified=True`, `diff_offsets=()` |
| re-read | an independent command 03 | **216 bytes, identical** to what was sent; volume read back as stored 126 / displayed 98 |
| restore | the original 216 bytes, `verify=True` | `verified=True`, `diff_offsets=()` |
| final read | command 03 | **216 bytes, identical** to the snapshot — the unit is byte-for-byte as found |

**216 bytes compared at every step, and the diff offsets were empty every
time.** The edited dump differs from the snapshot in exactly one byte, at
**dump offset 82** (preset offset 34, track 1's volume) — the byte that was
changed, and no other.

Nothing was stored. The unit held the edit in its **Edit Preset** buffer for
the length of the run, and the run put the original bytes back; the final
read matches `e2e_before.bin` byte for byte.

The tool is loud about failure: a device error prints what happened, is
written to the log and exits **2** — never 1, and never silently — and the
restore is attempted on every failure path first. The preset's *name* is
masked in the log: it is the owner's own text.

Run log: `/tmp/opencode/sqrex/e2e.log`. Snapshot: `/tmp/opencode/sqrex/e2e_before.bin`.

