# -*- coding: utf-8 -*-
"""Continue review patch: render threshold + prompt + review_exercise."""
import io

path = r'C:\HealthMate\ai-worker\healthmate_worker\processors\motion_review.py'
text = io.open(path, encoding='utf-8').read()

# render_stick_frames threshold
old_t = '                and float(item.get("visibility", 0)) >= 0.35\n            }'
new_t = '                and float(item.get("visibility", 0)) >= 0.25\n            }'
assert old_t in text, "render threshold anchor missing"
text = text.replace(old_t, new_t, 1)

old_prompt = '''- 返回 JSON，不要 Markdown。字段：exercise_type（英文小写下划线，如 squat、pushup、lunge、bicep_curl、deadlift、shoulder_press、leg_raise、side_plank 等；无法判断时必须是 "unknown"）、confidence（0~1）、reason（一句中文判断依据）、suggested_fix（给用户的改进建议，中文，可为空字符串）。
- 本地引擎候选（供参考，不必拘泥）：squat / pushup / lunge / leg_abduction / arm_abduction / arm_vw。'''
new_prompt = '''- 返回 JSON，不要 Markdown。字段：exercise_type（英文小写下划线；无法判断时必须是 "unknown"）、confidence（0~1）、reason（一句中文判断依据）、suggested_fix（给用户的改进建议，中文，可为空字符串）。
- 本地引擎候选（供参考，不必拘泥）：squat / pushup / lunge / leg_abduction / arm_abduction / arm_vw。
- 常见居家动作（可从中判断）：bicep_curl 哑铃弯举、deadlift 硬拉、shoulder_press 肩推、lateral_raise 侧平举、leg_raise 抬腿、side_plank 侧平板、plank 平板支撑、situp 卷腹、crunch 卷腹、pullup 引体向上、row 划船、chest_press 卧推、hip_thrust 臀桥、calf_raise 提踵、mountain_climber 登山跑、jumping_jack 开合跳、burpee 波比跳、high_knees 高抬腿。
- 只要画面中有人、骨架和角度证据足够，就给出最可能的动作并给出中等以上置信度；证据不足才 unknown。'''
assert old_prompt in text, "prompt anchor missing"
text = text.replace(old_prompt, new_prompt, 1)

old_rv = '''    rows = select_review_rows(sample_sets, count=4)
    if not rows:
        return None
    frames = render_stick_frames(video_path, rows)
    if not frames:
        return None
    summary = skeleton_summary(sample_sets)
    if local_accepted:
        suffix = (
            f"\\n本地引擎判定为 {local_pick}，请确认是否合理；若明显不符，给出你认为最可能的动作。"
        )
        fallback_pick = local_pick
    else:
        suffix = (
            f"\\n本地引擎无法可靠识别（{local_reason[:120]}）。"
            "请根据骨架与数值给出最可能的动作；确实无法判断则返回 unknown。"
        )
        fallback_pick = None
    try:
        review = _request_review(frames, summary, suffix)
    except ProcessingError:
        return None
    if not review or not review.get("exercise_type") or review["exercise_type"] == "unknown":
        return None
    if review["confidence"] < 0.4:
        return None
    chosen = review["exercise_type"] if not local_accepted else (
        review["exercise_type"] if review["confidence"] >= 0.55 else fallback_pick
    )
    review.update(chosen_type=chosen, frames_used=len(frames))
    return review'''
new_rv = '''    rows = select_review_rows(sample_sets, count=6)
    if not rows:
        return None, "骨架可见样本不足（可见关键点少于8个），视觉兜底无法取帧"
    frames = render_stick_frames(video_path, rows)
    if not frames:
        return None, "关键帧渲染失败，视觉兜底未执行"
    summary = skeleton_summary(sample_sets)
    if local_accepted:
        suffix = (
            f"\\n本地引擎判定为 {local_pick}，请确认是否合理；若明显不符，给出你认为最可能的动作。"
        )
        fallback_pick = local_pick
    else:
        suffix = (
            f"\\n本地引擎无法可靠识别（{local_reason[:120]}）。"
            "请根据骨架与数值给出最可能的动作；确实无法判断则返回 unknown。"
        )
        fallback_pick = None
    try:
        review = _request_review(frames, summary, suffix)
    except ProcessingError as exc:
        return None, f"视觉模型调用失败（{exc.error_code}）"
    if not review or not review.get("exercise_type") or review["exercise_type"] == "unknown":
        return None, "视觉模型无法判断动作类型（unknown）"
    if review["confidence"] < 0.35:
        return None, f"视觉模型置信度不足（{review['confidence']:.0%}）"
    chosen = review["exercise_type"] if not local_accepted else (
        review["exercise_type"] if review["confidence"] >= 0.55 else fallback_pick
    )
    review.update(chosen_type=chosen, frames_used=len(frames))
    return review, ""'''
assert old_rv in text, "review_exercise anchor missing"
text = text.replace(old_rv, new_rv, 1)

io.open(path, 'w', encoding='utf-8', newline='').write(text)
print("MOTION_REVIEW_STRENGTHENED")
