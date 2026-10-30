/* Playback timing regression; rendering commands are instrumented, not pixels. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs');
const {harness}=require('./viewer_harness.cjs');
const html=fs.readFileSync(process.argv[2],'utf8'),reports=[];
for(const gpu of [true,false]){
  const h=harness(html,{gpu}),{nodes,flush,diagnostics:diag}=h;flush();
  assert(h.payload.times.length>12,'This recording fixture needs at least 13 windows');
  const hop=h.payload.metadata.actual_hop_seconds;
  for(const [before,after] of [[1,4],[4,1],[1,2],[2,1]]){
    nodes.get('time').value='10';nodes.get('time').fire('input');flush();
    nodes.get('speed').value=String(before);nodes.get('speed').fire('change');
    nodes.get('play').click();flush(hop*1000*.75/before);
    assert.equal(diag().row,10);assert.equal(diag().playing,true);
    nodes.get('speed').value=String(after);nodes.get('speed').fire('change');
    // Already 75% into a hop: another 30% must cross the next retained window.
    flush(hop*1000*.30/after);
    assert.equal(diag().row,11,`${gpu?'GPU':'Canvas'} speed ${before}x -> ${after}x lost its fractional position`);
    nodes.get('play').click();flush();assert.equal(diag().playing,false);
  }
  reports.push({renderer:gpu?'webgl2':'canvas',speed_change_clock_continuity:'passed',browser_pixels_verified:false});
}
console.log(JSON.stringify({status:'passed',reports},null,2));
