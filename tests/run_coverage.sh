#!/bin/sh
# Statement coverage for the converter, INCLUDING code reached only through
# the CLI subprocess.
#
# The subprocess part is not optional. test_cli.py drives the real binary via
# subprocess.run, and without this the CLI-only path is invisible: report.py
# measured 0% (79 statements) while the tests exercising it passed. It was
# the measurement that was wrong, not the code.
#
# Three details that are load-bearing:
#  - COVERAGE_FILE must be EXPORTED, not set as a prefix on the `coverage
#    run` line. A prefix applies to that one command, so `combine` and
#    `report` fell back to vs2_convert/.coverage, found nothing, and exited
#    1 with "No data to combine". That is what "coverage is broken" meant
#    here - the subprocess hook was working the whole time.
#  - COVERAGE_PROCESS_START is deliberately NOT exported: it is a prefix on
#    `coverage run` alone, so the children inherit it while combine and
#    report are not themselves instrumented.
#  - the data file MUST live outside vs2_convert/, or corpus_scan.sources()
#    picks the .coverage.* files up as shader sources and the corpus test
#    fails on its own coverage run.
set -e
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT/vs2_convert"

PY="$ROOT/shader-pipeline/.venv/bin/python"
DATA="$ROOT/.coverage-data"
rm -rf "$DATA"; mkdir -p "$DATA"

RC="$ROOT/.coveragerc"
export COVERAGE_FILE="$DATA/.coverage"

# Check the subprocess hook BEFORE trusting any number. Without a hook the
# children record nothing and every CLI-only path (core/report.py) reads as
# 0%. A .pth file in site-packages is not proof: this runs a real child
# process and requires it to have written data.
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
    --source=core,validators,renderer \
    -m unittest discover tests -q
# No --keep: combine consumes the parallel files, so exactly one data file
# remains and a later `coverage report` has nothing to auto-combine.
"$PY" -m coverage combine --rcfile="$RC"
"$PY" -m coverage report --rcfile="$RC"
echo
echo "Per-function breakdown of core/generator.py (statements missed):"
"$PY" tests/coverage_breakdown.py
