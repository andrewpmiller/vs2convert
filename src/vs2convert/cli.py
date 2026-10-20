#!/usr/bin/env python3
"""
VS-2 Shader Conversion App - Self-Contained CLI

Usage:
    python vs2_convert.py <input.frag> [output.frag] [--validate] [--verify] [--dry-run]

Converts shaders from ISF, Shadertoy, or GLSLSandbox format to VS-2 .frag
format using the bundled conversion engine.

Dependencies:
    None required for basic conversion
    moderngl + numpy + Pillow for optional render verification (--verify)

Examples:
    python vs2_convert.py shaders/black_hole_sun.isf converted_black_hole_sun.frag
    python vs2_convert.py shaders/waves.shadertoy --validate
    python vs2_convert.py shaders/smoke.glsl --verify --dry-run
"""

import argparse
import re
import textwrap
import json
import sys
from pathlib import Path

def main():
    """Main entry point for VS-2 shader conversion."""
    parser = argparse.ArgumentParser(
        description="VS-2 Shader Conversion App - Convert shaders from ISF, "
                    "Shadertoy, GLSLSandbox to VS-2 format",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Exit codes:
  0  converted and (if requested) validated / rendered
  1  declined by a generator hard gate, or validation/render failure
  2  --verify could not RUN (missing dependency) - not a shader problem
  3  declined by the suitability gate (before conversion)
  4  could not write the output
  5  --strict, and the conversion was UNVERIFIED

Examples:
  python vs2_convert.py shaders/wave.fs                 # writes wave.frag beside it
  python vs2_convert.py shaders/wave.fs out.frag
  python vs2_convert.py shaders/wave.fs --validate --verify
  python vs2_convert.py shaders/wave.fs -v               # full detail on stderr
        """)
    parser.add_argument("input", help="Input shader file")
    parser.add_argument("output", nargs="?",
                        help="Output .frag (default: beside the input)")
    parser.add_argument("--validate", action="store_true",
                        help="Run structural validation")
    parser.add_argument("--verify", action="store_true",
                        help="Render headlessly and check the pixels")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the shader instead of writing it")
    parser.add_argument("--force", action="store_true",
                        help="Convert even when the gate declines")
    parser.add_argument("--meta", metavar="FILE",
                        help="Shadertoy pass/channel metadata JSON. Also "
                             "auto-detected as <stem>.stmeta.json beside the "
                             "input, or an inline /*{...}*/ block.")
    parser.add_argument("--strict", action="store_true",
                        help="Exit 5 if the conversion is only UNVERIFIED. "
                             "Use in CI.")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show every step and the full text of each note")
    args = parser.parse_args()

    from .core.report import Reporter
    from .core.detector import detect_format, check_suitability
    from .core.shadertoy_meta import load_meta
    from .core.generator import convert, ConversionError
    from .validators.validator import run_validation

    r = Reporter(verbose=args.verbose)
    name = Path(args.input).name

    def bail(headline, body=None, action=None, code=1):
        r.failure(name, headline, body=body, action=action, exit_code=code)
        sys.exit(code)

    # --- read + detect ---------------------------------------------------
    source_path = Path(args.input)
    if not source_path.exists():
        bail('input not found', body=[str(source_path)], code=1)
    source = source_path.read_text(encoding="utf-8")
    r.step('reading %s' % source_path)

    format_info = detect_format(source)
    fmt = format_info["format"]
    r.step('detected %s' % fmt)
    for w in format_info.get("warnings") or []:
        r.note(w)

    # --- suitability gate ------------------------------------------------
    suitability = check_suitability(source)
    if not suitability["suitable"]:
        if not args.force:
            r.failure(name, 'declined',
                      body=_wrap(suitability["errors"]),
                      action='convert by hand, or fix the source, or --force',
                      exit_code=3)
            sys.exit(3)
        r.note('--force: converting despite the decline; expect it to be wrong')
    else:
        for w in suitability.get("warnings") or []:
            r.note(w)

    # --- pass metadata ---------------------------------------------------
    meta = meta_from = None
    if fmt == 'Shadertoy' or 'mainImage' in source or 'iChannel' in source:
        try:
            meta, meta_from = load_meta(str(source_path), source, args.meta)
        except (ValueError, OSError, json.JSONDecodeError) as e:
            bail('could not read pass metadata', body=[str(e)], code=1)
        if meta:
            r.step('pass metadata from %s' % meta_from)

    # --- convert ---------------------------------------------------------
    r.step('converting')
    try:
        report = convert(source, fmt, source_path.stem, force=args.force,
                         meta=meta)
    except ConversionError as exc:
        bail('declined', body=_wrap([str(exc)]),
             action='convert it by hand, or fix the source', code=1)

    for item in report["baked"]:
        r.step('baked: %s' % item)
    for item in report["warnings"]:
        if item.startswith("UNVERIFIED"):
            r.unverified(_short_unverified(item), _unverified_body(item))
            r.detail('  ' + item)
        else:
            r.note(item)
    _unverified = [w for w in report["warnings"] if w.startswith("UNVERIFIED")]

    full_content = report["frag"]

    # --- output path -----------------------------------------------------
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = source_path.with_suffix('.frag')
        if output_path.resolve() == source_path.resolve():
            bail('refusing to overwrite the input',
                 body=['the default output for a .frag input is the input'],
                 action='pass an explicit output filename', code=4)

    stats = ['%s . %d param%s' % (fmt, len(report['params']),
                                    '' if len(report['params']) == 1 else 's')]

    # --- structural validation ------------------------------------------
    if args.validate:
        r.step('validating')
        validation_result = run_validation(full_content, output_path.stem)
        for w in validation_result["warnings"]:
            r.note(w)
        if validation_result["errors"]:
            bail('validation failed', body=_wrap(validation_result["errors"]),
                 action='fix the source', code=1)
        stats.append('validated')

    # --- render verification --------------------------------------------
    if args.verify:
        r.step('rendering')
        try:
            from .renderer.pipeline import run_verification
        except ImportError:
            r.skipped(name, 'converted, but not verified',
                      body=['the render check could not run'],
                      action="rendering needs moderngl+numpy+Pillow; install vs2convert[render]", exit_code=2)
            sys.exit(2)
        verification_result = run_verification(full_content, output_path.stem)
        if not verification_result["passed"]:
            errs = verification_result["errors"]
            unavailable = any('unavailable' in e for e in errs)
            if unavailable:
                r.skipped(name, 'converted, but not verified',
                          body=_wrap(errs),
                          action="rendering needs moderngl+numpy+Pillow; install vs2convert[render]",
                          exit_code=2)
                sys.exit(2)
            compile_fail = any('compile' in e for e in errs)
            bail('render check failed',
                 body=_gate_lines(errs),
                 action=('the output did not compile; this is a converter '
                         'defect, not a problem with your source'
                         if compile_fail else
                         'it compiled but is not showing anything useful'),
                 code=1)
        m = verification_result.get("metrics", {})
        stats.append('rendered (motion %s)' % m.get('motion'))

    if args.strict and _unverified:
        r.failure(name, 'UNVERIFIED under --strict',
                  body=['the conversion rests on an assumption the tool '
                        'could not check'],
                  action='check the shader against its original, or drop '
                         '--strict', exit_code=5)
        sys.exit(5)

    # --- write -----------------------------------------------------------
    if args.dry_run:
        r.success(name, wrote='(dry run, nothing written)', stats=stats)
        print()
        print(full_content)
        return 0

    if not output_path.parent.exists():
        bail('output directory does not exist',
             body=[str(output_path.parent)],
             action='pass a filename, not a path', code=4)
    try:
        output_path.write_text(full_content, encoding="utf-8")
    except OSError as e:
        bail('could not write', body=[str(e)], code=4)

    r.success(name, wrote=output_path.name, stats=stats)
    return 0


def _gate_lines(items, width=66):
    """Render gate errors one per line. A GLSL compiler dump arrives as one
    long blob and is unreadable when wrapped, so split it on its own line
    breaks first."""
    out = []
    for it in items:
        text = str(it)
        if 'ERROR:' in text or len(text) > 200:
            parts = [p.strip() for p in text.replace('====', '\n').split('\n')]
            for p in parts:
                p = p.replace('ERROR: ', '')
                p = re.sub(r'^(GLSL Compiler failed|fragment_shader|=+)$', '', p)
                p = p.strip()
                if p:
                    out.extend(textwrap.wrap(p, width) or [p])
        else:
            out.extend(textwrap.wrap(text, width) or [text])
    return out


def _wrap(items, width=66):
    """Wrap long gate messages to a readable width."""
    out = []
    for it in items:
        out.extend(textwrap.wrap(str(it), width) or [''])
    return out


def _short_unverified(text):
    """One-line headline for the UNVERIFIED block."""
    if 'iChannel' in text:
        return 'iChannel assumed to be a plain image'
    return text.split(':')[0][:60].strip()


def _unverified_body(text):
    """Short plain-English consequence. The full original text goes to
    stderr under -v, so the default output stays scannable."""
    if 'iChannel' in text:
        return ['Shadertoy channels can also be buffers, cubemaps, video or',
                'audio, and only the shader page says which. Check it there,',
                'or add a <name>.stmeta.json beside the .txt.']
    return _wrap([text])


if __name__ == '__main__':
    sys.exit(main())
