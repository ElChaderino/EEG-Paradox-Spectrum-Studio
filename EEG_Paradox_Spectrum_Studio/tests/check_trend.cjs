/* Numerical trend contracts, transport and cached drawing; graphics are mocked. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),C=require('../assets/review_core.js');
const {harness}=require('./viewer_harness.cjs');
const data={frequencies:[1,2,3,4],times:[2,4,6],metadata:{actual_window_seconds:4,bin_spacing_hz:1,
  selected_start_seconds:0,selected_stop_seconds:8,channel_names:['=SUM(1,2)'],source:'@local',source_sha256:'abc',condition:'EC',reference:'unknown'},
  valid:[[true,false,true]],linear:new Float32Array([1,2,3,4,NaN,NaN,NaN,NaN,100,200,300,400]),quality:{flags:new Uint8Array([0,2,0])}};
const series=C.trend(data,0,1,3,0,8);
assert.equal(series.valid_windows,2);assert.equal(series.masked_windows,1);assert.equal(series.mean_uv2,151.5);
assert.equal(series.median_uv2,151.5);assert.equal(series.rows[1].power_uv2,null);
assert.equal(C.trend(data,0,1,3,2,6).valid_windows,0);assert.equal(C.trend(data,0,1,3,2,6).rows.length,1);
const csv=C.trendCSV(data,0,series);assert(csv.includes('"\'=SUM(1,2)"'));assert(csv.includes('"\'@local"'));
assert(csv.includes(',2,4,6,0,,2'));assert(!csv.includes('NaN'));
assert.equal(C.nearestRow(data.times,3),0);assert.equal(C.nearestRow(data.times,-1),0);assert.equal(C.nearestRow(data.times,99),2);
assert.throws(()=>C.nearestRow(data.times,NaN));assert.throws(()=>C.trend(data,0,1,3,-1,8));
data.linear.fill(0);assert.equal(C.trend(data,0,1,3,0,8).rows[0].power_uv2,0);

(async()=>{
  const reports=[];
  for(const gpu of [true,false]){
    const h=harness(fs.readFileSync(process.argv[2],'utf8'),{gpu}),{nodes,flush,payload,diagnostics:diag}=h;flush();
    const click=id=>{nodes.get(id).click();flush();};
    const input=(id,value,type='input')=>{nodes.get(id).value=String(value);nodes.get(id).fire(type);flush();};
    const key=(value,extra={})=>{h.windowHandlers.get('keydown')({key:value,target:{tagName:'DIV'},preventDefault(){},...extra});flush();};
    click('reset');key('ArrowRight');assert.equal(diag().row,1);key('ArrowRight',{shiftKey:true});assert.equal(diag().row,11);
    key('Home');assert.equal(diag().row,0);key('End');assert.equal(diag().row,payload.times.length-1);
    key('ArrowLeft',{target:{tagName:'INPUT'}});assert.equal(diag().row,payload.times.length-1);
    key(' ');assert(diag().playing);key(' ');assert(!diag().playing);
    input('seekSeconds',payload.times[5]+.05);click('seekGo');assert.equal(diag().row,5);
    input('seekSeconds','not a number');click('seekGo');assert.equal(diag().row,5);assert.match(nodes.get('message').textContent,/Seek within/);
    input('floor',-45);input('palette','ice','change');click('resetDisplay');assert.equal(diag().floorDb,-20);assert.equal(diag().palette,'sdr');
    click('trendTab');assert(!nodes.get('trendPane').hidden);assert.match(nodes.get('trendCaption').textContent,/complete windows/);
    const built=diag().review.metrics.trendBuilds;
    for(let row=6;row<12;row++)input('time',row);
    assert.equal(diag().review.metrics.trendBuilds,built,'cursor movement must reuse the full-history trend image');
    const old=h.review().workspace();delete old.review.trend;old.view.dock='summary';h.review().importWorkspace(old);flush();
    assert.deepEqual(Array.from(h.review().workspace().review.trend),[payload.metadata.selected_start_seconds,payload.metadata.selected_stop_seconds]);
    const validWorkspace=h.review().workspace(),beforeImport=JSON.stringify(validWorkspace);
    for(const invalid of [false,null,0,'']){
      const bad=JSON.parse(beforeImport);bad.review.trend=invalid;
      assert.throws(()=>h.review().importWorkspace(bad),/trend interval/);
      assert.equal(JSON.stringify(h.review().workspace()),beforeImport,'invalid optional intervals must not silently reset review settings');
    }
    click('trendTab');input('trendStop',-1,'change');assert.match(nodes.get('message').textContent,/Trend interval/);
    const interval=Array.from(h.review().workspace().review.trend);
    input('trendStart','','change');assert.match(nodes.get('message').textContent,/Trend interval/);
    assert.deepEqual(Array.from(h.review().workspace().review.trend),interval);
    input('aStart','','change');assert.match(nodes.get('message').textContent,/Interval must fit/);
    assert.deepEqual(Array.from(h.review().workspace().review.a),Array.from(validWorkspace.review.a));
    click('trendWhole');click('saveTrendCSV');click('saveTrendPNG');
    const saved=h.downloads.find(x=>x.name==='spectrum_band_trend.csv');const contents=await h.blobs.get(saved.url).text();
    assert.match(contents,/window_start_seconds/);assert.match(contents,/band_power_uv2/);assert.equal(contents.trim().split('\r\n').length,payload.times.length+1);
    assert(h.downloads.some(x=>x.name==='spectrum_band_trend.png'));
    assert(h.canvasDraws.some(x=>JSON.stringify(x.args)==='[30,120]'),'Trend export must draw at its own resolution without stretching the screen canvas');
    const plotBefore={...h.review().workspace()};
    nodes.get('trendCanvas').fire('click',{clientX:60,clientY:50});flush();
    assert.equal(diag().row,0,'Export must preserve the screen trend seek geometry');
    h.review().importWorkspace(plotBefore);flush();
    const before=diag().review.metrics.trendDraws;click('summaryTab');input('time',14);assert.equal(diag().review.metrics.trendDraws,before);
    reports.push({renderer:gpu?'webgl2':'canvas',transport:'passed',trend:'passed',CSV:'actual Blob serialization passed',trend_cache_reuse:true,browser_pixels_verified:false});
  }
  console.log(JSON.stringify({status:'passed',pure_numerics:'passed',reports},null,2));
})().catch(error=>{console.error(error);process.exitCode=1;});
