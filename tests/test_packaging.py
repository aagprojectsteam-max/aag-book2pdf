from pathlib import Path
import zipfile
import json
import hashlib
import pytest

from scripts.windows_assets import audit_bundle, source_zip


def test_build_kit_excludes_private_documents(tmp_path):
    target = tmp_path / 'kit.zip'
    source_zip(target)
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        assert 'book2pdf/worker.py' in names
        assert 'tests/fixtures/bkf-noise.djvu' in names
        assert 'packaging/windows/djvu-runtime-lock.json' in names
        assert all(info.date_time==(1980,1,1,0,0,0) for info in archive.infolist())
        assert sum(name.endswith('.djvu') for name in names)==1
        assert 'packaging/windows/installer.iss' in names
        assert 'scripts/build_windows.ps1' in names
        assert 'scripts/test_windows_python.ps1' in names
        manifest=json.loads(archive.read('BUILDKIT-MANIFEST.json'))
        assert len(manifest['source_commit'])==40
        assert isinstance(manifest['tracked_worktree_clean'],bool)
        assert all(hashlib.sha256(archive.read(name)).hexdigest()==digest for name,digest in manifest['files'].items())
        assert not any(Path(name).suffix.lower() in {'.pdf', '.book', '.sqlite3'} for name in names)
        assert not any('633328' in name or name.startswith(('artifacts/', '.venv/', '.build/')) for name in names)


@pytest.mark.parametrize('name', ['private.book', '633328-reconstructed.pdf', 'journal.sqlite3', 'forensic.jsonl'])
def test_release_rejects_document_data(tmp_path, name):
    (tmp_path / name).write_bytes(b'private')
    with pytest.raises(ValueError, match='Private/document'):
        audit_bundle(tmp_path)


def test_windows_bkf_fixture_runs_unchanged_decoder(tmp_path):
    from scripts.windows_assets import bkf_fixture
    from book2pdf.bkf.format import parse
    source=bkf_fixture(tmp_path/'synthetic.book')
    book=parse(source.read_bytes())
    assert len(book.pages)==2
    assert all(entry.decoded==Path('tests/fixtures/bkf-noise.djvu').read_bytes() for entry in book.pages)
