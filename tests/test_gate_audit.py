"""Audit the gates: break the thing each one guards, and check it goes red.

A passing test run is not evidence. It becomes evidence only when the guard has
been shown to fail ON PURPOSE - "run the test twice, once broken". Three gates
in this repo were green while blind, and each time the cause was the same:
nobody had ever run them broken.

  - a gate reported green failed on the very next fresh run, because the run
    happened BEFORE the committing artefact that broke it existed;
  - forcing every shader to render black produced one failure, from a string
    assertion on the fragColor line, and nothing from the render gate. The
    interpreter used for that probe could not import moderngl, so the render
    gate was skipped - and a skipped gate reads as a pass;
  - corpus_scan's prose-exclusion guard had no test, and deleting it left the
    suite green (the mutation catalogue is what caught that one).

This module does it mechanically. For each gate: apply a named deliberate break
to the thing it guards, assert the gate goes RED, restore, assert it is GREEN
again, and assert the tree is clean. A gate that stays green with its defect in
place is a FAILURE here - unless it is recorded in gate_audit_baseline.txt with
a reason. It is never dropped in silence.

Run:    sh vs2_convert/tests/run_gate_audit.sh
Suite:  every test here is skipped unless VS2_GATE_AUDIT=1, because these tests
        EDIT SOURCE FILES. The variable is removed from the child test runs so
        the audit cannot recurse into itself.

Relationship to the mutation catalogue
--------------------------------------
tests/mutants.py asks "does some test catch this defect?" and is run by hand.
This asks "does the gate I am relying on actually go red?" and additionally
records WHAT fired. The overlap is deliberate: an audit entry that duplicates a
mutation costs a few seconds and proves the gate, not just the assertion.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
VS2_DIR = os.path.dirname(TESTS_DIR)
REPO_ROOT = os.path.dirname(VS2_DIR)
sys.path.insert(0, TESTS_DIR)

from safe_edit import EditError, edit, verify_clean  # noqa: E402

ENABLED = os.environ.get('VS2_GATE_AUDIT') == '1'
VENV_PY = os.path.join(REPO_ROOT, 'shader-pipeline', '.venv', 'bin', 'python')
SYSTEM_PY = sys.executable
BASELINE_PATH = os.path.join(TESTS_DIR, 'gate_audit_baseline.txt')


class GateBreak:
    """One gate and a deliberate break of the thing it guards.

    path      repo-relative file the break is applied to
    find      exact text to replace. Must occur exactly once, or safe_edit
              refuses and the entry reports "break did not apply" - which is
              itself a failure here, because a stale break audits nothing.
    replace   the deliberate defect
    selector  unittest target that must go RED under the break; None = the
              whole suite
    python    'system' or 'venv'. Not cosmetic: the render gate is skipped by
              an interpreter without moderngl, and a skipped gate passes.
              Auditing it under the wrong interpreter is exactly how the
              "black output goes undetected" conclusion was reached.
    expect_failing
              optional substrings; at least one FAILING test name must contain
              one of them. This is what makes an entry specific: "the suite
              noticed" is weak when a string assertion somewhere would also
              satisfy it.
    """

    def __init__(self, id, guards, path, find, replace, selector, why,
                 python='system', expect_failing=None):
        self.id = id
        self.guards = guards
        self.path = path
        self.find = find
        self.replace = replace
        self.selector = selector
        self.why = why
        self.python = python
        self.expect_failing = expect_failing


AUDITS = [
    # --- the aspect decision, which 17 of 18 expert conversions agreed on ---
    GateBreak(
        id='aspect-reference-parity',
        guards='the engine aspect factor in converted output',
        path='vs2_convert/core/generator.py',
        find=r"r'(texCoord * \1 - 1.0) * vec2(1.0, div)'",
        replace=r"r'(texCoord * \1 - 1.0) * vec2(div, 1.0)'",
        selector='test_reference_parity',
        expect_failing=('test_reference_parity',),
        why=('Every expert conversion in converted_shaders/ scales Y: '
             'vec2(1.0, div) with div = resolution.y / resolution.x, and the '
             'author verified in VS-2 that the transposed axis renders '
             'stretched on a landscape canvas.'),
    ),
    # --- the fix that stopped a raymarched ISF rendering black in VS-2 ---
    GateBreak(
        id='gl-fragcoord-regression',
        guards='the Shadertoy in-param mapping to texCoord, not gl_FragCoord',
        path='vs2_convert/core/generator.py',
        find="(in_name, 'texCoord')):",
        replace="(in_name, 'gl_FragCoord')):",
        selector='test_engine',
        expect_failing=('never_emits_gl_fragcoord',),
        why=('gl_FragCoord does not work in VS-2: three variants of one shader '
             'differing only in the uv derivation - the texCoord one rendered '
             'and animated, both gl_FragCoord ones failed.'),
    ),
    # --- the render gate itself ---
    GateBreak(
        id='render-gate-can-fail',
        guards='run_verification(): a flagged frame forces passed=False',
        path='vs2_convert/renderer/pipeline.py',
        find='        return {"passed": not errors, "errors": errors, '
             '"metrics": metrics}',
        replace='        return {"passed": True, "errors": [], '
                '"metrics": metrics}',
        selector=('test_isf_corpus.IsfCorpusTests'
                  '.test_render_and_external_validation'),
        python='venv',
        expect_failing=('test_render_and_external_validation',),
        why=('The module documents "there is no code path that returns '
             'passed=True without having rendered and measured". The corpus '
             'records one genuine render failure, so a gate that always passes '
             'reports 0 and the count assertion in test_isf_corpus goes red.'),
    ),
    # --- the historical worst case: every shader renders black ---
    GateBreak(
        id='black-output-is-caught',
        guards='the suite noticing an output that renders black',
        path='vs2_convert/core/generator.py',
        find='            "    fragColor = vec4(vs2Final * color.rgb, '
             'alpha * coloredPixels);\\n"',
        replace='            "    fragColor = vec4(0.0, 0.0, 0.0, 1.0);\\n"',
        selector=None,
        python='venv',
        expect_failing=('test_isf_corpus.',),
        why=('The worst case in the handover: every shader renders black and '
             'only a string assertion notices. expect_failing requires the '
             'CORPUS RENDER gate to fire, not merely that something went red, '
             'because a string assertion on the fragColor line fires too. An '
             'entry satisfied by the string assertion alone would leave the '
             'fidelity hole open.'),
    ),
    # --- hard rule 2 ---
    GateBreak(
        id='seven-param-budget',
        guards='MAX_PARAMS: VS-2 will not load more than 7 knobs',
        path='vs2_convert/core/generator.py',
        find='MAX_PARAMS = 7',
        replace='MAX_PARAMS = 50',
        selector=('test_engine.ConvertIsfTests'
                  '.test_seven_param_budget_enforced'),
        expect_failing=('test_seven_param_budget_enforced',),
        why=('Ten ISF float inputs must be capped at 7 and the dropped ones '
             'must be reported as baked. Silently emitting 8 is a hard failure '
             'at load time in VS-2, not a cosmetic overrun.'),
    ),

    # --- the image-input gate, which was a blanket refusal once ---
    GateBreak(
        id='image-input-gate',
        guards='the refusal of two or more distinct ISF image inputs',
        path='vs2_convert/core/generator.py',
        find='if len(_image_inputs) > 1:',
        replace='if False:',
        selector='test_engine.IsfImageInputTests',
        expect_failing=('test_multiple_image_inputs_are_declined',),
        why=('N image inputs collapse onto one imageTex, leaving the body '
             'sampling samplers that were never declared. Real case: GLSLOP '
             'developer-k0rz1pe2.fs declares six (src0..src5), found by running '
             'the collection rather than by any test.'),
    ),
    # --- the loop budget in the external validator ---
    GateBreak(
        id='perf-budget-warning',
        guards='the external validator warning on a loop bound over 100',
        path='.cline/skills/vs2-shader-expert/scripts/validate_shader.py',
        find='        if bound > 100:',
        replace='        if bound > 300:',
        selector='test_validator.PerfBudgetTests',
        expect_failing=('test_loop_over_the_budget_warns',),
        why=('WARN never fails the validator exit code and nothing asserted the '
             'warning, so 100 -> 300 was unobservable. The positive control in '
             'test_validator.PerfBudgetTests is what makes it observable; this '
             'entry proves that control is what catches the change. Real '
             'instances: pathogen_path (130), spiral_riders_kali (150).'),
    ),
    # --- the guard against a shadowed, dead definition ---
    GateBreak(
        id='single-definition-guard',
        guards='SingleDefinitionTests: each entry point defined exactly once',
        path='vs2_convert/core/generator.py',
        find='def _convert_isf(source: str, report: dict, meta: dict = None)'
             ' -> tuple:',
        replace=('def _convert_isf(source: str, report: dict,'
                 ' meta: dict = None) -> tuple:\n'
                 '    return _convert_isf(source, report, meta)\n\n\n'
                 'def _convert_isf(source: str, report: dict,'
                 ' meta: dict = None) -> tuple:'),
        selector='test_engine.SingleDefinitionTests',
        expect_failing=('test_entry_points_are_defined_exactly_once',),
        why=('A second definition shadows the first, so the earlier one can '
             'never run while apply_mutation patches the FIRST occurrence. That '
             'is how seven mutations reported as survivors while the live '
             'function was fine - 638 lines of dead code, from scripted edits '
             'that appended after a failed first attempt.'),
    ),
    # --- the claim linter: AGENTS.md rule 1, mechanised ---
    GateBreak(
        id='claim-linter',
        guards='the claim linter: a bare claim with no evidence beside it',
        path='vs2_convert/tests/coverage_breakdown.py',
        find="if __name__ == '__main__':\n    sys.exit(main())",
        replace=("if __name__ == '__main__':\n    sys.exit(main())\n"
                 "# this guard is latent and never triggered"),
        selector='test_claim_linter.GateIsWiredInTests',
        expect_failing=('test_repository_is_currently_clean',),
        why=('AGENTS.md rule 1 forbids these words, which do the work of a '
             'proof. The linter must flag a bare one; the acid version that '
             'bypasses the baseline lives in test_claim_linter.AcidTest.'),
    ),
]


# A break that changes no behaviour. The audit must NOT report it as red: an
# audit that answers "red" to every edit would look exactly like a working one
# while proving nothing, which is the failure this file exists to prevent.
NEUTERED_CONTROL = GateBreak(
    id='non-vacuity-control',
    guards='the audit itself: a behaviour-preserving edit is not a red gate',
    path='vs2_convert/core/generator.py',
    find='MAX_PARAMS = 7',
    replace='MAX_PARAMS = 7  # a comment is not a behaviour change',
    selector=('test_engine.ConvertIsfTests'
              '.test_seven_param_budget_enforced'),
    why=('Two linters in this repo shipped broken and were caught only by a '
         'test of this shape. If this one asserts red, the audit cannot '
         'distinguish "the gate went red" from "the edit applied".'),
)

def _interpreter(name):
    return VENV_PY if name == 'venv' else SYSTEM_PY


def _run_tests(selector, python, timeout=1800):
    """Run the suite, or one target, with `python`. True = it failed."""
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join(
        [TESTS_DIR, VS2_DIR, env.get('PYTHONPATH', '')]).strip(os.pathsep)
    # A child run must not start an audit of its own: this module would be
    # imported by discover, and with the flag set it would edit source files
    # underneath the run that is already in progress.
    env.pop('VS2_GATE_AUDIT', None)
    # The order is load-bearing: `python -m unittest -v discover <dir>`
    # treats "discover" as a test name to load, and the run fails with a
    # single unittest.loader._FailedTest error. The subcommand's flag goes
    # AFTER the subcommand.
    cmd = [python, '-m', 'unittest']
    cmd += [selector, '-v'] if selector else ['discover', TESTS_DIR, '-v']
    proc = subprocess.run(cmd, cwd=VS2_DIR, env=env, capture_output=True,
                          text=True, timeout=timeout)
    return proc.returncode != 0, (proc.stdout + proc.stderr)


# Two spellings of "this test failed", because one of them is not always
# there.
#
# The traceback summary (`FAIL: name (dotted.id)`) is reliable. The progress
# line (`name (dotted.id) ... FAIL`) is NOT: unittest breaks the line after
# the docstring, so a test that has one prints
#
#     test_seven_param_budget_enforced (test_engine.ConvertIsfTests....)
#     Hard rule 2: at most 7 manifest parameters. ... FAIL
#
# and a parser expecting `... FAIL` on the name's own line reports NOTHING.
# That is what happened on this module's first run: two gates were reported
# as "red, but not in the test that guards it - these did: ['nothing']"
# while their guard tests had in fact failed. Both spellings are parsed,
# and the union is what identifies a failure.
_FAILED_RES = (
    re.compile(r'^(?:FAIL|ERROR): (\S+) \((\S+)\)', re.M),
    re.compile(r'^(\S+) \((\S+)\) \.\.\. (?:FAIL|ERROR)', re.M),
)


def _tail(output, n=400):
    """The end of a child run's output, on one line, for a failure message."""
    return ' / '.join(output[-n:].split())


