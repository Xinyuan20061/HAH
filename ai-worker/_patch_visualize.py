# -*- coding: utf-8 -*-
"""Strengthen annotate_keyframes rendering success rate."""
import io

path = r'C:\HealthMate\ai-worker\healthmate_worker\visualize.py'
text = io.open(path, encoding='utf-8').read()

old = '''def _encode_jpeg(frame, cv2, max_bytes: int) -> bytes:
    image = frame
    for _ in range(5):
        for quality in (82, 68, 54, 42):
            ok, encoded = cv2.imencode(
                ".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality]
            )
            if ok and len(encoded) <= max_bytes:
                return encoded.tobytes()
        image = cv2.resize(image, None, fx=0.8, fy=0.8, interpolation=cv2.INTER_AREA)
    raise ValueError("annotated preview cannot fit size budget")'''
new = '''def _encode_jpeg(frame, cv2, max_bytes: int) -> bytes:
    image = frame
    # Pre-scale wide frames so JPEG stays small and encoding rarely fails.
    height, width = image.shape[:2]
    if max(width, height) > 720:
        factor = 720 / max(width, height)
        image = cv2.resize(
            image, (round(width * factor), round(height * factor)),
            interpolation=cv2.INTER_AREA,
        )
    for _ in range(6):
        for quality in (84, 72, 60, 48, 38):
            ok, encoded = cv2.imencode(
                ".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality]
            )
            if ok and len(encoded) <= max_bytes:
                return encoded.tobytes()
        image = cv2.resize(image, None, fx=0.8, fy=0.8, interpolation=cv2.INTER_AREA)
    raise ValueError("annotated preview cannot fit size budget")'''
assert old in text, "encode anchor missing"
text = text.replace(old, new, 1)

old2 = '''def render_motion_preview(frame, event: dict, *, max_bytes: int = 80 * 1024) -> dict:'''
new2 = '''def render_motion_preview(frame, event: dict, *, max_bytes: int = 160 * 1024) -> dict:'''
assert old2 in text
text = text.replace(old2, new2, 1)

old3 = '''    try:
        for event in selected:
            capture.set(
                cv2.CAP_PROP_POS_MSEC, float(event.get("timestamp") or 0) * 1000
            )
            ok, frame = capture.read()
            if not ok:
                continue
            try:
                event.update(render_motion_preview(frame, event))
            except (ValueError, cv2.error):
                continue'''
new3 = '''    try:
        for event in selected:
            target_ms = float(event.get("timestamp") or 0) * 1000
            capture.set(cv2.CAP_PROP_POS_MSEC, target_ms)
            ok, frame = capture.read()
            if not ok:
                # Seek can fail on some encoders: fall back to sequential read.
                capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                while True:
                    ok, frame = capture.read()
                    if not ok:
                        break
                    if capture.get(cv2.CAP_PROP_POS_MSEC) >= target_ms:
                        break
                if not ok:
                    continue
            try:
                event.update(render_motion_preview(frame, event))
            except (ValueError, cv2.error):
                continue'''
assert old3 in text, "annotate loop anchor missing"
text = text.replace(old3, new3, 1)

io.open(path, 'w', encoding='utf-8', newline='').write(text)
print("VISUALIZE_STRENGTHENED")
