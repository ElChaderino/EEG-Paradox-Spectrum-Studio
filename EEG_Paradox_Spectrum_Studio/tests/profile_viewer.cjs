/* Repeatable JS workload comparison. GL and Canvas calls are mocked. */
'use strict';
const fs=require('node:fs'),{performance}=require('node:perf_hooks');
const {harness}=require('./viewer_harness.cjs');
const paths=process.argv.slice(2),reports=[];
for(const path of paths){
  const html=fs.readFileSync(path,'utf8'),h=harness(html,{gpu:true}),N=h.payload.times.length;
  h.flush();
  const step=row=>{h.nodes.get('time').value=String(row);h.nodes.get('time').fire('input');h.flush();};
  for(let i=0;i<5;i++)step(N-1-i);
  const before={...h.counts},samples=[];
  for(let i=0;i<80;i++){
    const start=performance.now();step(Math.max(0,N-1-i));samples.push(performance.now()-start);
  }
  const sorted=samples.slice().sort((a,b)=>a-b),after=h.counts;
  reports.push({input:path,renderer:h.diagnostics().renderer,frames:80,
    median_js_ms:sorted[39],p95_js_ms:sorted[75],total_js_ms:samples.reduce((a,b)=>a+b,0),
    buffer_uploads:after.bufferUploads-before.bufferUploads,texture_uploads:after.textureUploads-before.textureUploads,
    draw_calls:after.draws-before.draws,scope_repaints:after.scopePaints-before.scopePaints,
    waterfall_image_allocations:after.waterfallImages-before.waterfallImages});
}
console.log(JSON.stringify({measurement:'Node JavaScript workload with mocked GPU/Canvas; not browser frame rate or GPU time',reports},null,2));
