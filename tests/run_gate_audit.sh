#!/bin/sh
# Audit the gates: break the thing each one guards, and require it to go red.
#
# WHY THIS EXISTS
# A passing test run is not evidence. It becomes evidence only when the guard
# has been shown to fail ON PURPOSE. Three gates in this repo were green while
# blind - a green that had already expired, a render gate that was skipped and
# so read as a pass, and a prose guard with no test at all. See the module
# docstring in vs2_convert/tests/test_gate_audit.py.
#
# This EDITS SOURCE FILES. It goes through safe_edit (which refuses an
# ambiguous or missing needle) and restores from a real file copy, and it
# asserts cleanliness with verify_clean() afterwards. The before/after
# `git status` comparison below is the shell-level proof that nothing was left
# behind, independent of the audit's own checks.
#
# It is NOT part of the default suite: every test in the module is skipped
# unless VS2_GATE_AUDIT=1, because a routine `unittest discover` must never
# modify source.
set -e
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

# The venv interpreter is required, not preferred: the render gate is skipped
# by an interpreter without moderngl, and a skipped gate passes.
PY="$ROOT/shader-pipeline/.venv/bin/python"
if [ ! -x "$PY" ]; then
    echo "FAIL: no interpreter at $PY" >&2
    echo "      Without moderngl the render gate is skipped, and a skipped" >&2
    echo "      gate reads as a pass. Auditing it there proves nothing." >&2
    exit 1
fi

BEFORE="$(git status --porcelain)"
echo "gate audit: $BEFORE" >/dev/null 2>&1 || true

rc=0
VS2_GATE_AUDIT=1 PYTHONPATH="$ROOT/vs2_convert/tests:$ROOT/vs2_convert" \
    "$PY" "$ROOT/vs2_convert/tests/test_gate_audit.py" || rc=$?

AFTER="$(git status --porcelain)"
if [ "$BEFORE" != "$AFTER" ]; then
    echo "" >&2
    echo "FAIL: the audit changed the working tree and did not restore it." >&2
    echo "--- before ---" >&2
    echo "$BEFORE" >&2
    echo "--- after ---" >&2
    echo "$AFTER" >&2
    echo "--- diff ---" >&2
    git --no-pager diff --stat >&2
    exit 1
fi
echo ""
echo "working tree unchanged by the audit (git status identical before/after)"

if [ "$rc" -eq 0 ]; then
    echo "gate audit PASSED"
else
    echo "gate audit FAILED (exit $rc)"
fi
exit "$rc"
