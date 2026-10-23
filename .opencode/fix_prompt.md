# Fix All Code Review Findings - Parallel Subagent Tasks

## Overview
8 findings across 3 files (ensqsqed/app.py, ensqsqed/cli.py, sq/catalog.py) + cleanup. Run 5 subagents in parallel.

---

## Subagent 1: App Core Fixes (Findings 1, 2, 5)
**File:** ensqsqed/app.py
**Model:** general (MiniMax M3.1 Flash Free)

### Task 1.1 - Fix Undo Logic (Finding 1)
In `_undo_apply()` (line ~1321), change:
```python
kind = payload.get("kind", "unknown")
```
to:
```python
kind = record.label
```
The `Record` dataclass from `vinsynlib.undo` uses `label` field, not `payload["kind"]`. Every `Record` pushed by the app uses `label="favourite"`, `"tags"`, `"note"`, or `"rating"`.

### Task 1.2 - Fix `i` Key Crash (Finding 2)
In `action_device_info()` (line ~1686), add exchange acquisition before spawning worker:
```python
def action_device_info(self) -> None:
    if not self._exchange.acquire(blocking=False):
        self.notify_status("device info: busy", refused=True)
        return
    self._info_worker()
```
This matches the pattern used by `action_select`, `action_read_names`, `_undo`, `action_scan_device`.

### Task 1.3 - Fix UI State Restoration & Rating Filter (Finding 5)
Three sub-fixes in `ensqsqed/app.py`:

**A)** In `on_mount()` after loading saved state (after line ~372), add:
```python
# Restore bank table cursor
if hasattr(self, "_saved_bank_index") and self._saved_bank_index is not None:
    self.query_one("#bank-table", DataTable).move_cursor(row=self._saved_bank_index)
# Restore slot table cursor
if self._saved_slot_cursor is not None:
    self.query_one("#slot-table", DataTable).move_cursor(row=self._saved_slot_cursor)
```

**B)** In `action_rating_filter()` (line ~1238), change clear logic to persist:
```python
if value.stars == 0 and value.at_least:
    self.rating_filter = None
    self._fill(self._current_bank)
    self.query_one("#slot-table", DataTable).focus()
    shown = len(self._current)
    self.notify_status(f"rating filter cleared — {shown} rows")
    # Persist the cleared state
    self._save_ui_state(rating_stars=None, rating_at_least=None)
```

**C)** In `_save_ui_state()` (line ~966), handle `None` values correctly using `UNSET` sentinel:
```python
from vinsynlib.config import UNSET

# ... in _save_ui_state:
if rating_stars is not UNSET:
    changes["rating_stars"] = rating_stars
if rating_at_least is not UNSET:
    changes["rating_at_least"] = rating_at_least
```

---

## Subagent 2: CLI Fixes (Findings 3, 7)
**File:** ensqsqed/cli.py
**Model:** general (MiniMax M3.1 Flash Free)

### Task 2.1 - Fix Alias/Offline Command Handling (Finding 3)
**A)** Update `OFFLINE` set (line ~430) to include ALL aliases:
```python
OFFLINE = {
    "ports",
    "banks",
    "groups",
    "regions",
    "roms",
    "list",
    "find",
    "search",
    "resolve",
    "fav",
    "favorites",
    "tags",
    "read",
    "syx",
}
```

**B)** Update `COMMANDS` dict (line ~467) to map all aliases:
```python
COMMANDS: Dict[str, Callable] = {
    "ports": _cmd_ports,
    "banks": _cmd_banks,
    "groups": _cmd_banks,
    "regions": _cmd_banks,
    "roms": _cmd_banks,
    "list": _cmd_list,
    "find": _cmd_find,
    "search": _cmd_find,
    "resolve": _cmd_resolve,
    "fav": _cmd_fav,
    "favorites": _cmd_fav,
    "tags": _cmd_tags,
    "names": _cmd_names,
    "current": _cmd_current,
    "select": _cmd_select,
    "reach": _cmd_select,
    "read": _cmd_read,
    "syx": _cmd_read,
    "status": _cmd_status,
    "hardware": _cmd_hardware,
    "config": _cmd_hardware,
    "inquire": _cmd_hardware,
    "channels": _cmd_channels,
    "multi": _cmd_multi,
    "dump": _cmd_dump,
}
```

**C)** Remove `send` from COMMANDS (commented at line 467) or implement it. Since it's editor-only, remove it from COMMANDS for now.

