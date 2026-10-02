"""Migration head audit (spec §9.3 DOC-01, §13.12).

Fails when:

* the repository has more than one Alembic head;
* a *live* document still writes a migration head by hand (the stale 0016 defect).

Historical task reports (marked ``HISTORICAL:``) and this remediation's own spec
may legitimately quote old revision ids, so they are excluded. Every document that
describes the current system must reference the command result instead.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent

EXCLUDED_DOCS = {
    # The remediation spec quotes the defect (0016) it is fixing.
    "HEALTHMATE_FULL_REMEDIATION_DEVELOPMENT_SPEC_2026-10-02.md",
}
# Any hand-written revision id, e.g. 0016_food_item_evidence or 0025_motion_...
REVISION_PATTERN = re.compile(r"\b(\d{4}[a-z]?_[a-z0-9_]+)\b")


def heads() -> list[str]:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    return list(ScriptDirectory.from_config(config).get_heads())


def known_revisions() -> set[str]:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    return {rev.revision for rev in script.walk_revisions()}


def docs() -> list[Path]:
    return sorted(
        list(REPO_ROOT.glob("*.md")) + list((REPO_ROOT / "docs").glob("*.md"))
    )


def main() -> int:
    current_heads = heads()
    revisions = known_revisions()
    failures: list[str] = []

    if len(current_heads) != 1:
        failures.append(f"Alembic head 数量应为 1，实际 {current_heads}")

    # DOC-01 is specifically "a document asserts the current head while it is
    # stale". Dated reports that quote the head at the time of writing are
    # accurate history, so only a *mismatching* current-head claim fails.
    live_head = current_heads[0] if len(current_heads) == 1 else None
    revisions.add(str(live_head))
    violations: list[tuple[str, int, str]] = []
    for doc in docs():
        if doc.name in EXCLUDED_DOCS:
            continue
        text = doc.read_text(encoding="utf-8", errors="ignore")
        if text.lstrip().startswith("HISTORICAL:"):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            lowered = line.lower()
            if "head" not in lowered:
                continue
            for match in REVISION_PATTERN.finditer(line):
                candidate = match.group(1)
                if candidate not in revisions:
                    continue
                if candidate == live_head:
                    continue
                violations.append((doc.name, lineno, candidate))

    for name, lineno, revision in violations:
        failures.append(
            f"{name}:{lineno} 声称当前 head 为 {revision}，实际为 {live_head}"
            "（应引用命令结果）"
        )

    print("heads=" + ",".join(current_heads))
    print(f"known_revisions={len(revisions)} documents_scanned={len(docs())}")
    if failures:
        print("\nFAIL")
        for item in failures:
            print(" -", item)
        return 1
    print("OK: 单一 head 且文档未手写过期 head")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
