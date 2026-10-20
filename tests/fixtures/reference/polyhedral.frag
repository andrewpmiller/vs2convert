/*
{
    "author": "VS-2 Shader Expert",
    "color": "#ffffff",
    "movement": true,
    "parameters": [
        {
            "name": "morph",
            "default": 0.25
        },
        {
            "name": "color_pulse",
            "default": 0.5
        },
        {
            "name": "tiling",
            "default": 0.3333
        }
    ],
    "url": "",
    "uuid": "5d1b8e3a-6c9f-4e2b-a7d4-3c8e9f1a2b5d"
}
*/

#ifdef GL_ES
precision highp float;
#endif

void main() {
    // Aspect: engine convention (div = y/x); matches source framing on square canvases
    float div = resolution.y / resolution.x;
    vec2 uv = (texCoord - 0.5) * vec2(1.0, div);

    // morph knob: source MorphStrength [0.0, 2.0], default 0.5 -> 0.25
    float morphVal = mix(0.0, 2.0, morph);
    // color_pulse knob: source ColorPulse [0.0, 1.0], default 0.5 -> 0.5
    // (R-channel phase density in the source — NOT a time rate, so it stays a knob)
    float pulseVal = mix(0.0, 1.0, color_pulse);
    // tiling knob: source GridTiling [1.0, 4.0], default 2.0 -> 0.3333
    float tilingVal = mix(1.0, 4.0, tiling);

    // Baked source default: Speed 1.0 (time-rate control, now on native time)
    vec3 baseColor = vec3(0.0);

    for (float i = 0.0; i < 5.0; i++) {
        uv = fract(uv * tilingVal) - 0.5;
        float d = length(uv) * (1.5 + morphVal * sin(time * 0.8));
        baseColor += vec3(
            sin(time + d * pulseVal),
            cos(time + d * 1.5),
            sin(time + d * 2.5)
        ) * exp(-d * 2.0);
    }

    // VS-2 standard output with coloredPixels transparency
    vec3 finalColor = baseColor;
    float coloredPixels = dot(clamp(finalColor, 0.0, 1.0), vec3(1.0));
    fragColor = vec4(finalColor * color.rgb, alpha * coloredPixels);
}