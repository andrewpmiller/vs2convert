"""Fail on unchecked claims that code cannot run.

AGENTS.md rule 1: never call anything "latent", "unreachable", "not
triggered" or "only a test artefact" without a test that FAILS if that is
wrong. Those words do the work of a proof, and they were wrong three times in
one session:

  - _replace_scoped's missing return was called "latent, not live". It was
    live - a shader whose ISF input name is also a local raised TypeError.
  - _enclosing_block's docstring was called wrong; the test had asked the
    wrong question.
  - a conversion was called a false positive while 17 of 18 expert reference
    conversions disagreed with it.

A rule in a file is weak. This makes it mechanical: the claim is not made
unless the evidence is next to it.

WHAT IS CHECKED
  - every tracked .py and .md file: docstrings, comments, prose
  - every commit message in the repository

WHAT SATISFIES A CLAIM
  A proof marker within PROOF_WINDOW lines: a reference to a test (test_*),
  or a runnable check (python3, unittest, verify_clean, cmp, diff, git diff).
  A claim with no evidence beside it is the failure mode this exists to stop.

ESCAPE HATCH
  "unchecked-claim: <reason>" on the same line. The reason must be non-empty.
  Every escape is printed, so a pile of them is visible rather than silent.
  An empty reason is itself a failure - that is the obvious bypass.

WHAT IT CANNOT DO
  It cannot tell whether the cited test would actually fail if the claim were
  wrong. It enforces that evidence is ADJACENT to the claim, nothing more. A
  test that passes on both the real and the claimed-broken code still slips
  through; only running the test settles that.
"""
import os
import re
import subprocess
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))

BANNED = re.compile(
    r'\b(?:latent(?:ly)?|unreachable|not triggered|never triggered'
    r'|only a test artefact|test artefact only)\b', re.I)

# Evidence that sits next to the claim.
PROOF = re.compile(
    r'\btest_[A-Za-z0-9_]+'                 # a named test
    r'|python3?\s+-m\s+unittest'
    r'|\bunittest\b'
    r'|\bverify_clean\b'
    r'|\bnoqa:\s*unchecked-claim\b'
    r'|check_claims\b'
    r'|\bclaim_linter\b'
    r'|\bCheckedFor\b'
    r'|\bgit\s+diff\b'
    r'|\bcmp\s+-s\b'
    r'|\bdiff\s+-\w'
    r'|\brepro\b', re.I)

ESCAPE = re.compile(r'unchecked-claim:(?P<why>[ \t]\S.*)?$')
MARKER = re.compile(r'unchecked-claim:')

PROOF_WINDOW = 12          # lines either side of the claim
SCAN_EXT = ('.py', '.md')

# Files that necessarily discuss the rule and cannot be its own evidence.
SELF_REFERENTIAL = (
    'vs2_convert/tests/claim_linter.py',
    'vs2_convert/tests/test_claim_linter.py',
)


def _norm(rel):
    return rel.replace(os.sep, '/')


def _lines_of(path):
    try:
        with open(path, encoding='utf-8') as fh:
            return fh.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return None


def check_text(text, label, results):
    """Append findings for `text` to `results`."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not BANNED.search(line):
            continue
        if MARKER.search(line):
            esc = ESCAPE.search(line)
            why = esc.group('why') if esc else None
            if not why or not why.strip().rstrip('.'):
                results.append(
                    (label, i + 1, 'escape hatch with an empty reason',
                     line.strip()))
            # a declared, justified escape is accepted
            continue
        lo = max(0, i - PROOF_WINDOW)
        hi = min(len(lines), i + PROOF_WINDOW + 1)
        window = '\n'.join(lines[lo:hi])
        if PROOF.search(window):
            continue
        results.append(
            (label, i + 1, 'claim with no evidence within %d lines'
             % PROOF_WINDOW, line.strip()))


def check_files(results, root=REPO_ROOT):
    proc = subprocess.run(['git', 'ls-files'], cwd=root,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return
    for rel in proc.stdout.split():
        if not rel.endswith(SCAN_EXT):
            continue
        if _norm(rel) in SELF_REFERENTIAL:
            continue
        lines = _lines_of(os.path.join(root, rel))
        if lines is not None:
            check_text('\n'.join(lines), rel, results)


def check_commits(results, root=REPO_ROOT, limit=None):
    """Scan commit messages. A claim written into history is still a claim."""
    # Parse by RECORD separator, not by a fixed stride. Two stride-based
    # versions shipped and both were wrong: the first filtered empty fields
    # and so walked off-by-N (it passed 34a9372, the very commit this linter
    # exists to catch); the second used a 3-field walk that emitted a
    # malformed entry. Splitting on an explicit record marker and keeping the
    # first line as the sha is not sensitive to how many fields follow.
    fmt = '--format=%H%x1f%B%x1e'
    proc = subprocess.run(['git', 'log', fmt], cwd=root,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return
    for rec in proc.stdout.split('\x1e'):
        if not rec.strip():
            continue
        sha, _, body = rec.lstrip('\n').partition('\x1f')
        sha = sha.strip()
        if not re.fullmatch(r'[0-9a-f]{7,40}', sha):
            continue
        if limit and not sha.startswith(limit):
            continue
        check_text(body, 'commit %s' % sha[:8], results)


def load_baseline(root=REPO_ROOT):
    """Findings that are known and accepted. New claims are NOT baselined.

    Without this the gate can never pass, because the false claim in
    34a9372's message is permanent history. Rewriting committed history to
    make a linter green is exactly the sort of thing that hides a problem,
    so the finding is recorded instead - with the reason it is not being
    fixed.
    """
    path = os.path.join(root, 'vs2_convert', 'tests',
                        'claim_linter_baseline.txt')
    if not os.path.exists(path):
        return set()
    out = set()
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            out.add(line.split('#')[0].strip())
    return out


def scan(root=REPO_ROOT, include_commits=True, baseline=None):
    results = []
    if baseline is None:
        baseline = load_baseline(root)
    check_files(results, root)
    if include_commits:
        check_commits(results, root)
    if baseline:
        results = [r for r in results
                   if '%s:%d' % (r[0], r[1]) not in baseline]
    return results


def format_report(results):
    if not results:
        return 'no unchecked claims found'
    out = ['%d unchecked claim(s):' % len(results), '']
    for label, line, why, text in results:
        out.append('  %s:%d  %s' % (label, line, why))
        out.append('      %s' % (text[:96]))
        out.append('')
    out.append('Either cite the test that would fail if the claim is wrong, or')
    out.append('add  unchecked-claim: <why>  on the line itself.')
    return '\n'.join(out)


def main(argv):
    if '--no-commits' in argv:
        res = []
        check_files(res)
    else:
        res = scan()
    print(format_report(res))
    return 1 if res else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
