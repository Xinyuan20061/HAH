from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

import sys

from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException

from app.core.config import settings
from app.core.database import build_engine
from app.models import User
from app.services.agent.evaluation import (
    attach_provenance,
    evaluate_agent_cases,
    render_markdown,
)
from app.services.agent import orchestrator
from app.services.agent.orchestrator import detect_intent
from app.services.ai.gateway import AIResult
from app.services.safety import evaluate_message

HARNESS_CONFIG = {
    "provider_mode": "mock",
    "mock_plan_items": 3,
    "urls": "stripped",
    "blocked_intents_excluded_from_mock": ["safety"],
}


class FakeProvider:
    provider_name = "fake"

    def __init__(self, expect_plan: bool):
        self.expect_plan = expect_plan

    async def chat(self, system: str, message: str) -> AIResult:
        if self.expect_plan:
            payload = {
                "reply": "已根据你的目标生成一份可执行的一周计划，请确认后再加入。",
                "facts_used": ["today", "goals", "recent_7d"],
                "plan": {
                    "title": "本周可执行计划",
                    "items": [
                        {
                            "date_offset": 0,
                            "category": "exercise",
                            "title": "深蹲与核心训练",
                            "description": "动作质量优先，保持核心稳定。",
                            "target": {"duration_min": 30},
                        },
                        {
                            "date_offset": 2,
                            "category": "exercise",
                            "title": "中等强度有氧",
                            "description": "快走、骑行或慢跑。",
                            "target": {"duration_min": 30},
                        },
                        {
                            "date_offset": 4,
                            "category": "recovery",
                            "title": "拉伸与活动度",
                            "description": "5-10 分钟拉伸。",
                            "target": {"duration_min": 15},
                        },
                    ],
                },
            }
        else:
            payload = {
                "reply": "这是根据权威资料给出的一般性建议。",
                "facts_used": ["today"],
                "plan": None,
            }
        return AIResult(json.dumps(payload, ensure_ascii=False), "fake")


class FailProvider:
    provider_name = "fail"

    async def chat(self, system: str, message: str) -> AIResult:
        raise HTTPException(503, "DeepSeek 服务暂时不可用（评测注入故障）")


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {error.msg}") from error
        if not isinstance(item, dict):
            raise ValueError(f"{path}:{line_number}: each line must be a JSON object")
        rows.append(item)
    return rows


async def _run_case(db: Session, user: User, case: dict) -> dict:
    message = case["message"]
    decision = evaluate_message(message)
    blocked = decision.action != "allow"
    intent = detect_intent(message)
    expect_plan = bool(case.get("expect_plan")) and not blocked
    if case.get("expect_fallback"):
        provider = FailProvider()
    else:
        provider = FakeProvider(expect_plan)
    original = orchestrator.get_provider
    orchestrator.get_provider = lambda u: provider
    try:
        result = await orchestrator.respond(db, user, message)
    finally:
        orchestrator.get_provider = original
    reply = result.get("reply") or ""
    trace = result.get("trace") or {}
    return {
        "intent": result.get("intent", intent),
        "blocked": blocked,
        "provider": result.get("provider"),
        "specialist": trace.get("specialist") or result.get("specialist"),
        "trace": trace,
        "reply": reply,
        "has_url": "http://" in reply or "https://" in reply,
        "plan_valid": bool(result.get("plan")),
        "knowledge_count": len(result.get("knowledge_sources") or []),
        "facts_used": result.get("facts_used", []),
        "safety_level": result.get("safety_level"),
        "disclaimer": result.get("disclaimer"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the HealthMate Health Agent offline.")
    parser.add_argument("--cases", required=True, type=Path, help="Fixed JSONL Agent case set")
    parser.add_argument("--database-url", help="Read-only benchmark database URL; defaults to app configuration")
    parser.add_argument("--output-dir", type=Path, default=Path("agent-benchmark-results"))
    args = parser.parse_args()

    case_path = args.cases.resolve()
    case_bytes = case_path.read_bytes()
    cases = read_jsonl(case_path)
    engine = build_engine(args.database_url or settings.effective_database_url)
    try:
        results = {}
        with Session(engine) as db:
            for case in cases:
                user = User(openid="agent-eval-" + uuid4().hex)
                db.add(user)
                db.flush()
                results[case["case_id"]] = asyncio.run(_run_case(db, user, case))
        report = evaluate_agent_cases(cases, results)
    finally:
        engine.dispose()
    attach_provenance(report, case_bytes, HARNESS_CONFIG)

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "report.json"
    markdown_path = output_dir / "report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))
    print(f"Reports: {json_path} | {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
