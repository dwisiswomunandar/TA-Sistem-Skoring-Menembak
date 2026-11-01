# MVP-02: audited draft scoring

`assessments.scoring_services.score_draft` accepts keyword-only actor, session_id,
assessment_id, expected_edit_version, expected_evidence_versions, operation_id,
reason and optional using. No caller scores, point/ring lists, matrix or caliber
are accepted. This is a draft calculation cache, never verification/locking.

The existing assessment.draft_updated policy is required: current active trusted
User, assessments.change_assessment global permission, matching active activity
grant and assigned_assessor. Preliminary and decisive in-transaction checks use
the existing policy without changing grant/bootstrap/transfer or permission sets.

expected_evidence_versions must contain exactly calibration, target_version and
scoring_rule_version; each has positive integer id and edit_version. Values are
compared with locked persisted rows. Target's aggregate token covers ring writes.
Existing retired references are usable with their current explicit versions;
new retired reference selection remains governed by the existing draft commands.

Lock requests: TrainingSession -> ActivityParticipation -> Assessment -> all
ShotPoint by PK -> TargetPhoto -> optional DetectionRun -> Calibration ->
TargetVersion -> all TargetRing by PK -> ScoringRuleVersion -> User -> grants.
Calibration-before-target and target-before-rings match existing writers. No
joined locking queries or earlier domain locks are introduced after this suffix.
Audit's session/participation locks are reentrant. Entire ring/point sets come
from the database under locks; session history, contexts, model constraints,
accepted calibration, published geometry, supported rule and AI provenance are
revalidated before the unchanged MVP-01 pure engine is invoked.

One atomic transaction saves every computed point score, total, engine version,
scored_at, scoring input SHA-256 and allowlisted scoring_context, increments the
Assessment version once and appends one session/assessment-scoped AuditEvent
(assessment.draft_scored). Excluded scores remain NULL; included misses are 0.
scored_edit_version equals the resulting version; the input snapshot records the
preceding version actually scored. Decimal/matrix inputs use canonical decimal
strings and transformed coordinates use numerator/denominator strings. JSON has
no Decimal/Fraction/float values, secrets, storage keys or raw inference parameters.
No rounding, new physical tolerances or changes to MVP-01 hole diameter are made.

The existing UUID ledger binds actor, scope, action, reason and expected versions.
Authorized identical retries return the historical DraftReceipt without computing
or restoring invalidated scores. Current authorization precedes replay; revoked
permission/grants deny replay. Different commands sharing a UUID conflict. Audit,
validation or persistence failure rolls back scores, timestamps and version.

Existing draft/point commands still invalidate the complete cache and audit their
changes. Public model/queryset score-writing and verified/locked guards remain
closed; only this bounded service uses internal Model.save for server results.
Privileged ORM/shell/SQL remain outside the audit guarantee. Photo metadata is
validated; original bytes are not reread during scoring. Field calibration and
physical rules still require observation. SQLite tests do not prove PostgreSQL
locking: test concurrent scoring vs edit/add/delete, evidence retirement,
grant/global revocation, duplicate UUIDs and FK/deadlock behavior on an isolated
PostgreSQL test database before operational enablement. No schema migration,
endpoint, UI, AI, official-result transition or security-administration change.

Verification: 118 focused tests and all 561 SQLite regression tests passed
(530 existing + 31 new); system check, migration drift and whitespace checks passed.
