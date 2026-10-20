"""Enforce that every refusal names its evidence and what it still allows.

The failure this prevents: a refusal justified by a reason that was never
checked. Three such claims shipped in this project - "custom uniforms are
valid", "image-input is unsupported", "ISF image input would need an image
supplied" - each of which blocked shaders that converted fine, each reported
as a property of the engine rather than a gap in our own tooling.

A refusal is a claim about reality. This suite holds it to the standard of
one, mechanically:

  - every gate must cite evidence (a spec line, a measurement, or a named
    failing example), not merely restate its reason
  - every gate must declare the near-miss it still ALLOWS
  - every gate's `allows` case must actually be allowed, verified by running
    the converter, so a gate cannot quietly widen into a blanket refusal
  - the registry must not drift from the code: every gate id the generator
    raises must be registered, and every registered gate must be raised
    somewhere (or be a disclosure, listed below)

Run: pytest tests/test_gates.py
"""
import os
import re
import unittest

from vs2convert.core.gates import GATES, allows_missing, evidence_missing
from vs2convert.core.generator import convert, ConversionError
from vs2convert.core.detector import check_suitability

PACKAGE_DIR = os.path.dirname(os.path.abspath(__import__(
    'vs2convert').__file__))

# Gates that are disclosures rather than refusals: they never raise, so no
# `gate=` site exists. Their positive control proves the disclosure fires.
DISCLOSURES = {'shadertoy-channel-uncertainty'}

# --- positive controls: each gate's declared `allows` case, run for real ----

MINIMAL_BODY = ('void main() {\n'
                '    vec2 uv = gl_FragCoord.xy / resolution.xy;\n'
                '    gl_FragColor = vec4(uv, 0.5, 1.0);\n'
                '}\n')


def _converts(source, fmt='PlainGLSL'):
    convert(source, fmt, 'probe')
    return True


