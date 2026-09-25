"""Deterministic tests for vs2_convert.core.mapper JSON manifest extraction.

Run: python3 vs2_convert/tests/test_mapper.py
"""
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(TESTS_DIR))

from core.mapper import _extract_source_parameters, map_parameters

FIXTURE_MULTI = os.path.join(TESTS_DIR, 'test_shader_multi_comments.shadertoy')
FIXTURE_PARAMS = os.path.join(TESTS_DIR, 'test_shader_with_params.shadertoy')


def read(path):
    with open(path) as f:
        return f.read()


class ExtractSourceParametersTests(unittest.TestCase):
    def test_empty_source_returns_empty(self):
        self.assertEqual(_extract_source_parameters(''), {})

    def test_non_json_comments_skipped(self):
        src = '/* SPDX-License-Identifier: MIT */\n/* Copyright 2024 */\n'
        self.assertEqual(_extract_source_parameters(src), {})

    def test_invalid_json_skipped(self):
        src = '/* {"broken": true, "missing_colon" } */\n'
        self.assertEqual(_extract_source_parameters(src), {})

    def test_json_list_not_treated_as_manifest(self):
        src = '/* [1, 2, 3] */\n'
        self.assertEqual(_extract_source_parameters(src), {})

    def test_dict_without_parameters_key_ignored(self):
        src = '/* {"author": "Test", "color": "white"} */\n'
        self.assertEqual(_extract_source_parameters(src), {})

    def test_first_valid_manifest_wins(self):
        src = ('/* {"parameters": [{"name": "a"}]} */\n'
               '/* {"parameters": [{"name": "b"}]} */\n')
        params = _extract_source_parameters(src)
        self.assertIn('a', params)
        self.assertNotIn('b', params)

    def test_single_manifest_block(self):
        params = _extract_source_parameters(read(FIXTURE_PARAMS))
        self.assertEqual(set(params), {'detail', 'form', 'speed'})

    def test_multi_comment_blocks_manifest_found(self):
        params = _extract_source_parameters(read(FIXTURE_MULTI))
        self.assertEqual(set(params), {'detail', 'form', 'speed'})

    def test_uniform_fallback_glsl_order(self):
        # GLSL order is `uniform <type> <name>;`
        src = 'uniform float intensity;\nuniform vec2 offset;\n'
        params = _extract_source_parameters(src)
        self.assertEqual(set(params), {'intensity', 'offset'})


class MapParametersTests(unittest.TestCase):
    def test_speed_parameter_baked_not_knobbed(self):
        result = map_parameters(read(FIXTURE_MULTI))
        self.assertEqual([p.name for p in result.parameters],
                         ['detail', 'form'])
        self.assertTrue(result.speed_baked)
        self.assertTrue(any('speed' in c for c in result.baked_constants))

    def test_manifest_defaults_survive_mapping(self):
        result = map_parameters(read(FIXTURE_PARAMS))
        by_name = {p.name: p for p in result.parameters}
        self.assertEqual(by_name['detail'].default, 0.5)
        self.assertEqual(by_name['detail'].source_range, (0.0, 1.0))


if __name__ == '__main__':
    unittest.main(verbosity=2)