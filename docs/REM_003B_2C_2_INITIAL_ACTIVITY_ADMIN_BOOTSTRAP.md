# REM-003B.2C.2 - Secure initial activity administrator bootstrap

Baseline: feat/django-migration at 31a2ca0. Adds a deployment-only bootstrap service
and interactive management command. No public endpoint, admin action, editor,
registration hook, data migration, recovery or authority transfer is introduced.

## Trust and authentication contract

Two independent conditions are required: a real Django authentication result AND
a matching plan in protected deployment settings. actor_email selects a login;
actor_password is authenticated through the configured trusted Django backend.
The service does not accept an actor User instance or actor_id as authentication.
Actor must exist, be active and belong to the write database. After locking User
rows, it checks that the authenticated password hash has not changed. Staff,
superuser, Group membership, activity authorship and possession of an approval
reference supply no bootstrap authority.

ACTIVITY_ADMIN_BOOTSTRAP_ENABLED defaults to the exact boolean False.
ACTIVITY_ADMIN_BOOTSTRAP_PLANS defaults to an empty tuple. No environment flag
silently enables this feature. Protected deployment settings must explicitly set
the boolean to True and provide a reviewed list/tuple of complete plans. Empty,
malformed or ambiguous enabled configuration fails before authentication/writes.
Only one plan per activity and one plan per operation UUID may be configured.

Each plan is a dict with exactly these keys:
- actor_id: authenticated operator's positive User PK;
- activity_id and recipient_id: explicit positive PKs;
- permissions: each of the eight registry names exactly once;
- operation_id: explicit UUID;
- reason and approval_reference: nonblank business metadata.

The service derives actor_id from authentication, canonicalizes the request and
requires exact agreement with a configured plan. Permission order and surrounding
text whitespace are normalized; permission duplicates, subsets or arbitrary names
are rejected. A flag, UUID, client-supplied reference or password alone cannot
substitute for the protected approval configuration.

The settings file and deployment execution environment are part of the security
boundary. An approval reference is traceability metadata, not cryptographic proof
that an organization approved the operation. Operators must review and restrict
who can alter/execute these plans. Settings are effective process configuration;
changing a settings file does not promise real-time revocation in a running process.
This is not a sandbox against privileged Python, shell, SQL or deployment operators.
Do not expose this service or settings through a future HTTP/config-editing API.

## Configuration template and command

The following template is documentation only. APPROVED_* values must be explicitly
reviewed and replaced in a protected deployment settings module; no such enabled
plan or real administrator is added to the repository:

```python
ACTIVITY_ADMIN_BOOTSTRAP_ENABLED = True
ACTIVITY_ADMIN_BOOTSTRAP_PLANS = ({
    "actor_id": APPROVED_ACTOR_PK,
    "activity_id": APPROVED_ACTIVITY_PK,
    "recipient_id": APPROVED_RECIPIENT_PK,
    "operation_id": APPROVED_UUID,
    "reason": APPROVED_REASON,
    "approval_reference": APPROVED_REFERENCE,
    "permissions": [
        "activities.issue_activitypermissiongrant",
        "activities.revoke_activitypermissiongrant",
        "activities.reactivate_activitypermissiongrant",
        "assessments.add_assessment",
        "assessments.change_assessment",
        "assessments.add_shotpoint",
        "assessments.change_shotpoint",
        "assessments.delete_shotpoint",
    ],
},)
```

Run bootstrap_activity_admin with --settings naming that approved settings module,
--actor-email, --activity-id, --recipient-id, --operation-id, --reason,
--approval-reference and a single --permission followed by all eight permission
names. The supplied nonsecret plan must match the protected configuration.
The command requires an interactive deployment terminal and obtains the actor
password through getpass. It has no password argument, environment-password or
piped-password interface. Never place credentials on a command line or in the
approval plan. Output contains only operation/activity/recipient/audit identifiers;
command authentication/configuration failures return a generic rejection.
Apply normal migrations/permission provisioning first; bootstrap fails closed if
any of the eight permission definitions is missing. It does not create definitions.
Review the target database and normal deployment controls before enabling any
plan. Disable/remove approvals after the reviewed operation; replay requires the
same still-effective approval and authenticated actor. No bootstrap command was
executed against a deployment or operational database during this implementation.

## Atomic state changes and first-only behavior

activities.bootstrap.bootstrap_activity_administrator requires actor credentials,
activity_id, recipient_id, permissions, UUID operation_id, reason and approval
reference. It uses the existing exact permission registry and audit insertion
helper, without adding bypasses to issue/revoke/reactivate or draft services.

