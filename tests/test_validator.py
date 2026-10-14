"""Deterministic tests for vs2_convert/validators/validator.py.

These exist because two agent-flagged "Critical" findings were adjudicated as
false positives by hand. Hand adjudication is not a safeguard - it is exactly
the kind of claim that failed review. Each adjudication is pinned here as an
executable assertion instead, so a change that reintroduces the reviewed-for
regression fails the suite rather than passing on my say-so.

Run: python3 vs2_convert/tests/test_validator.py
"""
import glob
import os
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
VS2_DIR = os.path.dirname(TESTS_DIR)
REPO_ROOT = os.path.dirname(VS2_DIR)
sys.path.insert(0, VS2_DIR)

from core.detector import check_suitability
from validators.validator import (
    run_validation,
    _has_uniform_declarations,
    _has_engine_var_redecl,
    _has_custom_speed_params,
)

# Ground truth corpus: every shader VS-2 has actually shipped.
CORPUS = sorted(glob.glob(os.path.join(REPO_ROOT, 'converted_shaders', '*.frag'))
                + glob.glob(os.path.join(REPO_ROOT, 'factory-examples', '*.frag')))

MINIMAL_VALID = '\n'.join([
    '/*{"parameters": [{"name": "level", "default": 0.5}]}*/',
    '#ifdef GL_ES',
    'precision highp float;',
    '#endif',
    'void main() {',
    '    vec2 uv = gl_FragCoord.xy / resolution.xy;',
    '    float div = resolution.y / resolution.x;',
    '    vec3 c = vec3(level) * color.rgb;',
    '    float coloredPixels = dot(clamp(c, 0.0, 1.0), vec3(1.0));',
    '    fragColor = vec4(c, alpha * coloredPixels);',
    '}',
])


def _read(path):
    with open(path) as fh:
        return fh.read()


class UniformRuleAdjudicationTests(unittest.TestCase):
    """ADJUDICATION: any uniform/varying declaration is an ERROR.

    A review claimed this rule was a bug that "rejects valid code", on the
    grounds that PRD hard rule 5 only forbids redeclaring the 7 engine vars.
    That claim was false. VS-2 injects its own uniforms and varyings; ANY
    declaration in the body shadows engine state. The authoritative external
    validator (validate_shader.py:125) makes any uniform/varying an error.
    Ground truth: 0 uniform/varying declarations across every shipped shader
    (asserted in CorpusParityTests below).

    These tests exist so that "relax the rule to allow custom uniforms" cannot
    land silently. It would be a real regression, not a fix.
    """

    def test_custom_uniform_is_an_error_not_a_pass(self):
        """A CUSTOM uniform must still be rejected - the engine owns the
        uniform namespace. This is the exact behaviour the review wanted
        reversed; reversing it is the regression these tests guard."""
        self.assertTrue(_has_uniform_declarations('uniform float myParam;'))

    def test_indented_uniform_is_caught(self):
        # Pre-fix regex was anchored at column 0 and missed indented decls.
        self.assertTrue(_has_uniform_declarations('    uniform float x;'))

    def test_varying_is_caught(self):
        self.assertTrue(_has_uniform_declarations('varying vec2 vUv;'))

    def test_commented_uniform_is_not_a_declaration(self):
        self.assertFalse(_has_uniform_declarations('// uniform float x;'))
        self.assertFalse(_has_uniform_declarations('    // uniform float x;'))

    def test_clean_shader_has_no_uniform_declarations(self):
        self.assertFalse(_has_uniform_declarations(MINIMAL_VALID))

    def test_external_validator_bans_these_too(self):
        """Parity with the authoritative gate. If the external validator ever
        relaxes this, this test fails and the question is reopened
        deliberately rather than by a confident review."""
        validator = os.path.join(REPO_ROOT, '.cline', 'skills',
                                 'vs2-shader-expert', 'scripts',
                                 'validate_shader.py')
        src = _read(validator)
        self.assertIn('uniform', src)
        self.assertIn('varying', src)
        self.assertIn('engine injects', src)


