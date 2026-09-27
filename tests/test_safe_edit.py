"""safe_edit must fail loudly on every edit failure seen in this repo.

Each test below reproduces a real incident. They are not hypothetical:

  - the duplicate-definition case is what left 638 lines of dead code in
    generator.py, because a re-run appended a second _convert_isf and
    apply_mutation() patches the FIRST occurrence;
  - the tab/space case is a test fixture whose str.replace matched nothing,
    returned the input unchanged, and was written anyway;
  - the unparseable case is a mutation entry written with nested quotes.

The point of these tests is the assertion AFTER the call: a failed edit must
leave the file byte-identical. Raising is only half of it - an edit that
raises after writing has still corrupted the file.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from safe_edit import (EditError, edit, insert_before,  # noqa: E402
                      verify_clean, write)


class SafeEditTests(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def mk(self, name, content):
        p = os.path.join(self.dir, name)
        with open(p, 'w') as fh:
            fh.write(content)
        return p

    def assertRaisesAndLeavesFileIntact(self, path, fn):
        """The core contract: raise AND do not touch the file."""
        with open(path) as fh:
            before = fh.read()
        with self.assertRaises(EditError):
            fn()
        with open(path) as fh:
            after = fh.read()
        self.assertEqual(before, after,
                         'the file changed even though the edit raised')

    # ---- incident 1: the needle occurs more than once ----
    # This is the dead-code bug. Patching the first of several matches is
    # how a duplicate definition survived review and seven mutations ended
    # up editing a function that could not run.
    def test_ambiguous_needle_is_rejected(self):
        p = self.mk('a.py', 'X = 1\nX = 1\nX = 1\n')
        self.assertRaisesAndLeavesFileIntact(
            p, lambda: edit(p, 'X = 1', 'X = 2'))

    def test_explicit_dupe_opt_in_still_works(self):
        p = self.mk('b.py', 'X = 1\nX = 1\n')
        edit(p, 'X = 1', 'X = 2', allow_dupes=True)
        with open(p) as fh:
            self.assertEqual('X = 2\nX = 2\n', fh.read())

    def test_expect_zero_asserts_absence(self):
        p = self.mk('c.py', 'X = 1\n')
        edit(p, 'NOT_PRESENT', 'whatever', expect=0)

    # ---- incident 2: tab/space mismatch, a silent no-op ----
    # str.replace returns the input unchanged when the needle is absent, so
    # the old code wrote back the original and reported success.
    def test_tab_space_mismatch_is_rejected(self):
        p = self.mk('t.frag', 'void main() {\n\tfloat a = 1.0;\n}\n')
        self.assertRaisesAndLeavesFileIntact(
            p, lambda: edit(p, '    float a = 1.0;', '    float a = 2.0;'))

    # ---- incident 3: nested quotes produce an unparseable file ----
    def test_unparseable_result_is_rejected(self):
        p = self.mk('q.py', 'X = 1\n')
        self.assertRaisesAndLeavesFileIntact(
            p, lambda: edit(p, 'X = 1', "Y = 'it\'s broken'"))

    # ---- incident 4: a replacement that changes nothing ----
    def test_no_op_replacement_is_rejected(self):
        p = self.mk('d.py', 'X = 1\n')
        self.assertRaisesAndLeavesFileIntact(
            p, lambda: edit(p, 'X = 1', 'X = 1'))

    # ---- incident 5: missing file ----
    def test_missing_file_is_rejected(self):
        with self.assertRaises(EditError):
            edit(os.path.join(self.dir, 'nope.py'), 'a', 'b')

    # ---- the append path is the one that caused the worst damage ----
    def test_insert_before_requires_a_unique_anchor(self):
        p = self.mk('e.py', 'Z = 9\n')
        self.assertRaisesAndLeavesFileIntact(
            p, lambda: insert_before(p, 'NOT_PRESENT', 'NEW = 1'))

    def test_insert_before_places_text_at_the_anchor(self):
        p = self.mk('f.py', 'A = 1\nZ = 9\n')
        insert_before(p, 'Z = 9', 'MIDDLE = 2')
        with open(p) as fh:
            self.assertEqual('A = 1\nMIDDLE = 2\nZ = 9\n', fh.read())

    # ---- non-Python artefacts skip the AST check but keep balance ----
    def test_frag_edit_does_not_require_ast_parse(self):
        p = self.mk('g.frag', 'void main() { }\n')
        edit(p, '{ }', '{ int x; }')

    def test_write_parse_checks_python(self):
        p = os.path.join(self.dir, 'h.py')
        with self.assertRaises(EditError):
            write(p, 'def broken(:\n')
        self.assertFalse(os.path.exists(p), 'wrote an unparseable file')

    def test_write_allows_valid_content(self):
        p = os.path.join(self.dir, 'i.py')
        write(p, 'X = 1\n')
        with open(p) as fh:
            self.assertEqual('X = 1\n', fh.read())

    # ---- forgotten-edit detection ----
    # While proving these guards I ran an allow_dupes edit against
    # generator.py and it survived as a live one-line change. Nothing
    # objected, because a stray edit to real source is indistinguishable
    # from an intended one. verify_clean exists for exactly that.
    def _git_repo(self, files):
        """A throwaway git repo. Never assert against the real tree: a test
        that depends on repo state is a test that breaks when the tree is
        left edited, which is the situation this whole module addresses."""
        d = tempfile.mkdtemp()
        repo = os.path.join(d, 'repo')
        os.makedirs(repo)
        for name, content in files.items():
            full = os.path.join(repo, name)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, 'w') as fh:
                fh.write(content)
        run = lambda *a: subprocess.run(a, cwd=repo, capture_output=True)
        run('git', 'init', '-q')
        run('git', 'config', 'user.email', 't@t')
        run('git', 'config', 'user.name', 't')
        run('git', 'add', '.')
        run('git', 'commit', '-qm', 'base')
        return d, repo

    def test_verify_clean_flags_a_dirty_tracked_file(self):
        d, repo = self._git_repo({'sub/a.py': 'X = 1\n'})
        try:
            target = os.path.join(repo, 'sub', 'a.py')
            self.assertTrue(verify_clean([target], repo_root=repo)[0],
                            'a freshly committed file is clean')
            with open(target, 'w') as fh:      # a forgotten scratch edit
                fh.write('X = 2\n')
            clean, dirty = verify_clean([target], repo_root=repo)
            self.assertFalse(clean, 'a modified tracked file must be dirty')
            self.assertTrue(any('a.py' in x for x in dirty))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_verify_clean_flags_an_untracked_file(self):
        # git diff reports nothing for an untracked file, so it silently
        # reads as clean unless checked explicitly.
        d = tempfile.mkdtemp()
        try:
            repo = os.path.join(d, 'repo')
            os.makedirs(os.path.join(repo, 'sub'))
            env_dir = os.path.join(repo, 'sub')
            subprocess.run(['git', 'init', '-q', repo], capture_output=True)
            subprocess.run(['git', '-C', repo, 'config', 'user.email', 't@t'],
                           capture_output=True)
            subprocess.run(['git', '-C', repo, 'config', 'user.name', 't'],
                           capture_output=True)
            tracked = os.path.join(env_dir, 'tracked.py')
            open(tracked, 'w').write('X = 1\n')
            subprocess.run(['git', '-C', repo, 'add', '.'], capture_output=True)
            subprocess.run(['git', '-C', repo, 'commit', '-qm', 'x'],
                           capture_output=True)
            self.assertTrue(verify_clean([tracked], repo_root=repo)[0])

            fresh = os.path.join(env_dir, 'fresh.py')
            open(fresh, 'w').write('X = 1\n')
            clean, dirty = verify_clean([fresh], repo_root=repo)
            self.assertFalse(clean, 'an untracked file must not read as clean')
        finally:
            shutil.rmtree(d, ignore_errors=True)


    # ---- the happy path, so the guards are not just failing everything ----
    def test_valid_edit_applies(self):
        p = self.mk('j.py', 'X = 1\n')
        edit(p, 'X = 1', 'X = 2')
        with open(p) as fh:
            self.assertEqual('X = 2\n', fh.read())


if __name__ == '__main__':
    unittest.main()
