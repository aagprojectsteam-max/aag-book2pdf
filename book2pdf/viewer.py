"""Process-safe viewer rendering and private temporary-file lifecycle."""
from pathlib import Path
import tempfile
from .platforms import FileLock, cache_root, owns_cache, remove_locked_cache


MARKER = 'AAG Book2PDF private viewing cache v1'


class ViewingCache:
    def __init__(self):
        self.path = Path(tempfile.mkdtemp(prefix='aag-book2pdf-view-', dir=cache_root()))
        self.lock = FileLock(self.path / '.active')
        (self.path / '.managed').write_text(MARKER)

    def close(self):
        # Keep the lock until files disappear, so another viewer cannot clean a live cache.
        remove_locked_cache(self.path, self.lock)

    @staticmethod
    def cleanup_orphans():
        for path in cache_root().glob('aag-book2pdf-view-*'):
            try:
                if not owns_cache(path):
                    continue
                if (path / '.managed').read_text() != MARKER:
                    continue
                with FileLock(path / '.active', blocking=False) as lock:
                    remove_locked_cache(path, lock)
            except (OSError, UnicodeError):
                continue


def document_layout(path, model):
    """Read lightweight geometry only; never rasterize a page during layout."""
    import pymupdf
    with pymupdf.open(path) as pdf:
        if len(model.get('pages',[]))!=pdf.page_count:
            raise ValueError('RecoveredDocument/page layout count mismatch')
        for index,page in enumerate(pdf):
            model['pages'][index].update(width=page.rect.width,height=page.rect.height)
    return model


def render_page(path, page_index, zoom, device_ratio, max_dimension=4096):
    """Runs in the viewer's single child process, never on the Qt event thread."""
    import pymupdf
    with pymupdf.open(path) as pdf:
        page = pdf[page_index]
        size = page.rect
        if max(size.width, size.height) <= 0:
            raise ValueError('Invalid page size')
        scale = min(zoom * device_ratio, max_dimension / max(size.width, size.height))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False, colorspace=pymupdf.csRGB)
        return {'page': page_index, 'zoom': zoom, 'width': pix.width, 'height': pix.height,
                'stride': pix.stride, 'pixels': pix.samples, 'ratio': device_ratio,
                'page_width': size.width, 'page_height': size.height}
