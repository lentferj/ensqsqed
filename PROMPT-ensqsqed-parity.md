# PROMPT: Bring ensqsqed to Feature Parity with Siblings

## Goal
Bring `ensqsqed` to full feature and UX parity with its siblings (`rxved`, `p2ked`, `emorphed`, `nanosyned`) using the latest `vinsynlib` (0.4.2) features.

## Current State Analysis

### What ensqsqed HAS (already good)
- Two-pane browser (groups left, slots right)
- Favorites with tags, notes (via vinsynlib.favorites)
- Three-view cycle (all / bank-favourites / all-favourites) on `F`
- Search (`/`)
- Channel controls (`[`, `]`, `c`)
- Read names (`r`) - only Internal group
- Device info (`i`)
- Config via vinsynlib.config
- Bridge using vinsynlib.midi.open_remembered_or_swept
- Terminology declared
- Demo device
- SQ-R specific protocol (selectors 124-127, drums 80-99)

### What ensqsqed IS MISSING (from siblings + vinsynlib)

| Feature | rxved | p2ked | emorphed | nanosyned | vinsynlib |
|---------|-------|-------|----------|-----------|-----------|
| Rating (0-5 stars) | ✓ | ✓ | ✓ | ✓ | RatingFilter type |
| Rating filter (`V`) | ✓ | ✓ | ✓ | ✓ | RatingFilterScreen |
| Category filter (`C`) | ✓ | ✓ | | | CategoryScreen |
| Undo (`z`/`Z`) | | ✓ | ✓ | ✓ | UndoLog |
| Write gate (`w`/`--allow-write`) | | ✓ | ✓ | ✓ | EditGate |
| Scan device (`S`) | ✓ | ✓ | ✓ | ✓ | ScanOfferScreen |
| Rebuild catalog (`R`) | ✓ | ✓ | | | - |
| Persistent UI state | ✓ | ✓ | ✓ | ✓ | - |
| Search with checkboxes | ✓ | | | ✓ | SearchScreen |
| Key hint: `v` for fav view | ✓ | ✓ | ✓ | ✓ | legend() |
| Demo in CLI | ✓ | | ✓ | ✓ | - |
| Live names persistence | ✓ | | ✓ | ✓ | livenames |
| Sound mode (`M`) | ✓ | | | | - |
| Multi-mode (`m`) | ✓ | | | | - |
| Effects (`x`) | ✓ | | | ✓ | - |
| Store/Performance screens | ✓ | | | | - |
| Patch picker | ✓ | | | | - |
| ConfirmScreen (vinsynlib) | ✓ | ✓ | ✓ | ✓ | ✓ |
| ReportScreen (vinsynlib) | ✓ | ✓ | ✓ | ✓ | ✓ |
| KeyHints (vinsynlib) | ✓ | ✓ | ✓ | ✓ | ✓ |
| BusyGate/close_when_idle | | | ✓ | ✓ | ✓ |
| Device ID support | | | ✓ | | - |
| CLI subcommands (spec) | ✓ | ✓ | | | spec.SUBCOMMANDS |

## Implementation Tasks (Atomic, Parallelizable)

### Phase 1: Core Infrastructure (Foundation)
1. **TASK-01**: Update `pyproject.toml` to depend on `vinsynlib>=0.4.2`
2. **TASK-02**: Switch `KEY_HINTS` to use `vinsynlib.keys.legend()` with `v` for favorites view
3. **TASK-03**: Replace local `ReportScreen`/`PromptScreen` with `vinsynlib.ui.report.ReportScreen` and `vinsynlib.ui.confirm.ConfirmScreen`
4. **TASK-04**: Replace local `KeyHints` with `vinsynlib.ui.hints.KeyHints`
5. **TASK-05**: Add `vinsynlib.ui.modals.RatingFilter`, `RatingFilterScreen`, `CategoryScreen`, `SearchScreen`, `ChoiceScreen`
6. **TASK-06**: Use `vinsynlib.ui.work.BusyGate` and `close_when_idle` instead of manual `_busy`/`_lock`
7. **TASK-07**: Use `vinsynlib.undo.UndoLog` for undo/redo (`z`/`Z`)

### Phase 2: Favorites Enhancements
8. **TASK-08**: Add rating support (0-5 stars) to favorites store + `*` key for rating, `V` for rating filter
9. **TASK-09**: Add category support to catalog + `C` key for category filter

### Phase 3: UI State Persistence
10. **TASK-10**: Persist UI state (bank index, slot cursor, view mode, rating filter, categories) to config.toml
11. **TASK-11**: Restore UI state on startup