class EngineVarRedeclTests(unittest.TestCase):
    def test_engine_var_redecl_caught(self):
        self.assertTrue(_has_engine_var_redecl('vec2 resolution;'))
        self.assertTrue(_has_engine_var_redecl('float time;'))

    def test_indented_engine_var_is_not_global_scope(self):
        self.assertFalse(_has_engine_var_redecl('    vec2 resolution;'))


class SpeedKeywordParityTests(unittest.TestCase):
    """ADJUDICATION: the validator's keyword list must cover the generator's.

    A review found validator.py:104 missing 'twist_speed'. The validator is
    defense-in-depth: it exists to catch what the conversion path missed, so
    a NARROWER list defeats the purpose. The live counterpart list is the
    generator's SPEED_KEYWORDS (core/generator.py); the dead mapper module
    it used to compare against was deleted in the audit-8 cleanup. Pinned so
    the two lists cannot silently drift apart again.
    """

    def test_validator_catches_each_speed_keyword(self):
        for kw in ['speed', 'rate', 'frequency', 'freq', 'twist_speed']:
            probe = '/*{"parameters": [{"name": "x_%s", "default": 0.5}]}*/' % kw
            self.assertTrue(
                _has_custom_speed_params(probe),
                "validator misses speed keyword '%s'" % kw)

    def test_validator_keyword_list_is_a_superset_of_generator_list(self):
        """Structural pin, because the behavioural test above cannot catch
        keyword-list drift here.

        Deleting 'twist_speed' from the validator is a NO-OP: the substring
        'speed' already matches any name containing 'twist_speed'. That makes
        it an equivalent mutant - the behaviour is identical, so no
        behaviour-based test can fail. Pinned structurally instead so the
        lists cannot silently diverge.

        (Recorded because a review reported the missing 'twist_speed' as a
        High-severity defense-in-depth gap. It is defensive bookkeeping, not
        a behavioural defect.)
        """
        import re as _re
        from core.generator import SPEED_KEYWORDS
        val_src = _read(os.path.join(VS2_DIR, 'validators', 'validator.py'))

        def _list_from(src, varname):
            m = _re.search(varname + r"\s*=\s*\[(.*?)\]", src, _re.DOTALL)
            self.assertIsNotNone(m, "could not find %s" % varname)
            return set(_re.findall(r"'([^']+)'", m.group(1)))

        val_kws = _list_from(val_src, 'speed_patterns')
        gen_kws = set(SPEED_KEYWORDS)
        self.assertTrue(
            gen_kws.issubset(val_kws),
            "validator keywords %s do not cover generator keywords %s"
            % (sorted(val_kws), sorted(gen_kws)))

    def test_speed_check_reads_params_not_raw_text(self):
        """The pre-fix version regexed the whole file, so the word 'speed' in
        a comment or variable name produced a false positive."""
        noisy = ('// this shader moves at high speed\n'
                 '/*{"parameters": [{"name": "level", "default": 0.5}]}*/\n')
        self.assertFalse(_has_custom_speed_params(noisy))


class CorpusParityTests(unittest.TestCase):
    """Ground truth: the shipped corpus must produce zero uniform/varying
    errors. This is the executable version of the 'no shipped shader declares
    uniforms' evidence - it re-derives the fact on every run rather than
    trusting a one-off grep quoted in a review."""

    def test_corpus_is_non_trivial(self):
        self.assertGreaterEqual(len(CORPUS), 50,
                                'corpus unexpectedly small - parity checks '
                                'would be vacuous')

    def test_corpus_has_zero_uniform_declarations(self):
        offenders = [f for f in CORPUS
                     if _has_uniform_declarations(_read(f))]
        self.assertEqual(offenders, [],
                         'shipped shaders must not declare uniform/varying')

    def test_corpus_produces_no_uniform_errors(self):
        bad = []
        for f in CORPUS:
            result = run_validation(_read(f), f)
            for e in result['errors']:
                if 'uniform' in e.lower() or 'varying' in e.lower():
                    bad.append((f, e))
        self.assertEqual(bad, [], 'uniform/varying false positives on corpus')


