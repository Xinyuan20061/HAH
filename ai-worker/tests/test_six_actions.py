import pytest

from healthmate_worker.processors.analyzers import get_analyzer
from healthmate_worker.processors.recognition import SUPPORTED_EXERCISES, recognize_exercise


def rows(action):
    active = [175, 168, 145, 115, 95, 120, 150, 168, 175]
    values = []
    for index, value in enumerate(active):
        row = {
            "t": index * 0.2,
            "knee": 175,
            "elbow": 175,
            "hip": 170,
            "trunk": 5,
            "visibility": 0.95,
            "back_visibility": 0.95,
            "back_knee": 175,
            "body_line": 175,
            "body_offset": 0.01,
            "side": "left",
            "leg_abduction": 175,
            "arm_abduction": 175,
            "arm_vw": 175,
        }
        if action in {"squat", "lunge"}:
            row["knee"] = value
        if action == "lunge":
            row["back_knee"] = max(90, 175 - (175 - value) * 0.3)
        if action == "pushup":
            row["elbow"] = value
            row["trunk"] = 65
        if action in {"leg_abduction", "arm_abduction", "arm_vw"}:
            row[action] = value
        if action == "arm_vw":
            row["elbow"] = value
            row["arm_abduction"] = 80
        values.append(row)
    return values


@pytest.mark.parametrize("action", ["leg_abduction", "arm_abduction", "arm_vw"])
def test_new_action_analyzers_count_one_complete_cycle(action):
    pose, frames = get_analyzer(action).analyze(rows(action), 9)
    assert pose["available"] and pose["reps"] == 1
    assert any(frame["event"] == action + "_bottom" for frame in frames)


@pytest.mark.parametrize("action", ["leg_abduction", "arm_abduction", "arm_vw"])
def test_rule_recognizer_ranks_each_new_action(action):
    samples = {candidate: rows(action) for candidate in SUPPORTED_EXERCISES}
    recognition = recognize_exercise(samples)
    assert recognition["accepted"]
    assert recognition["selected_type"] == action
    assert len(recognition["candidates"]) == 6
