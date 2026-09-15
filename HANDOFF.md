# Engineering Handoff — AAG Book2PDF

## Purpose

This is the durable engineering handoff for AAG Book2PDF. It records why the project exists, how format support is separated from recovery, what has been validated, what remains decoder-dependent, and how future maintainers should extend support without turning forensic guesses into false success.

## Goal

Build a Hebrew/RTL cross-platform reader and conservative recovery/export tool for scanned-book containers. The application should open formats it can prove it understands, provide a normal reader, export full or selected pages to PDF, and give useful bounded diagnostics for unknown/corrupt inputs without pretending unsupported encryption/encoding has been decoded.

## Core design principle

**Decode before salvage.** Structural recovery is only valid after the underlying payload/format is understood. Unknown variants receive classification, bounded analysis, provenance and truthful states. A reconstructed preview or repaired output must never be labeled as an exact original unless byte/format evidence supports that statement.

## Supported source families

- **BKC:** recognized BOOK envelopes containing PDF data; original content is preserved and evidence-based structural repair is permitted where safe.
- **BKF/DjVu:** recognized BOOK envelopes/directory layouts containing DjVu pages; native DjVuLibre is required.
- **TurboSun:** catalogue-indexed page folders with their catalogue/image containers; MDB Tools is required. An isolated encoded TIFF is not sufficient context.
- **PDF:** normal reading and direct page extraction.

The project is not a universal BOOK decoder. In particular, an unknown BKF-like file that still requires a real decoder remains `DECODER_REQUIRED`; research/preview capability does not change that truth state.

## Reader architecture

The application is shared Python/PySide6 code. Single-page and continuous-scroll modes use the same document/page model. Continuous mode lazily renders visible and nearby pages, releases distant rasters, tracks current page and preserves reading position during zoom. Scanned page images are never mirrored even though the interface is RTL.

Touch behavior includes pinch/double-tap and kinetic scrolling. Single-page horizontal navigation follows the Hebrew UX; continuous vertical gestures scroll rather than flip pages.

## Export and print

The application exports complete documents or ranges such as `1-5,8,10-15`, normalizing order and duplicates. PDF pages are copied directly where possible. TurboSun export retains verified original CCITT streams. No OCR is introduced. Output is built through temporary files, validated, and atomically published with overwrite protection. Batch work supports resumability and cancellation. Qt PDF printing is part of the Linux acceptance evidence; physical printer output is a separate evidence layer.

## Recovery architecture

Format detection, envelope/framing, container decode, BKF parsing, diagnostics, export and GUI are separated into modules under `book2pdf/`. This separation is important: adding a new format should not require scattering magic offsets or optimistic fallbacks through the GUI.

Unknown-file research is bounded. Diagnostic/forensic packages should explain what is known, damaged, missing or decoder-dependent. Generic wrapper detection and byte-interval reasoning are preferred over hard-coded offsets tied to one private sample.

## Installation

Linux uses Python 3.12 (recommended) plus native dependencies including qpdf, DjVuLibre, MDB Tools and Qt runtime libraries. A managed user-local install is available through `install.sh --make-default`; `uninstall.sh` removes the application integration but not source books or exported documents.

Windows uses the same Python code path; see `WINDOWS.md`. Native Windows acceptance of the exact public release must remain `NOT_PROVEN` until tested as such; a prepared BuildKit is not acceptance evidence.

## Validation

Linux production acceptance covered BKC, BKF and TurboSun opening, lazy viewing, full/partial export, background validation and Qt PDF printing. Tests use synthetic documents; real books and private forensic reports are intentionally excluded from the public repository. Optional private-sample tests skip when their environment variables are absent.

Run:

```sh
python -m pip install '.[test]'
QT_QPA_PLATFORM=offscreen python -m pytest -q
```

GitHub Actions runs cross-platform automation from `.github/workflows/cross-platform.yml`, but hosted CI must not be confused with real Windows/native-format acceptance.

## Known evidence gaps

Physical printer output is not proven by a Qt PDF-print test. Physical TurboSun touch acceptance remains not proven unless a specific test records it. Unknown encoded/encrypted BOOK variants may require a genuine decoder; the application should continue to say so. Public tests cannot reproduce private real-book evidence that was intentionally excluded.

## Security, privacy and legal boundaries

Do not commit copyrighted/private book content merely to make a regression fixture. Prefer synthetic fixtures and hashes/metadata where possible. Third-party native tools retain their own licenses; see `THIRD_PARTY.md`. The application itself is AGPL-3.0-or-later. Security reporting is documented in `SECURITY.md`.

## Repository map

- `README.md` — public capabilities, setup and limitations.
- `ARCHITECTURE.md` — component architecture.
- `RELEASE_PROVENANCE.md` — accepted-source/release relationship.
- `WINDOWS.md` — Windows preparation and evidence boundary.
- `book2pdf/` — detectors, decoders, recovery, export, batch and GUI.
- `tests/` — synthetic and optional private-sample tests.
- `install.sh` / `uninstall.sh` — user-local lifecycle.
- `.github/workflows/cross-platform.yml` — hosted automation.
- `CHANGELOG.md` — concise release history.

## Maintenance rules

When adding a format: capture a minimal non-private structural description; implement detection separately from decoding; make unsupported states explicit; add synthetic tests; add private-sample tests only behind environment variables when needed; validate exported PDFs structurally; verify atomic failure behavior; exercise GUI opening/navigation/export; update architecture, changelog, provenance and this handoff.

When adding recovery: record what bytes/records are trusted, what is reconstructed, what is discarded and why. A preview must carry provenance. Never bypass a missing decoder by calling damaged/opaque bytes a successful conversion.

## Historical integrity rule

The project evolved from format-specific recovery into a modular universal-facing tool, but “universal” means universal analysis/routing, not magical decoding of every proprietary container. Preserve failed/blocked cases because they define the safety boundary. New success claims require reproducible evidence.

## Current handoff status

As of the documentation audit on 2026-09-15, the repository has README, architecture, release provenance, Windows notes, changelog, contribution/security/third-party documentation, cross-platform CI, substantial tests, modular source, install/uninstall tooling, and this handoff. Future work should append exact decoder/recovery milestones and acceptance evidence here instead of relying only on transient development reports.