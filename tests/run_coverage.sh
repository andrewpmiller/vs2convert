#!/bin/sh
# Statement coverage for the package, INCLUDING code reached only through the
# CLI subprocess. The subprocess part is not optional: test_cli.py runs the
# real command, and without the hook report.py reads as 0%.
#
# Load-bearing details, each learned from a broken measurement:
#  - COVERAGE_FILE is exported, not prefixed onto one command, or `combine`
#    and `report` look in a different directory and exit 1.
#  - COVERAGE_PROCESS_START is a prefix on `coverage run` alone, so children
#    inherit it and combine/report are not themselves instrumented.
#  - the data file lives outside the package, and `discover` is not pointed
#    at tests/ recursively looking for shader sources.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY=python3
DATA="$ROOT/.coverage-data"
rm -rf "$DATA"; mkdir -p "$DATA"

RC="$ROOT/.coveragerc"
export COVERAGE_FILE="$DATA/.coverage"

# Prove the subprocess hook exists before trusting any number.
probe="$DATA/hook-probe"
COVERAGE_PROCESS_START="$RC" COVERAGE_FILE="$probe" \
    "$PY" -c 'import sys' >/dev/null 2>&1 || true
if ! ls "$probe"* >/dev/null 2>&1; then
    site="$("$PY" -c 'import site; print(site.getsitepackages()[0])')"
    echo "FAIL: no subprocess coverage data - that interpreter has no" >&2
    echo "      coverage hook, so CLI-only code would report 0%." >&2
    echo "      Fix: echo 'import coverage; coverage.process_startup()' \\" >&2
    echo "             > \"$site/coverage_subprocess.pth\"" >&2
    exit 1
fi
rm -f "$probe"*

COVERAGE_PROCESS_START="$RC" "$PY" -m coverage run --rcfile="$RC" \
    --source=vs2convert -m pytest -q
"$PY" -m coverage combine --rcfile="$RC"
"$PY" -m coverage report --rcfile="$RC"
echo
echo "Per-function breakdown of core/generator.py (statements missed):"
"$PY" tests/coverage_breakdown.py
