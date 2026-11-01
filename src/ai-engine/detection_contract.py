"""Ultralytics Boxes -> Django DetectionRun schema v1, without framework imports."""

import math

ADAPTER_VERSION = "ultralytics-boxes-v1"


class DetectionContractError(ValueError):
    pass


def _finite(value):
    return type(value) is int or (type(value) is float and math.isfinite(value))


def detection_result(results, *, width, height, max_detections):
    """One analysis image; no clipping/rounding or missing-result success fallback.

    IDs identify rows within this run, not tracks across images/runs.
    """
    if any(type(v) is not int or v < 1 for v in (width, height, max_detections)):
        raise DetectionContractError("Positive analysis dimensions and detection limit are required.")
    if not isinstance(results, (list, tuple)) or len(results) != 1:
        raise DetectionContractError("Exactly one image result is required.")
    result = results[0]
    shape, original_boxes = getattr(result, "orig_shape", None), getattr(result, "boxes", None)
    if not isinstance(shape, (tuple, list)) or tuple(shape) != (height, width) or original_boxes is None:
        raise DetectionContractError("Results must contain boxes in the analysis image coordinate space.")
    if len(original_boxes) > max_detections:
        raise DetectionContractError("Detection limit exceeded.")
    try:
        boxes = original_boxes.cpu()
        coordinates, confidences, classes = boxes.xyxy.tolist(), boxes.conf.tolist(), boxes.cls.tolist()
    except (AttributeError, TypeError, ValueError) as error:
        raise DetectionContractError("Invalid detection tensors.") from error
    if not (len(coordinates) == len(confidences) == len(classes) == len(boxes)):
        raise DetectionContractError("Detection tensor dimensions do not match.")
    detections = []
    for index, (box, confidence, class_id) in enumerate(zip(coordinates, confidences, classes), 1):
        if (not isinstance(box, list) or len(box) != 4 or any(not _finite(v) for v in box)
                or not _finite(confidence) or not 0 <= confidence <= 1
                or not _finite(class_id) or class_id < 0 or class_id != int(class_id)):
            raise DetectionContractError("Invalid box, confidence or class ID.")
        x1, y1, x2, y2 = box
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            raise DetectionContractError("Bounding box must have positive area inside the analysis image.")
        detections.append({"id": f"d{index:06d}", "class_id": int(class_id),
                           "confidence": confidence, "bbox_xyxy": box})
    return {"schema_version": 1, "detections": detections}
