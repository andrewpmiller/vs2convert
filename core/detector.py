"""
Input format detection and suitability checking.

Bundled rules from validate_shader.py and golden examples.
"""

import json
import re
from pathlib import Path


# --- Detection Patterns ---

# ISF: Has JSON manifest with "INPUTS" (ISF) or "parameters" (VS-2 style) array
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


def _code_only(source: str) -> str:
    """Source with comments removed, preserving line structure.

    HLSL/API checks must not fire on a mention inside a comment: a converted
    shader legitimately carries comments like "original: lerp(a, b, c)" that
    would otherwise be misread as unconverted HLSL. Newlines are preserved so
    per-line checks (engine-var redeclaration) keep their line numbers.
    """
    def _blank(m):
        return re.sub(r'[^\n]', ' ', m.group(0))
    out = re.sub(r'/\*.*?\*/', _blank, source, flags=re.DOTALL)
    out = re.sub(r'//[^\n]*', _blank, out)
    return out


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
    code = _code_only(source)

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
    
    # Only flag if used without alias AND not in a context that's expected
    for g in SHADERTOY_GLOBALS:
        if g not in defines:
            # Check if it's used without alias
            if re.search(rf'\b{g}\b', code):
                # Don't flag iTime/iResolution/iMouse as errors - they're VS-2 globals
                if g in ['iChannel0', 'iChannel1', 'iChannel2', 'iChannel3']:
                    errors.append(f"Shadertoy global '{g}' used without #define alias")
    
    # Detect format
    format_name = "PlainGLSL"
    
    # ISF check
    if ISF_MANIFEST_RE.search(source) and not (SHADERTOY_MAINIMAGE_RE.search(source) or GLSLSANDBOX_TIME_RE.search(source)):
        format_name = "ISF"
    # Shadertoy check
    elif SHADERTOY_MAINIMAGE_RE.search(source) or (SHADERTOY_TIME_RE.search(source) and not ISF_MANIFEST_RE.search(source)):
        format_name = "Shadertoy"
    # GLSLSandbox check
    elif GLSLSANDBOX_TIME_RE.search(source) or GLSLSANDBOX_RESOLUTION_RE.search(source):
        format_name = "GLSLSandbox"
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
    code = _code_only(source)
    
    # Additional suitability checks
    # Check parameter count (max 7)
    params = _extract_parameters(source)
    if len(params) > 7:
        result["errors"].append(f"Too many parameters: {len(params)} (max 7)")

    # Image-input shaders: DECLINE for the MVP.
    #
    # Scope note (deliberately narrow): this only records THAT the shader
    # takes an image, and refuses it. It does not attempt to model how
    # VS-2's image input works - that is an open question being revisited
    # separately, so nothing here should be read as a description of the
    # feature. Detection is intentionally broad and conservative: any of
    # these markers means "takes an image".
    #
    # Shadertoy-style channel references, with or without an #define alias.
    if re.search(r'\biChannel[0-3]\b', code):
        result["errors"].append(
            "takes an image input (iChannel): image-input conversion is not "
            "automated in the MVP; convert by hand via the vs2-shader-expert "
            "skill")

    # ISF manifest with a typed image input.
    for m in re.finditer(r'/\*(.*?)\*/', source, re.DOTALL):
        try:
            header = json.loads(m.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(header, dict):
            continue
        for inp in header.get('INPUTS', []) or []:
            if isinstance(inp, dict) and inp.get('TYPE') == 'image':
                result["errors"].append(
                    "ISF input '%s' is an image: image-input conversion is "
                    "not automated in the MVP; convert by hand via the "
                    "vs2-shader-expert skill" % inp.get('NAME', '?'))
                break
        break

    # VS-2 sampler / image-resolution usage. Checked unconditionally: a
    # shader need not carry a manifest comment to reference imageTex.
    if re.search(r'\bimageTex\b|\bimageResolution\b', code):
        result["errors"].append(
            "uses imageTex/imageResolution: image-input conversion is not "
            "automated in the MVP; convert by hand via the "
            "vs2-shader-expert skill")

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
# 1. Image-input shaders are DECLINED, including hybrids. `conjugoth.frag`
#    samples imageTex but falls back to a procedural path when
#    imageResolution is (0, 0), so it renders fine without an image - yet the
#    MVP gate still declines it. That is intentional: a conservative decline
#    is honest, whereas claiming to support the image path is not. 3 of 79
#    corpus shaders are declined for this reason.
#
# 2. This module records THAT a shader takes an image. It does not model how
#    VS-2's image input works; that is an open question still being worked
#    through, and the detection markers here are deliberately broad so they
#    can be revised without unblocking anything prematurely.
#
# 3. Parameter-over-budget is a decline reason, not an auto-trim. The
#    generator bakes extras to their defaults, but the app does not decide
#    which parameters a user would accept losing.
