"""Heuristic 2-D exercise evidence; thresholds are coaching hints, not clinical measurements."""

from __future__ import annotations
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Thresholds:
    down: float
    up: float
    depth: float
    trunk: float
    visibility: float = 0.5
    min_samples: int = 4
    min_valid_rate: float = 0.35
    min_rep_seconds: float = 0.35


# down/up hysteresis counts a descent followed by full extension; depth is evaluated separately.
THRESHOLDS = {
    "squat": Thresholds(down=145, up=155, depth=105, trunk=45),
    "pushup": Thresholds(down=140, up=155, depth=100, trunk=180),
    "lunge": Thresholds(down=140, up=155, depth=110, trunk=30),
    "leg_abduction": Thresholds(down=160, up=170, depth=145, trunk=30),
    "arm_abduction": Thresholds(down=150, up=165, depth=115, trunk=30),
    "arm_vw": Thresholds(down=145, up=160, depth=110, trunk=30),
}


def angle(a, b, c):
    ba, bc = (a[0] - b[0], a[1] - b[1]), (c[0] - b[0], c[1] - b[1])
    denominator = math.hypot(*ba) * math.hypot(*bc)
    if denominator < 1e-8:
        return None
    cosine = max(-1.0, min(1.0, sum(x * y for x, y in zip(ba, bc)) / denominator))
    return math.degrees(math.acos(cosine))


class MotionAnalyzer:
    exercise_type = ""
    primary = "knee"

    def __init__(self):
        self.thresholds = THRESHOLDS[self.exercise_type]

    def valid(self, row):
        required = [self.primary, "hip", "trunk", "visibility"]
        if self.exercise_type == "pushup":
            required += ["body_line", "body_offset"]
        if self.exercise_type == "lunge":
            required += ["back_knee", "back_visibility"]
        return (
            all(
                type(row.get(key)) in {int, float} and math.isfinite(row[key])
                for key in required
            )
            and row["visibility"] >= self.thresholds.visibility
            and row.get("back_visibility", 1) >= self.thresholds.visibility
        )

    def risk(self, row):
        return max(0, row["trunk"] - self.thresholds.trunk)

    def errors(self, rows, cycles):
        errors = []
        bottoms = [cycle["bottom"] for cycle in cycles]
        if not bottoms:
            bottoms = [min(rows, key=lambda x: x[self.primary])]
        shallow = [row for row in bottoms if row[self.primary] > self.thresholds.depth]
        if shallow:
            errors.append(
                {
                    "code": "depth_insufficient",
                    "label": "下放深度可能不足",
                    "severity": "medium",
                    "evidence": f"{len(shallow)} 个最低点 {self.primary} 角大于 {self.thresholds.depth}°",
                }
            )
        peak = max(rows, key=self.risk)
        if peak["trunk"] > self.thresholds.trunk:
            errors.append(
                {
                    "code": "trunk_lean",
                    "label": "躯干倾角较大",
                    "severity": "medium",
                    "evidence": f"{peak['t']:.2f}s 躯干倾角 {peak['trunk']:.1f}°",
                }
            )
        return errors

    def analyze(
        self, samples: list[dict], sampled_frames: int
    ) -> tuple[dict, list[dict]]:
        rows = [row for row in samples if self.valid(row)]
        valid_rate = len(rows) / sampled_frames if sampled_frames else 0
        common = {
            "engine": "mediapipe-pose",
            "exercise_type": self.exercise_type,
            "sample_count": len(rows),
            "sampled_frames": sampled_frames,
            "keypoint_valid_rate": round(valid_rate, 3),
            "measurement": "2D heuristic estimate",
        }
        if (
            len(rows) < self.thresholds.min_samples
            or valid_rate < self.thresholds.min_valid_rate
        ):
            return {
                **common,
                "available": False,
                "message": "不足以评价：关键点数量或可见度不足，请固定机位、完整入镜后重录。",
            }, []
        rows.sort(key=lambda x: x["t"])
        cycles, events = [], []
        top, bottom = None, None
        descent_start = None
        interrupted = False
        previous = None
        for row in rows:
            # Missing/occluded segments cannot be joined into a fabricated full repetition.
            if (
                previous is not None
                and descent_start is not None
                and row["t"] - previous["t"] > 0.75
            ):
                top, bottom, descent_start = None, None, None
                interrupted = True
            if top is None:
                if row[self.primary] >= self.thresholds.up:
                    top = row
            elif bottom is None and row[self.primary] < self.thresholds.down:
                bottom, descent_start = row, row["t"]
            elif bottom is not None:
                if row[self.primary] < bottom[self.primary]:
                    bottom = row
                if row[self.primary] >= self.thresholds.up:
                    if row["t"] - descent_start >= self.thresholds.min_rep_seconds:
                        cycles.append(
                            {
                                "start": top,
                                "bottom": bottom,
                                "end": row,
                                "duration": round(row["t"] - top["t"], 3),
                                "side": bottom.get("side"),
                            }
                        )
                    top, bottom, descent_start = row, None, None
            previous = row

        def add(row, event, reason):
            frame = {
                "timestamp": row["t"],
                "event": event,
                "reason": reason,
                "confidence": row["visibility"],
                "visibility": row["visibility"],
                "phase": event,
                "label": f"{row['t']:.1f}s",
            }
            for source, target in [
                ("knee", "knee_angle"),
                ("hip", "hip_angle"),
                ("trunk", "trunk_angle"),
                ("elbow", "elbow_angle"),
                ("body_line", "body_line_angle"),
                ("body_offset", "body_offset"),
                ("back_knee", "back_knee_angle"),
                ("leg_abduction", "leg_abduction_angle"),
                ("arm_abduction", "arm_abduction_angle"),
                ("arm_vw", "arm_vw_angle"),
                ("side", "side"),
            ]:
                if source in row:
                    frame[target] = row[source]
            if isinstance(row.get("skeleton"), list):
                frame["skeleton"] = row["skeleton"]
            events.append(frame)

        add(
            max(rows, key=lambda x: x[self.primary]),
            self.exercise_type + "_top",
            "最高/伸展姿态",
        )
        for number, cycle in enumerate(cycles, 1):
            add(
                cycle["bottom"],
                self.exercise_type + "_bottom",
                f"第 {number} 次动作最低点",
            )
            add(
                cycle["end"],
                self.exercise_type + "_completed",
                f"第 {number} 次动作伸展完成",
            )
        if bottom is not None:
            add(bottom, self.exercise_type + "_partial_bottom", "未完成动作的最低点")
        add(
            min(rows, key=lambda x: x[self.primary]),
            self.exercise_type + "_deepest",
            "全程最深姿态",
        )
        add(max(rows, key=lambda x: x["trunk"]), "max_trunk_lean", "最大躯干倾角")
        add(max(rows, key=self.risk), "max_rule_risk", "规则偏差最大时刻，仅作训练参考")
        unique = {(item["timestamp"], item["event"]): item for item in events}
        frames = sorted(unique.values(), key=lambda x: x["timestamp"])[:200]
        for index, frame in enumerate(frames):
            frame["index"] = index
        pose = {
            **common,
            "available": True,
            "visibility_mean": round(sum(r["visibility"] for r in rows) / len(rows), 3),
            "reps": len(cycles),
            "errors": self.errors(rows, cycles),
            "angles": {
                self.primary + "_min": min(r[self.primary] for r in rows),
                self.primary + "_max": max(r[self.primary] for r in rows),
                "hip_min": min(r["hip"] for r in rows),
            },
            "cycles": [
                {
                    "start": c["start"]["t"],
                    "bottom": c["bottom"]["t"],
                    "end": c["end"]["t"],
                    "duration": c["duration"],
                    "side": c["side"],
                }
                for c in cycles
            ][:100],
            "incomplete_cycle": bottom is not None or interrupted,
            "samples": [
                {key: value for key, value in row.items() if key != "skeleton"}
                for row in rows[:: max(1, math.ceil(len(rows) / 160))]
            ],
            "message": "二维角度为近似估算，遮挡/机位会影响结果，不作为医疗或精确运动学结论。",
        }
        return pose, frames


