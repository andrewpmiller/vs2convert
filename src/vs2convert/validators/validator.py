"""
Structural validation for VS-2 shaders.

Bundled rules from validation-checklist.md and golden examples.
"""

import json
import re


def run_validation(content: str, shader_name: str) -> dict:
    """
    Run structural validation on a VS-2 shader.
    
    Args:
        content: Full .frag file content
        shader_name: Name of the shader
        
    Returns:
        dict with keys:
            - passed: bool
            - errors: list[str]
            - warnings: list[str]
    """
    errors = []
    warnings = []
    
    # Check for HLSL leftovers / bundled textures
    if _has_hlsl_leftovers(content):
        if re.search(r'\btexture2D\s*\(|\btex2D\s*\(', content):
            errors.append(
                "texture2D()/tex2D() present - bundled texture input is a "
                "hard gate; convert it by hand")
        else:
            errors.append("Contains HLSL functions (frac, lerp) or gl_FragColor")
    
    # Check for engine variable redeclaration
    if _has_engine_var_redecl(content):
        errors.append("Engine variables redeclared at global scope")
    
    # Check for custom speed parameters
    if _has_custom_speed_params(content):
        warnings.append("Contains custom speed parameter (should use native 'time')")
    
    # Check for >7 parameters
    param_count = _count_parameters(content)
    if param_count > 7:
        errors.append(f"Too many parameters: {param_count} (max 7)")
    
    # Check parameter names are snake_case
    if _has_non_snake_case_params(content):
        errors.append("Parameter names are not snake_case")
    
    # Check for uniform declarations
    if _has_uniform_declarations(content):
        errors.append("Contains uniform/varying declarations "
                      "(engine injects these; not allowed)")
    
    # Check for missing output pattern
    if not _has_output_pattern(content):
        warnings.append("Missing coloredPixels or image alpha output pattern")
    
    # Check for missing coordinate setup
    if not _has_coordinate_setup(content):
        warnings.append("Missing coordinate setup (aspect ratio, pan)")
    
    # Check for missing manifest
    if not _has_manifest(content):
        warnings.append("Missing JSON manifest header")
    
    passed = len(errors) == 0
    
    return {
        "passed": passed,
        "errors": errors,
        "warnings": warnings
    }


def _has_hlsl_leftovers(content: str) -> bool:
    """Check for HLSL-specific functions."""
    if re.search(r'\btexture2D\s*\(|\btex2D\s*\(', content):
        return True
    return any(re.search(p, content) for p in [
        r'\bfrac\s*\(',
        r'\blerp\s*\(',
        r'gl_FragColor',
    ])


def _has_engine_var_redecl(content: str) -> bool:
    """Check for engine variable redeclaration."""
    engine_vars = ['resolution', 'time', 'texCoord', 'color', 'alpha', 'imageTex', 'imageResolution', 'fftTex']
    engine_var_re = re.compile(r'^(float|vec2|vec3|vec4|int|mat2|mat3|sampler2D)\s+(' + '|'.join(engine_vars) + r')\b')
    
    for line in content.split('\n'):
        if line and not line[0].isspace() and engine_var_re.match(line):
            return True
    return False


def _has_custom_speed_params(content: str) -> bool:
    """Check for custom speed parameters in manifest."""
    speed_patterns = ['speed', 'rate', 'frequency', 'freq', 'twist_speed']
    for data in _iter_manifests(content):
        if 'parameters' not in data:
            continue
        for p in data.get('parameters', []):
            name = p.get('name', '')
            if any(kw in name.lower() for kw in speed_patterns):
                return True
    return False


def _iter_manifests(content: str):
    """Yield parsed JSON dicts from all /* {json} */ comment blocks.
    Strip delimiters, try each block."""
    for block in re.findall(r'/\*(.*?)\*/', content, re.DOTALL):
        try:
            data = json.loads(block.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            yield data


def _count_parameters(content: str) -> int:
    """Count parameters in manifest."""
    for data in _iter_manifests(content):
        if 'parameters' in data:
            return len(data.get('parameters', []))

    # Fallback: count uniform declarations (GLSL order: uniform <type> <name>;)
    uniform_re = re.compile(
        r'uniform\s+(?:float|vec2|vec3|vec4|int|mat2|mat3|sampler2D)\s+\w+')
    return len(uniform_re.findall(content))


def _has_non_snake_case_params(content: str) -> bool:
    """Check if parameter names are not snake_case."""
    for data in _iter_manifests(content):
        if 'parameters' not in data:
            continue
        for p in data.get('parameters', []):
            name = p.get('name', '')
            if not re.match(r'^[a-z][a-z0-9_]*$', name):
                return True
        return False  # manifest found and parsed: its names are the truth
    return False  # no manifest: missing-manifest warning already covers this


def _has_uniform_declarations(content: str) -> bool:
    """Check for uniform/varying declarations.

    VS-2 injects its own uniforms and varyings; ANY declaration in the body
    shadows engine-injected state. This is an error, not a warning, and it
    applies to *custom* uniforms too - the engine owns the whole uniform
    namespace. Matches the external validator's rule (validate_shader.py:125)
    which matches `^\\s*(uniform|varying)\\s+`.
    """
    decl_re = re.compile(r'^\s*(uniform|varying)\s+')
    for line in content.split('\n'):
        if line and not line.lstrip().startswith('//') and decl_re.match(line):
            return True
    return False


def _has_output_pattern(content: str) -> bool:
    """Check for output pattern (coloredPixels or image alpha)."""
    if 'coloredPixels' in content:
        return True
    if 'fragColor' in content and ('alpha' in content or 'imgMask' in content):
        return True
    return False


def _has_coordinate_setup(content: str) -> bool:
    """Check for coordinate setup (aspect ratio, pan)."""
    if 'aspect' in content and 'div' in content:
        return True
    if 'uv =' in content:
        return True
    return False


def _has_manifest(content: str) -> bool:
    """Check for JSON manifest header."""
    return any('parameters' in data for data in _iter_manifests(content))
