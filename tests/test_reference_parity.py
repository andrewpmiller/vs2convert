"""Compare our conversions against the expert conversions in
converted_shaders/ - the reference outcomes that already exist in the repo.

Eighteen converted_shaders/*.frag have a matching ISF source. Those are the
only real oracle available for "does our output look like the output a
careful human produced", and until now nothing in the suite consulted them.

Two independent reasons this exists, both learned the hard way:

  1. The expert set injects the aspect pattern into every one of the eighteen,
     including shaders whose ISF source has no aspect correction at all. The
     expert comment says why: "engine convention (div = y/x); matches source
     framing on square canvases". That is a deliberate departure from source
     fidelity, chosen because it is correct in the engine. An earlier
     "stay faithful to the source" decision contradicted it 17 times out of
     18, and the contradiction was visible in this directory the whole time.

  2. Where the expert set is internally inconsistent - one uses the
     transposed axis, three differ structurally - this test does NOT copy a
     style. It compares outcomes and reports disagreement, so a genuine
     difference is visible rather than averaged away.

Reference: the author verified in VS-2 on 2026-09-25/26 that texCoord is not
pre-corrected by the engine (an uncorrected circle renders wider than tall)
and that the golden pattern scales Y (vec2(1.0, div), which renders round).
"""
import json
import os
import re
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
VS2_DIR = os.path.dirname(TESTS_DIR)
REPO_ROOT = os.path.dirname(VS2_DIR)
sys.path.insert(0, VS2_DIR)

from core.generator import convert  # noqa: E402

ASPECT = 'resolution.y / resolution.x'

# A canvas-dimension ratio in EITHER order, or the vec2 form, is a real
# aspect correction. Grepping for one spelling gave false mismatches:
# rotating_circles corrects aspect as `resolution.x / resolution.y`, which the
# narrow check read as "no correction" and reported as a converter
# disagreement when the converter was faithfully preserving the source. The
# question is "is the aspect corrected", not "is this exact string present".
_ASPECT_RE = re.compile(
    r'resolution\.(?:x|y|xy)\s*/\s*resolution\.(?:x|y|xy)')

# (expert .frag stem, path of the ISF source under ISF-shaders-main)
REFERENCE_PAIRS = [
    ('bloom', 'bloom/bloom.fs'),
    ('bulb_volumetrics', 'bulb volumetrics/bulb volumetrics.fs'),
    ('color_fractal_nayk', 'color fractal nayk/color fractal nayk.fs'),
    ('drostled', 'drostled/drostled.fs'),
    ('fork_of_4th_eye', 'Fork of 4th eye/Fork of 4th eye.fs'),
    ('gamma_xor', 'gamma xor/gamma xor.fs'),
    ('iq_line_fractal_tunnel',
     'iq - line fractal tunnel/iq - line fractal tunnel.fs'),
    ('julia', 'julia/julia.fs'),
    ('kabbalistic_fractal_circles',
     'kabbalistic fractal circles/kabbalistic fractal circles.fs'),
    ('kali_inversion_machine',
     'kali inversion machine/kali inversion machine.fs'),
    ('nayk_spherical_remake', 'nayk spherical remake/nayk spherical remake.fs'),
    ('neon_psy', 'Neon psy/Neon psy.fs'),
    ('phreax_portal', 'phreax portal/phreax portal.fs'),
    ('polyhedral', 'polyhedral/polyhedral.fs'),
    ('rotating_circles', 'Rotating circles/Rotating circles.fs'),
    ('spiral_riders_kali', 'spiral riders kali/spiral riders kali.fs'),
    ('waves', 'waves (1)/waves.fs'),
    ('xor_ascend', 'xor ascend/xor ascend.fs'),
]

# Known, documented divergence - NOT a rule this test fails to encode.
# xor_ascend builds a 3D RAY, not a uv:
#     source  normalize(vec3(2.1 * gl_FragCoord.xy, 0.0) - RENDERSIZE.xyy)
#     expert  normalize(vec3(2.1 * fragPx, 0.0)
#                      - vec3(resolution.y / div, resolution.y, resolution.y))
# The expert rewrote the ray construction by hand ("frag == texCoord *
# resolution in the engine"). No mechanical aspect rule reproduces that, and
# inventing one would be a guess. Converted by hand instead.
KNOWN_HAND_CONVERTED = {'xor_ascend'}


def _has_aspect(text):
    return bool(_ASPECT_RE.search(text))


def _load(relative):
    path = os.path.join(REPO_ROOT, relative)
    if not os.path.exists(path):
        raise unittest.SkipTest('missing fixture: %s' % relative)
    with open(path, encoding='utf-8-sig', errors='replace') as fh:
        return fh.read()


def _convert(relative):
    src = _load(os.path.join('ISF-shaders-main', relative))
    return convert(src, 'ISF', 'ref')['frag']