### Phase 4: Hardware Operations
12. **TASK-12**: Add `S` key for "Scan device" (read all readable banks)
13. **TASK-13**: Add `R` key for "Rebuild catalog" (discard live names, re-read)
14. **TASK-14**: Add live names persistence (save names read from hardware between runs)

### Phase 5: CLI Alignment
15. **TASK-15**: Align CLI with `vinsynlib.spec` (add `--device-id`, `--exclusive-channel`, `--timeout`, `--yes`, subcommands)
16. **TASK-16**: Add `--demo` flag to CLI entry point
17. **TASK-16b**: Add `--allow-write` flag (for future write path)

### Phase 6: Advanced Features (SQ-R specific)
18. **TASK-18**: Add sound mode selection (if SQ-R supports it)
19. **TASK-19**: Add multi-mode setup screen (if SQ-R supports it)
20. **TASK-20**: Add effects editing (if SQ-R supports it)

### Phase 7: Testing & Conformance
21. **TASK-21**: Add conformance tests (`check_bindings`, `check_legend`, `check_flags`, `check_terms`)
22. **TASK-22**: Run full test suite, fix any regressions

---

## Subagent Assignment Strategy

| Task | Agent | Model Suggestion |
|------|-------|------------------|
| TASK-01, TASK-02, TASK-03, TASK-04, TASK-05, TASK-06, TASK-07 | explore | Beam Free / xhigh (code changes) |
| TASK-08, TASK-09 | general | MiniMax M3.1 Flash Free (Coding) |
| TASK-10, TASK-11 | general | MiniMax M3.1 Flash Free (Coding) |
| TASK-12, TASK-13, TASK-14 | general | MiniMax M3.1 Flash Free (Coding) |
| TASK-15, TASK-16, TASK-16b | explore | Beam Free / xhigh (CLI/config) |
| TASK-18, TASK-19, TASK-20 | general | MiniMax M3.1 Flash Free (Coding) |
| TASK-21, TASK-22 | explore | Beam Free / xhigh (tests) |

---

## Branch Strategy
- Create branch `feature/parity-with-siblings` from `main`
- Each subagent works on the same branch (sequential or coordinated)
- PR at the end

---

## UX Consistency Rules (from vinsynlib UX-SPEC)

1. **Key bindings**: Use `vinsynlib.keys.legend()` - shared keys in shared positions
2. **Favorites view key**: Use `v` (not `F`) - `f` writes, `v` reads
3. **Status line**: Refusals = red + bold (`#status.refused` CSS)
4. **Legend**: Wraps, never truncates (`vinsynlib.ui.hints.KeyHints`)
5. **Enter**: Never bound at app level - arrives as `RowSelected`
6. **Workers**: MIDI only on worker thread; UI updates via `call_from_thread`
7. **Busy gate**: Check on main thread BEFORE spawning worker
8. **Undo**: Single timeline for all change types (favorites, ratings, edits)

---

## Acceptance Criteria

- [ ] All `vinsynlib.conformance` checks pass
- [ ] `make check` passes (ruff, mypy, pytest, audit, vulture, deptry, detect-secrets)
- [ ] Key hints match sibling order and content
- [ ] Rating (0-5) works with `*`, filter with `V`
- [ ] Category filter works with `C`
- [ ] Undo/redo works with `z`/`Z`
- [ ] Write gate works with `w` and `--allow-write`
- [ ] Scan device works with `S`
- [ ] Rebuild catalog works with `R`
- [ ] UI state persists across restarts
- [ ] Search screen has checkboxes for names/tags/notes
- [ ] CLI matches `vinsynlib.spec` canonical flags
- [ ] Demo mode works without MIDI hardware
- [ ] All existing tests still pass

---

## Files to Modify

### Core App
- `ensqsqed/app.py` - Main browser (major changes)
- `ensqsqed/cli.py` - CLI commands (add scan, rebuild, align flags)
- `ensqsqed/entry.py` - Version check for vinsynlib 0.4.2
- `ensqsqed/demo.py` - Ensure demo works

### SQ Module
- `sq/catalog.py` - Add category support, live names persistence
- `sq/favorites.py` - Already uses vinsynlib.favorites (rating should work)
- `sq/bridge.py` - Use BusyGate, add scan/rebuild methods
- `sq/config.py` - Add UI state persistence methods
- `sq/terms.py` - Verify terminology

### Tests
- `tests/test_conformance.py` - New conformance tests
- `tests/test_app.py` - Update for new features
- `tests/test_banks.py` - May need updates
- `tests/test_store.py` - Rating/category tests

### Config/Tools
- `pyproject.toml` - Update vinsynlib version
- `tools/extract_catalog.py` - Create (from TODO #5)
- `config.toml` - May need new keys

### Docs
- `README.md` - Update key hints, features
- `docs/QUALITY_CHECKS.md` - May need updates