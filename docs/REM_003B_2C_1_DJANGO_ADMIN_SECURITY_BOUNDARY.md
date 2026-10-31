# REM-003B.2C.1 - Django Admin security boundary

Baseline: feat/django-migration at 78026cc. Generic Django Admin is not an audited
security-administration workflow. This iteration closes its authority-writing
paths while preserving authenticated access and ordinary non-sensitive profiles.

## Root cause

AccountAdmin previously exposed is_active, is_staff, is_superuser, groups and
user_permissions, using AccountChangeForm with fields="__all__". Standard UserAdmin
could change authority without activity grant command audits. The default Group
admin could likewise change effective permissions indirectly. Hiding fields alone
would not prevent crafted POSTs or stale User objects saved with every column.
Generic password resets could take over an existing authority holder's account.

## Allowed operations and contracts

The five authority fields are read-only on existing User pages for everyone,
including superusers. A posted authority field is rejected even if its value
matches the current value or is empty. Admin forms also reject these payloads.
Only first_name and last_name are editable on existing accounts. Email is a login
identity, so it and security timestamps are read-only here. Credential/identity
fields injected into profile POSTs are rejected. AccountChangeForm has an explicit
safe input allowlist instead of all model fields; the dedicated admin profile form
contains only the two name fields.

Profile saves explicitly update only those two columns. save_related does not
write User M2Ms and rejects unexpected security fields or inline formsets. Thus
an object loaded before an external flag/permission/identity change cannot restore
those values when saving a profile. Profile edits retain normal Django Admin
LogEntry behavior; they do not pretend to be activity-scoped business audits.

Normal account creation remains available under Django's add/change User permissions,
with normalized email, validated hashed password and optional names. New users
retain model defaults: active, nonstaff, nonsuperuser, no Group/direct permissions
or activity grants. Authority payloads, preassigned PKs and unexpected authority
flags are rejected. This is ordinary account creation, not privileged provisioning.

User deletion, delete_selected, arbitrary bulk actions, list editing and save-as-new
are disabled. Direct delete hooks raise PermissionDenied. Group Admin is read-only,
including name/permissions, add/delete, form submission, bulk POST and save hooks.
No Permission or ActivityPermissionGrant editor is registered. There is no superuser
exception to these restrictions.

The per-account UserAdmin password-reset route is closed for all actors, including
superusers. Self-service password change stays on the existing admin site route:
it verifies the current password, uses Django password validators and session-hash
update, and saves only the password column. It cannot change another account or
write stale security flags. A small AdminSite subclass replaces only the password
form on that existing route; the default site namespace, templates, login/logout,
CSRF and permissions remain. SecurityBoundaryAdminConfig inherits AdminConfig with
the same django.contrib.admin app identity, so no new app/model/migration is added.

## Compatibility and intentional changes

Authentication, email normalization, model manager create_superuser and the
createsuperuser management command remain available. Their privileged behavior is
not replaced by admin UI policy. Existing account creation/profile pages remain;
email editing on existing accounts, credential resets, authority editing and Group
writes are intentionally closed until approved audited security services exist.
The old User admin page regression now distinguishes creation email input from
read-only existing email. No domain model or historical migration is changed.
Draft/grant commands and session assignment are untouched.

## Security scope and remaining work

This boundary covers the installed default AdminSite and registered admins, not
arbitrary third-party/custom admin sites, future endpoints, ModelForms used by
other trusted callers, privileged ORM, Django shell, management code or direct SQL.
Tests deliberately use trusted ORM fixtures to simulate external state changes;
that is not an assertion that such paths are blocked. No global User save override,
DB trigger, signal interception or last-administrator guarantee is introduced.

The general AccountChangeForm input allowlist is not a substitute for audited
security services or safe persistence in another endpoint; only these admin hooks
bound their writes. CSRF/authentication remain required. Own-password changes keep
the existing self-service contract and do not constitute a new activity audit API.

Bootstrap, recovery, authority transfer and complete last-administrator protection
remain future work. Global User/permission/Group mutation services still need an
approved authority policy, atomic audit, locking and revocation/continuity contract.
PostgreSQL concurrent-write behavior and external writers remain unproven. The
partial-write regressions verify that stale profile/password objects preserve
current authority state; SQLite is not evidence of PostgreSQL locking correctness.
No operational grant UI, API, scoring, AI or Laravel/FastAPI changes are included.

## Verification

Final verification: all 435 tests passed (414 baseline tests retained/adapted,
21 new security tests) in 106.559 seconds. The focused accounts suite passed
52 tests. Coverage includes superuser and delegated-staff tampering, read-only
Groups, POST/bulk/delete/save-as-new, form and save hooks, stale profile/password
objects, unchanged authority/M2Ms/grants, self-password authentication, CSRF,
createsuperuser, and unregistered grant/permission editors.
Standalone system check reported no issues; makemigrations --check --dry-run
reported No changes detected. Tracked and new-file whitespace checks were clean.
Synthetic photo regressions required approved Windows temporary-folder access.
Commands use explicit
config.settings.test, SQLite in memory, disabled dotenv and disabled bytecode writes.
No real PostgreSQL is used and no commit/push is performed.
