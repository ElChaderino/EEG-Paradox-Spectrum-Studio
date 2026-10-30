"""Native recording picker and responsive local run manager. No web server."""
from __future__ import annotations

import os
import json
from pathlib import Path
import queue
import threading
import time
from uuid import uuid4

from .intake import FORMATS
from .runner import RunOptions,run_recording,unique_output,read_completed_run
from .spectral import SpectrumConfig
from .runtime import data_directory,output_directory,frozen,set_windows_app_id,set_window_icon


def recent_run_paths(path):
    path=Path(path)
    if not path.is_file():return []
    if path.stat().st_size>1_000_000:raise ValueError('Recent-run index is too large')
    data=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data,dict):raise ValueError('Invalid recent-run index')
    paths=data.get('runs',[data.get('run')])
    if not isinstance(paths,list) or any(not isinstance(x,str) or not x for x in paths):raise ValueError('Invalid recent-run index')
    return list(dict.fromkeys(paths))[:20]


def options_from_fields(fields):
    """Shared, headless-testable form validation; no file operations."""
    import numpy as np
    start=float(fields['start']);duration=None if fields.get('whole') else float(fields['duration'])
    window=float(fields['window']);hop=float(fields['hop']);low=float(fields['fmin']);high=float(fields['fmax'])
    if not np.isfinite([start,window,hop,low,high]).all() or start<0 or window<=0 or not 0<hop<=window or not 0<=low<high:
        raise ValueError('Use finite settings: start >= 0, 0 < hop <= window, and 0 <= low < high')
    if duration is not None and (not np.isfinite(duration) or duration<window):raise ValueError('Selected duration must include at least one complete FFT window')
    rate=float(fields['sample_rate']) if str(fields.get('sample_rate','')).strip() else None
    if rate is not None and (not np.isfinite(rate) or rate<=0):raise ValueError('Tabular sample rate must be positive')
    unit=fields.get('unit') or None
    if unit not in (None,'V','mV','uV','nV'):raise ValueError('Choose a calibrated unit for tabular signals')
    channels=tuple(x.strip() for x in fields.get('channels','').split(',') if x.strip()) or None
    return RunOptions(start,duration,SpectrumConfig(window,hop,low,high),channels,
        fields.get('reference','').strip() or 'unknown',fields.get('condition','UNKNOWN'),
        {'auto':None,'unknown':None,'yes':True,'no':False}[fields.get('simultaneous','auto')],
        'standard_1020' if fields.get('montage') else None,rate,unit,
        Path(fields['converter_root']) if fields.get('converter_root','').strip() else None)


