/*
{
    "author": "VS-2 Shader Expert",
    "color": "#ffffff",
    "movement": true,
    "parameters": [
        {
            "default": 0.2222,
            "name": "zoom"
        },
        {
            "default": 0.3103,
            "name": "color_intensity"
        }
    ],
    "url": "",
    "uuid": "d2ccb4e7-17e1-4088-89d2-1ad58a9cdb98"
}
*/

#ifdef GL_ES
precision highp float;
#endif

void main()
{
    float div = resolution.y / resolution.x;
    vec2 aspect = vec2(1.0, div);
    // y spans [-1, 1] at square, matching the source's (gl_FragCoord*2 - RENDERSIZE)/RENDERSIZE.y
    vec2 uv = (texCoord * 2.0 - 1.0) * aspect;

    // Magnitudes recovered in the body, from the ISF source's own ranges:
    //   zoom            source [0.5, 5.0], default 1.5 -> knob 0.2222
    //   colorIntensity  source [0.1, 3.0], default 1.0 -> knob 0.3103
    // Both defaults reproduce the source's on-load frame exactly.
    float zoomMag = mix(0.5, 5.0, zoom);
    float colorIntensityVal = mix(0.1, 3.0, color_intensity);

    vec3 baseColor = vec3(0.0);
    float globalDist = length(uv);

    for (float i = 0.0; i < 4.0; i++) {
        uv = fract(uv * zoomMag) * 2.0 - 1.0;
        float dist = length(uv) * globalDist;
        // epsilon guards the division from inf/NaN at dist == 0.5
        baseColor = 0.1 / (abs(vec3(dist) - 0.5) + 0.001);
        baseColor = abs(sin(baseColor + globalDist + dist * 2.0 + time));
        baseColor = pow(baseColor, vec3(colorIntensityVal));
    }

    // finalColor is pre-tint; the engine color is applied once at output
    vec3 finalColor = baseColor;
    float coloredPixels = dot(clamp(finalColor, 0.0, 1.0), vec3(1.0));

    fragColor = vec4(finalColor * color.rgb, alpha * coloredPixels);
}