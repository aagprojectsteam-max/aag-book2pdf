"""Runtime preparation must fail closed on corrupt/mismatched downloads."""
import hashlib
import io
import pytest
from scripts import prepare_windows_runtime as runtime


def test_runtime_archive_hash_and_cache_protection(tmp_path,monkeypatch):
    data=b'controlled package bytes'
    row={'url':'https://repo.msys2.org/mingw/test.tar.zst','file':'test.tar.zst','sha256':hashlib.sha256(data).hexdigest()}
    monkeypatch.setattr(runtime.urllib.request,'urlopen',lambda *a,**kw:io.BytesIO(data))
    target=runtime.download(row,tmp_path)
    assert target.read_bytes()==data
    target.write_bytes(b'changed')
    with pytest.raises(ValueError,match='Cached package SHA256 mismatch'):
        runtime.download(row,tmp_path)
    assert target.read_bytes()==b'changed'


def test_runtime_download_mismatch_leaves_no_published_or_temporary_file(tmp_path,monkeypatch):
    row={'url':'https://repo.msys2.org/mingw/test.tar.zst','file':'test.tar.zst','sha256':'0'*64}
    monkeypatch.setattr(runtime.urllib.request,'urlopen',lambda *a,**kw:io.BytesIO(b'wrong'))
    with pytest.raises(ValueError,match='Downloaded package SHA256 mismatch'):
        runtime.download(row,tmp_path)
    assert not list(tmp_path.iterdir())


def test_runtime_download_has_a_byte_limit(tmp_path,monkeypatch):
    row={'url':'https://repo.msys2.org/mingw/test.tar.zst','file':'test.tar.zst','sha256':'0'*64}
    monkeypatch.setattr(runtime,'LIMIT',2)
    monkeypatch.setattr(runtime.urllib.request,'urlopen',lambda *a,**kw:io.BytesIO(b'oversized'))
    with pytest.raises(ValueError,match='budget'):runtime.download(row,tmp_path)
    assert not list(tmp_path.iterdir())
