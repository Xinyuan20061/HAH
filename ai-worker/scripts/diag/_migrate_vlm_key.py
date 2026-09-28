# -*- coding: utf-8 -*-
"""Migrate the hard-coded DeepSeek key in scripts/probe_vlm.py into
ai-worker/.env as DEEPSEEK_API_KEY (values never printed)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "scripts" / "probe_vlm.py"
ENV = ROOT / ".env"

src = SRC.read_text(encoding="utf-8")
match = re.search(r'KEY = "(sk-[A-Za-z0-9]+)"', src)
assert match, "key pattern not found in probe_vlm.py"
key = match.group(1)

lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
if any(re.match(r"^\s*DEEPSEEK_API_KEY\s*=", line) for line in lines):
    print("DEEPSEEK_API_KEY already exists; not overwriting")
else:
    lines.append("DEEPSEEK_API_KEY=" + key)
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("DEEPSEEK_API_KEY written to .env (value hidden)")
