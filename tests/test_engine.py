"""Deterministic tests for the vs2_convert conversion engine.

Run: python3 vs2_convert/tests/test_engine.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(TESTS_DIR))
sys.path.insert(0, os.path.dirname(TESTS_DIR))

from core.generator import convert, ConversionError

VALIDATOR = os.path.join(REPO_ROOT, '.cline', 'skills', 'vs2-shader-expert',
                         'scripts', 'validate_shader.py')

SHADERTOY = """// test
/*
{ "parameters": [
    {"name": "detail", "default": 0.5},
    {"name": "speed", "default": 0.5} ] }
*/
void mainImage(out vec4 fragColor, in vec2 fragCoord)
{
    vec2 uv = fragCoord / iResolution.xy;
    float t = iTime * 0.5;
    vec3 color = vec3(sin(uv.x * 10.0 + t));
    fragColor = vec4(color, 1.0);
}
"""

ISF = """/*{
 "INPUTS": [
  {"NAME": "Zoom", "TYPE": "float", "DEFAULT": 3.0, "MIN": 1.0, "MAX": 6.0},
  {"NAME": "RotateSpeed", "TYPE": "float", "DEFAULT": 0.4, "MIN": 0.0, "MAX": 2.0},
  {"NAME": "Tint", "TYPE": "color", "DEFAULT": [0.3, 0.7, 0.9, 1.0]}
 ]
}*/

float helper(float zoom, float x)
{
    return zoom * x;
}

