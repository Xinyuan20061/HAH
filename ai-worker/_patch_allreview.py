# -*- coding: utf-8 -*-
"""Review ALL exercises (auto + manual), mark confirmations too."""
import io

path = r'C:\HealthMate\ai-worker\healthmate_worker\processors\motion.py'
text = io.open(path, encoding='utf-8').read()

old = '''    compositional_semantics = infer_motion_semantics(sample_sets)
    if exercise_type == "auto":
        review, review_error = review_exercise(
            video_path,
            sample_sets,
            local_pick=recognition.get("selected_type"),
            local_accepted=bool(recognition.get("accepted")),
            local_reason=str(recognition.get("reason") or ""),
        )
        if review_error:
            recognition["review_note"] = review_error
        if review:
            recognition["review"] = review
            chosen = str(review.get("chosen_type") or "").strip().lower().replace("-", "_")
            if not recognition.get("accepted") and chosen:
                # Local rules abstained; the vision model identified a movement.
                recognition["accepted"] = True
                recognition["selected_type"] = chosen
                recognition["confidence"] = round(review.get("confidence") or 0, 3)
                recognition["method"] = (
                    "rule_feature_matching_v1 + deepseek_vision_fallback"
                )
                recognition["reason"] = (
                    f"本地规则未识别，DeepSeek 视觉兜底判定为 {chosen}（置信 "
                    f"{review.get('confidence'):.0%}）：{review.get('reason') or ''}"
                )
            elif recognition.get("accepted") and chosen in SUPPORTED_EXERCISES and (
                chosen != recognition.get("selected_type")
            ):
                recognition["selected_type"] = chosen
                recognition["method"] = (
                    str(recognition.get("method") or "") + "+ deepseek_vision_review"
                )
                recognition["reason"] = (
                    f"DeepSeek 视觉复查修正为 {chosen}：{review.get('reason') or ''}"
                )'''
new = '''    compositional_semantics = infer_motion_semantics(sample_sets)
    # DeepSeek vision review runs for EVERY exercise (auto-recognised and
    # manually selected): it confirms the local pick, corrects it, or rescues
    # an abstained recognition. Failure never blocks the local decision.
    review, review_error = review_exercise(
        video_path,
        sample_sets,
        local_pick=recognition.get("selected_type"),
        local_accepted=bool(recognition.get("accepted")),
        local_reason=str(recognition.get("reason") or ""),
    )
    if review_error:
        recognition["review_note"] = review_error
    if review:
        recognition["review"] = review
        chosen = str(review.get("chosen_type") or "").strip().lower().replace("-", "_")
        if not recognition.get("accepted") and chosen:
            # Local rules abstained; the vision model identified a movement.
            recognition["accepted"] = True
            recognition["selected_type"] = chosen
            recognition["confidence"] = round(review.get("confidence") or 0, 3)
            recognition["method"] = (
                "rule_feature_matching_v1 + deepseek_vision_fallback"
            )
            recognition["reason"] = (
                f"本地规则未识别，DeepSeek 视觉兜底判定为 {chosen}（置信 "
                f"{review.get('confidence'):.0%}）：{review.get('reason') or ''}"
            )
        elif recognition.get("accepted") and chosen in SUPPORTED_EXERCISES:
            if chosen != recognition.get("selected_type"):
                recognition["selected_type"] = chosen
                recognition["method"] = (
                    str(recognition.get("method") or "") + "+ deepseek_vision_review"
                )
                recognition["reason"] = (
                    f"DeepSeek 视觉复查修正为 {chosen}：{review.get('reason') or ''}"
                )
            else:
                # Vision model confirmed the local pick: still mark the review.
                review["confirmed"] = True
                recognition["method"] = (
                    str(recognition.get("method") or "") + "+ deepseek_vision_review"
                )
                recognition["reason"] = (
                    f"DeepSeek 视觉复查确认为 {chosen}（置信 "
                    f"{review.get('confidence'):.0%}）：{review.get('reason') or ''}"
                )'''
assert old in text, "review block anchor missing"
text = text.replace(old, new, 1)
io.open(path, 'w', encoding='utf-8', newline='').write(text)
print("ALL_EXERCISES_REVIEWED")
