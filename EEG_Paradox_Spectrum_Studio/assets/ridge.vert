#version 300 es
// SPDX-License-Identifier: GPL-3.0-only
// EEG/WebGL2 adaptation of AetherSDR dss_mesh.vert; see NOTICE.md.
precision highp float;
in vec2 inVert;
uniform sampler2D history;
uniform float currentRow;
uniform float phase;
uniform float visibleRows;
uniform float floorDb;
uniform float rangeDb;
uniform float lift;
uniform float depthSpan;
uniform float lowBin;
uniform float highBin;
uniform vec2 viewport;
uniform bool outline;
out float vLut;
out float vDepth;
out float vRibbon;
out float vValid;
float sampleDb(float x,int row){
    float col=mix(lowBin,highBin,clamp(x,0.0,1.0));
    int a=int(floor(col));
    float left=texelFetch(history,ivec2(a,row),0).r;
    float right=texelFetch(history,ivec2(min(a+1,int(highBin)),row),0).r;
    return mix(left,right,fract(col));
}
vec2 ridgeAt(float x,float db,float depth){
    float width=mix(1.0,0.60,depth);
    float strength=clamp((db-floorDb)/max(rangeDb,1.0),0.0,1.0);
    float y=0.985-depth*depthSpan-pow(strength,max(lift,0.05))*0.46*width;
    return vec2((0.5+(x-0.5)*width)*2.0-1.0,1.0-y*2.0);
}
void main(){
    // Front-to-back depth-tested curtains allow early fragment rejection.
    float age=float(gl_InstanceID);
    int row=int(currentRow-age);
    float depth=clamp((age+phase)/max(visibleRows-1.0,1.0),0.0,1.0);
    float db=sampleDb(inVert.x,max(row,0));
    vValid=row>=0 && db>-500.0 ? 1.0 : 0.0;
    vec2 ndc=ridgeAt(inVert.x,db,depth);
    float strength=clamp((db-floorDb)/max(rangeDb,1.0),0.0,1.0);
    if(outline){
        float du=1.0/max(highBin-lowBin,1.0);
        vec2 before=ridgeAt(max(0.0,inVert.x-du),sampleDb(max(0.0,inVert.x-du),max(row,0)),depth);
        vec2 after=ridgeAt(min(1.0,inVert.x+du),sampleDb(min(1.0,inVert.x+du),max(row,0)),depth);
        vec2 tangent=(after-before)*viewport;
        vec2 normal=length(tangent)>0.001 ? normalize(vec2(-tangent.y,tangent.x)) : vec2(0.0,1.0);
        vRibbon=inVert.y*2.0-1.0;
        ndc+=normal*vRibbon*1.65/max(viewport,vec2(1.0));
        vLut=strength;
    }else{
        // Original AetherSDR curtains descend to the plot floor.
        if(inVert.y>0.5) ndc.y=-0.97;
        vRibbon=0.0;
        vLut=inVert.y>0.5 ? strength*0.60 : strength;
    }
    gl_Position=vec4(ndc,depth*1.6-0.8-(outline?0.0001:0.0),1.0);
    vDepth=depth;
}