class ExpertReferenceParityTests(unittest.TestCase):
    """Our output should agree with the expert conversions on aspect."""

    def test_reference_pairs_all_exist(self):
        """Guards the comparison itself: a missing fixture would make the
        rest of this class vacuous, which is exactly how a test starts
        passing while checking nothing."""
        missing = []
        for stem, rel in REFERENCE_PAIRS:
            for path in (os.path.join(REPO_ROOT, 'converted_shaders',
                                      stem + '.frag'),
                         os.path.join(REPO_ROOT, 'ISF-shaders-main', rel)):
                if not os.path.exists(path):
                    missing.append(path)
        self.assertEqual([], missing,
                         'reference fixtures missing: %s' % missing[:3])
        self.assertGreaterEqual(len(REFERENCE_PAIRS), 15)

    def test_every_expert_reference_corrects_aspect(self):
        """Sanity-check the premise. If the expert set stopped correcting
        aspect, this fails loudly instead of the parity test below silently
        demanding a change nobody wants."""
        without = [s for s, _ in REFERENCE_PAIRS
                   if not _has_aspect(_load(
                       'converted_shaders/%s.frag' % s))]
        self.assertEqual(
            [], without,
            'expert conversions that no longer correct aspect: %s' % without)

    def test_our_output_corrects_aspect_like_the_expert_set(self):
        disagree = []
        for stem, rel in REFERENCE_PAIRS:
            if stem in KNOWN_HAND_CONVERTED:
                continue
            expert = _load('converted_shaders/%s.frag' % stem)
            try:
                ours = _convert(rel)
            except Exception as exc:                 # declined
                disagree.append('%s: declined (%s)' % (stem, exc))
                continue
            if _has_aspect(expert) != _has_aspect(ours):
                disagree.append(
                    '%s: expert corrects aspect=%s, ours=%s'
                    % (stem, _has_aspect(expert), _has_aspect(ours)))
        self.assertEqual(
            [], disagree,
            'aspect treatment disagrees with the expert reference set in '
            '%d of %d cases:\n  %s'
            % (len(disagree), len(REFERENCE_PAIRS), '\n  '.join(disagree)))

    def test_isf_uv_normalised_by_rendersize_y_gets_the_aspect_factor(self):
        """The specific source shape behind most of the 17.

            (gl_FragCoord.xy * K - C * RENDERSIZE.xy) / RENDERSIZE.y

        must come out as (texCoord * K - C) * vec2(1.0, div) with div
        declared. This is what the expert set does for bloom, julia,
        polyhedral, phreax_portal, gamma_xor and others.
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
                frag = convert(src, 'ISF', 'p')['frag']
                self.assertTrue(
                    _has_aspect(frag),
                    'no aspect factor emitted for %r\n%s' % (raw, frag))
                self.assertIn(expect_core, frag,
                              'expected the %r core in the output' % expect_core)
                self.assertIn('vec2(1.0, div)', frag,
                              'the expert form scales Y: vec2(1.0, div)')


    def test_expert_reference_parameters_are_valid_knob_ranges(self):
        """Hard rule 1 & formal spec: All parameters in converted_shaders/*.frag
        must have valid in-range knob defaults (0.0 <= default <= 1.0).
        """
        out_of_range = []
        for stem, _ in REFERENCE_PAIRS:
            expert = _load('converted_shaders/%s.frag' % stem)
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
        """Magnitude-fidelity across the reference corpus.

        When a source ISF float input declares DEFAULT over [MIN, MAX],
        evaluating the converted shader formula:
            restored = lo + knob * (hi - lo)
        with the manifest knob default MUST round-trip to the source DEFAULT.
        """
        roundtrip_pat = re.compile(
            r'\(\s*([-\d.eE+]+)\s*\+\s*(\w+)\s*\*\s*\(([-\d.eE+]+)\s*-\s*([-\d.eE+]+)\)\s*\)')
        checked = 0
        drifts = []
        for stem, rel in REFERENCE_PAIRS:
            isf_src = _load(os.path.join('ISF-shaders-main', rel))
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
            try:
                ours = _convert(rel)
            except Exception:
                continue

            m = re.search(r'/\*(.*?)\*/', ours, re.DOTALL)
            if not m:
                continue
            manifest = json.loads(m.group(1))
            knobs = {p['name']: p['default'] for p in manifest.get('parameters', [])}

            for lo_s, name, hi_s, lo2_s in roundtrip_pat.findall(ours):
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
                                drifts.append((stem, name, restored, src_def, diff))
                            checked += 1
                            break

        self.assertGreater(checked, 20, 'no roundtrips checked across reference corpus')
        self.assertEqual([], drifts, 'source default magnitude drifts: %s' % drifts)


if __name__ == '__main__':
    unittest.main()
