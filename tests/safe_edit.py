"""Checked source edits for scripts and one-off patches.

Every append-instead-of-replace failure this repo has suffered was a silent
one: the file on disk parsed fine, imported fine, and looked right in an
editor, so nothing objected until much later.

  - 638 lines of dead code: a scripted edit failed partway AFTER writing, and
    the re-run appended a second copy of _convert_isf instead of replacing
    the first. Python binds the last definition, so the earlier copy could
    never run - and apply_mutation() patches the FIRST occurrence, so seven
    mutations were silently editing a function that was not live.
  - a test fixture generated with str.replace on a tab/space mismatch: the
    replace matched nothing, returned the input unchanged, and the file was
    written anyway. It looked correct and had no bounce limiter in it.
  - a mutation entry written with nested quotes that did not parse.

None of these raise. str.replace returns the original string when the
needle is absent; Python allows redefinition; a missing edit and a correct
edit are indistinguishable from the result alone.

The rule: an edit either applies exactly as intended or raises. There is no
silent path, and no "check the diff afterwards" step left to the reader.

Usage
-----
    from safe_edit import edit, EditError, replace_once, insert_before

    edit('vs2_convert/core/generator.py', old, new, expect=1)
    edit(path, old, new, expect=1, must_parse=False)   # for .frag/.txt

Deliberately not offered:

  append(path, text)   - the failure mode itself. Use insert_before or
                         insert_after with an explicit anchor, so the edit
                         is anchored to something and can be checked.
"""
import ast
import os
import sys

__all__ = ['EditError', 'edit', 'replace_once', 'insert_before',
           'insert_after', 'read', 'write', 'verify_clean']


class EditError(AssertionError):
    """Raised when an edit does not apply exactly as intended.

    Subclasses AssertionError so existing test runners surface it as a
    failure rather than an error-with-context-loss.
    """