class SquatAnalyzer(MotionAnalyzer):
    exercise_type = "squat"


class PushupAnalyzer(MotionAnalyzer):
    exercise_type = "pushup"
    primary = "elbow"

    def risk(self, row):
        return abs(row["body_offset"]) * 100 + max(0, 165 - row["body_line"])

    def errors(self, rows, cycles):
        errors = super().errors(rows, cycles)
        peak = max(rows, key=self.risk)
        if peak["body_line"] < 165 or abs(peak["body_offset"]) > 0.12:
            errors.append(
                {
                    "code": "body_alignment",
                    "label": "臀部可能抬高或塌陷",
                    "severity": "medium",
                    "evidence": f"{peak['t']:.2f}s 肩-髋-踝角 {peak['body_line']:.1f}°，偏移 {peak['body_offset']:.3f}",
                }
            )
        return errors


class LungeAnalyzer(MotionAnalyzer):
    exercise_type = "lunge"

    def errors(self, rows, cycles):
        errors = super().errors(rows, cycles)
        bottoms = [cycle["bottom"] for cycle in cycles]
        if any(row["back_knee"] > 140 for row in bottoms):
            errors.append(
                {
                    "code": "back_leg_depth",
                    "label": "后腿下放可能不足",
                    "severity": "low",
                    "evidence": "动作最低点后腿膝角仍大于 140°，需结合拍摄侧面校正",
                }
            )
        return errors


class LegAbductionAnalyzer(MotionAnalyzer):
    exercise_type = "leg_abduction"
    primary = "leg_abduction"


class ArmAbductionAnalyzer(MotionAnalyzer):
    exercise_type = "arm_abduction"
    primary = "arm_abduction"


class ArmVWAnalyzer(MotionAnalyzer):
    exercise_type = "arm_vw"
    primary = "arm_vw"


def get_analyzer(exercise_type: str) -> MotionAnalyzer:
    classes = {
        "squat": SquatAnalyzer,
        "pushup": PushupAnalyzer,
        "lunge": LungeAnalyzer,
        "leg_abduction": LegAbductionAnalyzer,
        "arm_abduction": ArmAbductionAnalyzer,
        "arm_vw": ArmVWAnalyzer,
    }
    if exercise_type not in classes:
        raise ValueError("不支持的动作类型")
    return classes[exercise_type]()
