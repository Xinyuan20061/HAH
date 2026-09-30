"""Generic repetition counters and rule-based quality scorers (Work Package G).

This module turns a pose time-series (the per-frame rows produced by
``processors/motion.py::extract_metrics``) into the ``measurements`` group of the
MotionWorkerResultV2 receipt (contract §5)::

    {"available": True, "exercise_id": "bicep_curl", "reps": 5,
     "duration_ms": 16000, "quality": {...}}

Design principles (contract §5 / spec §3.1 / §5.4):

  * A repetition is counted ONLY when a full cycle
    (rest -> excursion -> rest) completes. An excursion that never returns to
    the rest band is flagged ``incomplete_cycle=True`` and is NOT added to
    ``reps`` (the exercise may still be recognised, but the count stays short).
  * Static holds (plank / side plank) do NOT require a cycle: they are scored
    on duration and posture stability, never on "completing a rep".
  * Everything is a 2-D heuristic. Scores are coaching hints, not clinical or
    kinematic truth. This module makes NO external model / network call.

The module is intentionally decoupled from ``motion_unified.py``: it is a pure
function of ``(exercise_id, samples, sampled_frames, duration_ms)`` so it can be
unit-tested offline with synthetic pose rows and later wired into the V2
measurements group by the integration owner.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import mean, pstdev

from .analyzers import angle

# --- Counter / scorer version strings handed to the catalog (contract §9) -----
COUNTER_VERSION = "hysteresis_cycle@v1"
SCORER_VERSION = "rule_quality@v1"
STATIC_COUNTER_VERSION = "static_hold_timer@v1"
STATIC_SCORER_VERSION = "static_posture_quality@v1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _finite(v) -> bool:
    return isinstance(v, (int, float)) and math.isfinite(v)


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _skeleton_point(skeleton: list[dict], name: str):
    for item in skeleton or []:
        if isinstance(item, dict) and item.get("id") == name:
            return item
    return None


def _elbow_angles_from_skeleton(skeleton: list[dict]):
    """Return (left_elbow_deg, right_elbow_deg) reconstructed from the skeleton,
    or (None, None) when the needed joints are absent/low-visibility.

    The per-frame row already carries the *chosen* side elbow angle; symmetry
    needs BOTH sides, which only the joint list provides.
    """
    if not isinstance(skeleton, list) or not skeleton:
        return None, None

    def _pt(name):
        p = _skeleton_point(skeleton, name)
        if not p or not _finite(p.get("x")) or not _finite(p.get("y")):
            return None
        if float(p.get("visibility", 0.0)) < 0.4:
            return None
        return (float(p["x"]), float(p["y"]))

    lsh, lel, lwr = _pt("left_shoulder"), _pt("left_elbow"), _pt("left_wrist")
    rsh, rel, rwr = _pt("right_shoulder"), _pt("right_elbow"), _pt("right_wrist")
    left = angle(lsh, lel, lwr) if all(p is not None for p in (lsh, lel, lwr)) else None
    right = angle(rsh, rel, rwr) if all(p is not None for p in (rsh, rel, rwr)) else None
    return left, right


# ---------------------------------------------------------------------------
# Per-exercise configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DynamicConfig:
    """How to count one dynamic (repetitive) exercise from a single angle series.

    ``rest_high=True``  : the resting / completed position is the HIGH value
                          (e.g. bicep curl: elbow extended ~165°). An excursion
                          dips BELOW ``lo`` and a rep completes when it returns
                          to >= ``hi``.
    ``rest_high=False`` : the resting / completed position is the LOW value
                          (e.g. shoulder press: elbow bent ~95° at the bottom).
                          An excursion rises ABOVE ``hi`` and a rep completes
                          when it returns to <= ``lo``.
    """

    signal: str               # which row field drives the cycle detector
    hi: float                 # rest-band boundary (high side)
    lo: float                 # excursion-band boundary (low side)
    rest_high: bool = True
    min_rep_seconds: float = 0.5
    interrupt_gap: float = 0.75
    target_extreme: float = 0.0   # "good" extreme; used for amplitude score
    amplitude_band: float = 60.0  # range the amplitude score normalises over
    trunk_key: str = "trunk"
    label: str = ""


@dataclass(frozen=True)
class StaticConfig:
    """How to score a static hold exercise (no reps)."""

    name: str
    duration_min_seconds: float = 2.0
    stability_band: float = 15.0   # trunk/body-line std that maps to ~60 pts
    posture_keys: tuple[str, ...] = ("body_line", "trunk")
    label: str = ""


# Dynamic upper-body priority set (P1). Thresholds are 2-D coaching hints.
DYNAMIC_EVALUATORS: dict[str, DynamicConfig] = {
    # P1 — highest frequency upper-body lifts.
    "bicep_curl": DynamicConfig(
        signal="arm_vw", hi=140.0, lo=100.0, rest_high=True,
        target_extreme=80.0, amplitude_band=70.0,
        label="哑铃弯举",
    ),
    "hammer_curl": DynamicConfig(
        # Neutral-grip curl: same elbow-flexion kinematics in 2-D.
        signal="arm_vw", hi=140.0, lo=100.0, rest_high=True,
        target_extreme=85.0, amplitude_band=70.0,
        label="锤式弯举",
    ),
    "front_raise": DynamicConfig(
        # arm_abduction = 180 - shoulder_angle; arm down => ~178, raised => ~95.
        signal="arm_abduction", hi=158.0, lo=128.0, rest_high=True,
        target_extreme=110.0, amplitude_band=80.0,
        label="前平举",
    ),
    "lateral_raise": DynamicConfig(
        signal="arm_abduction", hi=158.0, lo=128.0, rest_high=True,
        target_extreme=110.0, amplitude_band=80.0,
        label="侧平举",
    ),
    "shoulder_press": DynamicConfig(
        # Rest between reps = elbows bent ~95° at shoulder (LOW elbow angle);
        # the excursion presses the elbows EXTENDED (~170°, HIGH).
        signal="arm_vw", hi=145.0, lo=115.0, rest_high=False,
        target_extreme=165.0, amplitude_band=70.0,
        label="肩推",
    ),
    "row": DynamicConfig(
        # Start arms extended (elbow ~165°, HIGH); pull elbow back to ~80° (LOW).
        signal="arm_vw", hi=145.0, lo=110.0, rest_high=True,
        target_extreme=90.0, amplitude_band=80.0,
        label="划船",
    ),
}

# Static holds (P1-adjacent; spec §5.4: duration + posture, no cycle).
STATIC_EVALUATORS: dict[str, StaticConfig] = {
    "plank": StaticConfig(
        name="plank", duration_min_seconds=2.0, stability_band=12.0,
        posture_keys=("body_line", "trunk"), label="平板支撑",
    ),
    "side_plank": StaticConfig(
        name="side_plank", duration_min_seconds=2.0, stability_band=14.0,
        posture_keys=("trunk",), label="侧平板",
    ),
}


# ---------------------------------------------------------------------------
# Dynamic cycle counting
# ---------------------------------------------------------------------------

@dataclass
class CycleResult:
    reps: int
    cycles: list[dict] = field(default_factory=list)
    incomplete: bool = False
    interrupted: bool = False
    extreme_values: list[float] = field(default_factory=list)
    valid_rate: float = 0.0
    visibility_mean: float = 0.0
    trunk_std: float = 0.0


def _valid_dynamic_rows(rows: list[dict], signal: str, trunk_key: str) -> list[dict]:
    out = []
    for row in rows:
        if not _finite(row.get("visibility")):
            continue
        if float(row["visibility"]) < 0.5:
            continue
        if not _finite(row.get(signal)):
            continue
        out.append(row)
    out.sort(key=lambda r: float(r["t"]))
    return out


def count_dynamic_cycles(rows: list[dict], cfg: DynamicConfig) -> CycleResult:
    """Hysteresis cycle detector. A rep is a completed rest->excursion->rest.

    Returns a CycleResult; ``reps`` only counts *complete* cycles.
    """
    valid = _valid_dynamic_rows(rows, cfg.signal, cfg.trunk_key)
    sampled = len(rows)
    valid_rate = len(valid) / sampled if sampled else 0.0
    vis_mean = mean(float(r["visibility"]) for r in valid) if valid else 0.0
    trunk_vals = [float(r[cfg.trunk_key]) for r in valid if _finite(r.get(cfg.trunk_key))]
    trunk_std = pstdev(trunk_vals) if len(trunk_vals) >= 2 else 0.0

    result = CycleResult(reps=0, valid_rate=round(valid_rate, 3),
                         visibility_mean=round(vis_mean, 3),
                         trunk_std=round(trunk_std, 2))
    if len(valid) < 4 or valid_rate < 0.3:
        return result

    # State machine. rest_high: rest at >=hi, excursion below lo.
    # rest_low: rest at <=lo, excursion above hi.
    state = "seek_rest"     # haven't observed the rest band yet
    rest_row = None
    extreme_row = None
    prev_t = None
    interrupted = False

    for row in valid:
        t = float(row["t"])
        v = float(row[cfg.signal])

        # Camera cut / long occlusion: cannot join across it.
        if prev_t is not None and t - prev_t > cfg.interrupt_gap:
            state = "seek_rest"
            rest_row, extreme_row = None, None
            interrupted = True
        prev_t = t

        if state == "seek_rest":
            if (cfg.rest_high and v >= cfg.hi) or ((not cfg.rest_high) and v <= cfg.lo):
                rest_row, state = row, "in_rest"
        elif state == "in_rest":
            if (cfg.rest_high and v < cfg.lo) or ((not cfg.rest_high) and v > cfg.hi):
                extreme_row, state = row, "in_excursion"
        elif state == "in_excursion":
            # Track the extreme point of the excursion.
            if cfg.rest_high:
                if extreme_row is None or v < float(extreme_row[cfg.signal]):
                    extreme_row = row
            else:
                if extreme_row is None or v > float(extreme_row[cfg.signal]):
                    extreme_row = row
            returned = (cfg.rest_high and v >= cfg.hi) or (
                (not cfg.rest_high) and v <= cfg.lo
            )
            if returned:
                duration = t - float(rest_row["t"])
                if duration >= cfg.min_rep_seconds and extreme_row is not None:
                    result.reps += 1
                    result.cycles.append({
                        "start_t": round(float(rest_row["t"]), 3),
                        "extreme_t": round(float(extreme_row["t"]), 3),
                        "end_t": round(t, 3),
                        "duration": round(duration, 3),
                        "extreme_value": round(float(extreme_row[cfg.signal]), 1),
                    })
                    result.extreme_values.append(float(extreme_row[cfg.signal]))
                rest_row, extreme_row, state = row, None, "in_rest"

    if state == "in_excursion":
        result.incomplete = True
    result.interrupted = interrupted
    return result


# ---------------------------------------------------------------------------
# Quality scoring
# ---------------------------------------------------------------------------

def _symmetry_from_rows(rows: list[dict]) -> tuple[float | None, int]:
    """Mean |left-elbow - right-elbow| across frames that expose both sides.

    Returns (mean_deviation, frames_used). (None, 0) when the skeleton does not
    expose both elbows (e.g. side view) — symmetry is then reported as not
    measurable rather than guessed.
    """
    diffs = []
    for row in rows:
        le, re = _elbow_angles_from_skeleton(row.get("skeleton"))
        if le is not None and re is not None:
            diffs.append(abs(le - re))
    if not diffs:
        return None, 0
    return round(mean(diffs), 1), len(diffs)


def score_dynamic_quality(cycles: CycleResult, cfg: DynamicConfig,
                          rows: list[dict]) -> dict:
    """Build the explainable quality block for a dynamic exercise."""
    basis: list[str] = []

    # --- Amplitude: how deep / far the reps reach the target extreme. -------
    if cycles.extreme_values:
        ext = cycles.extreme_values
        if cfg.rest_high:
            # Lower extreme = deeper. target_extreme is the "good" low value.
            gap = mean([max(0.0, e - cfg.target_extreme) for e in ext])
        else:
            # Higher extreme = further. target_extreme is the "good" high value.
            gap = mean([max(0.0, cfg.target_extreme - e) for e in ext])
        amplitude = round(_clamp(100.0 - gap * (100.0 / max(cfg.amplitude_band, 1.0))))
        basis.append(
            f"动作幅度：{len(ext)} 次平均到达极值 {mean(ext):.1f}°，"
            f"参考目标 {cfg.target_extreme:.0f}°"
        )
    else:
        amplitude = round(_clamp(50.0))
        basis.append("未完成完整周期，幅度分采用保守基准值")

    # --- Rhythm: coefficient of variation of cycle durations. --------------
    periods = [c["duration"] for c in cycles.cycles if c["duration"] > 0]
    if len(periods) >= 2:
        m = mean(periods)
        cv = pstdev(periods) / m if m > 0 else 1.0
        rhythm = round(_clamp(100.0 - cv * 120.0))
        basis.append(f"节奏：{len(periods)} 个完整周期，周期变异系数 {cv:.2f}")
    else:
        rhythm = round(_clamp(65.0))
        basis.append("完整周期少于 2 个，节奏分采用保守基准值")

    # --- Stability: visibility + trunk wobble. -----------------------------
    vis = cycles.visibility_mean
    stability = round(_clamp(vis * 100.0 - cycles.trunk_std * 1.5))
    basis.append(
        f"稳定性：关键点平均可见度 {vis:.0%}，躯干角波动 {cycles.trunk_std:.1f}°"
    )

    # --- Symmetry (both elbows in skeleton, when measurable). --------------
    sym_dev, sym_n = _symmetry_from_rows(rows)
    if sym_dev is not None:
        symmetry = round(_clamp(100.0 - sym_dev * 2.0))
        basis.append(f"对称性：左右肘角平均差 {sym_dev:.1f}°（{sym_n} 帧）")
    else:
        symmetry = None
        basis.append("机位仅暴露单侧手臂，左右对称性本次不可评")

    dims = [("幅度", amplitude), ("节奏", rhythm), ("稳定", stability)]
    weights = [0.40, 0.25, 0.20]
    if symmetry is not None:
        dims.append(("对称", symmetry))
        weights.append(0.15)
    wsum = sum(weights)
    overall = round(sum(d * w for (_, d), w in zip(dims, weights)) / wsum)

    confidence = round(min(1.0, vis * min(1.0, max(1, len(rows)) / 12)), 3)
    return {
        "available": True,
        "amplitude": amplitude,
        "rhythm_control": rhythm,
        "stability": stability,
        "symmetry": symmetry,
        "overall": overall,
        "confidence": confidence,
        "counter": COUNTER_VERSION,
        "scorer": SCORER_VERSION,
        "basis": basis,
        "disclaimer": "评分来自单机位二维姿态估算，仅用于一般训练反馈，不作为医学或损伤诊断。",
    }


def score_static_quality(rows: list[dict], cfg: StaticConfig,
                         hold_seconds: float) -> dict:
    """Quality block for a static hold (plank / side plank)."""
    vis_vals = [float(r["visibility"]) for r in rows if _finite(r.get("visibility"))]
    vis_mean = mean(vis_vals) if vis_vals else 0.0

    stds = []
    for key in cfg.posture_keys:
        vals = [float(r[key]) for r in rows if _finite(r.get(key))]
        if len(vals) >= 2:
            stds.append(pstdev(vals))
    wobble = mean(stds) if stds else cfg.stability_band
    stability = round(_clamp(100.0 - (wobble / cfg.stability_band) * 40.0))

    duration_score = round(_clamp(
        40.0 + min(hold_seconds, 30.0) * 2.0
    ))
    overall = round(stability * 0.6 + duration_score * 0.4)
    confidence = round(min(1.0, vis_mean * min(1.0, max(1, len(rows)) / 12)), 3)
    return {
        "available": True,
        "amplitude": None,           # not a repetitive movement
        "rhythm_control": None,
        "stability": stability,
        "symmetry": None,
        "overall": overall,
        "hold_seconds": round(hold_seconds, 2),
        "confidence": confidence,
        "counter": STATIC_COUNTER_VERSION,
        "scorer": STATIC_SCORER_VERSION,
        "basis": [
            f"保持时长：{hold_seconds:.1f} 秒",
            f"姿态稳定：关键姿态角波动 {wobble:.1f}°，平均可见度 {vis_mean:.0%}",
            "静态动作不统计重复次数，以保持时长与姿态稳定为主。",
        ],
        "disclaimer": "评分来自单机位二维姿态估算，仅用于一般训练反馈，不作为医学或损伤诊断。",
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def supported_dynamic_exercises() -> list[str]:
    return sorted(DYNAMIC_EVALUATORS)


def supported_static_exercises() -> list[str]:
    return sorted(STATIC_EVALUATORS)


def evaluate_measurements(exercise_id: str,
                          samples: list[dict],
                          sampled_frames: int,
                          duration_ms: int) -> dict:
    """Produce the V2 ``measurements`` group for one exercise.

    Parameters
    ----------
    exercise_id : catalog id (e.g. ``"bicep_curl"``).
    samples : per-frame rows from ``extract_metrics`` (already filtered to this
        exercise's pose samples).
    sampled_frames : total pose frames sampled by the decode pass (for valid-rate).
    duration_ms : decoded video duration in ms.

    Returns
    -------
    dict matching contract §5 ``measurements``. When the exercise is not wired
    up yet, returns ``{"available": False, "exercise_id": ..., "reason": ...}``
    so the caller can keep ``measurements.available`` independent of recognition.
    """
    samples = samples or []
    duration_ms = int(duration_ms or 0)

    if exercise_id in DYNAMIC_EVALUATORS:
        cfg = DYNAMIC_EVALUATORS[exercise_id]
        cyc = count_dynamic_cycles(samples, cfg)
        if cyc.valid_rate <= 0 or len(cyc.extreme_values) == 0 and cyc.reps == 0:
            # Recognition may stand, but no complete cycle -> no count/score.
            reason = "未检测到完整动作周期（可能只录到半次），本次不统计次数。"
            if len([r for r in samples if _finite(r.get(cfg.signal))]) < 4:
                reason = "目标关节时序不足，请完整入镜并完成至少一次动作。"
            return {
                "available": False,
                "exercise_id": exercise_id,
                "reason": reason,
            }
        quality = score_dynamic_quality(cyc, cfg, samples)
        return {
            "available": True,
            "exercise_id": exercise_id,
            "reps": cyc.reps,
            "duration_ms": duration_ms,
            "quality": quality,
            "incomplete_cycle": cyc.incomplete,
            "cycles": cyc.cycles,
        }

    if exercise_id in STATIC_EVALUATORS:
        cfg = STATIC_EVALUATORS[exercise_id]
        valid = [r for r in samples
                 if _finite(r.get("visibility")) and float(r["visibility"]) >= 0.4]
        hold_seconds = duration_ms / 1000.0
        if len(valid) < 4 or hold_seconds < cfg.duration_min_seconds:
            return {
                "available": False,
                "exercise_id": exercise_id,
                "reason": (
                    f"静态保持样本不足（{len(valid)} 个有效姿态，时长 {hold_seconds:.1f}s），"
                    "请保持至少 2 秒并完整入镜。"
                ),
            }
        quality = score_static_quality(valid, cfg, hold_seconds)
        return {
            "available": True,
            "exercise_id": exercise_id,
            "reps": 0,            # static: reps is intentionally 0, duration carries it
            "duration_ms": duration_ms,
            "quality": quality,
        }

    return {
        "available": False,
        "exercise_id": exercise_id,
        "reason": f"{exercise_id} 暂未登记计次/评分器，仅提供识别与讲解。",
    }
