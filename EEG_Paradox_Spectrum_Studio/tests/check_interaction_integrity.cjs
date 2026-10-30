/* User review actions must not inherit a display check's temporary cursor. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs');
const {harness}=require('./viewer_harness.cjs');
const html=fs.readFileSync(process.argv[2],'utf8'),reports=[];
for(const gpu of [true,false]){
  const fresh=()=>{const h=harness(html,{gpu});h.flush();h.nodes.get('time').value='17';h.nodes.get('time').fire('input');h.flush();return h;};
  const startCheck=h=>{h.nodes.get('benchmark').click();for(let i=0;i<11;i++)h.flush(34);assert.notEqual(h.diagnostics().row,17);};
  const run=(name,body)=>{try{body();reports.push({renderer:gpu?'webgl2':'canvas',name,status:'passed'});}
    catch(error){reports.push({renderer:gpu?'webgl2':'canvas',name,status:'failed',reason:error.message});}};
  for(const [id,value] of [['palette','ice'],['channel','9'],['mapMax','50'],['bandPreset','alpha']])run(id+' change preserves selected time',()=>{
    const h=fresh();startCheck(h);h.nodes.get(id).value=value;h.nodes.get(id).fire('change');h.flush();
    assert.equal(h.nodes.get('benchmark').disabled,false);assert.equal(h.diagnostics().row,17);
  });
  run('bookmark uses selected time',()=>{
    const h=fresh();startCheck(h);h.nodes.get('bookmarkLabel').value='Integrity test bookmark';h.nodes.get('addBookmark').click();h.flush();
    const marker=h.review().workspace().review.events.find(x=>x.label==='Integrity test bookmark');
    assert.equal(marker.onset_seconds,h.payload.times[17]);assert.equal(h.diagnostics().row,17);
  });
  run('trend image cancels temporary cursor',()=>{
    const h=fresh();h.nodes.get('trendTab').click();h.flush();startCheck(h);h.nodes.get('saveTrendPNG').click();h.flush();
    assert.equal(h.nodes.get('benchmark').disabled,false);assert.equal(h.diagnostics().row,17);
    assert(h.downloads.some(x=>x.name==='spectrum_band_trend.png'));
  });
  run('seek keeps requested target',()=>{
    const h=fresh();startCheck(h);h.nodes.get('seekSeconds').value=String(h.payload.times[5]);h.nodes.get('seekGo').click();h.flush();
    assert.equal(h.diagnostics().row,5);assert.equal(h.nodes.get('benchmark').disabled,false);
  });
  run('slider keeps requested target',()=>{
    const h=fresh();startCheck(h);h.nodes.get('time').value='5';h.nodes.get('time').fire('input');h.flush();
    assert.equal(h.diagnostics().row,5);assert.equal(h.nodes.get('benchmark').disabled,false);
  });
  run('workspace keeps imported cursor',()=>{
    const h=fresh(),saved=h.review().workspace();saved.view.row=5;startCheck(h);h.review().importWorkspace(saved);h.flush();
    assert.equal(h.diagnostics().row,5);assert.equal(h.nodes.get('benchmark').disabled,false);
  });
  run('short trend interval does not export empty files',()=>{
    const h=fresh();h.nodes.get('trendTab').click();h.flush();
    h.nodes.get('trendStart').value='10';h.nodes.get('trendStart').fire('change');h.flush();
    h.nodes.get('trendStop').value='10.5';h.nodes.get('trendStop').fire('change');h.flush();
    assert.equal(h.nodes.get('saveTrendCSV').disabled,true);assert.equal(h.nodes.get('saveTrendPNG').disabled,true);
    h.nodes.get('saveTrendCSV').click();h.nodes.get('saveTrendPNG').click();h.flush();
    assert(!h.downloads.some(x=>/spectrum_band_trend\.(csv|png)/.test(x.name)));
    assert.match(h.nodes.get('trendCaption').textContent,/complete.*window|window.*fit/i);
    h.nodes.get('trendWhole').click();h.flush();
    assert.equal(h.nodes.get('saveTrendCSV').disabled,false);assert.equal(h.nodes.get('saveTrendPNG').disabled,false);
  });
  run('layout preserves selected time',()=>{
    const h=fresh();startCheck(h);assert.equal(h.review().workspace().view.row,17);
    h.tools().applyLayout('Channel overview');h.flush();
    assert.equal(h.diagnostics().row,17);assert.equal(h.nodes.get('benchmark').disabled,false);
  });
  run('relative keyboard navigation starts at selected time',()=>{
    const h=fresh();startCheck(h);
    h.windowHandlers.get('keydown')({key:'ArrowRight',target:{tagName:'DIV'},preventDefault(){}});h.flush();
    assert.equal(h.diagnostics().row,18);assert.equal(h.nodes.get('benchmark').disabled,false);
  });
}
console.log(JSON.stringify({status:reports.every(x=>x.status==='passed')?'passed':'failed',reports,browser_pixels_verified:false},null,2));
assert(reports.every(x=>x.status==='passed'),'Review interaction regressions');
