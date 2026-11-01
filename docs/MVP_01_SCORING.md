# MVP-01: pure deterministic scoring

`assessments.scoring.score_target` accepts immutable input snapshots and returns
per-point results plus the included total. It does no ORM/IO, authentication,
scoring persistence, audit or verified/locked transition.

The only supported rule tuple is method `inward`, algorithm_version
`inward-circle-v1`, boundary_policy `touch-inclusive-v1`, numeric_precision
`exact-rational-decimal-v1`. Other descriptive/legacy policies fail closed;
existing published rules are never rewritten. These are technical contracts,
not certification of physical targets or field procedures.

Callers must explicitly supply `analysis-px-to-target-mm-center-origin-v1`,
the baseline v1.1 section 3.4 contract: homogeneous calibration maps analysis
pixels to target mm with ring centre (0,0). No image midpoint is inferred.
For each ring, test d_squared <= (outer_radius_mm + caliber_mm/2)^2;
the first inward ring wins, including exact tangency. No epsilon, sqrt or rounding.
Target dimensions validate ring fit; they do not clip the circular bullet disk.

Integers/Decimals use exact rational values; JSON floats use their shortest
round-trip decimal representation, not binary arithmetic. Inputs have technical
numeric-size limits (4096 integer bits, 1000 Decimal digits/absolute exponent).
Output mm coordinates are Fractions; a future serializer must explicitly encode
numerator/denominator or apply a separately versioned display-rounding policy.
Excluded points return score None; included misses return 0; empty sets total 0.

`scoring_inputs.scoring_input_from_models` copies fully loaded, context-matching
snapshots without queries/writes; lazy querysets are rejected. It requires accepted calibration, previously
published target/rule and session caliber; supplied score fields are ignored.
It does not prove snapshot freshness, completeness of supplied ring/point sets or
access authorization. A future atomic audited service must enforce those under
locks, establish provenance and recheck versions before any official score write.