ALLOWED_CASES = {
    # comment mentioning frac()/lerp() must not trip the HLSL gate
    'hlsl-milkdrop': lambda: _converts(
        '// original: lerp(a, b, c) and frac(x)\n' + MINIMAL_BODY),
    # a PASSES array with no TARGET is not feedback
    'isf-feedback-buffer': lambda: _converts(
        '/*{"INPUTS": [{"NAME": "Zoom", "TYPE": "float", "DEFAULT": 0.5,'
        ' "MIN": 0.0, "MAX": 1.0}], "PASSES": [{"NAME": "p"}]}*/\n'
        'void main() {\n'
        '    vec2 uv = gl_FragCoord.xy / RENDERSIZE.xy;\n'
        '    gl_FragColor = vec4(uv * Zoom, 1.0);\n'
        '}\n', 'ISF'),
    # An ISF image input with no IMPORTED block is host-supplied: converts.
    # This is the canonical docs.isf.video/ref_json example.
    'isf-imported-resources': lambda: not _raises_on(
        '/*{"INPUTS": [{"NAME": "inputImage", "TYPE": "image"}]}*/\n'
        'void main() {\n'
        '    gl_FragColor = IMG_NORM_PIXEL(inputImage, isf_FragNormCoord);\n'
        '}\n', 'ISF'),
    'glsl-texture2d': lambda: not _raises_on(
        '/*{"INPUTS": [{"NAME": "img", "TYPE": "image"}]}*/\n'
        'void main() {\n'
        '    gl_FragColor = IMG_NORM_PIXEL(img, isf_FragNormCoord);\n}\n',
        'ISF'),
    # a single iChannel0 read is an ordinary image-input shader
    'shadertoy-multi-channel': lambda: _converts(
        'void mainImage(out vec4 f, in vec2 c) {\n'
        '    vec2 uv = c / iResolution.xy;\n'
        '    f = vec4(texture(iChannel0, uv).rgb, 1.0);\n}\n', 'Shadertoy'),
    # PASSINDEX alone is projected onto the final pass, not refused
    'body-feedback-refs': lambda: _converts(
        '/*{"INPUTS": [{"NAME": "Zoom", "TYPE": "float", "DEFAULT": 0.5,'
        ' "MIN": 0.0, "MAX": 1.0}], "PASSES": [{"NAME": "p1"},'
        ' {"NAME": "p2"}]}*/\n'
        'void main() {\n'
        '    int pass = int(PASSINDEX);\n'
        '    vec2 uv = gl_FragCoord.xy / RENDERSIZE.xy;\n'
        '    gl_FragColor = vec4(uv * float(pass) * Zoom, 1.0);\n'
        '}\n', 'ISF'),
    # A shader that samples imageTex directly is never flagged UNVERIFIED.
    'shadertoy-channel-uncertainty': lambda: not any(
        w.startswith('UNVERIFIED')
        for w in convert('void main() {\n'
                         '    fragColor = texture(imageTex, texCoord);\n'
                         '}\n', 'PlainGLSL', 'p')['warnings']),
    # a channel with no metadata is still allowed to convert (unverified)
    'shadertoy-unrepresentable-channel': lambda: not _raises_on(
        'void mainImage(out vec4 f, in vec2 c){'
        ' f = vec4(texture(iChannel0, c / 9.0).rgb, 1.0); }', 'Shadertoy'),
    # a single ISF image input is still allowed to convert
    'isf-multiple-image-inputs': lambda: not _raises_on(
        '/*{"INPUTS": [{"NAME": "src0", "TYPE": "image"}]}*/\n'
        'uniform sampler2D src0;\nvoid main() {\n'
        '    fragColor = texture(src0, gl_FragCoord.xy / 100.0);\n}\n', 'ISF'),
    # a custom uniform is not an engine var
    'engine-var-redeclaration': lambda: check_suitability(
        'uniform float myParam;\n' + MINIMAL_BODY)['suitable'],
    # exactly seven parameters is inside the budget
    'too-many-parameters': lambda: check_suitability(
        ''.join('uniform float p%d;\n' % i for i in range(7))
        + MINIMAL_BODY)['suitable'],
    # a source with a real entry point converts
    'missing-entry-point': lambda: _converts(MINIMAL_BODY),
    # a well-formed ISF manifest parses
    'isf-parse-failure': lambda: _converts(
        '/*{"INPUTS": [{"NAME": "z", "TYPE": "float", "DEFAULT": 0.5}]}*/\n'
        'void main() {\n'
        '    vec2 uv = gl_FragCoord.xy / RENDERSIZE.xy;\n'
        '    gl_FragColor = vec4(uv * z, 1.0);\n}\n', 'ISF'),
    # every format detection can name has a body converter
    'unsupported-format': lambda: _all_formats_convert(),
    # a malformed manifest is refused as data, not raised
    'internal-error': lambda: _never_raises('/*{"INPUTS": "nope"}*/\n'),
}


def _all_formats_convert():
    """PlainGLSL, Shadertoy, ISF and GLSLSandbox each reach a converter.

    MilkDrop is deliberately absent: detect_format may name it, but the HLSL
    gate refuses it first, so it never reaches the dispatch table.
    """
    from vs2convert.core.generator import convert as gen_convert
    sample = MINIMAL_BODY
    for fmt in ('PlainGLSL', 'Shadertoy', 'GLSLSandbox'):
        gen_convert(sample, fmt, 'probe')
    gen_convert(
        '/*{"INPUTS": [{"NAME": "z", "TYPE": "float", "DEFAULT": 0.5}]}*/\n'
        'void main() {\n'
        '    vec2 uv = gl_FragCoord.xy / RENDERSIZE.xy;\n'
        '    gl_FragColor = vec4(uv * z, 1.0);\n}\n', 'ISF', 'probe')
    return True


def _never_raises(source):
    from vs2convert.api import convert as api_convert
    try:
        result = api_convert(source)
    except Exception:
        return False
    return result.rejections and all(r.gate for r in result.rejections)


