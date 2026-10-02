# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# ensqsqed is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 2 of the License, or (at your option)
# any later version.
#
# ensqsqed is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.

"""Fail only on mypy errors that are not already known.

mypy has no built-in baseline, and the alternatives are both worse:

* Switching the whole module off (``ignore_errors``) hides **new** errors of
  any kind in it, which defeats the point of running a type checker.
* Per-module ``disable_error_code`` hides new errors of a whole category in
  that module -- close, but it cannot tell "this one was always there" from
  "this one is new and happens to share a code".

So this records each existing finding by ``file | code | message`` and fails
only on a finding whose key is not on the list. Line numbers are deliberately
excluded: moving code up or down is not a new type error, and a baseline that
breaks on every unrelated edit gets deleted within a week.

A finding that disappears is reported as stale but does **not** fail. Fixing
things should be easy; only new breakage should be loud. When the count of
stale entries reaches zero the file can be emptied and the target still
passes.

Usage::

    python quality/check_mypy.py [--baseline PATH] [--write]
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_BASELINE = os.path.join(ROOT, "quality", "mypy-baseline.txt")

#: ``path:line: error: message  [code]`` -- note the ``[code]`` is optional for
#: syntax-level diagnostics, which is why it is captured separately.
ERROR_RE = re.compile(
    r"^(?P<path>[^:]+):(?P<line>\d+):\s+error:\s+"
    r"(?P<message>.*?)\s*(?:\[(?P<code>[a-z-]+)\])?$"
)


def key(path: str, code: str, message: str) -> str:
    """The identity of a finding, deliberately without its line number."""
    return f"{path}|{code or 'no-code'}|{message}"


def run_mypy() -> list[tuple[str, str, str, int]]:
    """Every finding mypy reports, as ``(path, code, message, line)``."""
    proc = subprocess.run(
        [sys.executable, "-m", "mypy"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    found = []
    for line in proc.stdout.splitlines():
        match = ERROR_RE.match(line.strip())
        if match is not None:
            found.append(
                (
                    match["path"],
                    match["code"] or "",
                    match["message"],
                    int(match["line"]),
                )
            )
    return found


def load(path: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as handle:
        return {
            line.strip() for line in handle if line.strip() and not line.startswith("#")
        }


def save(path: str, keys: list[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("# Known mypy findings, one per line:\n")
        handle.write("#     path|code|message\n")
        handle.write("# Line numbers are excluded on purpose -- see the module\n")
        handle.write("# docstring. Regenerate with:\n")
        handle.write("#     python quality/check_mypy.py --write\n")
        for item in sorted(keys):
            handle.write(item + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--baseline", default=DEFAULT_BASELINE)
    parser.add_argument(
        "--write",
        action="store_true",
        help="rewrite the baseline from the current output",
    )
    args = parser.parse_args(argv)

    found = run_mypy()
    keys = [key(*f[:3]) for f in found]

    if args.write:
        save(args.baseline, keys)
        print(f"{len(keys)} known finding(s) written to {args.baseline}")
        return 0

    if not found:
        print("mypy: no findings")
        return 0

    known = load(args.baseline)
    seen = set(keys)
    new = [f for f in found if key(*f[:3]) not in known]
    stale = sorted(known - seen)

    for path, code, message, line in new:
        print(f"{path}:{line}: error: {message}  [{code}]", file=sys.stderr)

    if stale:
        print(
            f"note: {len(stale)} baseline entr"
            f"{'y is' if len(stale) == 1 else 'ies are'} now stale "
            f"(the finding is gone). Re-run with --write to prune.",
            file=sys.stderr,
        )

    if new:
        print(
            f"\ntypecheck: {len(new)} new mypy finding(s) not in the "
            f"baseline ({len(found) - len(new)} known). Fix them, or add "
            f"them with --write if they are pre-existing.",
            file=sys.stderr,
        )
        return 1

    print(f"typecheck: {len(found)} known mypy finding(s), no new ones")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
