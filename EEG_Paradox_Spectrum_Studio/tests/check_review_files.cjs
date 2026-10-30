/* Real Blob serialization and asynchronous file handlers, with mocked graphics. */
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),{harness}=require('./viewer_harness.cjs');
(async()=>{
  const h=harness(fs.readFileSync(process.argv[2],'utf8')),{nodes,flush}=h;flush();
  const api=h.review(),snapshot=api.workspace();
  const events=Array.from({length:7000},(_,i)=>({id:'long-review:'+i,onset_seconds:10+(i%60),duration_seconds:1,
    label:'E'.repeat(500),kind:'task',status:'proposed',channels:[],origin:'R'.repeat(200)}));
  api.importEvents({schema:'eeg-paradox.spectrum-events.v1',fingerprint:snapshot.fingerprint,events});flush();
  nodes.get('saveState').click();flush();
  const saved=h.downloads.findLast(x=>x.name==='spectrum_workspace.json'),blob=h.blobs.get(saved.url);
  assert(blob.size>2_000_000,'Fixture must reproduce the previous workspace import limit');
  nodes.get('workspaceFile').files=[blob];await nodes.get('workspaceFile').fire('change');flush();
  assert.equal(nodes.get('message').textContent,'Review data loaded.','A workspace saved by the viewer must reload');
  assert.equal(api.workspace().review.events.length,events.length+snapshot.review.events.length);
  // Save/import events uses the same bound; keep supplied review status intact.
  nodes.get('saveEvents').click();const eventFile=h.downloads.findLast(x=>x.name==='spectrum_events.json');
  nodes.get('eventsFile').files=[h.blobs.get(eventFile.url)];await nodes.get('eventsFile').fire('change');flush();
  assert.equal(nodes.get('message').textContent,'Review data loaded.');
  const before=JSON.stringify(api.workspace());let oversizedRead=false;
  nodes.get('workspaceFile').files=[{size:32_000_001,text:async()=>{oversizedRead=true;throw new Error('must not read oversized file');}}];
  await nodes.get('workspaceFile').fire('change');flush();
  assert.equal(oversizedRead,false);assert.match(nodes.get('message').textContent,/exceeds 32 MB/);
  assert.equal(JSON.stringify(api.workspace()),before);
  nodes.get('workspaceFile').files=[new Blob(['{malformed'])];await nodes.get('workspaceFile').fire('change');flush();
  assert.match(nodes.get('message').textContent,/Import refused/);assert.equal(JSON.stringify(api.workspace()),before);
  console.log(JSON.stringify({status:'passed',events:events.length,workspace_bytes:blob.size,large_save_load:true,
    asynchronous_file_handlers:true,oversized_and_malformed_refusal_atomic:true,browser_pixels_verified:false},null,2));
})().catch(error=>{console.error(error);process.exitCode=1;});
