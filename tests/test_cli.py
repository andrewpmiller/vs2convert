"""End-to-end CLI tests: argument handling, output paths, exit codes.

Design decision 2 (PRD §8) is a user-facing contract and had never been
implemented - the default wrote `converted_<name>.frag` into the current
directory instead of `<name>.frag` beside the input. These tests pin it.
"""
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
VS2_DIR = os.path.dirname(TESTS_DIR)
APP = os.path.join(VS2_DIR, 'vs2_convert.py')

# Sentinel for "key was not in sys.modules at all" (None is a real value
# with a meaning: blocked import).
_ABSENT = object()

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

# A real ISF block with no main(): _convert_isf raises its no-entry-point
# gate, --force falls back, and the fallback strips uniforms but NOT
# `varying` - so --validate finds a uniform/varying declaration and must
# fail the run with exit 1.
VALIDATE_ERR_ISF = ('/*{"INPUTS": [{"NAME": "Zoom", "TYPE": "float",'
                    ' "DEFAULT": 0.5, "MIN": 0.0, "MAX": 1.0}]}*/\n'
                    'varying vec2 vPos;\n')

# Bright, static, non-flat: passes empty/flat/dark, fails the motion check
# (manifest movement defaults to true), so --verify must exit 1 "frozen".
# One knob, because an EMPTY INPUTS block declines.
STATIC_ISF = ('/*{"INPUTS": [{"NAME": "Zoom", "TYPE": "float",'
              ' "DEFAULT": 1.0, "MIN": 0.0, "MAX": 1.0}]}*/\n'
              'void main() {\n'
              '    vec2 uv = gl_FragCoord.xy / RENDERSIZE.xy;\n'
              '    gl_FragColor = vec4(vec3(uv.x * 60.0 * Zoom), 1.0);\n'
              '}\n')


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


class UnicodeProbeTests(unittest.TestCase):
    """supports_unicode must decide without writing to the stream it probes.

    The old probe tested encodability by writing the three glyphs into the
    caller's stream, so a writable stream with no `encoding` came back True
    with the glyphs already emitted. test_probe_writes_nothing pins it.
    """

    def test_probe_writes_nothing(self):
        import core.report as report
        s = io.StringIO()
        self.assertTrue(report.supports_unicode(s))
        self.assertEqual(s.getvalue(), '',
                         'probe wrote into the stream it was checking')