Within one transaction: lock Activity -> actor/recipient User rows in ascending
PK -> recipient grant rows in ascending PK. User locking serializes compliant
writers for that recipient across activities; the bootstrap takes no earlier
activity/session locks after security locks. Read-only checks for administrative
mandates use the already locked activity. No locking select_related join is used.
Approval and actor state are checked again before any operation replay.

A new bootstrap requires an active recipient and a non-closed activity. An
existing bootstrap AuditEvent for the activity permanently closes initial
bootstrap, independent of later grant revocation. Existing administrative grants,
active or inactive, also reject bootstrap: this cannot repair a staged/old mandate.
An inactive operational grant is not reactivated. Existing active operational
records in the target activity may be retained, with PK/version/creator/timestamps
unchanged; only missing records are created at edit_version 1.

All eight whitelisted permissions are assigned directly to the recipient User
where missing, and all eight activity grants become available. Unrelated direct
permissions, Groups, account flags, credentials and identities are preserved.
No is_staff/is_superuser is set, no User is created, and no session is assigned.
The resulting business administrator still needs assignment for draft operations.

Global additions are refused if the recipient has any grants in another activity:
otherwise a newly added global permission could activate an unrelated scope.
The same recipient can initialize another activity if all eight direct globals
already exist, since that operation adds no global capability and preserves the
other activity. More complex cross-scope provisioning belongs to separate reviewed
security administration, not this initial-bootstrap command.

## Audit and idempotency

Exactly one AuditEvent records the batch, action activity_grant.admin_bootstrapped,
actor_kind=user with the authenticated operator, Activity scope only. Both scalar
version fields are NULL because individual grant versions are recorded in the
batch snapshot. authentication_context remains NULL for the User actor contract.

Before/after snapshots contain recipient/activity IDs, approved permission names,
approval reference, relevant direct permission IDs and allowlisted grant metadata.
After state includes the canonical request digest and a historical BootstrapReceipt
with grant IDs/versions and global permission IDs added. All grant/global-permission
changes are recorded. Unchanged unrelated global permissions are not copied.
No email, password/hash, authentication credential, token, cookie or session key
is copied into snapshots. Reasons/references are business free text; they must not
contain manually pasted credentials. Allowlisting is not semantic secret redaction.

The existing unique AuditEvent.operation_id is the ledger. Identical authorized
retries return the original receipt without writes. A different approved command
using the UUID raises OperationConflict. Current deployment approval and actor
authentication, not the recipient's subsequent administrative grant state, authorize
bootstrap replay. Thus revoked grants are never recreated by replay. Disabling the
approval denies replay. A new UUID cannot reopen an already bootstrapped activity.

An inner audit savepoint handles unique collisions, and errors propagate to roll
back all outer changes, including direct User permissions. Failed operations leave
no partially provisioned administrator or reserved UUID. The Activity lock prevents
two compliant first-bootstrap writers from both succeeding; this is application
serialization, not a DB trigger against arbitrary SQL or fabricated audit events.

## Remaining boundaries

Recovery, successor appointment, authority transfer and complete last-administrator
protection are not implemented. Django Admin security restrictions remain intact.
Ordinary grant/draft command authorization is unchanged. Global User/Group security
administration still needs its broader approved audit/revocation protocol.

The command is deployment-only, default-disabled and not a general operational
onboarding interface. Database administrators, privileged ORM/shell code, custom
untrusted authentication backends and settings writers remain outside its guarantee.
The configured authentication backend and DB routing are trusted deployment inputs.
No PostgreSQL concurrency guarantee follows from SQLite tests. On a dedicated
PostgreSQL test database, exercise simultaneous first bootstrap (same/different
UUID), UUID collisions across activities, grant writers sharing a recipient,
password/active-state changes, rollback/commit failures, implicit FK locks and
consistent lock ordering. Do not silently retry failed authorization with a new
plan/UUID or auto-refresh stale business commands.

## Verification

Final full-suite result: 468 tests passed (435 unchanged baseline tests plus
33 new bootstrap tests) in 65.540 seconds. The initial focused bootstrap suite
passed 30 tests; the final suite also covers missing approved resources, disabled
approval replay and CLI authentication-error confidentiality.
System check reported no issues; makemigrations --check --dry-run reported
No changes detected. No model/migration changes are needed. Diff/whitespace checks
cover all tracked and new files. Tests used explicit config.settings.test, SQLite
in memory, disabled dotenv and bytecode writes; command prompts were mocked and
all actors/recipients/approvals were synthetic. Photo regressions required approved
Windows temporary-directory access. No real administrator or operational database
was provisioned. No PostgreSQL, AI, scoring, Laravel/FastAPI, commit or push was used.
