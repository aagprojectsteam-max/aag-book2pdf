"""Probe optional/native runtimes in isolated processes; never label files here."""
import argparse
import json
import subprocess
import sys
import shutil


def probe(name, code):
    script = '''import json
try:
 CODE
except ModuleNotFoundError as e:
 print(json.dumps({'status':'DEPENDENCY_MISSING','error':str(e)}))
except Exception as e:
 print(json.dumps({'status':getattr(e,'code','RUNTIME_LOAD_FAILED'),'error':str(e)}))
else:
 print(json.dumps({'status':'PASS'}))
'''.replace(' CODE', ' ' + code)
    try:
        run = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=30)
        record = json.loads(run.stdout.strip().splitlines()[-1]) if run.returncode == 0 else {'status':'RUNTIME_LOAD_FAILED','error':run.stderr[-2000:]}
    except subprocess.TimeoutExpired:
        record = {'status':'LIMIT_EXCEEDED','error':'Runtime probe timed out'}
    except (ValueError, IndexError, OSError) as exc:
        record = {'status':'RUNTIME_LOAD_FAILED','error':str(exc)}
    return {'name':name, **record}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--require-djvu', action='store_true')
    args = parser.parse_args(argv)
    rows = [probe(name, code) for name, code in (
        ('pikepdf','import pikepdf; pikepdf.Pdf.new().close()'),
        ('PyMuPDF','import pymupdf; pymupdf.open().close()'),
        ('PySide6','from PySide6 import QtWidgets, QtPrintSupport'),
        ('pytest-qt','import pytestqt'),
        ('DjVuLibre','from book2pdf.bkf.native import Decoder; d=Decoder(); d.close()'))]
    for name in ('qpdf','pdfinfo','pdftoppm'):
        rows.append({'name':name,'status':'PASS' if shutil.which(name) else 'DEPENDENCY_MISSING','path':shutil.which(name)})
    required = {'pikepdf','PyMuPDF','PySide6'} | ({'DjVuLibre'} if args.require_djvu else set())
    ok = all(r['status']=='PASS' for r in rows if r['name'] in required)
    print(json.dumps({'status':'PASS' if ok else 'FAIL','python':sys.version,'platform':sys.platform,'checks':rows},indent=2))
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
