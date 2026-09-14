# Third-party dependency audit

Audit date: 2026-09-14. The public source archive and Windows BuildKit contain this application's source/scripts and a synthetic test image. They contain **no dependency wheels, native libraries, proprietary viewer executables, runtime archives or real book pages**. Dependencies obtained separately remain under their own licenses; the application license does not relicense them.

| Component | Upstream terms / source | Use in this project |
| --- | --- | --- |
| Python | [PSF](https://docs.python.org/3/license.html) | Interpreter, installed separately |
| Qt / PySide6 / shiboken6 | [LGPLv3/GPL/commercial; module-specific](https://doc.qt.io/qtforpython-6/licenses.html) | GUI and native print integration |
| PyMuPDF / MuPDF | [AGPL or commercial](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright) | PDF rendering and validation |
| pikepdf | [MPL-2.0](https://github.com/pikepdf/pikepdf/blob/main/LICENSE.txt) | PDF object copying/validation |
| qpdf | [Apache-2.0](https://github.com/qpdf/qpdf/blob/main/LICENSE.txt) | pikepdf runtime; optional structural checker |
| DjVuLibre | [GPL](https://djvu.sourceforge.net/) | Native BKF decoding; not bundled |
| MDB Tools | [GPL utilities / LGPL library; inspect exact distribution](https://github.com/mdbtools/mdbtools/blob/dev/COPYING) | Separate read-only catalogue commands; not bundled |
| Pillow | [HPND/PIL terms and bundled-library notices](https://github.com/python-pillow/Pillow/blob/main/LICENSE) | Image validation |
| cryptography | [Apache-2.0 or BSD; bundled notices also apply](https://github.com/pyca/cryptography) | AES decoding |
| Poppler | [GPL](https://poppler.freedesktop.org/) | Optional external validation tooling; not bundled |
| PyInstaller | [GPL with bootloader exception](https://pyinstaller.org/en/stable/license.html) | Optional future packaging, not required to run source |
| Inno Setup | [Upstream license](https://jrsoftware.org/files/is/license.txt) | Optional future installer tool |

The optional Windows runtime preparation script pins DjVuLibre and supporting GCC, winpthreads and libjpeg-turbo packages in `packaging/windows/djvu-runtime-lock.json`. It verifies downloads and retains original license notices, corresponding source archives and packaging recipes. Running that script does not itself establish native acceptance or satisfy every obligation of a later binary distributor.

No runtime bundle is published with this release. Before distributing binaries, audit the exact resolved dependency versions, retain notices, supply required corresponding sources and replacement/relinking materials, and meet each license's conditions. Wheel-provided notices are collected by the packaging helper; notices alone are not a substitute for required source. The optional TurboSun adapter does not include or execute the proprietary TurboSun application.

The synthetic DjVu fixture was generated from deterministic random pixels; its provenance is in `tests/fixtures/README.md`. There are no imported book images in this release.
