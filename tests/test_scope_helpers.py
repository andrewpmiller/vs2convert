"""Scope-aware replacement: _enclosing_block and _replace_scoped.

The two least-exercised functions in core/generator.py - 30 of 42 and 12 of
22 statements unexercised when the file as a whole read 80% covered. A
per-file number hid that. They are also the machinery behind engine-variable
shadow renaming, so a bug here silently produces a shader that compiles and
misbehaves.
"""
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
VS2_DIR = os.path.dirname(TESTS_DIR)
sys.path.insert(0, VS2_DIR)

from core.generator import _enclosing_block, _replace_scoped  # noqa: E402


class EnclosingBlockTests(unittest.TestCase):

    def test_finds_innermost_block(self):
        src = 'void main() {\n    if (x) {\n        int y;\n    }\n}\n'
        pos = src.index('int y')
        span = _enclosing_block(src, pos)
        self.assertIsNotNone(span)
        start, end = span
        self.assertLessEqual(start, pos)
        self.assertGreater(end, pos)
        # the span must contain the if-block, not the whole function
        self.assertLess(end - start, len(src) - 1)

    def test_signature_extension_is_NOT_implemented(self):
        """KNOWN DEFECT, pinned.

        The docstring says the range "is extended back over the signature so
        parameter declarations fall inside it". It is not: a position inside
        `float f(float t)` has no enclosing `{` and returns None. The
        docstring describes intended behaviour the code does not have.

        This is why shadowed engine names are handled by
        _rename_shadowed_engine_vars upstream instead - by the time
        _replace_scoped runs, the declarations are gone. Recorded so the gap
        between the docstring and the code is visible rather than latent.
        """
        src = 'float f(float t) {\n    return t;\n}\n'
        pos = src.index('t)')
        self.assertIsNone(_enclosing_block(src, pos),
                          'if this changes the docstring is now true - '
                          'update both')

    def test_nested_blocks_pick_the_inner_one(self):
        src = '{ { { int q; } } }'
        pos = src.index('int q')
        s, e = _enclosing_block(src, pos)
        # the innermost block is the third {...}; end is one PAST the '}',
        # consistent with the string/comment case above
        third_open = src.index('{', src.index('{', src.index('{') + 1) + 1)
        self.assertEqual(third_open, s)
        self.assertEqual(src.index('}', pos) + 1, e)

    def test_returns_none_when_there_is_no_block(self):
        src = 'int loose = 1;\n'
        self.assertIsNone(_enclosing_block(src, src.index('loose')))

    def test_braces_in_strings_and_comments_are_NOT_handled(self):
        """KNOWN DEFECT, pinned here so it cannot get worse unnoticed.

        _enclosing_block counts raw braces, so a `}` in a comment or a `{`
        in a string shifts the nesting depth and the span comes back wrong.
        With `// } not a real close` in the body the returned end is one
        past the real closing brace.

        Not fixed here: the only correct fix is to skip comments and string
        literals while scanning, which is a real change to a hot helper used
        by the shadow-renaming path. Recorded so the next person sees it as
        known rather than discovers it. Asserting current behaviour so a
        regression in either direction is visible.
        """
        src = ('void main() {\n    // } not a real close\n    s = "{";\n'
               '    int z;\n}\n')
        pos = src.index('int z')
        span = _enclosing_block(src, pos)
        self.assertIsNotNone(span)
        real_end = src.rindex('}')
        self.assertEqual(real_end + 1, span[1],
                         'if this now passes, the defect is fixed - update '
                         'the test and the docstring')


class ReplaceScopedTests(unittest.TestCase):

    def test_no_declaration_wraps_every_reference(self):
        out = _replace_scoped('a = b + 1;\n', 'b', 'B', '(B)')
        self.assertEqual('a = (B) + 1;\n', out)

    def test_local_declaration_keeps_references_plain(self):
        src = 'void f() {\n    float t = 1.0;\n    return t;\n}\n'
        out = _replace_scoped(src, 't', 't', '(t)')
        # inside the declaring block every reference stays plain
        self.assertIn('float t = 1.0;', out)
        self.assertIn('return t;', out)
        self.assertNotIn('(t)', out)

    def test_global_declaration_makes_the_whole_file_plain(self):
        src = 'float t = 1.0;\nvoid f() { return t; }\n'
        out = _replace_scoped(src, 't', 't', '(t)')
        self.assertNotIn('(t)', out)

    def test_same_name_in_two_functions_is_handled_per_scope(self):
        src = ('void a() {\n    float v = 0.0;\n    v += 1.0;\n}\n'
               'void b() {\n    return v;\n}\n')
        out = _replace_scoped(src, 'v', 'v', '(v)')
        # the reference OUTSIDE any declaration is wrapped
        self.assertIn('(v)', out)
        # and the local ones are not
        self.assertIn('v += 1.0;', out)

    def test_word_boundaries_are_respected(self):
        out = _replace_scoped('time = timescale + 1.0;\n', 'time', 'time', '(time)')
        self.assertIn('(time)', out)
        self.assertIn('timescale', out)
        self.assertNotIn('(timescale)', out)

    def test_no_occurrences_is_a_no_op(self):
        src = 'int q = 1;\n'
        self.assertEqual(src, _replace_scoped(src, 'absent', 'absent', 'X'))


if __name__ == '__main__':
    unittest.main()
