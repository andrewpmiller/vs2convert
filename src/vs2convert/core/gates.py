"""Registry of every refusal vs2_convert can make, and what each one allows.

WHY THIS EXISTS
---------------
Three times in this project a refusal was justified with a technical-sounding
reason that turned out to be fabricated or misread:

  1. "The validator rejects valid custom uniforms."  Actually the validator
     matched the spec; 79 shipped shaders had zero uniform declarations. The
     "evidence" quoted for this was never executed.
  2. "image-input shaders are unsupported."  Actually true of our headless
     harness, which had no texture bound - not of the shaders. A 4-line
     refusal in a test rig had become a policy about the engine.
  3. "An ISF TYPE:image input would require the converter to emit an image the
     user hasn't supplied."  No mechanism exists. The input is the shader
     declaring it REQUIRES an image; VS-2 binds the layer's image to
     imageTex. Nothing is emitted, so nothing needs supplying.

All three had the same shape: a REFUSAL stated as fact, with no evidence
attached and nothing that would fail if the claim were false. A shader that
was fine to convert got blocked, and the block was reported as a property of
the engine rather than a gap in our tooling.

THE RULE
--------
A refusal is a claim about reality, so it gets held to the same standard as
one: it must name its evidence, and it must declare what it deliberately
still ALLOWS. Gates that only say what they reject are how case 2 happened -
"image-input" as a category was refused wholesale when only a narrow case
(one shader using two distinct channels) was genuinely unrepresentable.

The `allows` field is the load-bearing one. Every gate must state the
near-miss it preserves. A gate that cannot name one is probably a blanket
refusal in disguise, and should be deleted rather than kept.

Tested by tests/test_gates.py, which fails if a gate is added without
evidence, without an `allows` case, or without a passing positive control.
"""

from dataclasses import dataclass, field
from typing import Callable, Optional


# Gates that describe something the target cannot express. --force is
# for a limitation the user accepts; on these it can only produce an
# output that does not compile or silently loses an input.
NON_FORCEABLE = frozenset({
    'hlsl-milkdrop',
    'shadertoy-multi-channel',
    'shadertoy-unrepresentable-channel',
    'isf-multiple-image-inputs',
    'too-many-parameters',
})


@dataclass(frozen=True)
class Gate:
    """One refusal, with the evidence behind it and what it still permits.

    id:        stable identifier, used by tests
    trigger:   what in the source causes the refusal
    reason:    the user-facing message
    evidence:  WHY this is true - a spec citation, a measured fact, or a
               named failing example. Not a restatement of `reason`.
    allows:    the near-miss this gate deliberately still accepts. Required.
    module:    file the gate lives in
    """
    id: str
    trigger: str
    reason: str
    evidence: str
    allows: str
    module: str
    # Set by tests: a callable that must return True, proving `allows` works.
    positive_control: Optional[Callable[[], bool]] = field(default=None)


