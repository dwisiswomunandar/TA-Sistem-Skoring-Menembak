# REM-003B.2C.3B - Normal administrator transfer and shared continuity

Baseline: feat/django-migration at 9463e50. This implements normal handover only.
Emergency recovery, global authority editors, endpoints and admin actions remain
unavailable. There is no schema change, data migration, automatic provisioning,
User.authority_edit_version or change to historical migrations.

## Fixed effective-administrator policy

activities.authority_policy defines activity_admin_v1 using these exact permissions:

- activities.issue_activitypermissiongrant
- activities.revoke_activitypermissiongrant
- activities.reactivate_activitypermissiongrant
- assessments.add_assessment
- assessments.change_assessment
- assessments.add_shotpoint
- assessments.change_shotpoint
- assessments.delete_shotpoint

An effective administrator is a persisted active User with all eight direct global
permissions AND all eight active grants in the same Activity. Content types are
matched by app, model and codename. Missing permission definitions fail closed.
Groups, staff, superuser and created_by do not supply this mandate. Future growth
of the ordinary permission registry does not enlarge activity_admin_v1.

effective_administrator_ids reads current database rows by default. Mutators pass
fresh locked users/grants, then read direct permissions without permission caches.
The predicate takes no locks itself and is not an endpoint/read-access selector.
require_administrator_continuity requires a nonempty administrator set and,
optionally, a particular successor in that set.

Ordinary grant commands retain their existing individual-command authorization,
receipt checks and contracts. Their existing before/after last-administrator guard
now uses this shared policy. An already zero-administrator activity is not repaired
or newly prohibited from all ordinary operations. Bootstrap retains its plan,
authentication, first-only latch and replay contract; its new-mutation postcondition
uses the shared predicate to require the initialized recipient. Existing bootstrap
receipt replay still does not recreate revoked authority.

## Authentication and protected approval

transfer_activity_administrator is both the deployment management-command name
and the domain service in activities.administrator_transfer. Service arguments:
source_email, source_password, successor_email, successor_password, operation_id,
optional using. Actor/scope/permission IDs from a request are not authentication.
The exact source and recipient IDs are selected only by the protected plan.

Both parties authenticate freshly through the configured trusted Django backend
before a new mutation. Under User locks, active state, database alias, authenticated
identity and unchanged credential hash are checked again. Password hashing occurs
outside the database transaction. Trusted backend/deployment configuration is part
of the boundary; no extra authentication backend or participant adapter is added.

ACTIVITY_ADMIN_TRANSFER_ENABLED defaults to the exact boolean False and
ACTIVITY_ADMIN_TRANSFER_PLANS to (). No environment flag silently enables transfer.
Enabled configuration must be a nonempty list/tuple of complete unambiguous plans.
Each plan has exactly these fields:

| Field | Contract |
| --- | --- |
| actor_id | Positive PK of the authenticated outgoing administrator |
| activity_id | Positive explicit Activity PK |
| recipient_id | Positive PK of a distinct, active, freshly authenticated successor |
| policy | Exact string activity_admin_v1 |
| expected_grant_versions | source/successor dictionaries, each with all eight permission names and integer versions |
| expected_state_sha256 | Lowercase 64-character digest of observed authority state |
| operation_id | Explicit UUID, unique among configured approvals |
| reason | Nonblank business explanation |
| approval_reference | Nonblank reviewed approval reference |

Source versions must be positive. Successor version 0 means no persisted row;
otherwise the exact positive version is required. Booleans are not versions/IDs.
Arbitrary permission fields, incomplete maps, self-transfer and other policy
versions fail configuration validation. The permission set cannot be chosen by
the caller. A reference alone is not authorization or proof of organizational
approval; authorized operators must review the actual protected configuration.

The trusted read-only transfer_preconditions(activity_id, source_id, recipient_id)
planning helper returns the expected_grant_versions and expected_state_sha256
fields. It neither grants approval nor proves identity and must never be exposed
as a general public selector. Its unlocked observation can become stale immediately.
Review the resulting plan and store a fixed copy in protected deployment settings;
do not call the helper dynamically from settings or regenerate approvals on retry.
The digest covers the Activity status, relevant User IDs/active flags/direct global
permission IDs and both parties' target-activity grants including identity, status,
version, original creator and timestamps. It contains no credential/hash/email.

Example template, with all APPROVED_* values reviewed externally (not executable
provisioning and not an enabled configuration added to this repository):

```python
ACTIVITY_ADMIN_TRANSFER_ENABLED = True
ACTIVITY_ADMIN_TRANSFER_PLANS = ({
    "actor_id": APPROVED_SOURCE_PK,
    "activity_id": APPROVED_ACTIVITY_PK,
    "recipient_id": APPROVED_SUCCESSOR_PK,
    "policy": "activity_admin_v1",
    "expected_grant_versions": APPROVED_FIXED_VERSION_MAPS,
    "expected_state_sha256": APPROVED_FIXED_STATE_DIGEST,
    "operation_id": APPROVED_UUID,
    "reason": APPROVED_REASON,
    "approval_reference": APPROVED_REFERENCE,
},)
```

Use the reviewed deployment settings module with the management command and
--operation-id, --source-email, --successor-email. It requires an interactive
terminal and prompts each party through getpass. There are no password arguments,
piped-password interface or credentials in the plan. Output includes receipt
identifiers only; errors are generic. Authentication failure for either party
prevents a new transfer. No operational database/real administrator was used here.

## Transaction, lifecycle and history

