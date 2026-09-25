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
               'is not automated; convert by hand via the '
               'vs2-shader-expert skill',
        evidence=(
            'MilkDrop HLSL is multi-pass and cannot be expressed as one VS-2 '
            'pass. Confirmed against the skill: convert MilkDrop by hand.'
        ),
        allows=(
            'A shader whose COMMENT mentions frac()/lerp() converts normally. '
            'Real instance: converted_shaders/conjugoth.frag carries '
            '"// original: lerp(a, b, c)" and must not be refused.'
        ),
        module='core/generator.py',
    ),
    Gate(
        id='isf-feedback-buffer',
        trigger='ISF INPUTS entry with TARGET, or a PASSES entry with TARGET',
        reason='feedback buffer (ISF TARGET input/pass): VS-2 is '
               'single-pass with no feedback; convert by hand via the '
               'vs2-shader-expert skill',
        evidence=(
            'formal-spec.md:27 ("No feedback buffers in VS-2") and :327 ("VS-2 '
            'has no feedback buffer"). Confirmed by execution: an ISF input '
            'declaring TARGET:1 is refused.'
        ),
        allows='An ISF shader with a PASSES array but no TARGET converts.',
        module='core/generator.py',
    ),
    Gate(
        id='isf-bundled-texture',
        trigger='texture2D()/tex2D() or IMG_NORM_PIXEL(TEXTURE_*, ...)',
        reason='bundled texture input (TEXTURE_*): hard gate; convert by '
               'hand via the vs2-shader-expert skill',
        evidence=(
            'formal-spec.md:185 - sampling is texture(imageTex, uv), NOT '
            'texture2D. A bundled texture is pixel data compiled into the '
            'source, so there is nothing for VS-2 to bind. The gate regex '
            'requires the TEXTURE_ prefix to avoid catching '
            'IMG_NORM_PIXEL(img, ...), which names a declared input - a '
            'distinction a test fixture previously got wrong.'
        ),
        allows=(
            'IMG_NORM_PIXEL(img, ...) - a named image input - converts. This '
            'distinction was previously conflated in a test fixture, which '
            'asserted a rejection the gate never performed.'
        ),
        module='core/generator.py',
    ),
    Gate(
        id='shadertoy-multi-channel',
        trigger='two or more distinct iChannelN reads in one body',
        reason='VS-2 exposes a single imageTex, so the channels cannot '
               'be preserved. Convert by hand via the vs2-shader-expert '
               'skill',
        evidence=(
            'formal-spec.md:26 and :274 - iChannel0..3 map to imageTex, a '
            'SINGLE sampler. Two distinct channels have no faithful target.'
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
               'single-pass; convert by hand via the vs2-shader-expert skill',
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
