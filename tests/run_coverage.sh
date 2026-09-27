#!/bin/sh
# Line/statement coverage for the converter, with a recorded floor.
#
# The floor exists so coverage cannot quietly DROP. It does not assert a
# number worth chasing on its own - the mutation catalogue and the expert
# reference set are the real oracles. This measures, and records, so that
# "which parts have never run" is answerable rather than guessed at.
set -e
cd "$(dirname "$0")/.."
PY=../shader-pipeline/.venv/bin/python
"$PY" -m coverage run --source=core,validators,renderer -m unittest discover tests -q
"$PY" -m coverage report
echo
echo "Per-function breakdown of core/generator.py (statements missed):"
"$PY" tools_coverage_breakdown.py 2>/dev/null || "$PY" tests/coverage_breakdown.py
