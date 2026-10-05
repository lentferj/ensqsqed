# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# ensqsqed is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 2 of the License, or (at your option) any later
# version.
#
# ensqsqed is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.

"""The suite must not write the application's config into the checkout.

``config.DEFAULT_PATH`` is the relative string ``"config.toml"``. That is
the right default for the application -- it is a disposable per-checkout
cache of which port answered last, gitignored, as distinct from the
favourites store, which is the user's own work and lives in the platform
data directory. It is the wrong thing for a test, which would drop the
file into whatever directory pytest was started from.

Being gitignored is what makes this worth a test rather than a habit: the
file never appears in ``git status``, so the only evidence is somebody
noticing it sitting next to the source.

Both checks below were this file's own AST walk, copied into each project
in the family. They are now one call into :mod:`vinsynlib.devchecks`.
"""

import ast
import os

from vinsynlib import devchecks


def test_every_config_save_in_the_suite_names_its_file() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    offenders = devchecks.config_saves_without_path(here)
    assert not offenders, (
        f"{offenders} save to config.DEFAULT_PATH, which is relative to the "
        f"working directory. Pass a tmp_path."
    )


def test_the_check_has_something_to_check() -> None:
    """Guard against the search passing because it matched nothing.

    Counts every ``config.save_*`` in the suite, offending or not:
    :func:`devchecks.config_saves_without_path` returns only the offenders,
    so "none" from it means nothing at all on its own.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    saves = 0
    for _name, tree in devchecks.iter_test_sources(here):
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr.startswith("save_")
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "config"
            ):
                saves += 1
    assert saves, "no config.save_* calls found; the check is vacuous"


def test_this_project_imports_no_sibling() -> None:
    """A sibling project's package is not installed beside this one."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    offenders = []
    for pkg in ("ensqsqed", "sq"):
        d = os.path.join(root, pkg)
        for name in os.listdir(d):
            if name.endswith(".py"):
                offenders += devchecks.foreign_imports(
                    os.path.join(d, name),
                    own=("ensqsqed", "sq", "vinsynlib"),
                )
    assert not offenders, offenders
