# ensqsqed -- development checks.
#
# Everything here runs against .venv, which the README tells you to create
# with --system-site-packages so the system python-rtmidi and textual are
# visible. Every target therefore goes through .venv/bin rather than PATH,
# because the tools are installed there and nowhere else.
#
#   make bootstrap   install the project and the pinned toolchain
#   make lint        ruff check + ruff format --check
#   make format      ruff format (rewrites files; NOT run by check)
#   make typecheck   mypy, against a baseline of pre-existing findings
#   make test        pytest with coverage
#   make audit       pip-audit, vulture, deptry, detect-secrets
#   make check       all of the above, in order, stopping at the first failure

VENV      := .venv
PYTHON    := $(VENV)/bin/python
PIP       := $(VENV)/bin/pip
RUFF      := $(VENV)/bin/ruff
MYPY      := $(VENV)/bin/mypy
VULTURE   := $(VENV)/bin/vulture
PIP_AUDIT := $(VENV)/bin/pip-audit
DETECT    := $(VENV)/bin/detect-secrets
DETECT_HOOK := $(VENV)/bin/detect-secrets-hook
PRE_COMMIT  := $(VENV)/bin/pre-commit

# Scanned source. sq/data is the generated name catalog and is excluded by
# ruff's extend-exclude and by detect-secrets' own filters.
SRC         := sq ensqsqed tools tests quality
VULTURE_SRC := sq ensqsqed tests

# vulture 2.x has no --whitelist flag: a whitelist is supplied by including
# the file among the scanned paths, so it belongs here.
WHITELIST := quality/vulture_whitelist.py

# Tracked files PLUS untracked-but-not-ignored ones. A brand new module is
# untracked until it is committed, and it is exactly that file the pipeline
# must hold to the full rule set -- so `git ls-files` alone would leave the
# one gap that matters. --exclude-standard keeps .venv and the caches out.
PYFILES := $(shell git ls-files --cached --others --exclude-standard '*.py')

.DEFAULT_GOAL := help
.PHONY: help bootstrap lint format typecheck typecheck-baseline \
        test coverage audit audit-vulns audit-dead audit-deps audit-secrets \
        secrets-baseline check clean

help:
	@echo 'ensqsqed development checks'
	@echo
	@echo '  make bootstrap         install the project and the pinned toolchain'
	@echo '  make lint              ruff check + ruff format --check'
	@echo '  make format            ruff format (rewrites files; NOT run by check)'
	@echo '  make typecheck         mypy, vs quality/mypy-baseline.txt'
	@echo '  make test              pytest + coverage (term-missing)'
	@echo '  make audit             pip-audit, vulture, deptry, detect-secrets'
	@echo '  make check             everything above; any failure stops the run'
	@echo
	@echo 'See docs/QUALITY_CHECKS.md for what is suppressed and why.'

bootstrap:
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt
	$(PIP) install -e .
	$(PRE_COMMIT) install
	@echo 'toolchain ready.'

# --- linting and formatting --------------------------------------------------

lint:
	$(RUFF) check $(SRC)
	$(RUFF) format --check $(SRC)

format:
	$(RUFF) format $(SRC)

# --- type checking -----------------------------------------------------------
#
# mypy has no baseline of its own, and both built-in alternatives are worse:
# ignore_errors hides new errors of any kind, and per-module disable_error_code
# cannot tell "always was there" from "new, same code". So quality/check_mypy.py
# records each known finding as file|code|message and fails only on a finding
# that is not on the list. Regenerate after deliberately accepting new debt:
#
#     make typecheck-baseline
typecheck:
	$(PYTHON) quality/check_mypy.py

typecheck-baseline:
	$(PYTHON) quality/check_mypy.py --write

# --- tests -------------------------------------------------------------------

test:
	$(PYTHON) -m pytest

coverage:
	$(PYTHON) -m pytest --cov-report=term-missing

# --- audit -------------------------------------------------------------------

audit: audit-vulns audit-dead audit-deps audit-secrets

# Audits what this project declares, not whatever happens to be installed in
# the ambient environment. Auditing the venv would report CVEs in unrelated
# system packages and drown the project's own dependencies in noise.
#
# vinsynlib is filtered out of the list handed to pip-audit: it is a
# sibling checkout rather than a package on an index (see [tool.uv.sources]
# in pyproject.toml), so pip cannot resolve it and pip-audit would fail
# resolving rather than auditing anything. It is this project's own source
# that has already been reviewed in this repository, and it carries no
# third-party dependencies of its own at this layer.
audit-vulns:
	$(PYTHON) -c "import pathlib, tomllib; \
	    data = tomllib.load(open('pyproject.toml', 'rb')); \
	    pathlib.Path('.audit-runtime-req.txt').write_text(\
	        ''.join(r + chr(10) for r in data['project']['dependencies'] \
	        if not r.startswith('vinsynlib')))"
	@$(PIP_AUDIT) --progress-spinner=off -r .audit-runtime-req.txt; \
	    status=$$?; rm -f .audit-runtime-req.txt; exit $$status
	$(PIP_AUDIT) --progress-spinner=off --no-deps -r requirements-dev.txt

audit-dead:
	$(VULTURE) --min-confidence 80 $(VULTURE_SRC) $(WHITELIST)

audit-deps:
	$(VENV)/bin/deptry .

# NOTE: the check is detect-secrets-HOOK, not `scan --baseline`.
# `detect-secrets scan --baseline <file>` does not check anything: in v1.5.0 it
# merges what it just found into the baseline and rewrites it, printing
# nothing and exiting 0. Used in a Makefile that silently accepts new secrets
# and poisons the baseline with them. The hook does the comparison and exits 1.
audit-secrets:
	$(DETECT_HOOK) --baseline .secrets.baseline $(PYFILES) \
	    Makefile README.md pyproject.toml requirements-dev.txt  # pragma: allowlist secret

# Regenerate .secrets.baseline after reviewing the diff. Review it: this file
# is what tells the next person which strings were considered and dismissed.
secrets-baseline:
	$(DETECT) scan --baseline .secrets.baseline \
	    --exclude-files '^\.venv/' \
	    --exclude-files '^sq/data/' \
	    --exclude-files '^\.pytest_cache/'

# --- everything --------------------------------------------------------------

check:
	@echo '== lint =='      && $(MAKE) --no-print-directory lint
	@echo '== typecheck ==' && $(MAKE) --no-print-directory typecheck
	@echo '== test =='      && $(MAKE) --no-print-directory test
	@echo '== audit =='     && $(MAKE) --no-print-directory audit
	@echo
	@echo 'all checks passed.'

clean:
	rm -rf .coverage .pytest_cache .mypy_cache .ruff_cache htmlcov
	rm -f .audit-runtime-req.txt
	find . -name __pycache__ -type d -prune -not -path './.venv/*' -exec rm -rf {} +
