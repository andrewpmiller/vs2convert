"""End-to-end CLI tests: argument handling, output paths, exit codes.

Design decision 2 (PRD §8) is a user-facing contract: the default writes
`<name>.frag` into the same directory as the input, never a
`converted_<name>.frag` in the current directory. These tests pin it.

The CLI is invoked as `python -m vs2convert.cli`, so the tests exercise the
installed entry point rather than a private script path.
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

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

# A single-channel Shadertoy with no metadata: converts, but the channel
# binding is a guess, so the run must disclose it as UNVERIFIED.
SHADERTOY_ONE_CHANNEL = ('void mainImage(out vec4 f, in vec2 c){'
                         ' f = vec4(texture(iChannel0, c / 9.0).rgb, 1.0); }')

# A converted output with no uv math at all: validator warns about the
# missing coordinate setup but the run still succeeds. (An EMPTY INPUTS
# block declines - _convert_isf treats [] as unparseable - so one knob.)
NOUV_ISF = ('/*{"INPUTS": [{"NAME": "Zoom", "TYPE": "float",'
            ' "DEFAULT": 0.5, "MIN": 0.0, "MAX": 1.0}]}*/\n'
            'void main() { gl_FragColor = vec4(vec3(0.7 * Zoom), 1.0); }\n')

# A real ISF block with no main(): the generator raises its entry-point gate,
# whatever --force says, because --force only overrides suitability.
NO_ENTRY_ISF = ('/*{"INPUTS": [{"NAME": "Zoom", "TYPE": "float",'
                ' "DEFAULT": 0.5, "MIN": 0.0, "MAX": 1.0}]}*/\n'
                'varying vec2 vPos;\n')

# Two distinct image inputs: suits detection, but the generator gate refuses
# because VS-2 exposes one imageTex. Exit 1, not the suitability exit 3.
MULTI_IMAGE_ISF = '''/*{
  "INPUTS": [
    {"NAME": "src0", "TYPE": "image", "SOURCE": "a"},
    {"NAME": "src1", "TYPE": "image", "SOURCE": "b"}
  ]
}*/
uniform sampler2D src0;
uniform sampler2D src1;
void main() {
    fragColor = texture(src0, gl_FragCoord.xy / 100.0)
              + texture(src1, gl_FragCoord.xy / 100.0);
}
'''

# Bright, static, non-flat: passes empty/flat/dark, fails the motion check
# (manifest movement defaults to true), so --verify must exit 1 "frozen".
# One knob, because an EMPTY INPUTS block declines.
STATIC_ISF = ('/*{"INPUTS": [{"NAME": "Zoom", "TYPE": "float",'
              ' "DEFAULT": 1.0, "MIN": 0.0, "MAX": 1.0}]}*/\n'
              'void main() {\n'
              '    vec2 uv = gl_FragCoord.xy / RENDERSIZE.xy;\n'
              '    gl_FragColor = vec4(vec3(uv.x * 60.0 * Zoom), 1.0);\n'
              '}\n')

_ABSENT = object()


def run(args, cwd, stdin=None, env=None):
    return subprocess.run([sys.executable, '-m', 'vs2convert.cli'] + args,
                          cwd=cwd, capture_output=True, text=True,
                          input=stdin, env=env)


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

    def test_validation_is_always_on(self):
        """There is no --validate flag: every conversion is validated before
        it is reported ok, and the verdict says so."""
        r = run(['ok.fs'], self.dir)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('validated', r.stdout)

    def test_decline_exits_three_and_writes_nothing(self):
        bad = os.path.join(self.dir, 'multi.fs')
        with open(bad, 'w') as f:
            f.write('void mainImage(out vec4 f, in vec2 c){'
                    ' f = texture(iChannel0, c) * texture(iChannel1, c); }')
        r = run(['multi.fs'], self.dir)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'multi.frag')))
        self.assertIn('github.com/andrewpmiller/vs2convert/issues/new',
                      r.stdout)

    def test_force_overrides_the_suitability_decline(self):
        bad = os.path.join(self.dir, 'multi.fs')
        with open(bad, 'w') as f:
            f.write('void mainImage(out vec4 f, in vec2 c){'
                    ' f = texture(iChannel0, c) * texture(iChannel1, c); }')
        r = run(['multi.fs', '--force'], self.dir)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # the override must be stated, so the result is not read as trusted
        self.assertIn('forced past the suitability gate', r.stdout.lower())
        self.assertTrue(os.path.exists(os.path.join(self.dir, 'multi.frag')))

    def test_verify_without_render_deps_fails_loudly(self):
        """--verify must never exit 0 when it could not run."""
        try:
            import moderngl  # noqa: F401
        except ImportError:
            pass
        else:
            self.skipTest('render deps present: the gate can actually run')
        r = run(['ok.fs', '--verify'], self.dir)
        self.assertEqual(r.returncode, 2,
                         'verify did not run but reported success: %s'
                         % (r.stdout + r.stderr))
        self.assertIn('not verified', r.stdout.lower())


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
        r = run(['ok.fs'], self.dir, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for ch in ('\u2705', '\u26a0', '\u274c'):
            self.assertNotIn(ch, r.stdout,
                             'emoji present despite VS2_ASCII=1')


class StdinTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)

    def test_stdin_requires_an_output_path(self):
        r = run(['-'], self.dir, stdin=SIMPLE_ISF)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn('OUTPUT is required', r.stderr)

    def test_stdin_converts_to_an_explicit_output(self):
        out = os.path.join(self.dir, 'from-stdin.frag')
        r = run(['-', out], self.dir, stdin=SIMPLE_ISF)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(out))


class UnicodeProbeTests(unittest.TestCase):
    """supports_unicode must decide without writing to the stream it probes.

    The old probe tested encodability by writing the three glyphs into the
    caller's stream, so a writable stream with no `encoding` came back True
    with the glyphs already emitted. test_probe_writes_nothing pins it.
    """

    def test_probe_writes_nothing(self):
        from vs2convert.core import report
        s = io.StringIO()
        self.assertTrue(report.supports_unicode(s))
        self.assertEqual(s.getvalue(), '',
                         'probe wrote into the stream it was checking')


class CliInnerPathTests(unittest.TestCase):
    """The CLI's error and diagnostic branches, driven in-process.

    In-process lets a test block an import with `sys.modules[name] = None`
    against the real module, which is how the --verify degradation contract
    is forced even in an interpreter that has the render dependencies.
    """

    @classmethod
    def setUpClass(cls):
        from vs2convert import cli
        cls.cli = cli

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)

    def _run(self, argv):
        """Run cli.main() with argv. Returns (exit_code, stdout_text)."""
        out = io.StringIO()
        saved_argv = sys.argv
        sys.argv = ['vs2convert'] + argv
        try:
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                try:
                    code = self.cli.main()
                except SystemExit as e:
                    code = e.code if isinstance(e.code, int) else 0
        finally:
            sys.argv = saved_argv
        return code, out.getvalue()

    def _write(self, name, text):
        path = os.path.join(self.dir, name)
        with open(path, 'w') as f:
            f.write(text)
        return path

    # -- input / detection ------------------------------------------------

    def test_missing_input_exits_1(self):
        code, out = self._run([os.path.join(self.dir, 'nope.fs')])
        self.assertEqual(code, 1, out)
        self.assertIn('input not found', out.lower())

    def test_detector_warnings_reach_the_verdict(self):
        """Wiring test. detect_format's own warnings list is never filled
        (no `warnings.append` exists in detector.py today), so a warning is
        stubbed on the function to prove the CLI loop that surfaces detector
        warnings is live rather than dead code."""
        from vs2convert.core import detector
        real = detector.detect_format

        def with_warning(source):
            info = dict(real(source))
            info['warnings'] = list(info.get('warnings') or []) + [
                'stub warning surfacing probe']
            return info

        detector.detect_format = with_warning
        self.addCleanup(setattr, detector, 'detect_format', real)
        src = self._write('w.fs', SIMPLE_ISF)
        code, out = self._run([src])
        self.assertEqual(code, 0, out)
        self.assertIn('stub warning surfacing probe', out)

    def test_speed_warning_and_baked_step_shown(self):
        """One run, two lines: check_suitability warns about the `speed`
        parameter, and the generator's baked-input report reaches -v
        output. (The speed keyword is why it is baked, not a knob.)"""
        src = self._write(
            'spd.shadertoy',
            '/*{"parameters": [{"name": "speed", "default": 0.5}]}*/'
            'void mainImage(out vec4 f, in vec2 c){ f = vec4(c, 1.0); }')
        code, out = self._run(['-v', src])
        self.assertEqual(code, 0, out)
        self.assertIn('may be a custom speed parameter', out)
        self.assertIn('baked:', out)

    # -- pass metadata ----------------------------------------------------

    def test_missing_explicit_meta_file_exits_1(self):
        src = self._write('m.shadertoy', SHADERTOY_ONE_CHANNEL)
        code, out = self._run(['--meta',
                               os.path.join(self.dir, 'absent.json'), src])
        self.assertEqual(code, 1, out)
        self.assertIn('could not read pass metadata', out)

    def test_explicit_meta_file_is_announced(self):
        meta = os.path.join(self.dir, 'ch.json')
        with open(meta, 'w') as f:
            f.write('{"channels": {"iChannel0": {"sampler": "texture"}}}')
        src = self._write('m.shadertoy', SHADERTOY_ONE_CHANNEL)
        code, out = self._run(['-v', '--meta', meta, src])
        self.assertEqual(code, 0, out)
        self.assertIn('pass metadata from', out)

    # -- declines ---------------------------------------------------------

    def test_converter_gate_after_suitability_exits_1(self):
        """The multi-image ISF gate sits in the generator, PAST the
        suitability check: the verdict is exit 1 with the gate id, not the
        suitability gate's exit 3."""
        src = self._write('two-images.fs', MULTI_IMAGE_ISF)
        code, out = self._run([src, os.path.join(self.dir, 'out.frag')])
        self.assertEqual(code, 1, out)
        self.assertIn('declined', out)
        self.assertIn('isf-multiple-image-inputs', out)

    # -- unverified disclosure --------------------------------------------

    def test_unverified_channel_block_names_the_assumption(self):
        src = self._write('ch.shadertoy', SHADERTOY_ONE_CHANNEL)
        code, out = self._run([src])
        self.assertEqual(code, 0, out)
        self.assertIn('unverified: iChannel assumed to be a plain image', out)
        self.assertIn('Shadertoy channels can also be buffers', out)

    # -- output paths -----------------------------------------------------

    def test_refuses_to_overwrite_a_frag_input(self):
        src = self._write('keep.frag', SIMPLE_ISF)
        code, out = self._run([src])
        self.assertEqual(code, 4, out)
        self.assertIn('refusing to overwrite', out)
        with open(src) as f:
            self.assertIn('INPUTS', f.read(),
                          'the input must be untouched after the refusal')

    def test_unwritable_output_exits_4(self):
        src = self._write('ok.fs', SIMPLE_ISF)
        subdir = os.path.join(self.dir, 'adir')
        os.mkdir(subdir)
        code, out = self._run([src, subdir])
        self.assertEqual(code, 4, out)
        self.assertIn('could not write', out)

    # -- validation -------------------------------------------------------

    def test_validation_warnings_do_not_fail_the_run(self):
        src = self._write('plain.fs', NOUV_ISF)
        code, out = self._run([src])
        self.assertEqual(code, 0, out)
        self.assertIn('Missing coordinate setup', out)

    def test_forced_source_that_fails_validation_exits_1(self):
        """--force only overrides suitability. An ISF with no main() falls
        back to a body the validator rejects, so the run exits 1 and writes
        nothing - a forced conversion must never be reported as a success."""
        src = self._write('nomain.fs', NO_ENTRY_ISF)
        code, out = self._run(['--force', src])
        self.assertEqual(code, 1, out)
        self.assertIn('structural-validation', out)
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'nomain.frag')))

    # -- verify: the exit routes ------------------------------------------

    def test_renderer_import_failure_exits_2_in_process(self):
        """sys.modules[..] = None raises ImportError for those names, which
        is what a missing renderer package raises - in-process, against the
        real module, wherever moderngl is installed or not."""
        key = 'vs2convert.renderer.pipeline'
        saved = sys.modules.get(key, _ABSENT)
        sys.modules[key] = None

        def restore():
            if saved is _ABSENT:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = saved

        self.addCleanup(restore)
        src = self._write('ok.fs', SIMPLE_ISF)
        code, out = self._run(['--verify', src])
        self.assertEqual(code, 2, out)
        self.assertIn('not verified', out.lower())
        self.assertNotIn('failed', out.lower())

    def test_verify_unavailable_exits_2_not_0_or_1(self):
        """moderngl blocked: run_verification reports "unavailable". That
        is a tooling problem - exit 2, disclosed - not a shader failure
        (1) and not success (0). Also the branch where pipeline.py reports
        its own missing dependency."""
        saved = sys.modules.get('moderngl', _ABSENT)
        sys.modules['moderngl'] = None

        def restore():
            if saved is _ABSENT:
                sys.modules.pop('moderngl', None)
            else:
                sys.modules['moderngl'] = saved

        self.addCleanup(restore)
        src = self._write('ok.fs', SIMPLE_ISF)
        code, out = self._run(['--verify', src])
        self.assertEqual(code, 2, out)
        self.assertIn('not verified', out.lower())
        self.assertIn('render verification unavailable', out)

    def test_static_shader_verify_failure_exits_1_with_gate_output(self):
        """The render-failure path end to end: a bright shader that ignores
        `time` is flagged frozen, _gate_lines prints the reason, and the
        run exits 1. Needs a real GL context; skipped where there is none."""
        try:
            import moderngl
            ctx = moderngl.create_standalone_context()
            ctx.release()
        except Exception as e:
            self.skipTest('no usable GL context: %s' % e)
        src = self._write('static.fs', STATIC_ISF)
        code, out = self._run(['--verify', src])
        self.assertEqual(code, 1, out)
        self.assertIn('render check failed', out)
        self.assertIn('frozen', out)

    # -- strict -----------------------------------------------------------

    def test_strict_unverified_exits_5(self):
        src = self._write('ch.shadertoy', SHADERTOY_ONE_CHANNEL)
        code, out = self._run(['--strict', src])
        self.assertEqual(code, 5, out)
        self.assertIn('UNVERIFIED under --strict', out)


if __name__ == '__main__':
    unittest.main(verbosity=2)
