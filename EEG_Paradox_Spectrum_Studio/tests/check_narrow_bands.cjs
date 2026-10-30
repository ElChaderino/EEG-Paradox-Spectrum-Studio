/* Named topo bands use retained full-FFT totals, independent of spectrum crop. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs');
const {harness}=require('./viewer_harness.cjs'),C=require('../assets/review_core.js');
assert.equal(C.fittedPowerMaximum([null,NaN,-1]),null);
assert.equal(C.fittedPowerMaximum([0,0]),1);
assert.equal(C.fittedPowerMaximum([200,50,12.5]),250);
assert.equal(C.fittedPowerMaximum([.0002]),.00025);
assert.equal(C.fittedPowerMaximum([1e-323]),1e-323);
const legacy={frequencies:[10,11,12,13],times:[2],linear:new Float32Array([0,100,0,0]),valid:[[true]],
  metadata:{channel_names:['Cz'],bands_hz:{alpha:[8,13],gamma:[30,45]},unavailable_bands:['gamma'],bin_spacing_hz:1},bands:{alpha:[[200]],gamma:[[999]]}};
assert.equal(C.mapPower(legacy,0,0,'alpha',[10,13]),200);
legacy.valid[0][0]=false;assert.equal(C.mapPower(legacy,0,0,'alpha',[10,13]),null);legacy.valid[0][0]=true;
assert.equal(C.mapBandAvailable(legacy,'gamma',[10,13]),false);
delete legacy.bands;assert.equal(C.mapBandAvailable(legacy,'alpha',[10,13]),false);
assert.equal(C.mapPower(legacy,0,0,'custom',[10,13]),100);
const html=fs.readFileSync(process.argv[2],'utf8'),reports=[];
for(const gpu of [true,false]){
  try{
    const h=harness(html,{gpu});h.flush();h.nodes.get('mapTab').click();h.flush();
    h.nodes.get('mapBand').value='alpha';h.nodes.get('mapBand').fire('change');h.flush();
    assert.equal(h.diagnostics().review.map.available,true,'The stored complete alpha total must remain available');
    const buttons=h.nodes.get('mapChannelChoices').children;
    for(const [index,power] of [200,50,12.5].entries())assert.equal(buttons[index].children[1].textContent,power.toFixed(2));
    h.nodes.get('fitMapScale').click();h.flush();assert.equal(h.diagnostics().review.map.maximum_uv2,250);
    h.nodes.get('play').click();for(let i=0;i<12;i++)h.flush(34);
    assert.equal(h.review().workspace().review.mapMax,250);h.nodes.get('play').click();h.flush();
    const option=h.nodes.get('bandPreset').children.find(x=>x.value==='delta');
    assert.equal(option.disabled,true,'Inspection presets outside retained FFT bins must be unavailable');
    const previous=Array.from(h.review().workspace().review.band);
    h.nodes.get('bandPreset').value='delta';h.nodes.get('bandPreset').fire('change');h.flush();
    assert.deepEqual(Array.from(h.review().workspace().review.band),previous);
    assert.notEqual(h.nodes.get('bandPreset').value,'delta','A refused preset must not retain a misleading band label');
    reports.push({renderer:gpu?'webgl2':'canvas',status:'passed'});
  }catch(error){reports.push({renderer:gpu?'webgl2':'canvas',status:'failed',reason:error.message});}
}
console.log(JSON.stringify({status:reports.every(x=>x.status==='passed')?'passed':'failed',reports,browser_pixels_verified:false},null,2));
assert(reports.every(x=>x.status==='passed'),'Narrow spectrum band regressions');
