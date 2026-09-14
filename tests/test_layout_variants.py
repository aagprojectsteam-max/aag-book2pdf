"""Synthetic layout families, with no private source bytes in fixtures."""
import random

import pikepdf
import pytest

from book2pdf.detector import detect
from book2pdf.models import Options, Unsupported
from book2pdf.worker import convert


def early_state_book(wrapper_size):
    """BKC-prefix/headerless PDF with two overwritten leading ExtGStates.

    Object counts, sizes, image and wrapper differ from the real sample. Only
    the discovered structural relationships are represented here.
    """
    image = bytes([40, 100, 180] * 16)
    content = b'q /GS1 gs /GS2 gs 100 0 0 100 22 22 cm /Im Do Q\n'
    page = b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 144 144] /Resources << /ExtGState << /GS1 4 0 R /GS2 5 0 R >> /XObject << /Im 7 0 R >> >> /Contents 6 0 R >>'
    objects = {1: b'<< /Type /Catalog /Pages 2 0 R >>',
               2: b'<< /Type /Pages /Kids [3 0 R 8 0 R 9 0 R] /Count 3 >>', 3: page, 8: page, 9: page,
               4: b'<< /Type /ExtGState /ca 1 /CA 1 >>',
               5: b'<< /Type /ExtGState /ca 1 /op true /OP true >>',
               6: b'<< /Length '+str(len(content)).encode()+b' >>\nstream\n'+content+b'endstream',
               7: b'<< /Type /XObject /Subtype /Image /Width 4 /Height 4 /BitsPerComponent 8 /ColorSpace /DeviceRGB /Length 48 >>\nstream\n'+image+b'\nendstream'}
    objects.update({i: str(i).encode() for i in range(10,55)})
    pdf = bytearray(b'%PDF-1.4\n%abcd\n')
    positions = {}
    order = [4,5,7,6,3,8,9,2,1] + list(range(10,55))
    for number in order:
        positions[number] = len(pdf)
        obj = f'{number} 0 obj\n'.encode()+objects[number]+b'\nendobj\n'
        pdf.extend(obj.ljust(512,b' ') if number in (4,5) else obj)
    xref = len(pdf)
    pdf.extend(b'xref\n0 55\n0000000000 65535 f \n')
    for i in range(1,55):
        pdf.extend(f'{positions[i]:010d} 00000 n \n'.encode())
    pdf.extend(f'trailer\n<< /Size 55 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    pdf[:positions[4]] = b'?'*positions[4]
    for number in (4,5):
        pdf[positions[number]:positions[number]+512] = b'!'*512
    prefix = b'BKC' + random.Random(wrapper_size).randbytes(wrapper_size-3)
    return prefix + pdf, image


@pytest.mark.parametrize('offset', [113, 937])
def test_headerless_early_states_layout(tmp_path, offset):
    raw, image = early_state_book(offset)
    source = tmp_path / 'arbitrary-name.book'
    source.write_bytes(raw)
    result = convert(source, tmp_path/'out.pdf', Options(), tmp_path/'state.sqlite3')
    assert result.status == 'PASS_REPAIRED', result.error
    assert result.page_count == 3
    assert result.detected_wrapper_offset == offset
    assert result.preservation['all_intact_objects_byte_identical']
    assert result.preservation['original_streams_preserved'] == 2
    with pikepdf.open(tmp_path/'out.pdf') as pdf:
        assert pdf.get_object((7,0)).read_raw_bytes() == image
    assert source.read_bytes() == raw
    strict = convert(source,tmp_path/'strict.pdf',Options(recovery='exact'),tmp_path/'state.sqlite3')
    assert strict.status == 'PREVIEW_AVAILABLE_WITH_ASSUMPTIONS'


@pytest.mark.parametrize('signature', [b'BKF', b'BKC', b'XYZ'])
def test_opaque_layout_evidence_without_false_recovery(tmp_path, signature):
    raw = signature + random.Random(7183).randbytes(200000)
    source = tmp_path / 'unrelated-filename.book'
    source.write_bytes(raw)
    result = convert(source, tmp_path/'out.pdf', Options(debug=False),tmp_path/'state.sqlite3')
    assert result.status == {b'BKF':'DECODER_REQUIRED',b'BKC':'NEW_VARIANT_DETECTED',b'XYZ':'UNSUPPORTED_AFTER_ANALYSIS'}[signature]
    assert result.page_count == 0 and not (tmp_path/'out.pdf').exists()
    layout = result.forensic['layout']
    assert layout['classification'] == 'OPAQUE_HIGH_ENTROPY_NO_PDF_STRUCTURE'
    assert layout['encryption'] == 'NOT_DETERMINED'
    assert layout['codec'] == 'UNKNOWN'
    assert 'no verified decoder' in result.error
    assert source.read_bytes() == raw


def test_bkf_prefix_does_not_override_positive_structure(tmp_path, pdf_bytes):
    source = tmp_path / '58.book'
    source.write_bytes(b'BKF' + b'wrapping-data'*23 + pdf_bytes)
    result = convert(source,tmp_path/'out.pdf',Options(),tmp_path/'state.sqlite3')
    assert result.status == 'PASS_EXACT', result.error
    assert result.page_count == 3


def test_partial_pdf_does_not_claim_opaque_or_encrypted(tmp_path, pdf_bytes):
    source = tmp_path / 'truncated.book'
    source.write_bytes(b'BKF' + pdf_bytes[:-40])
    with pytest.raises(Unsupported) as failure:
        detect(source)
    assert failure.value.forensic['layout']['classification'] == 'PDF_STRUCTURE_VISIBLE'
    assert 'truncation is only one possible cause' in str(failure.value)


def test_opaque_viewer_friendly_rejection_and_cleanup(qtbot, tmp_path):
    from book2pdf.gui.viewer_window import ViewerWindow
    raw = b'BKF' + random.Random(7183).randbytes(200000)
    source = tmp_path / 'opaque.book'
    source.write_bytes(raw)
    window = ViewerWindow(source)
    qtbot.addWidget(window)
    window.show()
    worker = window.worker
    qtbot.waitUntil(lambda: window.worker is None, timeout=30000)
    assert window.last_error.startswith('Opaque container (BKF)')
    assert 'לא ניתן' in window.banner.text()
    assert window.page_count == 0
    assert not window.save_button.isEnabled()
    assert worker.cache_path and not worker.cache_path.exists()
    assert source.read_bytes() == raw
    window.close()


def test_mixed_layout_batch_and_resume(tmp_path):
    from book2pdf.batch import run_batch
    import json
    source = tmp_path/'input'
    source.mkdir()
    (source/'supported.book').write_bytes(early_state_book(431)[0])
    (source/'opaque.book').write_bytes(b'BKF'+random.Random(7183).randbytes(200000))
    output = tmp_path/'output'
    summary = run_batch([source],output,Options(jobs=2))
    assert summary['PASS_REPAIRED'] == 1 and summary['UNSUPPORTED'] == 1
    with open(summary['REPORT'], encoding='utf-8') as report:
        rows = [json.loads(line) for line in report]
    failure = next(row for row in rows if row.get('status') == 'DECODER_REQUIRED')
    assert failure['forensic']['layout']['classification'] == 'OPAQUE_HIGH_ENTROPY_NO_PDF_STRUCTURE'
    assert not (output/'opaque.pdf').exists()
    resume = run_batch([source],output,Options(jobs=2))
    assert resume['SKIPPED'] == 1 and resume['UNSUPPORTED'] == 1
