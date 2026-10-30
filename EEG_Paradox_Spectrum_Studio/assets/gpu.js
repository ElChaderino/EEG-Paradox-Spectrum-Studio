/* SPDX-License-Identifier: GPL-3.0-only
 * Static instanced EEG curtains and a GPU waterfall sharing one data texture.
 * Shader derivation/provenance: NOTICE.md. No temporal amplitude interpolation.
 */
'use strict';
window.SpectrumGPU = class SpectrumGPU {
  constructor(canvas, sources) {
    this.canvas = canvas;
    const gl = canvas.getContext('webgl2', {antialias:true,alpha:false,depth:true,preserveDrawingBuffer:false,powerPreference:'high-performance'});
    if (!gl) throw new Error('WebGL2 unavailable');
    this.gl = gl;
    this.stats = {frames:0,drawCalls:0,lastFrameDrawCalls:0,historyUploads:0,paletteUploads:0,meshUploads:0,canvasResizes:0,historyBytesUploaded:0};
    const shader = (type, text) => {
      const s=gl.createShader(type);gl.shaderSource(s,text);gl.compileShader(s);
      if(!gl.getShaderParameter(s,gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      return s;
    };
    const program=(vertex,fragment) => {
      const p=gl.createProgram(),v=shader(gl.VERTEX_SHADER,vertex),f=shader(gl.FRAGMENT_SHADER,fragment);
      gl.attachShader(p,v);gl.attachShader(p,f);gl.linkProgram(p);gl.deleteShader(v);gl.deleteShader(f);
      if(!gl.getProgramParameter(p,gl.LINK_STATUS))throw new Error(gl.getProgramInfoLog(p));
      const locations=new Map();
      return {p,u:name=>{if(!locations.has(name))locations.set(name,gl.getUniformLocation(p,name));return locations.get(name);}};
    };
    this.ridge=program(sources.ridgeVertex,sources.ridgeFragment);
    this.water=program(sources.waterVertex,sources.waterFragment);
    const vao=(p,name,values) => {
      const a=gl.createVertexArray(),b=gl.createBuffer();gl.bindVertexArray(a);gl.bindBuffer(gl.ARRAY_BUFFER,b);
      if(values)gl.bufferData(gl.ARRAY_BUFFER,values,gl.STATIC_DRAW);
      const location=gl.getAttribLocation(p,name);gl.enableVertexAttribArray(location);gl.vertexAttribPointer(location,2,gl.FLOAT,false,0,0);
      return {a,b};
    };
    this.mesh=vao(this.ridge.p,'inVert');
    this.quad=vao(this.water.p,'position',new Float32Array([-1,-1,1,-1,-1,1,1,1]));
    this.history=gl.createTexture();this.lut=gl.createTexture();
    this.bindTexture(this.history,0,false);this.bindTexture(this.lut,1,true);
    gl.clearColor(0,0,0.018,1);gl.clearDepth(1);gl.depthFunc(gl.LEQUAL);
    for(const p of [this.ridge,this.water]){gl.useProgram(p.p);gl.uniform1i(p.u('history'),0);gl.uniform1i(p.u('palette'),1);}
    this.channel=-1;this.paletteKey='';this.meshKey='';this.layout=null;
    this.channelTextures=new Map();
  }
  bindTexture(texture,unit,linear) {
    const gl=this.gl;gl.activeTexture(gl.TEXTURE0+unit);gl.bindTexture(gl.TEXTURE_2D,texture);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,linear?gl.LINEAR:gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,linear?gl.LINEAR:gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
  }
  setLayout(w,h,dpr) {
    const width=Math.max(1,Math.round(w*dpr)),height=Math.max(1,Math.round(h*dpr));
    const ridgeCss=Math.round(h*0.46),axisCss=22,waterCss=Math.max(1,h-ridgeCss-axisCss);
    if(this.canvas.width!==width||this.canvas.height!==height){
      this.canvas.width=width;this.canvas.height=height;this.stats.canvasResizes++;
    }
    this.layout={w,h,dpr,width,height,ridgeCss,waterCss,axisCss,ridgePx:Math.round(ridgeCss*dpr),waterPx:Math.round(waterCss*dpr),axisPx:Math.round(axisCss*dpr)};
    return this.layout;
  }
  setChannel(channel,flat,columns,rows) {
    if(channel===this.channel)return;
    if(this.channelTextures.has(channel)){
      this.history=this.channelTextures.get(channel);this.channel=channel;return;
    }
    const gl=this.gl,limit=gl.getParameter(gl.MAX_TEXTURE_SIZE);
    if(columns>limit||rows>limit)throw new Error('Recording texture exceeds this GPU; use a shorter selection or larger hop');
    const pixels=new Float32Array(flat.length);
    for(let i=0;i<flat.length;i++)pixels[i]=Number.isFinite(flat[i])?flat[i]:-999;
    this.history=gl.createTexture();this.bindTexture(this.history,0,false);
    gl.texImage2D(gl.TEXTURE_2D,0,gl.R32F,columns,rows,0,gl.RED,gl.FLOAT,pixels);
    this.channel=channel;this.stats.historyUploads++;this.stats.historyBytesUploaded+=pixels.byteLength;
    this.channelTextures.set(channel,this.history);
    if(this.channelTextures.size>8){
      const oldest=this.channelTextures.keys().next().value;gl.deleteTexture(this.channelTextures.get(oldest));this.channelTextures.delete(oldest);
    }
  }
  setPalette(key,bytes) {
    if(key===this.paletteKey)return;
    const gl=this.gl;this.bindTexture(this.lut,1,true);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,256,1,0,gl.RGBA,gl.UNSIGNED_BYTE,bytes);
    this.paletteKey=key;this.stats.paletteUploads++;
  }
  setBins(lo,hi) {
    const key=lo+':'+hi;if(key===this.meshKey)return;
    this.columns=hi-lo+1;const vertices=new Float32Array(this.columns*4);
    for(let i=0;i<this.columns;i++)vertices.set([i/(this.columns-1),0,i/(this.columns-1),1],i*4);
    const gl=this.gl;gl.bindVertexArray(this.mesh.a);gl.bindBuffer(gl.ARRAY_BUFFER,this.mesh.b);gl.bufferData(gl.ARRAY_BUFFER,vertices,gl.STATIC_DRAW);
    this.meshKey=key;this.stats.meshUploads++;
  }
  render(state,phase,lo,hi,waterRows,viewport=null) {
    const gl=this.gl,L=this.layout;this.setBins(lo,hi);
    const x=viewport?Math.round(viewport.x*L.dpr):0,width=viewport?Math.round(viewport.w*L.dpr):L.width;
    gl.enable(gl.SCISSOR_TEST);gl.scissor(x,0,width,L.height);
    gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);
    this.bindTexture(this.history,0,false);this.bindTexture(this.lut,1,true);
    const rows=Math.min(state.depth,state.row+1);
    const uniforms=(p,count) => {
      gl.useProgram(p.p);
      gl.uniform1f(p.u('currentRow'),state.row);gl.uniform1f(p.u('phase'),phase);
      gl.uniform1f(p.u('visibleRows'),count);gl.uniform1f(p.u('lowBin'),lo);gl.uniform1f(p.u('highBin'),hi);
      gl.uniform1f(p.u('floorDb'),state.floor);gl.uniform1f(p.u('rangeDb'),state.range);
    };
    gl.viewport(x,L.height-L.ridgePx,width,L.ridgePx);gl.enable(gl.DEPTH_TEST);gl.depthMask(true);gl.disable(gl.BLEND);
    uniforms(this.ridge,state.depth);gl.uniform1f(this.ridge.u('lift'),state.lift);gl.uniform1f(this.ridge.u('depthSpan'),state.perspective);
    gl.uniform2f(this.ridge.u('viewport'),width,L.ridgePx);gl.bindVertexArray(this.mesh.a);
    gl.uniform1i(this.ridge.u('outline'),0);gl.drawArraysInstanced(gl.TRIANGLE_STRIP,0,this.columns*2,rows);
    gl.enable(gl.BLEND);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);gl.depthMask(false);
    gl.uniform1i(this.ridge.u('outline'),1);gl.drawArraysInstanced(gl.TRIANGLE_STRIP,0,this.columns*2,rows);
    gl.depthMask(true);gl.disable(gl.BLEND);gl.disable(gl.DEPTH_TEST);
    gl.viewport(x,0,width,L.waterPx);uniforms(this.water,waterRows);gl.bindVertexArray(this.quad.a);gl.drawArrays(gl.TRIANGLE_STRIP,0,4);
    gl.disable(gl.SCISSOR_TEST);
    this.stats.frames++;this.stats.drawCalls+=3;this.stats.lastFrameDrawCalls=3;
  }
};
