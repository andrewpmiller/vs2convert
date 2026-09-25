"""
Parameter mapping - converts source parameters to VS-2 fixed-range knobs.

Bundled rules from golden examples and conversion guide.
"""

import re
import json
from dataclasses import dataclass, field

@dataclass
class ParameterMapping:
    """Maps a source parameter to a VS-2 knob."""
    name: str
    default: float = 0.5
    source_range: tuple = (0.0, 1.0)
    body_formula: str = ""
    notes: str = ""
    merged_with: str = ""
    deleted: bool = False


@dataclass
class MappingResult:
    """Result of parameter mapping."""
    parameters: list = field(default_factory=list)
    merged_params: list = field(default_factory=list)
    deleted_params: list = field(default_factory=list)
    baked_constants: list = field(default_factory=list)
    zoom_direction: str = "inverted"
    speed_baked: bool = False


def map_parameters(source: str) -> MappingResult:
    """Map source parameters to VS-2 fixed-range knobs."""
    result = MappingResult()
    params = _extract_source_parameters(source)
    
    if not params:
        return result
    
    source_params = {}
    for name, info in params.items():
        source_params[name] = info
    
    for name, info in source_params.items():
        if _is_speed_parameter(name, info):
            result.speed_baked = True
            result.baked_constants.append(f"  // Baked speed from '{name}' (default: {info.get('default', 0.5)})")
            result.baked_constants.append(f"  float timeLocal = time * {info.get('default', 0.5):.2f};")
            continue
        
        if _is_zoom_parameter(name, info):
            result.parameters.append(ParameterMapping(
                name=f"{name}_zoom",
                default=info.get('default', 0.5),
                source_range=(info.get('min', 0.0), info.get('max', 1.0)),
                body_formula=f"uv *= (1.-zoom)*{info.get('multiplier', 10.0)};",
                notes="Zoom (inverted direction)"
            ))
            continue
        
        if _is_rotation_parameter(name, info):
            result.parameters.append(ParameterMapping(
                name=f"{name}_rot",
                default=info.get('default', 0.0),
                source_range=(-1.0, 1.0),
                body_formula=f"rotation * 2.0 * PI;",
                notes="Rotation (bipolar)"
            ))
            continue
        
        if _is_pan_parameter(name, info):
            result.parameters.append(ParameterMapping(
                name=f"{name}_pan",
                default=info.get('default', 0.5),
                source_range=(0.0, 1.0),
                body_formula=f"uv -= vec2({name}_pan, {name}_pan * div);",
                notes="Pan (centered)"
            ))
            continue
        
        # Standard parameter
        lo = info.get('min', 0.0)
        hi = info.get('max', 1.0)
        default = info.get('default', 0.5)
        knob_default = (default - lo) / (hi - lo) if hi != lo else 0.5
        
        if _should_rewindow(knob_default, lo, hi, default):
            knob_default = 0.5
            body_formula = f"{name} * 2.0 - 1.0 + {default};"
            result.parameters.append(ParameterMapping(
                name=name,
                default=knob_default,
                source_range=(lo, hi),
                body_formula=body_formula,
                notes="Rewindowed for knob ergonomics"
            ))
        else:
            body_formula = f"{lo} + {name} * ({hi} - {lo});"
            result.parameters.append(ParameterMapping(
                name=name,
                default=knob_default,
                source_range=(lo, hi),
                body_formula=body_formula,
                notes=""
            ))
    
    return result


def _is_speed_parameter(name: str, info: dict) -> bool:
    """Check if parameter is a speed/rate parameter."""
    speed_keywords = ['speed', 'rate', 'frequency', 'freq', 'twist_speed']
    name_lower = name.lower()
    return any(kw in name_lower for kw in speed_keywords)


def _is_zoom_parameter(name: str, info: dict) -> bool:
    """Check if parameter is a zoom parameter."""
    return name.lower() in ['zoom', 'zoom_level', 'zoom_factor']


def _is_rotation_parameter(name: str, info: dict) -> bool:
    """Check if parameter is a rotation parameter."""
    return name.lower() in ['rotation', 'angle', 'rot', 'twist']


def _is_pan_parameter(name: str, info: dict) -> bool:
    """Check if parameter is a pan/position parameter."""
    return name.lower() in ['pan', 'position', 'offset', 'x', 'y', 'center']


def _should_rewindow(knob_default: float, lo: float, hi: float, default: float) -> bool:
    """Check if parameter should be re-windowed for knob ergonomics."""
    range_size = hi - lo
    if range_size == 0:
        return False
    center = (lo + hi) / 2.0
    distance_from_center = abs(default - center)
    return distance_from_center > 0.3 * range_size


def _extract_source_parameters(source: str) -> dict:
    """Extract source parameters from manifest or code.

    CONTRACT: this handles the *lowercase* Shadertoy/PlainGLSL manifest
    convention only (`{"parameters": [...]}`), and a uniform-declaration
    fallback. It does NOT understand the ISF convention (`{"INPUTS": [...]}`
    with uppercase NAME/TYPE/DEFAULT/MIN/MAX keys) — ISF sources are handled
    by `core.generator._convert_isf`, which parses INPUTS directly.

    Feeding raw ISF here returns {} silently. That is intentional, not a bug,
    but it is a sharp edge: if you ever route ISF into this function, add ISF
    key handling first or the params will vanish without an error.
    """
    params = {}
    
    comment_blocks = re.findall(r'/\*(.*?)\*/', source, re.DOTALL)
    for block_text in comment_blocks:
        text = block_text.strip()
        try:
            params_data = json.loads(text)
            if isinstance(params_data, dict) and 'parameters' in params_data:
                for p in params_data.get('parameters', []):
                    name = p.get('name', '')
                    if name:
                        params[name] = {
                            'default': p.get('default', 0.5),
                            'min': p.get('min', 0.0),
                            'max': p.get('max', 1.0),
                            'type': p.get('type', 'float')
                        }
                break  # Found a valid manifest block
        except (json.JSONDecodeError, ValueError):
            continue
    
    if not params:
        uniform_re = re.compile(r'uniform\s+(?:float|vec2|vec3|vec4|int|mat2|mat3|sampler2D)\s+(\w+)\s*;')
        for match in uniform_re.finditer(source):
            name = match.group(1)
            if name not in params:
                params[name] = {
                    'default': 0.5,
                    'min': 0.0,
                    'max': 1.0,
                    'type': 'float'
                }
    
    return params
