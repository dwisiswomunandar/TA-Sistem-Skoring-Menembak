# REM-003B.2B - Audited activity grant command services

Baseline: feat/django-migration at 74ab375. Implements the three ordinary commands
from the REM-003B.2A design, with no bootstrap/recovery, HTTP API, UI, editable grant
admin, global-permission assignment, or operational activation. The previous
REM-003B.1 document describes its historical checkpoint; this document supersedes
its statement that ordinary audited grant commands are unavailable.

## Registry and authority

ActivityPermissionGrant now declares three custom permissions:
activities.issue_activitypermissiongrant, activities.revoke_activitypermissiongrant,
and activities.reactivate_activitypermissiongrant. Migration activities/0004 is
an additive AlterModelOptions migration; Django creates the permission definitions
through its normal post_migrate handling. No User, Group or existing grant is
assigned any new authority, and no existing row is rewritten.

The stored-permission registry accepts the five draft permissions and these three
administrative permissions, validated by exact app/model/codename. The delegation
registry remains restricted to the five draft permissions. Ordinary commands
cannot create, revoke or reactivate an administrative grant. Such mandates exist
only as trusted test fixtures until separately approved control-plane provisioning.

Each command requires an active, persisted, same-database Django User obtained
from trusted server authentication. A User instance or client actor ID alone is
not proof of authentication. Global administrative permission must be assigned
directly to that User, AND its administrative activity grant must be active.
Group membership, staff, superuser and activity authorship confer no shortcut.
All actor checks query fresh database state and ignore caller permission caches.

Issue/reactivate additionally require the actor's direct global target permission
and active target-permission grant in the same activity, following the strict
delegation ceiling proposed in 2A. Recipients must be active and have the target
global operational permission; their operational permission may come from a Group,
consistent with REM-003B.1. Revoke does not require the recipient to remain active
or globally eligible, nor the actor to retain the target operational permission.
All three reject actor == recipient. Commands never write global permissions,
Groups, session assignment, scores or historical context.

## Command API and lifecycle

activities.grant_services exports issue_grant, revoke_grant and reactivate_grant.
All use keyword-only actor, activity_id, recipient_id, permission_id,
expected_edit_version, operation_id, reason, and optional using. Resource IDs are
selectors, not assertions of scope or actor identity. This is a trusted Python
service boundary; there is no HTTP endpoint or payload-to-model adapter.

Issue requires expected version 0 and an absent User/Activity/Permission tuple;
it creates an active row at version 1. An inactive existing row must be reactivated,
not inserted again. Revoke requires an active row and matching positive version;
reactivate requires an inactive row and matching positive version. Each transition
increments the stored token once. Wrong-state commands with a fresh UUID are
rejected, not treated as successful no-ops. Deletion and identity reassignment
remain prohibited. Reopening keeps the same PK and original creator/timestamp.

New activation is conservatively denied on closed activities; revoke remains
available. Historical receipt replay precedes target-state/recipient-eligibility
checks, so an authorized replay does not depend on the target's later lifecycle.
These activation rules are explicit reviewable behavior, not a new activity
closure or historical reassignment workflow.

GrantReceipt is immutable and contains operation UUID, audit ID, grant ID,
edit_version and is_active at commit time. It is a historical result, not a fresh
read of the grant. Requests must carry an explicit valid UUID, exact integer
version (not bool), positive 64-bit IDs and nonblank trimmed business reason.

## Atomicity, locking and replay

The precheck rejects obvious unauthorized requests before locks. Within one
transaction.atomic, lock Activity, then all relevant User rows in ascending PK,
then relevant activity grant rows in ascending PK. Users include issuer, recipient
and candidate administrative mandate holders for the limited continuity check.
The registry, actor eligibility, direct permissions and scope are read again.
No select_related join is used in locking queries. Audit is Activity-scoped and
does not later acquire session locks. Existing 04B.1/REM-003B.1 lock order is kept.

An Activity lock serializes compliant grant commands even when a target row is
absent; uniqueness remains the DB backstop. The model's version/immutable identity
checks still run. There is no silent refresh/merge of stale input versions.

Current authorization is checked BEFORE reading the operation receipt. A canonical
SHA-256 binds schema/action/actor/activity/recipient/permission/expected version/
reason. An identical still-authorized retry returns the committed receipt without
mutations, version consumption or a second audit. Another command using the same
UUID raises OperationConflict. Authority revocation denies replay. Revoke and
reactivate retain their own per-action authority requirements on retry.

The global AuditEvent.operation_id uniqueness ledger is reused. Audit insertion
uses an inner savepoint; UUID collision or any audit error propagates and rolls
back the outer grant mutation/version. Failed commands do not reserve a UUID.
Hashes identify canonical requests; they are not signatures against a privileged
DB administrator or arbitrary trusted Python code.

## Audit contract

Each successful mutation emits exactly one AuditEvent: actor_kind=user, actor_user
from the fresh principal, activity set, participation/session/assessment NULL.
Actions are activity_grant.issued, activity_grant.revoked and
activity_grant.reactivated. Reason is required. Version steps are NULL->1 or v->v+1.

Before/after allowlists contain grant IDs, recipient/activity/permission IDs,
is_active, edit_version, created_by, creation/update timestamps, and permission
app/model/codename. After state adds the command digest and receipt. No request,
User profile, password/hash, credential, cookie, token or session key is copied.
Business reason is free text; callers must not paste credentials into it. The
allowlist is not automatic redaction of secrets manually included in free text.

## Limited continuity guard and remaining boundaries

A local defensive check rejects removal of the last currently effective full
administrator by these commands. Its definition is an active User holding all
eight registered permissions directly and all eight active scope grants. It
checks before/after under Activity/User/grant locks and rolls back the change.
Partial administrators remain valid for their authorized individual commands.
Existing zero-administrator states are not repaired by this guard.

This is NOT complete last-administrator protection. UserAdmin, direct permission
M2M writes, low-level grant/model writes, SQL and external maintenance do not yet
follow a unified continuity/revocation protocol. Those paths can disable Users or
remove mandates outside the commands. No bootstrap/recovery or authority-transfer
service is implemented. Do not expose grant commands for operational use before
bootstrap and security administration hardening are approved and implemented.

Low-level model operations remain trusted maintenance APIs and do not automatically
invoke command authorization or create audit events. No default editable ModelAdmin
or grant-management endpoint is added. The model retains all prior ORM guards,
FK PROTECT, uniqueness, and immutable original attribution.

SQLite tests prove deterministic validation/rollback behavior, not PostgreSQL
concurrency. A dedicated PostgreSQL test database still needs independent-session
barrier tests for simultaneous issue, revoke vs draft authorization/replay,
permission revocation, two candidate administrator removals, missing-row races,
FK/unique implicit locks and deadlocks. Global permission/User writers must follow
an agreed protocol before strong revocation/continuity guarantees are claimed.
Never retry stale input with a silently replaced expected version.

## Verification

Final verification: all 414 tests passed (383 unchanged baseline tests plus
31 new tests) in 75.420 seconds. The initial focused authorization run passed
47 tests; the final full run includes the two additional receipt/Group regression
cases. System check reported no issues. makemigrations --check --dry-run reported
No changes detected. Git diff and whitespace checks cover tracked and new files.
The additive metadata migration preserves grant state on forward/reverse migration.
Synthetic photo tests required approved temporary-directory access on Windows.
No commit or push was performed. All commands use explicit
config.settings.test with SQLite in memory, dotenv disabled and bytecode writes
disabled. No real PostgreSQL, AI or legacy Laravel/FastAPI was accessed.
