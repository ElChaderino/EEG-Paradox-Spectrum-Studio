/* SPDX-License-Identifier: GPL-3.0-only. Local layout library and diagnostics controls. */
'use strict';
window.SpectrumConsoleTools=class SpectrumConsoleTools {
  constructor(bridge){
    const B=this.B=bridge,C=this.C=window.SpectrumConsoleCore,{$}=B;
    this.storageKey='eeg-paradox.spectrum-layouts.v1';this.storageAvailable=true;this.removed=null;
    this.catalog={schema:this.storageKey,layouts:[]};
    let restored=false;
    try{
      const saved=window.localStorage?.getItem(this.storageKey);
      if(saved){if(saved.length>C.fileLimit)throw new Error('Saved layout library exceeds 256 KB');this.catalog=C.library(JSON.parse(saved));restored=true;}
      if(!window.localStorage)this.storageAvailable=false;
    }catch{this.storageAvailable=false;B.text('layoutStatus','Saved browser layouts could not be read. Import a saved layout library, or export new layouts to keep them.');}
    if(!restored)try{
      const base=C.capture('Single-channel review',B.review().workspace(),B.data);base.review.linked=false;base.view.dock='summary';base.view.focus=false;
      const overview=JSON.parse(JSON.stringify(base));overview.name='Channel overview';overview.review.linked=true;
      const trend=JSON.parse(JSON.stringify(base));trend.name='Band-power review';trend.view.dock='trend';
      const events=JSON.parse(JSON.stringify(base));events.name='Event review';events.view.dock='events';
      this.catalog=C.library({schema:this.storageKey,layouts:[base,overview,trend,events]});
    }catch{B.text('layoutStatus','Default layouts could not be created for these channel labels. The recording remains available for review.');}
    this.render();
    $('layoutsToggle').addEventListener('click',()=>{$('layoutsDialog').showModal();});
    $('closeLayouts').addEventListener('click',()=>$('layoutsDialog').close());
    $('layoutChoice').addEventListener('change',()=>{const selected=this.selected();if(selected)$('layoutName').value=selected.name;});
    const safe=action=>()=>{try{action();}catch(error){B.record('layout_refused');B.text('layoutStatus',error.message);}};
    $('newLayout').addEventListener('click',safe(()=>{
      const item=C.capture($('layoutName').value,B.review().workspace(),B.data);
      if(this.catalog.layouts.some(x=>x.name.toLowerCase()===item.name.toLowerCase()))throw new Error('That name already exists. Use Update selected, or choose a new name.');
      this.catalog=C.library({schema:this.storageKey,layouts:[...this.catalog.layouts,item]});this.saved(item.name);
    }));
    $('updateLayout').addEventListener('click',safe(()=>{
      const selected=this.selected();if(!selected)throw new Error('Select a layout to update.');
      const item=C.capture($('layoutName').value,B.review().workspace(),B.data);
      this.catalog=C.library({schema:this.storageKey,layouts:this.catalog.layouts.map(x=>x===selected?item:x)});this.saved(item.name);
    }));
    $('applyLayout').addEventListener('click',safe(()=>{
      const selected=this.selected();if(!selected)throw new Error('Select a layout first.');this.apply(selected);
      B.text('layoutStatus','Applied '+selected.name+'. Recording cursor, intervals and events retained.');
    }));
    $('removeLayout').addEventListener('click',safe(()=>{
      const selected=this.selected();if(!selected)throw new Error('Select a layout first.');
      this.removed={item:selected,index:this.catalog.layouts.indexOf(selected)};
      this.catalog=C.library({schema:this.storageKey,layouts:this.catalog.layouts.filter(x=>x!==selected)});this.persist();this.render();
      $('undoLayout').disabled=false;B.text('layoutStatus','Removed '+selected.name+'. Undo removal is available until the next removal.');
    }));
    $('undoLayout').addEventListener('click',safe(()=>{
      if(!this.removed)return;const layouts=[...this.catalog.layouts];layouts.splice(this.removed.index,0,this.removed.item);
      this.catalog=C.library({schema:this.storageKey,layouts});const name=this.removed.item.name;this.removed=null;
      this.persist();this.render(name);$('undoLayout').disabled=true;B.text('layoutStatus','Layout restored.');
    }));
    $('saveLayouts').addEventListener('click',()=>B.download(new Blob([JSON.stringify(C.library(this.catalog))],{type:'application/json'}),'spectrum_layouts.json'));
    $('loadLayouts').addEventListener('click',()=>$('layoutFile').click());
    $('layoutFile').addEventListener('change',async()=>{
      const field=$('layoutFile'),file=field.files?.[0];if(!file)return;
      try{
        if(file.size>C.fileLimit)throw new Error('Layout library exceeds 256 KB');
        this.importLayouts(JSON.parse(await file.text()));B.text('layoutStatus','Layout library imported. Name conflicts receive an “(imported)” suffix. Select a layout and click Apply.');
      }catch(error){B.record('layout_refused');B.text('layoutStatus','Import refused: '+error.message);}finally{field.value='';}
    });
    $('diagnosticsToggle').addEventListener('click',()=>{this.refreshDiagnostics();$('diagnosticsDialog').showModal();});
    $('closeDiagnostics').addEventListener('click',()=>$('diagnosticsDialog').close());
    $('refreshDiagnostics').addEventListener('click',()=>this.refreshDiagnostics());
    $('diagnosticsBenchmark').addEventListener('click',()=>{$('benchmark').click();B.text('diagnosticsStatus','Checking 120 display frames. Refresh this summary after the check completes.');});
    $('saveDiagnostics').addEventListener('click',()=>{
      B.download(new Blob([JSON.stringify(this.report(),null,2)],{type:'application/json'}),'spectrum_studio_diagnostics.json');
      B.text('diagnosticsStatus','Diagnostics saved locally. Nothing was sent.');
    });
    window.spectrumStudioTools={layouts:()=>C.library(this.catalog),importLayouts:value=>this.importLayouts(value),
      applyLayout:name=>{const item=this.catalog.layouts.find(x=>x.name===name);if(!item)throw new Error('Unknown layout');this.apply(item);},diagnostics:()=>this.report()};
  }
  selected(){return this.catalog.layouts.find(x=>x.name===this.B.$('layoutChoice').value);}
  render(name=null){
    const {$}=this.B,choice=$('layoutChoice'),previous=name||choice.value;choice.replaceChildren();
    for(const item of this.catalog.layouts){const option=document.createElement('option');option.value=item.name;option.textContent=item.name;choice.append(option);}
    choice.value=this.catalog.layouts.some(x=>x.name===previous)?previous:this.catalog.layouts[0]?.name||'';
    $('layoutName').value=this.selected()?.name||'';
    for(const id of ['applyLayout','updateLayout','removeLayout'])$(id).disabled=!this.catalog.layouts.length;
    $('undoLayout').disabled=!this.removed;
    this.B.text('layoutPersistence',this.storageAvailable?'Layouts are kept in this browser when local-file storage is available. Export the library to use it with another recording.':'Browser storage is unavailable. Export the library to keep layouts or move them to another recording.');
  }
  persist(){
    try{
      if(!window.localStorage)throw new Error('Storage unavailable');window.localStorage.setItem(this.storageKey,JSON.stringify(this.C.library(this.catalog)));this.storageAvailable=true;
    }catch{this.storageAvailable=false;}
  }
  saved(name){this.persist();this.render(name);this.B.record('layout_saved');this.B.text('layoutStatus','Saved '+name+'. '+(this.storageAvailable?'Export the library for portable use.':'Export the library to keep it after closing.'));}
  apply(item){
    const B=this.B;B.review().importWorkspace(this.C.resolve(item,B.review().workspace(),B.data));B.record('layout_applied');
  }
  importLayouts(value){this.catalog=this.C.merge(this.catalog,value);this.persist();this.render();this.B.record('layout_imported');}
  report(){return this.C.diagnostics(this.B.diagnosticSnapshot());}
  refreshDiagnostics(){
    const report=this.report();this.B.text('diagnosticsSummary',JSON.stringify(report,null,2));
    this.B.text('diagnosticsOverview',(report.renderer==='webgl2'?'GPU rendering':'CPU fallback')+' · '+report.graphics_quality+' quality · '+report.display.panel_count+' panel(s) · '+report.recent_events.length+' recent status entries.');
    this.B.text('diagnosticsStatus',report.performance_check?'The last completed display check is included.':'No completed display check yet. You can run one below.');
  }
};
