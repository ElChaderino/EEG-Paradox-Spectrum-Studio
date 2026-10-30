/* Offline DOM/GL command instrumentation. This does not render browser pixels. */
'use strict';
const fs=require('node:fs'),vm=require('node:vm');
function harness(html,{gpu=true,storage=null}={}){
  const downloads=[],blobs=new Map(),raf=new Map(),timers=new Map(),documentHandlers=new Map(),windowHandlers=new Map(),canvasText=[],canvasDraws=[];
  const counts={bufferUploads:0,textureUploads:0,draws:0,instancedDraws:0,scopePaints:0,waterfallImages:0};
  let clock=1000,nextId=1,objectId=1;
  const noop=()=>{};
  function ctx(id){return {setTransform:noop,fillRect:()=>{if(id==='scope')counts.scopePaints++;},clearRect:noop,
    fillText:(value,x,y)=>{if(canvasText.length<50000)canvasText.push({id,value,x,y});},beginPath:noop,moveTo:noop,lineTo:noop,stroke:noop,fill:noop,closePath:noop,
    save:noop,restore:noop,translate:noop,rotate:noop,drawImage:(...args)=>{if(canvasDraws.length<50000)canvasDraws.push({id,args:args.slice(1)});},putImageData:noop,rect:noop,clip:noop,arc:noop,ellipse:noop,setLineDash:noop,
    createImageData:(width,height)=>{counts.waterfallImages++;return {width,height,data:new Uint8ClampedArray(width*height*4)};}};}
  const gl=new Proxy({
    createShader:()=>({id:objectId++}),createProgram:()=>({id:objectId++}),createBuffer:()=>({id:objectId++}),createTexture:()=>({id:objectId++}),createVertexArray:()=>({id:objectId++}),
    getShaderParameter:()=>true,getProgramParameter:()=>true,getShaderInfoLog:()=>'',getProgramInfoLog:()=>'',getParameter:()=>16384,
    getUniformLocation:(_,name)=>name,getAttribLocation:()=>0,
    bufferData:()=>counts.bufferUploads++,texImage2D:()=>counts.textureUploads++,
    drawArrays:()=>counts.draws++,drawArraysInstanced:()=>{counts.draws++;counts.instancedDraws++;}
  },{get(target,name){if(name in target)return target[name];if(String(name)===String(name).toUpperCase())return String(name);return noop;}});
  function element(id='',tag=''){
    const events=new Map(),classes=new Set();
    const item={id,tag,style:{setProperty(key,value){this[key]=value;}},classList:{add:x=>classes.add(x),contains:x=>classes.has(x),toggle:(x,on)=>{const enabled=on===undefined?!classes.has(x):on;enabled?classes.add(x):classes.delete(x);return enabled;}},
      value:'',checked:false,disabled:false,hidden:false,children:[],textContent:'',
      append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;},remove:noop,setAttribute:noop,
      addEventListener:(type,fn)=>events.set(type,fn),fire(type,event={}){const fn=events.get(type);if(fn)return fn(event);},
      click(){if(this.download)downloads.push({name:this.download,url:this.href});this.fire('click');},
      showModal(){this.open=true;},close(){this.open=false;},
      setPointerCapture:noop,
      getBoundingClientRect:()=>({width:id==='stageAxes'?45:['scope','fft'].includes(id)?500:1000,
        height:['ridge','ridgeFallback','stage','stageAxes'].includes(id)?600:id==='waterfall'?302:140,left:0,top:id==='waterfall'?298:0}),
      getContext:type=>type==='webgl2'?(gpu?gl:null):ctx(id),toBlob:fn=>fn(new Blob(['mock PNG; no browser encoding']))};
    return item;
  }
  const nodes=new Map([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],element(m[1])]));
  const scripts=[...html.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)];
  for(const [_,attrs,content] of scripts){const id=attrs.match(/id="([^"]+)"/);if(id&&nodes.has(id[1]))nodes.get(id[1]).textContent=content;}
  const payload=JSON.parse(nodes.get('payload').textContent);
  for(const [id,value] of [['speed','1'],['scopeMode','centered'],['quality','balanced']])if(nodes.has(id))nodes.get(id).value=value;
  const document={getElementById:id=>{if(!nodes.has(id))throw new Error('Missing DOM id '+id);return nodes.get(id);},createElement:tag=>element('',tag),createTextNode:text=>({textContent:text}),
    addEventListener:(name,fn)=>documentHandlers.set(name,fn),body:element('body'),hidden:false};
  const sandbox={document,window:{addEventListener:(name,fn)=>windowHandlers.set(name,fn)},console,devicePixelRatio:2,Blob,atob,btoa,
    URL:{createObjectURL:blob=>{const url='blob:mock-'+objectId++;blobs.set(url,blob);return url;},revokeObjectURL:noop},performance:{now:()=>clock},
    requestAnimationFrame:fn=>{const id=nextId++;raf.set(id,fn);return id;},cancelAnimationFrame:id=>raf.delete(id),
    setTimeout:(fn,delay)=>{const id=nextId++;timers.set(id,{fn,delay});return id;},clearTimeout:id=>timers.delete(id)};
  if(storage==='denied')Object.defineProperty(sandbox.window,'localStorage',{get(){throw new Error('Storage denied');}});
  else if(storage)sandbox.window.localStorage={getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)};
  vm.createContext(sandbox);
  for(const [_,attrs,content] of scripts)if(!/type=/.test(attrs))vm.runInContext(content,sandbox,{timeout:30000});
  function flush(ms=16.667,rafLagMs=0){clock+=ms;const callbacks=[...raf.values()];raf.clear();callbacks.forEach(fn=>fn(clock-rafLagMs));return callbacks.length;}
  return {nodes,payload,downloads,blobs,counts,raf,timers,document,documentHandlers,windowHandlers,flush,canvasText,canvasDraws,
    diagnostics:()=>sandbox.window.spectrumStudioDiagnostics(),review:()=>sandbox.window.spectrumStudioReview,tools:()=>sandbox.window.spectrumStudioTools,get clock(){return clock;}};
}
module.exports={harness};
