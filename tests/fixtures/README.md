Captured from a real SQ-R Plus on 2026-10-20 (base channel 11).
single_seq_default.bin: alert(cmd 06) size 0x30 (48); dump = 48-byte sequence-data blob + 168-byte preset (factory 'Preset-0').
all_seq.bin: alert(cmd 07) size 0x10D4 (4308), count 0x50 (80); dump 17977 bytes; 80 presets at (len - 21 - 168*count); 21 trailing global bytes.
Tests must assert structure only, never sound or preset NAME contents.
