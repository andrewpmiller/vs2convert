"""Regression gate over the 41-source ISF corpus.

The unit tests are built from fixtures designed to pass. This runs the real
corpus in ISF-shaders-main/ through convert -> render -> the external
validator, and compares against a recorded baseline.

Why this exists: on 2026-09-25 the corpus caught a regression that no unit
test did. The IMPORTED gate added that day declined `phreax twisted carbon.fs`
because the manifest DECLARED a bundled texture, when the body never sampled
it (its only reference was inside a comment). Unit tests could not have found
that; a real source could.

Run: python3 vs2_convert/tests/test_isf_corpus.py
Skip with: SKIP_ISF_CORPUS=1  (needs moderngl; a venv that has it will do)
"""
import glob
import os
import subprocess
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
VS2_DIR = os.path.dirname(TESTS_DIR)
REPO_ROOT = os.path.dirname(VS2_DIR)
CORPUS = sorted(glob.glob(os.path.join(REPO_ROOT, 'ISF-shaders-main', '**', '*.fs'),
                         recursive=True))

sys.path.insert(0, VS2_DIR)
from core.generator import convert, ConversionError  # noqa: E402

# Recorded 2026-09-25 with the current engine. A change here is a behaviour
# change: investigate it, do not just update the number.
EXPECTED_CONVERTED = 32
EXPECTED_DECLINED = 9
EXPECTED_RENDER_FAILURES = 1
# Genuinely near-black at its own ISF defaults (max luminance 2/255), so the
# flat/dark check is correct rather than over-sensitive. Verified by hand.
EXPECTED_DARK = {'kali electromagnetic politics'}


def _skip_without_gl():
    try:
        import moderngl  # noqa: F401
        return False
    except ImportError:
        return True


@unittest.skipUnless(CORPUS, 'ISF corpus not present')
class IsfCorpusTests(unittest.TestCase):
    def test_corpus_is_present(self):
        self.assertEqual(len(CORPUS), 41, 'corpus size changed')

    def test_conversion_counts_match_baseline(self):
        converted = declined = 0
        errors = []
        for path in CORPUS:
            with open(path, encoding='utf-8-sig') as f:
                src = f.read()
            try:
                convert(src, 'ISF', 'probe')
                converted += 1
            except ConversionError:
                declined += 1
            except Exception as e:  # noqa: BLE001
                errors.append('%s: %s: %s' % (os.path.basename(path),
                                                type(e).__name__, e))
        self.assertEqual(errors, [], 'unexpected errors: %s' % errors)
        self.assertEqual(converted, EXPECTED_CONVERTED,
                         'converted count moved - a behaviour change')
        self.assertEqual(declined, EXPECTED_DECLINED,
                         'declined count moved - a behaviour change')

    @unittest.skipIf(os.environ.get('SKIP_ISF_CORPUS'),
                     'SKIP_ISF_CORPUS set')
    @unittest.skipIf(_skip_without_gl(), 'moderngl not available')
    def test_render_and_external_validation(self):
        from renderer.pipeline import run_verification
        import tempfile
        converted, failures = 0, []
        with tempfile.TemporaryDirectory() as outdir:
            for path in CORPUS:
                with open(path, encoding='utf-8-sig') as f:
                    src = f.read()
                try:
                    frag = convert(src, 'ISF', 'probe')['frag']
                except Exception:  # noqa: BLE001
                    continue
                converted += 1
                stem = os.path.splitext(os.path.basename(path))[0]
                with open(os.path.join(outdir, stem + '.frag'), 'w') as fh:
                    fh.write(frag)
                res = run_verification(frag, stem)
                if not res['passed']:
                    failures.append((stem, res['errors']))
            self.assertEqual(len(failures), EXPECTED_RENDER_FAILURES,
                             'render failures changed: %s'
                             % [(n, e[:1]) for n, e in failures])
            for name, _ in failures:
                self.assertIn(name, EXPECTED_DARK,
                              'new dark/flat failure: %s' % name)
            val = os.path.join(REPO_ROOT, '.claude', 'skills',
                               'vs2-shader-expert', 'scripts',
                               'validate_shader.py')
            proc = subprocess.run([sys.executable, val, outdir],
                                  capture_output=True, text=True)
            out = proc.stdout + proc.stderr
            self.assertNotIn('FAIL', out.split('\n')[-2] if '\n' in out else out,
                             'external validator rejected a converted shader')
            self.assertIn('ALL OK', out,
                          'external validator summary missing')


if __name__ == '__main__':
    unittest.main(verbosity=2)
