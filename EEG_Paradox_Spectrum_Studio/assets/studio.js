/* SPDX-License-Identifier: GPL-3.0-only. AetherSDR shader provenance: NOTICE.md. */
'use strict';
(() => {
  const $=id=>document.getElementById(id), D=JSON.parse($('payload').textContent), M=D.metadata;
  const F=D.frequencies,T=D.times,names=M.channel_names,N=T.length,C=F.length;
  const diagnosticStarted=performance.now(),diagnosticEvents=[];
  const diagnosticHistoryKey='eeg-paradox.spectrum-diagnostic-history.v1';
  try{
    const saved=window.localStorage?.getItem(diagnosticHistoryKey);
    if(saved&&saved.length<=32000){const history=JSON.parse(saved);if(history.schema===diagnosticHistoryKey)diagnosticEvents.push(...window.SpectrumConsoleCore.diagnosticHistory(history.events));}
  }catch{/* Local-file storage can be unavailable; diagnostics still work in memory. */}
  const diagnosticSession=(Math.max(0,...diagnosticEvents.map(e=>e.session))+1)%1e9;
  function recordDiagnostic(code,location={}){
    if(!window.SpectrumConsoleCore.eventCodes.includes(code))return;
    diagnosticEvents.push({code,elapsed_ms:Math.max(0,Math.round(performance.now()-diagnosticStarted)),session:diagnosticSession,
      line:location.line,column:location.column});
    if(diagnosticEvents.length>64)diagnosticEvents.shift();
    try{window.localStorage?.setItem(diagnosticHistoryKey,JSON.stringify({schema:diagnosticHistoryKey,events:window.SpectrumConsoleCore.diagnosticHistory(diagnosticEvents)}));}catch{/* Export remains available. */}
  }
  window.addEventListener('error',event=>recordDiagnostic('runtime_error',{line:event.lineno,column:event.colno}));
  window.addEventListener('unhandledrejection',()=>recordDiagnostic('unhandled_rejection'));
  const clamp=(v,a,b)=>Math.max(a,Math.min(b,v)),fmt=(v,n=2)=>Number.isFinite(v)?v.toFixed(n):'unavailable';
  function unpack(field,Type){
    const binary=atob(field.base64),bytes=new Uint8Array(binary.length);
    for(let i=0;i<binary.length;i++)bytes[i]=binary.charCodeAt(i);
    const values=new Type(bytes.buffer);
    if(Type.BYTES_PER_ELEMENT>1&&new Uint8Array(new Uint16Array([1]).buffer)[0]!==1){
      const view=new DataView(bytes.buffer),read=Type===Float64Array?'getFloat64':'getFloat32';
      for(let i=0;i<values.length;i++)values[i]=view[read](i*Type.BYTES_PER_ELEMENT,true);
    }
    return values;
  }
  let flat;
  if(D.psd_packed){
    D.linear=unpack(D.psd_packed,Float32Array);flat=new Float32Array(D.linear.length);
    for(let i=0;i<flat.length;i++)flat[i]=10*Math.log10(Math.max(D.linear[i],1e-12));
  }else if(D.db_packed)flat=unpack(D.db_packed,Float32Array);
  else{
    flat=new Float32Array(names.length*N*C);
    for(let ch=0;ch<names.length;ch++)for(let row=0;row<N;row++)for(let f=0;f<C;f++)flat[(ch*N+row)*C+f]=D.db[ch][row][f]===null?NaN:D.db[ch][row][f];
  }
  D.db=Array.from({length:names.length},(_,ch)=>Array.from({length:N},(_,row)=>flat.subarray((ch*N+row)*C,(ch*N+row+1)*C)));
  if(D.trace_packed){
    const values=unpack(D.trace_packed,Float64Array),length=D.traceTimes.length;
    D.trace=names.map((_,ch)=>values.subarray(ch*length,(ch+1)*length));
  }
  if(D.bands_packed)D.bands=Object.fromEntries(Object.entries(D.bands_packed).map(([name,field])=>{
    const values=unpack(field,Float64Array);return [name,names.map((_,ch)=>values.subarray(ch*N,(ch+1)*N))];
  }));
  D.quality=Object.fromEntries(Object.entries(D.quality_packed||{}).map(([key,value])=>[key,unpack(value,key==='flags'?Uint8Array:Float32Array)]));
  delete D.db_packed;delete D.psd_packed;delete D.trace_packed;delete D.bands_packed;delete D.quality_packed;$('payload').textContent='';
  const state={ch:0,row:N-1,low:F[0],high:F[C-1],floor:-20,range:50,palette:'sdr',depth:96,
    perspective:.58,lift:.65,playing:false,scopeCentered:true,quality:'balanced',dock:'summary',mapExpanded:false,
    compare:new Set(names.slice(0,4).map((_,i)=>i))};
  const palettes={
    sdr:[[0,0,8],[0,0,76],[5,8,205],[0,82,249],[0,206,233],[35,239,53],[221,249,0],[255,176,0],[255,46,12],[255,237,224]],
    thermal:[[4,2,14],[44,14,93],[119,29,135],[208,47,78],[251,119,42],[255,208,103],[255,249,218]],
    ice:[[0,5,15],[4,28,70],[6,80,130],[0,148,190],[79,219,232],[230,253,255]]
  };
  const paletteCache=new Map();
  function paletteData(key){
    if(paletteCache.has(key))return paletteCache.get(key);
    const stops=palettes[key],bytes=new Uint8Array(1024),css=[];
    for(let i=0;i<256;i++){
      const x=i/255*(stops.length-1),a=Math.min(stops.length-2,Math.floor(x)),f=x-a;
      for(let k=0;k<3;k++)bytes[i*4+k]=Math.round(stops[a][k]*(1-f)+stops[a+1][k]*f);
      bytes[i*4+3]=255;css.push('rgb('+bytes[i*4]+','+bytes[i*4+1]+','+bytes[i*4+2]+')');
    }
    const data={bytes,css};paletteCache.set(key,data);return data;
  }
  function level(db){return clamp((db-state.floor)/state.range,0,1);}
  function color(v){return paletteData(state.palette).css[Math.round(clamp(v,0,1)*255)];}
  const flags={STAGE:1,FFT:2,BANDS:4,SCOPE:8,COMPARE:16,LAYOUT:32,LABELS:64};
  const ALL=127,ROW=1|2|4|16|64,SCALE=1|2|16|64;
  let dirty=0,frameHandle=null,gpu=null,gpuError=null,L=null,waterGeometry=null,cpuTile=null;
  let playbackOrigin=0,playbackStart=0,phase=0,activeBenchmark=null,lastBenchmark=null;
  const samples=[],intervals=[];
  const metrics={stageFrames:0,fftDraws:0,scopeDraws:0,bandUpdates:0,comparisonDraws:0,
    waterfallCacheBuilds:0,scheduledFrames:0,coalescedRequests:0,canvasResizes:0};
  let lastFrameAt=0,lastStatAt=0,lastRenderedAt=0,review=null,consoleTools=null;
  const cpuTiles=new Map();
  const canvasCache=new Map();
  function text(id,value){const el=$(id);if(el.textContent!==value)el.textContent=value;}
  function rowText(parent,label,value){
    const row=document.createElement('div'),title=document.createElement('b');title.textContent=label+': ';
    row.append(title,document.createTextNode(String(value)));parent.append(row);
  }
  function request(bits=ALL){
    dirty|=bits;
    if(frameHandle!==null){metrics.coalescedRequests++;return;}
    metrics.scheduledFrames++;frameHandle=requestAnimationFrame(onFrame);
  }
  function stop(){
    state.playing=false;phase=0;text('play','Play recording');
  }
  function changed(bits){
    if(cancelDisplayCheck('Display check cancelled by a control change.'))bits|=ROW;
    request(bits);
  }
  function cancelDisplayCheck(reason,restoreCursor=true){
    if(!activeBenchmark)return false;
    if(restoreCursor)state.row=activeBenchmark.savedRow;
    activeBenchmark=null;phase=0;text('message',reason);$('benchmark').disabled=false;
    if($('diagnosticsDialog').open)text('diagnosticsStatus',reason);
    return true;
  }
  function selectRow(row){
    cancelDisplayCheck('Display check cancelled for recording navigation.');
    stop();state.row=clamp(row,0,N-1);changed(ROW);
  }
  function bounds(){
    let lo=0,hi=C-1;while(lo<C-1&&F[lo]<state.low)lo++;while(hi>lo&&F[hi]>state.high)hi--;return [lo,hi];
  }
  function canvas2d(id){
    let entry=canvasCache.get(id);
    if(!entry){const canvas=$(id);entry={canvas,ctx:canvas.getContext('2d')};canvasCache.set(id,entry);}
    const box=entry.canvas.getBoundingClientRect(),dpr=Math.min(devicePixelRatio||1,state.quality==='high'?2:1.25);
    const w=Math.max(1,box.width),h=Math.max(1,box.height);
    if(entry.canvas.width!==Math.round(w*dpr)||entry.canvas.height!==Math.round(h*dpr)){
      entry.canvas.width=Math.round(w*dpr);entry.canvas.height=Math.round(h*dpr);metrics.canvasResizes++;
    }
    entry.ctx.setTransform(dpr,0,0,dpr,0,0);entry.ctx.font='9px Consolas,monospace';
    return {...entry,w,h};
  }
  function layout(){
    const canvas=$(gpu?'ridge':'ridgeFallback'),box=canvas.getBoundingClientRect();
    const w=Math.max(100,box.width),h=Math.max(160,box.height),dpr=Math.min(devicePixelRatio||1,state.quality==='high'?2:1.25);
    const ridgeCss=Math.round(h*.46),axisCss=22,waterCss=h-ridgeCss-axisCss;
    L={w,h,dpr,ridgeCss,waterCss,axisCss,waterY:ridgeCss+axisCss};
    if(gpu)gpu.setLayout(w,h,dpr);else canvas2d('ridgeFallback');
    $('stage').style.setProperty('--ridge-height',ridgeCss+'px');
    $('stage').style.setProperty('--water-top',(ridgeCss+axisCss)+'px');
    drawRuler();
  }
  function drawRuler(){
    const [lo,hi]=bounds();
    const ruler=$('ridgeAxis');ruler.replaceChildren();
    for(let i=0;i<=8;i++){
      const span=document.createElement('span');span.textContent=fmt(F[lo]+(F[hi]-F[lo])*i/8,1);ruler.append(span);
    }
    for(const [name,a,b] of [['delta',.5,4],['theta',4,8],['alpha',8,13],['smr',13,15],['beta',15,30],['gamma',30,45]]){
      const left=Math.max(a,state.low),right=Math.min(b,state.high);if(right<=left)continue;
      const chip=document.createElement('i');chip.className='band-chip';
      chip.style.left=((left-state.low)/(state.high-state.low)*100)+'%';
      chip.style.width=((right-left)/(state.high-state.low)*100)+'%';
      chip.style.background={delta:'#4e72a9',theta:'#4da6ae',alpha:'#c4ae50',smr:'#4e9d71',beta:'#ac704b',gamma:'#955f94'}[name];
      ruler.append(chip);
    }
  }
  function drawTimeAxis(){
    const {ctx,w,h}=canvas2d('stageAxes');ctx.clearRect(0,0,w,h);ctx.fillStyle='#000006';ctx.fillRect(0,0,w,h);
    ctx.fillStyle='#597899';ctx.textAlign='right';
    const count=Math.min(N,400);
    for(let i=0;i<=4;i++){
      const age=Math.round((count-1)*i/4),y=L.waterY+i/4*L.waterCss;
      if(state.row-age>=0)ctx.fillText(fmt(T[state.row-age],1),w-6,clamp(y+4,L.waterY+10,h-5));
    }
    ctx.save();ctx.translate(9,L.waterY+L.waterCss/2);ctx.rotate(-Math.PI/2);ctx.textAlign='center';ctx.fillText('TIME · s',0,0);ctx.restore();
    ctx.fillStyle='#384f70';ctx.textAlign='right';
    for(let i=0;i<=3;i++)ctx.fillText(fmt(state.floor+state.range*(1-i/3),0),w-6,28+(L.ridgeCss-45)*i/3);
  }
  function drawFallback(viewWidth=null,offset=0){
    const {ctx,w:fullWidth,h}=canvas2d('ridgeFallback'),w=viewWidth||fullWidth,[lo,hi]=bounds(),count=Math.min(state.depth,state.row+1);
    ctx.save();ctx.translate(offset,0);
    ctx.fillStyle='#000006';ctx.fillRect(0,0,w,h);
    for(let age=count-1;age>=0;age--){
      const row=state.row-age;if(!D.valid[state.ch][row])continue;
      const depth=age/Math.max(1,state.depth-1),width=1-.4*depth,base=.985-depth*state.perspective;
      const points=[];
      for(let f=lo;f<=hi;f++){
        const strength=level(D.db[state.ch][row][f]);
        points.push([(.5+((f-lo)/(hi-lo)-.5)*width)*w,(base-Math.pow(strength,state.lift)*.46*width)*L.ridgeCss,strength]);
      }
      for(let i=1;i<points.length;i++){
        const a=points[i-1],b=points[i];ctx.fillStyle=color((a[2]+b[2])*.5*(1-depth*.16));
        ctx.beginPath();ctx.moveTo(a[0],L.ridgeCss*.985);ctx.lineTo(a[0],a[1]);ctx.lineTo(b[0],b[1]);ctx.lineTo(b[0],L.ridgeCss*.985);ctx.closePath();ctx.fill();
      }
      ctx.strokeStyle='#749ac545';ctx.lineWidth=.6;ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(p[0],p[1]):ctx.moveTo(p[0],p[1]));ctx.stroke();
    }
    const block=Math.floor(Math.max(0,state.row-400)/512)*512,tileCount=Math.min(1024,N-block);
    const key=[state.ch,state.palette,state.floor,state.range,lo,hi,block].join(':');
    if(cpuTiles.has(key))cpuTile=cpuTiles.get(key);
    if(!cpuTile||cpuTile.key!==key){
      const tile=document.createElement('canvas');tile.width=hi-lo+1;tile.height=tileCount;
      const tc=tile.getContext('2d'),image=tc.createImageData(tile.width,tile.height),lut=paletteData(state.palette).bytes;
      for(let r=0;r<tileCount;r++)for(let f=lo;f<=hi;f++){
        const v=D.db[state.ch][block+tileCount-1-r][f],k=(r*tile.width+f-lo)*4;
        if(!Number.isFinite(v)){image.data.set([20,24,31,255],k);continue;}
        const p=Math.round(level(v)*255)*4;image.data[k]=lut[p];image.data[k+1]=lut[p+1];image.data[k+2]=lut[p+2];image.data[k+3]=255;
      }
      tc.putImageData(image,0,0);cpuTile={key,tile,block,tileCount};metrics.waterfallCacheBuilds++;
      cpuTiles.set(key,cpuTile);if(cpuTiles.size>8)cpuTiles.delete(cpuTiles.keys().next().value);
    }
    ctx.imageSmoothingEnabled=false;
    const rows=Math.min(400,state.row+1),top=block+tileCount-1-state.row;
    ctx.drawImage(cpuTile.tile,0,top,hi-lo+1,rows,0,L.waterY,w,L.waterCss*rows/Math.min(N,400));ctx.imageSmoothingEnabled=true;
    ctx.restore();
  }
  function drawStage(smoothPhase=0){
    if(!L)layout();
    const [lo,hi]=bounds(),count=Math.min(N,400);
    if(gpu){
      try{
        gpu.setPalette(state.palette,paletteData(state.palette).bytes);
        const channels=review?review.panelChannels():[state.ch];
        channels.forEach((ch,index)=>{
          gpu.setChannel(ch,flat.subarray(ch*N*C,(ch+1)*N*C),C,N);
          gpu.render({...state,ch},smoothPhase,lo,hi,count,{x:index*L.w/channels.length,w:L.w/channels.length});
        });
        gpu.stats.lastFrameDrawCalls=3*channels.length;
      }catch(error){useFallback(error);layout();drawFallback();}
    }else{
      const channels=review?review.panelChannels():[state.ch],originalCh=state.ch;
      channels.forEach((ch,index)=>{
        const {ctx}=canvas2d('ridgeFallback');ctx.save();ctx.beginPath();ctx.rect(index*L.w/channels.length,0,L.w/channels.length,L.h);ctx.clip();
        state.ch=ch;drawFallback(L.w/channels.length,index*L.w/channels.length);ctx.restore();
      });state.ch=originalCh;
    }
    metrics.stageFrames++;waterGeometry={lo,hi,count};
  }
  function useFallback(error,reason='webgl_draw_failed'){
    cancelDisplayCheck('Display check cancelled because the renderer changed. Run the check again.',true);
    recordDiagnostic(reason);
    gpu=null;gpuError=String(error);$('ridge').hidden=true;$('ridgeFallback').hidden=false;
    text('engine','CPU FALLBACK');dirty|=ALL;
  }
  try{
    gpu=new window.SpectrumGPU($('ridge'),Object.fromEntries(['ridgeVertex','ridgeFragment','waterVertex','waterFragment'].map(id=>[id,$(id).textContent])));
    text('engine','GPU · SPECTRUM + WATERFALL');
  }catch(error){useFallback(error,'webgl_init_failed');}
  $('ridge').addEventListener('webglcontextlost',event=>{event.preventDefault();useFallback('WebGL context lost','context_lost');request(ALL);});
  $('ridge').addEventListener('webglcontextrestored',()=>{
    cancelDisplayCheck('Display check cancelled because the renderer restarted. Run the check again.',true);
    try{
      gpu=new window.SpectrumGPU($('ridge'),Object.fromEntries(['ridgeVertex','ridgeFragment','waterVertex','waterFragment'].map(id=>[id,$(id).textContent])));
      gpuError=null;recordDiagnostic('context_restored');$('ridge').hidden=false;$('ridgeFallback').hidden=true;text('engine','GPU · SPECTRUM + WATERFALL');request(ALL);
    }catch(error){useFallback(error,'webgl_restore_failed');request(ALL);}
  });
  function axes(ctx,w,h,min,max,xlabel,ylabel,xmin=state.low,xmax=state.high){
    const p={x:45,y:23,w:Math.max(1,w-57),h:Math.max(1,h-43)};
    ctx.fillStyle='#030811';ctx.fillRect(0,0,w,h);ctx.strokeStyle='#16243a';ctx.lineWidth=.6;ctx.fillStyle='#607f9e';
    for(let i=0;i<=4;i++){
      const x=p.x+p.w*i/4,y=p.y+p.h*i/4;
      ctx.beginPath();ctx.moveTo(x,p.y);ctx.lineTo(x,p.y+p.h);ctx.moveTo(p.x,y);ctx.lineTo(p.x+p.w,y);ctx.stroke();
      ctx.textAlign='center';ctx.fillText(fmt(xmin+(xmax-xmin)*i/4,1),x,p.y+p.h+12);
      ctx.textAlign='right';ctx.fillText(fmt(max-(max-min)*i/4,1),p.x-5,y+3);
    }
    ctx.fillStyle='#738da9';ctx.textAlign='left';ctx.fillText(ylabel,8,12);
    ctx.textAlign='right';ctx.fillText(xlabel,w-12,h-1);return p;
  }
  function spectrumLine(ctx,values,p,stroke){
    const [lo,hi]=bounds();ctx.strokeStyle=stroke;ctx.lineWidth=1;ctx.beginPath();let started=false;
    for(let f=lo;f<=hi;f++){
      const v=values[f];if(!Number.isFinite(v)){started=false;continue;}
      const x=p.x+(F[f]-state.low)/(state.high-state.low)*p.w,y=p.y+(1-level(v))*p.h;
      started?ctx.lineTo(x,y):ctx.moveTo(x,y);started=true;
    }ctx.stroke();
  }
  function drawFFT(){
    const {ctx,w,h}=canvas2d('fft'),p=axes(ctx,w,h,state.floor,state.floor+state.range,'Hz','PSD · dB re 1 µV²/Hz');
    spectrumLine(ctx,D.db[state.ch][state.row],p,'#62d0fb');
    if(!D.valid[state.ch][state.row]){ctx.fillStyle='#d7b077';ctx.textAlign='center';ctx.fillText('MASKED WINDOW',w/2,h/2);}
    metrics.fftDraws++;
  }
  const bandElements=Object.entries(D.bands).map(([name])=>{
    const row=document.createElement('div');row.className='bandrow';const title=document.createElement('span'),bar=document.createElement('span'),fill=document.createElement('i'),value=document.createElement('span');
    title.textContent=name.replaceAll('_',' ');title.title=M.bands_hz[name].join('–')+' Hz';
    bar.className='bar';value.className='val';bar.append(fill);row.append(title,bar,value);$('bands').append(row);return {name,fill,value};
  });
  function drawBands(){
    const values=bandElements.map(x=>D.bands[x.name][state.ch][state.row]),max=Math.max(1e-12,...values.filter(Number.isFinite));
    bandElements.forEach((el,i)=>{el.fill.style.width=(Number.isFinite(values[i])?values[i]/max*100:0)+'%';el.value.textContent=fmt(values[i]);});
    metrics.bandUpdates++;
  }
  const scopeCache=new Map();
  function drawScope(){
    const {ctx,w,h}=canvas2d('scope'),values=D.trace[state.ch],times=D.traceTimes,key=[state.ch,state.scopeCentered,Math.round(w)].join(':');
    let cached=scopeCache.get(key);
    if(!cached){
      let sum=0,n=0;for(const v of values)if(Number.isFinite(v)){sum+=v;n++;}
      const offset=state.scopeCentered&&n?sum/n:0;
      let maximum=1;for(const v of values)if(Number.isFinite(v))maximum=Math.max(maximum,Math.abs(v-offset));maximum*=1.1;
      const columns=Math.max(1,Math.floor(w-57)),envelope=new Float64Array(columns*2);
      for(let col=0;col<columns;col++){
        let low=Infinity,high=-Infinity;
        for(let i=Math.floor(col/columns*values.length),end=Math.max(i+1,Math.floor((col+1)/columns*values.length));i<end;i++){
          const v=values[i];if(Number.isFinite(v)){low=Math.min(low,v-offset);high=Math.max(high,v-offset);}
        }envelope[col*2]=low;envelope[col*2+1]=high;
      }
      cached={offset,maximum,columns,envelope};scopeCache.set(key,cached);
    }
    const {offset,maximum,columns,envelope}=cached;
    const p=axes(ctx,w,h,-maximum,maximum,'Original time · s','Voltage · µV',times[0],times[times.length-1]);
    ctx.strokeStyle='#7fabc9';ctx.lineWidth=.7;ctx.beginPath();
    for(let col=0;col<columns;col++)if(Number.isFinite(envelope[col*2])){
      ctx.moveTo(p.x+col,p.y+(1-(envelope[col*2+1]+maximum)/(2*maximum))*p.h);
      ctx.lineTo(p.x+col,p.y+(1-(envelope[col*2]+maximum)/(2*maximum))*p.h+.7);
    }ctx.stroke();
    text('traceCaption',names[state.ch]+' · first '+fmt(M.trace_stop_seconds-M.selected_start_seconds,1)+' s · '+(state.scopeCentered?'display mean removed: '+fmt(offset)+' µV':'as recorded; DC retained'));
    metrics.scopeDraws++;
  }
  const compareColors=['#6bcdf5','#e3af67','#a1b9f0','#d996c6','#99c98a','#e18b80','#88c3ca','#c6b983'];
  const compareLabels=[];
  function comparisonColors(){
    const selected=new Map(Array.from(state.compare).map((ch,i)=>[ch,compareColors[i]]));
    compareLabels.forEach((label,ch)=>{label.style.color=selected.get(ch)||'#61748b';});
  }
  function drawComparison(){
    const {ctx,w,h}=canvas2d('compare'),p=axes(ctx,w,h,state.floor,state.floor+state.range,'Hz','PSD · dB re 1 µV²/Hz');
    Array.from(state.compare).forEach((ch,i)=>spectrumLine(ctx,D.db[ch][state.row],p,compareColors[i]));
    metrics.comparisonDraws++;
  }
  function labels(){
    text('clock',fmt(T[state.row])+' s');$('time').value=String(state.row);
    if(document.activeElement!==$('seekSeconds'))$('seekSeconds').value=String(T[state.row]);
    text('position',(state.row+1)+' / '+N);
    if($('coverage').dataset?.channel!==String(state.ch)){
      $('coverage').replaceChildren();rowText($('coverage'),'Retained',N-M.invalid_windows_per_channel[state.ch]);rowText($('coverage'),'Masked',M.invalid_windows_per_channel[state.ch]);rowText($('coverage'),'Unwindowed tail',M.trailing_samples_not_windowed+' samples');
      if($('coverage').dataset)$('coverage').dataset.channel=String(state.ch);
    }
    const count=Math.min(state.depth,state.row+1),first=state.row-count+1;
    text('channelReadout',names[state.ch]+' / 3D');
    text('ridgeCaption',count+' rows · '+fmt(T[first],1)+'–'+fmt(T[state.row],1)+' s');
    text('waterCaption',names[state.ch]+' · '+fmt(T[Math.max(0,state.row-399)],1)+'–'+fmt(T[state.row],1)+' s · newest at top');
    text('fftCaption',names[state.ch]+' · center '+fmt(T[state.row])+' s · '+fmt(T[state.row]-M.actual_window_seconds/2)+'–'+fmt(T[state.row]+M.actual_window_seconds/2)+' s');
    text('floorValue',state.floor+' dB');text('rangeValue',state.range+' dB');
    text('scaleLow',state.floor+' dB');text('scaleHigh',state.floor+state.range+' dB');
    text('perspectiveValue',Math.round(state.perspective*100)+'%');text('liftValue',fmt(state.lift,2));
    $('colorbar').style.background='linear-gradient(to right,'+palettes[state.palette].map(c=>'rgb('+c.join(',')+')').join(',')+')';
    drawTimeAxis();
  }
  function draw(bits,smoothPhase){
    if(bits&flags.LAYOUT){layout();bits|=ALL;}
    if(bits&flags.LABELS)labels();
    if(bits&flags.STAGE)drawStage(smoothPhase);
    if(bits&flags.BANDS)drawBands();
    if(state.dock==='summary'){
      if(bits&flags.FFT)drawFFT();
      if(bits&flags.SCOPE)drawScope();
    }else if(state.dock==='compare'&&(bits&flags.COMPARE))drawComparison();
    if(review)review.draw(bits);
  }
  function percentile(values,p){
    if(!values.length)return null;const sorted=values.slice().sort((a,b)=>a-b);return sorted[Math.min(sorted.length-1,Math.floor(p*(sorted.length-1)))];
  }
  function onFrame(now){
    frameHandle=null;
    if((state.playing||activeBenchmark)&&!dirty&&now-lastRenderedAt<1000/(review?review.settings.fps:30)-.5){request(0);return;}
    const before=performance.now();let bits=dirty;dirty=0;
    if(state.playing){
      // RAF timestamps describe the start of a frame. A click handled later in
      // that same frame can have a newer performance.now() than its first RAF.
      // Never let that first callback select row -1 and abort rendering.
      const elapsed=Math.max(0,now-playbackStart);
      const position=Math.min(N-1,playbackOrigin+elapsed/1000/M.actual_hop_seconds*Number($('speed').value));
      const next=Math.floor(position);phase=position-next;
      if(next!==state.row){state.row=next;bits|=ROW;}
      if(gpu)bits|=flags.STAGE;
      if(position>=N-1){stop();bits|=ROW;}
    }
    if(activeBenchmark){
      const b=activeBenchmark;
      state.row=Math.min(N-1,(b.savedRow+Math.floor(b.frames/4))%N);phase=(b.frames%4)/4;
      bits|=flags.STAGE;if(b.frames%4===0)bits|=ROW;
      b.times.push(now);b.frames++;
    }
    draw(bits,phase);
    if(bits&flags.STAGE)lastRenderedAt=now;
    const cost=performance.now()-before;
    if(bits&flags.STAGE){samples.push(cost);if(samples.length>180)samples.shift();
      if(lastFrameAt&&now>lastFrameAt){intervals.push(now-lastFrameAt);if(intervals.length>180)intervals.shift();}lastFrameAt=now;}
    if(activeBenchmark){
      activeBenchmark.costs.push(cost);
      if(activeBenchmark.frames>=120){
        const b=activeBenchmark,g=gpu?{...gpu.stats}:null;
        lastBenchmark={schema:'eeg-paradox.spectrum-display-performance.v1',renderer:gpu?'webgl2':'canvas',
          mode:'120 display frames; one recorded window per four animation frames',source:M.source,source_sha256:M.source_sha256,
          target_fps:review.settings.fps,channels:review.panelChannels().map(ch=>names[ch]),graphics_quality:state.quality,
          frames:b.frames,elapsed_ms:b.times.at(-1)-b.times[0],observed_fps:119000/(b.times.at(-1)-b.times[0]),
          javascript_frame_ms_median:percentile(b.costs,.5),javascript_frame_ms_p95:percentile(b.costs,.95),
          gpu_time_measured:false,viewport:L,history_uploads_during_check:g?g.historyUploads-b.start.historyUploads:null,
          geometry_uploads_during_check:g?g.meshUploads-b.start.meshUploads:null,
          palette_uploads_during_check:g?g.paletteUploads-b.start.paletteUploads:null,
          canvas_resizes_during_check:g?g.canvasResizes-b.start.canvasResizes:null,draw_calls_per_gpu_frame:g?g.lastFrameDrawCalls:null};
        state.row=b.savedRow;activeBenchmark=null;phase=0;$('benchmark').disabled=false;
        recordDiagnostic('benchmark_complete');if($('diagnosticsDialog').open)consoleTools?.refreshDiagnostics();
        text('message',fmt(lastBenchmark.observed_fps,1)+' fps · '+fmt(lastBenchmark.javascript_frame_ms_p95,2)+' ms p95 JS · '+(gpu?gpu.stats.lastFrameDrawCalls+' GPU draws/frame':'CPU fallback')+'. Save the report for details.');
        dirty|=ROW;request();
      }
    }
    if(now-lastStatAt>450||(!state.playing&&!activeBenchmark)){
      const dt=percentile(intervals,.5),fps=dt?1000/dt:null;
      text('frameStats',gpu?(state.playing||activeBenchmark?(fps===null?'GPU · measuring…':fmt(fps,0)+' fps · '+fmt(percentile(samples,.5),2)+' ms JS'):'GPU · cached history'):'CPU fallback');
      lastStatAt=now;
    }
    if(state.playing||activeBenchmark||dirty)request(0);
  }
  function inspect(event,select){
    if(!L||!waterGeometry)return;
    const box=$('waterfall').getBoundingClientRect(),x=event.clientX-box.left,y=event.clientY-box.top;
    if(x<0||x>=box.width||y<0||y>=box.height)return;
    const channels=review?review.panelChannels():[state.ch],pane=Math.min(channels.length-1,Math.floor(x/box.width*channels.length)),ch=channels[pane];
    const fraction=x/box.width*channels.length-pane;
    const {lo,hi,count}=waterGeometry,bin=Math.min(hi,lo+Math.floor(fraction*(hi-lo+1)));
    const row=state.row-clamp(Math.floor(y/box.height*count-(gpu?phase:0)),0,count-1);
    if(row<0){text('hover','No retained window at this position.');return;}
    const v=D.db[ch][row][bin];
    text('hover',names[ch]+' · '+fmt(T[row])+' s · '+fmt(F[bin])+' Hz · '+(Number.isFinite(v)?fmt(v)+' dB re 1 µV²/Hz':'masked / unavailable'));
    $('probe').hidden=false;$('probe').style.left=(x/box.width*L.w)+'px';
    if(select){selectRow(row);if(state.ch!==ch)selectChannel(ch);}
  }
  $('waterfall').addEventListener('mousemove',e=>inspect(e,false));
  $('waterfall').addEventListener('click',e=>inspect(e,true));
  $('waterfall').addEventListener('mouseleave',()=>{$('probe').hidden=true;});
  names.forEach((name,ch)=>{
    const option=document.createElement('option');option.value=String(ch);option.textContent=name;$('channel').append(option);
    const label=document.createElement('label'),box=document.createElement('input');box.type='checkbox';box.checked=state.compare.has(ch);box.value=String(ch);
    compareLabels.push(label);label.append(box,document.createTextNode(name));$('compareControls').append(label);
    box.addEventListener('change',()=>{
      if(box.checked&&state.compare.size>=8){box.checked=false;text('message','Select up to eight channels for the comparison.');return;}
      box.checked?state.compare.add(ch):state.compare.delete(ch);comparisonColors();changed(flags.COMPARE);
    });
  });
  comparisonColors();
  text('mode',M.synthetic?'SYNTHETIC DEMO':'RECORDING REVIEW');if(M.synthetic)$('mode').classList.add('synthetic');
  text('source',M.source+' · '+M.condition+' · '+names.length+' ch · '+M.sample_rate_hz+' Hz');
  rowText($('context'),'Reference',M.reference);rowText($('context'),'Window',fmt(M.actual_window_seconds,2)+' s / '+fmt(M.actual_hop_seconds,2)+' s hop');
  rowText($('context'),'FFT',fmt(M.bin_spacing_hz,3)+' Hz · ENBW '+fmt(M.hann_enbw_hz,3)+' Hz');
  rowText($('context'),'Timing',window.SpectrumReviewCore.timingDescription(M));
  rowText($('coverage'),'Retained',N-M.invalid_windows_per_channel[0]);rowText($('coverage'),'Masked',M.invalid_windows_per_channel[0]);rowText($('coverage'),'Unwindowed tail',M.trailing_samples_not_windowed+' samples');
  text('method',JSON.stringify(M,null,2));text('footer',M.source+' · '+fmt(M.selected_start_seconds,1)+'–'+fmt(M.selected_stop_seconds,1)+' s · Hann / no padding · Spectrum Studio 0.6.8');
  text('compareCaption',window.SpectrumReviewCore.timeAligned(M)?'Descriptive sensor spectra · no connectivity inference':'Acquisition timing unknown/sequential · no cross-site temporal inference');
  $('time').max=String(N-1);$('time').value=String(state.row);$('fmin').value=String(state.low);$('fmax').value=String(state.high);$('depth').value='96';
  function aperture(){
    const low=Number($('fmin').value),high=Number($('fmax').value),values=F.filter(f=>f>=low&&f<=high);
    if(!Number.isFinite(low)||!Number.isFinite(high)||low<F[0]||high>F[C-1]||values.length<2||high-low<M.bin_spacing_hz){
      text('message','Choose at least two FFT bins within '+F[0]+'–'+F[C-1]+' Hz.');$('fmin').value=String(state.low);$('fmax').value=String(state.high);return;
    }
    state.low=values[0];state.high=values.at(-1);$('fmin').value=String(state.low);$('fmax').value=String(state.high);
    text('message','');drawRuler();changed(SCALE);
  }
  $('fmin').addEventListener('change',aperture);$('fmax').addEventListener('change',aperture);
  function selectChannel(ch){
    if(!Number.isInteger(ch)||ch<0||ch>=names.length)return;
    state.ch=ch;$('channel').value=String(ch);cpuTile=null;
    if(review)review.includeChannel(ch);
    $('coverage').replaceChildren();rowText($('coverage'),'Retained',N-M.invalid_windows_per_channel[state.ch]);rowText($('coverage'),'Masked',M.invalid_windows_per_channel[state.ch]);rowText($('coverage'),'Unwindowed tail',M.trailing_samples_not_windowed+' samples');
    text('hover','Hover over the waterfall to inspect '+names[state.ch]+'. Click to select a measured window.');changed(ALL&~flags.LAYOUT);
  }
  $('channel').addEventListener('change',()=>selectChannel(Number($('channel').value)));
  $('time').addEventListener('input',()=>selectRow(Number($('time').value)));
  for(const [id,key,scale,bits] of [['floor','floor',1,SCALE],['range','range',1,SCALE],['depth','depth',1,flags.STAGE|flags.LABELS],['perspective','perspective',.01,flags.STAGE|flags.LABELS],['lift','lift',.01,flags.STAGE|flags.LABELS]]){
    $(id).addEventListener('input',()=>{state[key]=Number($(id).value)*scale;changed(bits);});
  }
  $('palette').addEventListener('change',()=>{state.palette=$('palette').value;changed(flags.STAGE|flags.LABELS);});
  $('quality').addEventListener('change',()=>{state.quality=$('quality').value;changed(ALL);});
  $('scopeMode').addEventListener('change',()=>{state.scopeCentered=$('scopeMode').value==='centered';changed(flags.SCOPE);});
  $('play').addEventListener('click',()=>{
    cancelDisplayCheck('Display check cancelled for recording playback.',true);
    if(state.playing){stop();request(ROW);return;}
    if(state.row>=N-1)state.row=0;
    playbackOrigin=state.row;playbackStart=performance.now();samples.length=0;intervals.length=0;lastFrameAt=0;
    state.playing=true;text('play','Pause');request(ROW);
  });
  $('speed').addEventListener('change',()=>{if(state.playing){playbackOrigin=state.row+phase;playbackStart=performance.now();}});
  $('reset').addEventListener('click',()=>selectRow(0));
  $('seekSeconds').min=String(T[0]);$('seekSeconds').max=String(T.at(-1));$('seekSeconds').step=String(M.actual_hop_seconds);
  const seek=()=>{
    const value=Number($('seekSeconds').value);
    if(!Number.isFinite(value)||value<T[0]||value>T.at(-1)||!$('seekSeconds').value.trim()){
      text('message','Seek within '+fmt(T[0])+'–'+fmt(T.at(-1))+' original seconds.');$('seekSeconds').value=String(T[state.row]);return;
    }
    selectRow(window.SpectrumReviewCore.nearestRow(T,value));text('message','');
  };
  $('seekGo').addEventListener('click',seek);$('seekSeconds').addEventListener('focus',()=>{
    cancelDisplayCheck('Display check cancelled for recording navigation.');stop();request(ROW);
  });
  $('seekSeconds').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();seek();}});
  $('resetDisplay').addEventListener('click',()=>{
    Object.assign(state,{low:F[0],high:F.at(-1),floor:-20,range:50,palette:'sdr',depth:96,perspective:.58,lift:.65,quality:'balanced'});
    for(const [id,key,scale] of [['fmin','low',1],['fmax','high',1],['floor','floor',1],['range','range',1],['palette','palette',1],['depth','depth',1],['perspective','perspective',100],['lift','lift',100],['quality','quality',1]]){
      $(id).value=String(typeof state[key]==='number'?state[key]*scale:state[key]);
    }
    drawRuler();changed(ALL);text('message','Display reset; recording and review data retained.');
  });
  window.addEventListener('keydown',event=>{
    const target=event.target,tag=(target?.tagName||target?.tag||'').toUpperCase();
    if(event.ctrlKey||event.altKey||event.metaKey||target?.isContentEditable||['INPUT','SELECT','TEXTAREA','BUTTON'].includes(tag)||['methodDialog','layoutsDialog','diagnosticsDialog'].some(id=>$(id).open))return;
    if(event.key===' '){event.preventDefault();$('play').click();return;}
    if(!['Home','End','ArrowLeft','ArrowRight'].includes(event.key))return;
    cancelDisplayCheck('Display check cancelled for recording navigation.');
    const step=event.shiftKey?10:1;
    const row=event.key==='Home'?0:event.key==='End'?N-1:event.key==='ArrowLeft'?state.row-step:event.key==='ArrowRight'?state.row+step:null;
    if(row!==null){event.preventDefault();selectRow(row);}
  });
  $('focus').addEventListener('click',()=>{
    document.body.classList.toggle('focus-mode');
    if(document.body.classList.contains('focus-mode'))state.mapExpanded=false;
    if(review)review.mapLayout();changed(ALL);
  });
  function dock(name){
    state.dock=name;$('summaryPane').hidden=name!=='summary';$('comparePane').hidden=name!=='compare';
    for(const [id,active] of [['summaryTab',name==='summary'],['compareTab',name==='compare']]){$(id).classList.toggle('selected',active);$(id).setAttribute('aria-pressed',String(active));}
    changed(name==='summary'?flags.FFT|flags.SCOPE:flags.COMPARE);
    if(review)review.dock(name);
  }
  $('summaryTab').addEventListener('click',()=>dock('summary'));$('compareTab').addEventListener('click',()=>dock('compare'));
  $('methodToggle').addEventListener('click',()=>{$('methodDialog').showModal();});
  $('closeMethod').addEventListener('click',()=>{$('methodDialog').close();});
  document.addEventListener('visibilitychange',()=>{if(document.hidden){cancelDisplayCheck('Display check cancelled while the viewer was hidden. Run it again when visible.',true);stop();request(ROW);}});
  window.addEventListener('resize',()=>{cancelDisplayCheck('Display check cancelled because the window size changed. Run it again.',true);request(ALL);});
  function download(blob,name){
    const url=URL.createObjectURL(blob),anchor=document.createElement('a');anchor.href=url;anchor.download=name;document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  $('saveMethod').addEventListener('click',()=>download(new Blob([JSON.stringify(M,null,2)],{type:'application/json'}),'spectrum_method.json'));
  $('saveState').addEventListener('click',()=>{cancelDisplayCheck('Display check cancelled for workspace export.',true);review.saveWorkspace();request(ROW);});
  $('benchmark').addEventListener('click',()=>{
    stop();draw(ALL,0);activeBenchmark={frames:0,times:[],costs:[],savedRow:state.row,start:gpu?{...gpu.stats}:null};
    $('benchmark').disabled=true;text('message','Checking 120 display frames…');request(ROW);
  });
  $('savePerformance').addEventListener('click',()=>{
    if(!lastBenchmark){text('message','Run Check display performance first.');return;}
    download(new Blob([JSON.stringify(lastBenchmark,null,2)],{type:'application/json'}),'spectrum_performance.json');
  });
  $('savePng').addEventListener('click',()=>{
    cancelDisplayCheck('Display check cancelled for image export.',true);
    stop();
    try{
      draw(ALL,0);
      const output=document.createElement('canvas');output.width=1920;output.height=1280;const ctx=output.getContext('2d');
      ctx.fillStyle='#050a12';ctx.fillRect(0,0,1920,1280);ctx.fillStyle='#c9e5f7';ctx.font='22px Segoe UI';
      ctx.fillText('EEG PARADOX / SPECTRUM STUDIO',28,36);ctx.font='13px Consolas';
      const channels=review.panelChannels();
      ctx.fillStyle='#8fb3d1';ctx.fillText((M.synthetic?'SYNTHETIC · ':'')+M.source+' · '+channels.map(ch=>names[ch]).join(' / ')+' · '+M.condition+' · window center '+fmt(T[state.row])+' s',28,63);
      ctx.fillText('PSD '+state.floor+' to '+(state.floor+state.range)+' dB re 1 uV^2/Hz · Reference: '+M.reference,28,85);
      const x=75,y=104,w=1817,h=940;ctx.drawImage($(gpu?'ridge':'ridgeFallback'),x,y,w,h);
      const rulerY=y+L.ridgeCss/L.h*h;ctx.fillStyle='#15253d';ctx.fillRect(x,rulerY,w,L.axisCss/L.h*h);
      ctx.font='12px Consolas';ctx.fillStyle='#9bbbd7';ctx.textAlign='center';
      if(channels.length===1)for(let i=0;i<=8;i++)ctx.fillText(fmt(state.low+(state.high-state.low)*i/8,1),x+i/8*w,rulerY+16);
      ctx.drawImage($('reviewOverlay'),x,y,w,h);
      ctx.textAlign='right';
      const rows=Math.min(N,400),waterY=y+L.waterY/L.h*h,waterH=L.waterCss/L.h*h;
      for(let i=0;i<=4;i++){const row=state.row-Math.round((rows-1)*i/4);if(row>=0)ctx.fillText(fmt(T[row],1),x-9,waterY+Math.min(waterH-5,i/4*waterH+10));}
      ctx.textAlign='left';ctx.fillText('Hz · newest ridge at front / newest waterfall row at top · gray = masked',x,1065);
      if(state.dock==='summary'){
        drawFFT();drawScope();
        ctx.fillText('Selected-window PSD · '+$('fftCaption').textContent,28,1087,920);
        ctx.fillText('Waveform · '+$('traceCaption').textContent,973,1087,919);
        ctx.drawImage($('fft'),28,1100,920,140);ctx.drawImage($('scope'),973,1100,919,140);
      }else if(state.dock==='compare'){
        drawComparison();ctx.drawImage($('compare'),28,1090,1864,150);
      }else if(state.dock==='map'){
        review.drawMap(true);ctx.drawImage($('scalpMap'),28,1090,750,150);ctx.fillText($('mapCaption').textContent.slice(0,145),800,1130);
      }else if(state.dock==='ab'){
        review.drawAB(true);ctx.drawImage($('abSpectrum'),28,1090,1864,150);
      }else if(state.dock==='trend'){
        review.drawTrend(true);ctx.drawImage($('trendCanvas'),28,1090,1864,150);
      }else{
        review.visibleEvents().slice(0,6).forEach((event,i)=>ctx.fillText(fmt(event.onset_seconds)+' s · '+event.label+' · '+event.status,28,1105+i*22));
      }
      ctx.fillStyle='#6d8ea9';ctx.fillText('Exact retained windows · '+M.actual_window_seconds+' s Hann · '+M.bin_spacing_hz+' Hz FFT bins · AetherSDR shader adaptation / GPL-3.0',28,1260);
      output.toBlob(blob=>{if(blob)download(blob,'eeg_spectrum.png');else text('message','Image encoding failed.');},'image/png');
    }catch(error){text('message','Image export failed: '+String(error));}
  });
  review=new window.SpectrumReview({data:D,state,unpack,$,text,fmt,clamp,canvas2d,download,changed,stop,dock,
    flags,ALL,ROW,layout:()=>L,phase:()=>gpu?phase:0,comparisonColors,drawRuler,paletteData,selectChannel,
    cancelDisplayCheck,selectedRow:()=>activeBenchmark?.savedRow??state.row,
    selectRow});
  consoleTools=new window.SpectrumConsoleTools({$,data:D,review:()=>review,text,download,record:recordDiagnostic,
    diagnosticSnapshot:()=>({renderer:gpu?'webgl2':'canvas',quality:state.quality,playing:state.playing,dock:state.dock,
      historyRows:state.depth,panel_count:review.panelChannels().length,target_fps:review.settings.fps,markerSeconds:review.settings.markerSeconds,
      session_elapsed_ms:Math.max(0,Math.round(performance.now()-diagnosticStarted)),viewport:L,metrics,gpu:gpu?.stats,
      reviewMetrics:review.metrics,lastBenchmark,diagnosticEvents,
      browser_family:(()=>{const ua=typeof navigator==='undefined'?'':navigator.userAgent;return /Edg\//.test(ua)?'edge':/Firefox\//.test(ua)?'firefox':/Chrom(e|ium)\//.test(ua)?'chrome':/Safari\//.test(ua)?'safari':'other';})()})});
  recordDiagnostic('viewer_ready');
  request(ALL);
  window.spectrumStudioDiagnostics=()=>({version:'0.6.8',channel:names[state.ch],row:state.row,renderer:gpu?'webgl2':'canvas',gpuError,
    visibleFrequencyHz:[state.low,state.high],windowCount:N,synthetic:!!M.synthetic,floorDb:state.floor,rangeDb:state.range,historyRows:state.depth,
    perspective:state.perspective,heightLift:state.lift,palette:state.palette,scopeCentered:state.scopeCentered,playing:state.playing,
    comparisonChannels:Array.from(state.compare).map(ch=>names[ch]),metrics:{...metrics},gpu:gpu?{...gpu.stats}:null,
    frameJavascriptMsMedian:percentile(samples,.5),lastBenchmark:lastBenchmark?{...lastBenchmark}:null,review:review.diagnostics()});
})();
