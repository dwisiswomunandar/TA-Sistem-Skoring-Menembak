# REM-002 — Original photo storage write boundary

Remediates the `PrivateTargetPhotoStorage.open()` entry point in AUD-002 F05
on `feat/django-migration`, baseline `2a3e0ca`. The working tree was clean before
this change. Iterations 01–04B.2 and REM-001 remain compatible.

## Root cause and contract

`allow_overwrite=False` and `get_available_name()` protect the save path only.
Previously the inherited `Storage.open()` delegated to `FileSystemStorage._open()`
without checking the mode. That method forwards the mode to Python's filesystem
`open()`: `wb` can truncate an original and `r+b` can modify it without updating
the persisted SHA-256. This is an application API boundary defect; it is not
evidence of a remotely exploitable upload endpoint.

The existing storage now overrides `_open()`, Django's extension point used by
public `open()`. Only the exact string modes `r`, `rb`, and `rt` are accepted.
All other values raise `ValidationError` before filesystem access, including
write, append, exclusive creation, every update (`+`) mode, reordered spellings
such as `br`, malformed values, and nonstrings. `rb` remains the default. Text
modes retain ordinary Python decoding behavior; image consumers should use `rb`.
Private keys are validated with the existing validator before reading, just as
they are before saving. Missing originals still raise `FileNotFoundError` for
valid read modes; reads never create a missing original.

`inspect_original()` already requests `rb`. Its hashing, size, MIME verification,
image decoding and dimension checks are unchanged. The remediation does not
re-encode files, rewrite metadata or require a migration.

New originals still use the existing `save()` / `_save()` path, with private key
validation and no overwrite, key reuse, collision renaming or key truncation.
The normal read handles cannot write, append or truncate. Routine `delete()`
still raises `ValidationError`; `url()` still raises `NotImplementedError`.
No storage backend, settings, model, migration, endpoint or dependency is added.

## Security boundary

This is a guard on this storage API, not filesystem immutability or a sandbox
for arbitrary Python code. Callers must open/reopen originals through
`storages["target_photos"].open(key, "rb")`. The returned object is still Django's
standard `File`: closing it and invoking its own `File.open()` can reopen its
filesystem path without going through this storage guard. That separate API is
outside this remediation's `PrivateTargetPhotoStorage.open()` contract. No new
file-wrapper abstraction is introduced.

Direct filesystem access (including `storage.path()` plus Python `open()`, another
storage instance, or explicit base-class calls), code with process privileges,
and a filesystem administrator can bypass this guard. SQL and privileged ORM
paths can bypass application metadata guards; this storage wrapper does not
authorize database writes or make database records immutable. SHA-256 records
verified bytes at inspection time; it is not continuous tamper prevention or a
guarantee against privileged concurrent file replacement. No claim is made that
existing stored originals have been scanned or that historical corruption has
been repaired.

Deployment must keep the private root unserved and restrict filesystem access.
Object authorization, retention, orphan cleanup, upload resource limits, periodic
integrity checks, and stronger OS/object-store controls remain separate work.
No real PostgreSQL access, scoring, AI, official workflow, UI, Laravel/FastAPI
change, commit or push is part of REM-002.

## Verification and acceptance

Nine new storage tests use disposable synthetic files and prohibit database
queries via `SimpleTestCase`. They cover default/binary/text reads, write/update/
append/create modes, invalid mode values, private keys, refusal before filesystem
access, missing files, read-handle write/truncate rejection, and both save paths.
For PNG and JPEG fixtures, each forbidden mode is followed by an independent
filesystem byte/SHA-256 comparison and full `inspect_original()` metadata check.
Existing evidence tests continue to cover persisted `TargetPhoto` metadata,
privacy, collisions, lifecycle and model integration.

Acceptance is limited to the storage open-mode boundary: the complete isolated
SQLite regression suite, system check, migration drift check and diff checks
must pass. This does not certify deployment readiness or PostgreSQL concurrency.

31 October 2026: all **365 tests passed (356 existing + 9 new)** in 78.810 seconds,
using explicit `config.settings.test` and SQLite in memory, with dotenv and
bytecode writes disabled. The nine focused tests also passed separately.
System check reported no issues; `makemigrations --check --dry-run` reported
`No changes detected`; `git diff --check` and whitespace checks on both new files
found no whitespace defects. Git's LF/CRLF notices reflect Windows checkout
configuration, not whitespace failures.

The first filesystem test run was blocked by Windows sandbox permissions on
temporary directories (`WinError 5`). The focused and complete suites passed
after permission was granted for those disposable fixtures. No PostgreSQL
connection or external service was used. Recommend accepting REM-002 for this
explicit storage API contract, with the above bypass boundaries retained.
