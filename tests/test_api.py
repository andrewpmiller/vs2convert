"""The public API contract: a result for every input, never an exception."""
import unittest

from vs2convert.api import ConversionResult, Rejection, convert
from vs2convert.core.gates import GATES

ISF = '''/*{
  "INPUTS": [{"NAME": "Zoom", "TYPE": "float", "DEFAULT": 0.5,
              "MIN": 0.0, "MAX": 1.0}]
}*/
void main() {
    vec2 uv = gl_FragCoord.xy / RENDERSIZE.xy;
    gl_FragColor = vec4(uv, 0.0, 1.0);
}
'''

SHADERTOY_TWO_CHANNELS = (
    'void mainImage(out vec4 f, in vec2 c){'
    ' f = texture(iChannel0, c) * texture(iChannel1, c); }')

HLSL = 'float4 main(float2 uv) : SV_Target { return float4(frac(uv), 0, 1); }'

REDECL = ('uniform vec2 resolution;\n'
          'void mainImage(out vec4 f, in vec2 c){'
          ' f = vec4(c / resolution, 0.0, 1.0); }')

ISF_TWO_IMAGES = '''/*{
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

NO_ENTRY_ISF = ('/*{"INPUTS": [{"NAME": "Zoom", "TYPE": "float",'
                ' "DEFAULT": 0.5, "MIN": 0.0, "MAX": 1.0}]}*/\n'
                'varying vec2 vPos;\n')

# Shapes that parse as text but not as a manifest. All three raised a raw
# exception before the API boundary existed.
MALFORMED = {
    'empty': '',
    'binary junk': '\x00\x01\x02 not a shader',
    'ISF INPUTS is a string': '/*{"INPUTS": "nope"}*/ void main(){}',
    'ISF INPUTS list of ints': '/*{"INPUTS": [1, 2]}*/ void main(){}',
    'ISF DEFAULT null': ('/*{"INPUTS": [{"NAME": "Z", "TYPE": "float",'
                         ' "DEFAULT": null, "MIN": 0, "MAX": 1}]}*/'
                         ' void main(){}'),
    'ISF header truncated': '/*{"INPUTS": [{"NAME": "Z",',
    'nine parameters': ('/*{"parameters": [%s]}*/'
                        ' void mainImage(out vec4 f, in vec2 c){'
                        ' f = vec4(c, 1.0); }'
                        % ', '.join('{"name": "p%d", "default": 0.5}' % i
                                    for i in range(9))),
    'no entry point': 'varying vec2 vPos;\n',
    'two channels': SHADERTOY_TWO_CHANNELS,
    'HLSL': HLSL,
}


class ShapeTests(unittest.TestCase):
    def test_ok_result_shape(self):
        r = convert(ISF, name='probe.fs')
        self.assertIsInstance(r, ConversionResult)
        self.assertTrue(r.ok)
        self.assertIsInstance(r.frag, str)
        self.assertIn('fragColor', r.frag)
        self.assertIsInstance(r.manifest, dict)
        self.assertIsInstance(r.params, list)
        self.assertIsInstance(r.warnings, list)
        self.assertIsInstance(r.baked, list)
        self.assertEqual([], r.rejections)
        self.assertEqual('ISF', r.format)
        self.assertEqual('probe.fs', r.source_name)

    def test_decline_result_shape(self):
        r = convert(SHADERTOY_TWO_CHANNELS, name='probe')
        self.assertIsInstance(r, ConversionResult)
        self.assertFalse(r.ok)
        self.assertIsNone(r.frag)
        self.assertIsNone(r.manifest)
        self.assertEqual([], r.params)
        self.assertTrue(r.rejections)
        rejection = r.rejections[0]
        self.assertIsInstance(rejection, Rejection)
        for value in (rejection.stage, rejection.gate,
                      rejection.reason, rejection.detail):
            self.assertTrue(value)

    def test_every_rejection_gate_is_registered(self):
        known = {g.id for g in GATES}
        for label, source in MALFORMED.items():
            with self.subTest(label=label):
                r = convert(source)
                for rejection in r.rejections:
                    self.assertIn(rejection.gate, known)


class NeverRaisesTests(unittest.TestCase):
    def test_malformed_sources_return_a_decline(self):
        for label, source in MALFORMED.items():
            with self.subTest(label=label):
                r = convert(source)
                self.assertIsInstance(r, ConversionResult)
                self.assertFalse(r.ok, '%s was accepted' % label)
                self.assertTrue(r.rejections, '%s gave no reason' % label)

    def test_non_conversion_exceptions_become_internal_error(self):
        for label in ('ISF INPUTS is a string', 'ISF INPUTS list of ints',
                      'ISF DEFAULT null'):
            with self.subTest(label=label):
                r = convert(MALFORMED[label])
                self.assertEqual('internal-error', r.rejections[0].gate)


    def test_isf_without_main_is_declined(self):
        src = ('/*{"INPUTS": [{"NAME": "Zoom", "TYPE": "float",'
               ' "DEFAULT": 0.5, "MIN": 0.0, "MAX": 1.0}]}*/\n'
               '// a header but no entry point\n')
        r = convert(src)
        self.assertFalse(r.ok)
        self.assertIn('missing-entry-point',
                      [x.gate for x in r.rejections])

    def test_isf_surplus_inputs_are_baked_not_declined(self):
        """Known gap, recorded rather than hidden.

        The parameter budget is measured from Shadertoy "parameters" blocks
        and uniform declarations. ISF INPUTS are not counted, so a 9-input
        ISF converts with 7 knobs and the surplus is baked to its DEFAULT.
        See the `too-many-parameters` gate evidence. Pinned so a future fix
        changes this test deliberately instead of silently.
        """
        inputs = ', '.join(
            '{"NAME": "k%d", "TYPE": "float", "DEFAULT": 0.5,'
            ' "MIN": 0, "MAX": 1}' % i for i in range(9))
        src = ('/*{"INPUTS": [%s]}*/'
               ' void main(){ gl_FragColor = vec4(k8); }' % inputs)
        r = convert(src)
        self.assertTrue(r.ok, r.rejections)
        self.assertEqual(7, len(r.params))
        self.assertIn('0.5', r.frag)


class ForceTests(unittest.TestCase):
    def test_force_marks_the_result_forced(self):
        r = convert(REDECL, force=True)
        self.assertTrue(r.ok, r.rejections)
        self.assertTrue(any('FORCED' in w for w in r.warnings))

    def test_force_does_not_override_hlsl(self):
        r = convert(HLSL, force=True)
        self.assertFalse(r.ok)
        self.assertIn('hlsl-milkdrop',
                      [x.gate for x in r.rejections])

    def test_force_does_not_collapse_images(self):
        r = convert(SHADERTOY_TWO_CHANNELS, force=True)
        self.assertFalse(r.ok)
        self.assertIn('shadertoy-multi-channel',
                      [x.gate for x in r.rejections])

    def test_force_does_not_override_the_image_budget(self):
        r = convert(ISF_TWO_IMAGES, force=True)
        self.assertFalse(r.ok)
        self.assertIn('isf-multiple-image-inputs',
                      [x.gate for x in r.rejections])

    def test_force_does_not_override_the_parameter_budget(self):
        source = ('/*{"parameters": [%s]}*/'
                  ' void mainImage(out vec4 f, in vec2 c){'
                  ' f = vec4(c, 1.0); }'
                  % ', '.join('{"name": "p%d", "default": 0.5}' % i
                              for i in range(9)))
        r = convert(source, force=True)
        self.assertFalse(r.ok)
        self.assertIn('too-many-parameters',
                      [x.gate for x in r.rejections])


class ValidationFlagTests(unittest.TestCase):
    def test_validate_false_skips_structural_validation(self):
        """--force leaves a body the validator rejects; validate=False is
        the documented way to get that body back as data."""
        strict = convert(NO_ENTRY_ISF, force=True)
        self.assertFalse(strict.ok)
        self.assertEqual('structural-validation', strict.rejections[0].gate)

        loose = convert(NO_ENTRY_ISF, force=True, validate=False)
        self.assertTrue(loose.ok)
        self.assertIn('varying', loose.frag)


if __name__ == '__main__':
    unittest.main()
