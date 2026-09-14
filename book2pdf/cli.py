import argparse
import json
from pathlib import Path
import signal
import sys
import threading

from . import __version__
from .batch import run_batch
from .models import Options


def main(argv=None):
    import multiprocessing
    multiprocessing.freeze_support()
    arguments = list(argv if argv is not None else sys.argv[1:])
    if arguments and arguments[0]=='doctor':
        from .doctor import main as doctor
        return doctor(arguments[1:])
    if arguments and arguments[0]=='inspect-pdf-salvage':
        return salvage_command(arguments[1:])
    if arguments and arguments[0] == 'inspect-bkf-records':
        from .bkf_records import command
        return command(arguments[1:])
    if arguments and arguments[0] in ('analyze','compare','compare-family'):
        return research_command(arguments)
    parser = argparse.ArgumentParser(prog='book2pdf', description='Recover scanned .book files without OCR or source modification.')
    parser.add_argument('files', nargs='*', type=Path)
    parser.add_argument('--input', nargs='+', type=Path, default=[])
    parser.add_argument('-o', '--output', type=Path)
    parser.add_argument('--recursive', action='store_true')
    parser.add_argument('--overwrite', action='store_true')
    parser.add_argument('--skip-existing', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument('--jobs', type=int, default=2, choices=range(1,9), metavar='1..8')
    parser.add_argument('--validate-all-pages', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--pages', help='Export selected pages, e.g. 1-5,8,10-15 (ascending, duplicates removed)')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--debug', action='store_true', help='Include concise forensic evidence in JSONL')
    parser.add_argument('--flat', action='store_true', help='Do not preserve relative subdirectories')
    parser.add_argument('--recovery', choices=['exact', 'safe-salvage','reconstruction-preview-opaque','reconstruction-preview-transparent'], default='safe-salvage',
                        help='Exact recovery, constrained ExtGState salvage, or an explicit SMask preview policy; repairs render every page')
    parser.add_argument('--analysis-dir', type=Path, help='Private forensic package directory')
    parser.add_argument('--allow-reconstructed-preview', action='store_true')
    parser.add_argument('--thermal-pause', action='store_true')
    parser.add_argument('--pause-c', type=float, default=85)
    parser.add_argument('--resume-c', type=float, default=75)
    parser.add_argument('--version', action='version', version=__version__)
    args = parser.parse_args(arguments)
    if args.allow_reconstructed_preview:
        args.recovery = 'reconstruction-preview-opaque'
    inputs = args.files + args.input
    if not inputs:
        parser.error('Provide one or more .book files or folders')
    output = args.output or (inputs[0].with_suffix('.pdf') if len(inputs) == 1 and not inputs[0].is_dir() else inputs[0].parent / 'pdf')
    cancel = threading.Event()
    previous = {sig: signal.signal(sig, lambda *_: cancel.set()) for sig in (signal.SIGINT, signal.SIGTERM)}

    def event(data):
        if data['type'] == 'result':
            r = data['result']
            print(f"{r['status']} {r['source_path']!r}" + (f" — {r['error']}" if r['error'] else ''), flush=True)
        elif data['type'] == 'dry_run' or args.verbose:
            print(json.dumps(data, ensure_ascii=False), flush=True)

    try:
        from .turbosun.source import protect_outputs
        protect_outputs(inputs,output,args.report)
        options = Options(overwrite=args.overwrite, skip_existing=args.skip_existing, jobs=args.jobs,
                          recursive=args.recursive, validate_all_pages=args.validate_all_pages,
                          preserve_tree=not args.flat, thermal_pause=args.thermal_pause,
                          pause_c=args.pause_c, resume_c=args.resume_c, debug=args.debug, recovery=args.recovery, analysis_dir=str(args.analysis_dir or ''))
        if args.pages:
            if len(inputs) != 1 or output.suffix.lower() != '.pdf':
                parser.error('--pages requires one file and a .pdf output')
            from .page_ranges import parse_pages
            parse_pages(args.pages)
            if args.dry_run:
                print(json.dumps({'source':str(inputs[0]),'output':str(output),'pages':args.pages,'bounds':'checked after opening document'}))
                return 0
            from .export import convert_selected
            from .report import Report
            import uuid
            path = args.report or output.parent / f'book2pdf-export-{uuid.uuid4().hex[:12]}.jsonl'
            if path.suffix.lower() in ('.pdf','.book'):
                raise ValueError('Report must be a separate JSONL file')
            report = Report(path)
            try:
                result = convert_selected(inputs[0], output, args.pages, options)
                report.write(result)
            finally:
                report.close()
            event({'type':'result','result':result.to_dict()})
            print(f'PAGES={result.page_count} REPORT={path}')
            return 0 if result.status in ('PASS_EXACT','PASS_REPAIRED','PASS_DECODED','RECONSTRUCTED_PREVIEW') else 1
        summary = run_batch(inputs, output, options, report_path=args.report, cancel=cancel,
                            on_event=event, explicit_file=output.suffix.lower() == '.pdf', dry_run=args.dry_run)
        print(' '.join(f'{k}={v}' for k, v in summary.items()))
        return 130 if cancel.is_set() else 1 if summary['FAILED'] or summary['UNSUPPORTED'] else 0
    except Exception as exc:
        print(f'Book2PDF: {exc}', file=sys.stderr)
        return 2
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def research_command(argv):
    parser = argparse.ArgumentParser(prog='book2pdf '+argv[0])
    parser.add_argument('files',nargs='+',type=Path)
    parser.add_argument('--analysis-dir',type=Path)
    args=parser.parse_args(argv[1:])
    if argv[0]=='compare' and len(args.files)!=2:
        parser.error('compare requires exactly two sources')
    try:
        from .research import analyze_file,compare_family
        result = ([analyze_file(p,root=str(args.analysis_dir or '')) for p in args.files] if argv[0]=='analyze'
                  else compare_family(args.files,root=str(args.analysis_dir or '')))
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except Exception as exc:
        print('Analysis failed: '+str(exc),file=sys.stderr)
        return 2


def salvage_command(argv):
    import tempfile
    parser=argparse.ArgumentParser(prog='book2pdf inspect-pdf-salvage')
    parser.add_argument('file',type=Path)
    parser.add_argument('--analysis-dir',type=Path)
    args=parser.parse_args(argv)
    try:
        from .recovery.engine import attempt
        parent=args.analysis_dir or Path(tempfile.gettempdir())
        parent.mkdir(parents=True,exist_ok=True)
        result=attempt(args.file.resolve(strict=True),parent)
        package=result.pop('_scratch');result.pop('_path',None)
        print(json.dumps({'package':package,**result},ensure_ascii=False,indent=2))
        return 0
    except Exception as exc:
        print('PDF evidence analysis failed: '+str(exc),file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
