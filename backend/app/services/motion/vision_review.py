"""DeepSeek visual review of redacted keyframes (spec 4.6).

Security constraints enforced here:
- Keyframes are the already-redacted JPEG previews embedded in the Worker receipt
  (solid-canvas skeleton / blurred frames). We never accept an arbitrary external
  URL from the client; images are sent as inlined data: JPEG bytes.
- The prompt serializes ONLY action candidates, joint measurements, timestamps
  and the output JSON schema. It must never contain "只要有人就给中等以上置信度"
  style anchoring: insufficient evidence must make the model answer `unknown`.
- The model output is treated as untrusted data and re-validated with Pydantic.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.core.config import settings

logger = logging.getLogger("healthmate.motion.vision_review")

VISION_OPERATION = "deepseek_vision_review"
MAX_EVIDENCE_IDS = 4
MAX_FINDINGS = 4
MAX_REASON_LEN = 160
MAX_OBS_LEN = 100


class FrameFinding(BaseModel):
    frame_id: str = Field(pattern=r"^frame:\d+$")
    observation: str = Field(min_length=2, max_length=MAX_OBS_LEN)
    advice: str = Field(min_length=2, max_length=MAX_OBS_LEN)


class VisionReview(BaseModel):
    """Structured visual review returned by DeepSeek (before server-side validation)."""

    label_id: str
    evidence_ids: list[str] = Field(default_factory=list, max_length=MAX_EVIDENCE_IDS)
    findings: list[FrameFinding] = Field(default_factory=list, max_length=MAX_FINDINGS)
    reason: str = Field(default="", max_length=MAX_REASON_LEN)


class ReviewValidationError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def validate_review(review: VisionReview, candidate_ids: set[str], frame_ids: set[str]) -> None:
    """Raise ReviewValidationError when the model hallucinates. Pure deterministic check."""
    if review.label_id not in (candidate_ids | {"unknown"}):
        raise ReviewValidationError("unsupported_label")
    if not set(review.evidence_ids).issubset(frame_ids):
        raise ReviewValidationError("invented_evidence")
    if any(item.frame_id not in frame_ids for item in review.findings):
        raise ReviewValidationError("invented_frame")


def build_vision_facts_prompt(candidate_ids: list[str], measurements: dict) -> str:
    """Only whitelisted facts leave the server; no free text, no user identity."""
    schema_hint = {
        "label_id": "one of the candidate exercise ids, or \"unknown\" when evidence is insufficient",
        "evidence_ids": ["frame_id strings you actually examined, at most 4"],
        "findings": [
            {"frame_id": "frame:N", "observation": "2-100 chars", "advice": "2-100 chars"}
        ],
        "reason": "why you chose this label, <=160 chars",
    }
    return (
        "你是动作复核员。只能依据提供的关键帧和数值事实判断，禁止猜测未观察到的内容。"
        "如果画面人物不完整、动作周期不完整、关键点不可见或你无法确认动作，label_id 必须填 \"unknown\"。"
        "禁止仅凭画面中有人就给出动作类别。\n"
        f"候选动作：{json.dumps(candidate_ids, ensure_ascii=False)}\n"
        f"关节与测量事实：{json.dumps(measurements, ensure_ascii=False, sort_keys=True)}\n"
        f"输出必须是且仅是 JSON：{json.dumps(schema_hint, ensure_ascii=False)}"
    )


def build_vision_messages(frames: list[tuple[str, bytes]], facts: dict) -> list[dict]:
    content: list[dict] = [
        {"type": "text", "text": build_vision_facts_prompt(facts["candidates"], facts["measurements"])}
    ]
    for frame_id, jpeg in frames[:4]:
        content.append({"type": "text", "text": f"frame_id={frame_id}"})
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii"),
                    "detail": "low",
                },
            }
        )
    return [{"role": "user", "content": content}]


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no json object in model output")
    return json.loads(text[start : end + 1])


def call_vision_review(
    *,
    facts: dict,
    frames: list[tuple[str, bytes]],
    timeout: float = 40.0,
) -> tuple[VisionReview, dict]:
    """POST the bounded review request to DeepSeek. Returns (review, raw_meta).

    Synchronous on purpose: the worker-complete endpoint runs in a threadpool and
    the orchestrator stages are sequential. Raises RuntimeError/ValueError on any
    transport or parse failure so the caller marks review_status=unavailable
    without blind retries.
    """
    api_key = settings.deepseek_api_key
    if not api_key:
        raise RuntimeError("deepseek_api_key not configured")
    messages = build_vision_messages(frames, facts)
    payload = {
        "model": settings.deepseek_vision_model or settings.deepseek_model,
        "messages": messages,
        "temperature": 0.0,
        "stream": False,
        "max_tokens": 700,
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    url = settings.deepseek_base_url.rstrip("/") + "/chat/completions"
    with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        response = client.post(url, headers=headers, json=payload)
    if not response.is_success:
        raise RuntimeError(f"deepseek_vision_http_{response.status_code}")
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage") or {}
    review = VisionReview.model_validate(_extract_json(content))
    meta = {
        "provider_request_id": data.get("id"),
        "tokens": int(usage.get("total_tokens") or 0),
        "model": payload["model"],
    }
    return review, meta


def decode_preview_jpeg(frame: dict) -> bytes | None:
    """Return the raw JPEG bytes of a receipt frame, or None. Desensitized only."""
    encoded = frame.get("image_b64")
    if not isinstance(encoded, str):
        return None
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return None
    if not raw.startswith(b"\xff\xd8") or not raw.endswith(b"\xff\xd9"):
        return None
    return raw
