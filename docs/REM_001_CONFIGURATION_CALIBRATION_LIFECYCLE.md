# REM-001 — Configuration and calibration lifecycle

Remediates AUD-002 F04/F09 at checkpoint 73b23d7. The existing Django
architecture, historical FK PROTECT policy and Iterations 01–04B.2 remain.

## Contract

TargetVersion, ScoringRuleVersion, TargetRing and Calibration now have a positive
BigInteger edit_version. It is separate from the semantic configuration version.
New rows start at 1. Each successful existing-row save advances the token once,
including a no-op save; failed validation does not consume a token. A publish,
accept, reject or retire also advances the token once. Delete checks the token.

Full instance save/delete compare instance.edit_version with the persisted row
under its existing lock. Existing call syntax remains valid. Callers carrying a
request token can additionally supply expected_edit_version to save/delete,
configuration publish/retire, or async asave/adelete. When provided it must be an
exact integer matching the stored token AND the complete instance token. Never
reload an object and discard the request's old token before invoking these APIs.
This is a trusted model API, not an HTTP authentication/authorization boundary.

Conflicts raise ValidationError before domain writes. No merge, reload or retry
silently replaces stale payloads. Callers must reload and review before a new
command. retire retains its old behavior of changing only persisted lifecycle
metadata after validating the caller's token; unsaved payload edits are ignored.
No explicit accept method was added: Calibration.status/accepted_by plus save
remain the acceptance API, now guarded by the optimistic token.

Partial/deferred writes are rejected. New instances cannot overwrite existing
PKs. Previously persisted instances whose row is missing, including instances
whose PK was cleared by delete(), cannot fall back to INSERT. Intentional fresh
instances are not tombstones: explicit PK reuse and SQL/internal ORM bypasses
remain trusted maintenance concerns, not absolute database immutability.

## Target aggregate

Ring insert/edit/delete advances the draft target token in the same transaction.
A move advances both source and destination exactly once. The ring has its own
token so two loaded copies cannot overwrite/delete/reparent each other's edits.
Target tokens are not copied into cached parent objects after a ring write.
Reload the target explicitly after ring editing before publishing; old target
instances must fail publication. A ring's own token protects that row, not an
unrelated ring; independent ring edits may serialize successfully while both
invalidate previously loaded target tokens.

Existing lock order remains: target parents sorted by PK, then ring; calibration
then target at acceptance; configuration row alone for its own lifecycle. Aggregate
token updates reuse already locked parents and acquire no locks in reverse order.
The model transaction rolls back ring, parent tokens and timestamps together.
As with Django generally, after a surrounding transaction is rolled back callers
should discard/reload their in-memory objects before further work.

Published/retired payloads and accepted/rejected calibrations remain terminal.
Published targets/rules can only retire through the existing controlled API.
Geometry, target/rule eligibility and all previous publication checks still run.
This does not certify calibration quality or authorize acceptance by a user.

## Migration and compatibility

assessments/0004_harden_configuration_calibration_lifecycle adds four token columns
and four positivity constraints. All old rows initialize to 1 without rewriting
approved business payloads. No old migration is edited. Synthetic migration tests
compare all previous field values on published targets, rings, retired rules and
accepted calibrations before/after and after reversal.

Old regression fixture setup reloads targets after its own ring creation. This
is the intentional aggregate contract change, not silent conflict handling in
production. Tests cover stale edits/transitions/deletion, ring create/edit/move/
delete, resurrection, forged instances, expected tokens, async calls, validation
failure, aggregate rollback, constraints and additive migration preservation.

## Remaining boundaries

SQLite tests verify state transitions and rollback, not PostgreSQL row locks.
On a dedicated PostgreSQL test database, use independent connections/barriers to
exercise two equal-token writers, ring mutation vs publish, opposite moves, stale
acceptance, target retirement vs acceptance, missing-row writes, FK/unique implicit
locks, overflow and bounded deadlock retry. Never auto-retry a stale command with
a newer expected version. No real PostgreSQL connection is used by REM-001.

SQL, unguarded QuerySets, Model.save/save_base and historical migration models can
bypass application guards. Business audit and organization/object authorization
for configuration/calibration still need separate services. No F05 storage fix,
scoring engine, official assessment workflow, AI, endpoints, UI, Laravel/FastAPI
changes, commit or push are part of this remediation.

## Verification result

31 October 2026: all **356 tests passed (336 existing + 20 new)** using explicit
config.settings.test and SQLite in memory, with dotenv and bytecode writes
disabled. This includes the additive migration preservation/reversal test.
System check reported no issues; makemigrations --check --dry-run reported
No changes detected; git diff/whitespace checks cover tracked and new files.
The default sandbox denied Windows temporary photo directories; the complete
suite passed with filesystem access for those synthetic fixtures. No real
PostgreSQL, AI inference, legacy application or external database was used.
