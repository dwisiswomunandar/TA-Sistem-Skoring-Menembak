# ITER-05 - Provisional shooting parameter policy

Status: PROVISIONAL, implementing DEC-2026-11-02-01; not a certified field standard.
Activity remains the training activity, while TrainingSession.weapon_type is the
technical shooting category. No schema, migration, endpoint or AI change.

## Shared input contract

activities.parameters.resolve_parameters is a query-free keyword-only resolver.
For laras_panjang the operator selects berdiri/duduk/tiarap; the server derives
100 m and 5.56 mm. For pistol the operator selects 15/20/25 m; the server derives
berdiri and 9.00 mm. None/empty means absent: only derived values may be absent.
Explicit matching values are allowed; conflicting/invalid/nonfinite values are
rejected, never silently repaired. Model/form storage precision checks still apply.
No default weapon, long-gun stance or pistol distance is selected.

## Model and historical compatibility

TrainingSession.clean_fields resolves complete NEW input before required-field
validation; supplied field values are validated before any derived replacement.
clean also checks policy for direct callers. save retains HistoricalParent's
locked full_clean, optimistic token, eligibility and historical guards.

Policy applies to new sessions or changed parameter/identity context: the four
parameter fields, participation, session number, assigned assessor or creator.
Comparison uses field-normalized values, not a caller flag or a new DB column.
Unchanged persisted context retains all baseline field/caliber/history validation,
but is not rejected merely for legacy distance/stance. No historical values,
snapshots, audits, target/rule configurations or scores are rewritten. A legacy
row whose context is changed must satisfy current policy, and historical guards
still reject any frozen context change. Privileged SQL/ORM remains outside guards.

## Tests and boundaries

Current-model synthetic fixtures now use valid 15 m pistol sessions. Historical
migration fixtures retain their old values. Dedicated legacy fixtures explicitly
exercise unchanged full_clean/no-op saves, photo/assessment/audit freezing and
scoring/detection service compatibility. No new inference or field validation is
claimed. PostgreSQL concurrency remains unverified; tests use isolated SQLite.

## Actual verification - 11 November 2026

87 focused domain/parameter/history tests passed. The complete Django regression
suite passed all 613 tests in 97.653 seconds (599 baseline plus 14 new tests).
Explicit config.settings.test, disabled dotenv and bytecode writes, temporary
synthetic photos and SQLite in memory were used; no operational database or AI
service was accessed. System check passed; migration drift reported No changes
detected; tracked and untracked whitespace checks passed.

The initial focused run had three failures in new historical assertions: no-op
save already refreshes TrainingSession.updated_at through auto_now. The tests now
assert full_clean makes no writes, then permit only that existing save timestamp
change while comparing all other session values, versions, snapshots, audits and
configurations exactly. No production timestamp behavior was changed.
