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

from core.detector import detect_format
from core.generator import convert, ConversionError


def _read_fixture(name):
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, name)) as f:
        return f.read()

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
# GLSL texture2D/tex2D is not a VS-2 API (formal-spec.md:185). Not the same
# thing as an ISF image INPUT, which uses the IMG_* accessors.
ISF_BUNDLED_TEXTURE = """/*{
 "INPUTS": [{"NAME": "Zoom", "TYPE": "float", "DEFAULT": 0.5,
             "MIN": 0.0, "MAX": 1.0}]
}*/
uniform sampler2D tex;
void main() { gl_FragColor = texture2D(tex, gl_FragCoord.xy / 100.0); }
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


ISF_IMPORTED = """/*
{
"ISFVSN": "2.0",
"INPUTS": [ { "NAME": "inputImage", "TYPE": "image" } ],
"IMPORTED": { "blendImage": { "PATH": "Hexagon.tiff" } }
}
*/
void main()
{
vec4 srcPixel = IMG_NORM_PIXEL(inputImage, isf_FragNormCoord);
vec4 blendPixel = IMG_NORM_PIXEL(blendImage, isf_FragNormCoord);
gl_FragColor = (srcPixel + blendPixel) / 2.0;
}
"""

ISF_IMAGE_INPUT = """/*
{ "ISFVSN": "2.0",
  "INPUTS": [ { "NAME": "inputImage", "TYPE": "image" } ] }
*/
void main()
{
vec4 srcPixel = IMG_NORM_PIXEL(inputImage, isf_FragNormCoord);
gl_FragColor = vec4(srcPixel.rgb, 1.0);
}
"""


class IsfImageSpecTests(unittest.TestCase):
    """ISF image handling, per docs.isf.video/ref_json and the canonical
    Test-ImportedImage.fs example.

    An earlier gate invented a `TEXTURE_*` macro concept. ISF has no such
    thing: bundled images are declared under a top-level IMPORTED key with a
    PATH, and ALL image access - bundled or host-supplied - goes through the
    IMG_* accessor family. Researched 2026-09-25.
    """

    def test_imported_bundle_is_refused(self):
        """IMPORTED means image files ship with the .fs; VS-2 cannot hold them."""
        with self.assertRaises(ConversionError) as ctx:
            convert(ISF_IMPORTED, 'ISF', 'p')
        self.assertIn('IMPORTED', str(ctx.exception))

    def test_img_norm_pixel_never_survives_into_output(self):
        """The bug this caught: the canonical spec shader converted to output
        that still called IMG_NORM_PIXEL - an ISF host function, not GLSL.
        The result would not compile, and the converter reported success."""
        frag = convert(ISF_IMAGE_INPUT, 'ISF', 'p')['frag']
        self.assertNotIn('IMG_NORM_PIXEL', frag)
        self.assertNotIn('IMG_THIS_PIXEL', frag)
        self.assertIn('texture(imageTex', frag)

    def test_host_supplied_image_input_converts_with_a_warning(self):
        report = convert(ISF_IMAGE_INPUT, 'ISF', 'p')
        names = [p['name'] for p in report['params']]
        self.assertNotIn('inputImage', names,
                         'the image is implicit, not a manifest knob')
        self.assertTrue(any('image' in w and 'VS-2' in w
                            for w in report['warnings']),
                        'must warn that an image must be set in VS-2')


class ShadertoyChannelUncertaintyTests(unittest.TestCase):
    """A Shadertoy iChannel is a binding SLOT, not a known image.

    It may hold a texture, feedback buffer, cubemap, video, keyboard or audio
    data, and the pass configuration that decides lives on the Shadertoy page
    rather than in the .txt. So mapping it to VS-2's imageTex is a guess, and
    a wrong guess yields a shader that renders but is not the original.
    """

    SRC = ('void mainImage(out vec4 f, in vec2 c){'
           ' f = vec4(texture(iChannel0, c / 9.0).rgb, 1.0); }')

    def test_channel_conversion_is_marked_unverified(self):
        report = convert(self.SRC, 'Shadertoy', 'p')
        self.assertTrue(any(w.startswith('UNVERIFIED')
                            for w in report['warnings']),
                        'a guessed iChannel mapping must be disclosed')

    def test_channel_still_converts(self):
        """Refusing every channel shader would block the common legitimate
        case of a single user-supplied image."""
        frag = convert(self.SRC, 'Shadertoy', 'p')['frag']
        self.assertIn('imageTex', frag)
        self.assertNotRegex(frag, r'\biChannel[0-3]\b')

    def test_shader_already_using_image_tex_is_not_flagged(self):
        report = convert('void main() { fragColor = texture(imageTex, texCoord); }',
                         'PlainGLSL', 'p')
        self.assertFalse(any(w.startswith('UNVERIFIED')
                             for w in report['warnings']))

    def test_channel_metadata_references_warn(self):
        src = ('void mainImage(out vec4 f, in vec2 c){'
               ' vec2 s = iChannelResolution[0].xy;'
               ' f = vec4(texture(iChannel0, c / s).rgb, 1.0); }')
        report = convert(src, 'Shadertoy', 'p')
        self.assertTrue(any('iChannelResolution' in w
                            for w in report['warnings']),
                        'channel metadata has no VS-2 equivalent and must warn')


class ShadertoyMetaTests(unittest.TestCase):
    """Optional pass metadata resolves the iChannel guessing problem.

    A Shadertoy .txt is the body only; what each channel is bound to lives in
    the web UI. With metadata the verdict is confirmed; without it the
    conversion is marked UNVERIFIED; with a buffer/cubemap/audio channel it
    is refused outright.
    """

    SRC = ('void mainImage(out vec4 f, in vec2 c){'
           ' f = vec4(texture(iChannel0, c / 9.0).rgb, 1.0); }')

    def test_absent_metadata_is_unverified(self):
        report = convert(self.SRC, 'Shadertoy', 'p')
        self.assertTrue(any(w.startswith('UNVERIFIED')
                            for w in report['warnings']))

    def test_texture_metadata_confirms(self):
        report = convert(self.SRC, 'Shadertoy', 'p', meta={
            'channels': {'iChannel0': {'sampler': 'texture',
                                       'source': 'user image'}}})
        self.assertFalse(any(w.startswith('UNVERIFIED')
                             for w in report['warnings']),
                         'metadata supplied but still flagged unverified')
        self.assertTrue(any('verified' in w for w in report['warnings']))

    def test_buffer_channel_is_refused(self):
        with self.assertRaises(ConversionError) as ctx:
            convert(self.SRC, 'Shadertoy', 'p', meta={
                'channels': {'iChannel0': {'sampler': 'buffer'}}})
        self.assertIn('buffer', str(ctx.exception))

    def test_cubemap_and_audio_are_refused(self):
        for sampler in ('cubemap', 'audio'):
            with self.assertRaises(ConversionError):
                convert(self.SRC, 'Shadertoy', 'p', meta={
                    'channels': {'iChannel0': {'sampler': sampler}}})

    def test_channel_without_sampler_stays_unverified(self):
        report = convert(self.SRC, 'Shadertoy', 'p', meta={
            'channels': {'iChannel0': {}}})
        self.assertTrue(any(w.startswith('UNVERIFIED')
                            for w in report['warnings']))

    def test_meta_resolution_helper(self):
        from core.shadertoy_meta import resolve_channels
        self.assertEqual(resolve_channels(None, ['0'])[0], 'unverified')
        self.assertEqual(resolve_channels(
            {'channels': {'iChannel0': {'sampler': 'texture'}}}, ['0'])[0],
            'confirmed')
        self.assertEqual(resolve_channels(
            {'channels': {'iChannel0': {'sampler': 'buffer'}}}, ['0'])[0],
            'unrepresentable')


GLITCH = """float sat( float t ) { return clamp( t, 0.0, 1.0 ); }

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
\tvec2 uv = fragCoord.xy / iResolution.xy;
\t//note: /\ t=[0;0.5;1], y=[0;1;0]   <-- a bare 'y' inside a COMMENT
\tfloat time = mod(iTime*100.0, 32.0)/110.0;
\tfloat GLITCH = 0.1 + iMouse.x / iResolution.x;
\tfloat r0 = sat(GLITCH);
\tfor( int i=0; i<20; ++i )
\t{
\t\tvec4 samplecol = texture( iChannel0, uv );
\t\tsum += samplecol;
\t}
\tfragColor.a = sum.a;
\tfragColor.rgb = sum.rgb;
}
"""


class RealShadertoyGlitchTests(unittest.TestCase):
    """Defects found by running a real Shadertoy glitch shader through it.

    Every case here is a genuine bug this class previously shipped, found by
    feeding it a real shader rather than a fixture designed to pass.
    """

    def test_local_named_time_does_not_self_shadow(self):
        """`float time = mod(iTime*100.0, 32.0)/110.0;` renames to
        `float time = mod(time*...)`, which initialises time from itself. The
        shader compiles and renders; it just stops animating. Structural
        validation cannot see this - only a motion check can."""
        frag = convert(GLITCH, 'Shadertoy', 'glitch')['frag']
        self.assertIn('float srcTime', frag)
        self.assertRegex(frag, r'srcTime\s*=\s*mod\(time\*',
                         'the initializer must still read the engine time')

    def test_imouse_becomes_manifest_params(self):
        """iMouse was replaced with a constant vec4(0.0), silently freezing
        every pointer-driven term. formal-spec.md:25 maps it to x/y params."""
        result = convert(GLITCH, 'Shadertoy', 'glitch')
        names = [p['name'] for p in result['params']]
        # Only the axis the body actually READS becomes a parameter. The body
        # mentions `y` twice in ways that must NOT create a knob: as member
        # access (`uv.y`) and inside a comment (`y=[0;1;0]`). Only iMouse.x
        # is a real read.
        self.assertIn('x', names)
        self.assertNotIn('y', names)
        self.assertEqual(len(names), 1, 'exactly one axis knob expected')
        self.assertNotIn('vec4(0.0))', result['frag'])
        # UNITS: Shadertoy's iMouse.xy is in PIXELS, a VS-2 knob is 0..1.
        # Emitting a bare `x` under a `/ iResolution.x` divisor would shrink
        # the term ~1000x. Emitting `x * resolution.x` preserves the source's
        # arithmetic exactly.
        self.assertRegex(result['frag'],
                         r'0\.1\s*\+\s*\(x\s*\*\s*resolution\.x\)\s*/'
                         r'\s*resolution\.x')

    def test_ishadertoy_mouse_is_expressed_in_pixels(self):
        """iMouse.xy is in PIXELS on Shadertoy; a VS-2 knob is 0..1.

        Two source forms, both of which must keep their arithmetic:
          0.1 + iMouse.x / iResolution.x   ->  0.1 + (x*resolution.x)/resolution.x
          iMouse.x / 100.0                 ->  (x*resolution.x)/100.0
        A bare `x` in the first form would give 0.5/1920 instead of 0.5.
        """
        divided = convert(
            'void mainImage(out vec4 f, in vec2 c){'
            ' float g = 0.1 + iMouse.x / iResolution.x;'
            ' f = vec4(g, g, g, 1.0); }', 'Shadertoy', 'p')['frag']
        self.assertIn('(x * resolution.x) / resolution.x', divided)
        self.assertNotRegex(divided, r'\+\s*x\s*/\s*resolution\.x')

        scaled = convert(
            'void mainImage(out vec4 f, in vec2 c){'
            ' float m = iMouse.x / 100.0;'
            ' f = vec4(m, m, m, 1.0); }', 'Shadertoy', 'p')['frag']
        self.assertIn('(x * resolution.x) / 100.0', scaled)

    def test_image_sample_v_is_flipped_for_vs2(self):
        """Shadertoy and VS-2 disagree about which way image V runs.

        Established EMPIRICALLY: the author ran a real Shadertoy glitch in
        VS-2 (2026-09-25) and the image came out upside down. The skill docs
        document no flip rule, and the factory-references image shaders are
        VS-2-native so they carry no flip either - there is no citation for
        this, only the observed behaviour. Recorded as such deliberately.

        The flip belongs at the SAMPLE, not at the uv assignment: converted
        shaders reuse one uv for procedural work, and flipping it at the
        declaration would invert that too.
        """
        frag = convert(GLITCH, 'Shadertoy', 'glitch')['frag']
        self.assertIn('1.0 - (', frag, 'image sample V is not flipped')
        self.assertRegex(frag, r'texture\(\s*imageTex\s*,\s*vec2\(')

    def test_procedural_uv_is_not_flipped(self):
        """Counterpart: a shader with no image sampling must be untouched, or
        this fix would invert every procedural shader."""
        frag = convert('void main(){ fragColor = vec4(uv, 0.5, 1.0); }',
                       'PlainGLSL', 'p')['frag']
        self.assertNotIn('1.0 -', frag)

    def test_sample_survives_extra_bias_argument(self):
        """Shadertoy allows a third bias arg on texture(); it must be kept."""
        src = ('void mainImage(out vec4 f, in vec2 c){'
               ' f = texture(iChannel0, c / iResolution.xy, -10.0); }')
        frag = convert(src, 'Shadertoy', 'p')['frag']
        self.assertIn('-10.0', frag)
        self.assertIn('1.0 - (', frag)

    def test_image_input_uses_image_alpha_not_colored_pixels(self):
        """An image-sampling shader must take alpha from the IMAGE.

        `coloredPixels` sets alpha from pixel BRIGHTNESS, which on an image
        shader makes it ~1.0 almost everywhere - the shader renders as a solid
        full-bleed rectangle and ignores the image's transparency. All three
        authoritative references use col.a, never coloredPixels
        (factory-references/VS2-image-input/: 162, :95, :85).
        """
        frag = convert(GLITCH, 'Shadertoy', 'glitch')['frag']
        self.assertIn('sum.a * alpha', frag)
        self.assertNotIn('coloredPixels', frag)

    def test_procedural_shader_keeps_colored_pixels(self):
        """The counterpart: a shader with no image sampling must be unchanged,
        or the fix would have gutted the procedural pattern."""
        src = ('void main(){ vec2 uv = gl_FragCoord.xy / resolution.xy;'
               ' fragColor = vec4(uv, 0.5, 1.0); }')
        frag = convert(src, 'PlainGLSL', 'p')['frag']
        self.assertIn('coloredPixels', frag)

    def test_mainimage_params_are_rewritten_by_name(self):
        """Shadertoy parameter names are arbitrary. Stripping the signature
        without rewriting the body left `f`/`c` referring to identifiers that
        no longer existed."""
        src = ('void mainImage(out vec4 f, in vec2 c){'
               ' vec2 uv = c / iResolution.xy;'
               ' vec4 s = texture(iChannel0, uv);'
               ' f = vec4(s.rgb, s.a); }')
        frag = convert(src, 'Shadertoy', 'p')['frag']
        # The in-parameter is a vec2 UV, so it becomes texCoord - which is
        # already 0..1, the same value Shadertoy builds with
        # `fragCoord / iResolution.xy`. Mapping it to gl_FragCoord instead is
        # what made a raymarched shader render black in VS-2
        # (author-verified 2026-09-25).
        self.assertIn('texCoord', frag)
        self.assertNotIn('gl_FragCoord', frag)
        self.assertIn('fragColor', frag)
        self.assertNotRegex(frag, r'\b[fc]\s*/')
        # the redundant divide is cancelled, not doubled
        self.assertNotIn('texCoord / resolution.xy', frag)

    def test_mainimage_is_inlined_into_main(self):
        """The wrapper form `mainImage(fragColor, gl_FragCoord.xy)` assigns
        fragColor only inside mainImage, so the external validator reports
        'no assignment to fragColor' (validate_shader.py:156) on a correct
        conversion. Every golden VS-2 shader assigns in main() directly."""
        frag = convert(GLITCH, 'Shadertoy', 'glitch')['frag']
        self.assertIn('void main(void)', frag)
        self.assertNotIn('mainImage(', frag)
        self.assertRegex(frag, r'\bfragColor\s*=\s*vec4\(')

    def test_split_fragcolor_writes_are_coalesced(self):
        """`fragColor.a = X; fragColor.rgb = Y;` is equivalent GLSL but the
        external validator requires a single `fragColor =`."""
        frag = convert(GLITCH, 'Shadertoy', 'glitch')['frag']
        self.assertNotRegex(frag, r'fragColor\s*\.\s*\w+\s*=')

    def test_image_tex_rename_survives_the_shadow_pass(self):
        """The iChannel rename produces imageTex; a broadened shadow rename
        then turned it into srcImageTex and the shader stopped compiling."""
        frag = convert(GLITCH, 'Shadertoy', 'glitch')['frag']
        self.assertIn('imageTex', frag)
        self.assertNotIn('srcImageTex', frag)

    def test_engine_vars_are_not_renamed_without_a_shadow(self):
        frag = convert('void main(){ fragColor = vec4(time); }',
                       'PlainGLSL', 'p')['frag']
        self.assertNotIn('srcTime', frag)


class GLSLSandboxTests(unittest.TestCase):
    """GLSLSandbox: uniform time/resolution declarations, no JSON manifest.

    Advertised in --help as supported but had zero test coverage until
    2026-09-25 (PRD limitation 14.2). The single manual run that "verified"
    it was exactly the kind of unverified claim this suite exists to prevent.
    """

    def test_detected_as_glslsandbox(self):
        src = _read_fixture('test_glslsandbox.shadertoy')
        self.assertEqual(detect_format(src)['format'], 'GLSLSandbox')

    def test_resolution_only_variant_is_detected(self):
        src = _read_fixture('test_glslsandbox_resolution.fragshadertoy')
        self.assertEqual(detect_format(src)['format'], 'GLSLSandbox')

    def test_converts_and_strips_uniforms(self):
        frag = convert(_read_fixture('test_glslsandbox.shadertoy'),
                       'GLSLSandbox', 'gs')['frag']
        # the `uniform` declarations must be gone: VS-2 injects them
        self.assertNotIn('uniform float time', frag)
        self.assertNotIn('uniform vec2 resolution', frag)
        # ...and the engine names must be used instead
        self.assertIn('resolution.xy', frag)
        # gl_FragColor must be rewritten to the VS-2 name, not left behind
        self.assertNotIn('gl_FragColor', frag)
        self.assertRegex(frag, r'fragColor\s*=')

    def test_output_uses_the_procedural_pattern(self):
        frag = convert(_read_fixture('test_glslsandbox.shadertoy'),
                       'GLSLSandbox', 'gs')['frag']
        self.assertIn('coloredPixels', frag)
        self.assertRegex(frag, r'fragColor\s*=\s*vec4\(')


class DetectorPrecedenceTests(unittest.TestCase):
    """A real ISF INPUTS block must not be overridden by a mainImage().

    Found in the 2026-09-25 audit: a file carrying BOTH had its ISF manifest
    silently discarded, its knobs dropped, and undeclared names left in the
    body - producing GLSL that would not compile. ISF is self-describing, so
    the manifest outranks a coincidental mainImage.
    """

    ISF_WITH_MAINIMAGE = """/*{
  "INPUTS": [ {"NAME": "Zoom", "TYPE": "float", "DEFAULT": 3.0,
               "MIN": 1.0, "MAX": 6.0} ]
}*/
void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec2 uv = fragCoord / iResolution.xy;
    fragColor = vec4(uv * Zoom, 1.0);
}
"""

    def test_isf_manifest_wins_over_mainimage(self):
        self.assertEqual(detect_format(self.ISF_WITH_MAINIMAGE)['format'],
                         'ISF')

    def test_isf_params_are_not_lost(self):
        result = convert(self.ISF_WITH_MAINIMAGE, 'ISF', 'both')
        names = [p['name'] for p in result['params']]
        self.assertIn('zoom', names, 'the ISF knob was discarded')
        self.assertIn('fragColor', result['frag'])

    def test_shadertoy_with_vs2_params_block_is_still_shadertoy(self):
        """Lowercase "parameters" is a VS-2-style manifest a Shadertoy .txt
        may carry. It must not make the file look like ISF."""
        src = ('/*{"parameters": [{"name": "detail", "default": 0.5}]}*/\n'
               'void mainImage(out vec4 f, in vec2 c){'
               ' f = vec4(0.5, 0.2, 0.1, 1.0); }')
        self.assertEqual(detect_format(src)['format'], 'Shadertoy')

    def test_itime_with_parameters_block_is_plain_glsl(self):
        """The iTime branch specifically, with no mainImage to fall back on.

        Without this case the whole VS2_PARAMETERS_RE guard was untested: a
        mutation removing it survived, because every other fixture still
        matched via mainImage and so detected correctly either way.

        The guard routes this to PlainGLSL, NOT Shadertoy, and that is
        correct: a VS-2-style `parameters` manifest must go through the path
        that reads it, or the knob is silently dropped. Verified - the knob
        survives as a param, whereas the Shadertoy path would lose it.
        """
        src = ('/*{"parameters": [{"name": "detail", "default": 0.5}]}*/\n'
               'void main(){ fragColor = vec4(iTime * 0.1 * detail); }')
        self.assertEqual(detect_format(src)['format'], 'PlainGLSL')
        result = convert(src, 'PlainGLSL', 'p')
        self.assertIn('detail', [p['name'] for p in result['params']],
                      'the manifest knob was lost on the way through')

    def test_itime_with_isf_inputs_block_is_isf(self):
        """And the mirror case: ISF INPUTS still outranks an iTime body."""
        src = ('/*{"INPUTS": [{"NAME": "Zoom", "TYPE": "float",'
               ' "DEFAULT": 1.0}]}*/\n'
               'void main(){ fragColor = vec4(iTime * Zoom); }')
        self.assertEqual(detect_format(src)['format'], 'ISF')

    def test_plain_shadertoy_still_detected(self):
        src = ('void mainImage(out vec4 f, in vec2 c){'
               ' f = vec4(0.5, 0.2, 0.1, 1.0); }')
        self.assertEqual(detect_format(src)['format'], 'Shadertoy')


MULTI_IMAGE_ISF = """/*{
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
"""

SINGLE_IMAGE_ISF = """/*{
  "INPUTS": [ {"NAME": "src0", "TYPE": "image", "SOURCE": "a"} ]
}*/
uniform sampler2D src0;
void main() {
    fragColor = texture(src0, gl_FragCoord.xy / 100.0);
}
"""


class IsfImageInputTests(unittest.TestCase):
    """Multiple distinct ISF image inputs cannot be represented.

    Found 2026-09-25 by running the GLSLOP collection: developer-k0rz1pe2.fs
    declares six image inputs (src0..src5) and passes each to a different
    call. The converter collapsed them all onto imageTex, leaving the body
    referencing samplers that were never declared - GLSL that does not
    compile. Found by a real collection, not by any test.

    This is the ISF counterpart of the Shadertoy multi-channel gate and rests
    on the same evidence: formal-spec.md:26 and :274 map every image input to
    a SINGLE sampler.
    """

    def test_multiple_image_inputs_are_declined(self):
        with self.assertRaises(ConversionError) as ctx:
            convert(MULTI_IMAGE_ISF, 'ISF', 'p')
        self.assertIn('image input', str(ctx.exception))
        self.assertIn('imageTex', str(ctx.exception))

    def test_single_image_input_still_converts(self):
        """Counterpart, so the fix cannot have blocked ordinary image shaders."""
        frag = convert(SINGLE_IMAGE_ISF, 'ISF', 'p')['frag']
        self.assertIn('imageTex', frag)
        self.assertNotIn('src0', frag)

    def test_image_input_never_becomes_a_manifest_knob(self):
        names = [p['name'] for p in
                 convert(SINGLE_IMAGE_ISF, 'ISF', 'p')['params']]
        self.assertNotIn('src0', names)



_SKIP_DIRS = {'.git', '__pycache__', 'converted_shaders', 'factory-examples',
              'factory-references', 'shader-pipeline', 'node_modules',
              'tests', 'templates', 'core', 'validators', 'renderer'}


def _collections():
    """Every directory that directly holds .fs sources.

    The other helpers in this suite use a ">=3 files" threshold to decide
    what counts as a COLLECTION. That is right for test_isf_collections,
    which asks "which directories are collections", but wrong here: it left
    ISF-shaders-main out entirely, because that tree stores ONE .fs per
    subdirectory (41 dirs, 1 file each). The corpus check was silently
    running over 7 files instead of all of them.

    So: any directory with at least one .fs, plus its ancestors up to the
    repo root, so corpus_scan.sources() can walk each one.
    """
    found = set()
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith('.') and d not in _SKIP_DIRS]
        if any(f.lower().endswith('.fs') for f in filenames):
            d = dirpath
            while d.startswith(REPO_ROOT):
                found.add(d)
                parent = os.path.dirname(d)
                if parent == d:
                    break
                d = parent
    return sorted(found)


class ISFFragCoordTests(unittest.TestCase):
    """gl_FragCoord is not usable in VS-2.

    Author-verified 2026-09-25: three variants of one julia shader differing
    ONLY in how uv was derived. The texCoord variant rendered and animated;
    both gl_FragCoord variants failed. Variants B and C were arithmetically
    identical, which is what established gl_FragCoord itself - not the
    normalisation - as the variable.

    These guard the ISF/RENDERSIZE path specifically. The Shadertoy
    mainImage path has its own tests; that one passing says nothing about
    this one, which is how a whole-shader black screen shipped.
    """

    ISF_HEAD = (
        '/*{\n'
        '    "INPUTS": [\n'
        '        {"NAME": "Dummy", "TYPE": "float", "DEFAULT": 1.0,'
        ' "MIN": 0.0, "MAX": 2.0}\n'
        '    ]\n'
        '}*/\n\n'
    )

    def _convert(self, body):
        src = (self.ISF_HEAD + 'void main() {\n' + body + '\n'
               '    gl_FragColor = vec4(color, 1.0);\n}\n')
        return convert(src, 'ISF', 'p')['frag']

    def test_isf_never_emits_gl_fragcoord(self):
        # Several algebraic shapes, all of which occur in the corpus.
        shapes = [
            '    vec3 color = vec3(gl_FragCoord.xy / RENDERSIZE.xy, 0.0);',
            '    vec3 color = vec3((gl_FragCoord.xy * 2.0 - RENDERSIZE.xy)'
            ' / RENDERSIZE.y);',
            '    vec3 color = vec3((gl_FragCoord.xy - 2.0 * RENDERSIZE)'
            ' / RENDERSIZE.y);',
            '    vec3 color = vec3(gl_FragCoord.xy / RENDERSIZE.xy * 2.0 - 1.0);',
        ]
        for shape in shapes:
            with self.subTest(shape=shape):
                frag = self._convert(shape)
                self.assertNotIn('gl_FragCoord', frag,
                                 'gl_FragCoord survived: %r' % shape)

    def test_scalar_fragcoord_swizzles_become_the_pixel_coordinate(self):
        # A pixel-scale hash, as in phreax hexflower.
        frag = self._convert(
            '    vec3 color = vec3(fract(sin(gl_FragCoord.x * 12.9898'
            ' + gl_FragCoord.y * 75.5914)));')
        self.assertNotIn('gl_FragCoord', frag)
        self.assertIn('texCoord', frag)

    def test_uv_from_texcoord_does_not_carry_a_noop_divide(self):
        frag = self._convert('    vec3 color = vec3(texCoord * 2.0 - 1.0, 0.0);')
        self.assertNotIn('gl_FragCoord', frag)
        self.assertNotIn('resolution.xy / resolution.xy', frag)

    def test_shadertoy_path_never_emits_gl_fragcoord(self):
        """The same guarantee for the Shadertoy converter.

        A Julia-shape Shadertoy source is the case the author verified, and
        the ISF corpus does not contain one, so without this the whole
        Shadertoy half of the rule is unguarded.
        """
        body = ('void mainImage(out vec4 fragColor, in vec2 fragCoord) {\n'
                '    vec2 uv = (fragCoord * 2.0 - iResolution.xy)\n'
                '        / iResolution.y;\n'
                '    fragColor = vec4(uv, 0.0, 1.0);\n}\n')
        frag = convert(body, 'Shadertoy', 'p')['frag']
        self.assertNotIn(
            'gl_FragCoord', frag,
            'the Shadertoy mainImage in-param must map to texCoord, which is '
            'already 0..1 - mapping it to gl_FragCoord produced a black '
            'screen in VS-2')

    def test_whole_corpus_output_is_fragcoord_free(self):
        """Structural check across every ISF source in the corpus.

        The unit tests above pin individual shapes; this catches a shape
        nobody thought to enumerate, which is how the phreax hexflower
        scalar-swizzle case survived the first fix."""
        import corpus_scan
        checked = 0
        for coll in _collections():
            for path in corpus_scan.sources(coll):
                if not path.lower().endswith('.fs'):
                    continue
                try:
                    with open(path, encoding='utf-8-sig',
                              errors='replace') as fh:
                        src = fh.read()
                except OSError:
                    continue
                try:
                    frag = convert(src, 'ISF', 'p')['frag']
                except Exception:
                    continue  # declined, not converted
                checked += 1
                self.assertNotIn(
                    'gl_FragCoord', frag,
                    'corpus source %s emits gl_FragCoord' % path)
        self.assertGreater(checked, 0, 'no ISF sources were checked')

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
        """Distinct case: GLSL texture2D/tex2D is unrepresentable, and an ISF
        IMPORTED bundle has nowhere to live in VS-2. Both are covered; see
        IsfImageSpecTests for the researched spec constructs."""
        with self.assertRaises(ConversionError) as ctx:
            convert(ISF_BUNDLED_TEXTURE, 'ISF', 'probe')
        self.assertIn('texture', str(ctx.exception).lower())
        with self.assertRaises(ConversionError) as ctx2:
            convert(ISF_IMPORTED, 'ISF', 'probe')
        self.assertIn('IMPORTED', str(ctx2.exception))

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
