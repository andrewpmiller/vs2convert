# Tests

```sh
python -m pytest            # whole suite
python -m pytest -q         # same, quieter
```

The suite is self-contained: it needs only the package and its `dev` extra
(`pip install -e ".[dev]"`). Nothing here walks above this repository, reads a
private corpus, or invokes an external validator.

## What is covered

| file | what it pins |
|---|---|
| `test_api.py` | `convert()` returns a `ConversionResult` for every input, including malformed ones; gates are registered; force/validate flags |
| `test_cli.py` | exit codes, output paths, stdin, `--verify` degradation, verdict shape, ASCII mode |
| `test_engine.py` | source-shape behaviour: detection, ISF/Shadertoy/GLSLSandbox conversion, aspect, uniforms, feedback |
| `test_gates.py` | every refusal has evidence, an `allows` case, a positive control, and a reachable id |
| `test_pipeline_paths.py` | renderer input selection and output-path handling |
| `test_reference_parity.py` | the 3 curated ISF↔expert pairs and the expert aspect convention |
| `test_scope_helpers.py` | comment stripping / code-only scoping helpers |
| `test_shadertoy_meta.py` | sidecar discovery, precedence, and verdict changes from `docs/shadertoy.md` |
| `test_validator.py` | structural validator rules and messages |

Fixtures live beside `test_engine.py` (`.shadertoy` / `.fragshadertoy` bodies)
and under `tests/fixtures/reference/` (provenance in its README).

## What stayed private, and why

The carve plan (WP6) ported the conversion suite and deliberately dropped
these. They test the private repo's process, not the public package:

| dropped | why |
|---|---|
| `test_safe_edit.py` | repo-editing tooling; `safe_edit.py` remains, used by maintainers |
| `test_claim_linter.py` | conduct/documentation linting over private git history |
| `test_gate_audit.py` | mutation audit that edits source files; it refuses to run standalone by design |
| `test_isf_collections.py`, `test_isf_corpus.py` | oracle over the private shader corpus (`ISF-shaders-main/`, `converted_shaders/`) |
| `test_shader_pipeline.py` | imported `shader-pipeline/`, a private render harness |
| 14 of 17 reference pairs | private corpus material; 3 smallest pairs travel, the rest do not |
| whole-corpus `gl_FragCoord` check | requires the 48-source corpus (incl. gitignored collections) |

Also not ported, for the same reason: `mutants.py`, `run_mutation_tests.py`,
`gate_audit_baseline.txt`, `run_gate_audit.sh`, `claim_linter*`,
`corpus_scan.py`, `run_coverage.sh`, `coverage_breakdown.py`, `hand_flattened/`
and the `real_shadertoy_*` fixtures.

If you need corpus-level coverage, run it in the private repo where the oracle
and the collections exist. This suite stays green without them.