class StudioDesktop:
    def __init__(self,root):
        import tkinter as tk
        from tkinter import ttk,filedialog,messagebox
        self.root=root;self.tk=tk;self.ttk=ttk;self.dialog=filedialog;self.messagebox=messagebox
        self.messages=queue.Queue();self.cancel_event=threading.Event();self.worker=None;self.inspect_worker=None;self.results=[];self.selected_index=-1
        self.recent_file=data_directory()/'last_completed_run.json'
        self.run_errors=0;self.run_active=False
        from .diagnostics import read_history
        self.diagnostic_started=time.monotonic();self.diagnostic_file=data_directory()/'diagnostic_history.json'
        try:self.diagnostic_events=read_history(self.diagnostic_file)
        except (OSError,ValueError,TypeError):self.diagnostic_events=[]
        self.diagnostic_session=(max([0,*[e.get('session',0) for e in self.diagnostic_events]])+1)%1_000_000_000
        self.record_diagnostic('loader_ready')
        root.report_callback_exception=self.callback_error
        root.title('EEG Paradox | Spectrum Studio 0.6.8 - Recording Loader');root.geometry('1100x850');root.minsize(860,650)
        set_window_icon(root)
        root.configure(bg='#091320');style=ttk.Style(root);style.theme_use('clam')
        style.configure('.',background='#0d1b2b',foreground='#c5deed',fieldbackground='#11243a',font=('Segoe UI',10))
        style.configure('TButton',padding=7);style.configure('TEntry',fieldbackground='#102238',foreground='#d2eaf7')
        style.configure('TCombobox',fieldbackground='#102238',foreground='#d2eaf7');style.map('TCombobox',fieldbackground=[('readonly','#102238')])
        style.configure('Title.TLabel',font=('Segoe UI',21,'bold'),foreground='#d8f0fb');style.configure('Hint.TLabel',foreground='#7e9fb9',font=('Segoe UI',9))
        style.configure('TNotebook',background='#0d1b2b',borderwidth=0)
        style.configure('TNotebook.Tab',background='#172a3e',foreground='#a9c5db',padding=(12,7))
        style.map('TNotebook.Tab',background=[('selected','#20445d'),('active','#1a354d')],foreground=[('selected','#e1f4ff'),('active','#d2eaf7')])
        style.configure('Treeview.Heading',background='#193249',foreground='#d2eaf7',padding=5)
        style.map('Treeview.Heading',background=[('active','#23465f')])
        style.map('Treeview',background=[('selected','#24536f')],foreground=[('selected','#e1f4ff')])
        style.configure('Treeview',background='#07101a',fieldbackground='#07101a',foreground='#b9d5e7',rowheight=25)
        outer=ttk.Frame(root,padding=16);outer.pack(fill='both',expand=True)
        ttk.Label(outer,text='SPECTRUM STUDIO',style='Title.TLabel').pack(anchor='w')
        ttk.Label(outer,text='Inspect a recording, choose its analysis window, then generate a local viewer and reports.',style='Hint.TLabel').pack(anchor='w',pady=(0,10))
        self.notebook=ttk.Notebook(outer);self.notebook.pack(fill='both',expand=True)
        setup=ttk.Frame(self.notebook);self.output_tab=ttk.Frame(self.notebook,padding=12)
        self.notebook.add(setup,text='Recording & analysis');self.notebook.add(self.output_tab,text='Runs & outputs')
        canvas=tk.Canvas(setup,bg='#0d1b2b',highlightthickness=0);scroll=ttk.Scrollbar(setup,orient='vertical',command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set);scroll.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True)
        panel=ttk.Frame(canvas,padding=12);inner=canvas.create_window((0,0),window=panel,anchor='nw')
        panel.bind('<Configure>',lambda event:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda event:canvas.itemconfigure(inner,width=event.width))
        filebox=ttk.LabelFrame(panel,text='Recording files',padding=10);filebox.pack(fill='x')
        self.paths=tk.StringVar();self.path_entry=ttk.Entry(filebox,textvariable=self.paths);self.path_entry.pack(side='left',fill='x',expand=True)
        ttk.Button(filebox,text='Browse recordings...',command=self.browse).pack(side='left',padx=(8,0))
        self.inspect_button=ttk.Button(filebox,text='Inspect first file...',command=self.inspect);self.inspect_button.pack(side='left',padx=(8,0))
        ttk.Label(panel,text='Multiple files run separately. Select .vhdr/.set headers with their companion files together. Inspect previews channels and storage cost.',style='Hint.TLabel',wraplength=950).pack(anchor='w',pady=6)
        self.fields={};form=ttk.Frame(panel);form.pack(fill='x',pady=6)
        for col in range(4):form.columnconfigure(col,weight=1)
        def field(key,title,value,row,col,choices=None):
            box=ttk.Frame(form,padding=(0,4,12,4));box.grid(row=row,column=col,sticky='ew')
            ttk.Label(box,text=title).pack(anchor='w');var=tk.StringVar(value=value);self.fields[key]=var
            widget=ttk.Combobox(box,textvariable=var,values=choices,state='readonly') if choices else ttk.Entry(box,textvariable=var)
            widget.pack(fill='x',pady=(3,0));return widget
        field('start','Start offset (s)','0',0,0);field('duration','Selected duration (s)','120',0,1)
        field('window','FFT window (s)','4',0,2);field('hop','Hop (s)','0.5',0,3)
        field('fmin','Frequency from (Hz)','0.5',1,0);field('fmax','Frequency to (Hz)','45',1,1)
        field('condition','Declared condition','UNKNOWN',1,2,['UNKNOWN','EO','EC','ERP','SLEEP','TASK'])
        field('simultaneous','Recorded together? (override)','auto',1,3,['auto','yes','no'])
        field('reference','Documented input reference','unknown',2,0);field('channels','Channels (blank = eligible EEG)','',2,1)
        field('sample_rate','CSV/JSON sample rate (Hz)','',2,2);field('unit','CSV/JSON calibrated unit','',2,3,['','V','mV','uV','nV'])
        self.whole=tk.BooleanVar(value=False);self.montage=tk.BooleanVar(value=False)
        checks=ttk.Frame(panel);checks.pack(fill='x',pady=5)
        ttk.Checkbutton(checks,text='Whole recording (spectral-size limit applies)',variable=self.whole).pack(side='left')
        ttk.Checkbutton(checks,text='Explicit standard 10-20 template scalp positions',variable=self.montage).pack(side='left',padx=20)
        locations=ttk.LabelFrame(panel,text='Run destination and optional comparison',padding=10);locations.pack(fill='x',pady=8)
        self.destination=tk.StringVar(value=str(output_directory()))
        self.compare=tk.StringVar();self.compare_condition=tk.StringVar(value='UNKNOWN');self.converter=tk.StringVar()
        for i,(title,var,command) in enumerate([('Output parent folder',self.destination,self.browse_output),('Optional B recording',self.compare,self.browse_compare),('Optional WinEEG converter root',self.converter,self.browse_converter)]):
            ttk.Label(locations,text=title).grid(row=i,column=0,sticky='w',pady=4)
            ttk.Entry(locations,textvariable=var).grid(row=i,column=1,sticky='ew',padx=10,pady=4)
            ttk.Button(locations,text='Browse...',command=command).grid(row=i,column=2,pady=4)
        locations.columnconfigure(1,weight=1)
        ttk.Label(locations,text='B condition').grid(row=3,column=0,sticky='w');ttk.Combobox(locations,textvariable=self.compare_condition,values=['UNKNOWN','EO','EC','ERP','SLEEP','TASK'],state='readonly').grid(row=3,column=1,sticky='w',padx=10)
        ttk.Label(panel,text='File timing and saved scalp positions are checked automatically. Leave timing on auto to use file evidence; use yes/no only with acquisition records. Reports include PNG/PDF figures and a viewer. The input reference stays as declared.',style='Hint.TLabel',wraplength=950).pack(anchor='w',pady=8)
        ttk.Label(self.output_tab,text='Select a completed run to open its outputs. Every batch result remains selectable.',style='Hint.TLabel').pack(anchor='w',pady=(0,8))
        treebox=ttk.Frame(self.output_tab);treebox.pack(fill='both',expand=True)
        self.result_tree=ttk.Treeview(treebox,columns=('run','folder'),show='headings',height=7,selectmode='browse')
        self.result_tree.heading('run',text='Completed run');self.result_tree.heading('folder',text='Output folder')
        self.result_tree.column('run',width=250);self.result_tree.column('folder',width=650)
        ts=ttk.Scrollbar(treebox,orient='vertical',command=self.result_tree.yview);self.result_tree.configure(yscrollcommand=ts.set)
        self.result_tree.pack(side='left',fill='both',expand=True);ts.pack(side='right',fill='y');self.result_tree.bind('<<TreeviewSelect>>',self.choose_result)
        actions=ttk.Frame(self.output_tab);actions.pack(fill='x',pady=8);self.open_buttons=[]
        for title,kind in [('Open report','report'),('Open viewer','viewer'),('Output folder','root')]:
            button=ttk.Button(actions,text=title,command=lambda key=kind:self.open_result(key),state='disabled');button.pack(side='left',padx=(0,7));self.open_buttons.append(button)
        self.restore_buttons=[]
        for title,command in [('Load completed run...',self.browse_completed),('Recent runs...',self.browse_recent)]:
            button=ttk.Button(actions,text=title,command=command);button.pack(side='left',padx=7);self.restore_buttons.append(button)
        self.log=tk.Text(self.output_tab,height=5,bg='#07101a',fg='#96b8cf',insertbackground='#b5dded',relief='flat',font=('Consolas',9));self.log.pack(fill='x',pady=(4,0))
        footer=ttk.Frame(outer);footer.pack(fill='x',pady=(10,0))
        self.run_button=ttk.Button(footer,text='Run recording + reports',command=self.run);self.run_button.pack(side='left')
        self.cancel_button=ttk.Button(footer,text='Cancel after current stage',command=self.cancel_event.set,state='disabled');self.cancel_button.pack(side='left',padx=8)
        ttk.Button(footer,text='Save diagnostics...',command=self.save_diagnostics).pack(side='right')
        self.status=tk.StringVar(value='Choose a recording, or restore a completed run from Runs & outputs.')
        ttk.Label(outer,textvariable=self.status,wraplength=1000).pack(anchor='w',pady=6)
        self.progress=ttk.Progressbar(outer,mode='indeterminate');self.progress.pack(fill='x')
        root.protocol('WM_DELETE_WINDOW',self.close);root.after(100,self.poll);root.after(150,self.restore_recent)
    def filetypes(self):return [('EEG recordings',' '.join('*'+x for x in FORMATS)),('All files','*.*')]
    def browse(self):
        paths=self.dialog.askopenfilenames(parent=self.root,title='Choose EEG recordings',filetypes=self.filetypes())
        if paths:self.paths.set(self.root.tk.call('format','%s',tuple(paths)))
    def browse_output(self):
        value=self.dialog.askdirectory(parent=self.root,title='Choose output parent folder')
        if value:self.destination.set(value)
    def browse_compare(self):
        value=self.dialog.askopenfilename(parent=self.root,title='Choose B recording',filetypes=self.filetypes())
        if value:self.compare.set(value)
    def browse_converter(self):
        value=self.dialog.askdirectory(parent=self.root,title='Choose maintained WinEEG converter root')
        if value:self.converter.set(value)
    def current_options(self):
        fields={key:var.get() for key,var in self.fields.items()}
        fields.update(whole=self.whole.get(),montage=self.montage.get(),converter_root=self.converter.get())
        return options_from_fields(fields)
    def render_results(self):
        if not hasattr(self,'result_tree'):return
        self.result_tree.delete(*self.result_tree.get_children())
        for i,artifact in enumerate(self.results):self.result_tree.insert('', 'end',iid=str(i),values=(artifact.root.name,str(artifact.root)))
        if self.results:
            self.selected_index=max(0,min(getattr(self,'selected_index',len(self.results)-1),len(self.results)-1))
            self.result_tree.selection_set(str(self.selected_index));self.result_tree.see(str(self.selected_index))
    def choose_result(self,event=None):
        selected=self.result_tree.selection()
        if selected:self.selected_index=int(selected[0])
    def run_in_progress(self):
        worker=getattr(self,'worker',None)
        return bool(getattr(self,'run_active',False) or (worker and worker.is_alive()))
    def inspect(self):
        if self.run_in_progress() or (self.inspect_worker and self.inspect_worker.is_alive()):return
        try:
            paths=self.inputs()
            if not paths or not paths[0].is_file():raise ValueError('Choose an existing recording first')
            options=self.current_options()
        except (ValueError,OSError,self.tk.TclError) as exc:self.messagebox.showerror('Recording inspection',str(exc),parent=self.root);return
        self.inspect_button.configure(state='disabled');self.run_button.configure(state='disabled');self.progress.start(12)
        self.status.set('Inspecting '+paths[0].name+' without spectral processing...')
        self.record_diagnostic('inspection_started')
        def work():
            try:
                from .inspection import inspect_recording
                info=inspect_recording(paths[0],sample_rate=options.sample_rate,unit=options.unit,converter_root=options.converter_root)
                self.messages.put(('inspection',info))
            except Exception as exc:
                self.messages.put(('diagnostic',self.make_diagnostic('inspection_failed',error=exc)))
                self.messages.put(('inspection_error',str(exc)))
        self.inspect_worker=threading.Thread(target=work,daemon=True,name='SpectrumStudioInspection');self.inspect_worker.start()
    def inspection_popup(self,info):
        from .inspection import estimate_selection
        tk,ttk=self.tk,self.ttk;popup=tk.Toplevel(self.root);popup.title('Inspect recording | '+info['source']);popup.geometry('840x640');popup.minsize(640,450)
        panel=ttk.Frame(popup,padding=14);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text=info['source'],font=('Segoe UI',15,'bold')).pack(anchor='w')
        ttk.Label(panel,text=f"{info['format']} | {info['sample_rate_hz']:g} Hz | {info['duration_seconds']:.3f} s | {len(info['channels'])} input channels | {info['annotations']} annotations").pack(anchor='w',pady=6)
        ttk.Label(panel,text=info['notice'],wraplength=790,style='Hint.TLabel').pack(anchor='w',pady=5)
        from .recording_context import timing_description
        context=info.get('recording_context',{})
        detected=timing_description({'recording_context':context})
        sites=len(context.get('positions_xy',{}))
        layout=f"{sites} saved scalp positions" if sites else 'no usable saved scalp positions'
        ttk.Label(panel,text='Detected: '+detected+'. '+layout+'. '+(context.get('position_source') or 'A verified layout is still needed for a scalp map.'),wraplength=790,style='Hint.TLabel').pack(anchor='w',pady=5)
        box=ttk.Frame(panel);box.pack(fill='both',expand=True)
        tree=ttk.Treeview(box,columns=('name','type','unit','status'),show='headings',selectmode='extended')
        for key,title,width in [('name','Channel',150),('type','Type',90),('unit','Unit',80),('status','Selection',360)]:tree.heading(key,text=title);tree.column(key,width=width)
        bar=ttk.Scrollbar(box,orient='vertical',command=tree.yview);tree.configure(yscrollcommand=bar.set);bar.pack(side='right',fill='y');tree.pack(side='left',fill='both',expand=True)
        eligible=[]
        for i,item in enumerate(info['channels']):
            tree.insert('','end',iid=str(i),values=(item['name'],item['type'],item['unit'],'eligible EEG' if item['eligible'] else item['excluded_reason']))
            if item['eligible']:eligible.append(str(i))
        tree.selection_set(eligible);estimate=tk.StringVar();ttk.Label(panel,textvariable=estimate,wraplength=790).pack(anchor='w',pady=10)
        def selected():return [info['channels'][int(i)]['name'] for i in tree.selection() if info['channels'][int(i)]['eligible']]
        def update(event=None):
            try:
                result=estimate_selection(info,self.current_options(),selected())
                estimate.set(f"{len(result['channels'])} EEG channels | {result['window_count']:,} windows | {result['frequency_bins']} bins | {result['psd_megabytes']:.1f} MB PSD | "+('fits spectral limit' if result['fits_limit'] else 'EXCEEDS LIMIT: shorten selection or increase hop'))
            except ValueError as exc:estimate.set(str(exc))
        def apply():
            names=selected()
            if not names:self.messagebox.showerror('Channel selection','Select at least one eligible EEG channel',parent=popup);return
            if any(',' in name for name in names):self.messagebox.showerror('Channel selection','Comma-containing labels cannot be entered in this form; use the Python picks list.',parent=popup);return
            self.fields['channels'].set(', '.join(names));popup.destroy()
        tree.bind('<<TreeviewSelect>>',update);update()
        buttons=ttk.Frame(panel);buttons.pack(fill='x');ttk.Button(buttons,text='Use selected EEG channels',command=apply).pack(side='left')
        ttk.Button(buttons,text='Select eligible EEG',command=lambda:(tree.selection_set(eligible),update())).pack(side='left',padx=8)
        ttk.Button(buttons,text='Close',command=popup.destroy).pack(side='right')
    def browse_recent(self):
        if self.run_in_progress():self.status.set('Wait for the current recording run to finish before restoring another run.');return
        try:paths=recent_run_paths(self.recent_file)
        except (OSError,ValueError,TypeError) as exc:self.messagebox.showerror('Recent runs',str(exc),parent=self.root);return
        if not paths:self.status.set('No recent completed runs yet.');return
        popup=self.tk.Toplevel(self.root);popup.title('Recent Spectrum Studio runs');popup.geometry('900x400')
        panel=self.ttk.Frame(popup,padding=12);panel.pack(fill='both',expand=True)
        self.ttk.Label(panel,text='Choose a saved run. Its file hashes are verified before it is restored.').pack(anchor='w',pady=6)
        listing=self.tk.Listbox(panel,bg='#07101a',fg='#bdd8e8',selectbackground='#24536f',font=('Consolas',10));listing.pack(fill='both',expand=True)
        for path in paths:listing.insert('end',path)
        listing.selection_set(0)
        def load():
            if not listing.curselection():return
            try:self.select_completed(paths[listing.curselection()[0]]);self.notebook.select(self.output_tab);popup.destroy()
            except (OSError,ValueError,KeyError,TypeError) as exc:self.messagebox.showerror('Cannot restore run',str(exc),parent=popup)
        self.ttk.Button(panel,text='Verify and load selected run',command=load).pack(anchor='w',pady=8)
    def remember_completed(self,artifact):
        try:
            try:previous=recent_run_paths(self.recent_file)
            except (OSError,ValueError,TypeError):previous=[]
            paths=list(dict.fromkeys([str(artifact.root),*previous]))[:20]
            self.recent_file.parent.mkdir(parents=True,exist_ok=True)
            temporary=self.recent_file.with_name('.recent_'+uuid4().hex+'.tmp')
            temporary.write_text(json.dumps({'run':str(artifact.root),'runs':paths}),encoding='utf-8')
            temporary.replace(self.recent_file)
        except OSError as exc:self.log.insert('end','Could not save recent-run pointer: '+str(exc)+'\n')
    def select_completed(self,folder):
        if self.run_in_progress():raise ValueError('Wait for the current recording run to finish before restoring another run.')
        artifact=read_completed_run(folder)
        self.results=[item for item in self.results if item.root!=artifact.root]+[artifact];self.selected_index=len(self.results)-1
        self.render_results()
        for button in self.open_buttons:button.configure(state='normal')
        self.status.set('Restored completed run. Reports and viewer are ready to open.')
        self.log.insert('end','Restored completed run: '+str(artifact.root)+'\n');self.log.see('end')
        self.remember_completed(artifact)
        self.record_diagnostic('run_restored')
    def restore_recent(self):
        if self.run_in_progress():return
        if not self.recent_file.is_file():return
        try:
            paths=recent_run_paths(self.recent_file)
            if paths:self.select_completed(paths[0])
        except (OSError,ValueError,KeyError,TypeError) as exc:
            self.log.insert('end','Recent run could not be restored: '+str(exc)+'\n')
    def browse_completed(self):
        if self.run_in_progress():return
        value=self.dialog.askdirectory(parent=self.root,title='Choose a completed Spectrum Studio run folder')
        if value:
            try:self.select_completed(value)
            except (OSError,ValueError,KeyError,TypeError) as exc:
                self.messagebox.showerror('Cannot load completed run',str(exc),parent=self.root)
    def inputs(self):
        value=self.paths.get().strip()
        if Path(value).is_file():return [Path(value)]
        return [Path(x) for x in self.root.tk.splitlist(value)]
    def run(self):
        if self.run_in_progress() or (self.inspect_worker and self.inspect_worker.is_alive()):return
        try:
            inputs=self.inputs()
            if not inputs or any(not path.is_file() for path in inputs):raise ValueError('Choose existing recording files')
            if len(inputs)>1 and self.compare.get().strip():raise ValueError('Use one primary recording when loading a B comparison')
            fields={key:var.get() for key,var in self.fields.items()};fields.update(whole=self.whole.get(),montage=self.montage.get(),converter_root=self.converter.get())
            options=options_from_fields(fields);parent=Path(self.destination.get()).resolve()
            comparison=self.compare.get().strip() or None;condition=self.compare_condition.get()
        except (ValueError,OSError,self.tk.TclError) as exc:self.messagebox.showerror('Spectrum Studio',str(exc),parent=self.root);return
        self.run_active=True
        self.results=[];self.selected_index=-1;self.run_errors=0;self.render_results();self.cancel_event.clear();self.run_button.configure(state='disabled');self.cancel_button.configure(state='normal');self.inspect_button.configure(state='disabled')
        for button in self.restore_buttons:button.configure(state='disabled')
        self.notebook.select(self.output_tab)
        for button in self.open_buttons:button.configure(state='disabled')
        self.progress.start(12)
        self.record_diagnostic('run_started')
        def work():
            for i,path in enumerate(inputs,1):
                if self.cancel_event.is_set():break
                try:
                    self.messages.put(('progress',f'Recording {i}/{len(inputs)}: {path.name}'))
                    artifact=run_recording(path,unique_output(parent,path),options=options,comparison_source=comparison,
                        comparison_condition=condition,progress=lambda text:self.messages.put(('progress',text)),cancelled=self.cancel_event.is_set)
                    self.messages.put(('result',artifact))
                except Exception as exc:
                    self.messages.put(('diagnostic',self.make_diagnostic('run_cancelled' if isinstance(exc,InterruptedError) else 'run_failed',error=exc)))
                    self.messages.put(('error',path.name+': '+str(exc)))
            self.messages.put(('done',None))
        self.worker=threading.Thread(target=work,daemon=True,name='SpectrumStudioRun');self.worker.start()
    def poll(self):
        try:
            while True:
                kind,value=self.messages.get_nowait()
                if kind=='diagnostic':self.append_diagnostic(value)
                elif kind=='result':
                    self.results.append(value);self.selected_index=len(self.results)-1;self.render_results();self.remember_completed(value);self.record_diagnostic('run_complete')
                elif kind in ('inspection','inspection_error'):
                    self.progress.stop();self.inspect_button.configure(state='normal');self.run_button.configure(state='normal')
                    if kind=='inspection':self.record_diagnostic('inspection_complete');self.status.set('Inspection complete. Choose channels in the preview.');self.inspection_popup(value)
                    else:self.status.set('Inspection failed: '+value);self.messagebox.showerror('Recording inspection',value,parent=self.root)
                elif kind=='done':
                    self.run_active=False
                    self.progress.stop();self.run_button.configure(state='normal');self.cancel_button.configure(state='disabled');self.inspect_button.configure(state='normal')
                    for button in self.restore_buttons:button.configure(state='normal')
                    for button in self.open_buttons:button.configure(state='normal' if self.results else 'disabled')
                    self.status.set(f'{len(self.results)} completed run(s), {self.run_errors} failed. '+('Cancelled; partial folders remain incomplete.' if self.cancel_event.is_set() else 'Select a completed row to open its outputs.'))
                else:
                    if kind=='error':self.run_errors+=1
                    if kind=='progress':
                        from .diagnostics import progress_stage
                        stage=progress_stage(value)
                        if stage:self.record_diagnostic('run_stage',stage=stage)
                    self.status.set(value);self.log.insert('end',value+'\n');self.log.see('end')
        except queue.Empty:pass
        self.root.after(100,self.poll)
    def open_result(self,kind):
        if not self.results:return
        index=getattr(self,'selected_index',-1)
        path=getattr(self.results[index if -len(self.results)<=index<len(self.results) else -1],kind)
        try:
            if not Path(path).exists():raise FileNotFoundError(str(path))
            if os.name=='nt':os.startfile(str(path))
            else:
                import webbrowser
                if not webbrowser.open(Path(path).as_uri()):raise OSError('No browser accepted the local file')
            self.record_diagnostic('output_opened')
        except OSError as exc:
            self.record_diagnostic('output_open_failed',error=exc)
            message='Could not open '+str(path)+'\n\n'+str(exc)
            if getattr(exc,'winerror',None)==5:
                launcher='Spectrum_Studio.exe' if frozen() else 'Run_Spectrum_Studio.cmd'
                message+='\n\nThis app instance cannot hand files to Windows from its restricted session. Close this loader and double-click '+launcher+' in Explorer. The completed run will be restored; no EEG rerun is needed.'
            self.status.set('Windows could not open the output. See the error dialog and log.')
            self.log.insert('end',message+'\n');self.log.see('end')
            self.messagebox.showerror('Cannot open Spectrum Studio output',message,parent=self.root)
    def make_diagnostic(self,code,**kwargs):
        from .diagnostics import diagnostic_event
        now=time.monotonic();event=diagnostic_event(code,max(0,round((now-getattr(self,'diagnostic_started',now))*1000)),**kwargs)
        event['session']=getattr(self,'diagnostic_session',0);return event
    def record_diagnostic(self,code,**kwargs):
        self.append_diagnostic(self.make_diagnostic(code,**kwargs))
    def append_diagnostic(self,event):
        if not hasattr(self,'diagnostic_events'):self.diagnostic_events=[]
        self.diagnostic_events.append(event);self.diagnostic_events=self.diagnostic_events[-64:]
        if hasattr(self,'diagnostic_file'):
            from .diagnostics import write_history
            try:write_history(self.diagnostic_file,self.diagnostic_events)
            except (OSError,ValueError):pass
    def callback_error(self,kind,error,traceback):
        self.record_diagnostic('callback_error',error=error)
        if hasattr(self,'status'):self.status.set('An app error occurred. Save diagnostics for troubleshooting.')
        import traceback as trace
        trace.print_exception(kind,error,traceback)
    def save_diagnostics(self):
        from .diagnostics import build_diagnostics
        target=self.dialog.asksaveasfilename(parent=self.root,title='Save local app diagnostics (no EEG or recording identifiers)',
            initialfile='spectrum_studio_diagnostics.json',defaultextension='.json',filetypes=[('Diagnostics JSON','*.json')])
        if not target:return
        try:
            active=lambda worker:bool(worker and worker.is_alive())
            report=build_diagnostics(self.diagnostic_events,completed_runs=len(self.results),
                run_active=self.run_in_progress(),inspection_active=active(self.inspect_worker))
            Path(target).write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
            self.status.set('Diagnostics saved locally. EEG, recording identifiers and paths were excluded. Nothing was sent.')
        except (OSError,ValueError) as exc:self.messagebox.showerror('Cannot save diagnostics',str(exc),parent=self.root)
    def close(self):
        if self.worker and self.worker.is_alive():
            self.cancel_event.set();self.status.set('Cancellation requested. Close again after the current processing stage finishes.');return
        self.record_diagnostic('loader_closed');self.root.destroy()


def main():
    import tkinter as tk
    set_windows_app_id()
    root=tk.Tk();StudioDesktop(root);root.mainloop();return 0
