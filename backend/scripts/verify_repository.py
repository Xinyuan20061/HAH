"""Validate source JSON/JS and scan secrets without printing secret values."""

import json
import ast
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
EXCLUDED = {
    ".venv",
    ".git",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "uploads",
    "models",
    "cache",
    ".ruff_cache",
}
SUFFIXES = {
    ".py",
    ".js",
    ".mjs",
    ".json",
    ".wxml",
    ".wxss",
    ".md",
    ".yml",
    ".yaml",
    ".sh",
    ".txt",
    ".ini",
    ".example",
}
files = [
    p
    for p in ROOT.rglob("*")
    if p.is_file()
    and not EXCLUDED.intersection(p.relative_to(ROOT).parts)
    and not any(part.startswith(".venv") for part in p.relative_to(ROOT).parts)
    and (
        p.suffix in SUFFIXES or p.name in {"Dockerfile", ".gitignore", ".dockerignore"}
    )
]
errors = []
counts = {"json": 0, "js": 0}
secret_patterns = [
    re.compile(r"sk-[A-Za-z0-9_-]{24,}"),
    re.compile(r"(?:AKID|AKIA)[A-Za-z0-9]{16,}"),
    re.compile(r"(?:ghp_|github_pat_)[A-Za-z0-9_]{30,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        r"(?i)(?:app_secret|appsecret|worker_token|deepseek_api_key|mysql_password|credentials_encryption_key)\s*[:=]\s*[\"\']([A-Za-z0-9_-]{32,})[\"\']"
    ),
]
for path in files:
    content = path.read_text(encoding="utf-8-sig")
    relative = path.relative_to(ROOT)
    if path.suffix == ".json":
        try:
            json.loads(content)
            counts["json"] += 1
        except ValueError:
            errors.append(f"{relative}: invalid JSON")
    if path.suffix in {".js", ".mjs"}:
        result = subprocess.run(
            ["node", "--check", str(path)], capture_output=True, text=True
        )
        if result.returncode:
            errors.append(f"{relative}: invalid JavaScript")
        else:
            counts["js"] += 1
    if any(pattern.search(content) for pattern in secret_patterns):
        errors.append(
            f"{relative}: possible secret literal (review locally; value hidden)"
        )
    if path.suffix == ".py" and "tests" not in relative.parts:
        tree = ast.parse(content)
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "utcnow"
            for node in ast.walk(tree)
        ):
            errors.append(f"{relative}: deprecated UTC call")
    if path.suffix in {".js", ".wxml"} and re.search(r"['\"][?]{2,}['\"]", content):
        errors.append(f"{relative}: corrupted user-facing label")
print(
    f"JSON: {counts['json']}; JavaScript: {counts['js']}; scanned source files: {len(files)}"
)
if errors:
    print("\n".join(errors))
    sys.exit(1)
print(
    "[OK] JSON, JavaScript syntax, source secret patterns, deprecated UTC calls, labels"
)
print(
    "Scope: current source/examples/docs; ignored runtime files and absent Git history are not scanned. Heuristic scan is not proof against every secret format."
)
