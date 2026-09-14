# AAG Book2PDF

A Hebrew/RTL desktop reader and conservative recovery tool for scanned books, built with shared cross-platform Python and PySide6 code.

Open supported `.book` files directly, read them, and save full or selected pages as PDF. The interface is RTL; scanned pages are never mirrored.

## Supported sources

- **BKC:** recognized BOOK envelopes containing PDF data, with original content preservation and evidence-based structural repair where safe.
- **BKF/DjVu:** recognized BOOK envelopes and directory layouts containing DjVu pages. Native DjVuLibre is required.
- **TurboSun:** supported catalogue-indexed page folders, including their catalogue and image containers. Requires MDB Tools. An isolated encoded `.tif` is not enough.
- **PDF:** reading and direct page extraction.

This is not a universal decoder for every BOOK file or encryption scheme. Decode happens before salvage. Unknown variants receive bounded analysis and truthful diagnostic/recovery states. Previews and repaired documents retain their provenance; they are not presented as exact originals.

## Reader and export

Single-page and continuous-scroll modes share the same document/page model. Continuous mode renders visible pages and nearby prefetch pages lazily, releases distant rasters, tracks the current page, and preserves the reading position during zoom.

Features include page-number entry, first/last/next/previous navigation, zoom, Fit Page, Fit Width, 100%, native touch pinch and double tap, and kinetic touch scrolling. Single-page horizontal swipes follow the Hebrew UX; continuous vertical gestures scroll rather than flip pages.

Use **שמור כ-PDF**, **שמור עמודים כ-PDF**, or **הדפס**. Export accepts ranges such as `1-5,8,10-15`, sorts them ascending, and removes duplicates. PDF pages are copied directly where possible; TurboSun exports retain verified original CCITT streams. No OCR is performed. Output uses temporary files, validation and atomic publication, with overwrite protection. Batch processing supports resumability and cancellation.

## Linux quick start

Python 3.12 is recommended. From an extracted source archive or checkout:

```sh
sudo apt-get update
sudo apt-get install python3-venv qpdf libdjvulibre21 mdbtools libegl1 libopengl0 libxkbcommon0 libxcb-cursor0
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m book2pdf.gui.app
```

To open a file directly:

```sh
.venv/bin/python -m book2pdf.gui.app /path/to/input.book
.venv/bin/book2pdf input.book -o output.pdf
.venv/bin/book2pdf input.book --pages "1-5,8" -o selected.pdf
```

For a managed user-local installation with desktop/file association support, run `./install.sh --make-default`. It installs into your user-local application directory. Remove it with `./uninstall.sh`. These commands do not remove source books or exported documents. To open TurboSun, choose **פתח תיקיית TurboSun** and select the source folder. Trusted MDB binaries may also be supplied through `AAG_MDB_BIN`.

## Windows

Run the same application with **Python 3.12 x64**. Follow [WINDOWS.md](WINDOWS.md) for virtual environment, native dependency discovery and launch commands. EXE packaging is not required. Native Windows acceptance of this exact release remains **NOT_PROVEN**; a BuildKit is preparation, not proof.

## Validation and limitations

Linux production acceptance covered BKC, BKF and TurboSun opening, lazy viewing, full/partial export, background validation and Qt PDF printing. Real documents and forensic reports are intentionally excluded from this repository. Physical printer output and physical TurboSun touch acceptance remain **NOT_PROVEN**. See [release provenance](RELEASE_PROVENANCE.md) for the exact accepted source relationship and new public test results.

```sh
.venv/bin/python -m pip install '.[test]'
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```

Tests generate synthetic documents; the only binary fixture is documented synthetic DjVu noise. Optional private-sample tests skip when sample environment variables are absent.

See [architecture](ARCHITECTURE.md), [contributing](CONTRIBUTING.md), [security](SECURITY.md), [license](LICENSE) and [third-party terms](THIRD_PARTY.md). This release contains application source, not bundled third-party runtime binaries or book content.

## License

AAG Book2PDF is licensed under **AGPL-3.0-or-later** (GNU Affero General Public License version 3 or, at your option, any later version). See [LICENSE](LICENSE). Dependencies retain their separate licenses and notices in [THIRD_PARTY.md](THIRD_PARTY.md).
