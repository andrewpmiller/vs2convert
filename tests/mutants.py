"""Declarative mutation catalogue for vs2_convert.

A MUTATION is a deliberate, minimal defect injected into source code. If the
test suite still passes with the defect present, the test suite is not
actually guarding that behaviour.

Why this exists
---------------
A review layer rated three findings High/Critical. One turned out to be a
no-op, two were outright false, and one of the false ones carried fabricated
evidence that was never executed. Review prose did not catch any of that.
Mutation testing does: it is executable, it is repeatable, and it does not
depend on the reviewer's judgment being calibrated.

The point is NOT to prove the code is good. It is to find the assertions that
pass no matter what, which are worse than no assertions at all because they
report false confidence.

Adding a mutation
-----------------
Append a Mutation(...) to MUTATIONS. Use the `selector` field to scope which
test must fail, so an unrelated failing test cannot mask a survivor.

Equivalent mutants
------------------
Some mutations cannot be killed: the change is a behavioural no-op. Those are
still worth recording with equivalent=True, because "we know this is a no-op
and here's why" is a claim worth pinning - see the twist_speed entry.
"""


class Mutation:
    def __init__(self, name, path, find, replace, selector=None,
                 equivalent=False, why=''):
        self.name = name
        self.path = path
        self.find = find
        self.replace = replace
        # Dotted unittest target, e.g. 'test_validator.CorpusParityTests'.
        # None = run the whole suite.
        self.selector = selector
        # True when the mutation is a behavioural no-op and cannot be killed.
        self.equivalent = equivalent
        self.why = why

    def __repr__(self):
        return '<Mutation %s>' % self.name


MUTATIONS = [
    # ---- validator: the uniform/varying rule ---------------------------
    Mutation(
        name='validator: allow custom uniforms',
        path='validators/validator.py',
        find="decl_re = re.compile(r'^\\s*(uniform|varying)\\s+')",
        replace="decl_re = re.compile(r'^\\s*uniform\\s+resolution\\s+')",
        selector='test_validator.UniformRuleAdjudicationTests',
        why=('The change a review asked for. It is a real regression: VS-2 '
             'injects its own uniforms, so a custom declaration shadows '
             'engine state. See test_custom_uniform_is_an_error_not_a_pass.'),
    ),
    Mutation(
        name='validator: drop varying from the check',
        path='validators/validator.py',
        find="decl_re = re.compile(r'^\\s*(uniform|varying)\\s+')",
        replace="decl_re = re.compile(r'^\\s*uniform\\s+')",
        selector='test_validator.UniformRuleAdjudicationTests',
        why='Reintroduces the pre-fix gap: `varying` declarations slip through.',
    ),
    Mutation(
        name='validator: anchor uniform check at column 0',
        path='validators/validator.py',
        find="decl_re = re.compile(r'^\\s*(uniform|varying)\\s+')",
        replace="decl_re = re.compile(r'^(uniform|varying)\\s+')",
        selector='test_validator.UniformRuleAdjudicationTests',
        why='Indented declarations become invisible again.',
    ),

    # ---- validator: speed keyword detection ---------------------------
    Mutation(
        name='validator: speed check reverts to whole-file regex',
        path='validators/validator.py',
        find=("    speed_patterns = ['speed', 'rate', 'frequency', 'freq', "
              "'twist_speed']\n"
              "    for data in _iter_manifests(content):\n"
              "        if 'parameters' not in data:\n"
              "            continue\n"
              "        for p in data.get('parameters', []):\n"
              "            name = p.get('name', '')\n"
              "            if any(kw in name.lower() for kw in speed_patterns):\n"
              "                return True\n"
              "    return False"),
        replace=("    return bool(re.search(r'\\bspeed\\b', content))"),
        selector='test_validator.SpeedKeywordParityTests',
        why=('The pre-fix bug: the word "speed" in a comment or identifier '
             'raised a false positive. Mutated whole, not just the keyword '
             'list, so the body-level check is exercised too.'),
    ),
    Mutation(
        name='validator: drop twist_speed keyword',
        path='validators/validator.py',
        find="speed_patterns = ['speed', 'rate', 'frequency', 'freq', 'twist_speed']",
        replace="speed_patterns = ['speed', 'rate', 'frequency', 'freq']",
        selector='test_validator.SpeedKeywordParityTests',
        equivalent=True,
        why=('Rated High by a review. It is a NO-OP: "speed" is a substring '
             'of "twist_speed", so any name matching the longer keyword '
             'already matches the shorter one. Cannot be killed by a '
             'behaviour test. Caught instead by the structural superset '
             'assertion, which is why that assertion exists.'),
    ),

    # ---- generator: the knob remap that magnitude fidelity depends on --
    Mutation(
        name='generator: skew knob normalisation by +0.01',
        path='core/generator.py',
        find="knob = min(1.0, max(0.0, (dflt - lo) / (hi - lo)))",
        replace="knob = min(1.0, max(0.0, (dflt - lo) / (hi - lo))) + 0.01",
        selector='test_engine.ConvertIsfTests',
        why=('Silently drifts every converted default. Renders 3.05 where '
             'the source rendered 3.0. Invisible to the string-matching '
             'test_knob_range_remap, which is the whole reason '
             'test_magnitude_fidelity_round_trip was added.'),
    ),
    Mutation(
        name='generator: drop the 7-parameter budget',
        path='core/generator.py',
        find='if len(manifest) >= MAX_PARAMS:',
        replace='if False:',
        selector='test_engine',
        why='Regresses hard rule 2 (max 7 manifest parameters).',
    ),

    # ---- mapper -------------------------------------------------------
    Mutation(
        name='mapper: drop twist_speed from speed_keywords',
        path='core/mapper.py',
        find="speed_keywords = ['speed', 'rate', 'frequency', 'freq', 'twist_speed']",
        replace="speed_keywords = ['speed', 'rate', 'frequency', 'freq']",
        selector='test_validator.SpeedKeywordParityTests',
        equivalent=True,
        why=('Same no-op as the validator side. Unkillable by behaviour; the '
             'structural superset assertion covers it.'),
    ),
]
