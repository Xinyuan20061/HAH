# -*- coding: utf-8 -*-
"""Grounded text commentary (规格 §7.2 / §7.3)。

保留 V1 兼容入口（``call_text_summary``/``clean_summary_text``/``fallback_summary``）
供 orchestrator 现状链路使用；新增 V2 入口 ``build_summary(coach_review, metrics,
evidence, catalog) -> {text, primary_next_step, source}``。

R01 修复：
- 模型获得有意义的观察（已有中文 observation/finding/advice、动作知识条目、可用
  测量），而不是内部英文 event 码；正文只出现自然中文，ID 存结构化引用。
- ``clean_summary_text`` 升级为检测并拒绝技术字段污染（字段名/英文 ID/帧号/
  分值/事件名）；出现污染整段转本地可读文案并保留内部错误记录，不能靠删几个
  英文词硬凑病句。

降级模板按动作家族组织（``knowledge_zh.ACTION_FAMILY``），优先复用 Worker 已有
中文 finding/advice；卧姿/俯卧/静态/靠墙动作不套用"站稳/全身入镜"模板。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.core.config import settings
from app.services.motion.coach_review import (
    CoachReview,
    find_technical_leaks,
)
from app.services.motion import knowledge_zh

logger = logging.getLogger("healthmate.motion.text_summary")

TEXT_OPERATION = "deepseek_grounded_summary"
MAX_SUMMARY_CHARS = 300

# Health boundary: this product is general fitness, not medical advice.
MEDICAL_BLOCKLIST = (
    "治疗", "诊断", "治愈", "康复处方", "处方", "疾病", "损伤治疗",
    "消炎", "止痛药", "病症", "临床",
)


class GroundedSummary(BaseModel):
    text: str = Field(min_length=8, max_length=MAX_SUMMARY_CHARS)


def contains_medical_claim(text: str) -> bool:
    return any(word in text for word in MEDICAL_BLOCKLIST)


def is_polluted(text: str | None) -> bool:
    """正文是否含技术字段污染（帧号/英文动作 ID/字段名/分值/事件名）。"""
    return bool(find_technical_leaks(text))


def clean_summary_text(text: str | None) -> str | None:
    """返回干净的自然中文点评；长度/医学/技术字段污染任一不过则返回 None。

    污染时整段弃用（由调用方降级为本地可读文案），并在内部日志记录泄漏片段，
    不在用户可见正文里硬删英文词造成病句。
    """
    if not isinstance(text, str):
        return None
    text = " ".join(text.split()).strip()
    if len(text) < 8 or len(text) > MAX_SUMMARY_CHARS:
        return None
    if contains_medical_claim(text):
        return None
    leaks = find_technical_leaks(text)
    if leaks:
        logger.warning("summary text rejected for technical leaks: %s", leaks)
        return None
    return text


def fallback_summary(decision_state: str, label_id: str | None, reason_code: str) -> str:
    """确定性本地文案。按动作家族组织，卧姿/俯卧/静态不套用站姿话术。"""
    family = knowledge_zh.action_family(label_id)
    entries = knowledge_zh.knowledge_for_action(label_id)
    name_zh = ""
    if label_id:
        action = None
        from app.services.motion import catalog as catalog_mod
        action = catalog_mod.get_action(label_id)
        if action:
            name_zh = action["name_zh"]
    name = name_zh or (label_id or "这段动作")

    if decision_state == "abstained":
        return (
            "这段视频的有效画面不足，暂时无法判断动作。"
            "请拍一段 5-20 秒、光线充足、人物和器械清楚的视频后再试。"
        )
    if decision_state == "uncertain":
        mapping = {
            "REVIEW_UNAVAILABLE_OR_UNCERTAIN": "AI 视觉复核暂不可用或证据不足，暂时不能确定动作类别。",
            "UNSUPPORTED_REVIEW": "AI 复核结果与本地候选不一致，已按谨慎原则保留判断。",
            "MODEL_DISAGREEMENT": "本地姿态识别与 AI 视觉复核意见不一致，暂不下结论。",
        }
        return mapping.get(reason_code, "暂时不能确定动作，建议重新拍摄或手动选择动作类别。")

    # 已识别但无评分器：给一条与动作家族匹配的执行要点，而不是统一"保持稳定"。
    tip = entries[0]["tip"] if entries else "先把动作做慢、做顺，再逐渐增加次数。"
    return f"画面更像{name}。{tip}这段视频还不足以给出可靠的数值评分。"


def build_summary_prompt(facts: dict) -> str:
    """R01：喂给模型有意义的中文观察与知识要点，禁止它引用内部 ID。"""
    return (
        "你是健身教练，只依据下面给出的中文观察、动作技术要点和可用测量，写一段 80-160 字中文整体点评。"
        "禁止重新判断动作类别，禁止写评分、次数（除非测量里明确给出次数）或医学/诊断/治疗表述，"
        "禁止编造未给出的数值。正文只出现自然中文；不要复述字段名、英文动作 ID、帧编号、模型分值或内部事件名。"
        "引用关系放在结构化引用里，不要写进正文。\n"
        f"事实：{json.dumps(facts, ensure_ascii=False, sort_keys=True)}\n"
        '输出 JSON：{"text": "点评正文"}。'
    )


def call_text_summary(*, facts: dict, timeout: float = 40.0) -> tuple[str, dict]:
    api_key = settings.deepseek_api_key
    if not api_key:
        raise RuntimeError("deepseek_api_key not configured")
    payload = {
        "model": settings.deepseek_model,
        "messages": [
            {
                "role": "system",
                "content": "你只输出 JSON。不输出 Markdown、不输出解释。",
            },
            {"role": "user", "content": build_summary_prompt(facts)},
        ],
        "temperature": 0.2,
        "stream": False,
        "max_tokens": 400,
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    url = settings.deepseek_base_url.rstrip("/") + "/chat/completions"
    with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        response = client.post(url, headers=headers, json=payload)
    if not response.is_success:
        raise RuntimeError(f"deepseek_summary_http_{response.status_code}")
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(content[content.find("{") : content.rfind("}") + 1])
        summary = GroundedSummary.model_validate(parsed)
    except (ValueError, KeyError, ValidationError):
        raise ValueError("invalid grounded summary payload") from None
    usage = data.get("usage") or {}
    return summary.text, {
        "provider_request_id": data.get("id"),
        "tokens": int(usage.get("total_tokens") or 0),
        "model": payload["model"],
    }


# --------------------------------------------------------------------------- #
# V2：CoachReview -> 中文整体点评（契约 §8：build_summary）
# --------------------------------------------------------------------------- #
def _reps_text(metrics: dict | None, canonical_id: str | None) -> str:
    """仅当测量的就是同一动作且明确给出次数时，才把次数写进中文。"""
    if not metrics:
        return ""
    if metrics.get("exercise_id") and canonical_id and metrics["exercise_id"] != canonical_id:
        return ""
    reps = metrics.get("reps")
    if isinstance(reps, int) and reps > 0:
        return f"这段记录到约 {reps} 次。"
    return ""


def summarize_from_local_findings(
    evidence: list[dict] | None,
    label_id: str | None,
    metrics: dict | None = None,
) -> dict[str, str]:
    """视觉缺失时，优先复用 Worker 已生成的中文 finding/advice 组织点评。

    T08：review 缺失但 Worker 有 finding/advice -> 正常展示已有中文内容，
    不退回英文 event。卧姿/俯卧/静态动作不套用"站稳"模板。
    """
    evidence = evidence or []
    name = ""
    from app.services.motion import catalog as catalog_mod
    action = catalog_mod.get_action(label_id or "") if label_id else None
    if action:
        name = action["name_zh"]

    findings = [e for e in evidence if isinstance(e, dict) and (e.get("finding") or e.get("observation"))]
    advices = [e.get("advice") or e.get("next_step") for e in evidence if isinstance(e, dict) and (e.get("advice") or e.get("next_step"))]

    parts: list[str] = []
    if name:
        parts.append(f"画面更像{name}。")
    parts.append(_reps_text(metrics, label_id).rstrip("。"))
    parts = [p for p in parts if p]

    if findings:
        # 复用已有中文观察，不取英文 event 码。
        obs = findings[0].get("finding") or findings[0].get("observation") or ""
        if obs and not is_polluted(obs):
            parts.append(str(obs)[:120])

    entries = knowledge_zh.knowledge_for_action(label_id)
    primary = advices[0] if advices else (entries[0]["tip"] if entries else "先把动作做慢做顺。")

    text = "".join(parts) if parts else "已记录这段练习的画面，下面按关键时刻给出讲解。"
    if len(text) < 20:
        text = text + (entries[0]["point"] if entries else "可先回看关键时刻，留意动作的幅度与控制。")
    text = text[:MAX_SUMMARY_CHARS]
    return {
        "text": text,
        "primary_next_step": str(primary)[:120],
        "source": "local_observations",
    }


def build_summary(
    coach_review: CoachReview | None,
    metrics: dict | None = None,
    evidence: list[dict] | None = None,
    catalog: Any = None,
) -> dict[str, str]:
    """契约 §8 入口：``{text, primary_next_step, source}``。

    - coach_review 有效且正文干净 -> 直接采用其 summary / primary_next_step；
    - coach_review 缺失或正文被污染 -> 降级到本地中文观察 + 动作知识条目，
      绝不退回英文 event 码，也不硬删英文词凑病句。
    """
    if isinstance(coach_review, CoachReview):
        text = clean_summary_text(coach_review.summary)
        if text and not is_polluted(coach_review.identification_reason or ""):
            reps = _reps_text(metrics, coach_review.canonical_id)
            if reps and reps not in text:
                text = reps + text
            return {
                "text": text[:MAX_SUMMARY_CHARS],
                "primary_next_step": (coach_review.primary_next_step or "")[:120],
                "source": "visual_coach",
            }
        # 模型正文污染 -> 整段弃用，降级到本地可读文案。
        logger.warning("coach_review summary polluted; downgrading to local observations")
        label_id = coach_review.canonical_id
    else:
        label_id = None

    return summarize_from_local_findings(evidence, label_id, metrics)


__all__ = [
    "TEXT_OPERATION",
    "GroundedSummary",
    "contains_medical_claim",
    "is_polluted",
    "clean_summary_text",
    "fallback_summary",
    "build_summary_prompt",
    "call_text_summary",
    "build_summary",
    "summarize_from_local_findings",
]
