"""A cancelled output must leave the shared reading session usable."""
import time
from pathlib import Path
from book2pdf.gui.viewer_window import ViewerWindow
from test_envelope import wrap
from test_viewer import close_viewer


def held_export(source,cache,*args):
    from book2pdf.supervised import checkpoint
    (Path(cache).parent/'output-started').touch()
    while True:checkpoint();time.sleep(.01)


def test_cancel_export_keeps_viewer_usable(qtbot,tmp_path,pdf_bytes,monkeypatch):
    from book2pdf import interactive
    monkeypatch.setattr(interactive,'export_selection',held_export)
    source=tmp_path/'source.book';source.write_bytes(wrap([(b'pdf',pdf_bytes)]))
    window=ViewerWindow(source,view_mode='SINGLE_PAGE');qtbot.addWidget(window);window.show()
    try:
        qtbot.waitUntil(lambda:window.last_displayed==0,timeout=10000)
        target=tmp_path/'selected.pdf';window.export_to(target,[1])
        qtbot.waitUntil(lambda:(window.worker.cache_path/'output-started').exists(),timeout=10000)
        window.worker.cancel_output()
        qtbot.waitUntil(lambda:window.export_result is not None,timeout=10000)
        assert window.export_result['status']=='CANCELLED'
        assert not target.exists() and not window.last_error
        window.go_to(1);qtbot.waitUntil(lambda:window.last_displayed==1,timeout=10000)
    finally:close_viewer(qtbot,window)
