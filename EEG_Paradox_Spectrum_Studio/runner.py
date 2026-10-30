"""One local recording run: intake, analysis, interactive export and reports."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from uuid import uuid4

from .adapters import analyze_file
from .artifacts import read_object,verify_inventory
from .export import export_studio
from .intake import sha256
from .review import attach_review,artifact_review_events,normalize_events
from .spectral import SpectrumConfig


@dataclass(frozen=True)
class RunOptions:
    start: float=0.
    duration: float|None=120.
    config: SpectrumConfig=SpectrumConfig()
    channels: tuple[str,...]|None=None
    reference: str='unknown'
    condition: str='UNKNOWN'
    simultaneous: bool|None=None
    montage: str|None=None
    sample_rate: float|None=None
    unit: str|None=None
    converter_root: Path|None=None
    project: str='Spectrum Studio'


@dataclass(frozen=True)
class RunArtifacts:
    root: Path
    viewer: Path
    report: Path
    pdf: Path|None
    figures: tuple[Path,...]


def _completed_manifest(output):
    manifest=read_object(output/'run_manifest.json','run manifest')
    state=read_object(output/'run_status.json','completed run status')
    if (manifest.get('schema')!='eeg-paradox.spectrum-run.v1'
            or manifest.get('status')!='complete' or state.get('status')!='complete'):
        raise ValueError('Choose a completed Spectrum Studio run folder')
    if manifest.get('entry')!='OPEN_REPORT.html' or manifest.get('viewer')!='review/OPEN_SPECTRUM_STUDIO.html':
        raise ValueError('Invalid run manifest: report and viewer entrypoints do not match this format')
    verify_inventory(output,manifest.get('files_sha256'),required=(manifest.get('entry'),manifest.get('viewer')))
    return manifest


def read_completed_run(output):
    """Restore a completed local run after verifying its retained file inventory."""
    output=Path(output).resolve(strict=True);manifest=_completed_manifest(output)
    files=manifest['files_sha256']
    report=output/manifest['entry'];viewer=output/manifest['viewer']
    pdf=output/'reports/Spectrum_Studio_Report.pdf' if 'reports/Spectrum_Studio_Report.pdf' in files else None
    figures=tuple(output/name for name in files if name.startswith('reports/') and name.endswith('.png'))
    return RunArtifacts(output,viewer,report,pdf,figures)


def unique_output(parent,source='recording'):
    stem=re.sub(r'[^A-Za-z0-9_-]+','_',Path(source).stem).strip('_')[:60] or 'recording'
    return Path(parent)/(stem+'_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:6])


def refresh_run(output):
    """Refresh a run's viewer after checking every existing artifact hash."""
    from .export import refresh_studio
    from . import __version__
    output=Path(output).resolve(strict=True);manifest_path=output/'run_manifest.json'
    manifest=_completed_manifest(output)
    refresh_studio(output/'review')
    manifest['version']=__version__
    manifest['files_sha256']={name:sha256(output/name) for name in manifest['files_sha256']}
    staged=output/('.run_manifest_'+uuid4().hex+'.tmp')
    staged.write_text(json.dumps(manifest,indent=2),encoding='utf-8');staged.replace(manifest_path)
    return output/'OPEN_REPORT.html'


def run_recording(source,output,*,options=None,comparison_source=None,comparison_condition='UNKNOWN',
                  events_file=None,artifact_review_file=None,progress=lambda message:None,
                  cancelled=lambda:False,result=None,comparison_result=None,make_reports=True):
    """Create a fresh run folder. Failures remain explicitly marked, never complete.

    Native conversions stay inside this run, not next to the source recording.
    No viewer/browser is automatically opened by this worker.
    """
    from .reports import generate_reports,write_gallery
    options=options or RunOptions();output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    def status(state,**extra):
        (output/'run_status.json').write_text(json.dumps({'schema':'eeg-paradox.spectrum-run-status.v1','status':state,**extra},indent=2),encoding='utf-8')
    def check():
        if cancelled():raise InterruptedError('Run cancelled between processing stages; partial output retained')
    def load(path,label,condition):
        check();progress('Loading '+label+': '+Path(path).name)
        return analyze_file(path,picks=options.channels,start=options.start,duration=options.duration,
            reference=options.reference,condition=condition,simultaneous=options.simultaneous,config=options.config,
            sample_rate=options.sample_rate,unit=options.unit,conversion_dir=output/'intake'/label,
            converter_root=options.converter_root)
    status('running')
    try:
        check()
        if result is None:result=load(source,'primary',options.condition)
        attach_review(result,montage=options.montage,project=options.project)
        events=list(result.review['events'])
        if events_file:
            obj=json.loads(Path(events_file).read_text(encoding='utf-8'))
            if not result.metadata.get('source_sha256') or obj.get('source_sha256')!=result.metadata['source_sha256']:
                raise ValueError('Event file must match recording source_sha256')
            events+=normalize_events(obj['events'],result.metadata,origin='import')
        if artifact_review_file:events+=artifact_review_events(json.loads(Path(artifact_review_file).read_text(encoding='utf-8')),result)
        if events_file or artifact_review_file:attach_review(result,events=events,project=options.project)
        comparison=load(comparison_source,'comparison',comparison_condition) if comparison_source else comparison_result
        check();progress('Writing interactive review and numerical exports')
        viewer=export_studio(result,output/'review',comparison=comparison)
        summary=None
        if make_reports:
            check();progress('Rendering PNG figures and PDF report')
            summary=generate_reports(result,output/'reports',comparison=comparison,check_cancel=check)
            report=write_gallery(output,summary)
        else:
            report=output/'OPEN_REPORT.html'
            report.write_text('<!doctype html><meta charset="utf-8"><title>Spectrum Studio</title><p>Automatic image reports were disabled for this run.</p><a href="review/OPEN_SPECTRUM_STUDIO.html">Open interactive viewer</a>',encoding='utf-8')
        check();progress('Verifying run artifacts')
        files={str(path.relative_to(output)).replace('\\','/'):sha256(path)
               for path in output.rglob('*') if path.is_file() and path.name not in ('run_manifest.json','run_status.json')}
        from . import __version__
        manifest={'schema':'eeg-paradox.spectrum-run.v1','version':__version__,'status':'complete',
                  'entry':report.name,'viewer':'review/OPEN_SPECTRUM_STUDIO.html','source_sha256':result.metadata.get('source_sha256'),
                  'files_sha256':files,'report_figures':len(summary['figures']) if summary else 0}
        (output/'run_manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        status('complete',entry=report.name)
        progress('Complete: '+str(output))
        return RunArtifacts(output,viewer,report,output/'reports/Spectrum_Studio_Report.pdf' if make_reports else None,
                            tuple(output/'reports'/item['file'] for item in summary['figures']) if summary else ())
    except Exception as exc:
        status('cancelled' if isinstance(exc,InterruptedError) else 'failed',error=str(exc))
        (output/'RUN_FAILED.txt').write_text(str(exc)+'\nThis run is incomplete. Original recordings were not overwritten. Choose a new run folder to retry.\n',encoding='utf-8')
        raise
