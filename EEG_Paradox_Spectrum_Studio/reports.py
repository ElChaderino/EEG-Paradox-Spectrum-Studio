"""Automatic scientific PNG figures, offline gallery, PDF and measured summaries.

SPDX-License-Identifier: GPL-3.0-only
"""
from __future__ import annotations

import csv
import html
import json
import textwrap
from pathlib import Path

import numpy as np
from .recording_context import scalp_time_aligned,timing_description

BG='#070d17';PANEL='#0b1726';FG='#c6deef';MUTED='#7899b6';ACCENT='#69d1ef'


def _means(values):
    counts=np.isfinite(values).sum(axis=1)
    return np.divide(np.nansum(values,axis=1),counts,out=np.full(counts.shape,np.nan,dtype=float),where=counts>0)


def generate_reports(result,output,*,comparison=None,check_cancel=lambda:None,pdf=True):
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt,colors
    from matplotlib.colors import LinearSegmentedColormap
    from matplotlib.patches import Circle,Polygon,Ellipse
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    M=result.metadata;names=M['channel_names'];F=result.frequencies_hz;T=result.times_seconds
    counts=result.valid.sum(axis=1)
    mean_psd=np.divide(np.nansum(result.psd_uv2_hz,axis=1),counts[:,None],
                       out=np.full((len(names),len(F)),np.nan),where=counts[:,None]>0)
    means={key:_means(value) for key,value in result.band_power_uv2.items()}
    channel=next((i for i,name in enumerate(names) if name.lower().replace('-ref','')=='cz'),0)
    valid_rows=np.flatnonzero(result.valid[channel]);row=int(valid_rows[-1]) if len(valid_rows) else len(T)-1
    floor=-20.;ceiling=30.;norm=colors.Normalize(floor,ceiling)
    cmap=LinearSegmentedColormap.from_list('paradox_sdr',['#000008','#00004c','#0508cd','#0052f9','#00cee9','#23ef35','#ddf900','#ffb000','#ff2e0c','#ffede0'])
    cmap.set_bad('#48515b')
    db=10*np.log10(np.maximum(result.psd_uv2_hz,1e-12))
    synthetic='SYNTHETIC ENGINEERING FIXTURE - ' if M.get('synthetic') else ''
    source=synthetic+str(M['source']);source_display=textwrap.shorten(source,width=110,placeholder='...')
    figures=[]
    style={'figure.facecolor':BG,'axes.facecolor':PANEL,'axes.edgecolor':'#34516e','axes.labelcolor':FG,
           'text.color':FG,'xtick.color':MUTED,'ytick.color':MUTED,'grid.color':'#25415c','font.family':'DejaVu Sans',
           'font.size':10,'axes.titlesize':11,'axes.labelsize':10,'savefig.facecolor':BG,'text.parse_math':False}
    def decorate(fig,title,note=''):
        fig.suptitle('SPECTRUM STUDIO | '+title,x=.055,y=.97,ha='left',fontweight='bold',fontsize=16,color='#e1f1fa')
        fig.text(.055,.924,source_display+' | '+str(M['condition'])+' | Reference: '+str(M['reference'])[:75],fontsize=9,color=MUTED)
        footer=textwrap.shorten(note or 'Descriptive EEG measurements. Original-time windows; masked estimates are missing, never zero.',width=420,placeholder='...')
        fig.text(.055,.015,textwrap.fill(footer,width=175),fontsize=8,color=MUTED)
    def save(fig,stem,title,note=''):
        check_cancel();decorate(fig,title,note)
        path=output/(stem+'.png');fig.savefig(path,dpi=160);plt.close(fig)
        figures.append({'file':path.name,'title':title,'note':note})
    def axis(ax):ax.grid(True,alpha=.35);ax.spines[['top','right']].set_visible(False)
    def waterfall(ax,ch,*,compact=False):
        extent=[F[0]-M['bin_spacing_hz']/2,F[-1]+M['bin_spacing_hz']/2,T[0]-M['actual_hop_seconds']/2,T[-1]+M['actual_hop_seconds']/2]
        image=ax.imshow(np.ma.masked_invalid(db[ch]),extent=extent,origin='lower',aspect='auto',cmap=cmap,norm=norm,interpolation='nearest')
        ax.set_title(names[ch]+' | '+str(int(counts[ch]))+'/'+str(len(T))+' retained windows',loc='left',fontsize=9 if compact else 11)
        ax.set_xlabel('Frequency (Hz)');ax.set_ylabel('Original time (s)')
        for event in result.review.get('events',[]):
            if event['status']=='rejected' or (event['channels'] and names[ch] not in event['channels']):continue
            onset=event['onset_seconds']
            if T[0]<=onset<=T[-1]:ax.axhline(onset,color='#c0eee1',alpha=.35,lw=.6)
        return image
    with plt.rc_context(style):
        check_cancel()
        fig=plt.figure(figsize=(13.5,8.2));gs=fig.add_gridspec(2,3,left=.065,right=.95,bottom=.10,top=.87,hspace=.4,wspace=.42)
        ridge=fig.add_subplot(gs[0,0],projection='3d')
        lo=max(0,row-63);xx,yy=np.meshgrid(F,T[lo:row+1]);zz=db[channel,lo:row+1]
        if len(zz)>1:
            ridge.plot_surface(xx,yy,np.clip(zz,floor,ceiling),facecolors=cmap(norm(zz)),rcount=len(zz),ccount=len(F),
                               shade=False,linewidth=0,antialiased=False)
        elif np.isfinite(zz).any():ridge.plot(F,np.full(len(F),T[row]),zz[0],color=ACCENT)
        ridge.set(xlabel='Hz',ylabel='Time (s)',zlabel='PSD (dB)',zlim=(floor,ceiling),
                  xlim=(F[0],F[-1]),ylim=(T[lo]-M['actual_hop_seconds']/2,T[row]+M['actual_hop_seconds']/2))
        ridge.view_init(elev=27,azim=-72);ridge.set_box_aspect((1.6,1,.9),zoom=.90);ridge.set_title(names[channel]+' - exact spectral history',loc='left')
        for pane in (ridge.xaxis.pane,ridge.yaxis.pane,ridge.zaxis.pane):pane.set_facecolor(PANEL);pane.set_edgecolor('#203951')
        ax=fig.add_subplot(gs[1,:]);image=waterfall(ax,channel);fig.colorbar(image,ax=ax,pad=.015,label='PSD (dB re 1 uV^2/Hz)',fraction=.02)
        ax=fig.add_subplot(gs[0,1]);axis(ax);ax.plot(F,10*np.log10(np.maximum(mean_psd[channel],1e-12)),color=ACCENT,lw=1.4)
        ax.set(xlabel='Frequency (Hz)',ylabel='PSD (dB re 1 uV^2/Hz)',title='Mean linear PSD - '+names[channel])
        ax=fig.add_subplot(gs[0,2]);keys=list(means);values=[means[key][channel] for key in keys]
        ax.barh([key.replace('_',' ') for key in keys],values,color='#5bbed4');ax.invert_yaxis();axis(ax)
        ax.set(xlabel='Absolute band power (uV^2)',title='Mean band power - '+names[channel])
        save(fig,'01_overview','Recording overview','3D is spectral history, not anatomy. Fixed PSD scale: -20 to 30 dB re 1 uV^2/Hz. Gray = missing. Mean spectra average linear power.')
        fig,ax=plt.subplots(figsize=(13.5,8.2));fig.subplots_adjust(left=.08,right=.96,top=.86,bottom=.22);axis(ax)
        palette=plt.get_cmap('tab20')
        for ch,name in enumerate(names):ax.plot(F,10*np.log10(np.maximum(mean_psd[ch],1e-12)),lw=1,color=palette(ch%20),label=name)
        ax.set(xlabel='Frequency (Hz)',ylabel='Mean PSD (dB re 1 uV^2/Hz)')
        ax.legend(ncol=min(8,len(names)),loc='upper center',bbox_to_anchor=(.5,-.11),fontsize=8,frameon=False)
        save(fig,'02_channel_spectra','Channel spectra','Arithmetic means of retained linear PSD. Valid-window counts may differ by channel; overlapping windows are not independent observations.')
        fig,ax=plt.subplots(figsize=(13.5,8.2));fig.subplots_adjust(left=.12,right=.90,top=.86,bottom=.17)
        matrix=np.array([means[key] for key in means]).T;image=ax.imshow(np.ma.masked_invalid(10*np.log10(np.maximum(matrix,1e-12))),aspect='auto',cmap='viridis',interpolation='nearest')
        ax.set_yticks(range(len(names)),names);ax.set_xticks(range(len(means)),[x.replace('_',' ') for x in means],rotation=20,ha='right')
        fig.colorbar(image,ax=ax,label='Band power (dB re 1 uV^2)')
        save(fig,'03_band_power','Band-power overview','Absolute mean band power. SMR and high beta overlap other bands. Gray = unavailable; zero is shown at -120 dB. No normative scaling.')
        fig,(ax,left)=plt.subplots(1,2,figsize=(13.5,8.2),gridspec_kw={'width_ratios':[3,1]});fig.subplots_adjust(left=.085,right=.95,top=.85,bottom=.14,wspace=.32)
        if result.quality.get('flags') is not None:
            bits=result.quality['flags'];flag_count=sum(((bits&bit)!=0).astype(int) for bit in [1,2,4,8,16,32])
            image=ax.imshow(flag_count,aspect='auto',origin='upper',interpolation='nearest',extent=[T[0]-M['actual_hop_seconds']/2,T[-1]+M['actual_hop_seconds']/2,len(names)-.5,-.5],vmin=0,vmax=6,cmap='magma')
            fig.colorbar(image,ax=ax,label='Number of technical flags (0-6; not severity)',fraction=.035,pad=.02)
        else:ax.text(.5,.5,'Quality arrays unavailable in this export',ha='center',transform=ax.transAxes)
        ax.set_yticks(range(len(names)),names);ax.set_xlabel('Original window center (s)');ax.set_title('Technical flags; no new exclusions')
        left.barh(range(len(names)),counts/len(T)*100,color='#5abac6');left.set_yticks(range(len(names)),names);left.invert_yaxis();left.set_xlim(0,100);left.set_xlabel('Retained windows (%)');axis(left)
        save(fig,'04_quality','Coverage and signal quality','Flags: nonfinite, BAD/EDGE, flat, high amplitude, declared hardware rail, line-noise ratio. No flag is a diagnosis. Thresholds are recorded in method JSON.')
        fig,axes=plt.subplots(2,2,figsize=(13.5,8.2));fig.subplots_adjust(left=.08,right=.96,top=.85,bottom=.13,hspace=.43,wspace=.25)
        for ax,key in zip(axes.flat,['delta','theta','alpha','beta']):
            axis(ax);values=result.band_power_uv2[key][channel]
            ax.plot(T,values,color=ACCENT,lw=1)
            ax.fill_between(T,0,1,where=~result.valid[channel],transform=ax.get_xaxis_transform(),color='#8994a4',alpha=.20)
            ax.set(title=names[channel]+' | '+key,xlabel='Original window center (s)',ylabel='Band power (uV^2)',
                   xlim=(M['selected_start_seconds'],M['selected_stop_seconds']))
        save(fig,'04b_band_trends','Band-power timeline - '+names[channel],
             'Original complete windows; no temporal averaging or interpolation. Gray marks missing estimates. Overlapping windows are not independent. All channels/bands remain in the CSV.')
        for page,start in enumerate(range(0,len(names),12),1):
            selected=list(range(start,min(start+12,len(names))));cols=min(4,len(selected));rows=int(np.ceil(len(selected)/cols))
            fig,axes=plt.subplots(rows,cols,squeeze=False,figsize=(13.5,8.2));fig.subplots_adjust(left=.065,right=.91,top=.85,bottom=.13,hspace=.70,wspace=.38)
            for ax,ch in zip(axes.flat,selected):image=waterfall(ax,ch,compact=True);ax.tick_params(labelsize=8);ax.xaxis.label.set_size(8);ax.yaxis.label.set_size(8)
            for ax in list(axes.flat)[len(selected):]:ax.set_visible(False)
            cax=fig.add_axes([.935,.20,.012,.57]);fig.colorbar(image,cax=cax,label='PSD (dB re 1 uV^2/Hz)')
            save(fig,f'05_waterfalls_{page:02}','Channel waterfalls - page '+str(page),'Fixed shared color scale. Exact original-time windows; gray cells are missing. Event lines are overlays and do not create exclusions.')
        for page,start in enumerate(range(0,len(names),12),1):
            selected=list(range(start,min(start+12,len(names))));cols=min(3,len(selected));rows=int(np.ceil(len(selected)/cols))
            fig,axes=plt.subplots(rows,cols,squeeze=False,figsize=(13.5,8.2));fig.subplots_adjust(left=.075,right=.96,top=.84,bottom=.12,hspace=.65,wspace=.32)
            for ax,ch in zip(axes.flat,selected):
                values=result.trace_uv[ch];valid=np.isfinite(values);offset=float(np.mean(values[valid])) if valid.any() else 0.
                n=min(len(values),1000);x=[];low=[];high=[]
                for i in range(n):
                    a=i*len(values)//n;b=max(a+1,(i+1)*len(values)//n);chunk=values[a:b]-offset;good=chunk[np.isfinite(chunk)]
                    x.append(float(result.trace_times_seconds[a]));low.append(float(good.min()) if len(good) else np.nan);high.append(float(good.max()) if len(good) else np.nan)
                ax.vlines(x,low,high,color=ACCENT,lw=.6);axis(ax)
                ax.set_title(names[ch]+' | display mean '+format(offset,'.2f')+' uV removed',loc='left',fontsize=9)
                ax.set_xlabel('Original time (s)',fontsize=8);ax.set_ylabel('Voltage (uV)',fontsize=8);ax.tick_params(labelsize=8)
            for ax in list(axes.flat)[len(selected):]:ax.set_visible(False)
            save(fig,f'06_waveforms_{page:02}','Acquired waveform scope - page '+str(page),'Fixed first-12-second excerpt (or shorter retained scope). Min/max envelopes retain peaks. Display mean subtraction does not alter samples or PSD.')
        fig,ax=plt.subplots(figsize=(13.5,8.2));fig.subplots_adjust(left=.10,right=.88,top=.86,bottom=.12);ax.set_aspect('equal');ax.set_xlim(-1.4,1.4);ax.set_ylim(-1.35,1.35)
        montage=result.review.get('montage',{});positions=montage.get('positions_xy',{})
        unpositioned=[name for name in names if name not in positions]
        points=[]
        for name,xy in positions.items():
            ch=names.index(name)
            value=result.band_power_uv2.get('alpha',np.full(result.valid.shape,np.nan))[ch,row]
            if np.isfinite(value):points.append((name,xy,float(value)))
        noncollinear=len(points)>=3 and np.linalg.matrix_rank(np.array([p[1] for p in points])-np.mean([p[1] for p in points],axis=0))>=2
        map_available=scalp_time_aligned(M) and noncollinear
        if map_available:
            gx,gy=np.meshgrid(np.linspace(-1,1,100),np.linspace(-1,1,100));weights=[]
            for _,(x,y),_ in points:weights.append(1/np.maximum((gx-x)**2+(gy-y)**2,1e-5))
            weights=np.asarray(weights);values=np.sum(weights*np.array([p[2] for p in points])[:,None,None],axis=0)/weights.sum(axis=0)
            values[gx**2+gy**2>1]=np.nan;maximum=max(1.,float(np.nanmax(values)))
            image=ax.imshow(values,origin='lower',extent=[-1,1,-1,1],cmap='inferno',vmin=0,vmax=maximum)
            fig.colorbar(image,ax=ax,label='Alpha power (uV^2)',shrink=.8)
            ax.add_patch(Circle((0,0),1,fill=False,color=FG));ax.add_patch(Polygon([[-.1,1],[0,1.13],[.1,1]],closed=False,fill=False,color=FG))
            for name,(x,y),value in points:ax.plot(x,y,'o',color=FG,ms=3);ax.text(x,y+.045,name,ha='center',fontsize=8)
            ax.axis('off');note='Window center '+format(T[row],'.2f')+' s. '+montage['source']+'. '+timing_description(M)+'. Display-only IDW interpolation; not source localization.'
            if unpositioned:
                fig.text(.055,.76,'No saved scalp position:\n'+textwrap.fill(', '.join(unpositioned),width=19),fontsize=9,color=MUTED,va='top')
                note+=' Unpositioned channels remain available in the spectra: '+', '.join(unpositioned)+'.'
        else:
            ax.axis('off');ax.text(.5,.64,'Scalp map unavailable for this recording context',ha='center',fontsize=15,transform=ax.transAxes,color=FG)
            reason='Declared or file-backed channel time alignment and at least three valid non-collinear scalp positions are required.'
            ax.text(.5,.44,textwrap.fill(reason,70),ha='center',va='center',fontsize=12,transform=ax.transAxes,color=MUTED)
            note='No acquisition context or electrode positions were invented. Use the interactive viewer after supplying verified context.'
        save(fig,'07_scalp_map','Synchronized scalp band power',note)
        if comparison is not None:
            fig,ax=plt.subplots(figsize=(13.5,8.2));fig.subplots_adjust(left=.08,right=.95,top=.85,bottom=.14);axis(ax)
            for data,label,color in [(result,'A','#69d1ef'),(comparison,'B','#f0b76d')]:
                if names[channel] not in data.metadata['channel_names']:continue
                ch=data.metadata['channel_names'].index(names[channel]);count=data.valid[ch].sum()
                values=np.nansum(data.psd_uv2_hz[ch],axis=0)/count if count else np.full(len(F),np.nan)
                ax.plot(F,10*np.log10(np.maximum(values,1e-12)),color=color,label=label+' - '+data.metadata['condition'])
            ax.legend(frameon=False);ax.set(xlabel='Frequency (Hz)',ylabel='Mean PSD (dB re 1 uV^2/Hz)')
            save(fig,'08_comparison','Recording A/B comparison - '+names[channel],'Complete retained selections, mean linear PSD, identical estimator and reference. Descriptive comparison; no significance test.')
    summary={'schema':'eeg-paradox.spectrum-report.v1','source':M['source'],'source_sha256':M.get('source_sha256'),
             'synthetic':bool(M.get('synthetic')),'condition':M['condition'],'reference':M['reference'],
             'selected_seconds':[M['selected_start_seconds'],M['selected_stop_seconds']],
             'channels':names,'excluded_channels':M.get('excluded_channels',[]),'windows':len(T),'valid_windows_per_channel':counts.tolist(),
             'mean_band_power_uv2':{key:[float(x) if np.isfinite(x) else None for x in value] for key,value in means.items()},
             'scalp_map_available':bool(map_available),'scalp_positions_source':montage.get('source'),
             'unpositioned_channels':unpositioned,
             'timing_context':timing_description(M),'snapshot_channel':names[channel],
             'snapshot_window_center_seconds':float(T[row]),'figures':figures,
             'psd_display_scale_db':[floor,ceiling],'interpretation':'descriptive measurements only; no normative or clinical classification'}
    conversion=M.get('input_provenance',{}).get('conversion') or {}
    summary['intake_notices']=[]
    if conversion.get('mapped_event_table_notice'):
        summary['intake_notices'].append('Legacy empty mapped-event table marker: mapped events unavailable; none reconstructed. Primary and artifact marks retained. Source bytes unchanged. See intake audit for the exact header checks.')
    if conversion.get('quantization_notice'):
        summary['intake_notices'].append(conversion['quantization_notice'])
    (output/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False),encoding='utf-8')
    with (output/'channel_summary.csv').open('w',newline='',encoding='utf-8') as handle:
        writer=csv.writer(handle);writer.writerow(['channel','valid_windows','total_windows',*[key+'_uV2' for key in means]])
        for ch,name in enumerate(names):
            label="'"+name if name.lstrip().startswith(('=','+','-','@')) else name
            writer.writerow([label,int(counts[ch]),len(T),*[float(values[ch]) if np.isfinite(values[ch]) else '' for values in means.values()]])
    if pdf:_write_pdf(output,summary,M,check_cancel)
    return summary