void main() {
    vec2 uv = gl_FragCoord.xy / RENDERSIZE.xy;
    float v = helper(Zoom, uv.x + TIME * RotateSpeed);
    vec3 c = vec3(v) * Tint.rgb;
    gl_FragColor = vec4(c, 1.0);
}
"""

ISF_FEEDBACK = """/*{
 "INPUTS": [{"NAME": "Zoom", "TYPE": "float", "DEFAULT": 1.0, "MIN": 0.0, "MAX": 2.0}],
 "PASSES": [{"TARGET": "bufferA", "PERSISTENT": true}, {}]
}*/
void main() {
    vec4 prev = IMG_NORM_PIXEL(bufferA, isf_FragNormCoord);
    gl_FragColor = prev;
}
"""

# A declared image INPUT: the shader says it requires an image. VS-2 satisfies
# this from the layer's configured image via imageTex, so it must convert.
ISF_IMAGE = """/*{
 "INPUTS": [{"NAME": "img", "TYPE": "image"},
            {"NAME": "Zoom", "TYPE": "float", "DEFAULT": 0.5,
             "MIN": 0.0, "MAX": 1.0}]
}*/
uniform sampler2D img;
void main() {
    vec2 uv = gl_FragCoord.xy / RENDERSIZE.xy;
    vec3 c = texture(img, uv * (0.5 + Zoom)).rgb;
    gl_FragColor = vec4(c, 1.0);
}
"""

# A BUNDLED texture: the source embeds pixel data (TEXTURE_0), so there is no
# image for VS-2 to bind and nothing to sample. Still a hard gate. Note this is
# NOT the same as IMG_NORM_PIXEL(img, ...) which names a declared input.
ISF_BUNDLED_TEXTURE = """/*{
 "INPUTS": [{"NAME": "Zoom", "TYPE": "float", "DEFAULT": 0.5,
             "MIN": 0.0, "MAX": 1.0}]
}*/
void main() { gl_FragColor = IMG_NORM_PIXEL(TEXTURE_0, isf_FragNormCoord); }
"""


def validate_text(text, name='probe'):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, f'{name}.frag')
        with open(p, 'w') as f:
            f.write(text)
        r = subprocess.run([sys.executable, VALIDATOR, p],
                           capture_output=True, text=True)
        return r.returncode, r.stdout


class ConvertShadertoyTests(unittest.TestCase):
    def test_produces_valid_frag(self):
        result = convert(SHADERTOY, 'Shadertoy', 'probe')
        rc, out = validate_text(result['frag'])
        self.assertEqual(rc, 0, out)

    def test_output_pattern_and_aliases(self):
        frag = convert(SHADERTOY, 'Shadertoy', 'probe')['frag']
        self.assertIn('coloredPixels', frag)
        self.assertIn('void main(void)', frag)
        self.assertNotIn('iResolution', frag)
        self.assertNotIn('iTime', frag)

    def test_speed_param_baked_not_in_manifest(self):
        result = convert(SHADERTOY, 'Shadertoy', 'probe')
        names = [p['name'] for p in result['params']]
        self.assertIn('detail', names)
        self.assertNotIn('speed', names)
        self.assertTrue(any('speed' in b for b in result['baked']))

    def test_local_color_shadow_renamed(self):
        frag = convert(SHADERTOY, 'Shadertoy', 'probe')['frag']
        # the local `vec3 color` must not shadow the engine tint uniform
        self.assertIn('srcColor', frag)
        self.assertIn('color.rgb', frag)


class ConvertIsfTests(unittest.TestCase):
    def test_produces_valid_frag(self):
        result = convert(ISF, 'ISF', 'probe')
        rc, out = validate_text(result['frag'])
        self.assertEqual(rc, 0, out)

    def test_knob_range_remap(self):
        frag = convert(ISF, 'ISF', 'probe')['frag']
        # Zoom default 3.0 in [1,6] -> knob 0.4, body keeps absolute range
        self.assertIn('(1 + zoom * (6 - 1))', frag.replace('.0', ''))

    def test_speed_input_baked(self):
        result = convert(ISF, 'ISF', 'probe')
        names = [p['name'] for p in result['params']]
        self.assertNotIn('rotate_speed', names)

    def test_param_declaration_not_wrapped(self):
        # helper(float zoom, ...) must stay a declaration, not an expression
        frag = convert(ISF, 'ISF', 'probe')['frag']
        self.assertNotRegex(frag, r'float\s*\(')

    def test_color_input_baked_to_literal(self):
        frag = convert(ISF, 'ISF', 'probe')['frag']
        self.assertIn('vec4(0.3, 0.7, 0.9, 1.0)', frag)

    def test_seven_param_budget_enforced(self):
        """Hard rule 2: at most 7 manifest parameters.

        Found by the mutation harness - `generator: drop the 7-parameter
        budget` survived, meaning nothing asserted this. VS-2 will not load
        a shader declaring more than 7, so silently emitting 8 is a hard
        failure at runtime, not a cosmetic overrun.
        """
        # Ten float inputs, no speed keywords, so none get baked for other
        # reasons and the budget is the only thing that can cap the count.
        inputs = ', '.join(
            '{"NAME": "Level%d", "TYPE": "float", "DEFAULT": 0.5, '
            '"MIN": 0.0, "MAX": 1.0}' % i for i in range(10))
        src = '/*{\n "INPUTS": [%s]\n}*/\nvoid main() {\n' % inputs
        src += '    vec2 uv = gl_FragCoord.xy / RENDERSIZE.xy;\n'
        src += '    float v = uv.x;\n'
        src += '    gl_FragColor = vec4(v, v, v, 1.0);\n}\n'

        result = convert(src, 'ISF', 'probe')
        names = [p['name'] for p in result['params']]
        self.assertLessEqual(
            len(names), 7,
            'emitted %d parameters, over the 7-param budget: %s'
            % (len(names), names))
        self.assertTrue(
            any('budget' in b for b in result['baked']),
            'over-budget inputs must be reported as baked, got: %s'
            % result['baked'])

    def test_magnitude_fidelity_round_trip(self):
        """The knob default in the emitted manifest, substituted into the
        emitted body formula, must reproduce the source's original DEFAULT.

        This is the single most important correctness property of a
        conversion tool: a knob sitting at its default position must render
        what the source shader rendered at its default. String-matching the
        formula (as test_knob_range_remap does) proves the text is present,
        not that the arithmetic round-trips.
        """
        frag = convert(ISF, 'ISF', 'probe')['frag']

        # Source INPUTS defaults, keyed by the knob name they map to.
        # (ISF fixture: Zoom DEFAULT 3.0 over MIN 1.0 / MAX 6.0.)
        source_default = {'zoom': 3.0}

        # 1. knob defaults from the emitted manifest
        manifest = json.loads(re.search(r'/\*(.*?)\*/', frag, re.DOTALL).group(1))
        knob_defaults = {p['name']: p['default']
                         for p in manifest.get('parameters', [])}

        # 2. body formulas: "(LO + name * (HI - LO))"
        pattern = re.compile(
            r'\(\s*([-\d.]+)\s*\+\s*(\w+)\s*\*\s*\(([-\d.]+)\s*-\s*([-\d.]+)\)\s*\)')

        checked = 0
        for lo_s, name, hi_s, lo2_s in pattern.findall(frag):
            if name not in knob_defaults or name not in source_default:
                continue
            lo, hi, lo2 = float(lo_s), float(hi_s), float(lo2_s)
            self.assertEqual(lo, lo2,
                             f"formula for '{name}' has mismatched lo bounds")
            # 3. substitute the knob default and evaluate the arithmetic
            knob = knob_defaults[name]
            restored = lo + knob * (hi - lo)
            checked += 1
            self.assertAlmostEqual(
                restored, source_default[name], places=6,
                msg=(f"magnitude drift on '{name}': knob {knob} in range "
                     f"[{lo}, {hi}] restored {restored}, expected "
                     f"{source_default[name]}"))

        self.assertGreater(checked, 0,
                           'no knob formula matched - test would be vacuous')


class HardGateTests(unittest.TestCase):
    def test_feedback_buffer_rejected(self):
        with self.assertRaises(ConversionError) as ctx:
            convert(ISF_FEEDBACK, 'ISF', 'probe')
        self.assertIn('feedback', str(ctx.exception))

    def test_declared_image_input_converts_to_image_tex(self):
        """An ISF `TYPE: image` input is NOT an image to be emitted.

        An earlier revision rejected these, claiming the converter "would have
        to emit an image the user hasn't supplied". That was wrong: the input
        is the shader declaring that it REQUIRES an image, the same role as a
        Shadertoy iChannel0 read. VS-2 binds the layer's configured image to
        imageTex, and ignores a configured image for shaders that don't sample
        it. Nothing is emitted, so nothing needs supplying.

        formal-spec.md: "No image parameter is declared in the manifest. The
        image is implicit."
        """
        result = convert(ISF_IMAGE, 'ISF', 'probe')
        frag = result['frag']
        # the sampler resolves against the engine's binding
        self.assertIn('texture(imageTex', frag)
        # and the image input never becomes a manifest knob
        names = [p['name'] for p in result['params']]
        self.assertNotIn('img', names)
        self.assertIn('zoom', names)
        # the user is told it needs an image configured in VS-2
        self.assertTrue(any('image' in w and 'VS-2' in w
                            for w in result['warnings']),
                        'must warn that an image is required in VS-2')

    def test_bundled_texture_still_rejected(self):
        """Distinct case: a source that embeds pixels has no image for VS-2 to
        bind, so the bundled-texture gate still applies."""
        with self.assertRaises(ConversionError) as ctx:
            convert(ISF_BUNDLED_TEXTURE, 'ISF', 'probe')
        self.assertIn('texture', str(ctx.exception).lower())

    def test_hlsl_rejected(self):
        with self.assertRaises(ConversionError):
            convert('void main(){ float x = frac(1.0); }', 'PlainGLSL', 'p')

    def test_single_ichannel_renames_to_image_tex(self):
        """A single iChannel0 read is an ordinary image-input shader, not a
        hard gate.

        An earlier revision raised ConversionError here on the premise that
        image input was "not understood". formal-spec.md documents the
        mapping as a rename (iChannel0..3 -> imageTex), VS-2 binds the layer's
        configured image there, and the gallery previews such shaders against
        a default image. Converted output must sample imageTex and carry a
        warning that the shader needs an image set in VS-2.
        """
        src = SHADERTOY.replace('fragColor = vec4(color, 1.0);',
                                'fragColor = texture(iChannel0, uv);')
        report = convert(src, 'Shadertoy', 'p')
        frag = report['frag']
        self.assertIn('imageTex', frag)
        self.assertNotRegex(frag, r'\biChannel[0-3]\b')
        self.assertTrue(any('imageTex' in w for w in report['warnings']),
                        'must warn that an image is required in VS-2')

    def test_multiple_channels_rejected(self):
        """VS-2 exposes a single imageTex, so reading two distinct channels
        cannot be preserved. This is a real limit and stays a hard gate."""
        src = SHADERTOY.replace('fragColor = vec4(color, 1.0);',
                                'fragColor = texture(iChannel0, uv) '
                                '* texture(iChannel1, uv);')
        with self.assertRaises(ConversionError) as ctx:
            convert(src, 'Shadertoy', 'p')
        self.assertIn('iChannel', str(ctx.exception))


if __name__ == '__main__':
    unittest.main(verbosity=2)
