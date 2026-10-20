"""The sidecar / inline metadata rules documented in docs/shadertoy.md."""
import json
import os
import shutil
import tempfile
import unittest

from vs2convert.api import convert
from vs2convert.core.shadertoy_meta import find_meta_file, load_meta

BODY = ('void mainImage(out vec4 f, in vec2 c){'
        ' f = vec4(texture(iChannel0, c / 9.0).rgb, 1.0); }')

TEXTURE_META = {'channels': {'iChannel0': {'sampler': 'texture'}}}


class SidecarDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)

    def _write(self, name, text='{}'):
        path = os.path.join(self.dir, name)
        with open(path, 'w') as f:
            f.write(text)
        return path

    def test_find_meta_file_none_for_stdin(self):
        self.assertIsNone(find_meta_file(None))

    def test_stem_match_wins(self):
        body = self._write('shader.shadertoy', BODY)
        wanted = self._write('shader.stmeta.json')
        self._write('other.stmeta.json')
        self.assertEqual(wanted, find_meta_file(body))

    def test_lone_sidecar_is_the_fallback(self):
        body = self._write('shader.shadertoy', BODY)
        lone = self._write('anything.stmeta.json')
        self.assertEqual(lone, find_meta_file(body))

    def test_two_unrelated_sidecars_are_ambiguous(self):
        body = self._write('shader.shadertoy', BODY)
        self._write('a.stmeta.json')
        self._write('b.stmeta.json')
        self.assertIsNone(find_meta_file(body))

    def test_precedence_explicit_then_sidecar_then_inline(self):
        body = self._write('shader.shadertoy', BODY)
        sidecar = self._write('shader.stmeta.json',
                              json.dumps({'channels': {'iChannel0': {
                                  'sampler': 'texture'}}}))
        explicit = self._write('explicit.json',
                               json.dumps({'channels': {'iChannel0': {
                                   'sampler': 'video'}}}))
        source = '/*{"channels": {"iChannel0": {"sampler": "2d"}}}*/' + BODY

        self.assertEqual(explicit, load_meta(body, source, explicit)[1])
        self.assertEqual(sidecar, load_meta(body, source)[1])
        inline = load_meta(None, source)
        self.assertEqual('inline block', inline[1])
        self.assertEqual('2d', inline[0]['channels']['iChannel0']['sampler'])

    def test_missing_explicit_file_raises(self):
        body = self._write('shader.shadertoy', BODY)
        with self.assertRaises(ValueError):
            load_meta(body, BODY, os.path.join(self.dir, 'absent.json'))


class VerdictTests(unittest.TestCase):
    def test_texture_sidecar_confirms_the_binding(self):
        r = convert(BODY, meta=TEXTURE_META)
        self.assertTrue(r.ok, r.rejections)
        self.assertFalse(any(w.startswith('UNVERIFIED') for w in r.warnings),
                         r.warnings)
        self.assertTrue(any('confirmed' in w for w in r.warnings), r.warnings)

    def test_no_metadata_is_unverified(self):
        r = convert(BODY)
        self.assertTrue(r.ok, r.rejections)
        self.assertTrue(any(w.startswith('UNVERIFIED') for w in r.warnings),
                        r.warnings)

    def test_buffer_channel_is_declined(self):
        r = convert(BODY, meta={'channels': {'iChannel0': {
            'sampler': 'buffer'}}})
        self.assertFalse(r.ok)
        self.assertIn('shadertoy-unrepresentable-channel',
                      [x.gate for x in r.rejections])

    def test_metadata_is_ignored_for_isf(self):
        """A sidecar sits next to an ISF file too, but ISF carries its own
        declarations, so a buffer declaration must not leak into it."""
        isf = ('/*{"INPUTS": [{"NAME": "Zoom", "TYPE": "float",'
               ' "DEFAULT": 0.5, "MIN": 0.0, "MAX": 1.0}]}*/\n'
               'void main() { gl_FragColor = vec4(vec3(Zoom), 1.0); }\n')
        r = convert(isf, meta={'channels': {'iChannel0': {
            'sampler': 'buffer'}}})
        self.assertTrue(r.ok, r.rejections)


if __name__ == '__main__':
    unittest.main()
