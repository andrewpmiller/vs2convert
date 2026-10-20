"""Scope-aware replacement: _enclosing_block and _replace_scoped.

The two least-exercised functions in core/generator.py - 30 of 42 and 12 of
22 statements unexercised when the file as a whole read 80% covered. A
per-file number hid that. They are also the machinery behind engine-variable
shadow renaming, so a bug here silently produces a shader that compiles and
misbehaves.
"""
import unittest

from vs2convert.core.generator import _enclosing_block, _replace_scoped


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

    def test_extends_over_a_function_signature(self):
        """The range covers the signature, not just the braces.

        A declaration inside a function body resolves to a span starting at
        the function name. An earlier version of this test asked instead for
        the block containing a position IN the signature, got None, and I
        recorded that as the docstring being wrong. It was the test that was
        wrong: a position in the signature has no enclosing brace, so None is
        correct. Asserted both ways so the distinction stays explicit.
        """
        src = 'float f(float t) {\n    return t;\n}\n'
        span = _enclosing_block(src, src.index('t;'))
        self.assertIsNotNone(span)
        self.assertTrue(src[:span[0]].strip().startswith('float f'))
        # a position in the signature itself is not inside any block
        self.assertIsNone(_enclosing_block(src, src.index('t)')))

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

    def test_braces_in_strings_and_comments_are_ignored(self):
        """A `}` in a comment and a `{` in a string used to shift the depth
        count and return a span one character past the real closing brace.

        Fixed by masking comments and string literals before scanning. This
        was recorded as a known defect first, on the grounds that the helper
        is in a hot path - which was not a good enough reason to leave it."""
        src = ('void main() {\n    // } not a real close\n    s = "{";\n'
               '    int z;\n}\n')
        pos = src.index('int z')
        span = _enclosing_block(src, pos)
        self.assertIsNotNone(span)
        self.assertEqual(src.rindex('}') + 1, span[1])

    def test_block_comments_and_escaped_quotes_do_not_confuse_the_mask(self):
        src = ('void main() {\n    /* } */\n    s = "a\\"b{c";\n'
               '    // /* nested\n    int z;\n}\n')
        pos = src.index('int z')
        span = _enclosing_block(src, pos)
        self.assertIsNotNone(span)
        self.assertEqual(src.rindex('}') + 1, span[1])


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
