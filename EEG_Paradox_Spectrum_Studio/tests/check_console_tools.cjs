/* Real import/export handlers and privacy contracts; Canvas/WebGL calls are instrumented. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),C=require('../assets/console_core.js');
const {harness}=require('./viewer_harness.cjs'),copy=x=>JSON.parse(JSON.stringify(x));
const times=Array.from({length:26},(_,i)=>100+i),valid=times.map(t=>t!==110);
const grid=C.timeMarkers(times,valid,25,26,5,1);
assert.deepEqual(grid.markers.map(x=>x.seconds),[100,105,115,120,125]);
assert.equal(grid.markers.at(-1).ageRows,.5);
assert.deepEqual(C.timeMarkers([100,101,120,121],[true,true,true,true],3,4,5,1).markers.map(x=>x.seconds),[100,120]);
assert.equal(C.timeMarkers([109.5,110.5],[false,true],1,2,5,1).markers.length,0);
assert.equal(C.timeMarkers([109.5,110.5],[true,true],1,2,5,1,.25).markers[0].ageRows,1.25);
assert.equal(C.timeMarkers(times,valid,25,26,0,1).markers.length,0);
assert(C.timeMarkers(times,valid,25,26,5,1,0,1).markers.length<=2);
assert.throws(()=>C.timeMarkers(times,valid,25,26,7,1));

(async()=>{
  const html=fs.readFileSync(process.argv[2],'utf8'),reports=[];
  for(const gpu of [true,false]){
    const storage=new Map(),h=harness(html,{gpu,storage}),{nodes,flush}=h;flush();
    const api=h.tools(),review=h.review(),click=id=>{nodes.get(id).click();flush();};
    const change=(id,value,type='change')=>{nodes.get(id).value=String(value);nodes.get(id).fire(type);flush();};
    assert.equal(api.layouts().layouts.length,4);
    change('floor',-40,'input');change('palette','ice');change('markerSeconds',10);change('time',12,'input');
    nodes.get('bookmarkLabel').value='PRIVATE_EVENT_PAYLOAD';click('addBookmark');
    const before=review.workspace();nodes.get('layoutName').value='QA saved layout';click('newLayout');
    const item=api.layouts().layouts.find(x=>x.name==='QA saved layout');assert(item);
    assert(!JSON.stringify(item).includes('PRIVATE_EVENT_PAYLOAD'));
    for(const key of ['source','source_sha256','fingerprint','events','a','b','trend'])assert(!Object.hasOwn(item,key));
    change('floor',-65,'input');change('time',20,'input');api.applyLayout(item.name);flush();
    const applied=review.workspace();assert.equal(applied.view.floor,-40);assert.equal(applied.view.row,20);
    assert.equal(applied.review.markerSeconds,10);assert.deepEqual(copy(applied.review.events),copy(before.review.events));
    assert.deepEqual(copy(applied.review.a),copy(before.review.a));assert.deepEqual(copy(applied.review.trend),copy(before.review.trend));
    assert.equal(applied.source_sha256,before.source_sha256);
    nodes.get('layoutName').value='QA renamed layout';click('updateLayout');assert.equal(nodes.get('layoutChoice').value,'QA renamed layout');
    click('removeLayout');assert(!api.layouts().layouts.some(x=>x.name==='QA renamed layout'));click('undoLayout');
    assert(api.layouts().layouts.some(x=>x.name==='QA renamed layout'));
    click('saveLayouts');const download=h.downloads.findLast(x=>x.name==='spectrum_layouts.json'),blob=h.blobs.get(download.url),saved=JSON.parse(await blob.text());
    assert(saved.layouts.some(x=>x.name==='QA renamed layout'));assert(!JSON.stringify(saved).includes('PRIVATE_EVENT_PAYLOAD'));
    const reopened=harness(html,{gpu,storage});reopened.flush();assert.equal(reopened.tools().layouts().layouts.length,5);
    nodes.get('layoutFile').files=[blob];await nodes.get('layoutFile').fire('change');flush();assert.match(nodes.get('layoutStatus').textContent,/imported/);
    const libraryBefore=JSON.stringify(api.layouts()),workspaceBefore=JSON.stringify(review.workspace());
    const malformed=copy(saved);malformed.layouts[0].view.floor=Infinity;
    assert.throws(()=>api.importLayouts(malformed));assert.equal(JSON.stringify(api.layouts()),libraryBefore);
    const conflict=copy(saved);conflict.layouts[0].view.floor=-55;
    const merged=C.merge(saved,conflict);assert(merged.layouts.some(x=>x.name==='Single-channel review (imported)'&&x.view.floor===-55));
    assert.equal(C.merge(merged,conflict).layouts.length,merged.layouts.length,'repeated conflicting imports must be idempotent');
    const missing=copy(item);missing.name='Missing channels';missing.active_channel='MISSING_EEG_CHANNEL';
    api.importLayouts({schema:saved.schema,layouts:[missing]});assert.throws(()=>api.applyLayout(missing.name),/channel unavailable/);
    assert.equal(JSON.stringify(review.workspace()),workspaceBefore);
    let readOversized=false;nodes.get('layoutFile').files=[{size:C.fileLimit+1,text:async()=>{readOversized=true;return '{}';}}];
    const catalogBeforeOversized=JSON.stringify(api.layouts());await nodes.get('layoutFile').fire('change');flush();
    assert.equal(readOversized,false);assert.match(nodes.get('layoutStatus').textContent,/exceeds 256 KB/);assert.equal(JSON.stringify(api.layouts()),catalogBeforeOversized);
    const large=copy(item);large.active_channel='界'.repeat(500);large.panel_channels=Array.from({length:4},(_,i)=>'界'.repeat(499)+i);
    large.comparison_channels=Array.from({length:8},(_,i)=>'文'.repeat(499)+i);
    assert.throws(()=>C.library({schema:saved.schema,layouts:Array.from({length:32},(_,i)=>({...large,name:'Large '+i}))}),/256 KB/);
    const badBand=copy(item);badBand.name='Unsupported frequency';badBand.review.band=[1e6,2e6];
    api.importLayouts({schema:saved.schema,layouts:[badBand]});assert.throws(()=>api.applyLayout(badBand.name),/Band must fit/);
    assert.equal(JSON.stringify(review.workspace()),workspaceBefore);
    const old=review.workspace();delete old.review.markerSeconds;review.importWorkspace(old);flush();assert.equal(h.diagnostics().review.marker_seconds,0);
    for(const invalid of [false,null,'10',7]){const bad=review.workspace();bad.review.markerSeconds=invalid;assert.throws(()=>review.importWorkspace(bad),/clock interval/);}
    change('markerSeconds',10);change('time',h.payload.times.length-1,'input');
    assert.match(nodes.get('markerCaption').textContent,/Original-time grid/);
    assert(h.canvasText.some(x=>x.id==='reviewOverlay'&&/ s$/.test(x.value)),'clock labels must be drawn on the shared overlay');
    const warm=h.diagnostics();for(let i=0;i<20;i++)change('time',i+20,'input');const after=h.diagnostics();
    if(gpu){assert.equal(after.gpu.historyUploads,warm.gpu.historyUploads);assert.equal(after.gpu.meshUploads,warm.gpu.meshUploads);}
    change('time',10,'input');click('play');flush(250);
    const hoverRow=h.diagnostics().row-(gpu?0:1),count=Math.min(400,h.payload.times.length);
    nodes.get('waterfall').fire('mousemove',{clientX:100,clientY:298+1.25*302/count});
    assert(nodes.get('hover').textContent.includes(h.payload.times[hoverRow].toFixed(2)+' s'),'hover must follow the exact row displayed during fractional GPU scrolling');
    click('play');
    for(const dialog of ['layouts','diagnostics']){
      click(dialog+'Toggle');const row=h.diagnostics().row;
      h.windowHandlers.get('keydown')({key:'End',target:{tagName:'DIV'},preventDefault(){}});flush();assert.equal(h.diagnostics().row,row);
      click(dialog==='layouts'?'closeLayouts':'closeDiagnostics');
    }
    h.windowHandlers.get('error')({message:'PRIVATE_ERROR_MESSAGE',filename:'C:/PRIVATE_PATH/patient.js',lineno:123,colno:4});
    h.windowHandlers.get('unhandledrejection')({reason:new Error('PRIVATE_EXCEPTION')});
    click('diagnosticsToggle');click('diagnosticsBenchmark');for(let i=0;i<135;i++)flush(34);click('refreshDiagnostics');click('saveDiagnostics');
    const diagnosticDownload=h.downloads.findLast(x=>x.name==='spectrum_studio_diagnostics.json');
    const contents=await h.blobs.get(diagnosticDownload.url).text(),report=JSON.parse(contents);
    assert(report.performance_check&&report.performance_check.frames===120);assert.equal(report.performance_check.gpu_time_measured,false);
    assert(report.recent_events.some(x=>x.code==='runtime_error'));assert(report.recent_events.some(x=>x.code==='unhandled_rejection'));
    assert(report.recent_events.some(x=>x.code==='runtime_error'&&x.line===123&&x.column===4));
    const restarted=harness(html,{gpu,storage});restarted.flush();
    assert(restarted.tools().diagnostics().recent_events.some(x=>x.code==='runtime_error'),'bounded app status must survive a reload when storage is available');
    for(const value of ['PRIVATE_EVENT_PAYLOAD','PRIVATE_ERROR_MESSAGE','PRIVATE_EXCEPTION','PRIVATE_PATH',h.payload.metadata.source,h.payload.metadata.source_sha256])
      if(value)assert(!contents.includes(value),'diagnostics must exclude '+value);
    assert(!Object.hasOwn(report,'row'));assert(!Object.hasOwn(report,'channel'));
    click('diagnosticsBenchmark');flush(34);const originalCheck=api.diagnostics().performance_check;
    const lost={preventDefault(){}};nodes.get('ridge').fire('webglcontextlost',lost);flush();assert.equal(api.diagnostics().renderer,'canvas');
    assert.equal(nodes.get('benchmark').disabled,false);assert.match(nodes.get('diagnosticsStatus').textContent,/cancelled/);
    assert.deepEqual(api.diagnostics().performance_check,originalCheck,'renderer transitions must not complete a mixed-renderer benchmark');
    assert(api.diagnostics().recent_events.some(x=>x.code==='context_lost'));
    nodes.get('ridge').fire('webglcontextrestored');flush();assert.equal(api.diagnostics().renderer,gpu?'webgl2':'canvas');
    reports.push({renderer:gpu?'webgl2':'canvas',layout_create_update_remove_undo_persistence:'passed',
      library_serialize_import_and_atomic_refusals:'passed',recording_context_retained:true,time_grid_and_replay_cache:'passed',
      diagnostics_actual_Blob_privacy_and_performance:'passed',context_loss_and_recovery:'passed',browser_pixels_verified:false});
  }
  const denied=harness(html,{storage:'denied'});denied.flush();denied.nodes.get('layoutName').value='Storage unavailable';denied.nodes.get('newLayout').click();
  assert(denied.tools().layouts().layouts.some(x=>x.name==='Storage unavailable'));assert.match(denied.nodes.get('layoutPersistence').textContent,/unavailable/);
  const canary='DO_NOT_EXPORT_THIS_PATIENT';
  const clean=JSON.stringify(C.diagnostics({source:canary,channel:canary,gpuError:canary,metrics:{stageFrames:2,secret:canary},
    lastBenchmark:{source:canary,source_sha256:canary,channels:[canary],frames:120},diagnosticEvents:[{code:'runtime_error',message:canary,elapsed_ms:2}]}));
  assert(!clean.includes(canary));
  console.log(JSON.stringify({status:'passed',original_time_mask_geometry:'passed',blocked_browser_storage:'passed',reports},null,2));
})().catch(error=>{console.error(error);process.exitCode=1;});