def _structural_validation_allows():
    """A clean conversion passes: the validator is a real gate, not a blanket."""
    from vs2convert.api import convert as api_convert
    return api_convert(MINIMAL_BODY).ok


ALLOWED_CASES['structural-validation'] = _structural_validation_allows


def _raises_on(source, fmt):
    try:
        convert(source, fmt, 'probe')
        return False
    except ConversionError:
        return True


class EvidenceTests(unittest.TestCase):
    def test_every_gate_has_real_evidence(self):
        bad = evidence_missing()
        self.assertEqual(
            bad, [],
            'gates whose evidence is missing or merely restates the reason: %s'
            % bad)

    def test_every_gate_declares_what_it_allows(self):
        bad = allows_missing()
        self.assertEqual(
            bad, [],
            'gates with no declared allows case (a blanket refusal in '
            'disguise): %s' % bad)

    def test_gate_ids_are_unique(self):
        ids = [g.id for g in GATES]
        self.assertEqual(len(ids), len(set(ids)), 'duplicate gate id')

    def test_every_gate_names_a_real_module(self):
        for g in GATES:
            path = os.path.join(PACKAGE_DIR, g.module)
            self.assertTrue(os.path.exists(path),
                            'gate %s names missing module %s' % (g.id, g.module))


class AllowsPositiveControlTests(unittest.TestCase):
    """The load-bearing test: each gate must still permit its near-miss.

    A gate that only says what it rejects is how image-input shaders came to
    be refused wholesale when only a narrow case was unrepresentable.
    """

    def test_every_gate_has_a_positive_control(self):
        for g in GATES:
            self.assertIn(
                g.id, ALLOWED_CASES,
                'gate %r has no positive control proving its allows case. '
                'Add one, or delete the gate: a refusal that cannot be shown '
                'to preserve its near-miss is a blanket refusal.' % g.id)

    def test_allowed_cases_really_are_allowed(self):
        for gate_id, case in ALLOWED_CASES.items():
            with self.subTest(gate=gate_id):
                try:
                    self.assertTrue(
                        case(),
                        'gate %r blocked the near-miss it declares it allows'
                        % gate_id)
                except ConversionError as e:
                    self.fail('gate %r blocked its declared allows case: %s'
                              % (gate_id, e))


class RegistryMatchesCodeTests(unittest.TestCase):
    """The registry must not drift from the code it describes."""

    def _source(self, name):
        with open(os.path.join(PACKAGE_DIR, name), encoding='utf-8') as fh:
            return fh.read()

    def test_every_conversion_error_names_a_registered_gate(self):
        """A raise site without a gate id is a decline the API cannot label."""
        src = self._source('core/generator.py')
        blocks = re.findall(r'raise ConversionError\((.*?)\)\n', src,
                            re.DOTALL)
        self.assertTrue(blocks, 'no raise sites found - did the pattern rot?')
        ids = {g.id for g in GATES}
        missing = []
        for block in blocks:
            match = re.search(r"gate='([\w-]+)'", block)
            if not match:
                missing.append(' '.join(block.split())[:90])
            elif match.group(1) not in ids:
                missing.append('%s (unregistered)' % match.group(1))
        self.assertEqual(
            missing, [],
            'ConversionError raised without a registered gate id: %s' % missing)

    def test_every_registered_gate_is_used(self):
        """The reverse: a registered gate that no code raises is stale."""
        haystack = ''.join(
            self._source(name) for name in (
                'core/generator.py', 'core/detector.py', 'api.py',
                'validators/validator.py'))
        unused = [g.id for g in GATES
                  if g.id not in DISCLOSURES
                  and '"%s"' % g.id not in haystack
                  and "'%s'" % g.id not in haystack]
        self.assertEqual(unused, [],
                         'gate ids registered but absent from the code: %s'
                         % unused)


if __name__ == '__main__':
    unittest.main(verbosity=2)
