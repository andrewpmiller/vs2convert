"""The claim linter must catch a lazy claim and not catch a proven one.

Two versions of the linter shipped broken and both were caught by the same
test: the commit-walk parsed git output by a fixed stride, so it skipped most
of history. The acid test is commit 34a9372, whose message contains the false
claim "Latent, not live" - the exact error AGENTS.md rule 1 exists to stop. A
linter that misses that is decoration.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.claim_linter import (BANNED, check_text, format_report,  # noqa: E402
                               scan)
from tests import claim_linter  # noqa: E402


class ClaimLinterTests(unittest.TestCase):

    def _hits(self, text):
        res = []
        check_text(text, 't', res)
        return res

    def test_flags_a_bare_claim(self):
        self.assertEqual(1, len(self._hits('this is latent, not live\n')))

    def test_a_cited_test_satisfies_the_claim(self):
        res = self._hits('unreachable here.\nsee test_foo for the repro\n')
        self.assertEqual([], res)

    def test_a_cited_command_satisfies_the_claim(self):
        res = self._hits('latent. verify with:\n'
                         '  python3 -m unittest tests.test_x\n')
        self.assertEqual([], res)

    def test_escape_hatch_with_a_reason_is_accepted(self):
        res = self._hits('latent; unchecked-claim: dynamic dispatch, '
                         'documented in PRD section 4\n')
        self.assertEqual([], res)

    def test_escape_hatch_with_an_empty_reason_is_rejected(self):
        res = self._hits('latent; unchecked-claim:\n')
        self.assertEqual(1, len(res))
        self.assertIn('empty reason', res[0][2])

    def test_proof_far_away_does_not_count(self):
        body = ['latent claim here'] + ['filler'] * 30 + ['test_too_far']
        self.assertEqual(1, len(self._hits('\n'.join(body))))

    def test_prose_that_merely_sounds_like_verification_is_not_proof(self):
        # The failure that matters: a commit asserting reachability in prose
        # ("Confirmed a shader declaring a local time converts correctly")
        # must not be treated as evidence.
        res = self._hits('Latent, not live: shadowing is handled upstream.\n'
                         'Confirmed a shader declaring a local time converts\n'
                         'correctly today.\n')
        self.assertEqual(1, len(res))

    def test_banned_words_cover_the_documented_set(self):
        for word in ('latent', 'latently', 'unreachable', 'not triggered',
                     'only a test artefact'):
            self.assertTrue(BANNED.search('is %s here' % word),
                            'the linter does not know the word %r' % word)


class AcidTest(unittest.TestCase):
    """The linter must catch the real commit, not just synthetic input."""

    def test_false_claim_in_history_is_flagged(self):
        # baseline=set() so this is not satisfied by the baselined copy of
        # the same finding: the linter must still DETECT it, independently of
        # whether the gate is allowed to stay green.
        results = scan(baseline=set())
        hits = [(lbl, ln) for lbl, ln, _, _ in results
                if lbl.startswith('commit ')]
        self.assertTrue(
            any(lbl.startswith('commit 34a9372') for lbl, _ in hits),
            'the linter no longer catches commit 34a9372, which contains the '
            'false "Latent, not live" claim. Found commit hits: %s' % hits[:5])

    def test_the_whole_history_is_actually_walked(self):
        """Guards the bug that shipped twice: a malformed parse that silently
        scanned only part of history and reported success."""
        import subprocess
        body = scan(baseline=set())
        # every commit carrying a banned word must appear, not just some
        proc = subprocess.run(
            ['git', 'log', '--format=%H%x1f%B%x1e'],
            cwd=claim_linter.REPO_ROOT, capture_output=True, text=True)
        recs = [r for r in proc.stdout.split('\x1e') if r.strip()]
        with_claim = [r.lstrip('\n').partition('\x1f')[0][:8]
                      for r in recs if BANNED.search(r.partition('\x1f')[2])]
        seen = {lbl.replace('commit ', '')
                for lbl, _, _, _ in body if lbl.startswith('commit ')}
        # every commit whose message contains a banned word should be either
        # reported, or have a proof/escape in its window
        missing = [c for c in with_claim if c not in seen]
        for m in missing:
            rec = next(r for r in recs if r.lstrip('\n').startswith(m))
            res = []
            check_text(rec.partition('\x1f')[2], 'commit ' + m, res)
            self.assertEqual([], res,
                             'commit %s contains a banned word but was not '
                             'reported and has no proof: the history walk is '
                             'skipping records' % m)


if __name__ == '__main__':
    unittest.main()


class GateIsWiredInTests(unittest.TestCase):
    """The linter must run as part of the suite, not only when remembered."""

    def test_repository_is_currently_clean(self):
        from tests.claim_linter import format_report, scan
        results = scan()
        self.assertEqual(
            [], results,
            'unchecked claims present:\n%s\n'
            'Cite the failing test, add "unchecked-claim: <why>", or - if it '
            'is a pre-existing finding - add it to '
            'claim_linter_baseline.txt with a reason.'
            % format_report(results))

    def test_a_new_claim_would_fail_the_gate(self):
        """Prove the gate is not vacuous: inject a claim, expect a failure."""
        from tests.claim_linter import check_text
        res = []
        check_text('this path is latent.\n', 'x', res)
        self.assertEqual(1, len(res))
