"""
Input format detection and suitability checking.

Bundled rules from validate_shader.py and golden examples.
"""

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
    
    # Check for HLSL leftovers (definitely not suitable)
    if HLSL_FRACTION_RE.search(source):
        errors.append("Contains HLSL function 'frac()' (use 'fract()' in GLSL)")
    if HLSL_LERP_RE.search(source):
        errors.append("Contains HLSL function 'lerp()' (use 'mix()' in GLSL)")
    if HLSL_TEXTURE2D_RE.search(source):
        errors.append("Contains HLSL function 'texture2D()' or 'tex2D()' (use 'texture()' in GLSL)")
    
    # Check for engine variable redeclaration
    ENGINE_VARS = ['resolution', 'time', 'texCoord', 'color', 'alpha', 'imageTex', 'imageResolution']
    ENGINE_VAR_RE = re.compile(r'^(float|vec2|vec3|vec4|int|mat2|mat3|sampler2D)\s+(' + '|'.join(ENGINE_VARS) + r')\b')
    for i, line in enumerate(source.split('\n'), 1):
        if line and not line[0].isspace() and ENGINE_VAR_RE.match(line):
            errors.append(f"Line {i}: redeclares engine var '{ENGINE_VAR_RE.match(line).group(2)}' at global scope")
            break
    
    # Check for Shadertoy globals without #define alias
    # In VS-2, iTime/iResolution etc. are globals, so don't flag them as errors
    SHADERTOY_GLOBALS = ['iTime', 'iResolution', 'iMouse', 'iChannel0', 'iChannel1', 'iChannel2', 'iChannel3', 'iDate']
    defines = set()
    for line in source.split('\n'):
        dm = re.match(r'^\s*#define\s+(\w+)', line)
        if dm:
            defines.add(dm.group(1))
    
    # Only flag if used without alias AND not in a context that's expected
    for g in SHADERTOY_GLOBALS:
        if g not in defines:
            # Check if it's used without alias
            if re.search(rf'\b{g}\b', source):
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
    elif HLSL_FRACTION_RE.search(source) or HLSL_LERP_RE.search(source) or HLSL_TEXTURE2D_RE.search(source):
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
    
    # Additional suitability checks
    # Check parameter count (max 7)
    params = _extract_parameters(source)
    if len(params) > 7:
        result["errors"].append(f"Too many parameters: {len(params)} (max 7)")
    
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
