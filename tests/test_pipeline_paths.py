"""Pipeline paths the corpus render never takes.

test_isf_corpus proves the happy path over 41 real shaders. These exercise
the pure helpers and the FAILURE branches of renderer/pipeline.py, each one
a line `run_coverage.sh` reported missed (pipeline.py was 72%):

  * manifest/parameter parsing fallbacks (no comment, broken JSON, nameless
    knob, min/max knob positions - the fly_through case in the docstring),
  * the three error returns: dependency unavailable, context creation
    failed, compile failed,
  * the outer exception handler and the release() swallow in `finally`,
  * the empty/dark/frozen pixel verdicts and the single-frame _delta guard,
  * the image-input path: sampler declaration, default-texture binding.

GL-context tests skip where there is no usable context (plain python3);
the venv has one, and that is where coverage is measured.
"""
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
VS2_DIR = os.path.dirname(TESTS_DIR)
sys.path.insert(0, VS2_DIR)

import renderer.pipeline as pipeline  # noqa: E402
from core.generator import convert  # noqa: E402

# Raw VS-2 bodies (run_verification takes .frag content, not ISF sources).
# No manifest comment: the `movement` flag defaults to True, so a static body
# IS motion-checked and judged "frozen" - which is the verdict the tests below
# pin. The dim body below opts out with an explicit movement:false comment so
# the motion check is skipped and the dark verdict is reached.
STATIC_FRAG = ('void main() {'
               ' fragColor = vec4(vec3(texCoord.x * 60.0), 1.0); }')
DARK_FRAG = ('/*{"movement": false}*/\nvoid main() {'
             ' fragColor = vec4(vec3(fract(texCoord.x * 7.0) * 0.016),'
             ' 1.0); }')
TRANSPARENT_FRAG = 'void main() { fragColor = vec4(0.0, 0.0, 0.0, 0.0); }'

IMAGE_ISF = '''/*{
  "INPUTS": [{"NAME": "img", "TYPE": "image", "SOURCE": "a"}]
}*/
void main() {
    gl_FragColor = texture(img, gl_FragCoord.xy / RENDERSIZE.xy);
}
'''


def _skip_unless_gl(test):
    """Skip unless a real GL context can be created right now."""
    try:
        import moderngl
        ctx = moderngl.create_standalone_context()
        ctx.release()
    except Exception as e:
        test.skipTest('no usable GL context: %s' % e)


def _skip_unless_pil(test):
    try:
        import PIL  # noqa: F401
    except ImportError:
        test.skipTest('Pillow missing: no default texture to bind')


class ManifestParsingFallbackTests(unittest.TestCase):
    """_manifest_params / _manifest must degrade, not raise, on input the
    engine never produces but a hand-written .frag might contain."""

    def test_no_comment_block_returns_empty(self):
        self.assertEqual([], pipeline._manifest_params('void main() {}'))
        self.assertEqual({}, pipeline._manifest('void main() {}'))

    def test_broken_json_returns_empty(self):
        src = '/*{ not json at all */\nvoid main() {}'
        self.assertEqual([], pipeline._manifest_params(src))
        self.assertEqual({}, pipeline._manifest(src))

    def test_non_dict_manifest_returns_empty(self):
        src = '/*[1, 2, 3]*/\nvoid main() {}'
        self.assertEqual([], pipeline._manifest_params(src))


class ParamUniformMappingTests(unittest.TestCase):
    """_param_values: `default` is a knob POSITION on declared [min, max],
    per the fly_through incident in the docstring (raw default 1 on max 40
    rendered a single raymarch iteration - pure black)."""

    MANIFEST = '''/*
{"parameters": [
    {"name": "light", "default": 0.5, "min": 10.0, "max": 40.0},
    {"name": "plain", "default": 0.25},
    {"name": "", "default": 0.9}
]}
*/
void main() { fragColor = vec4(1.0); }'''

    def test_min_max_position_maps_onto_range(self):
        out = pipeline._param_values(self.MANIFEST)
        self.assertEqual(25.0, out['light'],
                         '0.5 on [10, 40] must be 10 + 0.5*30')

    def test_parameter_without_max_is_passed_through(self):
        out = pipeline._param_values(self.MANIFEST)
        self.assertEqual(0.25, out['plain'])

    def test_nameless_parameter_is_skipped(self):
        out = pipeline._param_values(self.MANIFEST)
        self.assertNotIn('', out)


class DependencyUnavailableTests(unittest.TestCase):
    """run_verification must report "unavailable", never passed=True, when
    moderngl cannot be imported - that is the CLI's exit-2 contract."""

    def test_missing_moderngl_reports_unavailable(self):
        saved = sys.modules.get('moderngl', _ABSENT)
        sys.modules['moderngl'] = None
        self.addCleanup(_restore_moderngl, saved)
        res = pipeline.run_verification(STATIC_FRAG, 'probe')
        self.assertFalse(res['passed'])
        self.assertIn('unavailable', res['errors'][0])