def _write_pdf(output,summary,metadata,check_cancel):
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.lib.pagesizes import landscape,A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import matplotlib
    font=Path(matplotlib.get_data_path())/'fonts/ttf/DejaVuSans.ttf'
    if 'SpectrumSans' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('SpectrumSans',str(font)))
    width,height=landscape(A4);canvas=Canvas(str(output/'Spectrum_Studio_Report.pdf'),pagesize=(width,height))
    canvas.setTitle('Spectrum Studio - descriptive EEG review');canvas.setAuthor('EEG Paradox Spectrum Studio')
    def background(title,page):
        canvas.setFillColorRGB(.027,.05,.085);canvas.rect(0,0,width,height,fill=1,stroke=0)
        canvas.setFont('SpectrumSans',18);canvas.setFillColorRGB(.82,.92,.97);canvas.drawString(30,height-38,title)
        canvas.setFont('SpectrumSans',8);canvas.setFillColorRGB(.48,.63,.74);canvas.drawString(30,20,'Spectrum Studio | Local descriptive EEG report | No clinical classification')
        canvas.drawRightString(width-30,20,str(page))
    background('EEG PARADOX / SPECTRUM STUDIO',1)
    fields=[('Recording',summary['source']),('Condition / reference',str(summary['condition'])+' / '+str(summary['reference'])),
            ('Selection',format(summary['selected_seconds'][0],'.3f')+' - '+format(summary['selected_seconds'][1],'.3f')+' original seconds'),
            ('Acquisition',str(len(summary['channels']))+' channels, '+str(metadata['sample_rate_hz'])+' Hz; '+timing_description(metadata)),
            ('Estimator',str(metadata['actual_window_seconds'])+' s periodic Hann; '+str(metadata['actual_hop_seconds'])+' s hop; per-window mean removed; no zero padding'),
            ('Resolution',str(metadata['bin_spacing_hz'])+' Hz FFT bins; '+str(metadata['hann_enbw_hz'])+' Hz Hann ENBW'),
            ('Integrity',summary.get('source_sha256') or 'Synthetic or in-memory data; no input-file hash'),
            ('Coverage','Valid windows by channel: '+', '.join(name+': '+str(n) for name,n in zip(summary['channels'],summary['valid_windows_per_channel']))),
            ('Reading this report','Power and technical-quality measurements only. Masked windows are missing, never zero. Overlapping windows are not independent. Hardware clipping is unchecked unless calibrated rails were explicitly provided.'),
            ('Scalp map','Available. '+(summary.get('scalp_positions_source') or '') if summary['scalp_map_available'] else 'Unavailable: required time alignment/positions/valid sites were not established.'),
            ('Data preservation','No module filtering, re-reference or artifact removal is performed. The waveform removes its display mean only. Original numerical estimates and provenance are retained alongside this report.')]
    if summary['synthetic']:fields.insert(0,('DATA TYPE','SYNTHETIC ENGINEERING FIXTURE - NOT A PATIENT RECORDING'))
    if summary.get('excluded_channels'):fields.insert(4,('Excluded input channels',', '.join(summary['excluded_channels'])))
    fields.extend(('Intake notice',notice) for notice in summary.get('intake_notices',[]))
    y=height-78
    page=1
    for label,value in fields:
        lines=textwrap.wrap(str(value),105) or ['']
        if y-24-12*len(lines)<45:
            canvas.showPage();page+=1;background('Recording methods and intake / continued',page);y=height-78
        canvas.setFont('SpectrumSans',9);canvas.setFillColorRGB(.46,.80,.89);canvas.drawString(32,y,label.upper());y-=14
        canvas.setFillColorRGB(.76,.86,.92)
        for line in lines:canvas.drawString(32,y,line);y-=12
        y-=10
    canvas.showPage()
    for page,figure in enumerate(summary['figures'],page+1):
        check_cancel();background(figure['title'],page)
        canvas.drawImage(ImageReader(str(output/figure['file'])),25,48,width=width-50,height=height-110,preserveAspectRatio=True,anchor='c')
        canvas.showPage()
    canvas.save()


