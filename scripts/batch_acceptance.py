"""Conservative 1,000-file scaling/resume acceptance using small synthetic books."""
import json
from pathlib import Path
import sys
import time
import pikepdf
from book2pdf.batch import run_batch
from book2pdf.models import Options


def main():
    base = Path(sys.argv[1]).resolve()
    source = base / 'ספרים'
    output = base / 'PDF'
    source.mkdir(parents=True, exist_ok=True)
    seed = base / 'fixture.pdf'
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(72,72))
    page.Contents = pikepdf.Stream(pdf, b'0 0 1 rg 4 4 50 50 re f\n')
    pdf.save(seed)
    data = b'Synthetic wrapper for batch acceptance\x00' + seed.read_bytes()
    for i in range(1000):
        folder = source / f'{i // 100:02}'
        folder.mkdir(exist_ok=True)
        (folder / f'ספר ({i:04}).book').write_bytes(data)
    start = time.monotonic()
    def event(data):
        if data['type'] == 'result' and data['completed'] % 100 == 0:
            print('Completed',data['completed'],flush=True)
    first = run_batch([source], output, Options(jobs=2, recursive=True, thermal_pause=True), on_event=event)
    seconds = time.monotonic() - start
    resumed = run_batch([source], output, Options(jobs=2, recursive=True, thermal_pause=True), on_event=event)
    assert first['PASS'] == first['TOTAL'] == 1000, first
    assert resumed['SKIPPED'] == 1000, resumed
    evidence = {'first':first, 'resumed':resumed, 'first_run_seconds': seconds, 'workers':2,
                'fixture':'synthetic; not 1,000 real proprietary variants'}
    (base / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    print(json.dumps(evidence, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
