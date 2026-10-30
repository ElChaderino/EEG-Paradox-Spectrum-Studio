/* Control and retained-resource checks; DOM/GL commands are mocked, not pixels. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs');
const {harness}=require('./viewer_harness.cjs');
const html=fs.readFileSync(process.argv[2],'utf8'),reports=[];
for(const gpu of [true,false]){
  const h=harness(html,{gpu}),{nodes,payload,flush,diagnostics:diag}=h;
  const names=payload.metadata.channel_names,N=payload.times.length;
  const input=(id,value,type='input')=>{nodes.get(id).value=String(value);nodes.get(id).fire(type);flush();};
  const click=id=>{nodes.get(id).click();flush();};
  flush();
  assert.equal(diag().renderer,gpu?'webgl2':'canvas');assert.equal(diag().windowCount,N);
  assert.equal(diag().synthetic,!!payload.metadata.synthetic);
  assert.equal(nodes.get('channel').children.length,names.length);
  for(let ch=0;ch<names.length;ch++){input('channel',ch,'change');assert.equal(diag().channel,names[ch]);assert(nodes.get('traceCaption').textContent.startsWith(names[ch]+' ·'));}
  input('scopeMode','recorded','change');assert.equal(diag().scopeCentered,false);assert.match(nodes.get('traceCaption').textContent,/DC retained/);
  input('scopeMode','centered','change');assert.equal(diag().scopeCentered,true);assert.match(nodes.get('traceCaption').textContent,/display mean removed/);
  for(const row of [0,Math.floor(N/2),N-1]){input('time',row);assert.equal(diag().row,row);}
  input('fmin',100,'change');assert.match(nodes.get('message').textContent,/Choose at least/);
  input('fmin',3.1,'change');input('fmax',30,'change');assert.equal(diag().visibleFrequencyHz[0],3.25);assert.equal(diag().visibleFrequencyHz[1],30);
  for(const palette of ['sdr','thermal','ice']){input('palette',palette,'change');assert.equal(diag().palette,palette);}
  for(const [id,values,key,scale] of [['depth',[32,64,96,128],'historyRows',1],['range',[10,50,90],'rangeDb',1],['perspective',[20,65],'perspective',.01],['lift',[40,160],'heightLift',.01]]){
    for(const value of values){input(id,value);assert.equal(diag()[key],value*scale);}
  }
  const beforeBurst=diag();
  for(let i=0;i<60;i++){nodes.get('floor').value=String(-60+i);nodes.get('floor').fire('input');}
  assert.equal(h.raf.size,1,'input events must coalesce into one animation frame');flush();
  assert.equal(diag().metrics.stageFrames-beforeBurst.metrics.stageFrames,1);
  const boxes=nodes.get('compareControls').children.map(label=>label.children[0]);
  boxes.forEach(box=>{box.checked=false;box.fire('change');});flush();assert.equal(diag().comparisonChannels.length,0);
  for(let i=0;i<Math.min(8,boxes.length);i++){boxes[i].checked=true;boxes[i].fire('change');}flush();
  if(boxes.length>8){boxes[8].checked=true;boxes[8].fire('change');flush();assert.equal(boxes[8].checked,false);}
  assert.equal(diag().comparisonChannels.length,Math.min(8,names.length));
  click('compareTab');assert(!nodes.get('comparePane').hidden);click('summaryTab');assert(!nodes.get('summaryPane').hidden);
  click('methodToggle');assert(nodes.get('methodDialog').open);click('closeMethod');assert(!nodes.get('methodDialog').open);
  click('focus');click('focus');input('quality','high','change');input('quality','balanced','change');
  input('time',N-1);
  const box=nodes.get('waterfall').getBoundingClientRect();
  nodes.get('waterfall').fire('mousemove',{clientX:500,clientY:box.top+80});assert.match(nodes.get('hover').textContent,/Hz/);
  nodes.get('waterfall').fire('click',{clientX:500,clientY:box.top+80});flush();assert(diag().row<N-1);
  input('time',0);nodes.get('waterfall').fire('click',{clientX:500,clientY:box.top+200});flush();assert.equal(diag().row,0);
  assert.match(nodes.get('hover').textContent,/No retained window/);
  // The first RAF can refer to the frame start before the Play click handler.
  click('reset');nodes.get('play').click();flush(0,1);
  assert.equal(diag().row,0,'a pre-click RAF timestamp must never select row -1');
  assert.equal(diag().playing,true);flush(502);assert(diag().row>=1);click('play');
  for(const speed of [1,2,4]){
    input('speed',speed,'change');click('reset');click('play');assert.equal(diag().playing,true);
    const start=diag().row;flush(payload.metadata.actual_hop_seconds*1000/speed+2);assert(diag().row>start);
    click('play');assert.equal(diag().playing,false);
  }
  input('time',N-2);click('play');flush(2000);assert.equal(diag().row,N-1);assert.equal(diag().playing,false);
  click('play');assert(diag().row<4);h.document.hidden=true;h.documentHandlers.get('visibilitychange')();flush();assert.equal(diag().playing,false);h.document.hidden=false;
  h.windowHandlers.get('resize')();flush();
  // After warm-up, row changes must neither rebuild geometry/textures nor redraw the fixed scope.
  input('time',N-1);flush();const warm=diag(),rawCounts={...h.counts};
  for(let i=1;i<=40;i++)input('time',Math.max(0,N-1-i));
  const steady=diag();
  assert.equal(steady.metrics.scopeDraws,warm.metrics.scopeDraws,'playback must not redraw the fixed waveform');
  if(gpu){
    assert.equal(steady.gpu.historyUploads,warm.gpu.historyUploads);
    assert.equal(steady.gpu.meshUploads,warm.gpu.meshUploads);
    assert.equal(steady.gpu.paletteUploads,warm.gpu.paletteUploads);
    assert.equal(steady.gpu.canvasResizes,warm.gpu.canvasResizes);
    assert.equal(steady.gpu.lastFrameDrawCalls,3);
    assert.equal(h.counts.bufferUploads,rawCounts.bufferUploads);
    assert.equal(h.counts.textureUploads,rawCounts.textureUploads);
  }else assert.equal(steady.metrics.waterfallCacheBuilds,warm.metrics.waterfallCacheBuilds,'fallback waterfall colors must be cached');
  click('benchmark');let guard=0;while(!diag().lastBenchmark&&guard++<370)flush();
  assert(diag().lastBenchmark);flush();
  if(gpu){
    const b=diag().lastBenchmark;
    assert.equal(b.history_uploads_during_check,0);assert.equal(b.geometry_uploads_during_check,0);assert.equal(b.palette_uploads_during_check,0);assert.equal(b.canvas_resizes_during_check,0);
  }
  click('savePng');
  for(const [prefix,id] of [['Selected-window PSD · ','fftCaption'],['Waveform · ','traceCaption']]){
    assert(h.canvasText.some(item=>item.id===''&&item.value===prefix+nodes.get(id).textContent),
      'Export must identify the selected chart channel and its own time range: '+id);
  }
  click('saveMethod');click('saveState');click('savePerformance');
  assert.deepEqual(h.downloads.map(x=>x.name),['eeg_spectrum.png','spectrum_method.json','spectrum_workspace.json','spectrum_performance.json']);
  reports.push({renderer:diag().renderer,channels:names.length,controls:'passed',coalesced_input_events:true,
    fixed_scope_redraws_during_40_rows:steady.metrics.scopeDraws-warm.metrics.scopeDraws,
    history_uploads_during_40_rows:gpu?steady.gpu.historyUploads-warm.gpu.historyUploads:null,
    geometry_uploads_during_40_rows:gpu?steady.gpu.meshUploads-warm.gpu.meshUploads:null,
    gpu_draws_per_frame:gpu?steady.gpu.lastFrameDrawCalls:null,browser_pixels_verified:false});
}
console.log(JSON.stringify({status:'passed',reports},null,2));
