/*
{
    "author": "VS-2 Convert (hand-flattened)",
    "color": "#ffffff",
    "movement": true,
    "parameters": [],
    "url": "",
    "uuid": "00000000-0000-4000-8000-000000000001"
}
*/

#ifdef GL_ES
precision highp float;
#endif

// SOURCE: IM-FractalSpeedSmooth.fs (VDMX collection), flattened from 2 passes.
//
// WHAT CHANGED AND WHY.
// The source has two passes. Pass 0 is a 1x1 PERSISTENT float buffer that
// integrates time every frame:
//
//     newTime = accumulatedTime + speedControl * TIMEDELTA
//
// Summing that telescopes to  speedControl * elapsed_seconds, and VS-2's
// `time` IS elapsed seconds. So the buffer never held anything the engine
// does not already provide, and the two-pass feedback is analytically
// reducible rather than a genuine cross-frame dependency.
//
//     accumulatedTime  ->  1.0 * time  ->  time
//
// speedControl (default 1.0) is baked, not exposed: VS-2 hard rule 4 forbids a
// custom speed parameter because the engine already modulates `time`.
//
// Only PASSINDEX==1 survives; it is the pass that draws. PASSINDEX==0, the
// buffer, FRAMEINDEX and TIMEDELTA are gone with it.
//
// CONFIDENCE: high for this shader, because the reduction is exact rather
// than an approximation. That is NOT true of every PERSISTENT buffer - a
// buffer holding genuine visual history (a trail, a fluid sim) cannot be
// reduced this way and the app still declines those.

#define R(p,a,r) mix(a*dot(p,a),p,cos(r))+sin(r)*cross(p,a)
#define H(t) (cos((vec3(0.0, 2.0, -2.0)/3.0+t)*6.2831853)*0.5+0.5)
#define D(a, l_dist, effTime) (length(vec2(fract(log(length(a.xy)) - effTime * 0.5) - 0.5, a.z)) / 3.0 - 0.005 * pow(l_dist, 0.03))

void main()
{
    vec4 srcFragColor = vec4(0.0);

    // Accumulated time, reduced analytically (see header).
    float effectiveTime = time;

    vec2 fragCoords = gl_FragCoord.xy;

    float total_dist = 0.0;
    float current_l = 0.0;
    float step_dist = 0.0;

    for(int i = 1; i < 99; ++i)
    {
        vec3 dir = normalize(vec3((fragCoords - 0.5 * resolution.xy) / resolution.y, 1.0));
        vec3 p = R(total_dist * dir - vec3(0.0, 0.0, 6.0), normalize(vec3(1.0, 2.0, 0.0)), effectiveTime * 0.2);

        current_l = length(p);

        step_dist = min(min(D(p, current_l, effectiveTime), D(p.zxy, current_l, effectiveTime)), D(p.yzx, current_l, effectiveTime));

        total_dist += step_dist;

        if(step_dist < 0.005)
        {
            srcFragColor.rgb += mix(vec3(1.0), H(current_l), 0.7) / float(i);
        }

        if (total_dist > 100.0 || step_dist < 0.0001) break;
    }

    srcFragColor.a = 1.0;

    {
    vec3 vs2Final = srcFragColor.rgb;
    float coloredPixels = dot(clamp(vs2Final, 0.0, 1.0), vec3(1.0));
    fragColor = vec4(vs2Final * color.rgb, alpha * coloredPixels);
}
}
