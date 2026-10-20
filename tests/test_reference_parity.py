"""The three curated ISF <-> expert-conversion pairs kept from the upstream
oracle, plus the inline aspect-convention test.

`converted_shaders/` held 17 pairs; the private suite compared all of them.
Only three travel with this repo: the rest are private-corpus material with
no place in a public package. The three here are the smallest sources that
still exercise the convention the expert set established:

  * every expert conversion corrects aspect (`resolution.y / resolution.x`),
    including shaders whose ISF source does not;
  * ISF float inputs become 0..1 knobs that round-trip the source DEFAULT.

Provenance: the `.fs` sources are from ISF-shaders-main (CC0 per its LICENSE
file; its README asks for non-commercial reuse - recorded, not resolved), the
`.frag` files are this project's converted output. Fixtures only.
"""
import json
import os
import re
import unittest

from vs2convert.api import convert

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'fixtures', 'reference')

PAIRS = ['julia', 'waves', 'polyhedral']

ASPECT = 'resolution.y / resolution.x'

# A canvas-dimension ratio in EITHER order, or the vec2 form, is a real
# aspect correction. Grepping for one spelling gave false mismatches:
# rotating_circles corrects aspect as `resolution.x / resolution.y`, which the
# narrow check read as "no correction" and reported as a converter
# disagreement when the converter was faithfully preserving the source. The
# question is "is the aspect corrected", not "is this exact string present".
_ASPECT_RE = re.compile(
    r'resolution\.(?:x|y|xy)\s*/\s*resolution\.(?:x|y|xy)')


def _has_aspect(text):
    return bool(_ASPECT_RE.search(text))


def _read(name):
    path = os.path.join(FIXTURES, name)
    if not os.path.exists(path):
        raise unittest.SkipTest('missing fixture: %s' % name)
    with open(path, encoding='utf-8-sig', errors='replace') as fh:
        return fh.read()


def _convert(stem):
    result = convert(_read(stem + '.fs'), name='ref')
    if not result.ok:
        raise AssertionError('%s declined: %r'
                             % (stem, result.rejections))
    return result.frag


