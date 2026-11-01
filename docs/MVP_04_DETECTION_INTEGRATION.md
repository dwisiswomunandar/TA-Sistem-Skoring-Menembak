# MVP-04: audited Django to FastAPI detection

`assessments.detection_services.detect_photo` takes keyword-only actor, session_id,
photo_id, operation_id UUID, reason and optional using. Actor must come from trusted
Django authentication, never a client actor ID. It reuses assessment.draft_updated
authorization: current global change_assessment permission, active activity grant
and assigned_assessor. No grant/scoring workflow or public endpoint is changed.

The entry point requires autocommit and rejects outer atomic/ATOMIC_REQUESTS/manual
transactions. Invoke it from a non-atomic server boundary or job, not from a caller
transaction that would retain locks during HTTP. No worker/queue is added here.

## Explicit deployment configuration

DETECTION_SERVICE defaults to None. New operations fail closed until protected
deployment settings supply exactly endpoint, provenance and timeouts. Example:

```python
DETECTION_SERVICE = {
    "endpoint": "http://127.0.0.1:8001/api/evaluate",
    "provenance": APPROVED_DETECTOR_PROVENANCE,
    "timeouts": {"connect": 3, "read": 30, "write": 10, "pool": 1, "total": 60},
}
```

APPROVED_DETECTOR_PROVENANCE must contain model_name, weights_sha256,
runtime_version, adapter_version and parameters from an independently verified
deployment manifest. Never learn/change the expected identity from the response
being validated. The adapter must be ultralytics-boxes-v1. Parameters are exactly
the 12 explicit MVP-03 predict keys (conf, iou, imgsz, max_det, device, half,
augment, rect, batch, stream, save, verbose) with valid bounded values/fixed flags.
No real detector manifest or enabled service configuration is added to Git.

Endpoints must use a literal loopback/RFC1918/IPv6 ULA address and the exact
/api/evaluate path, without credentials/query/fragment. HTTP is loopback-only;
other private addresses require HTTPS with trusted certificate verification.
DNS hostnames are deliberately unsupported at this checkpoint. No environment
proxies, redirects, automatic retries, shared cookies or persistent connections.
One connection per request and two admitted operations per Django process; fleet
quotas remain a deployment concern. Request/response limits are 8/2 MiB; image
limits match MVP-03 (16M pixels, 8192 maximum side). Compressed responses are
rejected and actual streamed bytes are counted independently of Content-Length.
[HTTPX timeouts](https://www.python-httpx.org/advanced/timeouts/) are per IO;
the total budget is additionally checked between response chunks. It is not a
hard process deadline and may overrun while a native/socket call is in progress.
See [resource limits](https://www.python-httpx.org/advanced/resource-limits/) and
[environment controls](https://www.python-httpx.org/environment_variables/).

## State, bytes and atomic audit

First short transaction: TrainingSession -> ActivityParticipation -> TargetPhoto
-> existing DetectionRun (replay only) -> User -> activity grants. It creates a
pending run using pinned provenance, saves running, then inserts one root audit
(detection.started, caller UUID), atomically. The intermediate pending state is
recorded in audit lifecycle metadata; it is not separately published. Locks on
the newly inserted run and audit's already-held parents are reentrant.

Outside every DB transaction: read the private original into a bounded snapshot,
verify SHA-256, size, decoded dimensions, MIME and still-image validity, and send
those same verified bytes. No public storage URL is requested. Only the explicit
decoded-rgb-to-bgr-no-exif-v1 contract with original=analysis dimensions is supported;
old photos are not silently resized/retagged. Success additionally rechecks the
original after HTTP. The existing wrapper is not protection against privileged
filesystem/SQL changes, including the small verification-to-commit race.

Response validation requires schema 1, succeeded status, complete matching
dimensions/preprocessing/input SHA, exact pinned provenance/parameters and valid
bounded unique detections. Duplicate JSON keys, nonfinite numbers, unsupported
metadata, excessive output and invalid boxes fail. Failed/invalid responses never
become empty detections and are not stored as raw_result.

Second short transaction uses the same parent order with DetectionRun before User,
rechecks current authorization, photo/session identity and the entire running run
snapshot, then saves succeeded/failed plus one terminal audit atomically. The
terminal UUID is uuid5(caller_uuid, "detection.finished"), caused_by_event is the
root audit. Scalar audit versions are NULL because DetectionRun has no edit token;
exact audited state and model terminal immutability guard stale writes instead.
There is no new model/migration, Assessment selection, point creation or score write.

HTTP timeout/error/invalid input yields an audited failed run with raw_result NULL.
Initial audit failure rolls back both initial transitions and the run. Terminal
audit failure rolls back only the terminal phase, preserving committed running
history. Remote inference cannot be rolled back as a database effect.

The root audit is the UUID ownership ledger. Current authorization precedes every
receipt replay. Identical terminal retries return historical receipts, including
after configuration changes/disablement. Running retries return progress without
resending HTTP. Different commands/actors/scopes sharing a UUID conflict. A run's
configuration snapshot governs that attempt; rotations apply to new operations.
Receipts do not revalidate current file bytes or repair/restart incomplete runs.

Crashes, revoked authority/context drift during HTTP or failed terminal audit can
leave running records. An audited recovery/cancellation process is still needed;
automatic resending would risk duplicate remote inference. Public low-level model
and privileged ORM/SQL paths do not automatically audit; unexpected unaudited state
changes are rejected, not overwritten. Real AI accuracy/weights family, PostgreSQL
concurrency/deadlock tests and operational TLS/network/time/memory controls remain
unverified. FastAPI was not started or exposed and no real weights/GPU were used.

Verification: 35 focused tests, all 599 isolated SQLite Django tests (564 existing
and 35 new) and 27 mocked FastAPI regression tests passed. System check, migration
drift, dependency and whitespace checks passed. No operational database was used.
