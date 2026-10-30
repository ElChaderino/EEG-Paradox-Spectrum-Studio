/* SPDX-License-Identifier: GPL-3.0-only. Linked EEG review presentation. */
'use strict';
window.SpectrumReview=class SpectrumReview {
  constructor(bridge){
    const B=this.B=bridge,{$,data:D,state:S}=B,M=D.metadata,C=window.SpectrumReviewCore;
    this.C=C;this.names=M.channel_names;this.revision=0;this.keys={};this.mapWeights=null;this.abResult=null;
    this.metrics={mapDraws:0,abDraws:0,overlayDraws:0,trendDraws:0,trendBuilds:0,timeMarkerDraws:0};
    const start=M.selected_start_seconds,stop=M.selected_stop_seconds,mid=(start+stop)/2;
    const lo=Math.max(D.frequencies[0],Math.min(8,D.frequencies.at(-2))),hi=Math.min(D.frequencies.at(-1),Math.max(lo+M.bin_spacing_hz,13));
    this.settings={linked:false,panels:this.names.slice(0,Math.min(4,this.names.length)).map((_,i)=>i),
      fps:30,band:[lo,hi],mapBand:'custom',mapMax:200,a:[start,mid],b:[mid,stop],bSource:'primary',trend:[start,stop],markerSeconds:0,
      events:C.events(D.review?.events||[],this.names)};
    this.comparison=null;
    if(D.comparison){try{this.importComparison(D.comparison);this.settings.bSource='imported';}
      catch(error){B.text('message','Embedded comparison unavailable: '+error.message);}}
    delete D.comparison;
    this.panelBoxes=[];this.handles=[];
    this.mapButtons=[];
    const positions=D.review.montage?.positions_xy||{};
    this.names.forEach((name,ch)=>{
      const positioned=Object.hasOwn(positions,name),button=document.createElement('button'),label=document.createElement('b'),value=document.createElement('span');
      label.textContent=name;button.type='button';button.value=String(ch);button.className='map-channel';button.append(label,value);
      button.addEventListener('click',()=>B.selectChannel(ch));
      $(positioned?'mapChannelChoices':'mapOtherChoices').append(button);
      this.mapButtons.push({button,value,positioned});
    });
    const positionedCount=this.mapButtons.filter(x=>x.positioned).length,otherCount=this.names.length-positionedCount;
    B.text('mapChannelsHeading','Scalp channels ('+positionedCount+') · µV²');
    B.text('mapOthersHeading','No saved scalp position ('+otherCount+')');
    $('mapChannelsHeading').hidden=!positionedCount;$('mapOthersHeading').hidden=!otherCount;
    B.text('mapOthersCaption',otherCount?'These channels remain selectable for spectra and waveforms. Their positions are not drawn on the head.':'All retained EEG channels have saved scalp positions.');
    B.text('mapExcluded',M.excluded_channels?.length?'Other source channels: '+M.excluded_channels.join(', ')+'. Not included in this EEG run.':'');
    $('expandMap').addEventListener('click',()=>{
      B.cancelDisplayCheck('Display check cancelled because the topo size changed. Run it again.',true);
      S.mapExpanded=!S.mapExpanded;
      if(S.mapExpanded)document.body.classList.toggle('focus-mode',false);
      this.mapLayout();this.changed();
    });
    this.names.forEach((name,ch)=>{
      const label=document.createElement('label'),box=document.createElement('input');box.type='checkbox';box.value=String(ch);label.append(box,document.createTextNode(name));$('panelChoices').append(label);this.panelBoxes.push(box);
      box.addEventListener('change',()=>{
        const selected=this.panelBoxes.flatMap((x,i)=>x.checked?[i]:[]);
        if(!selected.length||selected.length>4){box.checked=!box.checked;B.text('message','Choose one to four panels.');return;}
        this.settings.panels=selected;$('panelPreset').value='custom';this.changed();
      });
    });
    for(const [name,pair] of Object.entries(M.bands_hz)){
      for(const id of ['bandPreset','mapBand']){
        const option=document.createElement('option');option.value=name;option.textContent=name.replaceAll('_',' ')+' · '+pair.join('–')+' Hz';
        option.disabled=id==='bandPreset'?!C.bandFits(D,pair):!C.mapBandAvailable(D,name,this.settings.band);
        if(option.disabled)option.textContent+=' · not retained';$(id).append(option);
      }
    }
    $('linked').addEventListener('change',()=>{this.settings.linked=$('linked').checked;this.changed();});
    $('panelPreset').addEventListener('change',()=>{
      const sets={frontal:['Fp1','Fp2','F3','F4'],posterior:['P3','P4','O1','O2'],bilateral:['F3','F4','C3','C4']},preset=sets[$('panelPreset').value];
      if(!preset)return;
      const canonical=x=>x.replace(/^EEG[ _-]+/i,'').replace(/[-_ ]REF$/i,'').toLowerCase();
      const selected=preset.map(label=>this.names.findIndex(name=>canonical(name)===label.toLowerCase())).filter(i=>i>=0);
      if(!selected.length){B.text('message','Those named sites are absent in this recording.');return;}
      this.settings.panels=selected;this.settings.linked=true;this.sync();this.changed();
    });
    $('displayFps').addEventListener('change',()=>{this.settings.fps=Number($('displayFps').value);this.changed();});
    $('markerSeconds').addEventListener('change',()=>{
      const seconds=Number($('markerSeconds').value);
      if(!window.SpectrumConsoleCore.markerIntervals.includes(seconds)){B.text('message','Choose a supported waterfall clock interval.');this.sync();return;}
      this.settings.markerSeconds=seconds;this.changed();
    });
    const setBand=()=>{try{this.setBand([Number($('bandLow').value),Number($('bandHigh').value)]);$('bandPreset').value='custom';}
      catch(error){B.text('message',error.message);this.sync();}};
    $('bandLow').addEventListener('change',setBand);$('bandHigh').addEventListener('change',setBand);
    $('bandPreset').addEventListener('change',()=>{
      const pair=M.bands_hz[$('bandPreset').value];if(pair){try{this.setBand(pair);}catch(error){B.text('message',error.message);this.sync();}}
    });
    $('mapBand').addEventListener('change',()=>{
      const name=$('mapBand').value;
      if(!C.mapBandAvailable(D,name,this.settings.band)){B.text('message','This complete map band was not retained.');this.sync();return;}
      this.settings.mapBand=name;this.changed();
    });
    $('mapMax').addEventListener('change',()=>{
      const value=Number($('mapMax').value);if(!Number.isFinite(value)||value<=0||value>1e9){B.text('message','Map maximum must be positive and at most 1e9 µV².');this.sync();return;}
      this.settings.mapMax=value;this.changed();
    });
    $('fitMapScale').addEventListener('click',()=>{
      B.cancelDisplayCheck('Display check cancelled to fit the scalp power scale.');B.stop();
      const powers=this.mapButtons.flatMap((item,ch)=>item.positioned?[this.mapPower(ch)]:[]);
      const maximum=C.fittedPowerMaximum(powers);
      if(maximum===null){B.text('message','No valid positioned channels are available to fit the scale.');this.changed();return;}
      this.settings.mapMax=maximum;this.sync();this.changed();
      B.text('message','Power scale fitted to this window and held fixed during playback.');
    });
    for(const name of ['map','ab','events','trend'])$(name+'Tab').addEventListener('click',()=>B.dock(name));
    for(const [id,index] of [['trendStart',0],['trendStop',1]])$(id).addEventListener('change',()=>{
      const pair=[...this.settings.trend];pair[index]=Number($(id).value);
      if(!$(id).value.trim()||!pair.every(Number.isFinite)||pair[0]<start||pair[1]>stop||pair[0]>=pair[1]){B.text('message','Trend interval must fit the retained selection.');this.sync();return;}
      this.settings.trend=pair;this.changed();
    });
    $('trendWhole').addEventListener('click',()=>{this.settings.trend=[start,stop];this.sync();this.changed();});
    $('saveTrendCSV').addEventListener('click',()=>{
      try{
        const series=this.trendSeries();if(!series.rows.length){B.text('message',this.emptyTrendMessage());return;}
        const blob=new Blob([C.trendCSV(D,S.ch,series)],{type:'text/csv;charset=utf-8'});
        if(blob.size>C.fileLimits.workspace){B.text('message','Trend CSV exceeds the local export limit; shorten the interval.');return;}
        B.download(blob,'spectrum_band_trend.csv');
      }catch(error){B.text('message',error.message);}
    });
    $('saveTrendPNG').addEventListener('click',()=>this.saveTrend());
    $('trendCanvas').addEventListener('click',event=>{
      if(!this.trendPlot)return;const box=$('trendCanvas').getBoundingClientRect(),p=this.trendPlot;
      const fraction=B.clamp((event.clientX-box.left-p.x)/p.w,0,1);this.jump(p.start+fraction*(p.stop-p.start));
    });
    for(const [id,key,index] of [['aStart','a',0],['aStop','a',1],['bStart','b',0],['bStop','b',1]])$(id).addEventListener('change',()=>{
      const pair=[...this.settings[key]];pair[index]=Number($(id).value);
      const target=key==='b'&&this.settings.bSource==='imported'?this.comparison:D;
      if(!$(id).value.trim()||!target||!pair.every(Number.isFinite)||pair[0]<target.metadata.selected_start_seconds||pair[1]>target.metadata.selected_stop_seconds||pair[0]>=pair[1]){
        B.text('message','Interval must fit the selected recording and have start < stop.');this.sync();return;
      }
      this.settings[key]=pair;this.changed();
    });
    $('bSource').addEventListener('change',()=>{
      const source=$('bSource').value;
      if(source==='imported'&&!this.comparison){B.text('message','Load an exported recording spectrum first.');this.sync();return;}
      this.settings.bSource=source;
      if(source==='imported')this.settings.b=[this.comparison.metadata.selected_start_seconds,this.comparison.metadata.selected_stop_seconds];
      else this.settings.b=[mid,stop];this.sync();this.changed();
    });
    this.fileControl('loadWorkspace','workspaceFile',C.fileLimits.workspace,payload=>this.importWorkspace(payload));
    this.fileControl('loadEvents','eventsFile',C.fileLimits.events,payload=>this.importEvents(payload));
    this.fileControl('loadComparison','comparisonFile',C.fileLimits.comparison,payload=>{
      this.importComparison(payload);this.settings.bSource='imported';this.sync();this.changed();
    });
    $('saveComparison').addEventListener('click',()=>this.json(this.comparisonPayload(),'spectrum_recording.json'));
    $('saveAB').addEventListener('click',()=>{this.drawAB(true);this.json(this.abResult,'spectrum_ab_review.json');});
    $('saveEvents').addEventListener('click',()=>this.json({schema:'eeg-paradox.spectrum-events.v1',fingerprint:D.review.fingerprint,
      source_sha256:M.source_sha256||null,events:this.settings.events},'spectrum_events.json'));
    $('addBookmark').addEventListener('click',()=>{
      const label=$('bookmarkLabel').value.trim();if(!label){B.text('message','Enter a bookmark label.');return;}
      B.cancelDisplayCheck('Display check cancelled to bookmark the selected window.');
      const id='bookmark:'+Date.now()+':'+this.revision;
      try{this.settings.events=C.events([...this.settings.events,{id,onset_seconds:D.times[S.row],duration_seconds:0,label,kind:'bookmark',status:'recorded',channels:[],origin:'user bookmark'}],this.names);
        $('bookmarkLabel').value='';this.changed();}catch(error){B.text('message',error.message);}
    });
    for(const [id,direction] of [['previousEvent',-1],['nextEvent',1]])$(id).addEventListener('click',()=>{
      if(B.cancelDisplayCheck('Display check cancelled for event navigation.'))B.changed(B.ROW);
      const events=this.visibleEvents(),time=D.times[S.row],next=direction<0?[...events].reverse().find(e=>e.onset_seconds<time-1e-6):events.find(e=>e.onset_seconds>time+1e-6);
      if(next)this.jump(next.onset_seconds);else B.text('message','No further event in this retained selection.');
    });
    $('saveMap').addEventListener('click',()=>{
      B.cancelDisplayCheck('Display check cancelled for scalp image export.',true);B.stop();
      this.drawMap(true);
      if(this.mapState?.available!==true){B.text('message',this.mapState?.reason||'No scalp map is available for this window.');return;}
      const canvas=document.createElement('canvas');canvas.width=1100;canvas.height=900;const ctx=canvas.getContext('2d');
      ctx.fillStyle='#050c17';ctx.fillRect(0,0,1100,900);ctx.fillStyle='#b7d5eb';ctx.font='20px Segoe UI';ctx.fillText('EEG PARADOX · SCALP BAND POWER',25,35);
      ctx.font='12px Consolas';ctx.fillText((M.synthetic?'SYNTHETIC · ':'')+M.source+' · '+M.condition+' · Reference: '+M.reference,25,61);
      const source=$('scalpMap'),g=this.mapState.geometry,pad=g.radius*1.25;
      const left=Math.max(0,g.cx-pad),top=Math.max(0,g.cy-pad),right=Math.min(g.width,g.cx+pad),bottom=Math.min(g.height,g.cy+pad);
      const width=right-left,height=bottom-top,scale=Math.min(1000/width,680/height);
      // Export the head region at report size, independent of a wide dock's aspect ratio.
      ctx.drawImage(source,left*source.width/g.width,top*source.height/g.height,width*source.width/g.width,height*source.height/g.height,
        (1100-width*scale)/2,105+(680-height*scale)/2,width*scale,height*scale);
      ctx.font='12px Consolas';ctx.fillText(this.mapState.band.join('–')+' Hz · center '+D.times[S.row].toFixed(2)+' s · 0–'+this.mapState.maximum_uv2+' µV² · selected '+this.names[S.ch]+(this.mapButtons[S.ch].positioned?'':' (not positioned)'),25,83,1050);
      const unplaced=this.mapButtons.flatMap((item,ch)=>item.positioned?[]:[{name:this.names[ch],value:item.value.textContent}]);
      unplaced.forEach((item,index)=>{
        const column=Math.floor(index/31),row=index%31,x=column?925:25;
        ctx.font='11px Consolas';
        if(!row)ctx.fillText('No saved position',x,127,150);
        ctx.fillText(item.name+': '+item.value+(item.value==='masked'?'':' µV²'),x,150+row*18,150);
      });
      ctx.font='11px Consolas';ctx.fillText('Thermal scale: dark = 0, light = '+this.mapState.maximum_uv2+' µV²; gray sites = missing',25,802);
      const caption=$('mapCaption').textContent;for(let i=0;i<caption.length;i+=145)ctx.fillText(caption.slice(i,i+145),25,820+Math.floor(i/145)*17);
      canvas.toBlob(blob=>{if(blob)B.download(blob,'spectrum_scalp_map.png');},'image/png');
    });
    this.sync();this.dock(S.dock);
    window.spectrumStudioReview={workspace:()=>this.workspace(),importWorkspace:value=>this.importWorkspace(value),
      importEvents:value=>this.importEvents(value),importComparison:value=>{this.importComparison(value);this.sync();this.changed();},
      comparisonPayload:()=>this.comparisonPayload()};
  }
  changed(){this.revision++;this.keys={};this.B.changed(this.B.ALL);}
  json(value,name){
    const limit=name==='spectrum_recording.json'?this.C.fileLimits.comparison:this.C.fileLimits.workspace;
    const blob=new Blob([JSON.stringify(value,null,2)],{type:'application/json'});
    if(blob.size>limit){this.B.text('message','Export exceeds '+Math.round(limit/1e6)+' MB; reduce the review selection before saving.');return false;}
    this.B.download(blob,name);return true;
  }
  saveWorkspace(){return this.json(this.workspace(),'spectrum_workspace.json');}
  sync(){
    const {$}=this.B,r=this.settings;
    for(const [id,value] of [['bandLow',r.band[0]],['bandHigh',r.band[1]],['displayFps',r.fps],['mapBand',r.mapBand],['mapMax',r.mapMax],
      ['aStart',r.a[0]],['aStop',r.a[1]],['bStart',r.b[0]],['bStop',r.b[1]],['bSource',r.bSource]])$(id).value=String(value);
    $('trendStart').value=String(r.trend[0]);$('trendStop').value=String(r.trend[1]);
    $('markerSeconds').value=String(r.markerSeconds);
    $('linked').checked=r.linked;this.panelBoxes.forEach((box,i)=>{box.checked=r.panels.includes(i);});
    $('bandPreset').value=Object.entries(this.B.data.metadata.bands_hz).find(([,pair])=>pair.every((v,i)=>v===r.band[i]))?.[0]||'custom';
  }
  setBand(pair){this.C.band(this.B.data,0,0,...pair);this.settings.band=[...pair];this.sync();this.changed();}
  panelChannels(){return this.settings.linked?this.settings.panels:[this.B.state.ch];}
  includeChannel(ch){
    const r=this.settings;
    if(r.linked&&!r.panels.includes(ch)){
      r.panels=[ch,...r.panels.slice(1)];this.B.$('panelPreset').value='custom';this.sync();this.keys={};
    }
  }
  mapLayout(){
    const expanded=this.B.state.mapExpanded===true;
    document.body.classList.toggle('topo-expanded',expanded&&this.B.state.dock==='map');
    this.B.$('expandMap').textContent=expanded?'Compact topo':'Expand topo';
    this.B.$('expandMap').setAttribute('aria-pressed',String(expanded));
  }
  dock(name){
    for(const key of ['map','ab','events','trend']){
      const active=key===name;this.B.$(key+'Pane').hidden=!active;this.B.$(key+'Tab').classList.toggle('selected',active);this.B.$(key+'Tab').setAttribute('aria-pressed',String(active));
    }
    document.body.classList.toggle('review-expanded',['map','ab','events','trend'].includes(name));this.mapLayout();this.keys={};
    this.B.changed(this.B.ALL);
  }
  fileControl(button,input,limit,consume){
    this.B.$(button).addEventListener('click',()=>this.B.$(input).click());
    this.B.$(input).addEventListener('change',async()=>{
      const field=this.B.$(input),file=field.files?.[0];if(!file)return;
      try{if(file.size>limit)throw new Error('Selected JSON file exceeds '+Math.round(limit/1e6)+' MB');consume(JSON.parse(await file.text()));this.B.text('message','Review data loaded.');}
      catch(error){this.B.text('message','Import refused: '+error.message);}finally{field.value='';}
    });
  }
  workspace(){
    const {data:D,state:S}=this.B,row=this.B.selectedRow();
    return {schema:'eeg-paradox.spectrum-workspace.v1',fingerprint:D.review.fingerprint,source:D.metadata.source,source_sha256:D.metadata.source_sha256||null,
      project:D.review.project||'Spectrum Studio',version:'0.6.8',window_center_seconds:D.times[row],
      view:{...S,row,playing:false,compare:[...S.compare],speed:Number(this.B.$('speed').value),focus:document.body.classList.contains('focus-mode')},review:JSON.parse(JSON.stringify(this.settings)),
      external_comparison:this.comparison?{fingerprint:this.comparison.fingerprint,source:this.comparison.metadata.source}:null};
  }
  importWorkspace(payload){
    const result=this.C.workspace(payload,this.B.data,this.comparison);
    if(result.review.bSource==='imported'&&(!this.comparison||payload.external_comparison?.fingerprint!==this.comparison.fingerprint))
      throw new Error('Load the matching comparison recording before this workspace');
    this.B.cancelDisplayCheck('Display check cancelled to restore the workspace.');
    this.B.stop();Object.assign(this.B.state,result.view,{compare:new Set(result.view.compare)});this.settings=result.review;
    const {$,state:S}=this.B;
    $('speed').value=String(S.speed);document.body.classList.toggle('focus-mode',S.focus);
    for(const [id,value] of [['channel',S.ch],['time',S.row],['fmin',S.low],['fmax',S.high],['floor',S.floor],['range',S.range],['depth',S.depth],['perspective',Math.round(S.perspective*100)],['lift',Math.round(S.lift*100)],['palette',S.palette],['quality',S.quality],['scopeMode',S.scopeCentered?'centered':'recorded']])$(id).value=String(value);
    Array.from($('compareControls').children).forEach((label,ch)=>{if(label.children[0])label.children[0].checked=S.compare.has(ch);});
    this.B.comparisonColors();this.B.drawRuler();this.sync();this.B.dock(S.dock);this.changed();
  }
  importEvents(payload){
    const D=this.B.data;
    if(!payload||payload.schema!=='eeg-paradox.spectrum-events.v1'||
      !(payload.fingerprint===D.review.fingerprint||(D.metadata.source_sha256&&payload.source_sha256===D.metadata.source_sha256)))throw new Error('Events belong to a different recording');
    const imported=this.C.events(payload.events,this.names),byID=new Map(this.settings.events.map(e=>[e.id,e]));
    for(const event of imported){
      if(byID.has(event.id)&&JSON.stringify(byID.get(event.id))!==JSON.stringify(event))throw new Error('Conflicting event ID: '+event.id);
      byID.set(event.id,event);
    }
    this.settings.events=this.C.events([...byID.values()],this.names);this.changed();
  }
  importComparison(payload){
    const comparison=this.C.decodeComparison(payload,this.B.unpack);this.C.compatible(this.B.data,comparison);
    this.comparison=comparison;this.settings.bSource='imported';this.settings.b=[comparison.metadata.selected_start_seconds,comparison.metadata.selected_stop_seconds];this.keys.ab=null;
  }
  comparisonPayload(){
    const {data:D}=this.B,bytes=new Uint8Array(D.linear.buffer,D.linear.byteOffset,D.linear.byteLength);let binary='';
    // Export uses explicitly little-endian float32, independent of host endian.
    const packed=new Uint8Array(bytes.length),view=new DataView(packed.buffer);
    for(let i=0;i<D.linear.length;i++)view.setFloat32(i*4,D.linear[i],true);
    for(let i=0;i<packed.length;i+=16384)binary+=String.fromCharCode(...packed.subarray(i,i+16384));
    return {schema:'eeg-paradox.spectrum-comparison.v1',fingerprint:D.review.fingerprint,metadata:D.metadata,frequencies:D.frequencies,times:D.times,valid:D.valid,
      psd_packed:{dtype:'<f4',shape:[this.names.length,D.times.length,D.frequencies.length],base64:btoa(binary)}};
  }
  visibleEvents(){
    if(this.visibleEventsRevision!==this.revision){
      const M=this.B.data.metadata;this.visibleEventsCache=this.settings.events.filter(e=>e.onset_seconds<=M.selected_stop_seconds&&e.onset_seconds+e.duration_seconds>=M.selected_start_seconds);
      this.visibleEventsRevision=this.revision;
    }
    return this.visibleEventsCache;
  }
  jump(time){
    this.B.selectRow(this.C.nearestRow(this.B.data.times,time));
  }
  draw(){
    const {state:S,data:D,$,fmt}=this.B,r=this.settings;
    const key=[S.ch,S.row,this.revision].join(':');
    if(this.keys.measure!==key){
      const result=this.C.band(D,S.ch,S.row,...r.band);this.bandResult=result;
      this.B.text('bandReadout',r.band.join('–')+' Hz · '+fmt(result.power)+' µV² · peak '+fmt(result.peakHz)+' Hz · '+result.bins+' bins');
      this.drawQuality();this.keys.measure=key;
    }
    this.drawOverlay();
    if(S.dock==='map')this.drawMap();
    if(S.dock==='ab')this.drawAB();
    if(S.dock==='trend')this.drawTrend();
    if(S.dock==='events'&&this.keys.events!==String(this.revision)){
      $('eventList').replaceChildren();
      for(const event of this.visibleEvents()){
        const row=document.createElement('div'),button=document.createElement('button'),status=document.createElement('span');row.className='event-row';
        button.textContent=fmt(event.onset_seconds)+' s · '+event.label;button.addEventListener('click',()=>this.jump(event.onset_seconds));
        status.textContent=event.kind+' · '+event.status+(event.channels.length?' · '+event.channels.join(', '):'');status.className=event.status;row.title=event.origin;
        row.append(button,status);
        if(event.origin==='user bookmark'){
          const remove=document.createElement('button');remove.textContent='Remove';remove.addEventListener('click',()=>{this.settings.events=this.settings.events.filter(e=>e.id!==event.id);this.changed();});row.append(remove);
        }
        $('eventList').append(row);
      }
      if(!this.visibleEvents().length){const empty=document.createElement('p');empty.textContent='No events in this retained interval. Add a bookmark or import reviewed events.';$('eventList').append(empty);}
      this.keys.events=String(this.revision);
    }
  }
  drawQuality(){
    const {data:D,state:S,$,fmt}=this.B,parent=$('qualityReadout'),Q=D.quality,index=S.ch*D.times.length+S.row;
    parent.replaceChildren();
    const line=(label,value,flag=false)=>{const row=document.createElement('div');row.className=flag?'quality-flag':'';row.textContent=label+': '+value;parent.append(row);};
    if(!Q.flags){line('Quality','Not retained in this older export; regenerate from calibrated input.');return;}
    const value=Q.flags[index],labels={1:'nonfinite samples',2:'BAD/EDGE overlap',4:'flat signal',8:'high amplitude',16:'declared rail reached',32:'line-noise ratio'};
    line('Window',D.valid[S.ch][S.row]?'retained':'masked',!D.valid[S.ch][S.row]);
    line('Peak to peak',fmt(Q.peak_to_peak_uv[index])+' µV');line('AC RMS',fmt(Q.rms_ac_uv[index])+' µV');
    for(const hz of [50,60])line(hz+' Hz / total',Number.isFinite(Q['line_'+hz+'_ratio'][index])?fmt(Q['line_'+hz+'_ratio'][index]*100,1)+'%':'unavailable at this Nyquist');
    line('Rails',D.metadata.quality_contract?.rail_check_available?'declared hardware bounds':'not supplied; clipping check unavailable');
    const flags=Object.entries(labels).filter(([bit])=>value&Number(bit)).map(([,label])=>label);
    line('Flags',flags.length?flags.join(', '):'none at the disclosed thresholds',!!flags.length);
  }
  drawOverlay(){
    const B=this.B,{data:D,state:S,$}=B,L=B.layout();if(!L)return;
    const smoothPhase=this.settings.markerSeconds||this.settings.events.length?B.phase():0;
    const channels=this.panelChannels(),key=[S.row,S.low,S.high,S.floor,S.range,this.revision,L.w,L.h,L.dpr,channels.join(','),smoothPhase].join(':');
    if(this.keys.overlay===key)return;this.keys.overlay=key;
    this.metrics.overlayDraws++;
    const {ctx,w,h}=B.canvas2d('reviewOverlay'),count=channels.length,paneW=w/count,r=this.settings;
    document.body.classList.toggle('multi-mode',count>1);ctx.clearRect(0,0,w,h);ctx.font='10px Consolas';
    const visible=this.visibleEvents().filter(e=>e.status!=='rejected').reverse();
    const rowCount=Math.min(D.times.length,400),hop=D.metadata.actual_hop_seconds;
    let effectiveInterval=0,markerCount=0;
    channels.forEach((ch,index)=>{
      const x0=index*paneW;
      ctx.save();ctx.beginPath();ctx.rect(x0,0,paneW,h);ctx.clip();
      ctx.strokeStyle='#44607c';ctx.lineWidth=1;if(index){ctx.beginPath();ctx.moveTo(x0,0);ctx.lineTo(x0,h);ctx.stroke();}
      if(count>1){
        ctx.fillStyle='#06101ddd';ctx.fillRect(x0,0,paneW,27);ctx.fillStyle='#b5d9ef';ctx.textAlign='left';
        const q=D.quality.flags?.[ch*D.times.length+S.row];ctx.fillText(this.names[ch]+' · '+B.fmt(D.times[S.row],1)+' s'+(q?' · FLAG':''),x0+9,17);
        ctx.fillStyle='#101b2e';ctx.fillRect(x0,L.ridgeCss,paneW,L.axisCss);ctx.fillStyle='#9cb6d0';
        const ticks=paneW<250?2:4;
        for(let i=0;i<=ticks;i++){ctx.textAlign=i===0?'left':i===ticks?'right':'center';ctx.fillText(B.fmt(S.low+(S.high-S.low)*i/ticks,1),x0+i/ticks*paneW+(i===0?9:i===ticks?-9:0),L.ridgeCss+15);}
      }
      const a=B.clamp((r.band[0]-S.low)/(S.high-S.low),0,1),b=B.clamp((r.band[1]-S.low)/(S.high-S.low),0,1);
      ctx.fillStyle='#72c7ef12';ctx.fillRect(x0+a*paneW,30,(b-a)*paneW,h-30);ctx.strokeStyle='#9fceec88';
      for(const edge of [a,b]){ctx.beginPath();ctx.moveTo(x0+edge*paneW,35);ctx.lineTo(x0+edge*paneW,h);ctx.stroke();}
      const grid=window.SpectrumConsoleCore.timeMarkers(D.times,D.valid[ch],S.row,rowCount,r.markerSeconds,hop,smoothPhase,Math.floor(L.waterCss/18));
      effectiveInterval=grid.effective_seconds;
      if(r.markerSeconds){
        ctx.save();ctx.setLineDash([3,4]);ctx.strokeStyle='#a8cce74d';ctx.lineWidth=.7;
        for(const marker of grid.markers){
          const y=L.waterY+marker.ageRows/rowCount*L.waterCss;
          ctx.beginPath();ctx.moveTo(x0,y);ctx.lineTo(x0+paneW,y);ctx.stroke();
          ctx.fillStyle='#06101ddd';ctx.fillRect(x0+paneW-77,y-12,71,12);ctx.fillStyle='#abc7db';ctx.textAlign='right';
          ctx.fillText(B.fmt(marker.seconds,1)+' s',x0+paneW-9,y-3);markerCount++;
        }
        ctx.restore();
      }
      let labelY=-Infinity;
      for(const event of visible){
        if(event.channels.length&&!event.channels.includes(this.names[ch]))continue;
        const age=(D.times[S.row]-event.onset_seconds)/hop,endAge=Math.max(0,(D.times[S.row]-event.onset_seconds-event.duration_seconds)/hop);
        if(age<0||endAge>=rowCount)continue;
        const y=L.waterY+Math.min(rowCount,age+.5+smoothPhase)/rowCount*L.waterCss;
        ctx.strokeStyle=event.status==='proposed'?'#d6a85c88':event.kind==='artifact'?'#ec8c8c99':'#7fe0ce88';
        if(event.duration_seconds){const top=L.waterY+(endAge+.5+smoothPhase)/rowCount*L.waterCss;
          ctx.fillStyle=event.status==='proposed'?'#d6a85c11':'#7fe0ce0c';ctx.fillRect(x0,top,paneW,y-top);}
        ctx.beginPath();ctx.moveTo(x0,y);ctx.lineTo(x0+paneW,y);ctx.stroke();
        if(y-labelY>13){ctx.fillStyle='#b8d1de';ctx.textAlign='left';ctx.fillText(event.label.slice(0,32)+' ['+event.status+']',x0+7,y-3);labelY=y;}
      }
      ctx.restore();
    });
    this.metrics.timeMarkerDraws+=markerCount;
    B.text('markerCaption',r.markerSeconds?'Original-time grid · '+B.fmt(effectiveInterval,0)+' s'+(effectiveInterval!==r.markerSeconds?' (spaced to fit the view)':'')+' · valid rows only.':'Optional original-time guides; masked windows stay missing.');
    this.updateHandles(channels,paneW);
  }
  updateHandles(channels,paneW){
    const B=this.B,{$,state:S}=B;
    if(this.handles.length!==channels.length*2){
      $('bandHandles').replaceChildren();this.handles=[];
      channels.forEach((_,pane)=>[0,1].forEach(edge=>{
        const handle=document.createElement('button');handle.className='band-handle';handle.textContent=edge?'›':'‹';handle.setAttribute('aria-label',(edge?'Upper':'Lower')+' EEG band edge, panel '+(pane+1));
        handle.title='Drag or use arrow keys to change inspection band';let dragging=false;
        handle.addEventListener('pointerdown',event=>{event.preventDefault();dragging=true;handle.setPointerCapture(event.pointerId);B.stop();});
        handle.addEventListener('pointermove',event=>{
          if(!dragging)return;
          const box=$('reviewOverlay').getBoundingClientRect(),value=S.low+(event.clientX-box.left-pane*paneW)/paneW*(S.high-S.low);
          this.moveEdge(edge,value);
        });
        handle.addEventListener('pointerup',()=>{dragging=false;});handle.addEventListener('pointercancel',()=>{dragging=false;});
        handle.addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();this.moveEdge(edge,this.settings.band[edge]+(event.key==='ArrowLeft'?-1:1)*B.data.metadata.bin_spacing_hz);}});
        $('bandHandles').append(handle);this.handles.push({handle,pane,edge});
      }));
    }
    for(const {handle,pane,edge} of this.handles){
      const value=this.settings.band[edge];handle.hidden=value<S.low||value>S.high;
      handle.style.left=(pane*paneW+(value-S.low)/(S.high-S.low)*paneW-8)+'px';
    }
  }
  moveEdge(edge,value){
    const F=this.B.data.frequencies,df=this.B.data.metadata.bin_spacing_hz;
    value=F[0]+Math.round((value-F[0])/df)*df;
    const pair=[...this.settings.band];pair[edge]=this.B.clamp(value,edge?pair[0]+df:F[0],edge?F.at(-1):pair[1]-df);
    try{this.setBand(pair);this.B.$('bandPreset').value='custom';}catch(error){this.B.text('message',error.message);}
  }
  drawMap(force=false){
    const B=this.B,{data:D,state:S,$,fmt}=B,r=this.settings,montage=D.review.montage,{ctx,w,h}=B.canvas2d('scalpMap');
    const key=[S.row,S.ch,this.revision,w,h,S.quality].join(':');if(!force&&this.keys.map===key)return;this.keys.map=key;
    this.metrics.mapDraws++;
    ctx.fillStyle='#050c17';ctx.fillRect(0,0,w,h);ctx.textAlign='left';ctx.font='10px Consolas';
    const pair=r.mapBand==='custom'?r.band:D.metadata.bands_hz[r.mapBand];
    this.drawMapChannels();
    const bandAvailable=this.C.mapBandAvailable(D,r.mapBand,r.band);
    $('fitMapScale').disabled=!bandAvailable||!this.mapButtons.some((item,ch)=>item.positioned&&this.mapPower(ch)!==null);
    if(!bandAvailable){
      const reason='This complete map band was not retained. Choose another band or run again with a wider spectrum range.';
      $('saveMap').disabled=true;B.text('mapCaption',reason);ctx.fillStyle='#b0c2d4';ctx.fillText('Map band unavailable',20,40);
      this.mapState={available:false,reason};this.mapHitPoints=[];return;
    }
    const noncollinear=points=>points.some((a,i)=>points.some((b,j)=>j>i&&points.some((c,k)=>k>j&&Math.abs((b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]))>1e-5)));
    const positioned=montage&&noncollinear(Object.values(montage.positions_xy));
    const aligned=this.C.timeAligned(D.metadata),sequential=D.metadata.simultaneous===false||D.metadata.recording_context?.timing?.aligned===false;
    const available=positioned&&aligned;
    $('saveMap').disabled=!available;
    if(!available){
      const problems=[],steps=[];
      if(!aligned){
        problems.push(sequential?'Channels are marked as recorded separately.':'Channel timing is unknown.');
        if(!sequential)steps.push('confirm "Recorded together? (override)" using acquisition records');
      }
      if(!positioned){problems.push('Electrode positions are missing or unusable.');steps.push('select the standard 10–20 template only if it matches the recording');}
      const reason=problems.join(' ')+(sequential?' A shared-time scalp map cannot be drawn for sequential channels.':
        ' In Recording & analysis, '+steps.join(' and ')+', then run again.');
      ctx.fillStyle='#b0c2d4';ctx.textAlign='center';ctx.font='14px Segoe UI';
      const top=Math.max(28,h*.33);
      ctx.fillText(sequential?'Scalp map unavailable for sequential channels':'Scalp map needs recording details',w/2,top,w-36);
      ctx.font='11px Consolas';
      const details=[];
      if(!aligned)details.push(sequential?'Channels were recorded separately.':'Recorded together: not confirmed');
      if(!positioned)details.push('Electrode positions: missing or unusable');
      details.forEach((line,i)=>ctx.fillText(line,w/2,top+25+i*19,w-36));
      B.text('mapCaption',reason);this.mapState={available:false,reason};return;
    }
    const points=[];
    for(const [name,xy] of Object.entries(montage.positions_xy)){
      const ch=this.names.indexOf(name);if(ch<0)continue;
      const power=this.mapPower(ch);if(power!==null)points.push({ch,xy,power});
    }
    const cx=w/2,cy=h/2+7,radius=Math.max(12,Math.min(w*.32,h*.36)),grid=64;
    const interpolate=points.length>=3&&noncollinear(points.map(p=>p.xy));
    if(interpolate){
      const positionKey=points.map(p=>p.ch).join(',');
      if(!this.mapWeights||this.mapWeights.key!==positionKey){
        const weights=new Float32Array(grid*grid*points.length);
        for(let y=0;y<grid;y++)for(let x=0;x<grid;x++){
          const px=(x+.5)/grid*2-1,py=1-(y+.5)/grid*2;let total=0;
          for(let k=0;k<points.length;k++){const [a,b]=points[k].xy,v=1/Math.max(1e-5,(px-a)**2+(py-b)**2);weights[(y*grid+x)*points.length+k]=v;total+=v;}
          for(let k=0;k<points.length;k++)weights[(y*grid+x)*points.length+k]/=total;
        }
        this.mapWeights={key:positionKey,weights};
      }
      const image=ctx.createImageData(grid,grid),lut=B.paletteData('thermal').bytes;
      for(let y=0;y<grid;y++)for(let x=0;x<grid;x++){
        if(((x+.5)/grid*2-1)**2+((y+.5)/grid*2-1)**2>1)continue;
        let value=0;for(let k=0;k<points.length;k++)value+=points[k].power*this.mapWeights.weights[(y*grid+x)*points.length+k];
        const color=Math.round(B.clamp(value/r.mapMax,0,1)*255)*4,offset=(y*grid+x)*4;image.data.set(lut.subarray(color,color+4),offset);
      }
      if(!this.mapTile)this.mapTile=document.createElement('canvas');this.mapTile.width=grid;this.mapTile.height=grid;this.mapTile.getContext('2d').putImageData(image,0,0);
      ctx.drawImage(this.mapTile,cx-radius,cy-radius,radius*2,radius*2);
    }
    ctx.strokeStyle='#96b1c9';ctx.lineWidth=1;ctx.beginPath();ctx.arc(cx,cy,radius,0,Math.PI*2);ctx.stroke();
    ctx.beginPath();ctx.moveTo(cx-radius*.1,cy-radius);ctx.lineTo(cx,cy-radius*1.14);ctx.lineTo(cx+radius*.1,cy-radius);ctx.stroke();
    for(const side of [-1,1]){ctx.beginPath();ctx.ellipse(cx+side*radius*1.04,cy,radius*.07,radius*.16,0,0,Math.PI*2);ctx.stroke();}
    const allPoints=Object.entries(montage.positions_xy);this.mapHitPoints=[];
    for(const [name,xy] of allPoints){
      const ch=this.names.indexOf(name),x=cx+xy[0]*radius,y=cy-xy[1]*radius,valid=points.some(p=>p.ch===ch);
      if(ch===S.ch){ctx.strokeStyle='#72eeed';ctx.lineWidth=1.5;ctx.beginPath();ctx.arc(x,y,7,0,Math.PI*2);ctx.stroke();}
      ctx.fillStyle=valid?(ch===S.ch?'#ffffff':'#b8d9f3'):'#666f7b';ctx.beginPath();ctx.arc(x,y,ch===S.ch?3.5:2.5,0,Math.PI*2);ctx.fill();
      const labelSize=radius>=120?11:9;ctx.font=labelSize+'px Consolas';ctx.textAlign='center';ctx.fillText(name.replace(/[-_ ]REF$/i,''),x,y-7);this.mapHitPoints.push({ch,x,y});
    }
    ctx.fillStyle='#b1c4da';ctx.textAlign='left';ctx.font='10px Consolas';ctx.fillText(pair.join('–')+' Hz · '+fmt(D.times[S.row],1)+' s · 0–'+r.mapMax+' µV²',10,15);
    ctx.font='9px Consolas';ctx.fillText('Thermal scale: dark = 0, light = '+r.mapMax+' µV² · gray sites = missing',10,h-7);
    const reason=interpolate?null:'Need at least three valid non-collinear sites to interpolate this window.';
    $('saveMap').disabled=!interpolate;
    B.text('mapCaption',points.length+' / '+allPoints.length+' valid sites. '+montage.source+'. '+this.C.timingDescription(D.metadata)+'. '+(reason||'Display interpolation only; click a sensor to select it.'));
    this.mapState={available:interpolate,reason,sites:points.length,band:[...pair],source:montage.source,maximum_uv2:r.mapMax,
      geometry:{cx,cy,radius,width:w,height:h}};
    if(!this.mapClickBound){
      $('scalpMap').addEventListener('click',event=>{const box=$('scalpMap').getBoundingClientRect(),x=event.clientX-box.left,y=event.clientY-box.top;
        const near=this.mapHitPoints?.reduce((closest,p)=>Math.hypot(p.x-x,p.y-y)<(closest?Math.hypot(closest.x-x,closest.y-y):14)?p:closest,null);
        if(near)B.selectChannel(near.ch);});this.mapClickBound=true;
    }
  }
  drawMapChannels(){
    const {state:S,fmt}=this.B;
    this.mapButtons.forEach(({button,value,positioned},ch)=>{
      const power=this.mapPower(ch);
      const selected=ch===S.ch,display=power===null?'masked':fmt(power,2);
      if(value.textContent!==display)value.textContent=display;
      button.classList.toggle('selected',selected);button.classList.toggle('masked',power===null);
      button.setAttribute('aria-pressed',String(selected));
      button.setAttribute('aria-label',this.names[ch]+': '+display+(power===null?'':' µV²')+(positioned?'; saved scalp position':'; no saved scalp position'));
      if(selected)this.B.text('mapSelection','Selected: '+this.names[ch]+' · '+display+(power===null?'':' µV²')+(positioned?'':' · no saved position'));
    });
  }
  mapPower(ch){
    const {data:D,state:S}=this.B,r=this.settings;
    try{return this.C.mapPower(D,ch,S.row,r.mapBand,r.band);}catch{return null;}
  }
  emptyTrendMessage(){return 'No complete '+this.B.data.metadata.actual_window_seconds+' s FFT window fits this interval. Widen the interval to review or export a trend.';}
  trendSeries(){
    const {data:D,state:S}=this.B,r=this.settings,key=[S.ch,...r.band,...r.trend].join(':');
    if(this.trendErrorKey===key)throw new Error(this.trendError);
    if(this.trendDataKey!==key){
      try{this.trendData=this.C.trend(D,S.ch,...r.band,...r.trend);this.trendDataKey=key;this.trendErrorKey=null;}
      catch(error){this.trendErrorKey=key;this.trendError=error.message;throw error;}
    }
    return this.trendData;
  }
  drawTrend(force=false,output=null){
    const B=this.B,{data:D,state:S,$,fmt}=B,r=this.settings,{canvas,ctx,w,h}=output||B.canvas2d('trendCanvas');
    let series;
    try{series=this.trendSeries();}catch(error){
      ctx.clearRect(0,0,w,h);B.text('trendCaption',error.message);this.trendPlot=null;
      $('saveTrendCSV').disabled=true;$('saveTrendPNG').disabled=true;return;
    }
    $('saveTrendCSV').disabled=!series.rows.length;$('saveTrendPNG').disabled=!series.rows.length;
    if(!series.rows.length){
      ctx.clearRect(0,0,w,h);ctx.fillStyle='#b1c4da';ctx.fillText('Widen the interval to include a complete FFT window.',20,40);
      B.text('trendCaption',this.emptyTrendMessage());this.trendPlot=null;return;
    }
    const baseKey=[this.trendDataKey,w,h,canvas.width,canvas.height,output?'export':'screen'].join(':');
    const key=baseKey+':'+S.row;if(!force&&this.keys.trend===key)return;this.keys.trend=key;this.metrics.trendDraws++;
    const p={x:output?75:60,y:output?28:20,w:Math.max(1,w-(output?100:80)),h:Math.max(1,h-(output?75:53)),start:r.trend[0],stop:r.trend[1]};
    if(!output)this.trendPlot=p;
    if(!this.trendTile||this.trendTileKey!==baseKey){
      const tile=this.trendTile||(this.trendTile=document.createElement('canvas'));tile.width=canvas.width;tile.height=canvas.height;
      const c=tile.getContext('2d'),dpr=canvas.width/w;c.setTransform(dpr,0,0,dpr,0,0);c.font=(output?'14':'9')+'px Consolas';c.fillStyle='#050c17';c.fillRect(0,0,w,h);
      const top=Math.max(1,(series.maximum_uv2||0)*1.08);c.strokeStyle='#21334b';c.fillStyle='#87a4be';
      for(let i=0;i<=4;i++){
        const y=p.y+i/4*p.h;c.beginPath();c.moveTo(p.x,y);c.lineTo(p.x+p.w,y);c.stroke();c.textAlign='right';c.fillText(fmt(top*(1-i/4),1),p.x-6,y+3);
        c.textAlign='center';c.fillText(fmt(p.start+(p.stop-p.start)*i/4,1),p.x+i/4*p.w,h-(output?27:10));
      }
      c.textAlign='left';c.fillText('Band power · µV²',10,output?16:11);c.textAlign='right';c.fillText('Original seconds',w-15,h-1);
      const bins=Array.from({length:Math.max(1,Math.floor(p.w))},()=>({lo:Infinity,hi:-Infinity,masked:false}));
      for(const row of series.rows){const k=Math.min(bins.length-1,Math.floor((row.center_seconds-p.start)/(p.stop-p.start)*bins.length)),bucket=bins[k];
        if(!row.valid)bucket.masked=true;else{bucket.lo=Math.min(bucket.lo,row.power_uv2);bucket.hi=Math.max(bucket.hi,row.power_uv2);}}
      c.fillStyle='#76818d30';for(let i=0;i<bins.length;i++)if(bins[i].masked)c.fillRect(p.x+i,p.y,1,p.h);
      c.strokeStyle='#69d1ef';c.lineWidth=1;c.beginPath();let previous=null;
      for(let i=0;i<bins.length;i++){
        const b=bins[i];if(b.masked){previous=null;continue;}if(!Number.isFinite(b.lo))continue;
        const x=p.x+i+.5,y=p.y+(1-b.hi/top)*p.h,bottom=p.y+(1-b.lo/top)*p.h;
        if(previous!==null){c.moveTo(previous[0],previous[1]);c.lineTo(x,y);}
        c.moveTo(x-.35,y);c.lineTo(x+.35,bottom);previous=[x,bottom];
      }
      c.stroke();this.trendTileKey=baseKey;this.metrics.trendBuilds++;
    }
    ctx.clearRect(0,0,w,h);ctx.drawImage(this.trendTile,0,0,w,h);
    const time=D.times[S.row];if(time>=p.start&&time<=p.stop){const x=p.x+(time-p.start)/(p.stop-p.start)*p.w;ctx.strokeStyle='#f6b569';ctx.beginPath();ctx.moveTo(x,p.y);ctx.lineTo(x,p.y+p.h);ctx.stroke();}
    B.text('trendCaption',this.names[S.ch]+' · '+r.band.join('–')+' Hz (upper edge excluded) · '+series.valid_windows+' valid / '+series.masked_windows+' masked complete windows · mean '+fmt(series.mean_uv2)+' µV² · median '+fmt(series.median_uv2)+' µV². Click to seek. Gray is missing; dense bins show min/max without bridging gaps. Overlapping windows are not independent.');
  }
  saveTrend(){
    const B=this.B,{data:D,state:S,$}=B;
    B.cancelDisplayCheck('Display check cancelled for trend image export.');B.stop();B.changed(B.ROW);
    this.drawTrend(true);if(!this.trendPlot){B.text('message',$('trendCaption').textContent);return;}
    const output=document.createElement('canvas');output.width=1920;output.height=900;const ctx=output.getContext('2d');
    ctx.fillStyle='#050c17';ctx.fillRect(0,0,1920,900);ctx.fillStyle='#c6e2f0';ctx.font='24px Segoe UI';ctx.fillText('EEG PARADOX / BAND-POWER TREND',30,38);
    ctx.font='14px Consolas';ctx.fillText(D.metadata.source+' · '+this.names[S.ch]+' · '+D.metadata.condition+' · Reference: '+D.metadata.reference,30,68);
    ctx.fillText('Interval '+this.settings.trend.join('–')+' original seconds · '+this.settings.band.join('–')+' Hz · µV²',30,94);
    const plot=document.createElement('canvas');plot.width=1860;plot.height=640;
    this.drawTrend(true,{canvas:plot,ctx:plot.getContext('2d'),w:plot.width,h:plot.height});
    ctx.drawImage(plot,30,120);ctx.font='12px Consolas';
    const lines=$('trendCaption').textContent.match(/.{1,180}(?:\s|$)|\S+/g)||[];
    lines.forEach((line,i)=>ctx.fillText(line.trim(),30,790+i*18,1860));
    ctx.fillText('Source SHA-256: '+(D.metadata.source_sha256||'in-memory data'),30,875);
    output.toBlob(blob=>{if(blob)B.download(blob,'spectrum_band_trend.png');else B.text('message','Trend image encoding failed.');},'image/png');
  }
  drawAB(force=false){
    const B=this.B,{data:D,state:S,$,fmt}=B,r=this.settings,{ctx,w,h}=B.canvas2d('abSpectrum');
    const key=[S.ch,S.low,S.high,S.floor,S.range,this.revision,w,h].join(':');if(!force&&this.keys.ab===key)return;this.keys.ab=key;
    this.metrics.abDraws++;
    ctx.fillStyle='#050c17';ctx.fillRect(0,0,w,h);ctx.textAlign='left';ctx.font='10px Consolas';
    const other=r.bSource==='imported'?this.comparison:D,ch=other?.metadata.channel_names.indexOf(this.names[S.ch]);
    if(!other||ch<0){B.text('abCaption','B recording is unavailable or does not contain '+this.names[S.ch]);this.abResult={available:false};return;}
    const a=this.C.average(D,S.ch,...r.a),b=this.C.average(other,ch,...r.b),x0=48,y0=20,pw=w-64,ph=h-47;
    ctx.strokeStyle='#21334b';ctx.fillStyle='#7c98b5';
    for(let i=0;i<=4;i++){
      const y=y0+i/4*ph;ctx.beginPath();ctx.moveTo(x0,y);ctx.lineTo(x0+pw,y);ctx.stroke();ctx.textAlign='right';ctx.fillText(fmt(S.floor+S.range*(1-i/4),0),x0-5,y+3);
      ctx.textAlign='center';ctx.fillText(fmt(S.low+(S.high-S.low)*i/4,1),x0+pw*i/4,h-9);
    }
    for(const [mean,color] of [[a,'#69d4f5'],[b,'#f6b569']]){
      ctx.strokeStyle=color;ctx.beginPath();let started=false;
      for(let f=0;f<D.frequencies.length;f++){
        const hz=D.frequencies[f],v=mean.values[f];if(hz<S.low||hz>S.high||!Number.isFinite(v))continue;
        const x=x0+(hz-S.low)/(S.high-S.low)*pw,y=y0+B.clamp(1-(10*Math.log10(Math.max(v,1e-12))-S.floor)/S.range,0,1)*ph;
        started?ctx.lineTo(x,y):ctx.moveTo(x,y);started=true;
      }ctx.stroke();
    }
    let pa=0,pb=0;
    for(let f=0;f<D.frequencies.length;f++)if(D.frequencies[f]>=r.band[0]&&D.frequencies[f]<r.band[1]){pa+=a.values[f]*D.metadata.bin_spacing_hz;pb+=b.values[f]*D.metadata.bin_spacing_hz;}
    this.abResult={schema:'eeg-paradox.spectrum-ab-review.v1',available:!!a.count&&!!b.count,channel:this.names[S.ch],
      primary_fingerprint:D.review.fingerprint,comparison_fingerprint:other.review?.fingerprint||other.fingerprint,
      a:{source:D.metadata.source,condition:D.metadata.condition,interval_seconds:[...r.a],valid_windows:a.count,masked_windows:a.masked,band_power_uv2:Number.isFinite(pa)?pa:null},
      b:{source:other.metadata.source,condition:other.metadata.condition,interval_seconds:[...r.b],valid_windows:b.count,masked_windows:b.masked,band_power_uv2:Number.isFinite(pb)?pb:null},
      band_hz:[...r.band],difference_b_minus_a_uv2:Number.isFinite(pb-pa)?pb-pa:null,
      ratio_b_over_a:pa>0&&Number.isFinite(pb)?pb/pa:null,change_db:pa>0&&pb>0?10*Math.log10(pb/pa):null,
      reference:D.metadata.reference,estimator:'arithmetic mean of retained linear PSD; complete contained windows only; overlapping windows are not independent',
      locked_scale_db:[S.floor,S.floor+S.range],frequencies_hz:D.frequencies,
      mean_psd_a_uv2_hz:Array.from(a.values,v=>Number.isFinite(v)?v:null),mean_psd_b_uv2_hz:Array.from(b.values,v=>Number.isFinite(v)?v:null)};
    ctx.textAlign='left';ctx.fillStyle='#8ca9c5';ctx.fillText('Mean PSD · dB re 1 µV²/Hz · '+this.names[S.ch]+' · cyan A / amber B',12,12);
    B.text('abCaption','A: '+D.metadata.condition+' '+a.count+' valid / '+a.masked+' masked windows; B: '+other.metadata.condition+' '+b.count+' valid / '+b.masked+' masked. '+r.band.join('–')+' Hz: '+fmt(pa)+' → '+fmt(pb)+' µV². '+
      (!a.count||!b.count?'No complete valid windows in one interval. ':'')+'Overlapping windows are not independent. Reference: '+D.metadata.reference+'.');
  }
  diagnostics(){
    return {fingerprint:this.B.data.review.fingerprint,linked:this.settings.linked,panels:this.panelChannels().map(ch=>this.names[ch]),target_fps:this.settings.fps,
      marker_seconds:this.settings.markerSeconds,
      band:[...this.settings.band],measurement:this.bandResult,events:this.settings.events.length,map:this.mapState||null,ab:this.abResult?{available:this.abResult.available,a:this.abResult.a,b:this.abResult.b}:null,
      quality_flags:this.B.data.quality.flags?.[this.B.state.ch*this.B.data.times.length+this.B.state.row]??null,metrics:{...this.metrics}};
  }
};
