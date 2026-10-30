/* SPDX-License-Identifier: GPL-3.0-only. Pure review calculations and validation. */
'use strict';
(function(root){
  const finite=Number.isFinite;
  function requireValue(ok,message){if(!ok)throw new Error(message);}
  function timeAligned(M){
    if(M.simultaneous===false||M.recording_context?.timing?.aligned===false)return false;
    return M.simultaneous===true||M.recording_context?.timing?.aligned===true;
  }
  function timingDescription(M){
    if(M.simultaneous===false)return 'Recorded separately (operator declaration)';
    if(M.recording_context?.timing?.aligned===false)return 'Recorded separately (file declaration)';
    if(M.simultaneous===true)return 'Recorded together (operator declaration)';
    return M.recording_context?.timing?.aligned===true?'Shared file clock (detected); hardware simultaneity unconfirmed':'Channel timing unknown';
  }
  function band(data,ch,row,low,high){
    const {frequencies:F,times:T,linear,valid}=data,C=F.length;
    requireValue(finite(low)&&finite(high)&&low>=F[0]&&high<=F.at(-1)&&low<high,'Band must fit retained frequencies');
    requireValue(F.some(f=>f>=low&&f<high),'Band contains no retained FFT bins');
    if(!valid[ch][row])return {power:null,peakHz:null,bins:0};
    let sum=0,bins=0,peak=-Infinity,peakHz=null;
    for(let i=0;i<C;i++)if(F[i]>=low&&F[i]<high){
      const value=linear[(ch*T.length+row)*C+i];
      if(!finite(value))return {power:null,peakHz:null,bins:0};
      sum+=value;bins++;if(value>peak){peak=value;peakHz=F[i];}
    }
    return {power:bins?sum*data.metadata.bin_spacing_hz:null,peakHz:peak>0?peakHz:null,bins};
  }
  function bandFits(data,pair){
    const F=data.frequencies;
    return Array.isArray(pair)&&pair.length===2&&pair.every(finite)&&pair[0]>=F[0]&&pair[1]<=F.at(-1)&&pair[0]<pair[1]&&F.some(f=>f>=pair[0]&&f<pair[1]);
  }
  function mapBandAvailable(data,name,custom){
    if(name==='custom')return bandFits(data,custom);
    if(!data.metadata.bands_hz[name]||data.metadata.unavailable_bands?.includes(name))return false;
    const totals=data.bands?.[name];
    return totals?totals.length===data.metadata.channel_names.length&&totals.every(values=>values.length===data.times.length):bandFits(data,data.metadata.bands_hz[name]);
  }
  function mapPower(data,ch,row,name,custom){
    requireValue(mapBandAvailable(data,name,custom),'This complete map band was not retained; choose another band or run again with a wider spectrum range');
    if(!data.valid[ch][row])return null;
    if(name!=='custom'&&data.bands?.[name]){
      const power=data.bands[name][ch][row];return finite(power)&&power>=0?power:null;
    }
    return band(data,ch,row,...(name==='custom'?custom:data.metadata.bands_hz[name])).power;
  }
  function fittedPowerMaximum(powers){
    const values=powers.filter(value=>finite(value)&&value>=0);if(!values.length)return null;
    const maximum=Math.max(...values);if(!maximum)return 1;
    const step=5*10**(Math.floor(Math.log10(maximum))-1);
    if(!step)return maximum;
    return Math.min(1e9,Math.ceil(maximum*1.05/step)*step);
  }
  function average(data,ch,start,stop){
    const {times:T,frequencies:F,linear,valid,metadata:M}=data,C=F.length;
    requireValue(finite(start)&&finite(stop)&&start<stop,'Interval needs finite start < stop');
    const values=new Float64Array(C);let count=0,eligible=0;
    for(let row=0;row<T.length;row++){
      if(T[row]-M.actual_window_seconds/2<start-1e-8||T[row]+M.actual_window_seconds/2>stop+1e-8)continue;
      eligible++;if(!valid[ch][row])continue;count++;
      for(let f=0;f<C;f++)values[f]+=linear[(ch*T.length+row)*C+f];
    }
    for(let f=0;f<C;f++)values[f]=count?values[f]/count:NaN;
    return {values,count,masked:eligible-count,eligible};
  }
  function nearestRow(times,time){
    requireValue(finite(time)&&times.length>0,'Seek time must be finite');
    let left=0,right=times.length-1;
    while(left<right){const mid=Math.floor((left+right)/2);if(times[mid]<time)left=mid+1;else right=mid;}
    return left&&Math.abs(times[left-1]-time)<=Math.abs(times[left]-time)?left-1:left;
  }
  function trend(data,ch,low,high,start,stop){
    const M=data.metadata;
    requireValue(Number.isInteger(ch)&&ch>=0&&ch<M.channel_names.length,'Invalid trend channel');
    requireValue(finite(start)&&finite(stop)&&start<stop&&start>=M.selected_start_seconds&&stop<=M.selected_stop_seconds,'Trend interval must fit the retained recording');
    band(data,ch,0,low,high);
    const rows=[],values=[],half=M.actual_window_seconds/2;
    for(let row=0;row<data.times.length;row++){
      const time=data.times[row];if(time-half<start-1e-8||time+half>stop+1e-8)continue;
      requireValue(rows.length<200000,'Trend interval exceeds 200,000 windows; shorten the interval');
      const measured=band(data,ch,row,low,high),valid=measured.power!==null;
      if(valid)values.push(measured.power);
      rows.push({row,center_seconds:time,start_seconds:time-half,stop_seconds:time+half,valid,power_uv2:measured.power,
        quality_flags:data.quality?.flags?.[ch*data.times.length+row]??null});
    }
    const sorted=[...values].sort((a,b)=>a-b),n=sorted.length;
    return {rows,valid_windows:n,masked_windows:rows.length-n,mean_uv2:n?values.reduce((a,b)=>a+b,0)/n:null,
      median_uv2:n?(sorted[Math.floor((n-1)/2)]+sorted[Math.floor(n/2)])/2:null,
      maximum_uv2:n?sorted.at(-1):null,minimum_uv2:n?sorted[0]:null,band_hz:[low,high],interval_seconds:[start,stop]};
  }
  function trendCSV(data,ch,series){
    const cell=value=>typeof value==='number'?(finite(value)?String(value):''):value===null?'':
      '"'+String(value).replace(/^(\s*[=+@-])/,'\'$1').replaceAll('"','""')+'"';
    const M=data.metadata;
    const rows=[['source','source_sha256','channel','condition','reference','band_from_hz','band_to_hz_exclusive','window_start_seconds','window_center_seconds','window_stop_seconds','valid','band_power_uv2','quality_flags']];
    for(const row of series.rows)rows.push([M.source,M.source_sha256||'',M.channel_names[ch],M.condition,M.reference,...series.band_hz,
      row.start_seconds,row.center_seconds,row.stop_seconds,row.valid?1:0,row.power_uv2,row.quality_flags]);
    return rows.map(row=>row.map(cell).join(',')).join('\r\n')+'\r\n';
  }
  const contractKeys=['reference','sample_rate_hz','window_samples','taper','detrend','zero_padding','filters_applied','reference_changed','psd_unit'];
  function compatible(a,b){
    requireValue(typeof a.metadata.reference==='string'&&!['unknown','','none','n/a','auto'].includes(a.metadata.reference.trim().toLowerCase()),'Cross-recording comparison requires a documented reference');
    for(const key of contractKeys)requireValue(JSON.stringify(a.metadata[key])===JSON.stringify(b.metadata[key]),'Comparison mismatch: '+key);
    requireValue(a.frequencies.length===b.frequencies.length&&a.frequencies.every((v,i)=>v===b.frequencies[i]),'Comparison frequency bins differ');
    return true;
  }
  function decodeComparison(payload,unpack){
    requireValue(payload&&payload.schema==='eeg-paradox.spectrum-comparison.v1','Unsupported comparison schema');
    const M=payload.metadata,F=payload.frequencies,T=payload.times;
    requireValue(M&&Array.isArray(M.channel_names)&&M.channel_names.length>0&&M.channel_names.length<=64&&
      new Set(M.channel_names).size===M.channel_names.length&&M.channel_names.every(x=>typeof x==='string'&&x.length>0&&x.length<=500),'Invalid comparison channels');
    requireValue(Array.isArray(F)&&Array.isArray(T)&&F.length>=2&&T.length>0&&F.length*T.length*M.channel_names.length<=8000000,'Comparison size exceeds limits');
    requireValue(F.every((v,i)=>finite(v)&&v>=0&&(!i||v>F[i-1]))&&T.every((v,i)=>finite(v)&&v>=0&&(!i||v>T[i-1])),'Invalid comparison axes');
    requireValue(finite(M.actual_window_seconds)&&M.actual_window_seconds>0&&finite(M.bin_spacing_hz)&&M.bin_spacing_hz>0,'Invalid comparison estimator');
    requireValue(finite(M.sample_rate_hz)&&M.sample_rate_hz>0&&Number.isInteger(M.window_samples)&&M.window_samples>=8&&
      Math.abs(M.window_samples/M.sample_rate_hz-M.actual_window_seconds)<1e-8&&
      Math.abs(M.sample_rate_hz/M.window_samples-M.bin_spacing_hz)<1e-8,'Inconsistent comparison estimator');
    requireValue(finite(M.selected_start_seconds)&&M.selected_start_seconds>=0&&finite(M.selected_stop_seconds)&&M.selected_stop_seconds>M.selected_start_seconds&&
      T.every(t=>t-M.actual_window_seconds/2>=M.selected_start_seconds-1e-8&&t+M.actual_window_seconds/2<=M.selected_stop_seconds+1e-8)&&
      F.at(-1)<=M.sample_rate_hz/2,'Comparison time or frequency coverage is inconsistent');
    requireValue(F.every((v,i)=>!i||Math.abs(v-F[i-1]-M.bin_spacing_hz)<1e-8),'Nonuniform comparison bins');
    const p=payload.psd_packed,size=M.channel_names.length*T.length*F.length;
    requireValue(p&&p.dtype==='<f4'&&JSON.stringify(p.shape)===JSON.stringify([M.channel_names.length,T.length,F.length])&&
      typeof p.base64==='string'&&p.base64.length===4*Math.ceil(size*4/3),'Invalid packed comparison shape');
    const linear=unpack(p,Float32Array);
    requireValue(linear.length===size,'Invalid comparison byte count');
    requireValue(Array.isArray(payload.valid)&&payload.valid.length===M.channel_names.length,'Invalid comparison validity');
    for(let ch=0;ch<M.channel_names.length;ch++){
      requireValue(Array.isArray(payload.valid[ch])&&payload.valid[ch].length===T.length,'Invalid comparison validity shape');
      for(let row=0;row<T.length;row++){
        requireValue(typeof payload.valid[ch][row]==='boolean','Invalid comparison validity value');
        for(let f=0;f<F.length;f++){
          const value=linear[(ch*T.length+row)*F.length+f];
          requireValue(payload.valid[ch][row]?(finite(value)&&value>=0):Number.isNaN(value),'Comparison values disagree with valid mask');
        }
      }
    }
    return {...payload,linear};
  }
  function events(items,names){
    requireValue(Array.isArray(items)&&items.length<=10000,'Event list exceeds limits');
    const ids=new Set();
    return items.map(item=>{
      requireValue(item&&finite(item.onset_seconds)&&item.onset_seconds>=0&&finite(item.duration_seconds)&&item.duration_seconds>=0&&
        typeof item.label==='string'&&item.label.trim()&&item.label.length<=500&&typeof item.id==='string'&&item.id.length<=200&&item.id&&!ids.has(item.id)&&
        ['recorded','proposed','accepted','rejected'].includes(item.status)&&Array.isArray(item.channels)&&item.channels.every(x=>names.includes(x)),'Invalid event time, ID, label, status or channel');
      ids.add(item.id);return {id:item.id,onset_seconds:item.onset_seconds,duration_seconds:item.duration_seconds,label:item.label,
        kind:String(item.kind||'annotation').slice(0,80),status:item.status,channels:[...item.channels],origin:String(item.origin||'import').slice(0,200)};
    }).sort((a,b)=>a.onset_seconds-b.onset_seconds);
  }
  function workspace(value,data,comparison=null){
    requireValue(value&&value.schema==='eeg-paradox.spectrum-workspace.v1'&&value.fingerprint===data.review.fingerprint,'Workspace belongs to different data or recording context');
    const s=value.view,r=value.review;
    requireValue(s&&r&&Number.isInteger(s.ch)&&s.ch>=0&&s.ch<data.metadata.channel_names.length&&Number.isInteger(s.row)&&s.row>=0&&s.row<data.times.length,'Invalid workspace cursor');
    for(const [key,lo,hi] of [['low',data.frequencies[0],data.frequencies.at(-1)],['high',data.frequencies[0],data.frequencies.at(-1)],['floor',-90,20],['range',10,90],['perspective',.2,.65],['lift',.4,1.6]])
      requireValue(finite(s[key])&&s[key]>=lo&&s[key]<=hi,'Invalid workspace '+key);
    requireValue(s.low<s.high&&data.frequencies.filter(f=>f>=s.low&&f<=s.high).length>=2&&['sdr','thermal','ice'].includes(s.palette)&&
      ['balanced','high'].includes(s.quality)&&[32,64,96,128].includes(s.depth)&&typeof s.scopeCentered==='boolean'&&
      ['summary','compare','map','ab','events','trend'].includes(s.dock)&&[1,2,4].includes(s.speed)&&typeof s.focus==='boolean'&&
      (!Object.hasOwn(s,'mapExpanded')||typeof s.mapExpanded==='boolean')&&!(s.mapExpanded&&s.focus),'Invalid workspace display settings');
    requireValue(Array.isArray(s.compare)&&s.compare.length<=8&&new Set(s.compare).size===s.compare.length&&
      s.compare.every(ch=>Number.isInteger(ch)&&ch>=0&&ch<data.metadata.channel_names.length),'Invalid comparison selection');
    requireValue(Array.isArray(r.panels)&&r.panels.length>0&&r.panels.length<=4&&new Set(r.panels).size===r.panels.length&&
      r.panels.every(ch=>Number.isInteger(ch)&&ch>=0&&ch<data.metadata.channel_names.length)&&typeof r.linked==='boolean'&&
      [15,30,60].includes(r.fps)&&finite(r.mapMax)&&r.mapMax>0&&r.mapMax<=1e9&&['custom',...Object.keys(data.metadata.bands_hz)].includes(r.mapBand),'Invalid review layout');
    requireValue(Array.isArray(r.band)&&r.band.length===2,'Invalid review band');band(data,0,0,...r.band);
    for(const key of ['a','b']){
      const target=key==='b'&&r.bSource==='imported'?comparison:data;
      requireValue(target&&Array.isArray(r[key])&&r[key].length===2&&r[key].every(finite)&&r[key][0]>=target.metadata.selected_start_seconds&&
        r[key][1]<=target.metadata.selected_stop_seconds&&r[key][0]<r[key][1],'Invalid review interval '+key);
    }
    requireValue(r.bSource==='primary'||r.bSource==='imported','Invalid baseline source');
    const trendInterval=Object.prototype.hasOwnProperty.call(r,'trend')?r.trend:[data.metadata.selected_start_seconds,data.metadata.selected_stop_seconds];
    requireValue(Array.isArray(trendInterval)&&trendInterval.length===2&&trendInterval.every(finite)&&trendInterval[0]<trendInterval[1]&&
      trendInterval[0]>=data.metadata.selected_start_seconds&&trendInterval[1]<=data.metadata.selected_stop_seconds,'Invalid trend interval');
    const markerSeconds=Object.prototype.hasOwnProperty.call(r,'markerSeconds')?r.markerSeconds:0;
    requireValue([0,5,10,15,30,60,120,300,600,900,1800,3600].includes(markerSeconds),'Invalid waterfall clock interval');
    const view={};for(const key of ['ch','row','low','high','floor','range','perspective','lift','palette','quality','depth','scopeCentered','dock','speed','focus'])view[key]=s[key];
    const review={};for(const key of ['linked','fps','mapMax','mapBand','bSource'])review[key]=r[key];
    return {view:{...view,mapExpanded:s.mapExpanded??false,playing:false,compare:[...s.compare]},review:{...review,a:[...r.a],b:[...r.b],trend:[...trendInterval],markerSeconds,band:[...r.band],panels:[...r.panels],events:events(r.events,data.metadata.channel_names)}};
  }
  const fileLimits=Object.freeze({workspace:32_000_000,events:32_000_000,comparison:64_000_000});
  const api={timeAligned,timingDescription,band,bandFits,mapBandAvailable,mapPower,fittedPowerMaximum,average,nearestRow,trend,trendCSV,compatible,decodeComparison,events,workspace,contractKeys,fileLimits};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  root.SpectrumReviewCore=api;
})(typeof window==='undefined'?globalThis:window);
