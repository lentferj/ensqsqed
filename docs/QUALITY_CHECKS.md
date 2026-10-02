<!--
SPDX-License-Identifier: GPL-2.0-or-later
SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
-->

# Development checks

What `make check` runs, what it deliberately does not, and every finding that
is currently suppressed.

The rule this setup follows: **a check that passes today and also passes on
tomorrow's broken commit is worse than no check**, because it buys confidence
it has not earned. So nothing here is switched off wholesale to make the tree
green — every suppression is counted, and each one is scoped as narrowly as it
can be while still passing on the current code.

## Running them

```sh
make bootstrap   # install the project and the pinned toolchain
make check       # lint, typecheck, test, audit
make help        # every target
```

`make bootstrap` installs `requirements-dev.txt` into `.venv`, which the
README already tells you to create with `--system-site-packages` so the system
`python-rtmidi` and `textual` are visible. `requirements-dev.txt` is the
lockfile: the tools and their whole transitive closure are pinned exactly.

| Target | Tool | What it does |
|---|---|---|
| `lint` | ruff | `ruff check` plus `ruff format --check` |
| `format` | ruff | `ruff format`, rewriting files |
| `typecheck` | mypy | via `quality/check_mypy.py`, against a baseline |
| `test` | pytest + pytest-cov | 74 tests, coverage with `term-missing` |
| `audit-vulns` | pip-audit | runtime deps + the pinned toolchain |
| `audit-dead` | vulture | unused code at confidence ≥ 80 |
| `audit-deps` | deptry | unused / missing / misplaced dependencies |
| `audit-secrets` | detect-secrets | against `.secrets.baseline` |

`pre-commit install` wires up ruff-check, ruff-format, mypy and
detect-secrets, with every hook rev pinned to a released tag.

## pip-audit audits declared dependencies, not the environment

`.venv` is created with `--system-site-packages`, so `pip-audit --local` sees
every package installed on the host — several hundred of them, including the
host's own `pip` and `setuptools`. Auditing that produces a wall of findings
about software this project has nothing to do with, and the real signal (are
`textual` and `python-rtmidi` safe?) is lost in it.

So `make audit-vulns` audits two things: the runtime dependencies read out of
`pyproject.toml`, and the pinned toolchain in `requirements-dev.txt`.

## mypy has a baseline

mypy has no baseline of its own, and both built-in alternatives are worse:

- `ignore_errors` per module hides **new** errors of any kind in that module,
  which defeats the point of running a type checker at all.
- Per-module `disable_error_code` hides new errors of a whole category, and
  cannot tell "this was always there" from "this is new and happens to share a
  code".

`quality/check_mypy.py` records each existing finding as
`path | code | message` and fails only on a finding whose key is not on the
list. Line numbers are deliberately excluded, so moving code around does not
break it — a baseline that breaks on every unrelated edit gets deleted within
a week.

A finding that *disappears* is reported as stale but does not fail. Fixing
things should be easy; only new breakage should be loud.

```sh
make typecheck            # fail on anything not already known
make typecheck-baseline   # accept the current output as the new baseline
```

**Strictness.** The project runs non-strict with four flags that cost nothing
today: `warn_unused_ignores`, `warn_redundant_casts`, `no_implicit_optional`
and `warn_unreachable`. `sq/catalog.py` and `sq/messages.py` are clean under
`--strict`, so they are held to it; the rest of the codebase is not, and
dialling it in means editing annotated source, which is out of scope for a
tooling pass.

The `strict = true` bundle cannot be used in a per-module override here: in
mypy 2.4.0 it leaks out of the override and applies to the whole run, turning
10 findings into 157. The equivalent flags are therefore listed individually
under `[[tool.mypy.overrides]]`.

## vulture needs a whitelist as a path

vulture 2.x removed `--whitelist`. A whitelist is supplied by passing the file
among the scanned paths, so `quality/vulture_whitelist.py` is on vulture's
command line in the Makefile. That makes the file real Python, so ruff lints
it too; hence the `noqa` on its one line.

## Formatting is enforced

`ruff format --check` runs as part of `make lint`, so `make check` fails on
any file that is not `ruff format` clean, and the `ruff-format` pre-commit
hook rewrites one on the way in. The whole tree was brought into line in the
commit that cleared the original deferral; there is no exclusion list and
none should be added.

`make format` is the fixer for when the check complains; it is deliberately
not part of `make check`.

## shellcheck and shfmt are not wired

This repository contains no shell scripts — no `*.sh` and no file with a
`bash`/`sh` shebang — so both hooks would never fire. They are deliberately
absent rather than idle. Add them to `.pre-commit-config.yaml` alongside the
first `.sh` file.

