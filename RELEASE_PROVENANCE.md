# Release provenance

Application version: **1.5.0**.

Accepted private production commit: `6f7ba9be809b852a8a4e5797b6f302e0994ccf9a`.
Accepted private tag: `stable-book2pdf-turbosun-1.5.0-20260914`.

The public repository starts a new history because private history contained real document content. The accepted local repository, stable tags, installed application and rollback data are preserved. Every file under `book2pdf/` is byte-identical to the accepted production tree. Publication changes concern documentation/license metadata, distribution inputs and removal of machine-specific defaults from optional tests. No private history or real document fixture is included.

Linux production acceptance was completed before publication. Public-candidate tests are run again independently and their results are recorded in the release manifest/notes. A skipped private-sample test is not public proof of real-document acceptance. Native Windows and physical printer output remain **NOT_PROVEN**. Published archives contain no third-party native runtime; dependencies are installed separately under their own terms.

## Public-candidate verification

Python 3.12 on Linux: **308 passed, 6 skipped**. Four skips require private real-book samples and two require native Windows APIs. The full public suite runs without private fixtures. Public history and tracked-tree credential scans found no matches; all application package files match the accepted production commit byte-for-byte.