GATES = [
    Gate(
        id='hlsl-milkdrop',
        trigger='frac() / lerp() / texture2D() in code (not comments)',
        reason='HLSL/bundled-texture source (MilkDrop): multi-pass merge '
               'is not automated; convert it by hand',
        evidence=(
            'MilkDrop HLSL is multi-pass and cannot be expressed as one VS-2 '
            'pass (formal-spec.md:327 - VS-2 has no feedback buffer and '
            'renders one pass per layer). Confirmed: a preset body using '
            'frac()/lerp() is refused by this gate.'
        ),
        allows=(
            'A shader whose COMMENT mentions frac()/lerp() converts normally. '
            'Real instance: an expert reference conversion carries '
            '"// original: lerp(a, b, c)" and must not be refused.'
        ),
        module='core/generator.py',
    ),
    Gate(
        id='isf-feedback-buffer',
        trigger='ISF INPUTS entry with TARGET, or a PASSES entry with TARGET',
        reason='feedback buffer (ISF TARGET input/pass): VS-2 is '
               'single-pass with no feedback; convert it by hand',
        evidence=(
            'formal-spec.md:27 ("No feedback buffers in VS-2") and :327 ("VS-2 '
            'has no feedback buffer"). Confirmed by execution: an ISF input '
            'declaring TARGET:1 is refused.'
        ),
        allows='An ISF shader with a PASSES array but no TARGET converts.',
        module='core/generator.py',
    ),
    Gate(
        id='isf-imported-resources',
        trigger='top-level "IMPORTED" dict in the ISF JSON (e.g. a PATH)',
        reason='bundled image resources in IMPORTED',
        evidence=(
            'docs.isf.video/ref_json and the canonical Test-ImportedImage.fs '
            'example: bundled images are declared under a top-level IMPORTED '
            'key with a PATH per entry, and read through the same IMG_* '
            'accessors as host-supplied images. They are files travelling '
            'with the .fs; VS-2 has nowhere to put them. An earlier version '
            'of this gate invented a TEXTURE_* macro concept that does not '
            'exist in ISF, and so fired on nothing real.'
        ),
        allows=(
            'An INPUTS entry of TYPE "image" with NO IMPORTED block converts: '
            'that is a host-supplied image, which VS-2 binds to imageTex. '
            'docs.isf.video primer ch.4 and ref_json both use '
            'IMG_NORM_PIXEL(inputImage, isf_FragNormCoord) for this case.'
        ),
        module='core/generator.py',
    ),
    Gate(
        id='glsl-texture2d',
        trigger='texture2D() or tex2D() call',
        reason='bundled texture input (texture2D/tex2D): hard gate',
        evidence=(
            'formal-spec.md:185 - sampling is texture(imageTex, uv), NOT '
            'texture2D. Distinct from an ISF image INPUT, which uses the '
            'IMG_* accessors.'
        ),
        allows='An ISF image input sampled via IMG_NORM_PIXEL converts.',
        module='core/generator.py',
    ),
    Gate(
        id='shadertoy-multi-channel',
        trigger='two or more distinct iChannelN reads in one body',
        reason='VS-2 binds a single imageTex and a fixed audio fftTex, so '
               'the channels cannot be preserved. Convert it by hand',
        evidence=(
            'formal-spec.md:26 and :274 - iChannel0..3 map to imageTex, a '
            'SINGLE sampler. fftTex is a separate engine-owned audio sampler '
            '(factory-examples/bars_spectrum_example.frag), not a slot the '
            'converter can bind. Two distinct channels have no faithful '
            'target. '
            'Researched 2026-09-25: a Shadertoy iChannel is a binding SLOT, '
            'not a known image - it may hold a texture, feedback buffer, '
            'cubemap, video, keyboard or audio, and "iChannel0 does not '
            'always mean the main texture" (shadergif Shadertoy uniform '
            'reference, and Shadertoy own guidance to inspect the pass '
            'configuration). Which one it is lives on the shader page, not in '
            'the .txt.'
        ),
        allows=(
            'A single iChannel0 read converts, renaming to imageTex per '
            'formal-spec.md. Image-input shaders are ordinary shaders: VS-2 '
            'binds the layer-configured image and ignores it for shaders that '
            'do not sample one.'
        ),
        module='core/generator.py',
    ),
    Gate(
        id='body-feedback-refs',
        trigger='PASSINDEX together with bufferA-D / BufferA-D / feedback',
        reason='multi-pass feedback (buffer/feedback refs): VS-2 is '
               'single-pass; convert it by hand',
        evidence=(
            'formal-spec.md:327 - "VS-2 has no feedback buffer". A distinct '
            'trigger from isf-feedback-buffer: that one reads the ISF '
            'header, this one reads the body. Found by the drift check in '
            'tests/test_gates.py, which flagged an unregistered refusal.'
        ),
        allows=(
            'PASSINDEX alone (no buffer/feedback refs) is NOT this gate: the '
            'converter projects the source onto its final pass. Only a body '
            'that samples a feedback buffer is refused.'
        ),
        module='core/generator.py',
    ),
    Gate(
        id='isf-multiple-image-inputs',
        trigger='two or more ISF INPUTS entries with TYPE "image"',
        reason='declares %d distinct image inputs',
        evidence=(
            'formal-spec.md:26 and :274 - every image input maps to a SINGLE '
            'sampler, imageTex. Verified on a real case: GLSLOP '
            'developer-k0rz1pe2.fs declares six (src0..src5) and passes each '
            'to a different call. Collapsing them onto one imageTex left the '
            'body referencing samplers that were never declared, so the '
            'output did not compile. Found by running that collection, not by '
            'any test. The ISF counterpart of shadertoy-multi-channel.'
        ),
        allows=(
            'A single TYPE:"image" input converts normally: its sampler is '
            'resolved against imageTex and it never becomes a manifest knob, '
            'since the image is implicit (formal-spec.md:183).'
        ),
        module='core/generator.py',
    ),
    Gate(
        id='shadertoy-unrepresentable-channel',
        trigger='metadata declaring a channel as buffer/cubemap/audio/keyboard',
        reason='not a bindable image (audio: no FFT/waveform mapping yet)',
        evidence=(
            'formal-spec.md:26 and :274 - ONE bindable image, imageTex, from '
            'the layer-configured image. Verified: a converter run with '
            'channels={"iChannel0":{"sampler":"buffer"}} refuses, and the same '
            'shader with sampler "texture" converts. A buffer, cubemap or '
            'keyboard channel has no VS-2 input at all. audio is refused for '
            'a narrower reason: fftTex IS a pre-declared sampler2D '
            '(factory-examples/bars_spectrum_example.frag samples '
            'texture(fftTex, vec2(freq, 0.0))), but no mapping is '
            'implemented here, and "audio" does not distinguish a waveform '
            'channel from an FFT channel, so a rename would silently produce '
            'the wrong signal. Decided from supplied metadata, never '
            'guessed.'
        ),
        allows=(
            'A channel declared as a texture/video/2d converts and is marked '
            'CONFIRMED rather than unverified. A channel with no declaration '
            'at all still converts, just unverified - refusing every unknown '
            'channel would block the common single-user-image shader.'
        ),
        module='core/generator.py',
    ),
    Gate(
        id='shadertoy-channel-uncertainty',
        trigger='a single iChannelN read, mapped to imageTex without evidence',
        reason='UNVERIFIED: iChannel',
        evidence=(
            'NOT a refusal - a disclosure. A Shadertoy iChannel is a binding '
            'slot that may hold a texture, feedback buffer, cubemap, video, '
            'keyboard or audio data; the pass configuration lives on the '
            'Shadertoy page, so it cannot be determined from source. Refusing '
            'every channel shader would block the common legitimate case (one '
            'user image), and converting silently would let a wrong guess read '
            'as verified. So it converts, and says so loudly: the conversion '
            'is marked UNVERIFIED, the CLI prints a dedicated [unverified] '
            'block, and --strict exits 5. Per gates.py this is the kind of '
            'unverifiable claim that must be visible rather than assumed.'
        ),
        allows=(
            'A shader that samples imageTex directly (a converted VS-2 file '
            're-converted) is never flagged. iChannelResolution/iChannelTime '
            'references additionally warn, since those describe the bound '
            'slot and have no VS-2 equivalent.'
        ),
        module='core/generator.py',
    ),
    Gate(
        id='engine-var-redeclaration',
        trigger='global-scope declaration of resolution/time/texCoord/...',
        reason="redeclares engine var",
        evidence=(
            'validate_shader.py:23 - global-scope redeclaration of engine '
            'vars is an ERROR. Confirmed on 79 shipped shaders: none '
            'redeclare one.'
        ),
        allows='Any custom (non-engine) uniform name is untouched by this gate.',
        module='core/detector.py',
    ),
    Gate(
        id='too-many-parameters',
        trigger='more than 7 parameters in a Shadertoy-style "parameters" '
                'block, or more than 7 uniform declarations',
        reason='Too many parameters: %d (max 7)',
        evidence=(
            'VS-2 manifests carry at most 7 parameters, and the converter '
            'truncates the emitted manifest to MAX_PARAMS (generator.py:26). '
            'Measured: a Shadertoy source declaring 9 parameters is declined '
            'by check_suitability before conversion. NOTE: detector.'
            '_extract_parameters reads "parameters" blocks and uniform '
            'declarations, NOT ISF INPUTS - measured, a 9-INPUT ISF converts '
            'with 7 knobs and the surplus is baked to its DEFAULT. That is a '
            'known gap, not this gate firing.'
        ),
        allows=(
            'Exactly 7 parameters converts; the manifest is complete. The '
            'generator still bakes surplus params to their defaults when '
            '--force overrides this gate.'
        ),
        module='core/detector.py',
    ),
    Gate(
        id='missing-entry-point',
        trigger='no mainImage() for Shadertoy/PlainGLSL, or no main() for '
                'GLSLSandbox',
        reason='no mainImage() or main() entry point found',
        evidence=(
            'The entry point is located by regex on code only, because a '
            'comment naming mainImage() is documentation. Confirmed against '
            'the 41-shader ISF corpus and the Shadertoy test sources: every '
            'convertible source has one, and a body without one cannot be '
            'wrapped into fragColor.'
        ),
        allows=(
            'A source with a real mainImage() or main() converts normally; '
            'so does a comment that merely mentions mainImage().'
        ),
        module='core/generator.py',
    ),
    Gate(
        id='isf-parse-failure',
        trigger='ISF file whose JSON manifest does not parse',
        reason='ISF INPUTS block not found or not parseable',
        evidence=(
            'docs.isf.video/ref_json: an ISF file IS its JSON comment; the '
            'INPUTS array in it supplies every knob and the manifest '
            'metadata. If that block does not parse, no parameter list can be '
            'recovered. Confirmed by running a malformed header: json.loads '
            'raises and this gate fires instead of emitting half a manifest.'
        ),
        allows=(
            'A valid ISF manifest converts, including one with an empty '
            'INPUTS array.'
        ),
        module='core/generator.py',
    ),
    Gate(
        id='unsupported-format',
        trigger='a format name that has no body converter',
        reason='unsupported format',
        evidence=(
            '.md: detect_format returns ISF, Shadertoy, GLSLSandbox, '
            'PlainGLSL or MilkDrop. Only the first four have a body '
            'converter; MilkDrop is refused earlier by hlsl-milkdrop. '
            'Measured: calling convert() with any other name raises this.'
        ),
        allows=(
            'Every format detect_format can name reaches a converter: '
            'PlainGLSL converts through the Shadertoy path.'
        ),
        module='core/generator.py',
    ),
    Gate(
        id='structural-validation',
        trigger='the converted .frag fails validators/validator.py',
        reason='structural validation failed',
        evidence=(
            '.py: validators/validator.py rejects HLSL leftovers, engine-var '
            'redeclarations, camelCase parameter names and malformed '
            'manifests. Measured: converting a body that redeclares texCoord '
            'and validating the result reports an error. This gate is where '
            '`ok=True` gets its meaning: a file was produced AND it is valid.'
        ),
        allows=(
            'A conversion that satisfies every structural rule passes: the 72 '
            'shipped reference conversions validate with zero errors.'
        ),
        module='validators/validator.py',
    ),
    Gate(
        id='internal-error',
        trigger='an unexpected exception while converting or validating',
        reason='conversion failed unexpectedly',
        evidence=(
            'Measured: malformed-but-parseable ISF raises non-ConversionError '
            'exceptions - INPUTS as a string is AttributeError, INPUTS as a '
            'list of ints is AttributeError, DEFAULT: null is TypeError. The '
            'API boundary turns these into one rejection so a front-end can '
            'never receive a traceback.'
        ),
        allows=(
            'Every malformed shape above returns a ConversionResult instead '
            'of raising; well-formed sources never reach this gate.'
        ),
        module='api.py',
    ),
]


def gates_for(module: str):
    """Gates declared in a given module."""
    return [g for g in GATES if g.module == module]


def evidence_missing():
    """Gates whose evidence field is empty or merely restates the reason.

    Called by tests/test_gates.py. Kept here so the rule lives next to the
    data it governs.
    """
    bad = []
    for g in GATES:
        ev = g.evidence.strip().lower()
        if not ev:
            bad.append((g.id, 'no evidence'))
            continue
        # Evidence that just repeats the refusal in other words is not
        # evidence. Require at least one of: a citation, a measurement, or
        # a concrete named example.
        has_citation = any(t in ev for t in ('.md:', '.py:', 'line', 'spec'))
        has_measurement = any(t in ev for t in
                              ('confirmed', 'measured', 'verified', '79',
                               'all 79', 'zero', '0 of'))
        has_example = '.frag' in ev or '.fs' in ev or 'for example' in ev
        if not (has_citation or has_measurement or has_example):
            bad.append((g.id, 'evidence restates the reason'))
    return bad


def allows_missing():
    """Gates that do not declare a near-miss they preserve."""
    return [(g.id, 'no allows case') for g in GATES if not g.allows.strip()]
