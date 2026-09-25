"""
Input format detection and suitability checking.

Bundled rules from validate_shader.py and golden examples.
"""

import json
import re
from pathlib import Path

from .textutil import code_only


# --- Detection Patterns ---

# ISF: Has JSON manifest with "INPUTS" (ISF) or "parameters" (VS-2 style) array
# ISF's own declaration is an "INPUTS" array (uppercase). A VS-2-style
# manifest uses lowercase "parameters" and a Shadertoy .txt may carry one, so
# conflating the two let a Shadertoy file look like ISF and - worse - let a
# mainImage() override a real ISF INPUTS block, silently discarding it.
ISF_INPUTS_RE = re.compile(r'/\*.*?"INPUTS".*?\*/', re.DOTALL)
VS2_PARAMETERS_RE = re.compile(r'/\*.*?"parameters".*?\*/', re.DOTALL)
ISF_MANIFEST_RE = re.compile(r'/\*.*?"(?:INPUTS|parameters)".*?\*/', re.DOTALL)
ISF_CODE_RE = re.compile(r'(uniform\s+float\s+iTime|uniform\s+vec2\s+iResolution|uniform\s+vec2\s+iMouse|uniform\s+sampler2D\s+iChannel\d+)')

# Shadertoy: Has mainImage() or main() with iTime/iResolution references
SHADERTOY_MAINIMAGE_RE = re.compile(r'mainImage\s*\(\s*.*?\)')
SHADERTOY_TIME_RE = re.compile(r'\biTime\b')

# GLSLSandbox: Has uniform declarations for time/resolution
GLSLSANDBOX_TIME_RE = re.compile(r'uniform\s+float\s+time\s*;')
GLSLSANDBOX_RESOLUTION_RE = re.compile(r'uniform\s+vec2\s+resolution\s*;')

# MilkDrop/HLSL: Has HLSL-specific functions
HLSL_FRACTION_RE = re.compile(r'\bfrac\s*\(')
HLSL_LERP_RE = re.compile(r'\blerp\s*\(')
HLSL_TEXTURE2D_RE = re.compile(r'\btexture2D\s*\(|\btex2D\s*\(')


def detect_format(source: str) -> dict:
    """
    Detect input format from source code.
    
    Returns:
        dict with keys:
            - format: str (ISF, Shadertoy, GLSLSandbox, MilkDrop, PlainGLSL)
            - suitable: bool
            - errors: list[str]
            - warnings: list[str]
    """
    errors = []
    warnings = []
    
    # HLSL/API checks run against code only: a comment mentioning `lerp(` is
    # documentation of the original, not unconverted HLSL.
    code = code_only(source)

    # Check for HLSL leftovers (definitely not suitable)
    if HLSL_FRACTION_RE.search(code):
        errors.append("Contains HLSL function 'frac()' (use 'fract()' in GLSL)")
    if HLSL_LERP_RE.search(code):
        errors.append("Contains HLSL function 'lerp()' (use 'mix()' in GLSL)")
    if HLSL_TEXTURE2D_RE.search(code):
        errors.append("Contains HLSL function 'texture2D()' or 'tex2D()' (use 'texture()' in GLSL)")
    
    # Check for engine variable redeclaration
    ENGINE_VARS = ['resolution', 'time', 'texCoord', 'color', 'alpha', 'imageTex', 'imageResolution']
    ENGINE_VAR_RE = re.compile(r'^(float|vec2|vec3|vec4|int|mat2|mat3|sampler2D)\s+(' + '|'.join(ENGINE_VARS) + r')\b')
    for i, line in enumerate(code.split('\n'), 1):
        if line and not line[0].isspace() and ENGINE_VAR_RE.match(line):
            errors.append(f"Line {i}: redeclares engine var '{ENGINE_VAR_RE.match(line).group(2)}' at global scope")
            break
    
    # Check for Shadertoy globals without #define alias
    # In VS-2, iTime/iResolution etc. are globals, so don't flag them as errors
    SHADERTOY_GLOBALS = ['iTime', 'iResolution', 'iMouse', 'iChannel0', 'iChannel1', 'iChannel2', 'iChannel3', 'iDate']
    defines = set()
    for line in code.split('\n'):
        dm = re.match(r'^\s*#define\s+(\w+)', line)
        if dm:
            defines.add(dm.group(1))
    
    # iTime / iResolution / iMouse are VS-2 globals (or plain renames), so
    # they are not errors. iChannel0..3 previously WAS an error demanding an
    # #define alias, but formal-spec.md line 26/274 documents the mapping as a
    # rename: `iChannel0..3` -> `imageTex`. The converter does that rename,
    # so an unaliased reference is expected input, not a defect.
    #
    # The one case still worth declining is a Shadertoy shader that reads
    # several DISTINCT channels, since VS-2 exposes a single imageTex and
    # collapsing them would silently change the image. See the generator's
    # iChannel handling.
    _channels_used = sorted(set(re.findall(r'\biChannel([0-3])\b', code)))
    if len(_channels_used) > 1:
        errors.append(
            "reads %d distinct iChannels (iChannel%s); VS-2 exposes a single "
            "imageTex, so the channels would have to be collapsed. Convert by "
            "hand via the vs2-shader-expert skill"
            % (len(_channels_used), ''.join(_channels_used)))
    
    # Detect format
    format_name = "PlainGLSL"
    
    # ISF first, and on the strength of a real INPUTS block. ISF is
    # self-describing: the manifest declares what the file is, so it
    # outranks a `mainImage` that happens to be present. Previously mainImage
    # won, and a Shadertoy-imported ISF had its INPUTS silently discarded -
    # its knobs vanished and the body kept undeclared names, producing GLSL
    # that would not compile.
    if ISF_INPUTS_RE.search(source):
        format_name = "ISF"
    # GLSLSandbox: uniform time/resolution declarations, no mainImage.
    elif GLSLSANDBOX_TIME_RE.search(source) or GLSLSANDBOX_RESOLUTION_RE.search(source):
        format_name = "GLSLSandbox"
    # Shadertoy: mainImage, or iTime with no ISF manifest.
    elif SHADERTOY_MAINIMAGE_RE.search(source) or (SHADERTOY_TIME_RE.search(source) and not VS2_PARAMETERS_RE.search(source)):
        format_name = "Shadertoy"
    # MilkDrop check (HLSL functions present)
    elif HLSL_FRACTION_RE.search(code) or HLSL_LERP_RE.search(code) or HLSL_TEXTURE2D_RE.search(code):
        format_name = "MilkDrop"
    
    return {
        "format": format_name,
        "suitable": len(errors) == 0,
        "errors": errors,
        "warnings": warnings
    }


