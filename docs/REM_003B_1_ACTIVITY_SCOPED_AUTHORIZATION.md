# REM-003B.1 - Activity-scoped authorization foundation

Baseline: feat/django-migration at e124279. The working tree was clean: no earlier
REM-003B.1 implementation or test artifact was present to resume. Previous
checkpoint documentation reports 365 passing tests; that is not a result for
this change. This iteration implements only the approved draft authorization
foundation from REM-003A / AUD-002 F06.

## Contract and scope

The five draft services retain their signatures and permission combinations.
Each now requires a current active server-authenticated Django User, all global
permissions for its action, an active ActivityPermissionGrant for every required
permission in the persisted session activity, and matching assigned_assessor.
Neither created_by, staff nor superuser bypasses scope or assignment. A User
instance is not authentication proof: callers must obtain it from the trusted
server authentication boundary, never resolve a client actor ID into a User.

activities.access_rules is a closed action registry shared by the model, policy
and draft services. Only the five existing draft permissions can currently be
stored in grants. Unknown actions, invalid principals and inaccessible/missing
session IDs fail with a generic PermissionDenied. A client's activity/scope is
never an input to the policy. Assessment/point/evidence parent matching remains
validated by existing services and models. No verified/locked, scoring, participant
authentication, generic read policy, endpoints, download, or business admin is
opened. Activity closure/withdrawal eligibility for existing draft edits is not
newly defined here; existing lifecycle and history rules remain in force.

activities.authorization.draft_sessions is an action-specific authorized session
selector, filtering assignment and every required scope grant before results,
counts or pagination. It is not a photo/report/general object-read policy. Call
it afresh per request; do not retain evaluated querysets or permission decisions
across requests. Invalid principals/actions fail closed.

## Grant model and migration

ActivityPermissionGrant is an extension of the 14 CORE models. FK user, activity,
permission and created_by use PROTECT. The combination user/activity/permission
is unique, edit_version is a positive BigInteger, is_active controls eligibility,
and created_at/updated_at retain attribution/timing. Grant identity and original
creator are immutable. Only active status is editable through the low-level model.
It reuses HistoricalParent locking/version/missing-row guards and guarded default,
base and related managers. Partial/deferred/bulk/queryset writes and deletion are
disabled; stale saves cannot restore revoked grants. Successful payload changes
advance the server token; no-op saves do not. SQL/internal ORM bypasses remain.

activities/0003_activity_permission_grant only creates the table and constraints.
There is no data migration or implicit grant to existing creators/staff/superusers.
Historical migrations are unchanged. Permission allowlisting is Python validation;
DB enforces FK, uniqueness and version positivity, not cross-table codename rules.

## Transactions and locking

A cheap policy precheck precedes domain locks. Inside the original transaction,
lock order is TrainingSession -> ActivityParticipation -> Assessment -> ShotPoint
(PK order) -> User -> ActivityPermissionGrant (PK order), skipping unused types.
The final policy re-reads persisted session context and fresh User permissions,
locks matching grant rows, and checks active grants again BEFORE idempotent replay.
Grant locking does not select_related or implicitly lock Permission/Activity joins.
Authorization queries do not acquire an earlier domain lock after User/grant locks.
The existing audit insertion reuses domain locks already held by the transaction.

Identical authorized retries still return historical receipts before checking
mutation version/status, without new writes. Revoked permissions/grants or lost
assignment deny retries. Authorization failure cannot consume a version or create
an audit. Business mutations, invalidation, version increments and audit INSERT
remain in the same transaction; audit failure rolls everything back.

This establishes an application lock order, not PostgreSQL concurrency proof.
Global permission/group M2M writers do not yet follow a shared locking protocol.
User locking alone does not serialize those changes. Grant low-level save locks
its own row; future audited grant writers must acquire relevant User rows before
grant rows, and must never acquire existing session locks after security locks.
Do not promise immediate cancellation of already authorized in-flight commands.

## Provisioning and remaining security boundaries

No grant endpoint, admin registration/action, role-based automatic provisioning,
management command or business grant/revocation workflow is provided. Model writes
are for trusted synthetic fixtures/maintenance and do not produce business audit.
Do not expose this model directly to client payloads or register a default editable
ModelAdmin. Direct Assessment/ShotPoint model writes likewise remain trusted ORM
APIs without automatic service authorization/audit.

Before operational provisioning, implement a separate authorized atomic service:
validate an approved issuer and recipient, restrict scope/permissions to the issuer's
mandate, lock User IDs and grant IDs consistently, compare expected grant version,
write the explicit grant/status change plus allowlisted activity-scoped AuditEvent
in the same transaction, and enforce idempotency. Bootstrap authority must be
approved explicitly and recorded through a reviewed audited procedure. Never infer
it from activity authorship, staff/superuser flags or historical assessment actors.
Audit failure must abort provisioning; existing data receives no automatic grants.

Assignment changes after any photo/Assessment/session audit remain prohibited by
04B.1. Selective grant revocation does not rewrite historical assignment. Historical
reassignment needs its own approved audited design; this iteration adds no bypass.

On a dedicated PostgreSQL test database, test grant revocation vs draft writes and
receipt replay using independent connections/barriers; permission/group changes,
assignment changes before history, absent grants/concurrent provisioning, rollback,
FK/unique implicit locks and deadlock behavior. Define the ordering point for
revocation and require all production security writers to follow that contract.
No real PostgreSQL, legacy Laravel/FastAPI, AI, UI, scoring or official workflow is
part of this iteration. No commit/push is performed.

## Verification

Final verification: all 383 tests passed (365 baseline tests retained/adapted,
18 new tests) in 61.526 seconds using config.settings.test, SQLite in memory,
PYTHON_DOTENV_DISABLED=1 and bytecode writes disabled. The focused authorization
run initially found an old deleted-User fixture conflicting with the new PROTECT
FK; it was corrected without deleting grant history. Full regressions cover audit
rollback, all five operations, status/provenance, stale versions and idempotency.
Standalone system check reported no issues. makemigrations --check --dry-run
reported No changes detected. Tracked and new-file whitespace checks were run.
Synthetic photo tests required approved Windows temporary-directory access;
no PostgreSQL or external service was accessed.
