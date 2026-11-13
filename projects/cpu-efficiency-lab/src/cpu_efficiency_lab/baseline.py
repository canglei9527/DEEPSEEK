"""Paired idle-baseline estimates for battery-side load increments."""

from dataclasses import dataclass
import math
from typing import Optional, Sequence, Tuple

from .config import BASELINE_MAX_DRIFT_FRACTION, BASELINE_MAX_DRIFT_W, BASELINE_MAX_SAMPLE_GAP_S


@dataclass(frozen=True)
class BaselineEstimate:
    before_w: Optional[float]
    after_w: Optional[float]
    estimated_w: Optional[float]
    coverage_s: float
    drift_w: Optional[float]
    observed_range_w: Optional[float]
    incremental_w: Optional[float]
    valid: bool
    reason: str


def _window(points):
    ordered = sorted(points, key=lambda point: point[0])
    energy = coverage = weighted_time = 0.0
    values = [p for _, p in ordered if p is not None and math.isfinite(p) and p >= 0]
    for (t0, p0), (t1, p1) in zip(ordered, ordered[1:]):
        if (p0 is None or p1 is None or not math.isfinite(p0) or not math.isfinite(p1)
                or min(p0, p1) < 0 or not 0 < t1 - t0 <= BASELINE_MAX_SAMPLE_GAP_S):
            continue
        dt = t1 - t0
        energy += (p0 + p1) * dt / 2
        weighted_time += (t0 + t1) * dt / 2
        coverage += dt
    return (energy / coverage if coverage else None,
            weighted_time / coverage if coverage else None, coverage, values)


def paired_baseline(before: Sequence[Tuple[float, Optional[float]]],
                    after: Sequence[Tuple[float, Optional[float]]],
                    load_w: Optional[float], start_s: float, end_s: float) -> BaselineEstimate:
    """Interpolate idle draw in time; reject drift and sub-noise increments.

    The range is an observed screening bound, never a confidence interval or a
    claim that battery-side subtraction isolates the CPU power rail.
    """
    pre, pre_time, pre_duration, pre_values = _window(before)
    post, post_time, post_duration, post_values = _window(after)
    coverage = pre_duration + post_duration
    baseline = drift = observed_range = delta = None
    reason = ""
    if pre is None or post is None:
        reason = "missing paired idle baseline"
    elif (not math.isfinite(start_s) or not math.isfinite(end_s) or end_s <= start_s
          or not pre_time < start_s < end_s < post_time):
        reason = "idle windows do not bracket load"
    else:
        fraction = ((start_s + end_s) / 2 - pre_time) / (post_time - pre_time)
        baseline = pre + (post - pre) * fraction
        drift = abs(post - pre)
        values = pre_values + post_values
        observed_range = max(values) - min(values)
        if load_w is not None and math.isfinite(load_w):
            delta = load_w - baseline
        if delta is None or delta <= 0:
            reason = "non-positive incremental battery power"
        elif drift > max(BASELINE_MAX_DRIFT_W, BASELINE_MAX_DRIFT_FRACTION * (pre + post) / 2):
            reason = "idle baseline drift exceeds screening threshold"
        elif delta <= observed_range:
            reason = "incremental power not above observed idle range"
    return BaselineEstimate(pre, post, baseline, coverage, drift, observed_range,
                            delta, not reason, reason)