def write_gallery(root,summary):
    root=Path(root);escape=html.escape
    cards=''.join('<section><h2>'+escape(item['title'])+'</h2><a href="reports/'+escape(item['file'],quote=True)+'"><img loading="lazy" src="reports/'+escape(item['file'],quote=True)+'" alt="'+escape(item['title'],quote=True)+'"></a><p>'+escape(item['note'])+'</p></section>' for item in summary['figures'])
    content='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Spectrum Studio | Recording report</title><style>
    body{margin:0;background:#070d17;color:#c6deef;font:15px/1.6 'Segoe UI',sans-serif}main{max-width:1380px;margin:auto;padding:30px}h1{font-size:30px;letter-spacing:1px}h2{font-size:19px;color:#c5e9f8}p{color:#8ca9bf}nav{display:flex;flex-wrap:wrap;gap:10px;margin:22px 0}a{color:#7fdbed}nav a{border:1px solid #34516a;padding:8px 14px;text-decoration:none;border-radius:4px}section{background:#0b1726;border:1px solid #20364a;padding:18px;margin:20px 0}img{display:block;width:100%;height:auto}code{overflow-wrap:anywhere}.synthetic{color:#f0b76d;font-weight:bold}@media print{body{background:white;color:black}main{padding:0}section{break-inside:avoid;border:0;background:white}nav{display:none}}</style><main>'''
    content+='<h1>EEG PARADOX / SPECTRUM STUDIO</h1>'
    if summary['synthetic']:content+='<p class="synthetic">SYNTHETIC ENGINEERING FIXTURE - NOT A PATIENT RECORDING</p>'
    content+='<p>'+escape(summary['source'])+' | '+escape(summary['condition'])+' | Reference: '+escape(summary['reference'])+'</p>'
    for notice in summary.get('intake_notices',[]):content+='<p>'+escape(notice)+'</p>'
    content+='<nav><a href="review/OPEN_SPECTRUM_STUDIO.html">Open interactive viewer</a><a href="reports/Spectrum_Studio_Report.pdf">PDF report</a><a href="reports/channel_summary.csv">Channel summary CSV</a><a href="review/band_power.csv">All window band powers</a><a href="review/method.json">Method JSON</a><a href="reports/summary.json">Report data</a><a href="run_manifest.json">Run manifest</a></nav>'
    content+='<p>Original-time recording review. Technical flags are proposals; no diagnosis or normative classification is produced.</p>'+cards+'</main></html>'
    path=root/'OPEN_REPORT.html';path.write_text(content,encoding='utf-8');return path