## Suppressed findings

Every ruff rule requested is enabled. Of the 340 findings ruff reports on the
tree as it stands, 195 are suppressed project-wide and 145 per file. A **new**
file is checked against all of them.

### Disabled project-wide

These are properties of the whole style rather than of particular files, so a
per-file list would be longer and less honest.

| Rule | Count | Why |
|---|---:|---|
| `UP006` `UP035` `UP045` `UP037` | 117 | The project uses `typing.Dict/List/Optional` consistently under `from __future__ import annotations`. Rewriting every annotation is a refactor. |
| `PLC0415` | 11 | Local imports are deliberate: `rtmidi` is imported lazily so `sqcli groups` works with no MIDI backend; the tests import inside functions. |
| `PLR2004` | 44 | Protocol offsets, record sizes and function codes are compared as literals throughout. That is how binary formats read; naming every constant is a different project. |
| `PLR0913` `PLR0912` | 2 | Complexity and argument-count refactor suggestions, not defects. |
| `RUF022` `RUF012` | 17 | Established conventions: `__all__` ordered by protocol rather than alphabetically, and class-level constant tables. |
| `SIM105` | 2 | An explicit `try`/`except` reads better than `contextlib.suppress` at these sites. |
| `B905` | 2 | `zip(strict=)` changes runtime behaviour; enabling it is a decision, not a fix. |

### Disabled per file

Scoped to the codes listed, so **other** rules still fire in these files.

| Scope | Codes | Count | Why |
|---|---|---:|---|
| `tests/*` | `S101`, `F401` | 119 | `assert` is what a test is for; `F401` is the unused `inspect` import. |
| `tests/test_app.py`, `test_banks.py`, `test_store.py` | `I001` | 3 | Import order predates the pipeline; auto-fixable. |
| `ensqsqed/app.py` | `B904`, `F401`, `I001`, `PLW0108` | 4 | The Textual event layer. |
| `ensqsqed/cli.py` | `B904`, `C901`, `I001` | 5 | `main` is the CLI dispatcher and stays in one readable function. |
| `sq/bridge.py` | `S110`, `S112` | 3 | The deliberate teardown and drain loops swallow failures on purpose. |
| `sq/catalog.py` | `E501`, `I001` | 2 | An unsplittable docstring line, and import order. |
| `sq/config.py` | `PLW0603`, `UP031` | 2 | A module-level warn-once flag, and `"\u%04X" % ord(c)`. |
| `sq/dumps.py` | `F401` | 1 | Dead `typing` import. |
| `sq/favorites.py` | `E501`, `F401`, `RUF023`, `S608` | 5 | `E501` is an unsplittable SQL string. `S608` is `PRAGMA user_version = {SCHEMA_VERSION}`, an int constant. `RUF023` is `_Result.__slots__`. |
| `sq/messages.py` | `F401` | 1 | Dead `typing` import. |

### Other tools

| Tool | Suppressed | Where |
|---|---:|---|
| mypy | 10 | `quality/mypy-baseline.txt` |
| vulture | 1 | `quality/vulture_whitelist.py` (at confidence 80; there are 45 at 60) |
| deptry | 0 | — |
| pip-audit | 0 | — |
| detect-secrets | 0 | `.secrets.baseline` is empty of findings; one inline `allowlist` on the `audit-secrets` command line, whose text contains the word "secrets" |

### Worth fixing rather than suppressing

These are suppressed only because this pass does not edit application source.
Each is a safe, mechanical fix the owner may prefer to make:

- **Dead `typing` imports**: `Dict` in `ensqsqed/app.py`, `Sequence` in
  `sq/dumps.py`, `Iterable` and `Sequence` in `sq/favorites.py`, `Tuple` in
  `sq/messages.py`. None is re-exported through `__all__`.
- **`import inspect`** in `tests/test_store.py::TestABoolIsNotANumber._loaders`,
  unused (the neighbouring `importlib` is what the function uses).
- **Unsorted imports** in `ensqsqed/app.py`, `ensqsqed/cli.py`,
  `sq/catalog.py` and the three test modules — all auto-fixable with
  `ruff check --fix`.
- **`B904`** raises in `ensqsqed/app.py` and `ensqsqed/cli.py` that could name
  their cause with `from exc` or `from None`.

Once a suppression is no longer needed, delete the entry as well as the
finding — a baseline that only ever grows stops being read.

## CI

There is no CI configuration in this repository, so none was added. When there
is, the job is one line:

```yaml
- run: make check
```
