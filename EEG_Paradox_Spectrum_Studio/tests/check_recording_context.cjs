/* Metadata-derived maps and refusals in both display paths. No pixel assertions. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs');
const Core=require('../assets/review_core.js'),{harness}=require('./viewer_harness.cjs');
const html=fs.readFileSync(process.argv[2],'utf8');
const regex=/(<script id="payload" type="application\/json">)([\s\S]*?)(<\/script>)/;
const original=JSON.parse(html.match(regex)[2]);
const reports=[];
for(const gpu of [true,false])for(const scenario of ['detected','sequential','unknown','no_positions','invalid_window']){
  const payload=structuredClone(original);
  if(scenario==='sequential')payload.metadata.simultaneous=false;
  if(scenario==='unknown')delete payload.metadata.recording_context;
  if(scenario==='no_positions')delete payload.review.montage;
  if(scenario==='invalid_window')payload.valid.forEach(rows=>rows[rows.length-1]=false);
  const page=html.replace(regex,(_,prefix,body,suffix)=>prefix+JSON.stringify(payload).replaceAll('<','\\u003c')+suffix);
  const h=harness(page,{gpu});h.flush();h.nodes.get('mapTab').click();h.flush();
  const expected=scenario==='detected';
  assert.equal(h.diagnostics().review.map.available,expected,scenario);
  assert.equal(h.nodes.get('saveMap').disabled,!expected,scenario);
  assert.equal(h.nodes.get('mapChannelChoices').children.length+h.nodes.get('mapOtherChoices').children.length,payload.metadata.channel_names.length);
  h.nodes.get('saveMap').click();h.flush();
  assert.equal(h.downloads.some(x=>x.name==='spectrum_scalp_map.png'),expected,scenario);
  if(expected){
    assert.match(h.nodes.get('mapCaption').textContent,/19 \/ 19 valid sites.*WinEEG embedded montage.*hardware simultaneity unconfirmed/);
    const crop=h.canvasDraws.find(x=>x.id===''&&x.args.length===8);
    assert(crop&&crop.args[6]>=600&&crop.args[7]>=600,'The exported head must fill the report area, independently of dock aspect ratio');
    assert(h.canvasText.some(x=>x.id===''&&x.value==='No saved position'));
    assert(h.canvasText.some(x=>x.id===''&&x.value.startsWith('A1: ')));
  }
  reports.push({renderer:gpu?'webgl2':'canvas',scenario,available:expected,exports_map:expected});
}
for(const [M,expected] of [[{},false],[{simultaneous:true},true],[{simultaneous:false,recording_context:{timing:{aligned:true}}},false],
  [{recording_context:{timing:{aligned:true}}},true],[{simultaneous:true,recording_context:{timing:{aligned:false}}},false]])assert.equal(Core.timeAligned(M),expected);
console.log(JSON.stringify({status:'passed',reports,browser_pixels_verified:false},null,2));
