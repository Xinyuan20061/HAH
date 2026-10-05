from __future__ import annotations
import json
from dataclasses import dataclass
from typing import Callable, Any
from sqlalchemy.orm import Session
from app.models import AgentActionAudit
from app.services.redaction import redact
from app.services.evaluation import record_metric


@dataclass(frozen=True)
class ActionSpec:
    key: str
    title: str
    description: str
    risk_level: str = "low"
    requires_confirmation: bool = True
    allowed_sources: tuple[str, ...] = ("user", "agent")


ACTION_REGISTRY = {
    x.key: x
    for x in [
        ActionSpec(
            "plan.apply",
            "加入本周计划",
            "把 Agent 建议的计划写入用户本周计划。",
            "low",
            True,
        ),
        ActionSpec(
            "plan.replan.apply",
            "应用重规划变更",
            "把已确认的重规划写入计划：已完成项目冻结，未完成项目不判定为失败。",
            "low",
            True,
        ),
        ActionSpec(
            "goal.adjustment.apply",
            "应用动态目标",
            "把规则引擎计算出的目标建议写入目标设置。",
            "medium",
            True,
        ),
        ActionSpec(
            "diet.ai.finalize",
            "保存 AI 识餐记录",
            "把用户确认/校正后的识餐结果写入饮食记录。",
            "low",
            True,
        ),
        ActionSpec(
            "experiment.start",
            "启动健康微实验",
            "按用户选择的方案启动短周期自我观察，并冻结基线与评估口径。",
            "low",
            True,
            ("user",),
        ),
        ActionSpec(
            "experiment.finish",
            "完成健康微实验",
            "冻结实验期观测结果并生成不作因果承诺的个人变化报告。",
            "low",
            True,
            ("user",),
        ),
        ActionSpec(
            "experiment.cancel",
            "取消健康微实验",
            "停止当前微实验；不会删除已经存在的健康记录。",
            "low",
            True,
            ("user",),
        ),
        ActionSpec("policy.episode.start", "开始个人策略验证", "按已确认协议开始一个可撤销的个人观察周期。", "low", True, ("user",)),
        ActionSpec("policy.episode.finish", "复查个人策略周期", "结束周期并按证据门控生成可撤销裁决。", "low", True, ("user",)),
        ActionSpec("policy.episode.stop", "停止个人策略周期", "停止当前周期，不删除已经存在的健康记录。", "low", True, ("user",)),
        ActionSpec("policy.episode.rereview", "重新复查已修复周期", "保留既有复查版本，并按本人确认的新证据生成一个新版本。", "low", True, ("user",)),
        ActionSpec("policy.memory.reset", "清除个人策略记忆", "按用户指定范围清除策略后验的影响。", "medium", True, ("user",)),
        ActionSpec(
            "privacy.export",
            "导出个人数据",
            "生成当前账户健康数据导出包。",
            "medium",
            True,
            ("user",),
        ),
        ActionSpec(
            "privacy.account.delete",
            "删除账户和数据",
            "永久删除账户数据库记录和媒体文件。",
            "critical",
            True,
            ("user",),
        ),
    ]
}


def list_actions():
    return [
        {
            "key": x.key,
            "title": x.title,
            "description": x.description,
            "risk_level": x.risk_level,
            "requires_confirmation": x.requires_confirmation,
            "allowed_sources": list(x.allowed_sources),
        }
        for x in ACTION_REGISTRY.values()
    ]


def execute_action(
    db: Session,
    user_id: int,
    action_key: str,
    executor: Callable[[], Any],
    *,
    confirmed: bool,
    source: str = "user",
    run_id: int | None = None,
    input_data: dict | None = None,
):
    spec = ACTION_REGISTRY.get(action_key)
    if not spec:
        raise ValueError("未注册的 Action")
    audit = AgentActionAudit(
        user_id=user_id,
        run_id=run_id,
        action_key=action_key,
        risk_level=spec.risk_level,
        requires_confirmation=spec.requires_confirmation,
        status="pending",
        input_json=json.dumps(
            redact(input_data or {}), ensure_ascii=False, default=str
        ),
    )
    db.add(audit)
    db.flush()
    if source not in spec.allowed_sources:
        audit.status = "blocked"
        audit.block_reason = "source_not_allowed"
        record_metric(
            db,
            user_id,
            "agent_action_blocked",
            1,
            "count",
            "action_registry",
            False,
            {"action": action_key, "reason": "source_not_allowed"},
        )
        db.commit()
        return {"executed": False, "audit_id": audit.id, "reason": "source_not_allowed"}
    if spec.requires_confirmation and not confirmed:
        audit.status = "awaiting_confirmation"
        db.commit()
        return {"executed": False, "audit_id": audit.id, "requires_confirmation": True}
    try:
        result = executor()
        audit.status = "executed"
        audit.output_json = json.dumps(
            redact(result if isinstance(result, dict) else {"result": str(result)}),
            ensure_ascii=False,
            default=str,
        )
        record_metric(
            db,
            user_id,
            "agent_action_executed",
            1,
            "count",
            "action_registry",
            True,
            {"action": action_key, "risk": spec.risk_level},
        )
        db.commit()
        return {"executed": True, "audit_id": audit.id, "result": result}
    except Exception as exc:
        audit.status = "failed"
        audit.block_reason = type(exc).__name__
        record_metric(
            db,
            user_id,
            "agent_action_failed",
            1,
            "count",
            "action_registry",
            False,
            {"action": action_key},
        )
        db.commit()
        raise
