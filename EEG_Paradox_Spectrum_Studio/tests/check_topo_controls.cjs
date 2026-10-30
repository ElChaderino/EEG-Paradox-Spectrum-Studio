/* Select every retained EEG channel; verify exact powers and linked displays. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs');
const {harness}=require('./viewer_harness.cjs');
const html=fs.readFileSync(process.argv[2],'utf8'),reports=[];
for(const gpu of [true,false]){
  const h=harness(html,{gpu}),{nodes,payload}=h,diag=h.diagnostics,api=h.review();
  const click=id=>{nodes.get(id).click();h.flush();};
  h.flush();click('mapTab');
  const mapped=nodes.get('mapChannelChoices').children,other=nodes.get('mapOtherChoices').children,buttons=[...mapped,...other];
  assert.equal(mapped.length,19);assert.equal(other.length,2);assert.equal(buttons.length,payload.metadata.channel_names.length);
  assert.deepEqual(other.map(x=>x.children[0].textContent),['A1','A2']);
  const row=payload.times.length-1,F=payload.frequencies,C=F.length,N=payload.times.length;
  const raw=Buffer.from(payload.psd_packed.base64,'base64'),df=payload.metadata.bin_spacing_hz;
  for(let ch=0;ch<payload.metadata.channel_names.length;ch++){
    const button=buttons.find(x=>x.value===String(ch));assert(button);
    button.click();h.flush();
    assert.equal(diag().channel,payload.metadata.channel_names[ch]);
    assert.equal(nodes.get('channel').value,String(ch));assert(button.classList.contains('selected'));
    assert.equal(buttons.filter(x=>x.classList.contains('selected')).length,1);
    let power=0;for(let f=0;f<C;f++)if(F[f]>=8&&F[f]<13)power+=raw.readFloatLE(((ch*N+row)*C+f)*4)*df;
    assert.equal(button.children[1].textContent,power.toFixed(2));
    assert(nodes.get('mapSelection').textContent.includes('Selected: '+payload.metadata.channel_names[ch]+' ·'));
  }
  assert.match(nodes.get('mapSelection').textContent,/A2.*no saved position/);
  assert.match(nodes.get('mapExcluded').textContent,/Foto/);
  assert.equal(diag().review.map.sites,19);
  // The canvas itself must select each mapped site, including nearby sensors.
  const g=diag().review.map.geometry;
  for(const [name,xy] of Object.entries(payload.review.montage.positions_xy)){
    nodes.get('scalpMap').fire('click',{clientX:g.cx+xy[0]*g.radius,clientY:g.cy-xy[1]*g.radius});h.flush();
    assert.equal(diag().channel,name,'Nearest hit selects '+name);
  }
  nodes.get('linked').checked=true;nodes.get('linked').fire('change');h.flush();
  buttons.find(x=>x.value===String(payload.metadata.channel_names.indexOf('O1'))).click();h.flush();
  assert.equal(diag().review.panels[0],'O1','Selected site must be visible in a full linked layout');
  assert.equal(diag().review.panels.length,4);assert.equal(new Set(diag().review.panels).size,4);
  click('expandMap');assert.equal(api.workspace().view.mapExpanded,true);
  assert(h.document.body.classList.contains('topo-expanded'));
  const expanded=api.workspace();click('summaryTab');assert(!h.document.body.classList.contains('topo-expanded'));
  click('mapTab');assert(h.document.body.classList.contains('topo-expanded'));
  click('expandMap');assert.equal(api.workspace().view.mapExpanded,false);
  api.importWorkspace(expanded);h.flush();assert(h.document.body.classList.contains('topo-expanded'));
  const legacy=JSON.parse(JSON.stringify(expanded));delete legacy.view.mapExpanded;
  api.importWorkspace(legacy);h.flush();assert.equal(api.workspace().view.mapExpanded,false);
  const invalid=JSON.parse(JSON.stringify(expanded));invalid.view.mapExpanded='yes';assert.throws(()=>api.importWorkspace(invalid));
  invalid.view.mapExpanded=true;invalid.view.focus=true;assert.throws(()=>api.importWorkspace(invalid));
  click('expandMap');click('focus');assert.equal(api.workspace().view.mapExpanded,false);
  assert(!h.document.body.classList.contains('topo-expanded'));click('focus');
  reports.push({renderer:gpu?'webgl2':'canvas',selectable_channels:buttons.length,mapped_sites:19,unplaced_channels:2,
    all_channel_band_powers_exact:true,all_sensor_hits_select_correctly:true,linked_selection_visible:true,
    expanded_map_workspace_roundtrip:true,old_workspaces_supported:true,browser_pixels_verified:false});
}
console.log(JSON.stringify({status:'passed',reports},null,2));
