# Contributing

Contributions are accepted under the MIT license (see `LICENSE`). There is no
CLA — you keep your copyright, and the project gets the same permissive terms
everyone else has.

## Developer Certificate of Origin (DCO)

Every commit must be signed off. Sign-off is a one-line statement that you
wrote the patch, or have the right to submit it under the project license:

```
git commit -s -m "your message"
```

That appends:

```
Signed-off-by: Your Name <you@example.com>
```

Use your real name and a reachable address. Commits without a sign-off are
not merged; `git rebase --signoff` fixes a branch retroactively.

## Development

The package supports Python >= 3.10, and is developed and tested on 3.14.

```
/opt/homebrew/bin/python3.14 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

Rendering verification additionally needs the `render` extra:
`.venv/bin/pip install -e ".[dev,render]"`.

## Tests

`pytest` must be green. New conversion behaviour needs a test; a refusal gate
needs a positive control showing what the same gate still allows.

## Reporting conversion bugs

Include the source shader (or the smallest version that reproduces the
problem), the exact command, and the full output. A conversion that is wrong
but does not decline is more valuable to report than one that declines.
