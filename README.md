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
  answers "does this render visibly and animate", not "does this match VS-2".
- `--dry-run` prints the result without writing a file.
- `--force` converts past a soft capability gate and warns loudly that the
  output is very likely broken.
- `--meta FILE` supplies Shadertoy pass/channel metadata.
- `--strict` exits 5 if the conversion could not be verified.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Converted (and validated, and verified if requested) |
| 1 | Declined (includes validation and render failure) |
| 2 | Renderer unavailable, or bad command-line usage |
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