class SanityTests(unittest.TestCase):
    def test_minimal_valid_shader_passes(self):
        result = run_validation(MINIMAL_VALID, 'minimal')
        self.assertEqual(result['errors'], [],
                         'unexpected errors: %s' % result['errors'])

    def test_custom_uniform_surfaces_as_error(self):
        src = MINIMAL_VALID.replace('void main() {',
                                    'uniform float sneaky;\nvoid main() {')
        result = run_validation(src, 'probe')
        self.assertFalse(result['passed'])
        self.assertTrue(any('uniform' in e.lower() for e in result['errors']))


class SuitabilityGateTests(unittest.TestCase):
    """ADJUDICATION: the detector's suitability verdict must be ENFORCED.

    The detector computed `suitable` but the CLI ignored it and converted
    anyway. A shader redeclaring `resolution` was emitted as a .frag that
    shadows the engine's uniform -- the external validator flags it, but only
    if you passed --validate. These tests pin the decline reasons.

    Image-input scope note: these assert only THAT a shader takes an image
    and is declined. They deliberately say nothing about how VS-2's image
    input works; that is an open question being revisited separately.
    """

    def test_clean_shader_is_suitable(self):
        r = check_suitability(MINIMAL_VALID)
        self.assertTrue(r['suitable'], 'unexpected errors: %s' % r['errors'])

    def test_engine_var_redeclaration_declines(self):
        r = check_suitability('vec2 resolution;\nvoid main(){}')
        self.assertFalse(r['suitable'])
        self.assertTrue(any('resolution' in e for e in r['errors']))

    def test_image_tex_usage_is_not_declined(self):
        """ADJUDICATION: a shader sampling `imageTex` is ORDINARY.

        An earlier revision declined these as "image-input, not understood".
        That was wrong: VS-2 binds the layer's configured image to imageTex,
        ignores a configured image for shaders that do not sample it, and
        previews these shaders in the gallery against a default image. The
        refusal was a gap in our own headless harness (no texture bound), not
        a property of the shader. Do not re-add a blanket decline without a
        named failing shader.
        """
        r = check_suitability('void main(){ fragColor = texture(imageTex, texCoord); }')
        self.assertTrue(r['suitable'],
                        'imageTex wrongly declined: %s' % r['errors'])

    def test_single_ishadertoy_channel_is_not_declined(self):
        src = ('void mainImage(out vec4 f, in vec2 c){'
               ' f = vec4(texture(iChannel0, c / 10.0).rgb, 1.0); }')
        r = check_suitability(src)
        self.assertTrue(r['suitable'],
                        'single iChannel wrongly declined: %s' % r['errors'])

    def test_multiple_ishadertoy_channels_still_declines(self):
        """VS-2 exposes ONE imageTex, so several distinct channels cannot be
        preserved. That is a real, demonstrable limit, so it still declines."""
        src = ('void mainImage(out vec4 f, in vec2 c){'
               ' f = texture(iChannel0, c) * texture(iChannel1, c); }')
        r = check_suitability(src)
        self.assertFalse(r['suitable'], 'multi-channel image input not declined')
        self.assertTrue(any('iChannel' in e for e in r['errors']))

    def test_too_many_params_declines(self):
        params = ', '.join('{"name": "p%d", "default": 0.5}' % i
                           for i in range(9))
        r = check_suitability('/*{"parameters": [%s]}*/\nvoid main(){}' % params)
        self.assertFalse(r['suitable'])
        self.assertTrue(any('parameters' in e for e in r['errors']))

    def test_hlsl_mention_in_a_comment_does_not_decline(self):
        """A comment documenting the shader's origin must not be read as
        unconverted HLSL.

        Found by the mutation harness: `detector: check HLSL in comments too`
        survived because nothing pinned this. The real instance is
        converted_shaders/conjugoth.frag line 289, which carries
        "// ... (original: lerp(blur1+blur3, ret, ret))" and is a valid
        shader that the MVP gate must not decline.
        """
        src = ('/*{"parameters": [{"name": "level", "default": 0.5}]}*/\n'
               '// Blurred blend (original: lerp(a, b, c))\n'
               'void main() { fragColor = vec4(level); }')
        r = check_suitability(src)
        self.assertTrue(r['suitable'],
                        'comment mentioning lerp() caused a false decline: %s'
                        % r['errors'])

    def test_real_hlsl_call_still_declines(self):
        """The counterpart: an actual `lerp(` CALL in code must still be
        caught, or the fix above would have gutted the check."""
        src = 'void main() { fragColor = vec4(lerp(vec3(0.0), vec3(1.0), 0.5)); }'
        r = check_suitability(src)
        self.assertFalse(r['suitable'], 'real HLSL lerp() was not detected')

    def test_camelcase_source_params_do_not_error(self):
        """A camelCase SOURCE name is normal (iResolution etc.); the
        generator snake-cases the OUTPUT and the validator checks that.
        Regression: the old check appended its error AFTER `suitable` was
        computed, so it was invisible twice over."""
        r = check_suitability('uniform float badName;\nvoid main(){}')
        self.assertEqual(r['errors'], [],
                         'camelCase source param wrongly errored: %s' % r['errors'])
        self.assertTrue(r['suitable'])

    def test_suitable_is_consistent_with_errors(self):
        """suitable must be exactly `no errors`, or the CLI gate is a lie."""
        for src in [MINIMAL_VALID,
                    'vec2 resolution;\nvoid main(){}',
                    'void main(){ fragColor = texture(imageTex, texCoord); }',
                    'uniform float badName;\nvoid main(){}']:
            r = check_suitability(src)
            self.assertEqual(r['suitable'], len(r['errors']) == 0,
                             'suitable/errors disagree for: %r' % src[:40])


