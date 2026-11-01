# MVP-03: detection contract v1

Run from this directory with its isolated Python environment. Install
requirements.txt (runtime) or requirements-test.txt (runtime + HTTP tests).
Versions are pinned to the existing Python 3.13 environment; pip check passed.
Provision a trusted local .pt checkpoint and set AI_WEIGHTS_PATH to its absolute
path before starting `uvicorn main_api:app`. There is no default model filename,
download fallback, calibration or scoring. Unconfigured/unavailable weights give
HTTP 503 with status failed and failure_code MODEL_UNAVAILABLE.

Weights are executable deployment artifacts, never request uploads. Startup
hashes the local file before/after loading, enforces a size limit and verifies
task=detect. model_name reports the loaded network class, not an inferred YOLO
generation/size. weights_sha256 identifies the artifact; runtime_version records
the installed Ultralytics version. checkpoint_runtime_version is checkpoint-
reported metadata only. Static inspection of the ignored local artifact found
a yolov8n.pt reference; YOLO26s is not verified. No real inference/retraining or
accuracy measurement was performed and no weights/dataset is committed.

POST /api/evaluate accepts exactly one multipart file named file. Still JPEG/PNG
only, with declared MIME checked against decoded content. No filename is used as
a filesystem path. The raster is decoded RGB -> BGR with geometry unchanged:
preprocessing_version=decoded-rgb-to-bgr-no-exif-v1. EXIF rotation, perspective
transform, cropping and output coordinate rounding are deliberately absent.
Ultralytics internal letterboxing must return boxes in matching orig_shape.
See the [Results/Boxes contract](https://docs.ultralytics.com/reference/engine/results/)
and [prediction interface](https://docs.ultralytics.com/modes/predict/).

Successful JSON: status succeeded, schema_version 1, detections array. Each row
has a run-local unique id, integer class_id, finite confidence in [0,1], and
bbox_xyxy [x1,y1,x2,y2] in analysis pixels. Positive-area boxes must fit the image;
invalid/missing results fail instead of being clipped/dropped. Empty boxes mean
successful detections [], while inference/contract errors return status failed
and a failure_code with non-2xx HTTP status. Failure responses are not raw_result;
a future Django bridge must map them to failed/NULL and verify matching analysis
dimensions/preprocessing. No Django HTTP bridge or DetectionRun writer is added.
The full success envelope also passes Django's raw detection validator.

Success metadata includes analysis_width_px/analysis_height_px, input_sha256,
model/weights/runtime/adapter identity, and the explicit parameters passed to
predict. No raw exception details, server paths, scores or public image URLs are
returned. This changes the old shots/centroid response; legacy Laravel consumers
are not updated or claimed compatible.

Every Settings field can be set using AI_<UPPERCASE_FIELD_NAME>. Technical defaults:
confidence 0.2 (preserved), iou 0.7, image_size 640, max_detections 300, device cpu;
these are not optimized/validated field thresholds. Hard configuration ceilings
prevent unbounded overrides. Default limits: 8 MiB upload plus 64 KiB multipart
overhead, 16M pixels, 8192 maximum side, one in-flight request, 15-second upload
deadline and 1 GiB weights. Request bytes are counted before multipart spooling,
including absent/false Content-Length; parser file/field counts are bounded.
Image checks precede raster allocation; decoding/inference run outside the event
loop with a nonblocking native-model lock. Limits are per process, not a global
cluster quota. There is no hard cancellation of native inference: use deployment
worker/process memory/time limits, restricted network access and appropriate
rate limits before public operation. Authentication policy is unchanged.

Tests: `python -B -m unittest discover -s . -p 'test_*.py' -v`. They use fake
Ultralytics-shaped tensors, synthetic image/checkpoint bytes and no real weights,
torch import or GPU. A separate Django test imports only the standard-library
adapter and checks the actual Django validator. The pinned httpx compatibility
mode currently emits a Starlette test-client deprecation warning; it passes tests
and should be refreshed deliberately with that dependency transition.

Verification: 27 AI tests and all 564 isolated SQLite Django tests passed
(561 existing + 3 adapter compatibility tests). System check, migration drift,
pip check and whitespace checks passed; the local weights digest was unchanged.
