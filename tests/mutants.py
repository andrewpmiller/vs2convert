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

    # ---- detector: the suitability gate --------------------------------
    Mutation(
        name='detector: suitability verdict always True',
        path='core/detector.py',
        find='    result["suitable"] = len(result["errors"]) == 0',
        replace='    result["suitable"] = True',
        selector='test_validator.SuitabilityGateTests',
        why=('The original defect: the CLI computed a verdict and ignored it, '
             'so a shader redeclaring `resolution` was written out as a .frag '
             'shadowing the engine uniform. Pinning suitable=True reopens '
             'that hole.'),
    ),
    Mutation(
        name='detector: decline any iChannel again',
        path='core/detector.py',
        find="    if len(_channels_used) > 1:",
        replace="    if len(_channels_used) >= 1:",
        selector='test_validator.SuitabilityGateTests',
        why=('Reinstates the blanket image-input decline. VS-2 binds the '
             'layer image to imageTex and previews these shaders in the '
             'gallery, so a single-channel image shader converts fine - '
             'verified end to end, render gate included.'),
    ),
    Mutation(
        name='detector: check HLSL in comments too',
        path='core/detector.py',
        find='    if HLSL_LERP_RE.search(code):',
        replace='    if HLSL_LERP_RE.search(source):',
        selector='test_validator.SuitabilityGateTests',
        why=('Reintroduces a false positive: conjugoth.frag documents its '
             'origin as "original: lerp(...)" in a comment, and scanning raw '
             'text declines a valid shader.'),
    ),

    # ---- generator: the ISF image-input path ---------------------------
    Mutation(
        name='generator: reject ISF image inputs again',
        path='core/generator.py',
        find="        if itype == 'image':",
        replace="        if itype == 'image':\n            raise ConversionError('nope')",
        selector='test_engine.HardGateTests',
        why=('Reinstates the wrong claim that the converter "would have to '
             'emit an image the user has not supplied". A TYPE:image input is '
             'the shader declaring it REQUIRES an image; VS-2 binds the layer '
             'image to imageTex. Nothing is emitted.'),
    ),
    Mutation(
        name='generator: put the image input in the manifest',
        path='core/generator.py',
        find="            seen_image = True\n            _image_inputs.append(str(name))\n            continue",
        replace="            seen_image = True\n            manifest.append({'name': _snake(name), 'default': 0.0})",
        selector='test_engine.HardGateTests',
        why=('formal-spec.md: "No image parameter is declared in the manifest. '
             'The image is implicit." An image is not a knob.'),
    ),

    # ---- researched ISF / Shadertoy behaviour ------------------------
    Mutation(
        name='generator: leave IMG_NORM_PIXEL in the output',
        path='core/generator.py',
        find="body = re.sub(r'\\bIMG_NORM_PIXEL\\s*\\(\\s*\\w+\\s*,', 'texture(imageTex,', body)",
        replace='pass',
        selector='test_engine.IsfImageSpecTests',
        why=('ISF host function, not GLSL: the canonical spec shader would '
             'convert to output that cannot compile, and the converter would '
             'report success. Found by researching docs.isf.video/ref_json.'),
    ),
    Mutation(
        name='generator: drop the UNVERIFIED disclosure for iChannel',
        path='core/generator.py',
        find='            "UNVERIFIED: iChannel%s is a Shadertoy binding slot, not a "',
        replace='            "iChannel%s -> imageTex. "',
        selector='test_engine.ShadertoyChannelUncertaintyTests',
        why=('A Shadertoy iChannel is a binding slot that may hold a buffer, '
             'cubemap, video or audio - unknowable from source. Converting '
             'silently would let a wrong guess read as verified.'),
    ),
    Mutation(
        name='generator: allow an ISF IMPORTED bundle through',
        path='core/generator.py',
        find='    if _imported:',
        replace='    if False:',
        selector='test_engine.IsfImageSpecTests',
        why=('IMPORTED names image files shipped with the .fs; VS-2 has '
             'nowhere to put them, so the blend would silently vanish.'),
    ),

    # ---- iMouse units: pixels, not normalized ------------------------
    Mutation(
        name='generator: emit iMouse as a bare normalized knob',
        path='core/generator.py',
        find="body = re.sub(r'\\biMouse\\.x\\b', '(x * resolution.x)', body)",
        replace="body = re.sub(r'\\biMouse\\.x\\b', 'x', body)",
        selector='test_engine',
        why=('Shadertoy iMouse.xy is in PIXELS; a VS-2 knob is 0..1. A real '
             'glitch shader did `0.1 + iMouse.x / iResolution.x`, and a bare x '
             'gives 0.5/1920 instead of 0.5 - a ~1000x error that still '
             'compiles, renders and validates clean.'),
    ),

    # ---- output transparency pattern: image vs procedural ------------
    Mutation(
        name='generator: use coloredPixels for image-input shaders',
        path='core/generator.py',
        find='    if _samples_image(source):',
        replace='    if False:',
        selector='test_engine',
        why=('coloredPixels derives alpha from pixel BRIGHTNESS, which on an '
             'image shader is ~1.0 almost everywhere: the shader renders as a '
             'solid rectangle and ignores the image transparency. All three '
             'factory-references/VS2-image-input/ shaders use col.a instead.'),
    ),
    Mutation(
        name='generator: drop the image alpha, hardcode 1.0',
        path='core/generator.py',
        find="        elif len(args) == 2:\n            alpha_expr = args[1]",
        replace="        elif len(args) == 2:\n            alpha_expr = '1.0'",
        selector='test_engine',
        why=('vec4(rgb, a) arrives as 2 args. Reading 1.0 instead of args[1] '
             'silently discarded the source alpha - which is precisely what a '
             'first attempt did, producing `1.0 * alpha`.'),
    ),

    # ---- image Y orientation -----------------------------------------
    Mutation(
        name='generator: drop the image Y flip',
        path='core/generator.py',
        find='        body = _flip_image_sample_v(body)',
        replace='        pass',
        selector='test_engine',
        why=('Shadertoy and VS-2 disagree about image V direction, so a '
             'converted image shader renders its image upside down in VS-2. '
             'Empirically confirmed by the author running a real Shadertoy '
             'glitch in VS-2 on 2026-09-25 - not documented in the spec.'),
    ),
    Mutation(
        name='generator: detect mouse axes in comments',
        path='core/generator.py',
        find='        _code = code_only(body)',
        replace='        _code = body',
        selector='test_engine',
        why=('The real glitch shader has "//note: /\\ t=[0;0.5;1], y=[0;1;0]" '
             '- a bare `y` in a comment. Scanning raw text added a phantom y '
             'knob the shader never uses.'),
    ),

    # ---- detector precedence and GLSLSandbox coverage ----------------
    Mutation(
        name='detector: let mainImage override an ISF manifest',
        path='core/detector.py',
        find='    if ISF_INPUTS_RE.search(source):',
        replace='    if ISF_INPUTS_RE.search(source) and not SHADERTOY_MAINIMAGE_RE.search(source):',
        selector='test_engine.DetectorPrecedenceTests',
        why=('ISF is self-describing: the INPUTS block outranks a coincidental '
             'mainImage. Restoring the old precedence made a Shadertoy-imported '
             'ISF lose its knobs and emit GLSL that would not compile.'),
    ),
    Mutation(
        name='detector: conflate ISF INPUTS with a VS-2 parameters block',
        path='core/detector.py',
        find='    elif SHADERTOY_MAINIMAGE_RE.search(source) or (SHADERTOY_TIME_RE.search(source) and not VS2_PARAMETERS_RE.search(source)):',
        replace='    elif SHADERTOY_MAINIMAGE_RE.search(source) or SHADERTOY_TIME_RE.search(source):',
        selector='test_engine.DetectorPrecedenceTests',
        why=('A Shadertoy .txt may carry a lowercase VS-2-style "parameters" '
             'manifest; conflating it with ISF "INPUTS" misroutes the file.'),
    ),
    Mutation(
        name='detector: stop detecting GLSLSandbox',
        path='core/detector.py',
        find='    elif GLSLSANDBOX_TIME_RE.search(source) or GLSLSANDBOX_RESOLUTION_RE.search(source):',
        replace='    elif False:',
        selector='test_engine.GLSLSandboxTests',
        why=('GLSLSandbox is advertised in --help. It had zero coverage until '
             'this suite; the one manual run that "verified" it was exactly '
             'the kind of unverified claim the tests exist to prevent.'),
    ),

    # ---- ISF image inputs, from the GLSLOP collection ----------------
    Mutation(
        name='generator: allow multiple ISF image inputs',
        path='core/generator.py',
        find='    if len(_image_inputs) > 1:',
        replace='    if False:',
        selector='test_engine.IsfImageInputTests',
        why=('N distinct image inputs collapsed onto one imageTex, leaving the '
             'body referencing samplers that were never declared - output that '
             'does not compile. Real case: GLSLOP developer-k0rz1pe2.fs with '
             'six image inputs.'),
    ),
    Mutation(
        name='corpus_scan: count readme.txt as a shader',
        path='tests/corpus_scan.py',
        find="            if any(h in stem for h in _PROSE_HINTS):\n                continue",
        replace='            pass',
        selector='test_isf_collections',
        why=('Collections ship readme.txt and similar; counting them as '
             'sources reported a documentation file as an unconvertible '
             'shader.'),
    ),
]