### Task 2.2 - Fix `--demo` Flag (Finding 7)
In `main()` (line ~653), add demo device logic BEFORE the device connection block:
```python
if args.demo:
    from ensqsqed.demo import DemoDevice
    device = DemoDevice()
else:
    # existing device connection logic
```
Place this right after `validate_common(args)` and before `if args.command not in OFFLINE`.

---

## Subagent 3: Catalog + CLI List Fix (Finding 6)
**Files:** sq/catalog.py, ensqsqed/cli.py
**Model:** general (MiniMax M3.1 Flash Free)

### Task 3.1 - Fix Catalog Category Loading (sq/catalog.py)
In `load()` function (line ~226), add category field:
```python
entries.append(
    Entry(
        bank_id=bank_id,
        program_change=int(row["pc"]),
        name=row["name"],
        category=row.get("category"),  # Add this
    )
)
```

### Task 3.2 - Fix CLI List Key Lookup (ensqsqed/cli.py)
In `_cmd_list()` (line ~108), change from 0-based program_change to 1-based number:
```python
for item in banks.slots(entry.id):
    rows.append(
        [
            f"{item.number:03d}",
            str(item.program_change),
            str(item.bank),
            catalog.display_name(item.bank_id, item.number),  # was item.program_change
        ]
    )
```

---

## Subagent 4: Write Gate Implementation (Finding 4)
**Files:** ensqsqed/app.py, ensqsqed/cli.py, ensqsqed/gate.py (new)
**Model:** general (MiniMax M3.1 Flash Free)

### Task 4.1 - Create EditGate (ensqsqed/gate.py)
Create new file `ensqsqed/gate.py`:
```python
# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
from __future__ import annotations


class EditGate:
    def __init__(self, *, armed: bool = False) -> None:
        self.armed = armed

    def toggle(self) -> bool:
        self.armed = not self.armed
        return self.armed

    def describe(self) -> str:
        return "write gate ARMED" if self.armed else "write gate locked"

    def refuse(self, what: str) -> str:
        return f"{what} not sent: the write gate is locked. Press w to arm it, or start with --allow-write."
```

### Task 4.2 - Add Write Gate to App (ensqsqed/app.py)
**A)** Add import and instance in `EnsqsqedApp.__init__`:
```python
from ensqsqed.gate import EditGate

# ... in __init__:
self._write_gate = EditGate(armed=allow_write)
```
Add `allow_write: bool = False` parameter to `__init__`.

**B)** Add `action_toggle_write_gate`:
```python
def action_toggle_write_gate(self) -> None:
    self._write_gate.toggle()
    self.notify_status(self._write_gate.describe())
```

**C)** In `main()` (line ~1883), pass `allow_write=bool(getattr(args, "allow_write", False))` to app constructor.

### Task 4.3 - Add `--allow-write` to CLI (ensqsqed/cli.py)
In `build_parser()` call to `add_common_arguments()` (line ~493), add:
```python
allow_write = (True,)
```

---

## Subagent 5: Cleanup & Quality (Finding 8)
**Files:** Makefile, .gitignore, git tracking
**Model:** explore (Beam Free / xhigh)

### Task 5.1 - Fix Makefile (Remove `tools` from SRC)
In `Makefile` line 29, change:
```makefile
SRC         := sq ensqsqed tests quality
VULTURE_SRC := sq ensqsqed tests
```

### Task 5.2 - Update .gitignore
Add to `.gitignore`:
```
sq/data/*.livenames
PROMPT-ensqsqed-parity.md
```

### Task 5.3 - Remove tracked files from git
```bash
git rm --cached sq/data/catalog.livenames
git rm --cached PROMPT-ensqsqed-parity.md
```

---

## Execution
Run all 5 subagents in parallel. Each subagent should:
1. Make its changes
2. Run `make lint && make typecheck` to verify
3. Report completion

After all complete, run full test suite: `.venv/bin/python -m pytest -v --tb=short`

---

## Expected Outcomes
- All 8 findings resolved
- `make check` passes
- All 114 tests pass
- `sqcli groups`, `sqcli regions`, `sqcli search`, `sqcli syx`, `sqcli --demo` work
- `z`/`Z` undo works in browser
- `i` key shows device info without crash
- Rating filter clear persists
- UI state restores on startup
- Category filter works (`C` key)
- `sqcli list` shows correct names
- `make lint` passes (no `tools` directory error)