# -*- coding: utf-8 -*-
import io
path = r'C:\HealthMate\ai-worker\healthmate_worker\processors\motion_review.py'
text = io.open(path, encoding='utf-8').read()
old = 'f"视觉模型调用失败（{exc.error_code}）"'
new = 'f"视觉模型调用失败（{exc.code}）"'
assert old in text
text = text.replace(old, new, 1)
io.open(path, 'w', encoding='utf-8', newline='').write(text)
print("CODE_FIXED")
