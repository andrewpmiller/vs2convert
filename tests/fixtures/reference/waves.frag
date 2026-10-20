/*
{
    "author": "VS-2 Shader Expert",
    "color": "#ffffff",
    "movement": true,
    "parameters": [
        {
            "name": "warp_intensity",
            "default": 0.2
        }
    ],
    "url": "",
    "uuid": "5b3e7a2c-8f1d-4e9a-9c0b-1d2e3f4a5b6c"
}
*/

#ifdef GL_ES
precision highp float;
#endif

void main() {
    // Standard VS-2 coordinate setup
    float div = resolution.y / resolution.x;
    vec2 aspect = vec2(1.0, div);
    vec2 uv = texCoord * aspect;
    uv -= vec2(0.5, 0.5 * div);

    // Remapped parameter: warp_intensity [0.5, 3.0] -> knob [0,1], default 1.0 -> 0.2
    float warpMag = 0.5 + warp_intensity * 2.5;

    // Baked twist rate from ISF default (twist_speed=0.8), using native time (engine speed-modulated)
    float timeLocal = time * 0.8;

    // Apply rotation
    uv *= mat2(cos(timeLocal), -sin(timeLocal), sin(timeLocal), cos(timeLocal));

    // Kaleidoscopic fractal wrap
    uv = fract(uv * warpMag) - 0.5;

    // Distance from origin
    float dist = length(uv);

    // Wave pattern: sin(PI * 4.0 * dist + time * 2.0)
    float wave = sin(3.141592653589793 * 4.0 * dist + time * 2.0);

    // Color mapping: vec3(0.5*wave+0.5, 0.3*wave+0.7, 0.8*wave+0.2)
    vec3 baseColor = vec3(0.5 * wave + 0.5, 0.3 * wave + 0.7, 0.8 * wave + 0.2);

    // VS-2 standard output with coloredPixels transparency
    vec3 finalColor = baseColor;
    float coloredPixels = dot(clamp(finalColor, 0.0, 1.0), vec3(1.0));
    fragColor = vec4(finalColor * color.rgb, alpha * coloredPixels);
}