One transaction follows Activity -> all relevant User rows in ascending PK ->
grant rows in ascending PK. Relevant users include both parties and all potential
v1 administrators in the target activity. Grants include the target activity's
relevant users and all successor grants, to check cross-activity global effects.
No select_for_update joins, session locks or earlier Activity locks are acquired
after User/grant locking. Model saves re-lock only rows already held, or insert
new rows; they retain all HistoricalParent guards.

Approval and source authentication are checked under locks. For a new mutation,
the source must still be an effective v1 administrator. The successor must be
active and authenticate as the exact approved recipient. Closed activities deny
new transfers, consistent with the existing new-activation boundary.

All sixteen approved grant expectations and the authority digest must match.
Stale plans are rejected without refreshing/merging/replacing expectations.
Grant version counters catch status ABA changes. No global User version is added:
the digest checks current global state but cannot detect a remove/re-add ABA of
global permissions or active flags outside a unified versioned writer protocol.

The successor receives only missing direct v1 globals. Any global additions are
refused if that User has ANY grant in another activity (active or inactive), since
adding a global could activate that scope. If all eight direct globals already
exist, other-activity grants are left untouched. This conservative restriction
avoids cross-activity authority changes; it does not implement global offboarding.

Missing successor grants are inserted at version 1; inactive existing grants are
reactivated in place and increment once. Already active grants are not saved.
Original FK user/activity/permission/created_by, PK and created_at are preserved.
The successor is checked as effective BEFORE the source's three administrative
grants are deactivated in place. Those source grants each increment once. All
five operational grants, direct global permissions, Groups, User fields/passwords,
session assignments and prior audit events remain unchanged. A final continuity
check requires at least one effective administrator, including the successor.
This is handover of the administrative mandate, not complete personnel offboarding.

## Audit and historical receipts

Each successful mutation writes exactly one existing AuditEvent, action
activity_grant.admin_transferred, actor_kind=user, authenticated source actor,
Activity scope only. Scalar version_before/version_after are NULL for this batch;
per-grant versions appear in the allowlisted before/after snapshots and receipt.
Snapshots record relevant business state, the fixed policy, approval reference,
canonical command digest, added global permission IDs and effective-admin IDs.
Passwords/hashes, email, authentication tokens and arbitrary User data are excluded.
Business reason/reference fields must never contain manually pasted secrets;
attribute allowlisting cannot semantically redact arbitrary free text.

Audit insertion and every permission/grant change share the transaction. Audit or
continuity failure rolls back grants, increments and global M2M additions together.
AuditEvent.operation_id uniqueness is the existing ledger; no new receipt table
is needed. A UUID already used by a different plan/action/actor/scope is a conflict.

New-mutation authorization and historical-receipt authorization are distinct.
Before replay, the source must still authenticate as the active approved User,
and the exact original plan must still be present/enabled. A matching committed
receipt then returns BEFORE current source-mandate, successor eligibility, lifecycle
or stale-plan checks. Losing the old three grants therefore does not break an
authorized retry. Successor authentication is not required to read that historical
receipt (the CLI still prompts both parties). Receipt replay performs no mutation,
including when successor authority was later revoked. A new UUID still requires
current full source authority. Removing/altering the approval or disabling/inactivating
the source denies replay. Receipts describe the committed event, not current rights.

## Bounded security guarantees and follow-up

No emergency recovery, global security editor, authority-version column, model
migration, API, UI, scoring, assignment workflow or default real approval is added.
The generic User/Group Admin restrictions remain in force. Deployment settings
and execution privileges must be protected; effective settings are process-local,
so file edits do not promise instantaneous revocation in running processes. Decide
organizational approvers, evidence/retention and deployment change control outside
this code. Source and successor proofs use existing trusted Django password
authentication, not a new MFA/dual-control approval system.

Shared continuity protects these compliant command paths, not privileged ORM,
M2M writes, shell, SQL, account deactivation or arbitrary maintenance. Future
global-authority writers must adopt a consistent User lock/version/audit protocol
and assess all affected activities before global last-administrator guarantees.
Zero-admin emergencies remain blocked from normal handover; recovery is separate.

SQLite verifies functional validation, deterministic lock-request ordering,
rollback and regression behavior; it does not implement PostgreSQL row locks.
Before operational enablement, use independent connections in a dedicated
PostgreSQL test database for concurrent transfers on one activity; transfer vs
grant revoke, draft mutation/replay and bootstrap; competing cross-activity global
additions for the same successor; UUID collisions across activities; auth/permission
revocation during transfer; FK/unique implicit locks and deadlock behavior.
Never use a real deployment database for those tests. Reusing a rejected stale
plan with silently replaced expectations is prohibited.

## Verification

The final isolated SQLite run passed all 505 tests in 71.781 seconds: 468 existing
tests plus 37 new transfer/security tests. The focused transfer/bootstrap/grant
run passed 101 tests. System check reported no issues and makemigrations --check
--dry-run reported No changes detected. Tracked and new files pass whitespace
checks. No old tests or migrations were modified.

The initial sandboxed full run encountered Windows TEMP permission failures in
existing synthetic photo fixtures; the complete rerun with approved temporary
filesystem access passed. Both runs used explicit config.settings.test (in-memory
SQLite), PYTHON_DOTENV_DISABLED=1 and disabled bytecode writes. No real PostgreSQL,
operational bootstrap/transfer, commit or push was performed.

The tests cover dual authentication, protected approvals, fixed policy, source/recipient
eligibility, preservation, grant conflicts/ABA, cross-activity effects, atomic
audit rollback, continuity rollback, UUID conflicts, authorized historical retry,
approval cancellation, CLI rejection and shared grant/bootstrap compatibility.
