"""Behavioural baseline over whatever ISF collections are present.

Sources are DISCOVERED, never hardcoded. Collections get added, moved or
removed and this keeps working; the two new collections
(VDMX_collection, GLSLOP_collection) are not referenced by path anywhere.

Baselines are expressed as PROPORTIONS and as cause-codes rather than exact
counts, so adding a collection is not a failure - only a change in behaviour
on a given collection is.

Run: python3 vs2_convert/tests/test_isf_collections.py
"""
import os
import sys
import unittest
from collections import Counter

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
VS2_DIR = os.path.dirname(TESTS_DIR)
REPO_ROOT = os.path.dirname(VS2_DIR)
sys.path.insert(0, VS2_DIR)
sys.path.insert(0, TESTS_DIR)

from corpus_scan import scan, sources  # noqa: E402


def _collections():
    """Any directory holding >=3 SOURCE (.fs) files, discovered not listed.

    Only .fs counts for discovery: converted .frag OUTPUTS live in
    converted_shaders/ and must never be fed back in as if they were sources.
    """
    # Directories whose contents are never SOURCE collections. Note
    # `vs2_convert` is deliberately NOT skipped: the VDMX and GLSLOP
    # collections live inside it. Code subdirs are skipped individually.
    skip = {'.git', '__pycache__', 'converted_shaders', 'factory-examples',
            'factory-references', 'shader-pipeline', 'node_modules',
            'tests', 'templates', 'core', 'validators', 'renderer'}
    found = []
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith('.') and d not in skip]
        n = sum(1 for f in filenames if f.lower().endswith('.fs'))
        if n >= 3:
            found.append(dirpath)
    return sorted(set(found))


class ProseExclusionTests(unittest.TestCase):
    """`sources()` walks the collections and must not hand back documentation
    as if it were a shader. Without a test here the exclusion can be deleted
    and the corpus still reports clean, because nothing asserts it.

    These call sources() and check its OUTPUT. An earlier version of this
    class only inspected the _PROSE_HINTS list, which stayed green with the
    guard in sources() deleted - the mutation harness caught that."""

    def test_sources_excludes_prose_in_a_real_collection(self):
        import corpus_scan
        # A temp tree is the only reliable probe: it isolates the question
        # "does sources() drop a file named readme.txt" from whatever the
        # collections happen to ship today.
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            for name in ('readme.txt', 'Readme.txt', 'CREDITS.txt',
                         'notes.md', 'real shader.fs'):
                open(os.path.join(d, name), 'w').close()
            found = [os.path.basename(p) for p in corpus_scan.sources(d)]
        self.assertIn('real shader.fs', found,
                      'probe is broken: sources() dropped a real .fs file')
        for prose in ('readme.txt', 'Readme.txt', 'CREDITS.txt', 'notes.md'):
            self.assertNotIn(prose, found,
                             'sources() returned %r as a shader source' % prose)

    def test_prose_hints_cover_what_the_collections_actually_contain(self):
        import corpus_scan
        prose = ('.txt', '.md')
        undocumented = set()
        for coll in _collections():
            for dirpath, _, filenames in os.walk(coll):
                for f in filenames:
                    if not f.lower().endswith(prose):
                        continue
                    stem = os.path.splitext(f)[0].lower()
                    if not any(h in stem for h in corpus_scan._PROSE_HINTS):
                        undocumented.add(f)
        self.assertEqual(
            set(), undocumented,
            'prose names not covered by _PROSE_HINTS: %s'
            % sorted(undocumented)[:5])


class CollectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cols = _collections()
        cls.results = {}
        for d in cls.cols:
            # convert + validate only; the render half needs moderngl and is
            # covered by a separate opt-in test below.
            rows, verdicts = scan(d, do_render=False, do_validate=True)
            cls.results[d] = (rows, verdicts)

    def test_at_least_one_collection_found(self):
        self.assertTrue(self.cols, 'no source collections discovered')

    def test_nothing_crashes(self):
        """An unexpected exception is a defect. Declines are not."""
        for d, (rows, _) in self.results.items():
            self.assertEqual(rows['error'], [],
                             'unexpected errors in %s: %s'
                             % (d, rows['error'][:3]))

    def test_no_validation_failures(self):
        for d, (rows, _) in self.results.items():
            self.assertEqual(rows['validate_fail'], [],
                             'structural validation failed for %s: %s'
                             % (d, rows['validate_fail'][:3]))

    def test_every_decline_has_a_known_cause(self):
        """A decline must be explainable. 'other' means something declined
        for a reason nobody has written down, which is how a bad gate hides."""
        allowed = {'feedback buffer', 'HLSL / bundled texture', 'multi-image input',
                   'bundled image (IMPORTED)', 'engine var redeclared',
                   'no entry point', 'no ISF INPUTS'}
        for d, (_, verdicts) in self.results.items():
            causes = Counter(verdicts.values())
            unknown = set(causes) - allowed
            self.assertFalse(unknown, 'undeclared decline cause(s) %s in %s'
                             % (unknown, d))

    def test_prose_is_not_counted_as_a_source(self):
        """Collections ship readme.txt; counting it as a shader produced a
        spurious 'no entry point' decline."""
        for d in self.cols:
            for f in sources(d):
                stem = os.path.splitext(os.path.basename(f))[0].lower()
                self.assertNotIn('readme', stem,
                                 'prose counted as a source: %s' % f)

    def test_multi_image_inputs_always_declined(self):
        """Any shader declaring >1 image input must be declined, never
        converted into GLSL that cannot compile."""
        from core.detector import detect_format
        from core.generator import convert, ConversionError
        import json as _json
        import re as _re
        for d in self.cols:
            for f in sources(d):
                s = open(f, encoding='utf-8-sig').read()
                m = _re.search(r'/\*(.*?)\*/', s, _re.DOTALL)
                if not m:
                    continue
                try:
                    n = sum(1 for i in _json.loads(m.group(1).strip())
                            .get('INPUTS', []) if i.get('TYPE') == 'image')
                except Exception:  # noqa: BLE001
                    continue
                if n < 2:
                    continue
                with self.subTest(src=os.path.relpath(f, d)):
                    with self.assertRaises(ConversionError):
                        convert(s, detect_format(s)['format'], 'p')


if __name__ == '__main__':
    unittest.main(verbosity=2)