_ABSENT = object()


def _restore_moderngl(saved):
    if saved is _ABSENT:
        sys.modules.pop('moderngl', None)
    else:
        sys.modules['moderngl'] = saved


class ContextCreationFailureTests(unittest.TestCase):
    """A context that cannot be created is a reported failure, not an
    exception escaping run_verification."""

    def test_context_creation_failure_is_reported_not_raised(self):
        try:
            import moderngl
        except ImportError:
            self.skipTest('moderngl missing')
        real = moderngl.create_standalone_context

        def boom(*a, **k):
            raise RuntimeError('no context for this test')

        moderngl.create_standalone_context = boom
        self.addCleanup(setattr, moderngl, 'create_standalone_context', real)
        res = pipeline.run_verification(STATIC_FRAG, 'probe')
        self.assertFalse(res['passed'])
        self.assertIn('could not create a GL context', res['errors'][0])


class CompileFailureTests(unittest.TestCase):
    """A body the GLSL compiler rejects must come back as a failed result
    (the CLI then exits 1 with the compiler dump), never as a raise."""

    def test_invalid_glsl_reports_compile_failure(self):
        _skip_unless_gl(self)
        res = pipeline.run_verification('void main() { this is not glsl',
                                        'probe')
        self.assertFalse(res['passed'])
        self.assertIn('shader failed to compile', res['errors'][0])


class ForcedRuntimeFailureTests(unittest.TestCase):
    """The outer except (return passed=False) and the release() swallow in
    `finally`. A release that raises must not mask the original failure -
    and must not escape, or a broken render crashes the CLI instead of
    exiting 1."""

    class FakeCtx:
        def program(self, **kwargs):
            return object()  # compiles "fine"; the failure comes next

        def buffer(self, *a, **k):
            raise RuntimeError('forced mid-render failure')

        def release(self):
            raise RuntimeError('forced release failure')

    def test_mid_render_failure_is_reported_and_release_swallows(self):
        try:
            import moderngl
        except ImportError:
            self.skipTest('moderngl missing')
        real = moderngl.create_standalone_context
        moderngl.create_standalone_context = lambda *a, **k: self.FakeCtx()
        self.addCleanup(setattr, moderngl, 'create_standalone_context', real)
        res = pipeline.run_verification(STATIC_FRAG, 'probe')
        self.assertFalse(res['passed'])
        self.assertIn('render verification failed: forced mid-render failure',
                      res['errors'][0])


class ImageInputBindingTests(unittest.TestCase):
    """The image-input path: _adapt declares the sampler only when the body
    uses it, the default texture is bound, and the render completes."""

    def test_image_input_shader_renders_with_default_texture(self):
        _skip_unless_gl(self)
        _skip_unless_pil(self)
        frag = convert(IMAGE_ISF, 'ISF', 'img_probe')['frag']
        self.assertIn('imageTex', frag)
        res = pipeline.run_verification(frag, 'img_probe')
        self.assertNotIn('shader failed to compile', res['errors'],
                         res['errors'])
        self.assertEqual(63, res['metrics'].get('frames'),
                         'the 3 wide + 60 dense frames must all render')


class PixelVerdictTests(unittest.TestCase):
    """The three pixel checks no corpus shader triggers (the corpus has one
    dark shader as its only render failure): empty, dark, frozen. Each is
    a gate the render harness claims to enforce."""

    def test_fully_transparent_output_is_empty(self):
        _skip_unless_gl(self)
        res = pipeline.run_verification(TRANSPARENT_FRAG, 'probe')
        self.assertFalse(res['passed'])
        self.assertTrue(any('empty:' in e for e in res['errors']),
                        res['errors'])

    def test_dim_gradient_is_dark_not_flat(self):
        _skip_unless_gl(self)
        res = pipeline.run_verification(DARK_FRAG, 'probe')
        self.assertFalse(res['passed'])
        self.assertTrue(any('dark:' in e for e in res['errors']),
                        res['errors'])

    def test_static_bright_output_is_frozen(self):
        _skip_unless_gl(self)
        res = pipeline.run_verification(STATIC_FRAG, 'probe')
        self.assertFalse(res['passed'])
        self.assertTrue(any('frozen:' in e for e in res['errors']),
                        res['errors'])


class SingleFrameMotionGuardTests(unittest.TestCase):
    """_delta with fewer than two frames returns 0.0 instead of raising on
    max() of an empty sequence. TIME_SAMPLES is reduced to one wide sample
    so the wide probe takes that guard."""

    def test_single_wide_sample_reports_zero_wide_motion(self):
        _skip_unless_gl(self)
        saved = pipeline.TIME_SAMPLES
        pipeline.TIME_SAMPLES = (0.0,)
        self.addCleanup(setattr, pipeline, 'TIME_SAMPLES', saved)
        res = pipeline.run_verification(STATIC_FRAG, 'probe')
        self.assertEqual(0.0, res['metrics'].get('wide_motion'),
                         res['metrics'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
