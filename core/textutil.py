"""Shared text helpers for the conversion core.

Kept separate from detector and generator because BOTH must apply the same
comment-stripping before scanning for API names. When only the detector did
it, `convert()` still scanned raw text and refused a valid shader whose
comment merely mentioned `lerp()` - the same false positive, fixed in one
place and reintroduced in the other.
"""
import re


def code_only(source: str) -> str:
    """Source with comments blanked, preserving line structure.

    API and HLSL checks must not fire on a mention inside a comment. A
    converted shader legitimately carries comments like
    "// original: lerp(a, b, c)" documenting where the math came from, and
    scanning raw text reads that as unconverted HLSL.

    Newlines are preserved so per-line checks (engine-var redeclaration) keep
    their reported line numbers.
    """
    def _blank(m):
        return re.sub(r'[^\n]', ' ', m.group(0))
    out = re.sub(r'/\*.*?\*/', _blank, source, flags=re.DOTALL)
    out = re.sub(r'//[^\n]*', _blank, out)
    return out
