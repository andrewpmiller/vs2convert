"""Shared text helpers for the conversion core.

Kept separate from detector and generator because BOTH must apply the same
comment-stripping before scanning for API names. When only the detector did
it, `convert()` still scanned raw text and refused a valid shader whose
comment merely mentioned `lerp()` - the same false positive, fixed in one
place and reintroduced in the other.
"""
import re


def code_only(source: str) -> str:
    """Source with comments and string-literal contents blanked, preserving
    line structure.

    API and HLSL checks must not fire on a mention inside a comment. A
    converted shader legitimately carries comments like
    "// original: lerp(a, b, c)" documenting where the math came from, and
    scanning raw text reads that as unconverted HLSL.

    String literals are masked because generator._enclosing_block counts
    braces on this output: a `}` in a string used to shift the depth count
    and return a span one character past the real closing brace
    (test_scope_helpers.EnclosingBlockTests.test_braces_in_strings_and_comments_are_ignored).

    Newlines are preserved so per-line checks (engine-var redeclaration) keep
    their reported line numbers.
    """
    def _blank(m):
        return re.sub(r'[^\n]', ' ', m.group(0))
    out = re.sub(r'/\*.*?\*/', _blank, source, flags=re.DOTALL)
    out = re.sub(r'//[^\n]*', _blank, out)
    return _blank_string_literals(out)


def _blank_string_literals(src: str) -> str:
    """Blank the contents of "..." and '...' literals; the quotes stay put.

    Same-length output, escape-aware. Carried over from the deleted
    generator._mask_comments_and_strings; newlines inside a literal are kept
    (code_only's line-structure contract, and brace counting treats ' ' and
    '\\n' alike). test_scope_helpers.EnclosingBlockTests.
    """
    out = list(src)
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in '"\'':
            quote = c
            j = i + 1
            while j < n:
                if src[j] == '\\' and j + 1 < n:
                    j += 2
                    continue
                if src[j] == quote:
                    break
                j += 1
            for k in range(i + 1, min(j, n - 1)):
                if out[k] != '\n':
                    out[k] = ' '
            i = j + 1
        else:
            i += 1
    return ''.join(out)
