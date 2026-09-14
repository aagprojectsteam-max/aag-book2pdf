"""Build on Windows x64. One shared runtime, GUI and console entry points."""
from pathlib import Path
import os
import sys
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

if sys.platform != 'win32':
    raise SystemExit('Windows artifacts must be built on native Windows.')
root = Path(SPECPATH).resolve().parents[1]
metadata = []
for package in ('aag-book2pdf', 'pikepdf', 'PyMuPDF', 'PySide6-Essentials', 'shiboken6'):
    metadata += copy_metadata(package)
data = metadata + [
    (str(root / 'book2pdf/gui/aag-book2pdf.svg'), 'book2pdf/gui'),
    (str(root / '.build/windows-notices'), 'THIRD_PARTY_NOTICES'),
    (str(root / 'WINDOWS.md'), '.'),
]
hidden = collect_submodules('book2pdf') + ['PySide6.QtSvg', 'PySide6.QtPrintSupport']
binaries = []
runtime = os.environ.get('BOOK2PDF_DJVU_RUNTIME_DIR')
if runtime:
    runtime = Path(runtime).resolve()
    if not any((runtime/name).is_file() for name in ('libdjvulibre-21.dll','libdjvulibre.dll','djvulibre.dll')):
        raise SystemExit('DjVu runtime directory has no supported native DLL')
    notices = [p for p in runtime.iterdir() if p.is_file() and p.name.upper().startswith(('LICENSE','COPYING','SOURCE'))]
    if not notices:
        raise SystemExit('Include DjVuLibre license/source-distribution notices with the native runtime')
    binaries += [(str(p),'.') for p in runtime.glob('*.dll')]
    data += [(str(p),'THIRD_PARTY_NOTICES/DjVuLibre') for p in notices]
analyses = []
executables = []
for entry, name, console in [('gui_entry.py', 'AAG-Book2PDF', False), ('cli_entry.py', 'book2pdf', True)]:
    a = Analysis([str(root / 'packaging/windows' / entry)], pathex=[str(root)],
                 binaries=binaries, datas=data, hiddenimports=hidden,
                 excludes=['pytest', 'tkinter', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets'],
                 noarchive=False)
    analyses.append(a)
    executables.append(EXE(PYZ(a.pure), a.scripts, [], exclude_binaries=True,
        name=name, debug=False, strip=False, upx=False, console=console,
        icon=str(root / '.build/windows-icon.ico'),
        manifest=str(root / 'packaging/windows/app.manifest')))
coll = COLLECT(*executables, *(a.binaries for a in analyses), *(a.datas for a in analyses),
               strip=False, upx=False, name='AAG-Book2PDF')
