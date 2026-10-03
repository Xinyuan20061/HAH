"""Capability honesty audit (capability plan §13.6).

Fails when the code claims more capability than it can evidence. The checks are
structural on purpose — they cannot be satisfied by editing a threshold:

1. **no invented Gold** — the motion catalog's ``quality_scorer`` / Gold flags must
   not be presented as passing unless a gate-passing evaluation exists in the model
   governance tables. A capability that is merely *implemented* is silver;
2. **planning-only exercises are declared** — a planning entry with no motion
   catalog row must be visible in ``PLANNING_ONLY_IDS`` so a UI cannot show a
   numeric score for it;
3. **the food table declares its review state** — an unreviewed table must report
   ``reviewed=False`` and keep ``seed_unreviewed`` as its source;
4. **the Gold gate thresholds match the plan** — the §5.10 numbers are re-declared
   in the worker; a silent reduction of a threshold is exactly how a fake "Gold"
   gets shipped. The worker copy is compared when the worker is importable.

Usage::

    # audits the *code* only (no schema needed): clauses 2 and 4
    python backend/scripts/audit_capability_honesty.py

    # full audit against a migrated database: clauses 1, 2, 3, 4
    python backend/scripts/audit_capability_honesty.py \\
        --database-url sqlite:///./audit-migrated.db

The database-dependent clauses are also covered by
``backend/tests/test_phase_capability_honesty.py``, which runs them on a
freshly migrated database so a missing local database cannot hide a regression.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

import app.models  # noqa: E402,F401  (populates Base.metadata)
from app.core.config import settings  # noqa: E402
from app.core.database import build_engine  # noqa: E402
from app.services.motion import catalog  # noqa: E402
from app.services.planning.library import EXERCISES, PLANNING_ONLY_IDS  # noqa: E402

# §5.10 thresholds, restated here as the audit's reference. A change to either side
# without the other is a finding.
PLAN_GOLD_THRESHOLDS: dict[str, float] = {
    "macro_f1": 0.85,
    "unknown_recall": 0.85,
    "rep_mae": 1.0,
    "hold_mae_seconds": 1.5,
    "phase_boundary_median_error_ms": 300.0,
    "high_severity_precision": 0.85,
    "unsupported_view_false_scoring_rate": 0.02,
    "evidence_coverage": 1.0,
}


def check_gold_gate_thresholds() -> list[str]:
    """Compare the worker's declared thresholds against the plan's numbers.

    The worker's ``gold_gate.py`` is parsed with :mod:`ast` rather than imported: a
    backend-only deployment (which is exactly where this audit runs in CI) does not
    have the worker's runtime dependencies, and an import failure must not be
    mistakable for "thresholds match".

    The worker expresses the thresholds as ``METRIC_*`` named constants, so the
    module's literal constants are resolved first and used as dict keys.
    """
    import ast

    worker_file = (
        REPO_ROOT / "ai-worker" / "healthmate_worker" / "processors"
        / "motion_gold" / "gold_gate.py"
    )
    if not worker_file.is_file():
        return [f"找不到 worker Gold 门禁文件：{worker_file}"]

    try:
        tree = ast.parse(worker_file.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return [f"worker gold_gate.py 无法解析：{exc.msg}"]

    constants: dict[str, object] = {}
    raw_thresholds: ast.AST | None = None
    for node in tree.body:
        # Plain `X = <literal>`.
        name: str | None = None
        value_node: ast.AST | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                name, value_node = target.id, node.value
        # Annotated `X: T = <literal>`.
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name, value_node = node.target.id, node.value
        if name is None or value_node is None:
            continue
        # Record the node BEFORE evaluating: literal_eval raises on a dict whose
        # keys are METRIC_* names, and a failed evaluation must not lose it.
        if name == "GOLD_THRESHOLDS":
            raw_thresholds = value_node
        try:
            constants[name] = ast.literal_eval(value_node)
        except ValueError:
            continue

    if raw_thresholds is None:
        return ["worker gold_gate.py 中没有 GOLD_THRESHOLDS 赋值"]

    declared: dict[str, float] = {}
    if isinstance(raw_thresholds, ast.Dict):
        for key_node, value_node in zip(raw_thresholds.keys, raw_thresholds.values):
            name: str | None = None
            if isinstance(key_node, ast.Name):
                name = key_node.id
            elif isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                name = key_node.value
            if name is None:
                continue
            resolved = constants.get(name)
            if isinstance(resolved, (int, float)) and not isinstance(resolved, bool):
                declared[name] = float(resolved)
            elif isinstance(value_node, ast.Constant) and isinstance(
                value_node.value, (int, float)
            ):
                declared[name] = float(value_node.value)
    if not declared:
        return ["worker GOLD_THRESHOLDS 不含可静态解析的数值阈值"]

    # Map the worker's constant names onto the plan's metric keys so the two sides
    # can be compared by meaning rather than by spelling.
    def _norm(name: str) -> str:
        return name.lower().removeprefix("metric_")

    normalized = {_norm(key): value for key, value in declared.items()}
    problems: list[str] = []
    for key, expected in PLAN_GOLD_THRESHOLDS.items():
        actual = normalized.get(key)
        if actual is None:
            problems.append(f"worker GOLD_THRESHOLDS 缺少 {key}")
            continue
        if abs(actual - expected) > 1e-9:
            problems.append(
                f"Gold 阈值被改动：{key} 计划为 {expected}，worker 为 {actual}"
            )
    return problems


def check_planning_only_declared() -> list[str]:
    declared = set(PLANNING_ONLY_IDS)
    actual = {
        key
        for key, exercise in EXERCISES.items()
        if catalog.get_action(key) is None
    }
    problems: list[str] = []
    undeclared = actual - declared
    if undeclared:
        problems.append(
            f"以下动作在计划库中但没有目录行，且未声明为 planning-only：{sorted(undeclared)}"
        )
    stale = declared - actual
    if stale:
        problems.append(f"PLANNING_ONLY_IDS 声明了实际有目录项的动作：{sorted(stale)}")
    return problems


# Catalog markers that would assert a Gold-grade claim. A rule-based scorer
# ("rule_quality@v1") is a legitimate Silver capability and must NOT be flagged:
# the audit is about invented *Gold*, not about a rule scorer existing.
GOLD_CLAIM_MARKERS = ("gold", "gated", "verified", "calibrated")


def check_gold_claims(db) -> list[str]:
    """A catalogue entry may only be called Gold with a passing evaluation."""
    from app.models import MotionGoldEvaluation

    problems: list[str] = []
    passing = {
        row.exercise_id
        for row in db.scalars(
            select(MotionGoldEvaluation).where(
                MotionGoldEvaluation.tier == "gold",
                MotionGoldEvaluation.available.is_(True),
            )
        ).all()
    }
    for action in catalog.list_capabilities():
        capabilities = action.get("capabilities") or {}
        for field in ("quality_scorer", "repetition_counter"):
            note = capabilities.get(field)
            if not isinstance(note, str) or not note.strip():
                continue
            lowered = note.strip().lower()
            if not any(marker in lowered for marker in GOLD_CLAIM_MARKERS):
                continue
            if action["id"] in passing:
                continue
            problems.append(
                f"{action['id']} 在 {field} 声明 {note!r}（Gold 级声明），"
                "但没有通过门禁的评测记录"
            )
    return problems


def check_food_table_state(db) -> list[str]:
    from app.services.food.references import ensure_seed_table, table_status

    ensure_seed_table(db)
    status = table_status(db)
    problems: list[str] = []
    if status["reviewed"] and "seed_unreviewed" in status["source_ids"]:
        problems.append("食物库同时声称已复核并保留 seed_unreviewed 来源，状态自相矛盾")
    if not status["reviewed"] and not status["entries"]:
        problems.append("食物库为空且未复核：确定性计算路径不可用")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true")
    parser.add_argument(
        "--database-url",
        default=None,
        help="已迁移到 head 的数据库；不提供时只审计代码层条款（2/4）",
    )
    args = parser.parse_args()

    problems: list[str] = []
    problems.extend(check_gold_gate_thresholds())
    problems.extend(check_planning_only_declared())

    database_checked = False
    if args.database_url:
        try:
            engine = build_engine(args.database_url)
            from sqlalchemy.orm import Session

            with Session(engine) as db:
                problems.extend(check_gold_claims(db))
                problems.extend(check_food_table_state(db))
            engine.dispose()
            database_checked = True
        except Exception as exc:  # noqa: BLE001 - report, do not crash the audit
            problems.append(f"数据库检查未完成（{type(exc).__name__}）：条款 1/3 未验证")

    report = {
        "gold_thresholds_reference": PLAN_GOLD_THRESHOLDS,
        "planning_only_exercises": list(PLANNING_ONLY_IDS),
        "database_checked": database_checked,
        "problems": problems,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if problems:
        print("\nFAIL: 能力声明与证据不一致")
        return 1
    if not database_checked:
        print(
            "\nOK（代码层）：Gold 阈值与 planning-only 声明一致。"
            "条款 1/3 需 --database-url 指向已迁移数据库，"
            "或由 tests/test_phase_capability_honesty.py 覆盖。"
        )
        return 0
    print("\nOK: 能力声明未超出可提供证据的范围")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