def _failed_tests(output):
    """Dotted ids of the tests that failed, in the order they ran."""
    names = []
    for pattern in _FAILED_RES:
        for m in pattern.finditer(output):
            if m.group(2) not in names:
                names.append(m.group(2))
    return names


def run_break(gb):
    """Apply the break, expect red, restore, expect green, expect clean.

    Every outcome is reported rather than raised, so one broken entry does not
    hide the state of the rest. `verdict()` decides pass/fail.
    """
    res = {'id': gb.id, 'applied': False, 'red': False, 'green': False,
           'clean': False, 'failing': [], 'detail': ''}
    path = os.path.join(REPO_ROOT, gb.path)
    python = _interpreter(gb.python)

    if gb.python == 'venv' and not os.path.exists(python):
        res['detail'] = ('this gate needs %s, which is not present. The gate '
                         'cannot be audited here, and that is reported as a '
                         'failure: a skipped gate reads as a pass.' % python)
        return res
    if not os.path.exists(path):
        res['detail'] = 'no such file: %s' % path
        return res

    # A real file copy, not an in-memory buffer, so an interrupted audit still
    # leaves the tree recoverable.
    fd, backup = tempfile.mkstemp(suffix='.gateaudit.bak')
    os.close(fd)
    shutil.copy2(path, backup)
    try:
        try:
            edit(path, gb.find, gb.replace, expect=1)
        except EditError as exc:
            res['detail'] = 'the break did not apply: %s' % exc
            return res
        res['applied'] = True
        red, out = _run_tests(gb.selector, python)
        res['red'] = red
        res['failing'] = _failed_tests(out)
        if not red:
            res['detail'] = ('the gate stayed GREEN with the defect in place: '
                             '%s' % _tail(out))
    finally:
        shutil.copy2(backup, path)
        os.unlink(backup)

    # NOTE the polarity. _run_tests reports "DID IT FAIL?", so False here is
    # the GOOD outcome. Unpacking it into a variable named `green` inverts
    # the meaning: the first version of this module reported 'still red
    # after the restore' for every entry while the restored runs were
    # passing. The failing-run detail below carries raw output for exactly
    # this reason - an empty failing-test list said 'look at the loader'.
    failed_after_restore, out2 = _run_tests(gb.selector, python)
    res['green'] = not failed_after_restore
    if failed_after_restore:
        res['detail'] = ('still RED after the restore, so no result from this '
                         'entry can be trusted: %s | %s'
                         % (_failed_tests(out2)[:3], _tail(out2)))

    clean, dirty = verify_clean([path], REPO_ROOT)
    res['clean'] = clean
    if not clean:
        res['detail'] = 'the tree is dirty after the restore: %s' % dirty
    return res


