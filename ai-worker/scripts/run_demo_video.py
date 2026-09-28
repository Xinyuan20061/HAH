"""Run Kinetics-400 model on a normal-scene demo video (pipeline sanity check)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2

from healthmate_worker.models.kinetics_clip import build_clip
from healthmate_worker.models.slowfast_r50 import SlowFastKinetics400

CHECKPOINT = r"D:\HealthMateData\motion\pretrained\slowfast_r50_8xb8-8x8x1-steplr-256e_kinetics400-rgb_20220818-b62a501f.pth"
LABELS = str(ROOT / "healthmate_worker" / "models" / "kinetics400_labels.txt")
VIDEO = sys.argv[1]

model = SlowFastKinetics400(CHECKPOINT, LABELS)
clip, frames = build_clip(VIDEO)
result = model.predict(clip, topk=5)
print(f"frames={frames}")
print("top:", result["top_label"], result["top_probability"])
for c in result["candidates"]:
    print(f"  {c['class_index']:3d} {c['label']:30s} {c['probability']:.3f}")

# save a middle frame for visual confirmation
cap = cv2.VideoCapture(VIDEO)
cap.set(cv2.CAP_PROP_POS_FRAMES, frames // 2)
ok, frame = cap.read()
if ok:
    out = str(Path(VIDEO).with_suffix(".mid.jpg"))
    cv2.imwrite(out, frame)
    print("saved", out)
cap.release()
