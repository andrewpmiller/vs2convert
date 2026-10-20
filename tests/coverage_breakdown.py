"""Per-function statement coverage for core/generator.py.

coverage.py reports whole files. A file at 80% can hide a function at 29%,
which is the case that matters: _enclosing_block was 30 of 42 statements
unexercised while the file read 80%.

Run tests/run_coverage.sh first; this reads the combined data it writes.
"""
import ast
import os

import coverage
from coverage.parser import PythonParser

import vs2convert

TARGET = os.path.join(os.path.dirname(os.path.abspath(vs2convert.__file__)),
                      'core', 'generator.py')
# <repo>/.coverage-data/.coverage, written by run_coverage.sh.
DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    '.coverage-data', '.coverage')


def main():
    if not os.path.exists(DATA):
        print('  no .coverage data at %s' % os.path.normpath(DATA))
        print('  run tests/run_coverage.sh first')
        return 1
    src = open(TARGET).read()
    tree = ast.parse(src)
    funcs = [(n.lineno, n.end_lineno, n.name) for n in tree.body
             if isinstance(n, ast.FunctionDef)]
    parser = PythonParser(text=src)
    parser.parse_source()
    stmts = set(parser.statements)

    data = coverage.CoverageData(DATA)
    data.read()
    measured = [f for f in data.measured_files() if f.endswith('generator.py')]
    if not measured:
        print('  generator.py not measured')
        return 1
    executed = set(data.lines(measured[0]) or set())
    missed = stmts - executed

    print('  %-34s %s' % ('function', 'missed / total'))
    rows = []
    for lo, hi, name in funcs:
        m = sum(1 for x in missed if lo <= x <= hi)
        t = sum(1 for x in stmts if lo <= x <= hi)
        if t:
            rows.append((m, t, name))
    for m, t, name in sorted(rows, reverse=True):
        flag = '  <-- under 50%' if m * 2 > t else ''
        if m:
            print('  %-34s %3d / %3d%s' % (name, m, t, flag))
    print()
    print('  generator.py overall: %d / %d statements covered (%.0f%%)'
          % (len(stmts) - len(missed), len(stmts),
             100.0 * (len(stmts) - len(missed)) / max(len(stmts), 1)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
