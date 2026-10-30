from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .demo import make_demo
from .export import refresh_studio
from .spectral import SpectrumConfig
from .runner import RunOptions,run_recording


def main():
    parser = argparse.ArgumentParser(description="Separate, local EEG Spectrum Studio")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--gui", action="store_true", help="Open the native recording loader")
    source.add_argument("--list-formats", action="store_true", help="List accepted recording types")
    source.add_argument("--input", type=Path, help="EEG recording: EDF/BDF/FIF/GDF/VHDR/CNT/SET/CSV/TSV/JSON/NPZ/BXS/WinEEG")
    source.add_argument("--demo", action="store_true", help="Clearly labelled synthetic 90-second demo")
    source.add_argument("--refresh", type=Path, help="Update an existing Spectrum export's viewer, preserving scientific arrays")
    parser.add_argument("--output", type=Path, help="New output folder (never overwrites)")
    parser.add_argument("--start", type=float, default=0.)
    parser.add_argument("--duration", type=float, default=120., help="Selected duration; default 120 seconds")
    parser.add_argument("--whole", action="store_true", help="Analyze the whole remaining recording; spectral bounds still apply")
    parser.add_argument("--sfreq", type=float, help="Explicit sample rate for tabular inputs without metadata")
    parser.add_argument("--unit", choices=("V","mV","uV","nV"), help="Explicit calibrated unit for tabular inputs")
    parser.add_argument("--converter-root", type=Path, help="Optional maintained WinEEG converter root")
    parser.add_argument("--no-reports", action="store_true", help="Skip automatic PNG/PDF reports for this run")
    parser.add_argument("--window", type=float, default=4.)
    parser.add_argument("--hop", type=float, default=.5)
    parser.add_argument("--fmin", type=float, default=.5)
    parser.add_argument("--fmax", type=float, default=45.)
    parser.add_argument("--channels", nargs="+", help="Exact EEG labels; default all EEG except bad/aux channels")
    parser.add_argument("--reference", default="unknown", help="Documented input reference; no re-reference")
    parser.add_argument("--condition", default="UNKNOWN", help="Operator-supplied state; never inferred")
    parser.add_argument("--simultaneous", choices=("yes", "no", "unknown"), default="unknown")
    parser.add_argument("--montage", choices=("standard_1020",), help="Explicit template scalp positions; not measured anatomy")
    parser.add_argument("--events", type=Path, help="Original-time events JSON with matching source_sha256")
    parser.add_argument("--artifact-review", type=Path, help="Matching Decoder artifact-review sidecar; display decisions only")
    parser.add_argument("--project", default="Spectrum Studio", help="Origin project for the portable review handoff")
    parser.add_argument("--compare-input", type=Path, help="Second calibrated recording; identical known reference/settings required")
    parser.add_argument("--compare-condition", default="UNKNOWN")
    args = parser.parse_args()
    if args.gui or len(sys.argv)==1:
        from .desktop import main as desktop_main
        return desktop_main()
    if args.list_formats:
        from .intake import FORMATS
        print(json.dumps(FORMATS,indent=2));return 0
    if not (args.input or args.demo or args.refresh):parser.error("Choose --input, --demo, --gui or --refresh")
    if args.refresh:
        if args.output:
            parser.error("--refresh uses the existing export folder; omit --output")
        try:
            if (args.refresh/'run_manifest.json').exists():
                from .runner import refresh_run
                print(refresh_run(args.refresh))
            else:print(refresh_studio(args.refresh))
        except (ValueError, OSError, RuntimeError) as exc:
            parser.exit(2, f"Spectrum Studio: {exc}\n")
        return 0
    if args.output is None:
        parser.error("--output is required for a new analysis or demo")
    if args.output.exists():
        parser.error("Output already exists; select a new folder")
    config = SpectrumConfig(args.window, args.hop, args.fmin, args.fmax)
    try:
        options=RunOptions(args.start,None if args.whole else args.duration,config,
            tuple(args.channels) if args.channels else None,args.reference,args.condition,
            {"yes":True,"no":False,"unknown":None}[args.simultaneous],args.montage,
            args.sfreq,args.unit,args.converter_root,args.project)
        run=run_recording(args.input,args.output,options=options,comparison_source=args.compare_input,
            comparison_condition=args.compare_condition,events_file=args.events,artifact_review_file=args.artifact_review,
            result=make_demo(config) if args.demo else None,
            comparison_result=make_demo(config,gain=1.4) if args.demo else None,
            make_reports=not args.no_reports,progress=lambda message:print(message,flush=True))
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, f"Spectrum Studio: {exc}\n")
    print(run.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
