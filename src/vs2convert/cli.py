#!/usr/bin/env python3
"""vs2convert - convert Shadertoy, ISF and GLSLSandbox shaders to VS-2 .frag.

A thin shell over :func:`vs2convert.api.convert`: it reads the source, prints
the verdict, and writes the file. All conversion decisions live in the API.
"""

import argparse
import json
import re
import shlex
import sys
import textwrap
from pathlib import Path
from urllib.parse import urlencode

from . import api
from .core.detector import detect_format
from .core.report import Reporter
from .core.shadertoy_meta import load_meta

# Where a declined conversion is reported. Kept in one place: it is printed
# on every decline, so it must be right.
ISSUES_URL = "https://github.com/andrewpmiller/vs2convert/issues/new"


def main():
    parser = argparse.ArgumentParser(
        prog="vs2convert",
        description="Convert a Shadertoy, ISF or GLSLSandbox shader to a "
                    "VS-2 .frag file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Exit codes:
  0  converted (always validated) and, if requested, rendered
  1  declined, or the input/metadata could not be read - includes
     validation failure and a failed render check
  2  render verification unavailable, or bad command-line usage
  3  the source is not suitable for conversion
  4  could not write the output (missing directory, write error, would
     overwrite the input)
  5  --strict, and the conversion was UNVERIFIED

Pass metadata:
  --meta FILE                     explicit JSON file
  <stem>.stmeta.json beside the source (or a lone *.stmeta.json in its dir)
  an inline /*{...}*/ JSON block at the top of the source
  Precedence: --meta, then sidecar, then inline. Only Shadertoy-style
  sources consult it.

Examples:
  vs2convert shader.fs                    # writes shader.frag beside it
  vs2convert shader.fs out.frag --verify
  cat shader.glsl | vs2convert - out.frag  # stdin needs an explicit output
""")
    parser.add_argument("source", metavar="SOURCE",
                        help="Shader file, or - to read stdin")
    parser.add_argument("output", nargs="?",
                        help="Output .frag (default: <source stem>.frag "
                             "beside the input; required with stdin)")
    parser.add_argument("--verify", action="store_true",
                        help="Render headlessly and print metrics (needs "
                             "vs2convert[render])")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the shader instead of writing it")
    parser.add_argument("--force", action="store_true",
                        help="Convert past a capability gate (not past HLSL)")
    parser.add_argument("--meta", metavar="FILE",
                        help="Shadertoy pass/channel metadata JSON")
    parser.add_argument("--strict", action="store_true",
                        help="Exit 5 if the conversion is only UNVERIFIED")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show every step and the full text of each note")
    args = parser.parse_args()

    r = Reporter(verbose=args.verbose)

    # --- read -----------------------------------------------------------------
    if args.source == "-":
        source = sys.stdin.read()
        source_path = None
        name = "stdin"
        if not args.output:
            parser.error("OUTPUT is required when reading from stdin")
        r.step("reading stdin")
    else:
        source_path = Path(args.source)
        name = source_path.name
        if not source_path.exists():
            _fail(r, name, "input not found", [str(source_path)],
                  "check the path", 1)
        source = source_path.read_text(encoding="utf-8")
        r.step("reading %s" % source_path)

    # --- metadata -------------------------------------------------------------
    # Pass metadata is a Shadertoy concept. Loading it for every format made
    # an ISF header look like pass metadata and let a stray .stmeta.json
    # abort an ISF conversion.
    meta = None
    try:
        fmt = detect_format(source).get("format")
    except Exception:
        fmt = None
    if fmt in ("Shadertoy", "PlainGLSL"):
        try:
            meta, meta_from = load_meta(
                str(source_path) if source_path else None, source, args.meta)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            _fail(r, name, "could not read pass metadata", [str(exc)],
                  "fix or omit --meta", 1)
        if meta:
            r.step("pass metadata from %s" % meta_from)
    elif args.meta:
        r.note("ignoring --meta: only Shadertoy sources use pass metadata")

    # --- convert --------------------------------------------------------------
    r.step("converting")
    result = api.convert(source, name=source_path.stem if source_path else "stdin",
                         force=args.force, meta=meta)

    if not result.ok:
        _report_decline(r, name, result, source, sys.argv[1:])
        sys.exit(3 if any(x.stage == "suitability" for x in result.rejections)
                 else 1)

    for item in result.baked:
        r.note("baked: %s" % item)
    for item in result.warnings:
        if item.startswith("UNVERIFIED"):
            r.unverified(_short_unverified(item), _unverified_body(item))
            r.detail("  " + item)
        else:
            r.note(item)
    unverified = [w for w in result.warnings if w.startswith("UNVERIFIED")]

    # --- output path ----------------------------------------------------------
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = source_path.with_suffix(".frag")
    # The guard must cover an explicit output too: `vs2convert in.fs in.fs`
    # used to overwrite the source and report success.
    if source_path is not None and \
            output_path.resolve() == source_path.resolve():
        _fail(r, name, "refusing to overwrite the input",
              ["the output path is the input file"],
              "pass a different output filename", 4)

    stats = ["%s \u00b7 %d param%s" % (result.format, len(result.params),
                                        "" if len(result.params) == 1 else "s")]
    stats.append("validated")

    # --- render verification --------------------------------------------------
    if args.verify:
        r.step("rendering")
        try:
            from .renderer.pipeline import run_verification
        except ImportError:
            _unavailable(r, name)
        verification = run_verification(result.frag, output_path.stem)
        if not verification["passed"]:
            errs = verification["errors"]
            if any("unavailable" in e for e in errs):
                _unavailable(r, name, errs)
            if any("could not create a GL context" in e for e in errs):
                _unavailable(
                    r, name, errs,
                    "OpenGL is not working on this machine, so the shader "
                    "was not rendered")
            compile_fail = any("compile" in e for e in errs)
            _fail(r, name, "render check failed", _gate_lines(errs),
                  ("the output did not compile; this is a converter defect, "
                   "not a problem with your source" if compile_fail else
                   "it compiled but is not showing anything useful"), 1)
        metrics = verification.get("metrics", {})
        shown = ", ".join(
            "%s %s" % (k, metrics[k])
            for k in ("mean", "std", "alpha", "motion") if k in metrics)
        stats.append("rendered (%s)" % shown if shown else "rendered")

    # --- strict ---------------------------------------------------------------
    if args.strict and unverified:
        _fail(r, name, "UNVERIFIED under --strict",
              ["the conversion rests on an assumption the tool could not check"],
              "check the shader against its original, or drop --strict", 5)

    # --- write ----------------------------------------------------------------
    if args.dry_run:
        r.success(name, wrote="(dry run, nothing written)", stats=stats)
        print()
        print(result.frag)
        return 0

    if not output_path.parent.exists():
        _fail(r, name, "output directory does not exist",
              [str(output_path.parent)], "pass a filename, not a path", 4)
    try:
        output_path.write_text(result.frag, encoding="utf-8")
    except OSError as exc:
        _fail(r, name, "could not write", [str(exc)], None, 4)

    r.success(name, wrote=output_path.name, stats=stats)
    return 0


def _report_decline(r, name, result, source, argv=None):
    """One `stage/gate: reason` line per rejection, then its advice."""
    code = 3 if any(x.stage == "suitability" for x in result.rejections) else 1
    body = []
    for rejection in result.rejections:
        body.extend(_wrap("%s/%s: %s"
                          % (rejection.stage, rejection.gate,
                             rejection.reason)))
        body.extend(_wrap("  " + rejection.detail))
    r.failure(name, "declined", body=body,
              action="fix the source, or convert it by hand", exit_code=code)
    print()
    print("Report this with the source: %s"
          % _issue_url(name, result, source, argv))


def _issue_url(name, result, source, argv=None):
    gates = ", ".join(sorted({x.gate for x in result.rejections}))
    title = "Declined: %s (%s)" % (name, gates)
    command = "vs2convert %s" % (shlex.join(argv) if argv else name)
    lines = ["Command: %s" % command, "", "Rejections:"]
    for rejection in result.rejections:
        lines.append("- %s/%s: %s" % (rejection.stage, rejection.gate,
                                      rejection.reason))
    lines += ["", "Source (truncated):", source[:1500]]
    body = "\n".join(lines)
    return ISSUES_URL + "?" + urlencode({"title": title, "body": body})


def _unavailable(r, name, errs=None, action=None):
    body = [str(e) for e in (errs or ["the render check could not run"])]
    action = action or ("rendering needs moderngl+numpy+Pillow; "
                        "install vs2convert[render]")
    r.skipped(name, "converted, but not verified", body=_wrap(body),
              action=action, exit_code=2)
    sys.exit(2)


def _fail(r, name, headline, body=None, action=None, code=1):
    r.failure(name, headline, body=_wrap(body) if body else None,
              action=action, exit_code=code)
    sys.exit(code)


def _gate_lines(items, width=66):
    """Render gate errors one per line. A GLSL compiler dump arrives as one
    long blob and is unreadable when wrapped, so split it on its own line
    breaks first."""
    out = []
    for it in items:
        text = str(it)
        if "ERROR:" in text or len(text) > 200:
            parts = [p.strip() for p in text.replace("====", "\n").split("\n")]
            for part in parts:
                part = part.replace("ERROR: ", "")
                part = re.sub(r"^(GLSL Compiler failed|fragment_shader|=+)$",
                              "", part)
                part = part.strip()
                if part:
                    out.extend(textwrap.wrap(part, width) or [part])
        else:
            out.extend(textwrap.wrap(text, width) or [text])
    return out


def _wrap(items, width=66):
    """Wrap one message or a list of them to a readable width."""
    if isinstance(items, str):
        items = [items]
    out = []
    for it in items:
        out.extend(textwrap.wrap(str(it), width) or [""])
    return out


def _short_unverified(text):
    """One-line headline for the UNVERIFIED block."""
    if "iChannel" in text:
        return "iChannel assumed to be a plain image"
    return text.split(":")[0][:60].strip()


def _unverified_body(text):
    """Short plain-English consequence. The full text goes to stderr under -v,
    so the default output stays scannable."""
    if "iChannel" in text:
        return ["Shadertoy channels can also be buffers, cubemaps, video or",
                "audio, and only the shader page says which. Check it there,",
                "or add a <name>.stmeta.json beside the .txt."]
    return _wrap([text])


if __name__ == "__main__":
    sys.exit(main())
