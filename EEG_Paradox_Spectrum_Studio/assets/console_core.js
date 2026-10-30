/* SPDX-License-Identifier: GPL-3.0-only. Portable layouts, clock geometry and local diagnostics. */
'use strict';
(function(root){
  const finite=Number.isFinite,own=(o,k)=>Object.prototype.hasOwnProperty.call(o,k);
  const check=(ok,message)=>{if(!ok)throw new Error(message);};
  const markerIntervals=Object.freeze([0,5,10,15,30,60,120,300,600,900,1800,3600]);
  const layoutLimit=32,fileLimit=256_000;
  const viewRanges={low:[0,1e9],high:[0,1e9],floor:[-90,20],range:[10,90],perspective:[.2,.65],lift:[.4,1.6]};
  const choices={palette:['sdr','thermal','ice'],quality:['balanced','high'],depth:[32,64,96,128],
    dock:['summary','compare','trend','map','ab','events'],speed:[1,2,4]};
  function label(value,max){return typeof value==='string'&&value.trim().length>0&&value.length<=max&&!/[\u0000-\u001f\u007f]/.test(value);}
  function names(value,min,max){return Array.isArray(value)&&value.length>=min&&value.length<=max&&value.every(x=>label(x,500))&&new Set(value).size===value.length;}
  function layout(value){
    check(value&&label(value.name,80)&&value.name===value.name.trim(),'Use a nonempty layout name of at most 80 characters');
    const v=value.view,r=value.review;check(v&&r,'Layout needs display settings');
    const view={};
    for(const [key,[lo,hi]] of Object.entries(viewRanges)){check(finite(v[key])&&v[key]>=lo&&v[key]<=hi,'Invalid layout '+key);view[key]=v[key];}
    check(view.low<view.high,'Invalid layout frequency crop');
    for(const [key,allowed] of Object.entries(choices)){check(allowed.includes(v[key]),'Invalid layout '+key);view[key]=v[key];}
    for(const key of ['scopeCentered','focus']){check(typeof v[key]==='boolean','Invalid layout '+key);view[key]=v[key];}
    check(label(value.active_channel,500)&&names(value.panel_channels,1,4)&&names(value.comparison_channels,0,8),'Invalid layout channel labels');
    check(typeof r.linked==='boolean'&&[15,30,60].includes(r.fps)&&finite(r.mapMax)&&r.mapMax>0&&r.mapMax<=1e9,'Invalid layout review controls');
    check(Array.isArray(r.band)&&r.band.length===2&&r.band.every(finite)&&r.band[0]>=0&&r.band[0]<r.band[1]&&r.band[1]<=1e9,'Invalid layout inspection band');
    check(typeof r.mapBand==='string'&&['custom','delta','theta','alpha','smr','beta','high_beta','gamma_30_45'].includes(r.mapBand),'Invalid layout map band');
    check(markerIntervals.includes(r.markerSeconds),'Invalid layout clock interval');
    return {name:value.name,active_channel:value.active_channel,panel_channels:[...value.panel_channels],comparison_channels:[...value.comparison_channels],view,
      review:{linked:r.linked,fps:r.fps,mapMax:r.mapMax,mapBand:r.mapBand,band:[...r.band],markerSeconds:r.markerSeconds}};
  }
  function library(payload){
    check(payload&&payload.schema==='eeg-paradox.spectrum-layouts.v1'&&Array.isArray(payload.layouts)&&payload.layouts.length<=layoutLimit,'Invalid layout library or more than 32 layouts');
    const layouts=payload.layouts.map(layout),keys=layouts.map(x=>x.name.toLowerCase());
    check(new Set(keys).size===keys.length,'Layout names must be unique');
    const result={schema:'eeg-paradox.spectrum-layouts.v1',layouts};
    check(new Blob([JSON.stringify(result)]).size<=fileLimit,'Layout library exceeds 256 KB; export fewer layouts');
    return result;
  }
  function capture(name,workspace,data){
    const v=workspace.view,r=workspace.review,channels=data.metadata.channel_names;
    return layout({name:name.trim(),view:v,active_channel:channels[v.ch],panel_channels:r.panels.map(ch=>channels[ch]),
      comparison_channels:v.compare.map(ch=>channels[ch]),review:{...r,markerSeconds:r.markerSeconds??0}});
  }
  function resolve(value,workspace,data){
    const preset=layout(value),channels=data.metadata.channel_names;
    const channel=name=>{
      const exact=channels.indexOf(name);if(exact>=0)return exact;
      const matches=channels.flatMap((candidate,i)=>candidate.toLowerCase()===name.toLowerCase()?[i]:[]);
      check(matches.length===1,'Layout channel unavailable or ambiguous: '+name);return matches[0];
    };
    const panels=preset.panel_channels.map(channel),compare=preset.comparison_channels.map(channel);
    check(new Set(panels).size===panels.length&&new Set(compare).size===compare.length,'Layout channel labels resolve to duplicate channels');
    // Preserve this recording's fingerprint, cursor, intervals, events and comparison context.
    return {...workspace,view:{...workspace.view,...preset.view,ch:channel(preset.active_channel),compare},
      review:{...workspace.review,...preset.review,band:[...preset.review.band],panels}};
  }
  function merge(current,incoming){
    const result=library(current),imported=library(incoming),byName=new Map(result.layouts.map(x=>[x.name.toLowerCase(),x]));
    for(const item of imported.layouts){
      const previous=byName.get(item.name.toLowerCase());
      if(!previous||JSON.stringify(previous)===JSON.stringify(item)){byName.set(item.name.toLowerCase(),item);continue;}
      const base=item.name.slice(0,60).trimEnd();let counter=1;
      while(counter<=layoutLimit){
        const name=base+' (imported'+(counter===1?'':' '+counter)+')',renamed={...item,name},existing=byName.get(name.toLowerCase());
        if(!existing||JSON.stringify(existing)===JSON.stringify(renamed)){byName.set(name.toLowerCase(),renamed);break;}
        counter++;
      }
      check(counter<=layoutLimit,'Layout name conflicts exceed the library limit');
    }
    return library({schema:result.schema,layouts:[...byName.values()]});
  }
  function timeMarkers(times,valid,row,visibleRows,seconds,hop,phase=0,maxLines=40){
    check(markerIntervals.includes(seconds),'Invalid clock interval');
    if(!seconds)return {markers:[],effective_seconds:0};
    check(Number.isInteger(row)&&row>=0&&row<times.length&&finite(hop)&&hop>0&&finite(phase)&&phase>=0&&phase<1,'Invalid clock geometry');
    const count=Math.max(1,Math.min(400,Math.floor(visibleRows))),first=Math.max(0,row-count+1);
    const maximum=Math.max(1,Math.min(64,Math.floor(maxLines)||1));
    const effective=seconds*Math.max(1,Math.ceil((times[row]-times[first])/seconds/maximum));
    const markers=[],seen=new Set(),tolerance=Math.max(1e-8,hop*1e-7);
    for(let i=first;i<=row;i++){
      if(!valid[i]||!finite(times[i]))continue;
      const boundary=Math.floor((times[i]+tolerance)/effective)*effective;
      if(boundary<times[first]-tolerance||seen.has(boundary))continue;
      const exact=Math.abs(times[i]-boundary)<=tolerance;
      const continuous=i>0&&valid[i-1]&&Math.abs(times[i]-times[i-1]-hop)<=tolerance;
      if(!exact&&(!continuous||boundary<=times[i-1]+tolerance||boundary>times[i]))continue;
      const ageRows=row-i+(times[i]-boundary)/hop+.5+phase;
      if(ageRows<0||ageRows>=count)continue;
      markers.push({seconds:boundary,ageRows});seen.add(boundary);
    }
    return {markers,effective_seconds:effective};
  }
  const eventCodes=Object.freeze(['viewer_ready','webgl_init_failed','webgl_draw_failed','context_lost','context_restored',
    'webgl_restore_failed','runtime_error','unhandled_rejection','benchmark_complete','layout_saved','layout_applied','layout_imported','layout_refused']);
  const counterKeys=['stageFrames','fftDraws','scopeDraws','bandUpdates','comparisonDraws','waterfallCacheBuilds','scheduledFrames','coalescedRequests','canvasResizes',
    'historyUploads','historyBytesUploaded','meshUploads','paletteUploads','frames','drawCalls','lastFrameDrawCalls','mapDraws','abDraws','overlayDraws','trendDraws','trendBuilds','timeMarkerDraws'];
  const number=(value,maximum=1e12)=>finite(value)&&value>=0&&value<=maximum?value:null;
  const counters=value=>Object.fromEntries(counterKeys.filter(key=>own(value||{},key)).map(key=>[key,number(value[key])]));
  function diagnosticHistory(events){
    return (Array.isArray(events)?events:[]).slice(-64).filter(e=>eventCodes.includes(e?.code)).map(e=>{
      const entry={code:e.code,elapsed_ms:number(e.elapsed_ms),session:Number.isInteger(e.session)&&e.session>=0&&e.session<=1e9?e.session:0};
      for(const key of ['line','column'])if(Number.isInteger(e[key])&&e[key]>0&&e[key]<=1e7)entry[key]=e[key];
      return entry;
    });
  }
  function diagnostics(snapshot){
    const s=snapshot||{},performance=s.lastBenchmark||{};
    const report={schema:'eeg-paradox.spectrum-diagnostics.v1',app_version:'0.6.8',surface:'viewer',
      privacy:'Local export: app status and rendering measurements only. No EEG, recording identifiers, source paths, events or channel labels.',
      renderer:['webgl2','canvas'].includes(s.renderer)?s.renderer:'unknown',
      browser_family:['edge','chrome','firefox','safari','other'].includes(s.browser_family)?s.browser_family:'other',
      session_elapsed_ms:number(s.session_elapsed_ms),graphics_quality:['balanced','high'].includes(s.quality)?s.quality:'unknown',
      viewport:{width:number(s.viewport?.w,1e5),height:number(s.viewport?.h,1e5),pixel_ratio:number(s.viewport?.dpr,10)},
      display:{target_fps:[15,30,60].includes(s.target_fps)?s.target_fps:null,panel_count:number(s.panel_count,4),
        history_rows:[32,64,96,128].includes(s.historyRows)?s.historyRows:null,dock:choices.dock.includes(s.dock)?s.dock:'unknown',
        marker_seconds:markerIntervals.includes(s.markerSeconds)?s.markerSeconds:0,playing:s.playing===true},
      counters:counters(s.metrics),gpu:s.gpu?counters(s.gpu):null,review_counters:counters(s.reviewMetrics),
      recent_events:diagnosticHistory(s.diagnosticEvents),
      performance_check:null};
    if(s.lastBenchmark){
      report.performance_check={gpu_time_measured:false,renderer:['webgl2','canvas'].includes(performance.renderer)?performance.renderer:'unknown',
        graphics_quality:['balanced','high'].includes(performance.graphics_quality)?performance.graphics_quality:'unknown',
        panel_count:Array.isArray(performance.channels)?number(performance.channels.length,4):null};
      for(const key of ['frames','elapsed_ms','observed_fps','target_fps','javascript_frame_ms_median','javascript_frame_ms_p95',
        'history_uploads_during_check','geometry_uploads_during_check','palette_uploads_during_check','canvas_resizes_during_check','draw_calls_per_gpu_frame'])
        report.performance_check[key]=number(performance[key]);
    }
    return report;
  }
  const api={markerIntervals,layoutLimit,fileLimit,layout,library,capture,resolve,merge,timeMarkers,eventCodes,diagnosticHistory,diagnostics};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  root.SpectrumConsoleCore=api;
})(typeof window==='undefined'?globalThis:window);
