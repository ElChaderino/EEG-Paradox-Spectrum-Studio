/* Review contracts and both presentation paths. This does not verify pixels. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs');
const Core=require('../assets/review_core.js'),{harness}=require('./viewer_harness.cjs');
const d={frequencies:[1,2,3,4],times:[2,4,6],metadata:{actual_window_seconds:4,bin_spacing_hz:1},
  valid:[[true,false,true]],linear:new Float32Array([1,2,3,4,NaN,NaN,NaN,NaN,100,200,300,400])};
assert.deepEqual(Core.band(d,0,0,1,3),{power:3,peakHz:2,bins:2});
assert.equal(Core.band(d,0,1,1,3).power,null);
const mean=Core.average(d,0,0,8);assert.deepEqual(Array.from(mean.values),[50.5,101,151.5,202]);assert.equal(mean.count,2);assert.equal(mean.masked,1);
assert.equal(Core.average(d,0,2,6).count,0);assert.throws(()=>Core.band(d,0,0,0,3));
const html=fs.readFileSync(process.argv[2],'utf8'),reports=[];
for(const gpu of [true,false]){
  const h=harness(html,{gpu}),{nodes,flush,payload}=h,diag=h.diagnostics;
  const api=h.review(),clone=x=>JSON.parse(JSON.stringify(x));
  const change=(id,value,type='change')=>{nodes.get(id).value=String(value);nodes.get(id).fire(type);flush();};
  const click=id=>{nodes.get(id).click();flush();};flush();
  const n=payload.metadata.channel_names.length,N=payload.times.length;
  change('bandLow',8);change('bandHigh',12);assert.deepEqual(clone(diag().review.band),[8,12]);
  const expected=diag().review.measurement;
  const raw=Buffer.from(payload.psd_packed.base64,'base64'),C=payload.frequencies.length,df=payload.metadata.bin_spacing_hz;
  let power=0;for(let f=0;f<C;f++)if(payload.frequencies[f]>=8&&payload.frequencies[f]<12)power+=raw.readFloatLE(((N-1)*C+f)*4)*df;
  assert(Math.abs(expected.power-power)<1e-8);
  change('bandHigh',2);assert.equal(diag().review.band[1],12);
  const handle=nodes.get('bandHandles').children[0];handle.fire('keydown',{key:'ArrowRight',preventDefault(){}});flush();assert.equal(diag().review.band[0],8+df);
  handle.fire('pointerdown',{pointerId:1,preventDefault(){}});handle.fire('pointermove',{clientX:250});handle.fire('pointerup');flush();
  assert(diag().review.band[0]<diag().review.band[1]);
  nodes.get('linked').checked=true;nodes.get('linked').fire('change');flush();assert.equal(diag().review.panels.length,Math.min(4,n));
  const panelBoxes=nodes.get('panelChoices').children.map(label=>label.children[0]);
  if(n>4){panelBoxes[4].checked=true;panelBoxes[4].fire('change');flush();assert.equal(panelBoxes[4].checked,false);}
  const warm=diag();for(let i=1;i<=25;i++)change('time',N-1-i,'input');const after=diag();
  assert.equal(after.review.metrics.mapDraws,warm.review.metrics.mapDraws,'hidden map must not repaint');
  assert.equal(after.review.metrics.abDraws,warm.review.metrics.abDraws,'hidden baseline must not repaint');
  if(gpu){assert.equal(after.gpu.historyUploads,warm.gpu.historyUploads);assert.equal(after.gpu.meshUploads,warm.gpu.meshUploads);assert.equal(after.gpu.lastFrameDrawCalls,3*Math.min(4,n));}
  else assert.equal(after.metrics.waterfallCacheBuilds,warm.metrics.waterfallCacheBuilds);
  change('time',N-1,'input');click('mapTab');
  const shouldMap=Core.timeAligned(payload.metadata)&&Object.keys(payload.review.montage?.positions_xy||{}).length>=3;
  assert.equal(diag().review.map.available,shouldMap);
  if(!shouldMap){
    assert.equal(nodes.get('saveMap').disabled,true,'Unavailable scalp maps must not offer blank map exports');
    if(!Core.timeAligned(payload.metadata))assert.match(nodes.get('mapCaption').textContent,/timing is unknown|recorded separately/);
    if(!payload.review.montage)assert.match(nodes.get('mapCaption').textContent,/positions are missing/);
  }
  click('saveMap');click('savePng');
  if(!shouldMap)assert(!h.downloads.some(x=>x.name==='spectrum_scalp_map.png'),'Unavailable context must not export a blank scalp figure');
  change('mapMax',300);if(shouldMap)assert.equal(diag().review.map.maximum_uv2,300);
  change('mapMax',-1);assert.equal(nodes.get('mapMax').value,'300');
  click('abTab');assert.equal(diag().review.ab.available,true);assert(diag().review.ab.a.valid_windows>0);
  click('savePng');
  change('aStop',-1);assert(Number(nodes.get('aStop').value)>0);
  click('saveAB');click('saveComparison');
  const comparison=api.comparisonPayload();
  if(!['unknown',''].includes(payload.metadata.reference.trim().toLowerCase())){
    api.importComparison(comparison);flush();change('bSource','imported');assert.equal(api.workspace().review.bSource,'imported');
    const saved=api.workspace();api.importWorkspace(saved);flush();
    const bad=clone(comparison);bad.metadata.reference='different';assert.throws(()=>api.importComparison(bad));
    const badShape=clone(comparison);badShape.psd_packed.shape=[1,1,1];assert.throws(()=>api.importComparison(badShape));
    change('bSource','primary');
  }else assert.throws(()=>api.importComparison(comparison));
  click('eventsTab');const initial=diag().review.events;
  click('savePng');
  nodes.get('bookmarkLabel').value='Reviewed point <script>';click('addBookmark');assert.equal(diag().review.events,initial+1);
  click('saveEvents');
  const visibleEvents=api.workspace().review.events.filter(event=>event.onset_seconds<=payload.metadata.selected_stop_seconds&&
    event.onset_seconds+event.duration_seconds>=payload.metadata.selected_start_seconds);
  assert.equal(nodes.get('eventList').children.length,visibleEvents.length||1,'Event rows describe only the retained interval; exports retain all source events');
  const saved=api.workspace(),fingerprint=saved.fingerprint;
  change('floor',-50,'input');change('bandLow',6);api.importWorkspace(saved);flush();assert.equal(diag().floorDb,saved.view.floor);assert.equal(diag().review.band[0],saved.review.band[0]);
  const before=JSON.stringify(api.workspace());
  for(const mutate of [x=>x.fingerprint='other',x=>x.view.row=-1,x=>x.view.floor=Infinity,x=>x.review.panels=[100],x=>x.review.band=[20,10],x=>x.review.fps=1000]){
    const bad=clone(saved);mutate(bad);assert.throws(()=>api.importWorkspace(bad));assert.equal(JSON.stringify(api.workspace()),before,'Import refusal must be atomic');
  }
  const events={schema:'eeg-paradox.spectrum-events.v1',fingerprint,events:[{id:'new',onset_seconds:10,duration_seconds:2,label:'Task',kind:'task',status:'proposed',channels:[],origin:'test'}]};
  api.importEvents(events);flush();assert.equal(diag().review.events,initial+2);
  assert.throws(()=>api.importEvents({...events,fingerprint:'wrong'}));
  const conflict=clone(events);conflict.events[0].status='accepted';assert.throws(()=>api.importEvents(conflict));
  click('previousEvent');click('nextEvent');
  change('panelPreset','frontal');assert(diag().review.panels.length>0);
  change('displayFps',15);click('summaryTab');click('reset');click('play');const frames=diag().metrics.stageFrames;
  for(let i=0;i<60;i++)flush(16.667);const drawCount=diag().metrics.stageFrames-frames;assert(drawCount<=16,'15 fps budget must limit active replay rendering');click('play');
  click('saveState');
  assert(h.downloads.some(x=>x.name==='spectrum_workspace.json'));
  reports.push({renderer:gpu?'webgl2':'canvas',linked_panels:Math.min(4,n),steady_history_uploads:gpu?after.gpu.historyUploads-warm.gpu.historyUploads:null,
    draw_calls_per_linked_frame:gpu?after.gpu.lastFrameDrawCalls:null,hidden_map_and_baseline_repaints:0,
    band_measurement:'independent linear PSD sum passed',workspace:'round-trip and atomic refusals passed',
    comparisons:'intervals, known reference and shape checks passed',scalp_map_available:shouldMap,events:'bookmarks, review status and recording binding passed',
    replay_frames_in_one_second_at_15fps:drawCount,browser_pixels_verified:false});
}
console.log(JSON.stringify({status:'passed',pure_numerics:'passed',reports},null,2));
