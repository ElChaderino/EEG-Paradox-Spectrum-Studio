#version 300 es
// SPDX-License-Identifier: GPL-3.0-only
// EEG/WebGL2 adaptation of AetherSDR dss_mesh.frag; see NOTICE.md.
precision highp float;
in float vLut;
in float vDepth;
in float vRibbon;
in float vValid;
uniform sampler2D palette;
uniform bool outline;
out vec4 fragColor;
void main(){
    if(vValid<0.5) discard;
    vec3 c=texture(palette,vec2(clamp(vLut,0.0,1.0),0.5)).rgb;
    c=mix(c,vec3(0.0,0.0,0.018),vDepth*0.16);
    if(outline){
        float coverage=1.0-smoothstep(0.1,1.0,abs(vRibbon));
        c=mix(c,vec3(0.67,0.82,1.0),0.24);
        fragColor=vec4(c,coverage*(0.13+0.25*(1.0-vDepth)));
    }else fragColor=vec4(c,1.0);
}
