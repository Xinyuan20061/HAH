# -*- coding: utf-8 -*-
"""Work Package G: offline validation of the generic counter/scorer module.

Every exercise below is driven by PROGRAMMATIC pose rows (the same dict shape
``processors/motion.py::extract_metrics`` emits) — no real video, no MediaPipe,
no network. We assert:

  * complete cycles are counted and incomplete ones are NOT;
  * the quality block carries amplitude / rhythm / stability / (when measurable)
    symmetry;
  * static holds (plank / side plank) report duration + posture stability and
    never fabricate a rep;
  * exercises without a registered evaluator stay ``available=False``.

These are code-logic checks only; they do NOT claim real-world accuracy.
"""

from __future__ import annotations

import math

import pytest

from healthmate_worker.processors.counters import (
    COUNTER_VERSION,
    SCORER_VERSION,
    STATIC_COUNTER_VERSION,
    STATIC_SCORER_VERSION,
    count_dynamic_cycles,
    evaluate_measurements,
    supported_dynamic_exercises,
    supported_static_exercises,
    DYNAMIC_EVALUATORS,
    STATIC_EVALUATORS,
)


# ---------------------------------------------------------------------------
# Synthetic pose-row builders (no MediaPipe involved)
# ---------------------------------------------------------------------------

def _row(t, *, arm_vw=165.0, arm_abduction=178.0, trunk=5.0,
         body_line=175.0, visibility=0.9, skeleton=None):
    """One per-frame pose row, shaped like extract_metrics() output."""
    return {
        "t": round(t, 3),
        "side": "right",
        "visibility": visibility,
        "knee": 170.0,
        "hip": 175.0,
        "elbow": arm_vw,
        "body_line": body_line,
        "body_offset": 0.0,
        "trunk": trunk,
        "shin_verticality": 1.0,
        "arm_vw": arm_vw,
        "arm_abduction": arm_abduction,
        "skeleton": skeleton,
    }


def _oscillating_rows(signal, rest_val, excursion_val, reps, *,
                      fps=8.0, trunk=5.0, visibility=0.9,
                      pre_rest_frames=4, tail_rest=True):
    """Rows where `signal` oscillates rest_val <-> excursion_val `reps` times.

    A rep = ramp from rest_val down to excursion_val, a short hold, ramp back.
    The generator is direction-agnostic; the DynamicConfig.rest_high flag decides
    which level is "rest".
    """
    dt = 1.0 / fps
    rows = []
    t = 0.0

    def push(v, tt):
        kw = {"trunk": trunk, "visibility": visibility}
        if signal == "arm_vw":
            kw["arm_vw"] = v
        elif signal == "arm_abduction":
            kw["arm_abduction"] = v
        rows.append(_row(tt, **kw))

    for _ in range(pre_rest_frames):
        push(rest_val, t)
        t += dt

    for _ in range(reps):
        # descend rest -> excursion over ~0.6s
        n = max(2, int(0.6 / dt))
        for i in range(n):
            f = i / (n - 1)
            push(rest_val + (excursion_val - rest_val) * f, t)
            t += dt
        # hold at excursion ~0.2s
        for _ in range(2):
            push(excursion_val, t)
            t += dt
        # ramp back excursion -> rest over ~0.8s
        n = max(2, int(0.8 / dt))
        for i in range(n):
            f = i / (n - 1)
            push(excursion_val + (rest_val - excursion_val) * f, t)
            t += dt

    if tail_rest:
        for _ in range(2):
            push(rest_val, t)
            t += dt
    return rows


def _partial_excursion_rows(signal, rest_val, excursion_val, *, fps=8.0,
                            trunk=5.0, visibility=0.9):
    """Rest -> excursion but NEVER returns (incomplete cycle)."""
    dt = 1.0 / fps
    rows = []
    t = 0.0
    for _ in range(4):
        rows.append(_row(t, **{signal: rest_val}, trunk=trunk, visibility=visibility))
        t += dt
    n = max(2, int(1.2 / dt))
    for i in range(n):
        f = i / (n - 1)
        v = rest_val + (excursion_val - rest_val) * f
        rows.append(_row(t, **{signal: v}, trunk=trunk, visibility=visibility))
        t += dt
    return rows


