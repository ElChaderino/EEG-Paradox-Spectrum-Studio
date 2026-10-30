#version 300 es
// SPDX-License-Identifier: GPL-3.0-only
precision highp float;
in vec2 uv;
uniform sampler2D history;
uniform sampler2D palette;
uniform float currentRow;
uniform float phase;
uniform float visibleRows;
uniform float lowBin;
uniform float highBin;
uniform float floorDb;
uniform float rangeDb;
out vec4 fragColor;
void main(){
    // Exact temporal rows: smooth geometric scrolling, no BAD-gap interpolation.
    int age=max(0,int(floor(uv.y*visibleRows-phase)));
    int row=int(currentRow)-age;
    if(row<0){fragColor=vec4(0.0,0.0,0.018,1.0);return;}
    int col=int(round(mix(lowBin,highBin,uv.x)));
    float db=texelFetch(history,ivec2(col,row),0).r;
    if(db < -500.0){fragColor=vec4(0.08,0.095,0.12,1.0);return;}
    float level=clamp((db-floorDb)/max(rangeDb,1.0),0.0,1.0);
    fragColor=vec4(texture(palette,vec2(level,0.5)).rgb,1.0);
}