class ExpertReferenceParityTests(unittest.TestCase):
    """Our output should agree with the expert conversions on aspect."""

    def test_reference_pairs_all_exist(self):
        """Guards the comparison itself: a missing fixture would make the
        rest of this class vacuous."""
        missing = []
        for stem in PAIRS:
            for ext in ('.fs', '.frag'):
                path = os.path.join(FIXTURES, stem + ext)
                if not os.path.exists(path):
                    missing.append(path)
        self.assertEqual([], missing, 'reference fixtures missing: %s'
                         % missing[:3])

    def test_every_expert_reference_corrects_aspect(self):
        """Sanity-check the premise. If the expert set stopped correcting
        aspect, this fails loudly instead of the parity test below silently
        demanding a change nobody wants."""
        without = [s for s in PAIRS
                   if not _has_aspect(_read(s + '.frag'))]
        self.assertEqual(
            [], without,
            'expert conversions that no longer correct aspect: %s' % without)

    def test_our_output_corrects_aspect_like_the_expert_set(self):
        for stem in PAIRS:
            with self.subTest(stem=stem):
                expert = _read(stem + '.frag')
                ours = _convert(stem)
                self.assertEqual(_has_aspect(expert), _has_aspect(ours),
                                 '%s: expert corrects aspect=%s, ours=%s'
                                 % (stem, _has_aspect(expert),
                                    _has_aspect(ours)))

    def test_isf_uv_normalised_by_rendersize_y_gets_the_aspect_factor(self):
        """The specific source shape behind most of the 17.

            (gl_FragCoord.xy * K - C * RENDERSIZE.xy) / RENDERSIZE.y

        must come out as (texCoord * K - C) * vec2(1.0, div) with div
        declared. This is what the expert set does for bloom, julia,
        polyhedral, phreax_portal and others.
        """
        cases = [
            ('(gl_FragCoord.xy * 2.0 - RENDERSIZE.xy) / RENDERSIZE.y',
             '(texCoord * 2.0 - 1.0)'),
            ('(gl_FragCoord.xy - RENDERSIZE.xy * 0.5) / RENDERSIZE.y',
             '(texCoord - 0.5)'),
            ('(gl_FragCoord.xy - 0.5 * RENDERSIZE.xy) / RENDERSIZE.y',
             '(texCoord - 0.5)'),
        ]
        for raw, expect_core in cases:
            with self.subTest(raw=raw):
                src = ('/*{\n    "INPUTS": [\n        {"NAME": "K",'
                       ' "TYPE": "float", "DEFAULT": 0.5, "MIN": 0.0,'
                       ' "MAX": 1.0}\n    ]\n}*/\nvoid main() {\n'
                       '    vec2 uv = %s;\n'
                       '    float v = uv.x;\n'
                       '    gl_FragColor = vec4(v, v, v, 1.0);\n}\n' % raw)
                result = convert(src, name='p')
                self.assertTrue(result.ok, result.rejections)
                frag = result.frag
                self.assertTrue(
                    _has_aspect(frag),
                    'no aspect factor emitted for %r\n%s' % (raw, frag))
                self.assertIn(expect_core, frag,
                              'expected the %r core in the output' % expect_core)
                self.assertIn('vec2(1.0, div)', frag,
                              'the expert form scales Y: vec2(1.0, div)')

    def test_expert_reference_parameters_are_valid_knob_ranges(self):
        """Hard rule 1 & formal spec: all parameters in the expert .frag
        files must have valid in-range knob defaults (0.0 <= default <= 1.0).
        """
        out_of_range = []
        for stem in PAIRS:
            expert = _read(stem + '.frag')
            m = re.search(r'/\*(.*?)\*/', expert, re.DOTALL)
            if not m:
                continue
            manifest = json.loads(m.group(1))
            for p in manifest.get('parameters', []):
                d = p.get('default')
                if d is None or not (0.0 <= float(d) <= 1.0):
                    out_of_range.append((stem, p.get('name'), d))
        self.assertEqual(
            [], out_of_range,
            'expert reference manifests with out-of-range knob defaults: %s'
            % out_of_range)

    def test_our_converted_knobs_round_trip_source_defaults(self):
        """Magnitude fidelity across the curated corpus.

        When a source ISF float input declares DEFAULT over [MIN, MAX],
        evaluating the converted shader formula:
            restored = lo + knob * (hi - lo)
        with the manifest knob default MUST round-trip to the source DEFAULT.
        """
        roundtrip_pat = re.compile(
            r'\(\s*([-\d.eE+]+)\s*\+\s*(\w+)\s*\*\s*\(([-\d.eE+]+)\s*-\s*([-\d.eE+]+)\)\s*\)')
        checked = 0
        drifts = []
        for stem in PAIRS:
            isf_src = _read(stem + '.fs')
            header = ''
            if '/*{' in isf_src:
                header = isf_src[isf_src.find('/*{')+2 : isf_src.find('}*/')+1]
            elif '/*' in isf_src:
                header = isf_src[isf_src.find('/*')+2 : isf_src.find('*/')]
            try:
                inputs = json.loads(header).get('INPUTS', [])
            except Exception:
                inputs = []

            src_inputs = {inp['NAME']: inp for inp in inputs
                          if 'NAME' in inp and inp.get('TYPE') == 'float'}
            ours = _convert(stem)

            m = re.search(r'/\*(.*?)\*/', ours, re.DOTALL)
            if not m:
                continue
            manifest = json.loads(m.group(1))
            knobs = {p['name']: p['default']
                     for p in manifest.get('parameters', [])}

            for lo_s, name, hi_s, _ in roundtrip_pat.findall(ours):
                if name in knobs:
                    lo, hi = float(lo_s), float(hi_s)
                    knob = knobs[name]
                    restored = lo + knob * (hi - lo)
                    for in_name, inp in src_inputs.items():
                        if inp.get('DEFAULT') is not None and (
                                name == in_name.lower()
                                or name.startswith(in_name.lower())
                                or in_name.lower().startswith(name)):
                            src_def = float(inp['DEFAULT'])
                            diff = abs(restored - src_def)
                            if diff > 0.001:
                                drifts.append((stem, name, restored,
                                               src_def, diff))
                            checked += 1
                            break

        self.assertGreater(checked, 0, 'no roundtrips checked')
        self.assertEqual([], drifts,
                         'source default magnitude drifts: %s' % drifts)


if __name__ == '__main__':
    unittest.main()
