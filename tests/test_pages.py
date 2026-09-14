from pathlib import Path
import pytest
import pikepdf
import pymupdf

from book2pdf.page_ranges import parse_pages, PageRangeError
from book2pdf.export import export_pages, convert_selected
from book2pdf.models import Options
from book2pdf.state import sha256
from book2pdf.validator import ValidationError


@pytest.mark.parametrize('text,expected', [('1',[1]),('1-3',[1,2,3]),('1,3',[1,3]),
    (' 1 - 2 , 3 ',[1,2,3]),('1,1,3',[1,3]),('1-2,2-3',[1,2,3]),('3,1',[1,3])])
def test_range_normalization(text,expected):
    assert parse_pages(text,3) == expected


@pytest.mark.parametrize('text', ['0','-3','3-2','abc','1,,3','4','1-4','','1,','1.0','+1','1-2-3'])
def test_invalid_range(text):
    with pytest.raises(PageRangeError):
        parse_pages(text,3)


@pytest.mark.parametrize('selection,count', [('2',1),('1-3',3),('1,3',2)])
def test_export_preserves_selected_pages(tmp_path,pdf_bytes,selection,count):
    source = tmp_path/'source.pdf'
    source.write_bytes(pdf_bytes)
    before = sha256(source)
    target = tmp_path/'selected.pdf'
    result = export_pages(source,target,selection)
    assert result.status == 'PASS_EXACT', result.error
    assert result.page_count == count
    assert result.preservation['page_streams_byte_identical']
    assert sha256(source) == before
    with pymupdf.open(source) as original,pymupdf.open(target) as selected:
        for i,source_page in enumerate(parse_pages(selection,3)):
            assert original[source_page-1].get_pixmap().samples == selected[i].get_pixmap().samples


def test_export_atomic_overwrite_and_failure(tmp_path,pdf_bytes,monkeypatch):
    source=tmp_path/'source.pdf'; source.write_bytes(pdf_bytes)
    target=tmp_path/'selected.pdf'; target.write_bytes(b'precious')
    assert export_pages(source,target,'1').status == 'FAIL_REPAIR'
    assert target.read_bytes() == b'precious'
    def fail(*args):
        raise ValidationError('validation failure')
    monkeypatch.setattr('book2pdf.export.validate',fail)
    assert export_pages(source,target,'1',overwrite=True).status == 'FAIL_VALIDATION'
    assert target.read_bytes() == b'precious'
    assert not list(tmp_path.glob('*.tmp.pdf'))


def test_export_never_overwrites_source(tmp_path,pdf_bytes):
    source=tmp_path/'source.pdf'; source.write_bytes(pdf_bytes)
    assert export_pages(source,source,'1',overwrite=True).status == 'FAIL_REPAIR'
    assert source.read_bytes() == pdf_bytes


def test_book_cli_export_backend(book,tmp_path):
    result = convert_selected(book,tmp_path/'part.pdf','1,3',Options())
    assert result.status == 'PASS_EXACT',result.error
    assert result.page_count == 2
    with pytest.raises(PageRangeError):
        convert_selected(book,tmp_path/'bad.pdf','4',Options())
    assert not (tmp_path/'bad.pdf').exists()


def test_page_selection_dialog(qtbot):
    from book2pdf.gui.page_dialog import PageSelectionDialog
    dialog=PageSelectionDialog(187,25)
    qtbot.addWidget(dialog)
    dialog.current_button.setChecked(True); dialog.accept()
    assert dialog.pages == [25]
    dialog.range_button.setChecked(True); dialog.range_edit.setText('1-5,3-7'); dialog.accept()
    assert dialog.pages == list(range(1,8))
    invalid=PageSelectionDialog(187,25);qtbot.addWidget(invalid);invalid.show()
    invalid.range_button.setChecked(True);invalid.range_edit.setText('200');invalid.accept()
    assert invalid.isVisible() and 'מחוץ' in invalid.error_label.text()
