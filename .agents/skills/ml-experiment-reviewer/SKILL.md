---
name: ml-experiment-reviewer
description: Review an existing ML experiment for leakage, evaluation validity, baseline quality, reproducibility, and unsupported performance claims.
---

# ML experiment reviewer

Use this for an experiment review, not as a requirement to redesign every ordinary ML edit. Inspect the dataset description, split code, preprocessing, training configuration, saved results, and inference contract. Keep review read-only unless fixes are requested.

Trace each reported result to its code and data partition. Check target availability at prediction time, duplicated entities, temporal overlap, group leakage, target-derived features, and preprocessing or feature selection fit before splitting. Confirm hyperparameter search does not repeatedly consume the final test set.

Compare against a meaningful dummy or simple baseline on identical splits and metrics. Evaluate metric suitability for imbalance or regression costs, sample counts, slice performance, and uncertainty. Distinguish training, validation, cross-validation, and held-out test results. Missing evidence is an unverified claim, not automatically proof of a defect.

Check provenance, data quality, random seeds, dependency versions, artifact metadata, feature schema, and the ability to reproduce a small run. Load only trusted model artifacts. Where execution is feasible, rerun the provided evaluation or a bounded fixture and compare measured results; do not claim reproducibility from code inspection alone.

Report actionable findings in severity order with file/location evidence, the impact on conclusions, and a concrete correction. Separate confirmed defects, assumptions, and unavailable checks. Never fabricate performance, silently alter results, or present a smoke-test dataset score as product quality.
