#!/usr/bin/env python3
"""Mutation-test the vs2_convert suite: does it actually catch defects?

Usage:
    python3 vs2_convert/tests/run_mutation_tests.py [options]

    --only SUBSTR   run only mutations whose name contains SUBSTR
    --list          list mutations and exit
    --keep-going    report all survivors; default stops at the first
    -v / --verbose  show captured test output per mutation

What this does
--------------
For each mutation in `mutants.py`, it injects a known defect into the source,
runs the tests, and records whether they noticed.

  SURVIVED  = tests passed with a deliberate defect present. This is a hole
              in the suite: something is being asserted that cannot fail, or
              is not being asserted at all. Reported as a failure.
  KILLED    = tests failed as they should.
  EQUIV     = mutation is a behavioural no-op and cannot be killed. Declared
              explicitly in the catalogue with a written justification.

A clean run means: for every mutation here, some test genuinely detects the
defect. It does NOT mean the code is correct - only that these particular
defects are caught. Treat a survivor as the priority; treat a clean run as
the floor, not the goal.

Why bother
----------
Review prose did not catch the defects in this project. A review rated three
findings High/Critical: one was a no-op, two were false, and one carried
fabricated evidence. This harness is executable and repeatable, which is
exactly the property that was missing.

Exit code 0 = all non-equivalent mutations killed. 1 = at least one survivor.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
VS2_DIR = os.path.dirname(TESTS_DIR)
REPO_ROOT = os.path.dirname(VS2_DIR)
sys.path.insert(0, TESTS_DIR)

from mutants import MUTATIONS, Mutation  # noqa: E402

GREEN, RED, YELLOW, DIM, BOLD, RESET = (
    '\033[32m', '\033[31m', '\033[33m', '\033[2m', '\033[1m', '\033[0m')
if not sys.stdout.isatty():
    GREEN = RED = YELLOW = DIM = BOLD = RESET = ''


def run_tests(selector, verbose=False):
    """Run the suite, optionally scoped. Returns (killed, output)."""
    env = dict(os.environ)
    # tests/ must be importable for a scoped run to find the module. Without
    # this the scoped run errors with "module not found", which looks exactly
    # like "tests correctly detected the mutation" - every mutation would be
    # reported killed and the harness would be green while testing nothing.
    env['PYTHONPATH'] = os.pathsep.join(
        [TESTS_DIR, VS2_DIR, env.get('PYTHONPATH', '')]).strip(os.pathsep)
    if selector:
        parts = selector.split('.')
        mod = parts[0]
        if len(parts) > 1:
            target = '%s.%s' % (mod, '.'.join(parts[1:]))
        else:
            target = mod
        cmd = [sys.executable, '-m', 'unittest', '-v', target]
    else:
        cmd = [sys.executable, '-m', 'unittest', 'discover', TESTS_DIR, '-v']
    proc = subprocess.run(cmd, cwd=VS2_DIR, env=env,
                          capture_output=True, text=True)
    out = proc.stdout + proc.stderr
    if verbose:
        print(DIM + out + RESET)
    return proc.returncode != 0, out


def apply_mutation(mutation):
    """Patch the target file. Returns (ok, detail)."""
    target = os.path.join(VS2_DIR, mutation.path)
    if not os.path.exists(target):
        return False, 'target not found: %s' % mutation.path
    with open(target) as f:
        original = f.read()
    if mutation.find not in original:
        return False, ('search string not found in %s - the catalogue is '
                       'stale, update it' % mutation.path)
    mutated = original.replace(mutation.find, mutation.replace, 1)
    if mutated == original:
        return False, 'mutation produced no change'
    with open(target, 'w') as f:
        f.write(mutated)
    return True, None


def main():
    ap = argparse.ArgumentParser(
        description='Mutation-test the vs2_convert suite.')
    ap.add_argument('--only', help='run only mutations matching this substring')
    ap.add_argument('--list', action='store_true', help='list and exit')
    ap.add_argument('--keep-going', action='store_true',
                    help='report all survivors instead of stopping at the first')
    ap.add_argument('-v', '--verbose', action='store_true',
                    help='show test output per mutation')
    args = ap.parse_args()

    selected = [m for m in MUTATIONS
                if not args.only or args.only.lower() in m.name.lower()]

    if args.list:
        for m in selected:
            tag = 'EQUIV' if m.equivalent else '     '
            print('%s  %-52s %s' % (tag, m.name, m.path))
        return 0

    if not selected:
        print('no mutations matched %r' % args.only)
        return 0

    # Baseline: the suite must pass BEFORE any mutation, or a "killed"
    # result is meaningless.
    print(BOLD + 'baseline (unmutated)' + RESET)
    baseline_failed, out = run_tests(None, args.verbose)
    if baseline_failed:
        print(RED + '  FAIL - suite is already red before any mutation.' + RESET)
        print(out)
        print(RED + 'Fix the baseline before trusting any result.' + RESET)
        return 1
    print(GREEN + '  ok' + RESET)

    print(BOLD + '\nmutations' + RESET)
    killed, survived, equiv, broken = [], [], [], []

    for m in selected:
        if m.equivalent:
            equiv.append(m)
            print('%s  %-52s %s' % (YELLOW + 'EQUIV', RESET, m.name))
            print(DIM + '        %s' % m.why + RESET)
            continue

        # Back up the real file, mutate, test, restore. The backup is a real
        # file copy (not an in-memory buffer) so an interrupted run still
        # leaves the tree recoverable.
        target = os.path.join(VS2_DIR, m.path)
        fd, backup = tempfile.mkstemp(suffix='.bak')
        os.close(fd)
        shutil.copy2(target, backup)
        try:
            applied, why = apply_mutation(m)
            if not applied:
                broken.append((m, why))
                print('%s  %-52s %s' % (RED + 'BROKEN', RESET, m.name))
                print(DIM + '        %s' % why + RESET)
                continue
            did_fail, out = run_tests(m.selector, args.verbose)
            if did_fail:
                killed.append(m)
                print('%s  %-52s %s' % (GREEN + 'killed', RESET, m.name))
            else:
                survived.append((m, out))
                print('%s  %-52s %s' % (RED + 'SURVIVED', RESET, m.name))
                if not args.keep_going:
                    break
        finally:
            shutil.copy2(backup, target)
            os.unlink(backup)

    # Prove the tree is clean. A harness that leaves mutations behind is
    # worse than no harness.
    print(BOLD + '\nrestore check' + RESET)
    residual = []
    for m in selected:
        target = os.path.join(VS2_DIR, m.path)
        with open(target) as f:
            for mut in MUTATIONS:
                if mut.path == m.path and mut.find in f.read() \
                        and mut.replace != mut.find:
                    if mut.find in f.read() and mut.replace not in f.read():
                        residual.append(m.name)
    if residual:
        print(RED + '  residue detected: %s' % residual + RESET)
    else:
        still_red, _ = run_tests(None)
        if still_red:
            print(RED + '  suite red after restore - a mutation leaked!' + RESET)
        else:
            print(GREEN + '  ok - all files restored, suite green' + RESET)

    total = len(killed) + len(survived) + len(equiv) + len(broken)
    print(BOLD + '\nsummary' + RESET)
    print('  total     %d' % total)
    print('  killed    %d%s' % (len(killed), GREEN if killed else ''))
    print('  survived  %d%s' % (len(survived), RED if survived else ''))
    print('  equiv     %d%s' % (len(equiv), YELLOW if equiv else ''))
    print('  broken    %d%s' % (len(broken), RED if broken else ''))

    if broken:
        print(RED + '\nBROKEN catalogue entries - the mutation no longer '
              'applies to the source:' + RESET)
        for m, why in broken:
            print('  %s: %s' % (m.name, why))

    if survived:
        print(RED + '\nSURVIVORS - the suite passes with a deliberate defect '
              'present:' + RESET)
        for m, _ in survived:
            print('  %s' % m.name)
            print('    %s' % m.why)
            print('    -> the tests guarding this behaviour are vacuous, or '
                  'the behaviour is untested.')

    if survived or broken or residual or still_red:
        return 1
    print(GREEN + '\nAll non-equivalent mutations killed.' + RESET)
    return 0


if __name__ == '__main__':
    sys.exit(main())
