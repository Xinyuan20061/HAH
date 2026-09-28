"""End-to-end food analysis via the configured provider chain."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from healthmate_worker.processors.food import analyze_food

image = Path(sys.argv[1])
result = analyze_food(image, progress=lambda p, s: print(f"  progress {p} {s}"))
print("source      :", result.get("source"))
print("model       :", result.get("model"))
print("dish_name   :", result.get("dish_name"))
print("confidence  :", result.get("confidence"))
print("weight_g    :", result.get("estimated_weight_g"))
print("calories    :", result.get("calories"))
print("protein     :", result.get("protein"))
print("items       :", [i.get("name") for i in result.get("items", [])][:6])
