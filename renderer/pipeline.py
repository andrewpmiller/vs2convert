"""
Headless render verification for VS-2 shaders.

Requires moderngl + numpy + Pillow. Optional: the CLI degrades to a clear
"skipped" message when they are absent, rather than reporting a pass.

What this actually checks
-------------------------
The structural validator checks *shape*: manifest, API names, output
assignments. A shader can satisfy every structural rule and still render
nothing - that exact failure is in the skill's lessons log (2026-09-18). So
this renders actual frames and looks at the pixels.

It renders 3 frames at increasing `time` and reports:

  luminance/alpha statistics per frame, and flags:
    empty  nothing was drawn (transparent or black everywhere)
    flat   something was drawn, but one colour in every pixel
    dark   visible but very dim at every sample
  motion  mean absolute luminance difference between frames. A shader can be
          bright and high-contrast and still be broken if every frame is
          identical - the body ignores `time`. No per-frame statistic can
          see that; only comparing frames can.

A previous version of this module could not fail: it tested `rgba.size > 0`,
which is always true for a successfully created texture, so `--verify`
reported success without rendering anything meaningful. It also declared
`#version 300 es` while compiling a VS-2 body that uses `fragColor` and
`#ifdef GL_ES`. Both are fixed.

Exit semantics: `passed` is False if anything is flagged, if motion is below
the floor, or if the context/shader could not be created at all. There is no
code path that returns passed=True without having rendered and measured.
"""
import re

# Mean absolute luminance difference (0-255) below which a multi-frame
# render counts as frozen. Matches shader-pipeline/pipeline.py's floor.
MIN_MOTION = 1.0
WIDTH, HEIGHT = 256, 256
# Motion probe. A single pair of frames 1s apart is the wrong instrument for
# a shader whose animation is discrete rather than smooth: a per-frame random
# glitch (real Shadertoy case: the seed is quantised by mytrunc() to 6 levels)
# renders identically at t=0,1,2 and genuinely animates between them. Sampling
# 3 far-apart frames called that "frozen" and rejected a working shader.
# So: WIDE_SAMPLES catch a body that truly ignores time, while DENSE_FRAMES at
# frame rate catch discrete per-frame animation the wide probe cannot see.
TIME_SAMPLES = (0.0, 1.0, 2.0)
DENSE_FRAMES = 60
DENSE_SPAN = 1.0

# VS-2 emits a GLES-flavoured body (fragColor, #ifdef GL_ES) but the host
# context is desktop GL, so compile as 330 and let the engine's helpers in.
VERTEX_SHADER = """#version 330
in vec2 in_position;
out vec2 texCoord;
void main() {
    texCoord = in_position * 0.5 + 0.5;
    gl_Position = vec4(in_position, 0.0, 1.0);
}
"""


def _manifest_params(source: str) -> list:
    """Parameter names from the leading JSON manifest, if present."""
    import json
    m = re.search(r'/\*(.*?)\*/', source, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(1).strip())
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    return [p.get('name', '') for p in data.get('parameters', []) if p.get('name')]


def _adapt(source: str) -> str:
    """Adapt a VS-2 .frag body to a desktop GLSL 330 compile.

    VS-2's engine injects precision guards, `fragColor`, `texCoord`, the
    colour/alpha/resolution uniforms, and one uniform per manifest parameter.
    On the host we declare them ourselves, or the body will not compile.
    Mirrors shader-pipeline/renderer.py:_prepend_uniforms.
    """
    lines = [
        '#version 330',
        '',
        '// Engine-provided uniforms (injected by VS-2, declared here for headless rendering)',
        'uniform vec2 resolution;',
        'uniform float time;',
        'uniform vec3 color;',
        'uniform float alpha;',
        'in vec2 texCoord;',
        'out vec4 fragColor;',
    ]
    # Image-input shaders need the engine's sampler and its resolution.
    # VS-2 injects these; declare them only when the body uses them so we do
    # not trip the "sampler not bound" path on procedural shaders.
    if 'imageTex' in source:
        lines += ['uniform sampler2D imageTex;',
                  'uniform vec2 imageResolution;']
    lines += ['', '// Custom parameters from manifest']
    for pname in _manifest_params(source):
        lines.append('uniform float %s;' % pname)
    lines += ['', '// Original shader source follows', '']

    # Strip a UTF-8 BOM if present: it lands on the `/*` of the manifest
    # comment and the GLSL compiler rejects it with a bare syntax error.
    # (shader-pipeline avoids this by reading with encoding='utf-8-sig'.)
    source = source.lstrip('\ufeff')

    # Strip the host-dictated #version and the GLES precision block.
    body = re.sub(r'^\s*#version.*$', '', source, flags=re.MULTILINE)
    body = re.sub(
        r'#ifdef\s+GL_ES\s*\n\s*precision\s+highp\s+float\s*;\s*\n\s*#endif\s*\n?',
        '', body)
    return '\n'.join(lines) + body


