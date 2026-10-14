"""The shader-pipeline pixel gate: skip counting, movement flag, word boundary.

Pins the three fixes from the 2026-10-14 audit plan (Phase 2). The repro run
of this file on the pre-fix tree: 3 failures — no_manifest PASSED with exit 0,
the static checkerboard was failed as frozen via the "runtime" comment, and
the is_animated substring test fired on "runtime". The positive control
(test_verify_fails_a_frozen_animated_shader) passed before and after, and is
what keeps the fixes from turning the gate into a rubber stamp.

Import caution (load-bearing): do NOT import shader-pipeline's pipeline.py
in-process — tests/test_pipeline_paths.py already caches `renderer`
(= vs2_convert/renderer) in sys.modules, and pipeline.py does
`from renderer import HeadlessRenderer`, which would resolve to the wrong
module. manifest_parser.py is stdlib-only and is loaded by path; pipeline.py
is exercised via subprocess runs of the venv interpreter (the VENV_PY pattern
from test_gate_audit.py).
"""
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
VS2_DIR = os.path.dirname(TESTS_DIR)
REPO_ROOT = os.path.dirname(VS2_DIR)
PIPELINE_DIR = os.path.join(REPO_ROOT, 'shader-pipeline')
VENV_PY = os.path.join(PIPELINE_DIR, '.venv', 'bin', 'python')

_spec = importlib.util.spec_from_file_location(
    'sp_manifest_parser', os.path.join(PIPELINE_DIR, 'manifest_parser.py'))
manifest_parser = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(manifest_parser)


STATIC_CHECKERBOARD = '''void main() {
  vec2 cell = floor(texCoord * 12.0);
  float checker = mod(cell.x + cell.y, 2.0);
  fragColor = vec4(vec3(0.2 + 0.6 * checker), 1.0);
}'''

# The word "runtime" in a comment used to flip manifest.is_animated to True via
# the 'time' substring test; pinned by test_is_animated_ignores_runtime_word.
STATIC_WITH_RUNTIME_COMMENT = '''/*{"name": "static_checker", "movement": false}*/
// this branch is checked at runtime
''' + STATIC_CHECKERBOARD

# movement: true + a body that reads `time` but whose output never moves. The
# gate must still call this frozen; pinned by
# test_verify_fails_a_frozen_animated_shader.
FROZEN_BUT_ANIMATED = '''/*{"name": "frozen_animated", "movement": true}*/
void main() {
  vec2 cell = floor(texCoord * 12.0);
  float checker = mod(cell.x + cell.y, 2.0);
  float drift = 0.0 * time;
  fragColor = vec4(vec3(0.2 + 0.6 * checker + drift), 1.0);
}'''

# A visible shader with NO manifest comment: the pipeline skips it without
# rendering, and --verify must count that as a failure, not a pass; pinned by
# test_verify_fails_a_manifestless_shader.
MANIFESTLESS = STATIC_CHECKERBOARD

# validation_enabled: false and a ~1s time sample, per the audit repro.
CONFIG_TEMPLATE = '''shader_source_dirs:
  - shaders
output_dir: output
thumbnail_dir: output/thumbnails
cache_file: .cache/render_cache.json
thumbnail_width: 128
thumbnail_height: 128
time_samples:
  - 0.0
  - 1.0
validation_enabled: false
'''


def _skip_unless_venv_gl(test):
    """Skip unless the venv interpreter can create a GL context right now."""
    if not os.path.exists(VENV_PY):
        test.skipTest('venv interpreter missing: %s' % VENV_PY)
    proc = subprocess.run(
        [VENV_PY, '-c',
         'import moderngl; moderngl.create_standalone_context().release()'],
        cwd=PIPELINE_DIR, capture_output=True, text=True)
    if proc.returncode != 0:
        lines = proc.stderr.strip().splitlines() or ['no stderr']
        test.skipTest('no usable GL context in venv: %s' % lines[0])


def run_verify(frag_text, filename):
    """Run pipeline.py --verify over a one-shader temp source dir.

    Returns (returncode, combined output). The temp config points the pipeline
    at a throwaway shader dir, so the real config is never touched.
    """
    with tempfile.TemporaryDirectory() as td:
        shaders = Path(td) / 'shaders'
        shaders.mkdir()
        (shaders / filename).write_text(frag_text)
        config = Path(td) / 'config.yaml'
        config.write_text(CONFIG_TEMPLATE)
        proc = subprocess.run(
            [VENV_PY, 'pipeline.py', '--config', str(config), '--verify'],
            cwd=PIPELINE_DIR, capture_output=True, text=True, timeout=300)
    return proc.returncode, proc.stdout + proc.stderr


class IsAnimatedDetectionTests(unittest.TestCase):
    """manifest_parser.is_animated: word boundary, comments stripped."""

    def _parse(self, body):
        with tempfile.TemporaryDirectory() as td:
            frag = Path(td) / 'probe.frag'
            frag.write_text('/*{"name": "probe"}*/\n' + body)
            return manifest_parser.parse_manifest(str(frag))

    def test_is_animated_ignores_runtime_word(self):
        static = self._parse('// checked at runtime\n' + STATIC_CHECKERBOARD)
        self.assertFalse(static.is_animated,
                         "'runtime' in a comment must not count as animation")
        animated = self._parse(
            'void main() { fragColor = vec4(vec3(time), 1.0); }')
        self.assertTrue(animated.is_animated,
                        '`time` in code must count as animation')


class VerifyGateTests(unittest.TestCase):
    """pipeline.py --verify end-to-end via subprocess (never imported)."""

    def test_verify_fails_a_manifestless_shader(self):
        _skip_unless_venv_gl(self)
        rc, out = run_verify(MANIFESTLESS, 'no_manifest.frag')
        self.assertEqual(1, rc, out)
        self.assertIn('FAILED', out)

    def test_verify_does_not_fail_a_static_shader(self):
        _skip_unless_venv_gl(self)
        rc, out = run_verify(STATIC_WITH_RUNTIME_COMMENT, 'static_checker.frag')
        self.assertEqual(0, rc, out)
        self.assertIn('PASSED', out)

    def test_verify_fails_a_frozen_animated_shader(self):
        _skip_unless_venv_gl(self)
        rc, out = run_verify(FROZEN_BUT_ANIMATED, 'frozen_animated.frag')
        self.assertEqual(1, rc, out)
        self.assertIn('frozen', out)


if __name__ == '__main__':
    unittest.main(verbosity=2)
