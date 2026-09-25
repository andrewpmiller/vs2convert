"""End-to-end CLI tests: argument handling, output paths, exit codes.

Design decision 2 (PRD §8) is a user-facing contract and had never been
implemented - the default wrote `converted_<name>.frag` into the current
directory instead of `<name>.frag` beside the input. These tests pin it.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
VS2_DIR = os.path.dirname(TESTS_DIR)
APP = os.path.join(VS2_DIR, 'vs2_convert.py')

SIMPLE_ISF = '''/*{
  "INPUTS": [{"NAME": "Zoom", "TYPE": "float", "DEFAULT": 0.5,
              "MIN": 0.0, "MAX": 1.0}]
}*/
void main() {
    vec2 uv = gl_FragCoord.xy / RENDERSIZE.xy;
    float w = sin(uv.x * 8.0 + TIME);
    gl_FragColor = vec4(vec3(w * 0.5 + 0.5) * Zoom, 1.0);
}
'''


def run(args, cwd):
    return subprocess.run([sys.executable, APP] + args, cwd=cwd,
                          capture_output=True, text=True)


class OutputPathTests(unittest.TestCase):
    """PRD §8 design decision 2: same directory as input, .frag extension."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.src = os.path.join(self.dir, 'sample-isf.fs')
        with open(self.src, 'w') as f:
            f.write(SIMPLE_ISF)

    def test_default_output_is_beside_input_with_frag_extension(self):
        r = run(['sample-isf.fs'], self.dir)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        expected = os.path.join(self.dir, 'sample-isf.frag')
        self.assertTrue(os.path.exists(expected),
                        'expected %s; directory holds %s'
                        % (expected, os.listdir(self.dir)))
        # the input is untouched and both files sit side by side
        self.assertTrue(os.path.exists(self.src))
        self.assertEqual(sorted(os.listdir(self.dir)),
                         sorted(['sample-isf.fs', 'sample-isf.frag']))
        self.assertEqual(len(os.listdir(self.dir)), 2,
                         'exactly two files, side by side, as decided')

    def test_no_converted_prefix_in_default_name(self):
        """Regression guard: the old default was converted_<name>.frag."""
        run(['sample-isf.fs'], self.dir)
        self.assertNotIn('converted_sample-isf.frag', os.listdir(self.dir))

    def test_explicit_output_argument_wins(self):
        out = os.path.join(self.dir, 'star-VS.frag')
        r = run(['sample-isf.fs', 'star-VS.frag'], self.dir)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(out))

    def test_output_directory_that_does_not_exist_is_reported(self):
        r = run(['sample-isf.fs', 'no/such/dir/out.frag'], self.dir)
        self.assertEqual(r.returncode, 4,
                         'a missing output directory is exit 4')
        self.assertIn('output directory does not exist',
                      r.stdout.lower())


class FlagTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.src = os.path.join(self.dir, 'ok.fs')
        with open(self.src, 'w') as f:
            f.write(SIMPLE_ISF)

    def test_dry_run_writes_nothing(self):
        r = run(['ok.fs', '--dry-run'], self.dir)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'ok.frag')))
        self.assertIn('dry run', r.stdout.lower())

    def test_validate_flag_reports_valid(self):
        r = run(['ok.fs', '--validate'], self.dir)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # the verdict names validation, whatever the glyph set in use
        self.assertIn('validated', r.stdout)

    def test_decline_exits_three_and_writes_nothing(self):
        bad = os.path.join(self.dir, 'multi.fs')
        with open(bad, 'w') as f:
            f.write('void mainImage(out vec4 f, in vec2 c){'
                    ' f = texture(iChannel0, c) * texture(iChannel1, c); }')
        r = run(['multi.fs'], self.dir)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'multi.frag')))

    def test_force_overrides_the_decline(self):
        bad = os.path.join(self.dir, 'multi.fs')
        with open(bad, 'w') as f:
            f.write('void mainImage(out vec4 f, in vec2 c){'
                    ' f = texture(iChannel0, c) * texture(iChannel1, c); }')
        r = run(['multi.fs', '--force'], self.dir)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # the override must be stated, so the result is not read as trusted
        self.assertIn('expect it to be wrong', r.stdout.lower())
        self.assertTrue(os.path.exists(os.path.join(self.dir, 'multi.frag')))

    def test_verify_without_moderngl_fails_loudly(self):
        """--verify must never exit 0 when it could not run.

        The old renderer could not fail, and a silent skip reads as a pass.
        """
        r = run(['ok.fs', '--verify'], self.dir)
        has_moderngl = True
        try:
            import moderngl  # noqa: F401
        except ImportError:
            has_moderngl = False
        if has_moderngl:
            self.skipTest('moderngl present: the gate can actually run')
        # Two acceptable loud-failure routes: the renderer returns a failed
        # result (exit 1), or the import fails outright (exit 2). What must
        # never happen is exit 0.
        self.assertIn(r.returncode, (1, 2),
                      'verify did not run but reported success: %s'
                      % (r.stdout + r.stderr))
        self.assertIn('moderngl', (r.stdout + r.stderr).lower())


class VerdictShapeTests(unittest.TestCase):
    """The verdict must be the last thing on stdout, and must be obvious."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.src = os.path.join(self.dir, 'ok.fs')
        with open(self.src, 'w') as f:
            f.write(SIMPLE_ISF)

    def test_success_glyph_and_name_present(self):
        r = run(['ok.fs'], self.dir)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r'(✅|\[ok\])')
        self.assertIn('ok.frag', r.stdout)

    def test_ascii_mode_avoids_wide_glyphs(self):
        env = dict(os.environ, VS2_ASCII='1')
        proc = subprocess.run([sys.executable, APP, 'ok.fs'], cwd=self.dir,
                              capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        for ch in ('\u2705', '\u26a0', '\u274c'):
            self.assertNotIn(ch, proc.stdout,
                             'emoji present despite VS2_ASCII=1')

    def test_missing_verify_is_not_reported_as_failed(self):
        """Exit 2 is a tooling problem, not a broken shader. Reporting it as
        a failure sends people looking in the wrong place."""
        has_gl = True
        try:
            import moderngl  # noqa: F401
        except ImportError:
            has_gl = False
        if has_gl:
            self.skipTest('moderngl present: the gate can actually run')
        r = run(['ok.fs', '--verify'], self.dir)
        self.assertEqual(r.returncode, 2)
        self.assertIn('not verified', r.stdout.lower())
        self.assertNotIn('failed', r.stdout.lower())


if __name__ == '__main__':
    unittest.main(verbosity=2)
