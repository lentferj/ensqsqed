# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  ensqsqed contributors
#
# This file is part of ensqsqed.
#
# Vulture whitelist: names referenced here count as used.
#
# vulture 2.x has no --whitelist flag; a whitelist is supplied by passing this
# file among the scanned paths, which is what the Makefile does. That means
# this file has to be valid Python that ruff is also willing to lint, hence the
# trailing marker: a bare `inspect` on a line of its own is a useless
# expression (B018) and an undefined name (F821).

# Imported inside TestABoolIsNotANumber._loaders but never referenced -- the
# neighbouring `importlib` is what the function actually uses. Kept rather than
# removed, because this pass does not edit application source.
inspect  # noqa: B018, F821
