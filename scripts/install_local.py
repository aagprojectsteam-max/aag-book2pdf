#!/usr/bin/env python3
"""User-local install; only app-owned paths are ever removed."""
import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

MARKER = 'AAG-BOOK2PDF-MANAGED-V1'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prefix', type=Path, default=Path.home() / '.local')
    parser.add_argument('--uninstall', action='store_true')
    parser.add_argument('--require-bkf', action='store_true', help='Require a working native DjVuLibre runtime for BKF opening')
    parser.add_argument('--mdb-bin', type=Path, help='Trusted MDB Tools bin directory to copy into the managed environment for TurboSun')
    parser.add_argument('--wheel', type=Path, help='Install an already built local wheel without resolving dependencies (existing environment only)')
    parser.add_argument('--nautilus', action='store_true', help='Install an optional Nautilus Scripts action (default prefix only)')
    parser.add_argument('--make-default', action='store_true', help='Set AAG Book2PDF as the default handler for .book files')
    args = parser.parse_args()
    prefix = args.prefix.expanduser().resolve()
    if any(c in str(prefix) for c in '\n\r'):
        raise ValueError('Installation prefix cannot contain newlines')
    project = Path(__file__).resolve().parents[1]
    appdir = prefix / 'share/aag-book2pdf'
    marker = appdir / '.managed'
    cli = prefix / 'bin/book2pdf'
    gui = prefix / 'bin/aag-book2pdf'
    desktop = prefix / 'share/applications/aag-book2pdf.desktop'
    icon = prefix / 'share/icons/hicolor/scalable/apps/aag-book2pdf.svg'
    mime = prefix / 'share/mime/packages/aag-book2pdf.xml'
    nautilus = prefix / 'share/nautilus/scripts/המרה ל-PDF'
    if appdir.exists() and (not marker.is_file() or marker.read_text() != MARKER):
        raise ValueError('Refusing to modify an installation without an ownership marker')
    if args.uninstall:
        if not marker.is_file():
            print('No managed installation found.')
            return
        for path in (cli, gui, desktop, nautilus, mime):
            if path.is_file() and MARKER in path.read_text():
                path.unlink()
        if icon.is_file() and MARKER in icon.read_text():
            icon.unlink()
        if shutil.which('xdg-mime') and prefix == Path.home()/'.local':
            current = subprocess.run(['xdg-mime','query','default','application/x-aag-book'],capture_output=True,text=True).stdout.strip()
            old = appdir/'previous-mime-default.txt'
            if current == 'aag-book2pdf.desktop' and old.is_file() and old.read_text().strip():
                subprocess.run(['xdg-mime','default',old.read_text().strip(),'application/x-aag-book'],check=False)
            for preferences in (Path.home()/'.config/mimeapps.list',prefix/'share/applications/mimeapps.list'):
                if preferences.is_file():
                    original=preferences.read_text()
                    lines=[]
                    for line in original.splitlines(keepends=True):
                        if line.startswith('application/x-aag-book='):
                            values=[v for v in line.split('=',1)[1].strip().split(';') if v and v!='aag-book2pdf.desktop']
                            if values:
                                lines.append('application/x-aag-book='+';'.join(values)+';\n')
                        else:
                            lines.append(line)
                    updated=''.join(lines)
                    if updated!=original:
                        preferences.write_text(updated)
        shutil.rmtree(appdir)
        if shutil.which('update-mime-database'):
            subprocess.run(['update-mime-database',str(prefix/'share/mime')],check=False)
        if shutil.which('update-desktop-database'):
            subprocess.run(['update-desktop-database',str(prefix/'share/applications')],check=False)
        print('Uninstalled AAG Book2PDF. PDFs, source books, reports, state, and preferences were retained.')
        return
    for path in (cli, gui, desktop):
        if path.exists() and MARKER not in path.read_text():
            raise ValueError(f'Refusing to overwrite an unrelated file: {path}')
    if args.wheel and not (appdir/'venv/pyvenv.cfg').is_file():
        raise ValueError('--wheel requires an existing managed environment')
    appdir.mkdir(parents=True, exist_ok=True)
    marker.write_text(MARKER)
    venv = appdir / 'venv'
    subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
    python = venv / 'bin/python'
    if args.wheel:
        if not (venv/'pyvenv.cfg').is_file() or not args.wheel.is_file():
            raise ValueError('--wheel requires an existing environment and a local wheel')
        subprocess.run([str(python), '-m', 'pip', 'install', '--no-deps', '--force-reinstall', str(args.wheel.resolve())], check=True)
    else:
        subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '--timeout', '60', str(project)], check=True)
    if args.mdb_bin:
        native=args.mdb_bin.resolve()
        for name in ('mdb-tables','mdb-export'):
            if not (native/name).is_file():raise ValueError('Missing MDB Tools executable: '+name)
        destination=venv/'native/mdbtools'
        (destination/'bin').mkdir(parents=True,exist_ok=True)
        for name in ('mdb-tables','mdb-export'):
            shutil.copy2(native/name,destination/'bin'/name)
        libraries=native.parent/'lib/x86_64-linux-gnu'
        if libraries.is_dir():
            shutil.copytree(libraries,destination/'lib/x86_64-linux-gnu',dirs_exist_ok=True)
        notices=native.parent/'share/doc'
        if notices.is_dir():shutil.copytree(notices,destination/'share/doc',dirs_exist_ok=True)
    # Verify native dependencies before advertising the launcher.
    subprocess.run([str(python), '-c', 'import pikepdf, pymupdf, cryptography, PIL; from PySide6.QtWidgets import QApplication'], check=True)
    runtime_check = subprocess.run([str(python), '-c', 'from book2pdf.bkf.native import runtime_version; print(runtime_version())'])
    if runtime_check.returncode:
        if args.require_bkf:raise ValueError('BKF requires DjVuLibre; install the libdjvulibre21 runtime')
        print('BKF runtime unavailable. BKC/PDF work; install libdjvulibre21 to enable BKF.')
    for path, module in ((cli, 'book2pdf.cli'), (gui, 'book2pdf.gui.app')):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'#!/bin/sh\n# {MARKER}\nexec {shlex.quote(str(python))} -m {module} "$@"\n')
        path.chmod(0o755)
    icon.parent.mkdir(parents=True, exist_ok=True)
    if icon.exists() and MARKER not in icon.read_text():
        raise ValueError('Refusing to overwrite an unrelated icon')
    icon.write_text((project / 'assets/aag-book2pdf.svg').read_text().replace('<svg ', f'<!-- {MARKER} -->\n<svg ', 1))
    desktop.parent.mkdir(parents=True, exist_ok=True)
    escaped = str(gui).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%')
    desktop.write_text(f'''[Desktop Entry]
# {MARKER}
Type=Application
Name=AAG Book2PDF
Name[he]=AAG Book2PDF
Comment=Convert scanned books to PDF
Comment[he]=המרת ספרים סרוקים ל־PDF
Exec="{escaped}" %F
Icon=aag-book2pdf
Terminal=false
Categories=Office;
StartupNotify=true
StartupWMClass=AAG Book2PDF
MimeType=application/x-aag-book;application/pdf;
''')
    mime.parent.mkdir(parents=True,exist_ok=True)
    if mime.exists() and MARKER not in mime.read_text():
        raise ValueError('Refusing to overwrite unrelated MIME registration')
    shutil.copyfile(project/'assets/aag-book2pdf.xml',mime)
    if shutil.which('update-mime-database'):
        subprocess.run(['update-mime-database',str(prefix/'share/mime')],check=True)
    if args.make_default:
        if prefix != Path.home()/'.local' or not shutil.which('xdg-mime'):
            raise ValueError('--make-default requires xdg-mime and the standard user-local prefix')
        old = appdir/'previous-mime-default.txt'
        if not old.exists():
            previous = subprocess.run(['xdg-mime','query','default','application/x-aag-book'],capture_output=True,text=True,check=True).stdout.strip()
            old.write_text(previous if previous != 'aag-book2pdf.desktop' else '')
        subprocess.run(['xdg-mime','default','aag-book2pdf.desktop','application/x-aag-book'],check=True)
    if args.nautilus:
        nautilus.parent.mkdir(parents=True, exist_ok=True)
        if nautilus.exists() and MARKER not in nautilus.read_text():
            raise ValueError('Refusing to overwrite an unrelated Nautilus script')
        nautilus.write_text(f'''#!/usr/bin/env python3
# {MARKER}
import os, subprocess
paths = os.environ.get('NAUTILUS_SCRIPT_SELECTED_FILE_PATHS', '').splitlines()
subprocess.Popen([{str(gui)!r}, *paths], start_new_session=True)
''')
        nautilus.chmod(0o755)
    updater = shutil.which('update-desktop-database')
    if updater:
        subprocess.run([updater, str(desktop.parent)], check=False)
    print(f'Installed. Open AAG Book2PDF from the application menu. CLI: {cli}')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Installation error / שגיאת התקנה: {exc}', file=sys.stderr)
        sys.exit(1)
