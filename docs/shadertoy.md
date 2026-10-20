# Shadertoy sources

A Shadertoy `.txt` paste is the **body only**. Everything that makes the
Shadertoy shader what it is — how many passes there are, which one writes the
screen, and what each `iChannel` is bound to — lives on the Shadertoy page,
not in the file.

`vs2convert` therefore converts the body, and tells you when the result rests
on a guess. You can remove the guess by supplying the channel data.

## Save two files

```
my-shader.shadertoy      # the body: mainImage(), iTime, iChannel0, ...
my-shader.stmeta.json    # what the channel slots are bound to
```

The sidecar is found automatically when it sits beside the source and matches
`<stem>.stmeta.json`. As a fallback, if the directory contains exactly one
`*.stmeta.json`, that file is used — handy when you keep the body and its
metadata under different names. If two or more sidecars are present and none
matches the stem, none is used.

Every path below is equivalent, in this precedence order:

1. `--meta FILE`
2. the `<stem>.stmeta.json` sidecar (or the lone `*.stmeta.json`)
3. an inline comment block at the top of the body

## The sidecar

The only key that changes the safety verdict is `channels`:

```json
{
  "channels": {
    "iChannel0": { "sampler": "texture", "source": "a photo I supplied" }
  },
  "passes": 1,
  "final_pass": 0
}
```

`sampler` is the important field. `source` is free text and only appears in
the confirmation message.

The inline form is the same JSON at the very top of the `.shadertoy` file:

```
/*{
  "channels": {"iChannel0": {"sampler": "texture"}}
}*/
void mainImage(out vec4 fragColor, in vec2 fragCoord) { ... }
```

`passes`, `final_pass` and `uniforms` are accepted for completeness but are
currently ignored: the converter projects the body onto its final pass from
the source itself, and never needs the other two.

## What VS-2 can represent

VS-2 binds **one** image, exposed as `imageTex`. A Shadertoy channel slot can
hold many things, and only the shader page says which.

| `sampler` in the sidecar | Outcome |
|---|---|
| `texture`, `image`, `video`, `2d` | Converts; the channel is marked **CONFIRMED** |
| no sidecar, or the channel is absent from it | Converts, marked **UNVERIFIED**; `--strict` exits 5 |
| declared with no `sampler` | Converts, marked **UNVERIFIED** |
| `buffer`, `cube`, `cubemap`, `audio`, `keyboard` | **Declined**: `shadertoy-unrepresentable-channel` |

Two rules hold regardless of the sidecar:

- A body that reads **two or more distinct** channels is declined with
  `shadertoy-multi-channel`, because both would have to be collapsed onto one
  `imageTex`.
- A body that reads a single channel converts, even with no metadata; it is
  simply flagged as unverified.

## Scope

Metadata is only consulted for Shadertoy-style sources. ISF and GLSLSandbox
carry their own declarations (or none), so a sidecar next to one of those is
ignored.

## Worked example

```
mkdir -p shaders
# paste the body from the Shadertoy page
$EDITOR shaders/gradient.shadertoy
cat > shaders/gradient.stmeta.json <<'JSON'
{
  "channels": {"iChannel0": {"sampler": "texture", "source": "u.jpg"}}
}
JSON
vs2convert shaders/gradient.shadertoy
```

That converts with a confirmed channel binding. Without the sidecar the same
command still converts, but prints an `unverified:` block, and `--strict`
would exit 5.