class PerfBudgetTests(unittest.TestCase):
    """ADJUDICATION: the loop-budget warning is a gate, so it needs a control.

    Found by the gate audit (tests/test_gate_audit.py). WARN never fails the
    validator's exit code and NOTHING asserted this warning, so raising the
    threshold from 100 to 300 changed no observable behaviour anywhere in the
    suite. A warning no test can see is not a gate.

    The two real instances it guards today are converted_shaders/
    pathogen_path.frag (130) and spiral_riders_kali.frag (150), the only two
    shipped files the validator warns about.
    """

    OVER_BUDGET = '\n'.join([
        '/*{"parameters": [{"name": "level", "default": 0.5}]}*/',
        'void main() {',
        '    vec3 acc = vec3(0.0);',
        '    for (int i = 0; i < 200; i++) {',
        '        acc += vec3(float(i) * 0.001);',
        '    }',
        '    vec3 c = acc * color.rgb * level;',
        '    float coloredPixels = dot(clamp(c, 0.0, 1.0), vec3(1.0));',
        '    fragColor = vec4(c, alpha * coloredPixels);',
        '}',
    ])

    def _validator_output(self, body):
        validator = os.path.join(REPO_ROOT, '.cline', 'skills',
                                 'vs2-shader-expert', 'scripts',
                                 'validate_shader.py')
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'over-budget.frag')
            with open(path, 'w') as fh:
                fh.write(body)
            proc = subprocess.run([sys.executable, validator, path],
                                  capture_output=True, text=True)
        return proc.stdout + proc.stderr

    def test_loop_over_the_budget_warns(self):
        out = self._validator_output(self.OVER_BUDGET)
        self.assertIn(
            'loop bound 200 > 100', out,
            'the perf-budget warning did not fire for a 200-iteration loop: %s'
            % out[-300:])

    def test_loop_under_the_budget_does_not_warn(self):
        """Counterpart, so a check that warns on every loop cannot pass."""
        out = self._validator_output(self.OVER_BUDGET.replace('< 200', '< 50'))
        self.assertNotIn('loop bound', out,
                         'a 50-iteration loop was reported over budget: %s'
                         % out[-300:])


if __name__ == '__main__':
    unittest.main(verbosity=2)
