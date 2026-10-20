"""The public conversion API.

This is the single surface both the CLI and any future front-end call.
`convert()` never raises on a bad source: a decline is returned as data.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

from .core.detector import check_suitability
from .core.gates import NON_FORCEABLE
from .core.generator import ConversionError
from .core.generator import convert as _convert
from .validators.validator import run_validation

__all__ = ["convert", "ConversionResult", "Rejection"]


@dataclass(frozen=True)
class Rejection:
    """One reason a source was not converted."""

    stage: str          # "detect" | "suitability" | "convert" | "validate"
    gate: str           # stable id, from core.gates.GATES
    reason: str         # one-line human summary
    detail: str         # what the user can do about it
    issue_hint: Optional[str] = None


@dataclass(frozen=True)
class ConversionResult:
    """Everything a caller needs to show or write the outcome."""

    ok: bool
    frag: Optional[str]
    manifest: Optional[dict]
    params: List[dict]
    warnings: List[str]
    baked: List[str]
    rejections: List[Rejection]
    format: Optional[str]
    source_name: Optional[str]


# What the user can do about each refusal. Kept here, not in the CLI, so a
# future front-end gives the same advice. Keyed by the stable gate id, so a
# renamed message cannot silently lose its advice.
_GATE_ADVICE = {
    "hlsl-milkdrop": "MilkDrop/HLSL cannot be expressed as one VS-2 pass; "
                     "port it by hand.",
    "isf-feedback-buffer": "VS-2 has no feedback buffer. Remove the TARGET "
                           "input/pass, or convert by hand.",
    "isf-imported-resources": "The .fs ships its own image files, which VS-2 "
                              "cannot load. Supply those images from the VS-2 "
                              "layer instead.",
    "glsl-texture2d": "Replace texture2D()/tex2D() with texture(imageTex, uv), "
                      "or convert by hand.",
    "shadertoy-multi-channel": "VS-2 binds one image. Strip the shader down to "
                               "a single channel, or convert by hand.",
    "isf-multiple-image-inputs": "VS-2 binds one image. Use a single TYPE:image "
                                 "input, or convert by hand.",
    "shadertoy-unrepresentable-channel": "The pass metadata declares a buffer, "
                                         "cubemap or audio channel, which VS-2 "
                                         "cannot bind. Change the channel to a "
                                         "texture, or convert by hand.",
    "shadertoy-channel-uncertainty": "Pass the Shadertoy channel declarations "
                                     "with --meta so the binding can be "
                                     "confirmed.",
    "engine-var-redeclaration": "Remove the declaration; VS-2 provides "
                                "resolution, time, texCoord, color, alpha, "
                                "imageTex and imageResolution.",
    "too-many-parameters": "VS-2 accepts at most 7 parameters. Remove the "
                           "extras, or hard-code their values.",
    "missing-entry-point": "The source has no mainImage()/main(). Check that it "
                           "is a complete shader.",
    "isf-parse-failure": "Fix the JSON manifest, or obtain a clean copy of the "
                         ".fs.",
    "unsupported-format": "The source is not Shadertoy, ISF or GLSLSandbox.",
    "structural-validation": "The converted body did not pass the structural "
                             "validator. Report it with the source; do not "
                             "ship the output.",
    "internal-error": "This is a converter bug, not a problem with the source. "
                      "Please report it with the full traceback.",
}
_DEFAULT_ADVICE = "Adjust the source, or convert it by hand."


def _reject(stage: str, gate: str, reason: str) -> Rejection:
    return Rejection(stage=stage, gate=gate, reason=reason,
                     detail=_GATE_ADVICE.get(gate, _DEFAULT_ADVICE))


def _decline(source_name, fmt, warnings, rejections) -> ConversionResult:
    return ConversionResult(
        ok=False,
        frag=None,
        manifest=None,
        params=[],
        warnings=list(warnings),
        baked=[],
        rejections=list(rejections),
        format=fmt,
        source_name=source_name,
    )


def convert(source: str, *, name: Optional[str] = None, force: bool = False,
            meta: Optional[Dict] = None,
            validate: bool = True) -> ConversionResult:
    """Convert a shader source string to a VS-2 `.frag`.

    Never raises on a bad source: declines come back as `rejections`.
    `ok=True` means the body was produced AND (unless `validate=False`)
    passed the structural validator.
    """
    try:
        return _convert_checked(source, name, force, meta, validate)
    except Exception as exc:  # the contract boundary, deliberately broad
        return _decline(
            name, None, [],
            [_reject("convert", "internal-error",
                     "conversion failed unexpectedly: %s: %s"
                     % (type(exc).__name__, exc))])


def _convert_checked(source, name, force, meta, validate) -> ConversionResult:
    # One call decides: it returns the format and every suitability error,
    # and it re-runs detection internally. Calling detect_format as well
    # would just duplicate the same errors.
    verdict = check_suitability(source)
    fmt = verdict.get("format")
    errors = verdict.get("errors") or []
    gates = verdict.get("error_gates") or []
    warnings = list(verdict.get("warnings") or [])

    if errors:
        # A non-forceable gate is a fact about the target, not a limitation
        # this tool can override. Forcing one emits a body that does not
        # compile or silently drops an input, so decline even with --force.
        blocked = [g for g in gates if g in NON_FORCEABLE]
        if force and not blocked:
            warnings.append(
                "FORCED past the suitability gate: the output is very likely "
                "broken.")
        else:
            rejections = [_reject("suitability", gate, message)
                          for gate, message in zip(gates, errors)]
            return _decline(name, fmt, warnings, rejections)

    try:
        report = _convert(source, fmt, name or "shader", force=force, meta=meta)
    except ConversionError as exc:
        gate = exc.gate or "internal-error"
        return _decline(name, fmt, warnings,
                        [_reject("convert", gate, str(exc))])

    frag = report["frag"]
    warnings.extend(report.get("warnings") or [])
    baked = list(report.get("baked") or [])
    manifest = report.get("manifest")
    params = list(report.get("params") or [])

    rejections = []
    if validate:
        try:
            check = run_validation(frag, name or "shader")
        except Exception as exc:
            rejections.append(_reject(
                "validate", "internal-error",
                "validation failed unexpectedly: %s: %s"
                % (type(exc).__name__, exc)))
        else:
            for message in check.get("errors") or []:
                rejections.append(
                    _reject("validate", "structural-validation", message))
            warnings.extend(check.get("warnings") or [])

    if rejections:
        return _decline(name, fmt, warnings, rejections)

    return ConversionResult(
        ok=True,
        frag=frag,
        manifest=manifest,
        params=params,
        warnings=warnings,
        baked=baked,
        rejections=[],
        format=fmt,
        source_name=name,
    )
