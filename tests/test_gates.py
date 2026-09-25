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
  - the registry must not drift from the code: every ConversionError raised
    by the generator must be accounted for by some gate

Run: python3 vs2_convert/tests/test_gates.py
"""
import os
import re
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
VS2_DIR = os.path.dirname(TESTS_DIR)
sys.path.insert(0, VS2_DIR)

from core.gates import GATES, allows_missing, evidence_missing, gates_for
from core.generator import convert, ConversionError
from core.detector import check_suitability

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
    # a custom uniform is not an engine var
    'engine-var-redeclaration': lambda: check_suitability(
        'uniform float myParam;\n' + MINIMAL_BODY)['suitable'],
}


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
            path = os.path.join(VS2_DIR, g.module)
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

    def test_every_conversion_error_is_accounted_for(self):
        """Every refusal the generator can make must be traceable to a gate.

        Keeps the registry honest in both directions: a new hard gate cannot
        be added without documenting it, and a documented gate cannot be
        quietly deleted.
        """
        src = open(os.path.join(VS2_DIR, 'core', 'generator.py')).read()
        # Messages that are pure input-format complaints rather than
        # capability limits; these need no gate entry.
        FORMAT_COMPLAINTS = (
            'no mainImage() or main() entry point found',
            'no main() entry point found',
            'ISF INPUTS block not found or not parseable',
            'unsupported format:',
        )
        blocks = re.findall(r'raise ConversionError\(\s*(.*?)\)\n', src,
                            re.DOTALL)
        undocumented = []
        for block in blocks:
            text = ' '.join(block.split())
            if any(c in text for c in FORMAT_COMPLAINTS):
                continue
            if not text:
                continue
            # Match against the registered reason strings.
            if not any(_matches(text, g.reason) for g in GATES):
                undocumented.append(text[:90])
        self.assertEqual(
            undocumented, [],
            'ConversionError raised with no entry in core/gates.py. Either it '
            'is a capability limit and needs a gate (with evidence and an '
            'allows case), or it is a format complaint: %s' % undocumented)

    def test_every_gate_reason_appears_in_the_code(self):
        """And the reverse: a registered gate that no longer exists in code
        is stale documentation."""
        src = open(os.path.join(VS2_DIR, 'core', 'generator.py')).read()
        det = open(os.path.join(VS2_DIR, 'core', 'detector.py')).read()
        haystack = src + det
        for g in GATES:
            if not _matches(haystack, g.reason):
                self.fail('gate %r is registered but its reason text is not '
                          'in %s - update or delete it' % (g.id, g.module))


def _norm(text):
    """Collapse whitespace and drop %-format placeholders.

    Source strings are wrapped across lines and interpolated, so compare on
    normalized text with a distinctive literal fragment.
    """
    text = re.sub(r'%\([^)]*\)[sd]|%[sd]', ' ', text)
    text = text.replace('"', ' ').replace("'", ' ')
    return ' '.join(text.split())


def _matches(haystack, reason):
    """True if any substantial literal run of `reason` appears in `haystack`."""
    hay = _norm(haystack)
    # Split the reason on placeholders and try each literal part; the longest
    # is the most distinctive.
    parts = [p for p in re.split(r'%\([^)]*\)[sd]|%[sd]', reason) if p.strip()]
    if not parts:
        return _norm(reason) in hay
    for part in parts:
        frag = _norm(part)
        if len(frag) >= 25 and frag in hay:
            return True
    # Fall back to the longest fragment, shortened to survive wrapping.
    longest = max(parts, key=lambda p: len(_norm(p)))
    frag = _norm(longest)
    return len(frag) >= 12 and frag[:60] in hay


if __name__ == '__main__':
    unittest.main(verbosity=2)