def verdict(res, expect_failing=None):
    """(bool, detail). The entry passed only if every step behaved."""
    if not (res['applied'] and res['red'] and res['green'] and res['clean']):
        return False, res['detail']
    if expect_failing:
        hit = [n for n in res['failing']
               if any(sub in n for sub in expect_failing)]
        if not hit:
            return False, (
                'the gate went red, but not in the test that guards it. '
                'Expected one of %s to fail; these did: %s'
                % (list(expect_failing), res['failing'][:6] or ['nothing']))
    return True, ''


def read_baseline():
    """Gate ids recorded as genuinely blind, mapped to the reason why."""
    out = {}
    if not os.path.exists(BASELINE_PATH):
        return out
    with open(BASELINE_PATH, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            gate_id, _, why = line.partition('#')
            out[gate_id.strip()] = why.strip()
    return out

@unittest.skipUnless(ENABLED, 'set VS2_GATE_AUDIT=1 to run the gate audit '
                             '(it edits source files)')
class GateAuditTests(unittest.TestCase):

    def test_each_gate_goes_red_under_its_break(self):
        """One break per gate: red under it, green once it is restored."""
        baseline = read_baseline()
        failures, stale = [], []
        lines = []
        for gb in AUDITS:
            res = run_break(gb)
            ok, why = verdict(res, gb.expect_failing)
            if ok and gb.id in baseline:
                stale.append(gb)
            if not ok and gb.id in baseline:
                lines.append('BLIND %-32s recorded: %s'
                             % (gb.id, baseline[gb.id]))
                continue
            if not ok:
                failures.append((gb, why))
            lines.append('%s %-32s %s'
                         % ('ok   ' if ok else 'FAIL ', gb.id,
                            why or ('red in: %s' % (res['failing'][:4] or '-'))))
        report = '\n'.join(lines)
        print('\n' + report)
        if stale:
            # A baseline entry that now goes red is a stale excuse, and a
            # standing "known blind" licence is how a blind gate survives.
            self.fail('baseline entries that are no longer needed: %s\n%s'
                      % ([g.id for g in stale], report))
        if failures:
            self.fail(
                'gates that did NOT go red under a deliberate break:\n'
                + '\n'.join('  %s: %s' % (g.id, w) for g, w in failures)
                + '\n\n' + report)

    def test_audit_is_not_vacuous(self):
        """A behaviour-preserving edit must NOT be reported as a red gate.

        Without this, an audit that answers "red" to every edit would look
        identical to a working one. Two linters in this repo shipped broken and
        were caught by exactly this shape of test.
        """
        res = run_break(NEUTERED_CONTROL)
        self.assertTrue(res['applied'],
                        'the control edit did not apply: %s' % res['detail'])
        self.assertFalse(
            res['red'],
            'a behaviour-preserving edit was reported as a gate going red, so '
            'every result in this module is suspect')
        self.assertTrue(res['green'])
        self.assertTrue(res['clean'])
        # verdict() is deliberately NOT used here: it encodes "the gate must
        # go red", which is the opposite of what this control asserts. A
        # helper that cannot express the negative case is how a vacuous
        # audit comes to pass.
        self.assertEqual(res['failing'], [],
                         'the control run reported failing tests: %s'
                         % res['failing'])

    def test_every_audited_path_is_tracked(self):
        """verify_clean() can only see tracked files.

        `git diff` says nothing at all about an untracked path, so such a path
        reads as clean whatever the audit did to it. Assert every path is
        tracked, or the cleanliness check is the vacuous assertion this repo
        keeps rediscovering.
        """
        for gb in AUDITS + [NEUTERED_CONTROL]:
            proc = subprocess.run(
                ['git', 'ls-files', '--error-unmatch', gb.path],
                cwd=REPO_ROOT, capture_output=True, text=True)
            self.assertEqual(
                proc.returncode, 0,
                '%s is not tracked, so verify_clean() cannot check it'
                % gb.path)

    def test_gate_ids_are_unique(self):
        ids = [g.id for g in AUDITS]
        self.assertEqual(len(ids), len(set(ids)), 'duplicate gate id')

    def test_every_gate_says_why_it_matters(self):
        for gb in AUDITS:
            self.assertGreaterEqual(
                len(gb.why.strip()), 60,
                'gate %s has no argument for why the break matters' % gb.id)
            self.assertNotEqual(
                gb.why.strip(), gb.guards.strip(),
                'gate %s restates its guard as its reason' % gb.id)

    def test_baseline_entries_name_a_real_gate_and_a_reason(self):
        known = {g.id for g in AUDITS}
        for gate_id, why in read_baseline().items():
            self.assertIn(gate_id, known,
                          'gate_audit_baseline.txt names %r, which is not in '
                          'AUDITS' % gate_id)
            self.assertTrue(why,
                            'baseline entry for %s has no reason - an empty '
                            'excuse is the bypass' % gate_id)

    def test_tracked_sources_are_untouched_at_the_end(self):
        paths = sorted({g.path for g in AUDITS + [NEUTERED_CONTROL]})
        clean, dirty = verify_clean([os.path.join(REPO_ROOT, p)
                                     for p in paths], REPO_ROOT)
        self.assertTrue(
            clean, 'the audit left the tree modified: %s. Restore these before '
                   'doing anything else.' % dirty)

@unittest.skipUnless(ENABLED, 'set VS2_GATE_AUDIT=1 to run the gate audit '
                             '(it edits source files)')
class CoverageHookProbeTests(unittest.TestCase):
    """The coverage hook check must be able to fail.

    Without a subprocess hook the children record nothing and every CLI-only
    path (core/report.py) reports 0% - which is what "coverage is broken" meant
    here, twice, both times wrongly aimed at the code. run_coverage.sh now
    probes a real child process and exits 1 if no data appears. A probe that
    cannot fail would be decoration, so both directions are pinned.
    """

    def _probe(self, extra_args=()):
        with tempfile.TemporaryDirectory() as d:
            probe = os.path.join(d, 'hook-probe')
            env = dict(os.environ,
                       COVERAGE_PROCESS_START=os.path.join(REPO_ROOT,
                                                           '.coveragerc'),
                       COVERAGE_FILE=probe)
            subprocess.run([VENV_PY] + list(extra_args) + ['-c', 'import sys'],
                           env=env, capture_output=True, text=True)
            return [f for f in os.listdir(d) if f.startswith('hook-probe')]

    def test_probe_sees_data_when_the_hook_is_installed(self):
        if not os.path.exists(VENV_PY):
            self.fail('the venv interpreter is missing: %s' % VENV_PY)
        self.assertTrue(
            self._probe(),
            'the probe found no coverage data with the hook installed, so '
            'run_coverage.sh would refuse to measure at all')

    def test_probe_sees_nothing_when_site_processing_is_off(self):
        """-S disables .pth processing, which is exactly "no hook".

        This is the direction that proves the check can go red; without it the
        probe is only ever seen passing.
        """
        self.assertFalse(
            self._probe(['-S']),
            'the probe reported coverage data for an interpreter with no hook, '
            'so it cannot detect the condition it exists for')


def main(argv):
    if not ENABLED:
        print('This module EDITS SOURCE FILES, so it is not part of the '
              'default suite.')
        print('Run it through its runner instead:')
        print('  sh vs2_convert/tests/run_gate_audit.sh')
        return 2
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
