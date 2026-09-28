"""End-to-end check: analyze_motion in auto mode should recognize the demo via
the local Kinetics-400 model without a DeepSeek call."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from healthmate_worker.processors.motion import analyze_motion

video = Path(sys.argv[1])
result = analyze_motion(video, exercise_type="auto",
                        progress=lambda p, stage: print(f"  progress {p} {stage}"))

rec = result["recognition"]
print("accepted     :", rec.get("accepted"))
print("selected_type:", rec.get("selected_type"))
print("rescued_by   :", rec.get("rescued_by"))
print("confidence   :", rec.get("confidence"))
print("method       :", result.get("method"))
print("score avail  :", result["score"].get("available"))
if rec.get("kinetics400"):
    print("kinetics top :", rec["kinetics400"].get("top_label"),
          rec["kinetics400"].get("top_probability"))
print("reason       :", rec.get("reason"))
