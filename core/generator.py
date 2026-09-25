"""
VS-2 conversion engine - transforms source shaders into complete VS-2 .frag files.

Pipeline: consume source metadata -> format-specific body transforms (scope-aware
parameter remapping, builtin renames) -> output-pattern rewrite -> precision block
-> manifest header.

Hard gates (ConversionError): MilkDrop/HLSL, bundled textures, feedback buffers /
ISF TARGET inputs, and Shadertoy shaders reading more than one distinct
iChannel (VS-2 exposes a single imageTex).

Image inputs are NOT gated. A single iChannel0 read, or an ISF
`TYPE: image` input, renames to `imageTex` per formal-spec.md, which VS-2
binds from the layer's configured image. The image is implicit: no manifest
parameter is created for it.

Manifest knobs are injected by the VS-2 engine as uniforms; body references to
knob names are valid as-is.
"""
import re
import json
import uuid as _uuid

from .textutil import code_only

MAX_PARAMS = 7
SPEED_KEYWORDS = ('speed', 'rate', 'frequency', 'freq')

ENGINE_VARS = {'resolution', 'time', 'texCoord', 'color', 'alpha',
               'imageTex', 'imageResolution'}
GLSL_TYPES = ('float', 'vec2', 'vec3', 'vec4', 'int', 'bool',
              'mat2', 'mat3', 'mat4')

MANIFEST_BLOCK_RE = re.compile(r'/\*(.*?)\*/', re.DOTALL)
UNIFORM_DECL_RE = re.compile(
    r'^[ \t]*uniform\b[^;]*;', re.MULTILINE | re.DOTALL)
PRECISION_RE = re.compile(r'#ifdef\s+GL_ES')


class ConversionError(Exception):
    """Fatal: source cannot be converted automatically."""


def _fmt_num(v) -> str:
    """GLSL-safe float literal."""
    f = float(v)
    s = f"{f:.6g}"
    if '.' not in s and 'e' not in s and 'E' not in s:
        s += '.0'
    return s


def _snake(name: str) -> str:
    s = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    s = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s)
    s = s.lower()
    s = re.sub(r'_+', '_', s).strip('_')
    if not s:
        s = 'param'
    if s[0].isdigit():
        s = '_' + s
    return s


def _find_manifest_params(source: str) -> dict:
    """Extract {name: {default, min, max}} from a /* {json} */ manifest block."""
    for block in MANIFEST_BLOCK_RE.findall(source):
        try:
            data = json.loads(block.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict) and 'parameters' in data:
            out = {}
            for p in data.get('parameters', []):
                if isinstance(p, dict) and p.get('name'):
                    out[p['name']] = {
                        'default': p.get('default', 0.5),
                        'min': p.get('min', 0.0),
                        'max': p.get('max', 1.0),
                    }
            return out
    return {}


def _strip_uniforms(source: str) -> str:
    """Remove uniform declarations (engine/manifest inject these)."""
    return UNIFORM_DECL_RE.sub('', source)


