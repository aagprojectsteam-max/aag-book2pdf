# Architecture

`book2pdf` is the shared Linux/Windows Python package. Platform code is limited to paths, dependency discovery, runtime loading and desktop/packaging integration.

## Decode before salvage

The detector and container decoding layer rank structures by evidence rather than filenames or fixed sample offsets. Known BKC and BKF envelopes decode before fallback analysis. Recovery strategies retain exact/repaired/preview distinctions. Unknown data receives bounded signature, entropy, container, compression and relationship analysis; the engine never manufactures successful content recovery.

## Common page model

`RecoveredDocument` and its ordered page model feed the viewer and exports. PDF/BKC, BKF/DjVu and TurboSun adapters implement source-specific decoding beneath that model. TurboSun derives page order from catalogues and local indexes; TIFF boundaries and original strips are validated. Proprietary source executables are not run.

## Interactive priority

Supervised workers deliver checked visible pages promptly while background validation continues. The virtualized continuous viewer maintains lightweight geometry for all pages but expensive rasters only for visible/prefetch pages. Generation/cancellation guards discard stale work. Touch and kinetic scrolling live in the shared viewer, not format adapters.

## Fidelity and safety

Sources are read-only. Conversion records provenance and validates structures and page rendering. PDF extraction copies objects, BKF retains decoded native content evidence, and supported TurboSun PDF export preserves original CCITT strips without screenshot conversion. Recovery status and unresolved assumptions remain explicit. Atomic writers publish validated output; batch state supports safe resumption. Diagnostics may contain private paths and bytes, so generated evidence must not be posted publicly without review.
