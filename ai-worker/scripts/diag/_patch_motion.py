# -*- coding: utf-8 -*-
"""Adapt motion.py to (review, reason) return + record review_note."""
import io

path = r'C:\HealthMate\ai-worker\healthmate_worker\processors\motion.py'
text = io.open(path, encoding='utf-8').read()

old = '''    if exercise_type == "auto":
        review = review_exercise(
            video_path,
            sample_sets,
            local_pick=recognition.get("selected_type"),
            local_accepted=bool(recognition.get("accepted")),
            local_reason=str(recognition.get("reason") or ""),
        )
        if review:'''
new = '''    if exercise_type == "auto":
        review, review_error = review_exercise(
            video_path,
            sample_sets,
            local_pick=recognition.get("selected_type"),
            local_accepted=bool(recognition.get("accepted")),
            local_reason=str(recognition.get("reason") or ""),
        )
        if review_error:
            recognition["review_note"] = review_error
        if review:'''
assert old in text, "motion review anchor missing"
text = text.replace(old, new, 1)
io.open(path, 'w', encoding='utf-8', newline='').write(text)
print("MOTION_ADAPTED")
