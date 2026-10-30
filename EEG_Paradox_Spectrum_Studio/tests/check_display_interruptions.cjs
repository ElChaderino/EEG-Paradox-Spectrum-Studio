/* Performance checks must not compete with playback or export temporary cursors. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs');
const {harness}=require('./viewer_harness.cjs');
const html=fs.readFileSync(process.argv[2],'utf8');
(async()=>{
  const reports=[];
  for(const gpu of [true,false])for(const action of ['play','resize','hidden','image','workspace','map_image','topo_size']){
    const h=harness(html,{gpu}),{nodes,flush,diagnostics:diag}=h;flush();
    if(['map_image','topo_size'].includes(action)){nodes.get('mapTab').click();flush();}
    nodes.get('time').value='17';nodes.get('time').fire('input');flush();
    nodes.get('benchmark').click();for(let i=0;i<130;i++)flush(34);
    const previous=JSON.stringify(diag().lastBenchmark);assert(diag().lastBenchmark);
    nodes.get('benchmark').click();for(let i=0;i<11;i++)flush(34);
    assert.equal(nodes.get('benchmark').disabled,true);assert.notEqual(diag().row,17);
    if(action==='play')nodes.get('play').click();
    if(action==='resize'){
      nodes.get('ridge').getBoundingClientRect=()=>({width:850,height:600,left:0,top:0});
      nodes.get('ridgeFallback').getBoundingClientRect=()=>({width:850,height:600,left:0,top:0});
      h.windowHandlers.get('resize')();
    }
    if(action==='hidden'){h.document.hidden=true;h.documentHandlers.get('visibilitychange')();}
    if(action==='image')nodes.get('savePng').click();
    if(action==='workspace')nodes.get('saveState').click();
    if(action==='map_image')nodes.get('saveMap').click();
    if(action==='topo_size')nodes.get('expandMap').click();
    flush(34);
    try{
      assert.equal(nodes.get('benchmark').disabled,false,'unfinished check must be cancelled');
      assert.equal(diag().row,17,'restore the selected recording cursor');
      assert.equal(diag().playing,action==='play');
      for(let i=0;i<160;i++)flush(51);
      assert.equal(JSON.stringify(diag().lastBenchmark),previous,'retain the last completed report');
      if(action==='image'){
        assert(h.downloads.some(x=>x.name==='eeg_spectrum.png'));
        assert(h.canvasText.some(x=>String(x.value).includes('window center '+h.payload.times[17].toFixed(2)+' s')));
      }
      if(action==='workspace'){
        const saved=h.downloads.findLast(x=>x.name==='spectrum_workspace.json');
        const data=JSON.parse(await h.blobs.get(saved.url).text());assert.equal(data.view.row,17);
      }
      if(action==='map_image'&&diag().review.map.available){
        assert(h.downloads.some(x=>x.name==='spectrum_scalp_map.png'));
        assert(h.canvasText.some(x=>String(x.value).includes('center '+h.payload.times[17].toFixed(2)+' s')));
      }
      reports.push({renderer:gpu?'webgl2':'canvas',action,status:'passed'});
    }catch(error){reports.push({renderer:gpu?'webgl2':'canvas',action,status:'failed',reason:error.message});}
  }
  console.log(JSON.stringify({status:reports.every(x=>x.status==='passed')?'passed':'failed',reports,browser_pixels_verified:false},null,2));
  assert(reports.every(x=>x.status==='passed'),'Display-check interruptions failed');
})().catch(error=>{console.error(error.message);process.exitCode=1;});