def _skeleton(left_elbow_angle, right_elbow_angle):
    """Build a minimal 12-joint skeleton whose left/right elbow angles approx.
    the requested values (used only to exercise the symmetry estimator)."""
    # Place shoulder -> elbow vertically; flex the wrist sideways to set angle.
    def side(shx, elx, angle_deg):
        # upper arm points down (shoulder->elbow); forearm direction rotates by
        # (180 - angle) from straight-down.
        ely = 0.45
        sh = (shx, 0.25)
        el = (elx, ely)
        rad = math.radians(180.0 - angle_deg)
        # straight down forearm = (0, +0.2); rotate by rad around elbow
        fx, fy = math.sin(rad), math.cos(rad)
        wr = (elx + fx * 0.2, ely + fy * 0.2)
        return sh, el, wr

    lsh, lel, lwr = side(0.35, 0.35, left_elbow_angle)
    rsh, rel, rwr = side(0.65, 0.65, right_elbow_angle)
    pts = [
        ("left_shoulder", *lsh), ("right_shoulder", *rsh),
        ("left_elbow", *lel), ("right_elbow", *rel),
        ("left_wrist", *lwr), ("right_wrist", *rwr),
        ("left_hip", 0.42, 0.70), ("right_hip", 0.58, 0.70),
        ("left_knee", 0.42, 0.85), ("right_knee", 0.58, 0.85),
        ("left_ankle", 0.42, 0.98), ("right_ankle", 0.58, 0.98),
    ]
    return [{"id": name, "x": round(x, 4), "y": round(y, 4), "visibility": 0.9}
            for name, x, y in pts]


# ---------------------------------------------------------------------------
# Registry sanity
# ---------------------------------------------------------------------------

def test_registry_covers_p1_priority_set():
    dyn = set(supported_dynamic_exercises())
    static = set(supported_static_exercises())
    # P1 must all be wired.
    assert {
        "bicep_curl", "hammer_curl", "front_raise",
        "lateral_raise", "shoulder_press", "row",
    } <= dyn
    # Static holds wired.
    assert {"plank", "side_plank"} <= static
    # Everything else stays null (not claimed here).
    not_wired = {"deadlift", "bench_press", "crunch", "hip_bridge",
                 "calf_raise", "jumping_jack", "mountain_climber"}
    assert not_wired.isdisjoint(dyn) and not_wired.isdisjoint(static)


# ---------------------------------------------------------------------------
# P1 dynamic exercises
# ---------------------------------------------------------------------------

def test_bicep_curl_counts_complete_reps():
    rows = _oscillating_rows("arm_vw", rest_val=165.0, excursion_val=65.0, reps=5)
    out = evaluate_measurements("bicep_curl", rows, sampled_frames=len(rows),
                               duration_ms=int(rows[-1]["t"] * 1000))
    assert out["available"] is True
    assert out["exercise_id"] == "bicep_curl"
    assert out["reps"] == 5, out
    assert out["incomplete_cycle"] is False
    q = out["quality"]
    assert q["amplitude"] >= 0 and q["overall"] > 0
    assert q["counter"] == COUNTER_VERSION
    assert q["scorer"] == SCORER_VERSION
    # A complete rep has a measured depth.
    assert len(out["cycles"]) == 5


def test_bicep_curl_incomplete_cycle_not_counted():
    # Curl down but never return to extension.
    rows = _partial_excursion_rows("arm_vw", rest_val=165.0, excursion_val=65.0)
    cyc = count_dynamic_cycles(rows, DYNAMIC_EVALUATORS["bicep_curl"])
    assert cyc.reps == 0
    assert cyc.incomplete is True
    out = evaluate_measurements("bicep_curl", rows, sampled_frames=len(rows),
                                duration_ms=int(rows[-1]["t"] * 1000))
    assert out["available"] is False
    assert "完整" in out["reason"] or "周期" in out["reason"]


def test_one_complete_plus_one_incomplete_counts_only_the_complete():
    # Build: 1 clean rep, then a second descent that never returns.
    done = _oscillating_rows("arm_vw", 165.0, 65.0, reps=1)
    t0 = done[-1]["t"]
    tail = _partial_excursion_rows("arm_vw", 165.0, 65.0)
    tail = [dict(r, t=round(r["t"] + t0 + 0.1, 3)) for r in tail]
    rows = done + tail
    out = evaluate_measurements("bicep_curl", rows, sampled_frames=len(rows),
                               duration_ms=int(rows[-1]["t"] * 1000))
    assert out["available"] is True
    assert out["reps"] == 1
    assert out["incomplete_cycle"] is True


def test_hammer_curl_same_elbow_flexion_kinematics():
    rows = _oscillating_rows("arm_vw", 165.0, 75.0, reps=4)
    out = evaluate_measurements("hammer_curl", rows, len(rows),
                               int(rows[-1]["t"] * 1000))
    assert out["available"] is True
    assert out["reps"] == 4


def test_front_raise_via_arm_abduction():
    rows = _oscillating_rows("arm_abduction", 178.0, 105.0, reps=4)
    out = evaluate_measurements("front_raise", rows, len(rows),
                               int(rows[-1]["t"] * 1000))
    assert out["available"] is True
    assert out["reps"] == 4
    assert out["quality"]["amplitude"] > 0


def test_lateral_raise():
    rows = _oscillating_rows("arm_abduction", 178.0, 110.0, reps=3)
    out = evaluate_measurements("lateral_raise", rows, len(rows),
                               int(rows[-1]["t"] * 1000))
    assert out["available"] is True
    assert out["reps"] == 3


