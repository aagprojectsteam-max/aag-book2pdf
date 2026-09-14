"""Opt-in acceptance for installed/frozen builds; never runs on normal opening.

Synthetic evidence is safe for CI. --sample must be supplied explicitly and its
private outputs stay in the chosen evidence directory, outside release bundles.
"""
import argparse
import json
import platform
from pathlib import Path
import sys
import time


def synthetic_book(path):
    import io
    import pikepdf
    with pikepdf.Pdf.new() as pdf:
        for i in range(3):
            page = pdf.add_blank_page(page_size=(144, 144))
            scan = pikepdf.Stream(pdf, bytes([25 + i * 50, 100, 220] * 16))
            scan.Type = pikepdf.Name.XObject
            scan.Subtype = pikepdf.Name.Image
            scan.Width = scan.Height = 4
            scan.ColorSpace = pikepdf.Name.DeviceRGB
            scan.BitsPerComponent = 8
            page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im=scan))
            page.Contents = pikepdf.Stream(pdf, b'q 100 0 0 100 22 22 cm /Im Do Q\n')
        buffer = io.BytesIO()
        pdf.save(buffer, object_stream_mode=pikepdf.ObjectStreamMode.disable)
    path.write_bytes(b'AAG synthetic acceptance\x00' + buffer.getvalue())


def check_core(source, base, evidence):
    from .batch import run_batch
    from .models import Options
    from .export import convert_selected
    from .validator import validate
    from .state import sha256
    before = sha256(source)
    output = base / 'המרה עם רווח'
    options = Options(jobs=2, validate_all_pages=True)
    summary = run_batch([source], output, options)
    assert summary['PASS'] == 1, summary
    resume = run_batch([source], output, options)
    assert resume['SKIPPED'] == 1, resume
    pdf = output / source.with_suffix('.pdf').name
    from .state import pdf_provenance
    record=pdf_provenance(pdf,sha256(pdf)) or {}
    count, warning = validate(pdf, True, **({'font_policy':'report-fonts'} if record.get('recovery_class')=='DECODED_CONTAINER_CONTENT' else {}))
    selected = convert_selected(source, base / 'עמודים נבחרים.pdf', f'1,{count}', options)
    assert selected.status in ('PASS_EXACT', 'PASS_REPAIRED', 'PASS_DECODED'), selected.error
    assert selected.page_count == (1 if count == 1 else 2)
    assert selected.preservation['page_streams_byte_identical']
    assert sha256(source) == before
    evidence.update(batch='PASS', resume='PASS', multiprocessing='PASS', page_count=count,
                    partial_export='PASS', source_unchanged='PASS', validator_warning=warning,
                    recovery_status=selected.status)