class VerifyContractTests(unittest.TestCase):
    """--verify must never exit 0 when the render check did not run.

    This FORCES the failure instead of waiting for an interpreter without
    moderngl. The two tests above skip whenever moderngl is present, so between
    them the contract was guarded only in the interpreter where --verify cannot
    run at all: in the venv they skip, and in plain python3 the corpus render
    test skips instead. A gate that cannot fire in the environment it describes
    is the failure mode tests/test_gate_audit.py exists to catch.

    Forced how: a throwaway `renderer/` package whose import raises ImportError
    goes first on PYTHONPATH, and the CLI is copied beside it so that its own
    directory (sys.path[0], and the PROJECT_ROOT it inserts) holds no
    `renderer/`. Everything else resolves from vs2_convert as usual.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        with open(os.path.join(self.dir, 'ok.fs'), 'w') as f:
            f.write(SIMPLE_ISF)

    def test_render_import_failure_exits_2_even_where_moderngl_is_present(self):
        shim = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, shim)
        os.mkdir(os.path.join(shim, 'renderer'))
        with open(os.path.join(shim, 'renderer', '__init__.py'), 'w') as f:
            f.write('raise ImportError("forced for the exit-code contract")\n')

        appdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, appdir)
        app = os.path.join(appdir, 'vs2_convert.py')
        shutil.copy2(APP, app)

        env = dict(os.environ, PYTHONPATH=os.pathsep.join([shim, VS2_DIR]))
        # The child must NOT be coverage-instrumented: it runs a COPY of the
        # app under a temporary path, and addCleanup deletes that path before
        # run_coverage.sh's `coverage report` step. The dangling data file
        # makes report exit 1 with "No source for code: .../vs2_convert.py".
        # Reproduced with `sh vs2_convert/tests/run_coverage.sh` before this
        # line existed; the CLI-only paths are still measured because the
        # other test_cli tests run APP at its real path.
        env.pop('COVERAGE_PROCESS_START', None)
        env.pop('COVERAGE_FILE', None)
        r = subprocess.run([sys.executable, app, 'ok.fs', '--verify'],
                           cwd=self.dir, capture_output=True, text=True,
                           env=env)
        self.assertEqual(
            r.returncode, 2,
            '--verify exited %s when the render check could not run. Exit 0 '
            'here reports an unverified shader as verified. Output: %s'
            % (r.returncode, (r.stdout + r.stderr)[-300:]))
        self.assertIn('not verified', r.stdout.lower())
        self.assertNotIn('failed', r.stdout.lower())


class CliInnerPathTests(unittest.TestCase):
    """The CLI's error and diagnostic branches, driven in-process.

    WHY IN-PROCESS rather than subprocess like FlagTests:

    * The renderer-ImportError branch (vs2_convert.py `except ImportError:
      ... exit 2`) can only fire when `renderer/` is absent beside the app.
      In a subprocess the real package sits at sys.path[0], so
      VerifyContractTests copies the app next to a throwing shim - and a
      copy runs under a path coverage cannot attribute. In-process,
      `sys.modules['renderer'] = None` raises the same ImportError against
      the REAL file, so those lines land in the coverage report too.

    * Coverage attribution: subprocess children record vs2_convert.py only
      when they execute the real path. Everything here runs main() directly
      against the real file, which is how these branches entered
      `run_coverage.sh`'s report (vs2_convert.py 68% -> 100%).

    VerifyContractTests stays: it proves the contract through a real
    process. These prove the same contract plus the branches around it.
    Permanent red-proofs for the key ones live in mutants.py.
    """

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('vs2_cli_under_test',
                                                      APP)
        cls.cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.cli)

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)

    def _run(self, argv):
        """Run cli.main() with argv. Returns (exit_code, stdout_text)."""
        out = io.StringIO()
        saved_argv = sys.argv
        sys.argv = ['vs2_convert.py'] + argv
        try:
            with redirect_stdout(out):
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
        import core.detector as detector
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
        suitability check: the verdict is exit 1 with the skill handoff
        message, not the suitability gate's exit 3."""
        src = os.path.join(VS2_DIR, 'GLSLOP_collection',
                           'developer-k0rz1pe2.fs')
        self.assertTrue(os.path.exists(src), 'fixture missing: %s' % src)
        code, out = self._run([src, os.path.join(self.dir, 'out.frag')])
        self.assertEqual(code, 1, out)
        self.assertIn('declined', out)
        self.assertIn('vs2-shader-expert', out)

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
        code, out = self._run(['--validate', src])
        self.assertEqual(code, 0, out)
        self.assertIn('Missing coordinate setup', out)

    def test_validation_errors_fail_with_exit_1(self):
        """--force output can carry a construct the validator REJECTS: an
        ISF with no main() raises the entry-point gate, the fallback strips
        uniforms but leaves `varying`, so --validate must fail the run - a
        validator ERROR that only warned would let a known-broken forced
        conversion exit 0."""
        src = self._write('nomain.fs', VALIDATE_ERR_ISF)
        code, out = self._run(['--force', '--validate', src])
        self.assertEqual(code, 1, out)
        self.assertIn('validation failed', out)

    # -- verify: the three exit routes ------------------------------------

    def test_renderer_import_failure_exits_2_in_process(self):
        """sys.modules[..] = None raises ImportError for those names, which
        is what a missing renderer package raises - in-process, against the
        real file, wherever moderngl is installed or not."""
        saved = {}
        for key in ('renderer', 'renderer.pipeline'):
            saved[key] = sys.modules.get(key, _ABSENT)
            sys.modules[key] = None

        def restore():
            for key, val in saved.items():
                if val is _ABSENT:
                    sys.modules.pop(key, None)
                else:
                    sys.modules[key] = val

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
