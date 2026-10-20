# vs2convert

Turn a Shadertoy, ISF, or GLSLSandbox shader into a `.frag` file that VS-2
can load.

One command reads the source, works out what kind of shader it is, converts
the body and parameters, checks the result against VS-2's rules, and writes
the file. It doesn't download anything, and it doesn't need a GPU unless you
ask it to render the result.

This is an independent project. It is not affiliated with or endorsed by
Imaginando, the maker of VS-2.

## What you need

- Python 3.10 or newer. Development and testing use 3.14.
- A copy of this repository. The package is not on PyPI, so install from a
  checkout.
- VS-2, to load the finished `.frag`.
- For `--verify`: `moderngl`, NumPy, Pillow, and a working OpenGL driver.

## Install

Run these from the repository checkout.

### uv

Install it as a command you can run from anywhere:

```sh
uv tool install .
```

If `vs2convert` is not found afterwards, run `uv tool update-shell` or add
uv's tool directory to your `PATH`. uv prints the location when it installs.

Or run it once without installing:

```sh
uvx --from . vs2convert shader.fs
```

If you keep a project virtualenv instead, `uv venv` followed by
`uv pip install .` works too.

### pipx

```sh
pipx install .
```

### pip

```sh
python3 -m venv .venv
.venv/bin/pip install .
```

Then call `.venv/bin/vs2convert`, or activate the venv and call
`vs2convert`.

### Render verification

`--verify` needs three extra packages. Add the `render` extra to whichever
install you used:

```sh
uv tool install ".[render]"
# or
pipx install ".[render]"
# or
.venv/bin/pip install ".[render]"
```

## Convert a shader

```sh
vs2convert shader.fs
```

That writes `shader.frag` next to the input. You can name the output
yourself:

```sh
vs2convert shader.fs path/to/output.frag
```

Other common runs:

```sh
vs2convert shader.fs --dry-run        # print the result, write nothing
vs2convert shader.fs --verify         # convert, then render and measure it
cat shader.glsl | vs2convert - out.frag
```

With stdin you must give the output path. There is nowhere else to put it.

A successful run looks like this:

```
✅ julia.fs  ->  julia.frag
  ISF · 2 params · validated
```

`-v` shows the steps it took and the full text of every note.

## What it can and cannot convert

It reads ISF, Shadertoy, and GLSLSandbox sources. HLSL and MilkDrop shaders
are detected and declined. `--force` cannot override that one.

VS-2 exposes a single `imageTex`, so a shader may use one image input. Two
distinct image inputs, or two different `iChannel`s, are declined rather than
silently collapsed onto the first.

VS-2 takes at most 7 parameters. If the source has more, the extras are
baked into the shader at their default values and the output tells you which
ones. It does not quietly drop them.

Every file that gets written has passed the validator. There is no
`--validate` flag because validation is not optional: if the result is
invalid, the run declines instead of writing a broken file.

## Reading the output

Declines print the stage and gate that stopped the conversion, what to do
about it, and a link that opens a GitHub issue with the command and source
already in the body:

```
❌ broken.fs - declined
  convert/missing-entry-point: no mainImage() or main() entry point found
    The source has no mainImage()/main(). Check that it is a complete shader.
  -> fix the source, or convert it by hand
  exit 1 . nothing written
```

A note that starts with `UNVERIFIED` is different from a refusal. The
conversion was written, but the tool could not prove one part of the mapping
matches the original. One case is a Shadertoy `iChannel`: VS-2's `imageTex`
is not a drop-in replacement for every channel type, so the output is marked
and you should look at it in VS-2 before trusting it. The file is still
written, and the run exits 0 unless you passed `--strict`.

## Options

**`--verify`** renders the converted file headlessly and prints metrics. It
answers "does this compile and draw something that moves", not "does this
match VS-2". A passing render is evidence the file works, not proof it looks
like the original. Needs the render extra and a GL driver.

**`--dry-run`** prints the converted shader instead of writing it. Useful
when you want to read the manifest or diff two attempts.

**`--force`** converts past a capability gate. It warns that the output is
very likely broken, because it usually is. HLSL and MilkDrop cannot be
forced.

**`--strict`** exits 5 if the conversion is only `UNVERIFIED`. Use it in
scripts that should not accept a maybe.

**`--meta FILE`** supplies Shadertoy pass and channel metadata. The tool also
looks for `<stem>.stmeta.json` beside the source, a lone `*.stmeta.json` in
the same directory, or an inline `/*{...}*/` JSON block at the top of the
source. Precedence is `--meta`, then sidecar, then inline. Only
Shadertoy-style sources consult it. Details are in `docs/shadertoy.md`.

**`-v`, `--verbose`** shows every step and the full text of each note.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Converted, validated, and rendered if you asked for it |
| 1 | Declined, including a validation failure or a failed render check |
| 2 | Render verification unavailable, or bad command-line usage |
| 3 | The source is not suitable for conversion |
| 4 | Could not write the output: missing directory, write error, or it would overwrite the input |
| 5 | `--strict` was set and the conversion was unverified |

## When it declines

The message names the gate, so start there.

- **`convert/missing-entry-point`** means the file has no `mainImage()` or
  `main()`. It is not a complete shader, or it was truncated.
- **A suitability decline (exit 3)** means the format is recognised but
  cannot be converted, usually HLSL or MilkDrop. Convert that one by hand.
- **"would overwrite"** means the output path is the input, or the input is
  already a `.frag`. Give the output a different name.
- **"render verification unavailable"** means `--verify` could not run. Install
  the render extra or fix the OpenGL driver. It is not a shader failure.
- **"two image inputs"** means exactly that. Keep one image path.

If you think the converter got it wrong, use the issue link the decline
prints. It includes the command, the rejection, and the source, which is what
makes the report actionable.

## Contributing

Install the development extras and run the tests:

```sh
pip install -e ".[dev,render]"
python -m pytest
```

Contributions are accepted under the MIT license with a DCO sign-off. Use
`git commit -s`.

Bugs and bad conversions: open an issue at
<https://github.com/andrewpmiller/vs2convert/issues>.

## License

MIT. See `LICENSE`.