def check_gui(source, base, evidence):
    from PySide6.QtCore import Qt, QSettings
    from PySide6.QtWidgets import QApplication
    from PySide6.QtPrintSupport import QPrinter
    from .gui.viewer_window import ViewerWindow
    from .platforms import resource_path
    from .state import sha256
    from .validator import validate
    app = QApplication.instance() or QApplication(['AAG Book2PDF acceptance'])
    app.setQuitOnLastWindowClosed(False)
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    evidence['qt_platform'] = app.platformName()
    assert resource_path('aag-book2pdf.svg').is_file()
    before = sha256(source)
    window = ViewerWindow(source, settings=QSettings(str(base / 'viewer-settings.ini'), QSettings.Format.IniFormat),
                          view_mode='SINGLE_PAGE')
    window.show()
    cache = None

    def wait_for(condition, timeout=240):
        deadline = time.monotonic() + timeout
        while not condition():
            app.processEvents()
            if window.last_error:
                raise AssertionError(window.last_error)
            if time.monotonic() > deadline:
                raise TimeoutError('Viewer acceptance timed out')
            time.sleep(.01)

    try:
        wait_for(lambda: window.last_displayed == 0)
        evidence['interactive_status'] = window.open_result['status']
        evidence['page_count'] = window.page_count
        cache = window.worker.cache_path
        for name, index in [('first', 0), ('middle', window.page_count // 2), ('last', window.page_count - 1)]:
            window.go_to(index)
            wait_for(lambda: window.last_displayed == index)
            assert not window.area.page_image.pixmap().isNull()
            evidence[name + '_page_render'] = 'PASS'
        window.actual_button.click()
        window.zoom_in_button.click()
        assert window.zoom > 1
        wait_for(lambda: window.last_displayed == window.page_index)
        window.fit_page_button.click()
        wait_for(lambda: window.last_displayed == window.page_index)
        assert window.fit_mode == 'page'
        assert window.area.page_image.width() <= window.area.viewport().width()+2
        active_page = window.page_index
        window.set_view_mode('CONTINUOUS_SCROLL')
        for index in (0, window.page_count // 2, window.page_count - 1):
            window.go_to(index)
            wait_for(lambda: window.last_displayed == index)
            assert index in window.continuous_area.cache
            assert set(window.continuous_area.cache) <= set(window.continuous_area.wanted_pages())
        window.go_to(active_page)
        window.set_view_mode('SINGLE_PAGE')
        wait_for(lambda: window.last_displayed == active_page)
        assert not window.continuous_area.cache
        evidence['continuous_scroll'] = 'PASS'
        assert window.area.page_image.height() <= window.area.viewport().height()+2
        window.fit_width_button.click()
        wait_for(lambda: window.last_displayed == window.page_index)
        assert window.fit_mode == 'width'
        assert window.area.page_image.width() <= window.area.viewport().width()+2
        window.save_to(base / 'viewer-saved.pdf')
        wait_for(lambda: window.save_result is not None)
        wait_for(lambda: window.validation_result is not None)
        evidence['recovery_status'] = window.open_result['status']
        assert window.save_result['status'] == window.open_result['status'], window.save_result
        window.export_to(base / 'viewer-selected.pdf', [1, window.page_count])
        wait_for(lambda: window.export_result is not None)
        assert window.export_result['preservation']['page_streams_byte_identical']
        from PySide6.QtCore import QTimer
        from PySide6.QtPrintSupport import QPrintDialog
        dialog_printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dialog = QPrintDialog(dialog_printer, window)
        QTimer.singleShot(200, dialog.reject)
        dialog.exec()
        evidence['native_print_dialog'] = 'PASS'
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(base / 'qt-print.pdf'))
        window.start_print(printer, [window.page_count])
        wait_for(lambda: window.print_worker is None)
        assert window.print_result and window.print_result['pages'] == [window.page_count]
        assert validate(base / 'qt-print.pdf', True)[0] == 1
        assert sha256(source) == before
        evidence.update(gui='PASS', save_as_pdf='PASS', viewer_partial_export='PASS',
                        navigation='PASS', zoom='PASS', fit_page='PASS', fit_width='PASS',
                        qt_pdf_printing='PASS', microsoft_print_to_pdf='NOT_PROVEN',
                        physical_printing='NOT_PROVEN', source_unchanged='PASS')
    finally:
        window.last_error = ''  # Allow cleanup even after an assertion/error.
        window.close()
        wait_for(lambda: window.worker is None and window.print_worker is None)
    assert cache is not None and not cache.exists()
    evidence['temp_cleanup'] = 'PASS'


def main(argv=None, *, gui=False):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence', type=Path, help='New empty evidence directory; may contain private book outputs')
    parser.add_argument('--sample', type=Path, help='Optional read-only local sample; never bundled in releases')
    parser.add_argument('--gui', action='store_true', help='Exercise the shared Qt viewer using this Python interpreter')
    parser.add_argument('--expected-sha256', help='Independently recorded original source SHA256')
    parser.add_argument('--expected-pages', type=int, help='Independently known page count')
    parser.add_argument('--expected-status', choices=['PASS_EXACT','PASS_REPAIRED','PASS_DECODED'])
    args = parser.parse_args(argv)
    base = args.evidence.resolve()
    base.mkdir(parents=True, exist_ok=True)
    if any(base.iterdir()):
        parser.error('Evidence directory must be empty; existing files will not be overwritten')
    import struct
    from . import __version__
    evidence = dict(status='FAIL', os=sys.platform, platform=platform.platform(),
                    python=platform.python_version(), python_bits=struct.calcsize('P')*8, engine_version=__version__,
                    frozen=bool(getattr(sys, 'frozen', False)), supplied_sample=bool(args.sample))
    try:
        if args.sample:
            source = args.sample.resolve(strict=True)
            from .state import sha256
            if args.expected_sha256:
                assert sha256(source) == args.expected_sha256, 'Source differs from expected SHA256'
        else:
            source = base / 'ספר לדוגמה עם רווח.book'
            synthetic_book(source)
        check_core(source, base, evidence)
        if gui or args.gui:
            check_gui(source, base, evidence)
        if args.expected_pages is not None:
            assert evidence['page_count'] == args.expected_pages
        if args.expected_status:
            assert evidence['recovery_status'] == args.expected_status
        evidence['status'] = 'PASS'
    except Exception as exc:
        evidence['error'] = repr(exc)
    (base / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(evidence, ensure_ascii=False), flush=True)
    return 0 if evidence['status'] == 'PASS' else 1


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