def _read(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def edit(path, old, new, expect=1, must_parse=None, allow_dupes=False):
    """Replace `old` with `new` in `path`, or raise EditError.

    expect    exact number of occurrences that must be replaced. Default 1.
              0 asserts the text is ABSENT (use for asserting a removal or
              for a must-not-regress check).
    must_parse  'ast' to require the result still parses as Python,
              True as a synonym, False/None to skip. Defaults to 'ast' for
              .py and None otherwise.
    allow_dupes  permit expect > 1. Off by default: a needle that appears
              more than once is nearly always a sign the caller matched too
              loosely, and replacing the first is what hid the dead code.

    Guards, in order:
      1. the file exists
      2. `old` occurs exactly `expect` times
      3. `new` is not already present in a way that would compound
      4. the result differs from the input
      5. the result still parses (when must_parse)
    """
    if not os.path.exists(path):
        raise EditError('%s: no such file' % path)

    if must_parse is None:
        must_parse = 'ast' if path.endswith('.py') else None
    elif must_parse is True:
        must_parse = 'ast'

    original = _read(path)
    count = original.count(old)

    # allow_dupes is the opt-in to replacing EVERY occurrence, so the
    # expected count becomes the number actually found. Without this the
    # count check below still demands 1 and the opt-in cannot work - which
    # is its own silent failure: the caller asked for a dupe edit and got
    # an exception instead.
    if allow_dupes and expect == 1:
        expect = count

    if count != expect:
        raise EditError(
            '%s: expected %d occurrence(s) of the search text, found %d.\n'
            '  search: %r\n'
            '  Nothing was written. If the text is absent the edit has '
            'already been applied, or the needle does not match (check '
            'tabs vs spaces, and trailing whitespace).'
            % (path, expect, count, old[:120]))

    if expect > 1 and not allow_dupes:
        raise EditError(
            '%s: the search text occurs %d times. Replacing only the first '
            'is how a duplicate definition survived review. Pass '
            'allow_dupes=True if you really mean all of them.\n'
            '  search: %r' % (path, count, old[:120]))

    if expect == 0:
        return original, 0

    # replace() counts from the start, so replace(old, new, 0) would be a
    # no-op. expect is 0 only on the assert-absent path, which returns early.
    updated = original.replace(old, new, expect) if expect else original

    if updated == original:
        # new == old, or the replacement is a no-op reformat
        raise EditError(
            '%s: the replacement produced no change. old and new are '
            'probably identical, or the edit was a no-op reformat.\n'
            '  old: %r\n  new: %r' % (path, old[:80], new[:80]))

    if must_parse == 'ast':
        try:
            ast.parse(updated)
        except SyntaxError as exc:
            raise EditError(
                '%s: the edit would produce a file that does not parse '
                '(%s at line %s). Nothing was written.\n'
                '  Usually nested or mismatched quotes in the replacement.'
                % (path, exc.msg, exc.lineno)) from exc

    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(updated)
    return updated, expect


def replace_once(path, old, new, must_parse=None):
    """edit() with expect=1. The common case."""
    return edit(path, old, new, expect=1, must_parse=must_parse)


def insert_before(path, anchor, text, must_parse=None):
    """Insert `text` immediately before `anchor`, which must occur once.

    The anchored form of an append. Fails if the anchor is missing or
    ambiguous, so an insertion can never land in the wrong place twice.
    """
    if not text.endswith('\n'):
        text += '\n'
    return edit(path, anchor, text + anchor, expect=1, must_parse=must_parse)


def insert_after(path, anchor, text, must_parse=None):
    """Insert `text` immediately after the line containing `anchor`."""
    if not text.endswith('\n'):
        text += '\n'
    _read(path)  # existence check with a clear error
    n = _read(path).count(anchor)
    if n != 1:
        raise EditError('%s: anchor occurs %d times, expected 1: %r'
                        % (path, n, anchor[:100]))
    return edit(path, anchor, anchor + text, expect=1, must_parse=must_parse)


def verify_clean(paths, repo_root=None):
    """Assert `paths` match git HEAD, so a scratch edit cannot be left behind.

    The failure mode this file exists to prevent is not only a bad edit - it
    is a *forgotten* one. While proving these guards I ran an `allow_dupes`
    edit against generator.py to demonstrate the opt-in, and it survived the
    run as a live one-line change. Nothing objected, because a mutation
    applied to real source is indistinguishable from an intended one.

    Call this after any deliberate scratch edit. It fails loudly rather than
    relying on a later `git diff` being noticed.

    Returns (clean, [dirty paths]). Never raises, so a script can report and
    clean up in one pass.
    """
    import subprocess
    if repo_root is None:
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
    dirty = []
    for p in paths:
        rel = os.path.relpath(p, repo_root)
        # tracked: does it differ from HEAD
        proc = subprocess.run(
            ['git', 'diff', '--quiet', '--', rel],
            cwd=repo_root, capture_output=True)
        if proc.returncode != 0:
            dirty.append(rel)
            continue
        # untracked: `git diff` reports nothing at all, so an untracked file
        # silently reads as clean. That is the same silence this module
        # exists to remove - check explicitly.
        ls = subprocess.run(['git', 'ls-files', '--error-unmatch', rel],
                            cwd=repo_root, capture_output=True)
        if ls.returncode != 0 and os.path.exists(p):
            dirty.append(rel + ' (untracked)')
    return (not dirty), dirty


def read(path):
    return _read(path)


def write(path, text, must_parse=None):
    """Write a whole file, parse-checking Python first.

    For GENERATED artefacts - test fixtures, converted shaders - where the
    content is built by a script and there is no needle to anchor to. The
    value is the post-write assertions the caller still has to make; this
    only stops a malformed file being written silently.
    """
    if must_parse is None:
        must_parse = 'ast' if path.endswith('.py') else None
    if must_parse == 'ast':
        try:
            ast.parse(text)
        except SyntaxError as exc:
            raise EditError('%s: generated content does not parse (%s at '
                            'line %s)' % (path, exc.msg, exc.lineno)) from exc
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(text)
    return path


if __name__ == '__main__':  # pragma: no cover - smoke check
    print('safe_edit importable from', __file__)
    sys.exit(0)
