"""Explicit release inputs only: never collect the project directory wholesale."""
import argparse
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ('book2pdf', 'packaging', 'scripts', 'tests', 'assets', '.github')
SOURCE_FILES = ('packaging/windows/djvu-runtime-lock.json', 'pyproject.toml', 'README.md', 'WINDOWS.md', 'ARCHITECTURE.md', 'THIRD_PARTY.md', 'LICENSE', 'CHANGELOG.md', 'CONTRIBUTING.md', 'SECURITY.md', 'RELEASE_PROVENANCE.md', 'HANDOFF.md', 'install.sh', 'uninstall.sh', '.gitignore')
PUBLIC_FIXTURES = ('tests/fixtures/bkf-noise.djvu',)
ALLOWED_SUFFIXES = {'.py', '.ps1', '.spec', '.iss', '.manifest', '.svg', '.yml', '.yaml', '.md'}


def source_files(root=ROOT):
    for name in PUBLIC_FIXTURES:
        yield root / name
    for name in SOURCE_FILES:
        yield root / name
    for name in SOURCE_DIRS:
        for path in sorted((root / name).rglob('*')):
            if path.is_file() and path.suffix in ALLOWED_SUFFIXES and '__pycache__' not in path.parts:
                yield path


def source_zip(target):
    files = sorted(set(source_files()))
    digests = {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    try:
        commit = subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True,stderr=subprocess.DEVNULL).strip()
        tracked = set(subprocess.check_output(['git','-C',str(ROOT),'ls-files','-z'],text=True).split('\0'))
        clean = (set(digests) <= tracked and not subprocess.check_output(
            ['git','-C',str(ROOT),'status','--porcelain','--untracked-files=no'],text=True).strip())
    except (OSError,subprocess.CalledProcessError):
        prior = ROOT / 'BUILDKIT-MANIFEST.json'
        previous = json.loads(prior.read_text(encoding='utf-8')) if prior.exists() else {}
        commit = previous.get('source_commit','NOT_RECORDED')
        clean = previous.get('tracked_worktree_clean',False) and previous.get('files') == digests
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            info=zipfile.ZipInfo(path.relative_to(ROOT).as_posix(),date_time=(1980,1,1,0,0,0))
            info.create_system=3
            info.external_attr=(0o755 if path.suffix=='.sh' else 0o644)<<16
            info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,path.read_bytes())
        manifest=zipfile.ZipInfo('BUILDKIT-MANIFEST.json',date_time=(1980,1,1,0,0,0))
        manifest.create_system=3;manifest.external_attr=0o644<<16;manifest.compress_type=zipfile.ZIP_DEFLATED
        archive.writestr(manifest,json.dumps({'source_commit':commit,'tracked_worktree_clean':clean,'files':digests},indent=2)+'\n')


def bkf_fixture(destination):
    """Two synthetic known native scans; production parsing derives all extents."""
    import struct
    from book2pdf.bkf.format import transform
    plain = (ROOT / PUBLIC_FIXTURES[0]).read_bytes()
    raw = transform(plain[:200], encode=True) + plain[200:]
    entries = b''.join(name + b'\0' + struct.pack('<II', i*len(raw), len(raw))
                       for i,name in enumerate((b'synthetic-first', b'synthetic-last')))
    directory = struct.pack('<I', len(entries)) + b'synthetic!' + entries
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b'BKFtest' + transform(directory, encode=True) + raw + raw)
    return destination


def prepare():
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    from PIL import Image
    folder = ROOT / '.build'
    folder.mkdir(exist_ok=True)
    renderer = QSvgRenderer(str(ROOT / 'assets/aag-book2pdf.svg'))
    canvas = QImage(256, 256, QImage.Format.Format_ARGB32)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    renderer.render(painter)
    painter.end()
    png = folder / 'windows-icon.png'
    assert canvas.save(str(png))
    with Image.open(png) as image:
        image.save(folder / 'windows-icon.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    notices = folder / 'windows-notices'
    if notices.exists():
        shutil.rmtree(notices)
    notices.mkdir()
    shutil.copy2(ROOT / 'THIRD_PARTY.md', notices)
    inventory = []
    for distribution in metadata.distributions():
        name = distribution.metadata['Name']
        if name.lower() in {'pip', 'setuptools', 'wheel'}:
            continue
        inventory.append({'name': name, 'version': distribution.version,
                          'license': distribution.metadata.get('License-Expression') or distribution.metadata.get('License', '')})
        for file in distribution.files or []:
            if any(part.lower().startswith(('license', 'copying', 'notice')) for part in Path(str(file)).parts):
                source = Path(distribution.locate_file(file))
                if source.is_file() and source.suffix.lower() not in ('.py', '.pyc'):
                    relative = Path(*[part for part in Path(str(file)).parts if part not in ('..', '.')])
                    target = notices / name / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    (notices / 'BUILD-DEPENDENCIES.json').write_text(json.dumps(inventory, indent=2), encoding='utf-8')
    source_zip(notices / 'AAG-Book2PDF-source.zip')


def audit_bundle(bundle):
    forbidden = []
    for path in bundle.rglob('*'):
        if path.is_file() and (path.suffix.lower() in {'.book', '.pdf', '.sqlite3', '.jsonl'}
                               or '633328' in path.name or path.is_symlink()):
            forbidden.append(str(path))
    if forbidden:
        raise ValueError('Private/document files in release bundle: ' + repr(forbidden))


def release(bundle, destination):
    audit_bundle(bundle)
    archive = destination / 'AAG-Book2PDF-Portable.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
        for path in sorted(bundle.rglob('*')):
            if path.is_file():
                output.write(path, Path('AAG-Book2PDF') / path.relative_to(bundle))
    from book2pdf import __version__
    setup = destination / f'AAG-Book2PDF-Setup-{__version__}.exe'
    if not setup.is_file():
        raise FileNotFoundError('Installer must be built before release checksums')
    def digest(path):
        with path.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()
    (destination / 'SHA256SUMS').write_text(''.join(f'{digest(p)}  {p.name}\n' for p in (setup, archive)), encoding='ascii')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'release', 'build-kit', 'bkf-fixture'])
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--destination', type=Path)
    args = parser.parse_args()
    if args.action == 'bkf-fixture':
        bkf_fixture(args.destination)
    elif args.action == 'prepare':
        prepare()
    elif args.action == 'release':
        release(args.bundle, args.destination)
    else:
        args.destination.mkdir(parents=True, exist_ok=True)
        source_zip(args.destination / 'AAG-Book2PDF-Windows-BuildKit.zip')
