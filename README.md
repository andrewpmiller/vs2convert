# vs2convert

Convert Shadertoy, ISF and GLSLSandbox shaders into VS-2 `.frag` files.

`vs2convert` is a Python library and command-line tool. It reads a shader
source file (or stdin), detects its format, converts the body and manifest,
and validates the result. It does not download anything and does not need a
GPU unless you ask for render verification.

**Not affiliated with or endorsed by Imaginando.** VS-2 is a product of
Imaginando; this project is an independent converter for its shader format.

## Install

Python 3.10 or newer is required. Development and testing happen on 3.14.

```
python3 -m venv .venv
.venv/bin/pip install .
```

Optional render verification (headless OpenGL; needs a working GL driver):

```
.venv/bin/pip install ".[render]"
```

From a checkout, `pip install -e ".[dev,render]"` gives you the tests too.

## Usage

```
vs2convert SOURCE [OUTPUT] [--verify] [--dry-run] [--force]
                  [--meta FILE] [--strict] [-v]
```

- `SOURCE` is a shader file, or `-` to read stdin. With stdin, `OUTPUT` is
  required.
- `OUTPUT` defaults to `<source stem>.frag` beside the input.
- `--verify` renders the converted shader headlessly and prints metrics. It
  answers "does this render visibly and animate", not "does this match
  VS-2": a passing render is evidence the file compiles and draws, not proof
  that it is the same image as the original.
- `--dry-run` prints the result without writing a file.
- `--force` converts past a capability gate and warns loudly that the output
  is very likely broken. HLSL/MilkDrop cannot be forced.
- `--meta FILE` supplies Shadertoy pass/channel metadata. It is also read
  from `<stem>.stmeta.json` beside the source (or a lone `*.stmeta.json` in
  the same directory) or from an inline `/*{...}*/` block at the top of the
  source. Precedence: `--meta`, then sidecar, then inline. Only
  Shadertoy-style sources consult it. See `docs/shadertoy.md`.
- `--strict` exits 5 if the conversion could not be verified.

`vs2convert - out.frag < shader.glsl` converts stdin.

Every source is structurally validated before it is written: a file that is
produced is a file that passed the validator. `--validate` no longer exists;
validation is not optional. The trade-off is deliberate - a shader that
would previously have been written with validation errors now declines.

A decline prints `stage/gate: reason`, what to do about it, and a link that
opens a GitHub issue with the source pre-filled.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Converted, validated, and rendered if requested |
| 1 | Declined (includes validation failure and a failed render check) |
| 2 | Render verification unavailable, or bad command-line usage |
| 3 | Source is not suitable for conversion |
| 4 | Output failure (write error, missing directory, would overwrite input) |
| 5 | `--strict` and the conversion was unverified |

## License

MIT. See `LICENSE`. Contributions are accepted under MIT with a DCO
sign-off; see `CONTRIBUTING.md`.

## History

The converter was extracted from a private repository with `git subtree
split`, so commits before the import refer to the old layout
(`core/`, `renderer/`, `validators/`, `vs2_convert.py` at the repository
root). The paths in those commits are historical; the current layout is the
one documented above.
