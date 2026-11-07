"""Transparent aggregate metrics; undefined denominators remain null."""

from collections import Counter
from dataclasses import dataclass, field


def ratio(numerator: int | float, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


@dataclass
class Metrics:
    counts: Counter = field(default_factory=Counter)
    normalized_errors: list[float] = field(default_factory=list)
    total_errors: list[float] = field(default_factory=list)
    durations_ms: list[float] = field(default_factory=list)
    known_costs: list[float] = field(default_factory=list)

    def criterion(self, label: dict, prediction: dict | None, rule, normalized_error) -> None:
        c = self.counts
        c["labeled"] += 1
        resolved = prediction is not None and prediction["decision_status"] == "evaluated"
        c["resolved"] += int(resolved)
        correct = resolved and (
            prediction["applicability"] == label["applicability"]
            and prediction["value"] == label["value"]
            and isinstance(prediction["value"], bool) == isinstance(label["value"], bool)
        )
        c["correct"] += int(correct)
        if resolved:
            predicted_na = prediction["applicability"] == "not_applicable"
            expected_na = label["applicability"] == "not_applicable"
            c["false_na"] += int(predicted_na and not expected_na)
            c["missed_na"] += int(expected_na and not predicted_na)
            if rule.answer_type == "flag":
                if not predicted_na and not expected_na:
                    c["flag_cases"] += 1
                    actual, predicted = label["value"], prediction["value"]
                    c["tp"] += int(actual and predicted)
                    c["fp"] += int(not actual and predicted)
                    c["fn"] += int(actual and not predicted)
            if rule.answer_type == "scale":
                if normalized_error is None:
                    c["scale_excluded_na"] += 1
                else:
                    self.normalized_errors.append(normalized_error)
            # This optional review happens after independent human labels are frozen.
            supported = label.get("prediction_evidence_correct")
            if type(supported) is bool:
                c["evidence_reviewed"] += 1
                c["evidence_supported"] += int(supported)

    def case(self, metadata: dict, completed: bool, total_error: float | None) -> None:
        c = self.counts
        c["conversations"] += 1
        c["completed"] += int(completed)
        c["failed"] += int(metadata["status"] in {"failed", "interrupted"})
        c["calls"] += len(metadata["attempts"])
        c["retries"] += max(0, len(metadata["attempts"]) - 1)
        if total_error is not None:
            self.total_errors.append(total_error)
        if metadata["elapsed_ms"] is not None:
            self.durations_ms.append(metadata["elapsed_ms"])
        if metadata["estimated_cost_usd"] is not None:
            self.known_costs.append(metadata["estimated_cost_usd"])

    def report(self) -> dict:
        c = self.counts
        result = {
            **{key: c[key] for key in ("labeled", "resolved", "correct", "false_na", "missed_na")},
            "coverage": ratio(c["resolved"], c["labeled"]),
            "accuracy_all_labeled": ratio(c["correct"], c["labeled"]),
            "accuracy_resolved": ratio(c["correct"], c["resolved"]),
            "normalized_scale_mae": ratio(sum(self.normalized_errors), len(self.normalized_errors)),
            "scale_pairs": len(self.normalized_errors),
            "scale_excluded_na": c["scale_excluded_na"],
            "flag_cases": c["flag_cases"],
            "flag_tp": c["tp"],
            "flag_fp": c["fp"],
            "flag_fn": c["fn"],
            "flag_precision": ratio(c["tp"], c["tp"] + c["fp"]),
            "flag_recall": ratio(c["tp"], c["tp"] + c["fn"]),
            "flag_f1": ratio(2 * c["tp"], 2 * c["tp"] + c["fp"] + c["fn"]),
            "semantic_evidence_reviewed": c["evidence_reviewed"],
            "semantic_evidence_supported_fraction": ratio(
                c["evidence_supported"],
                c["evidence_reviewed"],
            ),
        }
        if c["conversations"]:
            result.update(
                conversations=c["conversations"],
                completed=c["completed"],
                failed=c["failed"],
                completion_rate=ratio(c["completed"], c["conversations"]),
                total_mae=ratio(sum(self.total_errors), len(self.total_errors)),
                total_pairs=len(self.total_errors),
                calls=c["calls"],
                retries=c["retries"],
                mean_duration_ms=ratio(sum(self.durations_ms), len(self.durations_ms)),
                duration_cases=len(self.durations_ms),
                known_cost_cases=len(self.known_costs),
                total_cost_usd=(
                    sum(self.known_costs) if len(self.known_costs) == c["conversations"] else None
                ),
            )
        return result