def _manifest(source: str) -> dict:
    import json
    m = re.search(r'/\*(.*?)\*/', source, re.DOTALL)
    if not m:
        return {}
    try:
        data = json.loads(m.group(1).strip())
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _param_values(source: str) -> dict:
    """Uniform values to send for the manifest's parameters.

    VS-2 exposes each parameter as a normalized knob positioned at `default`
    and maps that position onto the parameter's declared [min, max]. So when
    a manifest declares min/max, `default` is a POSITION, not a magnitude:
    uniform = min + default * (max - min). Sending the raw default instead
    makes fly_through's `light` (default 1, max 40) run a single raymarch
    iteration and render pure black.

    A no-op for our own conversions, which never emit min/max.
    """
    data = _manifest(source)
    out = {}
    for p in data.get('parameters', []):
        name = p.get('name')
        if not name:
            continue
        default = float(p.get('default', 0.5))
        if 'max' in p:
            lo = float(p.get('min', 0.0))
            hi = float(p['max'])
            out[name] = lo + default * (hi - lo) if hi != lo else lo
        else:
            out[name] = default
    return out


def _declares_movement(source: str) -> bool:
    """Manifest `movement` flag. False means the shader is static by design
    and must not be motion-checked (e.g. hiding_squares)."""
    return _manifest(source).get('movement', True) is not False


