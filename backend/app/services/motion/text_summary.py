"""Grounded text commentary (spec 4.4 / 4.2, second DeepSeek call).

The second call ONLY receives already-decided structured facts: the locked
recognition result, keyframe events, measurement fields and the user's confirmed
goal. It may NOT re-classify the exercise or write scores. Its output is treated
as untrusted: the server re-validates reference existence, character limits and a
medical-claim blocklist; on any failure we fall back to deterministic structured
wording and mark degraded=true. The model can never invent image URLs.
"""

from __future__ import annotations

import json
import logging

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.core.config import settings
from app.services.motion.decision import LABEL_ZH

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


def clean_summary_text(text: str | None) -> str | None:
    if not isinstance(text, str):
        return None
    text = " ".join(text.split()).strip()
    if len(text) < 8 or len(text) > MAX_SUMMARY_CHARS:
        return None
    if contains_medical_claim(text):
        return None
    return text


def fallback_summary(decision_state: str, label_id: str | None, reason_code: str) -> str:
    """Deterministic structured wording used when the model call fails or is invalid."""
    zh = LABEL_ZH.get(label_id or "", "")
    if decision_state == "abstained":
        return (
            "这段视频的有效画面不足，暂时无法判断动作。"
            "请拍摄 5-20 秒、全身入镜、光线充足的视频后再试。"
        )
    if decision_state == "uncertain":
        mapping = {
            "REVIEW_UNAVAILABLE_OR_UNCERTAIN": "云端视觉复核暂不可用或证据不足，暂时不能确定动作类别。",
            "UNSUPPORTED_REVIEW": "云端复核结果与本地候选不一致，已按谨慎原则保留判断。",
            "MODEL_DISAGREEMENT": "本地姿态识别与云端视觉复核意见不一致，暂不下结论。",
        }
        return mapping.get(reason_code, "暂时不能确定动作，建议重新拍摄或手动选择动作类别。")
    name = zh or (label_id or "该动作")
    return (
        f"画面更像{name}。可以先放慢动作速度、保持身体稳定；"
        "这段视频还不足以给出可靠的动作评分。"
    )


def build_summary_prompt(facts: dict) -> str:
    return (
        "你是健身教练，只依据下面锁定的结构化事实写一段 80-160 字中文点评。"
        "禁止重新判断动作类别，禁止写任何评分、次数或医学/诊断/治疗表述，"
        "禁止编造未给出的数值。只能引用给出的关键帧 frame:id 或测量字段。\n"
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