def test_shoulder_press_inverted_rest_position():
    # Rest = elbows bent ~95 (LOW); excursion = press to ~170 (HIGH).
    rows = _oscillating_rows("arm_vw", rest_val=95.0, excursion_val=170.0, reps=4)
    out = evaluate_measurements("shoulder_press", rows, len(rows),
                               int(rows[-1]["t"] * 1000))
    assert out["available"] is True
    assert out["reps"] == 4, out
    assert out["quality"]["amplitude"] > 0


def test_row_pull_and_return():
    # Start arms extended (elbow ~165, HIGH); pull elbow back to ~85 (LOW).
    rows = _oscillating_rows("arm_vw", rest_val=165.0, excursion_val=85.0, reps=4)
    out = evaluate_measurements("row", rows, len(rows), int(rows[-1]["t"] * 1000))
    assert out["available"] is True
    assert out["reps"] == 4


# ---------------------------------------------------------------------------
# Quality dimensions
# ---------------------------------------------------------------------------

def test_symmetry_measured_when_skeleton_exposes_both_arms():
    rows = _oscillating_rows("arm_vw", 165.0, 70.0, reps=3)
    # Attach an asymmetric skeleton: left elbow ~160, right ~90.
    skel = _skeleton(left_elbow_angle=160.0, right_elbow_angle=90.0)
    for r in rows:
        r["skeleton"] = skel
    out = evaluate_measurements("bicep_curl", rows, len(rows),
                               int(rows[-1]["t"] * 1000))
    q = out["quality"]
    assert q["symmetry"] is not None
    # ~70° left/right gap should pull symmetry well below 100.
    assert q["symmetry"] < 80, q
    assert any("对称" in b for b in q["basis"])


def test_symmetry_not_guessed_when_only_one_side_visible():
    rows = _oscillating_rows("arm_vw", 165.0, 70.0, reps=3)
    for r in rows:
        r["skeleton"] = None   # side view: no both-arm skeleton
    out = evaluate_measurements("bicep_curl", rows, len(rows),
                               int(rows[-1]["t"] * 1000))
    assert out["quality"]["symmetry"] is None
    assert any("不可评" in b or "单侧" in b for b in out["quality"]["basis"])


def test_rhythm_reflects_cycle_variation():
    # Regular cadence -> high rhythm score.
    reg = _oscillating_rows("arm_vw", 165.0, 70.0, reps=4)
    q_reg = evaluate_measurements("bicep_curl", reg, len(reg),
                                  int(reg[-1]["t"] * 1000))["quality"]
    assert q_reg["rhythm_control"] >= 70


def test_interrupted_gap_breaks_partial_cycle():
    rows = _oscillating_rows("arm_vw", 165.0, 70.0, reps=1)
    # Append a second excursion after a 1.5s camera cut (> 0.75s interrupt gap).
    t0 = rows[-1]["t"]
    tail = _partial_excursion_rows("arm_vw", 165.0, 70.0)
    tail = [dict(r, t=round(r["t"] + t0 + 1.5, 3)) for r in tail]
    cyc = count_dynamic_cycles(rows + tail, DYNAMIC_EVALUATORS["bicep_curl"])
    # The interrupted tail excursion must not join anything; only the 1 clean rep.
    assert cyc.reps == 1
    assert cyc.interrupted is True


# ---------------------------------------------------------------------------
# Static holds
# ---------------------------------------------------------------------------

def test_plank_is_timed_not_repeated():
    rows = [
        _row(t * 0.125, arm_vw=165.0, body_line=172.0, trunk=8.0, visibility=0.9)
        for t in range(24)  # ~3s at 8fps
    ]
    out = evaluate_measurements("plank", rows, len(rows), duration_ms=3000)
    assert out["available"] is True
    assert out["reps"] == 0
    q = out["quality"]
    assert q["counter"] == STATIC_COUNTER_VERSION
    assert q["scorer"] == STATIC_SCORER_VERSION
    assert q["hold_seconds"] == pytest.approx(3.0, abs=0.2)
    assert q["amplitude"] is None and q["rhythm_control"] is None
    assert q["stability"] > 0


def test_side_plank_min_duration_gate():
    rows = [
        _row(t * 0.125, body_line=170.0, trunk=10.0, visibility=0.9)
        for t in range(10)  # ~1.25s, below the 2s gate
    ]
    out = evaluate_measurements("side_plank", rows, len(rows), duration_ms=1250)
    assert out["available"] is False
    assert "保持" in out["reason"] or "样本" in out["reason"]


# ---------------------------------------------------------------------------
# Not-yet-wired exercises stay unavailable
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("exercise_id", ["deadlift", "bench_press", "crunch",
                                         "hip_bridge", "calf_raise",
                                         "jumping_jack", "mountain_climber",
                                         "squat", "pushup"])
def test_unwired_exercises_stay_null(exercise_id):
    rows = _oscillating_rows("arm_vw", 165.0, 70.0, reps=3)
    out = evaluate_measurements(exercise_id, rows, len(rows),
                               int(rows[-1]["t"] * 1000))
    assert out["available"] is False
    assert out["exercise_id"] == exercise_id