def check_suitability(source: str) -> dict:
    """
    Check if source is suitable for conversion to VS-2.
    
    Returns:
        dict with keys:
            - suitable: bool
            - errors: list[str]
            - warnings: list[str]
    """
    # Reuse detection errors
    result = detect_format(source)
    code = code_only(source)
    
    # Additional suitability checks
    # Check parameter count (max 7)
    params = _extract_parameters(source)
    if len(params) > 7:
        result["errors"].append(f"Too many parameters: {len(params)} (max 7)")

    # Image-input shaders are NOT declined.
    #
    # An earlier revision declined any shader referencing iChannel / imageTex
    # on the grounds that image input was "not understood". That was wrong on
    # two counts: VS-2 binds the layer's configured image to `imageTex` and
    # the gallery previews these shaders against a default image, so they are
    # ordinary shaders; and the refusal was a limitation of our own headless
    # harness (it had no texture bound), not of the conversion.
    #
    # The harness now binds a default image, so image-input shaders render
    # and are gated on pixels like everything else - 79/79 corpus shaders
    # pass. The genuine hard gates stay in core/generator.py, where they
    # belong: ISF `TYPE: image` inputs, which would require the converter to
    # *emit* an image the user has not supplied, and bundled texture reads
    # (texture2D/TEXTURE_*) that imply a multi-pass merge.
    #
    # Do not re-add a blanket image-input decline without a demonstrated
    # failure. If one is found, put it here with the failing shader named.

    result["suitable"] = len(result["errors"]) == 0
    
    # Check parameter naming (snake_case required)
    for name in params:
        if not re.match(r'^[a-z][a-z0-9_]*$', name):
            result["errors"].append(f"Parameter '{name}' is not snake_case")
    
    # Check for speed parameters
    SPEED_PATTERNS = ['speed', 'rate', 'frequency', 'freq', 'twist_speed']
    for pattern in SPEED_PATTERNS:
        if re.search(rf'\b{pattern}\b', ' '.join(params)):
            result["warnings"].append(f"Parameter '{pattern}' may be a custom speed parameter (use native 'time')")
    
    return result


def _extract_parameters(source: str) -> list:
    """Extract parameter names from manifest (GLSL order: uniform <type> <name>;)"""
    params = []

    # Manifest: strip comment delimiters before json.loads (same proven
    # approach as core/mapper.py - group(0) includes /* */ and always throws)
    import json
    for block in re.findall(r'/\*(.*?)\*/', source, re.DOTALL):
        try:
            data = json.loads(block.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict) and 'parameters' in data:
            params = [p.get('name', '') for p in data.get('parameters', [])
                      if isinstance(p, dict) and p.get('name')]
            break

    # Fallback: extract from code (uniform declarations)
    if not params:
        uniform_re = re.compile(
            r'uniform\s+(?:float|vec2|vec3|vec4|int|mat2|mat3|sampler2D)\s+(\w+)')
        for match in uniform_re.finditer(source):
            name = match.group(1)
            if name not in params:
                params.append(name)

    return params


# --- Known MVP limitations ----------------------------------------------
#
# 1. Image-input shaders are supported. A shader sampling `imageTex` (or
#    reading a single iChannel) converts and renders; the headless gate binds
#    a default image, mirroring the VS-2 gallery. Such a shader needs an image
#    configured in VS-2 to show anything, and conversion warns about it.
#
# 2. Shadertoy shaders reading more than one distinct iChannel are declined:
#    VS-2 exposes a single imageTex, so the channels cannot be preserved.
#    That is a real limit of the engine, demonstrated by reading two.
#
# 3. ISF `TYPE: image` inputs convert. An earlier revision declined them,
#    claiming the converter "would have to emit an image the user hasn't
#    supplied" - which was wrong twice over: the input is the shader declaring
#    that it REQUIRES an image (the same role as an iChannel0 read), and VS-2
#    satisfies it from the layer's configured image. No image is emitted, so
#    none needs supplying. A source that instead EMBEDS pixel data
#    (IMG_NORM_PIXEL(TEXTURE_0), TEXTURE_* macros) is a different case and
#    remains gated: there is genuinely nothing for VS-2 to sample.
#
# 4. Parameter-over-budget is a decline reason, not an auto-trim. The
#    generator bakes extras to their defaults, but the app does not decide
#    which parameters a user would accept losing.
