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
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))


def main():
    """Main entry point for VS-2 shader conversion."""
    parser = argparse.ArgumentParser(
        description="VS-2 Shader Conversion App - Convert shaders from ISF, Shadertoy, GLSLSandbox to VS-2 format",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python vs2_convert.py shaders/black_hole_sun.isf converted_black_hole_sun.frag
    python vs2_convert.py shaders/waves.shadertoy --validate
    python vs2_convert.py shaders/smoke.glsl --verify --dry-run

Options:
    --validate    Run structural validation, exit 1 if errors found
    --verify      Run render verification (headless OpenGL), exit 1 if fails
    --dry-run     Show what would be generated without writing
    --help        Show this help message
        """
    )

    parser.add_argument("input", help="Input shader file (ISF, Shadertoy, GLSLSandbox, or plain GLSL)")
    parser.add_argument("output", nargs="?", help="Output .frag file (default: converted_<name>.frag)")
    parser.add_argument("--validate", action="store_true", help="Run structural validation, exit 1 if errors found")
    parser.add_argument("--verify", action="store_true", help="Run render verification (headless OpenGL), exit 1 if fails")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be generated without writing")
    parser.add_argument("--force", action="store_true",
                        help="Convert even when the suitability gate declines")

    args = parser.parse_args()

    from core.detector import detect_format, check_suitability
    from core.generator import convert, ConversionError
    from validators.validator import run_validation

    # Read source first: detection must see file CONTENTS, not the path (D1)
    source_path = Path(args.input)
    if not source_path.exists():
        print(f"[error] Input file not found: {args.input}")
        sys.exit(1)
    source = source_path.read_text(encoding="utf-8")

    print(f"[info] Detecting format for: {args.input}")
    format_info = detect_format(source)
    fmt = format_info["format"]
    print(f"[info] Format detected: {fmt}")

    if format_info.get("warnings"):
        print("[warn] Issues detected (proceeding anyway):")
        for warning in format_info["warnings"]:
            print(f"  - {warning}")

    # --- suitability gate -----------------------------------------------
    # The detector computes a suitability verdict, but it used to be purely
    # advisory: the CLI printed warnings and converted anyway. That let a
    # shader redeclaring `resolution` be emitted as a .frag that shadows the
    # engine's uniform. Unsuitable sources are now declined by default.
    suitability = check_suitability(source)
    if not suitability["suitable"]:
        print()
        print("[decline] This shader is not suitable for automated conversion:")
        for err in suitability["errors"]:
            print(f"  - {err}")
        print()
        print("[decline] No output was written. Reasons above are the ones the")
        print("[decline] app could not resolve on its own. Fix the source, or")
        print("[decline] convert it by hand via the vs2-shader-expert skill.")
        if not args.force:
            sys.exit(3)
        print("[warn] --force given: converting anyway. The result is very")
        print("[warn] likely to be broken; a render gate is the only real check.")
    elif suitability.get("warnings"):
        print("[warn] Suitability notes:")
        for warning in suitability["warnings"]:
            print(f"  - {warning}")

    # Convert
    print("[info] Converting...")
    try:
        report = convert(source, fmt, source_path.stem)
    except ConversionError as exc:
        print(f"[error] {exc}")
        sys.exit(1)

    for item in report["baked"]:
        print(f"[baked] {item}")
    for item in report["warnings"]:
        print(f"[warn] {item}")

    full_content = report["frag"]


    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = Path(f"converted_{source_path.stem}.frag")

    # Structural validation (D2 fix: always initialized)
    validation_result = {"errors": [], "warnings": []}
    if args.validate:
        print("[info] Running structural validation...")
        validation_result = run_validation(full_content, output_path.stem)
        for warning in validation_result["warnings"]:
            print(f"[warn] {warning}")
        if validation_result["errors"]:
            print("[error] Validation failed:")
            for error in validation_result["errors"]:
                print(f"  - {error}")
            sys.exit(1)
        print("[info] Validation passed")

    # Optional render verification
    if args.verify:
        print("[info] Running render verification...")
        try:
            from renderer.pipeline import run_verification
            verification_result = run_verification(full_content, output_path.stem)
            if not verification_result["passed"]:
                print("[error] Render verification failed:")
                for error in verification_result["errors"]:
                    print(f"  - {error}")
                sys.exit(1)
            m = verification_result.get("metrics", {})
            print("[info] Render verification passed "
                  "(mean=%s std=%s alpha=%s motion=%s)"
                  % (m.get('mean'), m.get('std'), m.get('alpha'),
                     m.get('motion')))
        except ImportError:
            # Must not exit 0 here. The old renderer could not fail, and a
            # silent skip reads like a pass in CI. Say the gate did not run.
            print("[error] Render verification could not run: "
                  "moderngl + numpy + Pillow are required "
                  "(pip install 'vs2-convert[render]')")
            print("[error] --verify was requested but did not execute. "
                  "Treating as FAILED so it cannot be mistaken for a pass.")
            sys.exit(2)

    if args.dry_run:
        print("")
        print("[preview] Generated shader:")
        print("=" * 60)
        print(full_content)
        print("=" * 60)
        print("")
        print(f"[info] Would save to: {output_path}")
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(full_content, encoding="utf-8")
        print(f"[info] Saved to: {output_path}")

    # Summary
    print("")
    print("[summary]")
    print(f"  Input:    {args.input}")
    print(f"  Output:   {output_path}")
    print(f"  Format:   {fmt}")
    print(f"  Params:   {len(report['params'])}")
    if args.validate:
        print(f"  Valid:    {'Yes' if not validation_result['errors'] else 'No'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
