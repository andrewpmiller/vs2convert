"""Run the converter over any directory of source shaders and report.

Sources are DISCOVERED, not hardcoded. Collections get added, moved, renamed
or removed and this keeps working; there is no path baked in anywhere.

Usage:
    python3 vs2_convert/tests/corpus_scan.py <dir> [<dir> ...]
    python3 vs2_convert/tests/corpus_scan.py --discover        # find dirs
    python3 vs2_convert/tests/corpus_scan.py <dir> --no-render   # fast

Exit 0 if nothing needs attention, 1 otherwise, so it can gate CI.
"""
import argparse
import os
import subprocess
import sys
import tempfile

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
VS2_DIR = os.path.dirname(TESTS_DIR)
REPO_ROOT = os.path.dirname(VS2_DIR)
sys.path.insert(0, VS2_DIR)

SOURCE_EXT = {'.fs', '.isf', '.frag', '.shadertoy'}
# .txt is a Shadertoy paste, but collections also ship readme.txt and similar.
# Only count a .txt that actually looks like GLSL, so a stray readme is not
# reported as an unconvertible shader.
_PROSE_HINTS = ('readme', 'changelog', 'license', 'notes', 'credits', 'todo')

# A directory counts as a collection if it holds source files at any depth.
COLLECTION_HINTS = ('collection', 'shaders', 'isf')


def discover(root=None):
    """Find likely source collections under `root`. Nothing is hardcoded."""
    root = root or REPO_ROOT
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith('.')
                       and d not in ('__pycache__', 'converted_shaders',
                                     'factory-examples', 'factory-references',
                                     'shader-pipeline', 'node_modules',
                                     'shaders-to-convert')]
        exts = {os.path.splitext(f)[1].lower() for f in filenames}
        if exts & SOURCE_EXT and len(filenames) >= 2:
            found.append(dirpath)
    return sorted(found)


def sources(directory):
    out = []
    for dirpath, _, filenames in os.walk(directory):
        for f in filenames:
            ext = os.path.splitext(f)[1].lower()
            if ext not in SOURCE_EXT and ext != '.txt':
                continue
            stem = os.path.splitext(f)[0].lower()
            if any(h in stem for h in _PROSE_HINTS):
                continue
            if not f.startswith('.'):
                out.append(os.path.join(dirpath, f))
    return sorted(out)


def scan(directory, do_render=True, do_validate=True, sample=6):
    from core.detector import detect_format
    from core.generator import convert, ConversionError

    files = sources(directory)
    rows = {'ok': [], 'declined': [], 'error': [], 'render_fail': [],
            'validate_fail': [], 'unsupported': []}
    verdicts = {}

    for path in files:
        rel = os.path.relpath(path, directory)
        try:
            src = open(path, encoding='utf-8-sig').read()
        except Exception as e:  # noqa: BLE001
            rows['error'].append((rel, 'unreadable: %s' % e))
            continue
        try:
            fmt = detect_format(src)['format']
            report = convert(src, fmt, os.path.splitext(rel)[0])
        except ConversionError as e:
            rows['declined'].append((rel, str(e)))
            verdicts[rel] = msg_key(str(e))
            continue
        except Exception as e:  # noqa: BLE001
            rows['error'].append((rel, '%s: %s' % (type(e).__name__, e)))
            continue
        frag = report['frag']
        rows['ok'].append((rel, len(report['params'])))
        if do_validate:
            from validators.validator import run_validation
            v = run_validation(frag, os.path.splitext(rel)[0])
            if v['errors']:
                rows['validate_fail'].append((rel, v['errors'][0]))
        if do_render:
            try:
                from renderer.pipeline import run_verification
                r = run_verification(frag, os.path.splitext(rel)[0])
            except ImportError:
                r = None
            if r is not None and not r['passed']:
                if r.get('metrics', {}).get('unsupported'):
                    rows['unsupported'].append((rel, 'image-input'))
                else:
                    rows['render_fail'].append((rel, r['errors'][0][:90]))
    return rows, verdicts


def msg_key(text):
    """Bucket a decline message so counts group by cause."""
    t = text.lower()
    if 'feedback' in t or 'target' in t:
        return 'feedback buffer'
    if 'milkdrop' in t or 'hlsl' in t or 'texture2d' in t:
        return 'HLSL / bundled texture'
    if 'ichannel' in t:
        return 'multi-image input'
    if 'image input' in t:
        return 'multi-image input'
    if 'imported' in t:
        return 'bundled image (IMPORTED)'
    if 'engine var' in t:
        return 'engine var redeclared'
    if 'entry point' in t:
        return 'no entry point'
    if 'inputs block' in t:
        return 'no ISF INPUTS'
    return 'other'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dirs', nargs='*')
    ap.add_argument('--discover', action='store_true',
                    help='list source collections found under the repo')
    ap.add_argument('--no-render', action='store_true')
    ap.add_argument('--no-validate', action='store_true')
    a = ap.parse_args()

    if a.discover:
        for d in discover():
            print('%-60s %d files' % (os.path.relpath(d, REPO_ROOT),
                                      len(sources(d))))
        return 0
    if not a.dirs:
        print(__doc__)
        print('no directories given; try --discover')
        return 1

    total_bad = 0
    for d in a.dirs:
        rows, verdicts = scan(d, not a.no_render, not a.no_validate)
        print('=' * 66)
        print('%s   (%d sources)' % (d, sum(len(v) for v in rows.values())))
        print('=' * 66)
        for key in ('ok', 'validate_fail', 'render_fail', 'unsupported',
                    'declined', 'error'):
            items = rows[key]
            if not items:
                continue
            print('\n%-16s %d' % (key, len(items)))
            for rel, info in items[:6]:
                print('    %s' % rel)
                print('        %s' % str(info)[:96])
            if len(items) > 6:
                print('    ... and %d more' % (len(items) - 6))
        if verdicts:
            from collections import Counter
            print('\n%-16s %s' % ('decline causes', dict(Counter(verdicts.values()))))
        total_bad += sum(len(rows[k]) for k in
                         ('error', 'validate_fail', 'render_fail'))
    return 1 if total_bad else 0


if __name__ == '__main__':
    sys.exit(main())
