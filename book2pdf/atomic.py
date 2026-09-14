"""Validated export staging with the same publication guarantees as conversion."""
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
from .platforms import FileLock, output_key, publish


@contextmanager
def staged_output(target: Path, source: Path, overwrite=False):
    target = Path(target).absolute()
    if target.suffix.lower() != '.pdf':
        raise ValueError('Output must have a .pdf extension')
    if target.resolve() == source.resolve() or (target.exists() and os.path.samefile(target, source)):
        raise ValueError('Cannot overwrite the source document')
    target.parent.mkdir(parents=True, exist_ok=True)
    key = output_key(target)
    with FileLock(target.parent / f'.book2pdf-{key}.lock'):
        if target.is_symlink() or (target.exists() and not overwrite):
            raise FileExistsError('Existing PDF is protected; explicitly confirm overwrite')
        prefix = f'.book2pdf-{key}-'
        for stale in target.parent.glob(prefix + '*.tmp.pdf'):
            if stale.is_file() and not stale.is_symlink():
                stale.unlink()
        fd, name = tempfile.mkstemp(prefix=prefix, suffix='.tmp.pdf', dir=target.parent)
        os.close(fd)
        tmp = Path(name)
        try:
            yield tmp
            publish(tmp, target, overwrite)
        finally:
            tmp.unlink(missing_ok=True)
