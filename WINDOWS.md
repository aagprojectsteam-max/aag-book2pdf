# Windows: shared Python application first

Native Windows acceptance of release 1.5.0 is **NOT_PROVEN**. Linux tests and cross-platform code inspection do not establish native GUI, printer or touch success. No Windows-specific recovery implementation exists.

Use Windows 64-bit with Python 3.12 installed. In PowerShell, from the source/BuildKit folder:

```powershell
py -3.12 -m venv .venv-win
.\.venv-win\Scripts\python.exe -m pip install ".[test]"
.\.venv-win\Scripts\python.exe -m book2pdf.gui.app
.\.venv-win\Scripts\python.exe -m book2pdf.gui.app "C:\Books\input.book"
```

For BKF, prepare the pinned native DjVuLibre dependency set (network download; hashes and sources retained):

```powershell
.\.venv-win\Scripts\python.exe -m pip install pefile==2024.8.26 zstandard==0.25.0
.\.venv-win\Scripts\python.exe scripts/prepare_windows_runtime.py
$env:BOOK2PDF_DJVU_LIBRARY = "$PWD\.build\djvu-runtime\libdjvulibre-21.dll"
.\.venv-win\Scripts\python.exe -m book2pdf.gui.app
```

TurboSun additionally requires trusted native MDB Tools (`mdb-tables.exe`, `mdb-export.exe` and required DLLs), with their directory in `AAG_MDB_BIN`. They are not bundled here; native TurboSun dependency/runtime acceptance remains unproven.

Run `.\.venv-win\Scripts\python.exe -m pytest -q` for synthetic tests. `scripts/test_windows_python.ps1` accepts locally owned BKC, BKF and supported new-variant samples, expected page counts and a fresh evidence directory. Use a release BuildKit with its verified manifest. Automated Qt PDF output is distinct from Microsoft Print to PDF or a physical printer.

Packaging scripts are provided for later use, but their native acceptance gate must pass first. No EXE, installer or native runtime bundle is released with 1.5.0. Source and BuildKit package this same Python application. Third-party licenses and corresponding source obligations apply to any later binary distribution.