def _strip_consumed_manifests(source: str) -> str:
    """Remove comment blocks that parsed as JSON metadata (manifest/ISF INPUTS)."""
    def _keep(m):
        try:
            data = json.loads(m.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            return m.group(0)
        if isinstance(data, dict) and ('parameters' in data or 'INPUTS' in data):
            return ''
        return m.group(0)
    return MANIFEST_BLOCK_RE.sub(_keep, source)


def _ensure_precision(source: str) -> str:
    if PRECISION_RE.search(source):
        return source
    return ("#ifdef GL_ES\nprecision highp float;\n#endif\n\n" + source)



def _enclosing_block(src: str, pos: int):
    """Innermost {...} block containing pos. Returns (start, end) or None.
    When the block is a function body, the range is extended back over the
    signature so parameter declarations fall inside it."""
    depth = 0
    open_pos = None
    for i in range(pos, -1, -1):
        c = src[i]
        if c == '}':
            depth += 1
        elif c == '{':
            if depth == 0:
                open_pos = i
                break
            depth -= 1
    if open_pos is None:
        return None
    depth = 0
    close_pos = None
    for j in range(open_pos, len(src)):
        if src[j] == '{':
            depth += 1
        elif src[j] == '}':
            depth -= 1
            if depth == 0:
                close_pos = j + 1
                break
    if close_pos is None:
        return None
    k = open_pos - 1
    while k >= 0 and src[k] in ' \t\n\r':
        k -= 1
    if k >= 0 and src[k] == ')':
        pdepth = 0
        for i in range(k, -1, -1):
            if src[i] == ')':
                pdepth += 1
            elif src[i] == '(':
                pdepth -= 1
                if pdepth == 0:
                    m = i
                    while m >= 0 and (src[m].isalnum() or src[m] in '_'):
                        m -= 1
                    return (m + 1, close_pos)
    return (open_pos, close_pos)


def _replace_scoped(src: str, name: str, plain: str, wrapped: str) -> str:
    """Replace `name` refs: plain inside blocks that declare it (local scope,
    e.g. function parameters), wrapped everywhere else (global/knob reference)."""
    decl_re = re.compile(
        r'\b(?:' + '|'.join(GLSL_TYPES) + r')\s+' + re.escape(name) + r'\b')
    decls = list(decl_re.finditer(src))
    if not decls:
        return re.sub(r'\b' + re.escape(name) + r'\b', wrapped, src)

    plain_ranges = []
    for d in decls:
        blk = _enclosing_block(src, d.start())
        if blk:
            plain_ranges.append(blk)
        else:
            # global-scope declaration: entire file is plain for this name
            return re.sub(r'\b' + re.escape(name) + r'\b', plain, src)
    plain_ranges.sort()

    out = []
    idx = 0
    for m in re.finditer(r'\b' + re.escape(name) + r'\b', src):
        if m.start() < idx:
            continue
        in_plain = any(s <= m.start() < e for s, e in plain_ranges)
        out.append(src[idx:m.start()])
        out.append(plain if in_plain else wrapped)
        idx = m.end()
    out.append(src[idx:])


def _rewrite_output(source: str) -> str:
    """Replace the final fragColor/gl_FragColor = vec4(...) assignment with the
    VS-2 coloredPixels pattern. Paren-depth aware (handles nested commas)."""
    pat = re.compile(r'\b(fragColor|gl_FragColor)\s*=\s*vec4\s*\(')
    matches = list(pat.finditer(source))
    if not matches:
        return source
    m = matches[-1]
    open_idx = m.end() - 1
    depth = 0
    close_idx = None
    for i in range(open_idx, len(source)):
        if source[i] == '(':
            depth += 1
        elif source[i] == ')':
            depth -= 1
            if depth == 0:
                close_idx = i
                break
    if close_idx is None:
        return source
    inner = source[open_idx + 1:close_idx]
    args, cur, d = [], '', 0
    for ch in inner:
        if ch == '(':
            d += 1
        elif ch == ')':
            d -= 1
        if ch == ',' and d == 0:
            args.append(cur)
            cur = ''
        else:
            cur += ch
    args.append(cur)
    args = [a.strip() for a in args]
    if len(args) == 2:
        expr = args[0]
    elif len(args) == 4:
        expr = f"vec3({args[0]}, {args[1]}, {args[2]})"
    else:
        return source  # unexpected shape: leave alone rather than corrupt it
    end = close_idx + 1
    while end < len(source) and source[end] in ' \t':
        end += 1
    if end < len(source) and source[end] == ';':
        end += 1
    replacement = (
        "{\n"
        "    vec3 vs2Final = " + expr + ";\n"
        "    float coloredPixels = dot(clamp(vs2Final, 0.0, 1.0), vec3(1.0));\n"
        "    fragColor = vec4(vs2Final * color.rgb, alpha * coloredPixels);\n"
        "}")
    return source[:m.start()] + replacement + source[end:]


def _rename_shadowed_engine_vars(source: str) -> str:
    """Rename local declarations that shadow engine vars (color, alpha) so the
    generated output pattern reaches the engine globals."""
    for var in ('color', 'alpha'):
        decl_re = re.compile(
            r'\b(?:' + '|'.join(GLSL_TYPES) + r')\s+' + var + r'\s*=')
        if decl_re.search(source):
            source = re.sub(r'\b' + var + r'\b', 'src' + var.capitalize(),
                            source)
    return source


def _build_manifest(params: list, shader_name: str) -> str:
    """params: [{'name': ..., 'default': ...}] already sanitized and clamped."""
    manifest = {
        "author": "VS-2 Convert",
        "color": "#ffffff",
        "movement": True,
        "parameters": [
            {"default": round(float(p['default']), 4), "name": p['name']}
            for p in params[:MAX_PARAMS]
        ],
        "url": "",
        "uuid": str(_uuid.uuid4()),
    }
    return "/*\n" + json.dumps(manifest, indent=4) + "\n*/\n\n"



# ---------------------------------------------------------------------------
# format-specific body transforms
# ---------------------------------------------------------------------------

def _convert_shadertoy(source: str, report: dict) -> tuple:
    """Returns (body, manifest_params)."""
    raw_params = _find_manifest_params(source)
    if not raw_params:
        for m in re.finditer(
                r'uniform\s+(float|vec2|vec3|vec4)\s+(\w+)\s*;', source):
            raw_params[m.group(2)] = {'default': 0.5, 'min': 0.0, 'max': 1.0}

    body = _strip_uniforms(source)
    body = _strip_consumed_manifests(body)

    # formal-spec.md: `iChannel0..3` maps to `imageTex`. VS-2 binds the
    # layer's configured image there, and ignores a configured image for
    # shaders that do not sample it. So a single-channel read is an ordinary
    # image-input shader, not something to refuse.
    _channels = sorted(set(re.findall(r'\biChannel([0-3])\b', body)))
    _chan_meta = sorted(set(re.findall(r'\biChannel(?:Resolution|Time)\b', body)))
    if len(_channels) > 1:
        raise ConversionError(
            "reads %d distinct iChannels (iChannel%s): VS-2 exposes a single "
            "imageTex, so the channels cannot be preserved. Convert by hand "
            "via the vs2-shader-expert skill"
            % (len(_channels), ''.join(_channels)))
    if _channels:
        # SAFETY: a Shadertoy iChannel is a binding SLOT, not a known image.
        # Per Shadertoy's own model it may hold a texture, a feedback buffer,
        # a cubemap, a video source, keyboard data, or an audio input, and
        # "iChannel0 does not always mean the main texture" - it means whatever
        # the pass configuration attached to slot 0. The pass configuration
        # lives on the Shadertoy page, not in the .txt, so from source alone
        # we cannot know. Mapping an unknown slot onto VS-2's single imageTex
        # is therefore a GUESS, and a wrong guess produces a shader that
        # renders but is not the original.
        #
        # So: the conversion is allowed to proceed, because refusing every
        # channel shader would block the common and legitimate case of a
        # single user-supplied image, but the uncertainty is stated loudly and
        # the output is marked so it cannot be mistaken for a verified
        # conversion. See gates.py:shadertoy-channel-uncertainty.
        _slot = _channels[0]
        report['warnings'].append(
            "UNVERIFIED: iChannel%s is a Shadertoy binding slot, not a "
            "guaranteed image - it may be a feedback buffer, cubemap, video, "
            "or audio. Mapped to VS-2's imageTex, which binds the image set on "
            "the layer. Confirm against the Shadertoy page's pass settings "
            "that slot %s really is a single user image; if not, this "
            "conversion is wrong and needs doing by hand." % (_slot, _slot))
        if _chan_meta:
            report['warnings'].append(
                "iChannelResolution/iChannelTime referenced: these describe "
                "the bound slot and have no VS-2 equivalent. Any scaling or "
                "sync built on them is not preserved.")
        body = re.sub(r'\btexture\s*\(\s*iChannel[0-3]\b', 'texture(imageTex', body)
        body = re.sub(r'\biChannel[0-3]\b', 'imageTex', body)
        body = re.sub(r'\biChannelResolution\b', 'imageResolution', body)
        body = re.sub(r'\biChannelTime\b', 'time', body)
    body = re.sub(r'\biResolution\b', 'resolution', body)
    body = re.sub(r'\biTime\b', 'time', body)
    if re.search(r'\biMouse\b', body):
        body = re.sub(r'\biMouse\b', '(vec4(0.0))', body)
        report['warnings'].append(
            "iMouse replaced with static vec4(0.0) - source was click-driven")
    body = re.sub(r'\biDate\b', 'vec4(0.0)', body)

    body = _rename_shadowed_engine_vars(body)

    if re.search(r'\bmainImage\s*\(', body) and \
            not re.search(r'\bvoid\s+main\s*\(', body):
        body += ("\nvoid main(void)\n{\n"
                 "    mainImage(fragColor, gl_FragCoord.xy);\n}\n")
    elif not re.search(r'\bvoid\s+main\s*\(', body):
        raise ConversionError("no mainImage() or main() entry point found")

    body = _rewrite_output(body)
    body = _ensure_precision(body)

    params = []
    for name, info in raw_params.items():
        clean = _snake(name)
        while clean in ENGINE_VARS:
            clean += '_amt'
        default = min(1.0, max(0.0, float(info['default'])))
        if any(kw in clean for kw in SPEED_KEYWORDS):
            body = re.sub(r'\b' + re.escape(name) + r'\b',
                          _fmt_num(info['default']), body)
            report['baked'].append(
                f"{name} -> {_fmt_num(info['default'])} (speed baked per rule 4)")
            continue
        if default != float(info['default']):
            report['warnings'].append(
                f"param '{clean}': default {info['default']} clamped to "
                f"{default} (knob range 0..1)")
        if clean != name:
            body = re.sub(r'\b' + re.escape(name) + r'\b', clean, body)
        params.append({'name': clean, 'default': default})
    return body, params


def _convert_glslsandbox(source: str, report: dict) -> tuple:
    """uniform time/resolution/mouse style source."""
    body = _strip_uniforms(source)
    if re.search(r'\bsurfacePosition\b', body):
        body = re.sub(
            r'\bsurfacePosition\b',
            'vec2(texCoord.x, texCoord.y * resolution.y / resolution.x)',
            body)
    if re.search(r'\bmouse\b', body, re.IGNORECASE):
        body = re.sub(r'\bmouse\b', 'vec2(x, y)', body)
        report['warnings'].append("mouse -> vec2(x, y) pan parameters")
        pan_params = [{'name': 'x', 'default': 0.5},
                      {'name': 'y', 'default': 0.5}]
    else:
        pan_params = []
    body = _rename_shadowed_engine_vars(body)
    body = re.sub(r'\bgl_FragColor\b', 'fragColor', body)
    body = _rewrite_output(body)
    body = _ensure_precision(body)
    if not re.search(r'\bvoid\s+main\s*\(', body):
        raise ConversionError("no main() entry point found")
    return body, pan_params



def _convert_isf(source: str, report: dict) -> tuple:
    """Returns (body, manifest_params). Parses the ISF INPUTS block."""
    inputs = None
    m = MANIFEST_BLOCK_RE.search(source)
    if m:
        try:
            header = json.loads(m.group(1).strip())
            inputs = header.get('INPUTS', [])
        except (json.JSONDecodeError, ValueError):
            inputs = None
    if not inputs:
        raise ConversionError("ISF INPUTS block not found or not parseable")

    # --- hard gates before any transformation ---
    header_all = {}
    if m:
        try:
            header_all = json.loads(m.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            header_all = {}
    if any(inp.get('TARGET') for inp in inputs) or \
            any(p.get('TARGET') for p in header_all.get('PASSES', [])):
        raise ConversionError(
            "feedback buffer (ISF TARGET input/pass): VS-2 is single-pass "
            "with no feedback; convert by hand via the vs2-shader-expert skill")
    # BUNDLED image resources: a top-level "IMPORTED" dict naming files the
    # composition ships with, e.g. {"blendImage": {"PATH": "Hexagon.tiff"}}.
    # Per docs.isf.video/ref_json and the canonical Test-ImportedImage.fs.
    # These are pixel data travelling with the .fs; VS-2 has no equivalent,
    # so refuse rather than silently drop the blend.
    _imported = header_all.get('IMPORTED') if isinstance(header_all, dict) else None
    if _imported:
        _names = ', '.join(sorted(_imported)[:4])
        raise ConversionError(
            f"bundled image resources in IMPORTED ({_names}): these are image "
            f"files shipped with the .fs, and VS-2 has nowhere to put them. "
            f"Supply the image in VS-2 instead, or convert by hand via the "
            f"vs2-shader-expert skill")

    # Non-ISF GLSL texture calls. Genuinely unrepresentable: these imply a
    # multi-pass merge, which VS-2 does not have.
    if re.search(r'\btexture2D\s*\(|\btex2D\s*\(', source):
        raise ConversionError(
            "bundled texture input (texture2D/tex2D): hard gate; convert by "
            "hand via the vs2-shader-expert skill")

    body = source[m.end():] if m else source
    body = _strip_uniforms(body)

    # --- classify inputs: knob vs baked-to-default constant ---
    manifest = []
    baked = {}
    used_names = set()
    seen_image = False
    for inp in inputs:
        name = inp.get('NAME', '')
        itype = inp.get('TYPE', 'float')
        default = inp.get('DEFAULT', 0.0)
        if itype == 'image':
            # An ISF `TYPE: image` input is not an image the converter has to
            # supply. It is the shader declaring that it REQUIRES an image -
            # the same role as a Shadertoy iChannel0 read. VS-2 satisfies it
            # from the layer's configured image via `imageTex`, and ignores a
            # configured image for shaders that do not sample one.
            #
            # So: do not turn it into a manifest parameter (formal-spec.md:
            # "No image parameter is declared in the manifest. The image is
            # implicit."), and do not reject it. The body samples whatever
            # sampler the source used; rename to imageTex so it resolves
            # against the engine's binding.
            report['warnings'].append(
                f"input '{name}' is an image: the converted shader samples "
                f"imageTex, which VS-2 binds from the image configured on the "
                f"layer. It needs an image set in VS-2 to show anything.")
            seen_image = True
            continue
        clean = _snake(name)
        while clean in used_names or clean in ENGINE_VARS:
            clean += '_amt'
        if itype != 'float':
            if isinstance(default, (list, tuple)) and len(default) == 4:
                literal = ("vec4(" + ", ".join(_fmt_num(v) for v in default)
                           + ")")
            elif isinstance(default, (list, tuple)) and len(default) == 2:
                literal = f"vec2({default[0]}, {default[1]})"
            elif default is True or default is False:
                literal = 'true' if default else 'false'
            elif itype == 'long':
                values = inp.get('VALUES', [])
                literal = (str(values.index(default)) if default in values
                           else '0')
            elif isinstance(default, str):
                literal = '0'
                report['warnings'].append(
                    f"input '{name}' (type {itype}, default '{default}') "
                    f"baked to 0 - not expressible in GLSL")
            else:
                literal = _fmt_num(default)
            baked[name] = literal
            report['warnings'].append(
                f"input '{name}' (type {itype}) baked to {literal}")
            continue
        if any(kw in clean for kw in SPEED_KEYWORDS):
            baked[name] = _fmt_num(default)
            report['baked'].append(
                f"{name} -> {_fmt_num(default)} (speed baked per rule 4)")
            continue
        if len(manifest) >= MAX_PARAMS:
            baked[name] = _fmt_num(default)
            report['baked'].append(
                f"{name} -> {_fmt_num(default)} (over 7-param budget)")
            continue
        lo = float(inp.get('MIN', 0.0))
        hi = float(inp.get('MAX', 1.0))
        dflt = float(default)
        if hi <= lo:
            lo, hi = 0.0, 1.0
        knob = min(1.0, max(0.0, (dflt - lo) / (hi - lo)))
        manifest.append({'name': clean, 'default': knob,
                         '_lo': lo, '_hi': hi, '_orig': name})
        used_names.add(clean)
        baked[name] = None  # marker: kept as knob

    # An ISF that declares an image input almost certainly samples a sampler.
    # ISF spells the sampler as `sampler_main`/`sampler_image`/`sampler`, or
    # uses an image uniform directly; all of them become the engine's
    # imageTex, which VS-2 binds from the layer's configured image.
    if seen_image:
        # ISF image accessors (docs.isf.video/ref_json): IMG_NORM_PIXEL(name, uv)
        # and IMG_THIS_PIXEL(name) read the image named in INPUTS. VS-2 exposes a
        # single sampler, imageTex, so every one of these becomes a texture()
        # call against it. Leaving them in would emit a shader that cannot
        # compile - these are ISF host functions, not GLSL.
        body = re.sub(r'\bIMG_NORM_PIXEL\s*\(\s*\w+\s*,', 'texture(imageTex,', body)
        body = re.sub(r'\bIMG_THIS_PIXEL\s*\(\s*\w+\s*\)', 'texture(imageTex, texCoord)', body)
        body = re.sub(r'\bIMG_PIXEL\s*\(\s*\w+\s*,', 'texture(imageTex,', body)
        body = re.sub(r'\btexture2D\s*\(\s*\w+\s*,', 'texture(imageTex,', body)
        body = re.sub(r'\btexture\s*\(\s*\w+\s*,', 'texture(imageTex,', body)

    # --- ISF builtins ---
    if re.search(r'\bPASSINDEX\b', body):
        if re.search(r'\bbuffer[A-D]\b|\bBuffer[A-D]\b|\bfeedback\b', body):
            raise ConversionError(
                "multi-pass feedback (buffer/feedback refs): VS-2 is "
                "single-pass; convert by hand via the vs2-shader-expert skill")
        # project onto the FINAL pass (the composite that writes fragColor);
        # count comes from the header PASSES array, default 2
        passes = 1
        passes = max(1, len(header_all.get('PASSES', [])) or 1)
        final_pass = str(passes - 1)
        body = re.sub(r'\bPASSINDEX\b', final_pass, body)
        report['warnings'].append(
            f"PASSINDEX -> {final_pass} (single-pass projection onto the "
            f"final pass of {passes})")
    if re.search(r'\bMOUSE\b', body):
        mouse_is_vec4 = bool(re.search(
            r'uniform\s+vec4\s+MOUSE\b', source))
        mouse_expr = ('vec4(x, y, 0.0, 0.0)' if mouse_is_vec4
                      else 'vec2(x, y)')
        if len(manifest) + 2 <= MAX_PARAMS and 'x' not in used_names \
                and 'y' not in used_names:
            body = re.sub(r'\bMOUSE\b', mouse_expr, body)
            manifest.append({'name': 'x', 'default': 0.5})
            manifest.append({'name': 'y', 'default': 0.5})
            used_names.update({'x', 'y'})
            report['warnings'].append(
                f"MOUSE -> {mouse_expr} pan parameters")
        else:
            body = re.sub(r'\bMOUSE\b',
                          'vec4(0.5, 0.5, 0.0, 0.0)' if mouse_is_vec4
                          else 'vec2(0.5)', body)
            report['warnings'].append("MOUSE -> static centre")
    body = re.sub(r'\bRENDERSIZE\b', 'resolution', body)
    body = re.sub(r'\bisf_FragNormCoord\b', 'texCoord', body)
    body = re.sub(r'\bgl_FragColor\b', 'fragColor', body)

    # --- input substitution: baked -> literal everywhere; knobs -> scoped wrap
    # NOTE: runs BEFORE the uppercase builtin TIME rename so an ISF input
    # literally named `time` cannot collide with the engine clock.
    for orig, literal in baked.items():
        if literal is not None:
            body = _replace_scoped(body, orig, literal, literal)
    for p in manifest:
        orig = p.get('_orig')
        if orig is None:  # synthetic knob (x/y from MOUSE)
            continue
        wrap = (f"({p['_lo']:.6g} + {p['name']} * "
                f"({p['_hi']:.6g} - {p['_lo']:.6g}))")
        body = _replace_scoped(body, orig, p['name'], wrap)

    body = re.sub(r'\bTIME\b', 'time', body)

    body = _rename_shadowed_engine_vars(body)
    body = _rewrite_output(body)
    body = _ensure_precision(body)
    for p in manifest:
        p.pop('_lo', None)
        p.pop('_hi', None)
        p.pop('_orig', None)
    return body, manifest



# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def _forced_fallback(source: str, report: dict) -> tuple:
    """Best-effort body when --force overrides a hard gate.

    Deliberately minimal and honest: strip uniforms, do the mechanical
    builtin renames, and hand the result over marked as forced. It does NOT
    try to be clever, because a forced conversion is a starting point for a
    human, not a working shader.
    """
    body = _strip_uniforms(source)
    body = re.sub(r'\biResolution\b', 'resolution', body)
    body = re.sub(r'\biTime\b', 'time', body)
    body = re.sub(r'\biChannel[0-3]\b', 'imageTex', body)
    body = re.sub(r'\bgl_FragColor\b', 'fragColor', body)
    body = re.sub(r'\btexture2D\s*\(', 'texture(', body)
    return body, []


def convert(source: str, fmt: str, shader_name: str,
             force: bool = False) -> dict:
    """Convert source shader to a complete VS-2 .frag string.

    Returns {'frag': str, 'warnings': [..], 'baked': [..], 'params': [..]}.
    Raises ConversionError for sources that cannot be converted safely.
    """
    report = {'warnings': [], 'baked': [], 'params': []}

    # Scan code with comments blanked: a comment naming frac()/lerp() is
    # documentation of the original, not unconverted HLSL.
    if re.search(r'\b(frac|lerp|tex2D|texture2D)\s*\(', code_only(source)):
        raise ConversionError(
            "HLSL/bundled-texture source (MilkDrop): multi-pass merge is not "
            "automated; convert by hand via the vs2-shader-expert skill")

    fmt_map = {
        'Shadertoy': _convert_shadertoy,
        'ISF': _convert_isf,
        'GLSLSandbox': _convert_glslsandbox,
        'PlainGLSL': _convert_shadertoy,
    }
    if fmt not in fmt_map:
        raise ConversionError(f"unsupported format: {fmt}")

    try:
        body, params = fmt_map[fmt](source, report)
    except ConversionError:
        # --force means "convert anyway", so a capability gate that the user
        # has explicitly overridden must not abort the run. The result is
        # reported as such and is very likely broken; the render gate is the
        # only real check.
        if not force:
            raise
        report['warnings'].append(
            "FORCED past a hard conversion gate: the output is very likely "
            "broken. Treat it as a starting point for a manual conversion, "
            "not as a working shader.")
        body, params = _forced_fallback(source, report)
    manifest = _build_manifest(params, shader_name)
    report['params'] = params
    report['frag'] = manifest + body
    return report