def _load_default_texture(ctx):
    """Bind a default image for image-input shaders.

    VS-2's gallery renders image-input shaders against a default image, so
    the headless gate does the same: it is what makes a preview comparable
    to the app rather than a black frame. Returns None if the asset is
    missing, in which case the sampler reads the driver's zero texture and
    the shader takes whatever fallback branch it has.
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    import os
    for rel in ('shader-pipeline/assets/default_texture.png',
                '../shader-pipeline/assets/default_texture.png'):
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))), rel)
        if os.path.exists(path):
            img = Image.open(path).convert('RGBA')
            return ctx.texture(img.size, 4, img.tobytes())
    return None


def _stats(arr):
    """(mean, std, alpha) over an HxWx4 float array, 0-255 luminance."""
    lum = arr[..., :3].mean(axis=2)
    return float(lum.mean()), float(lum.std()), float(arr[..., 3].mean())


def run_verification(shader_content: str, shader_name: str) -> dict:
    """Render `shader_content` headlessly and judge the pixels.

    Returns {'passed': bool, 'errors': [...], 'metrics': {...}}.
    `passed` is True only if frames were rendered AND measured AND looked
    like a visible, animating shader.
    """
    try:
        import moderngl
        import numpy as np
    except ImportError as e:
        return {
            "passed": False,
            "errors": ["render verification unavailable: %s "
                       "(rendering needs moderngl+numpy+Pillow; "
                       "use shader-pipeline/.venv)" % e],
            "metrics": {},
        }

    errors = []
    metrics = {}

    try:
        ctx = moderngl.create_standalone_context()
    except Exception as e:
        return {"passed": False,
                "errors": ["could not create a GL context: %s" % e],
                "metrics": {}}

    try:
        try:
            # Pass sources as strings: compiling the vertex shader to its own
            # object first and then linking it again loses its `out` varying.
            prog = ctx.program(vertex_shader=VERTEX_SHADER,
                               fragment_shader=_adapt(shader_content))
        except Exception as e:
            return {"passed": False,
                    "errors": ["shader failed to compile: %s" % e],
                    "metrics": {}}

        # Fullscreen triangle in clip space (covers the viewport with 3 verts).
        tri = ctx.buffer(np.array(
            [-1.0, -1.0, 3.0, -1.0, -1.0, 3.0], dtype='f4').tobytes())
        vao = ctx.vertex_array(prog, [(tri, '2f', 'in_position')])

        texture = ctx.texture((WIDTH, HEIGHT), 4, dtype='f1')
        fbo = ctx.framebuffer([texture])
        texture.filter = (moderngl.NEAREST, moderngl.NEAREST)

        # Image-input shaders sample `imageTex`. VS-2 binds the layer's
        # configured image to that sampler, and the gallery previews these
        # shaders against a default image, so binding one here mirrors the
        # engine. VS-2 reports imageResolution = (0,0) when no image is set,
        # which is how a shader tells "procedural fallback" from "has image";
        # we bind a real image, so we report its true size.
        img_tex = None
        if 'imageTex' in prog:
            img_tex = _load_default_texture(ctx)
            if img_tex is not None:
                img_tex.use(0)
                prog['imageTex'].value = 0
                if 'imageResolution' in prog:
                    prog['imageResolution'].value = tuple(
                        float(v) for v in img_tex.size)

        param_defaults = _param_values(shader_content)
        animated = _declares_movement(shader_content)
        metrics['params'] = len(param_defaults)
        metrics['movement'] = animated

        frames = []
        for t in list(TIME_SAMPLES) + [i * DENSE_SPAN / DENSE_FRAMES
                                       for i in range(DENSE_FRAMES)]:
            fbo.use()
            fbo.clear(0.0, 0.0, 0.0, 0.0)
            if 'resolution' in prog:
                prog['resolution'].value = (WIDTH, HEIGHT)
            if 'color' in prog:
                prog['color'].value = (1.0, 1.0, 1.0)
            if 'time' in prog:
                prog['time'].value = t
            if 'alpha' in prog:
                prog['alpha'].value = 1.0
            # Manifest parameters MUST be set: VS-2 initialises each knob to
            # its manifest default. Leaving them at 0 renders many shaders
            # black (e.g. acid_jelly scales its output by `level`).
            for pname, pval in param_defaults.items():
                if pname in prog:
                    prog[pname].value = pval
            vao.render(moderngl.TRIANGLES)
            buf = fbo.read(components=4, dtype='f1')
            arr = np.frombuffer(buf, dtype=np.uint8).reshape(
                HEIGHT, WIDTH, 4).astype(np.float32)
            frames.append(arr)

        # The wide probe uses only the first len(TIME_SAMPLES) frames.
        wide = frames[:len(TIME_SAMPLES)]
        dense = frames[len(TIME_SAMPLES):]
        means, stds, alphas = zip(*[_stats(f) for f in wide])
        mean, std, alpha = max(means), max(stds), max(alphas)
        def _delta(imgs):
            if len(imgs) < 2:
                return 0.0
            lums = [f[..., :3].mean(axis=2) for f in imgs]
            return max(float(np.abs(lums[i] - lums[j]).mean())
                       for i in range(len(lums))
                       for j in range(i + 1, len(lums)))

        wide_motion = _delta(wide)
        dense_motion = _delta(dense)
        distinct = len({f.tobytes() for f in dense})
        # A shader animates if EITHER probe sees change: smooth shaders show it
        # across wide samples, discrete per-frame ones within the dense run.
        motion = max(wide_motion, dense_motion)

        metrics = {
            "resolution": (WIDTH, HEIGHT),
            "frames": len(frames),
            "mean": round(mean, 2),
            "std": round(std, 2),
            "alpha": round(alpha, 2),
            "motion": round(motion, 2),
            "wide_motion": round(wide_motion, 2),
            "dense_motion": round(dense_motion, 2),
            "distinct_dense_frames": distinct,
        }

        if mean < 1.0 and alpha < 1.0:
            errors.append("empty: nothing was drawn (mean=%.2f alpha=%.2f)"
                          % (mean, alpha))
        elif std < 1.0:
            errors.append("flat: one colour in every pixel (std=%.2f)" % std)
        elif mean < 3.0:
            errors.append("dark: visible but very dim (mean=%.2f)" % mean)
        # Only motion-check shaders that declare movement. A static shader
        # rendering identical frames is correct, not broken.
        #
        # Two thresholds, because the two probes measure different things.
        # A smooth shader drifts in mean luminance between frames 1s apart, so
        # the wide probe keeps the original 1.0 floor. A discrete per-frame
        # shader barely moves mean luminance but produces a different image
        # every frame, so for the dense probe the honest test is whether the
        # frames are byte-identical - and the floor scales down to match, since
        # 60 per-frame deltas each contribute 1/60 of the wide average.
        if animated:
            if wide_motion < MIN_MOTION and distinct < DENSE_FRAMES:
                errors.append(
                    "frozen: neither the wide probe (motion=%.2f < %.1f over "
                    "%ds) nor the dense probe (%d/%d distinct frames) sees the "
                    "body change - it appears to ignore `time`"
                    % (wide_motion, MIN_MOTION, len(wide), distinct,
                       DENSE_FRAMES))

        metrics['gl_version'] = ctx.info.get('GL_VERSION')
        return {"passed": not errors, "errors": errors, "metrics": metrics}

    except Exception as e:
        return {"passed": False,
                "errors": ["render verification failed: %s" % e],
                "metrics": metrics}
    finally:
        try:
            ctx.release()
        except Exception:
            pass
