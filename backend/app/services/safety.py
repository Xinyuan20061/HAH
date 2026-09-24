from __future__ import annotations
import hashlib, re
from dataclasses import dataclass
from sqlalchemy.orm import Session
from app.models import SafetyEvent
from app.services.redaction import redact_text
from app.services.evaluation import record_metric
from app.core.config import settings

MEDICAL_DISCLAIMER = "HealthMate 仅提供一般健康管理与生活方式信息，不进行疾病诊断、处方或药物调整；出现急症或明显不适时应及时联系当地急救服务或专业医疗人员。"


@dataclass
class SafetyDecision:
    level: str = "normal"
    category: str = "general"
    action: str = "allow"
    rule: str = ""
    message: str = ""


RULES = [
    (
        "emergency",
        "critical",
        "block",
        "emergency_symptoms",
        [
            "胸痛",
            "呼吸困难",
            "昏迷",
            "意识不清",
            "大量出血",
            "抽搐",
            "严重过敏",
            "喘不过气",
        ],
    ),
    (
        "self_harm",
        "critical",
        "block",
        "self_harm",
        ["自杀", "自残", "不想活", "结束生命", "伤害自己"],
    ),
    (
        "medication",
        "high",
        "block",
        "medication_change",
        ["停药", "加药", "减药", "换药", "药量", "剂量怎么改", "能不能不吃药", "能停吗", "可以停", "停掉", "减半", "加量", "加倍", "停几天"],
    ),
    (
        "diagnosis",
        "high",
        "redirect",
        "diagnosis_request",
        ["我是不是得了", "帮我诊断", "确诊", "是什么病", "判断我有病"],
    ),
    (
        "extreme_diet",
        "high",
        "redirect",
        "extreme_restriction",
        ["一天不吃", "绝食", "只喝水", "每天500卡", "每天 500 卡", "500卡", "每天只吃", "催吐", "泻药减肥"],
    ),
    (
        "exercise_red_flag",
        "high",
        "redirect",
        "exercise_red_flag",
        ["运动后晕厥", "运动时晕厥", "晕倒", "突然晕", "晕过去", "关节明显肿胀", "无法负重", "突发剧烈疼痛"],
    ),
]

UNSAFE_OUTPUT_PATTERNS = [
    "自行停药",
    "立即停药",
    "自行加药",
    "自行增加剂量",
    "每天只吃500卡",
    "每天只吃 500 卡",
    "通过催吐",
    "使用泻药减肥",
]


def evaluate_message(message: str) -> SafetyDecision:
    text = (message or "").strip()
    for category, level, action, rule, patterns in RULES:
        if any(p in text for p in patterns):
            if category == "emergency":
                msg = "你描述的情况可能需要尽快获得专业医疗评估。若症状正在发生或迅速加重，请优先联系当地急救服务或立即前往医疗机构。"
            elif category == "self_harm":
                msg = "我不能继续生成训练或饮食计划。若你有立即伤害自己的风险，请尽快联系当地紧急服务、危机热线或身边可信任的人，并让自己处在有人陪伴的安全环境。"
            elif category == "medication":
                msg = "药物开始、停止或剂量调整需要由有资质的医疗专业人员结合具体情况决定。HealthMate 不会给出停药、换药或改剂量指令。"
            elif category == "diagnosis":
                msg = "仅凭聊天或健康记录不能可靠诊断疾病。我可以帮助你整理症状、时间线和需要向医生说明的信息，但不会给出确诊结论。"
            elif category == "exercise_red_flag":
                msg = "你描述的运动后表现不适合继续依赖自动训练建议。请停止相关动作并尽快由有资质的医疗专业人员评估；若症状严重或正在加重，应及时联系当地急救服务。"
            else:
                msg = "过度限制饮食、催吐或依赖泻药可能带来健康风险。HealthMate 不会生成极端节食方案，可以改为更温和、可持续的一般饮食与活动建议。"
            return SafetyDecision(level, category, action, rule, msg)
    return SafetyDecision()


def review_generated_advice(text: str) -> SafetyDecision:
    """Block a narrow set of clearly unsafe instructions in model output."""
    content = (text or "").replace("\n", "")
    matched = next((item for item in UNSAFE_OUTPUT_PATTERNS if item in content), "")
    if not matched:
        return SafetyDecision()
    return SafetyDecision(
        level="high",
        category="unsafe_generated_advice",
        action="block",
        rule="generated_advice_filter",
        message="AI 返回内容未通过健康安全复核，本次建议已停止展示。请改为咨询有资质的专业人员，或重新提出不涉及诊断、药物调整和极端饮食的问题。",
    )


def audit_decision(db: Session, user_id: int | None, message: str, d: SafetyDecision):
    if d.action == "allow":
        return None
    raw = (message or "").encode("utf-8")
    excerpt = (
        redact_text(message or "")[:80]
        if settings.retain_sensitive_audit_excerpt
        else ""
    )
    item = SafetyEvent(
        user_id=user_id,
        category=d.category,
        severity=d.level,
        action=d.action,
        matched_rule=d.rule,
        message_hash=hashlib.sha256(raw).hexdigest(),
        excerpt_redacted=excerpt,
    )
    db.add(item)
    record_metric(
        db,
        user_id,
        "safety_intercept",
        1,
        "count",
        "safety",
        True,
        {"category": d.category, "action": d.action},
    )
    return item
