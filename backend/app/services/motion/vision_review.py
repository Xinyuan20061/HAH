# -*- coding: utf-8 -*-
"""DeepSeek 视觉复核（规格 §7.1：一次综合视觉分析）。

本模块保留 V1 兼容入口（``VisionReview``/``FrameFinding``/``call_vision_review``/
``validate_review``/``decode_preview_jpeg``）供 orchestrator 现状链路使用，同时新增
V2 一次综合视觉入口 ``run_visual_review(frames, context, catalog) -> CoachReview``：
一次视觉请求同时完成"动作判断 + 关键帧观察 + 阶段 + 点评草案"。

安全约束（与历史一致并加强）：
- 关键帧只接受 Worker 回执中已脱敏的 JPEG 字节，绝不接受客户端任意 URL；
- 提示词只放行候选、可见/缺失部位、测量事实与动作知识条目，
  原始日志/trace/密钥/用户身份一律不进提示词；
- 模型输出视为不可信数据：先 pydantic 结构校验，再 ``semantically_validate``
  语义校验；任何失败都抛错，由调用方降级，绝不盲信"输出 JSON"一句提示。
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
from typing import Any, Iterable

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.core.config import settings
from app.services.motion.coach_review import (
    CoachReview,
    CoachReviewValidationError,
    assert_coach_review_valid,
)
from app.services.motion import catalog as catalog_mod
from app.services.motion import knowledge_zh

logger = logging.getLogger("healthmate.motion.vision_review")

VISION_OPERATION = "deepseek_vision_review"
MAX_EVIDENCE_IDS = 4
MAX_FINDINGS = 4
MAX_REASON_LEN = 160
MAX_OBS_LEN = 100
MAX_COACH_FRAMES = 8


# --------------------------------------------------------------------------- #
# V1 兼容模型（orchestrator 现状链路 + 旧测试直接构造，保持字段不变）
# --------------------------------------------------------------------------- #
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


def validate_review(
    review: VisionReview,
    candidate_ids: set[str],
    frame_ids: set[str],
) -> None:
    """Raise ReviewValidationError on hallucinated references.

    R06 / 规格 §6.3：开放类别——不再因为 ``label_id`` 不在本地候选集合内就拒绝。
    视觉模型可以返回目录已有 ID，也可以用 novel_label_zh 描述新动作。这里只做
    确定性证据校验：引用的帧必须真的属于本次素材。
    """
    if not set(review.evidence_ids).issubset(frame_ids):
        raise ReviewValidationError("invented_evidence")
    if any(item.frame_id not in frame_ids for item in review.findings):
        raise ReviewValidationError("invented_frame")


# --------------------------------------------------------------------------- #
# V1 提示词（R07：按任务分别判断，开放类别，不再一刀切强制 unknown）
# --------------------------------------------------------------------------- #
def build_vision_facts_prompt(candidate_ids: list[str], measurements: dict) -> str:
    """Only whitelisted facts leave the server; no free text, no user identity."""
    schema_hint = {
        "label_id": "目录中的动作 ID；若可见动作不在候选内，仍填你判断最接近的 ID 或 novel_label_zh",
        "evidence_ids": ["frame_id strings you actually examined, at most 4"],
        "findings": [
            {"frame_id": "frame:N", "observation": "2-100 字自然中文", "advice": "2-100 字自然中文"}
        ],
        "reason": "为什么这样判断，<=160 字，自然中文",
    }
    return (
        "你是给普通运动者看动作的教练。先独立描述主运动者实际做了什么，再对照下面的候选与数值指出相符或冲突。"
        "允许识别候选之外的动作。没拍到脚不等于不能识别上肢动作；没有完整周期不要统计完整次数，"
        "但仍可以判断动作类别并解释可见阶段。"
        "人物局部不完整只关闭对应部位的评价：识别弯举可依赖上半身，计完整次数才要求完整周期，"
        "下肢评分才要求对应下肢关节可见。字幕、课程名称、教学小窗只作参考，不覆盖主画面实际动作。\n"
        f"候选动作：{json.dumps(candidate_ids, ensure_ascii=False)}\n"
        f"关节与测量事实：{json.dumps(measurements, ensure_ascii=False, sort_keys=True)}\n"
        "用自然中文作答，不要复述字段名、英文动作 ID、帧编号、模型分值或内部事件名。\n"
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


# --------------------------------------------------------------------------- #
# V2：一次综合视觉分析 -> CoachReview（契约 §8 / 规格 §7.1）
# --------------------------------------------------------------------------- #
def _norm_coach_frame(frame: Any) -> tuple[str, int, bytes]:
    """Accept dict {frame_id,timestamp_ms,jpeg} or (frame_id, timestamp_ms, jpeg)."""
    if isinstance(frame, dict):
        fid = str(frame["frame_id"])
        ts = int(frame.get("timestamp_ms") or 0)
        jpeg = frame.get("jpeg") or frame.get("image_b64") or b""
        if isinstance(jpeg, str):
            jpeg = base64.b64decode(jpeg, validate=False)
        return fid, ts, bytes(jpeg)
    fid, ts, jpeg = frame[0], frame[1], frame[2]
    return str(fid), int(ts), bytes(jpeg)


def build_coach_prompt(context: dict, catalog: Any = None) -> str:
    """§7.1 提示词：先独立观察，再对照候选；开放类别；只放行白名单事实。"""
    catalog = catalog or catalog_mod
    candidates = context.get("candidates") or []
    measurements = context.get("measurements") or {}
    visible = context.get("visible_regions") or []
    missing = context.get("missing_regions") or []
    local_label = context.get("local_label")
    confirmed_goal = context.get("confirmed_goal")

    cand_lines = []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        source = c.get("source") or "unknown"
        label = c.get("canonical_id") or c.get("source_label") or ""
        cand_lines.append(f"- 来源 {source}：{label}")

    knowledge_lines: list[str] = []
    seed_ids = [c.get("canonical_id") for c in candidates if isinstance(c, dict) and c.get("canonical_id")]
    if local_label:
        seed_ids.insert(0, local_label)
    seen: set[str] = set()
    for sid in seed_ids:
        if not sid or sid in seen:
            continue
        seen.add(sid)
        for line in knowledge_zh.knowledge_text_for_action(sid):
            knowledge_lines.append(f"- {line}")

    schema_hint = {
        "canonical_id": "目录动作 ID，开放类别时填 null",
        "novel_label_zh": "候选外新动作的中文名称（无则 null）",
        "identification": "identified | likely | unknown",
        "identification_reason": "<=180 字，自然中文",
        "summary": "20-240 字整体点评，自然中文",
        "primary_next_step": "<=120 字，一条可执行建议",
        "frame_notes": [
            {
                "frame_id": "你实际观察过的帧 id",
                "phase": "<=24 字阶段名，如 抬起阶段",
                "observation": "4-160 字看到什么",
                "explanation": "4-180 字为什么留意",
                "next_step": "4-120 字下一遍怎么做",
                "advice_kind": "observed_correction | general_tip | capture_tip",
                "evidence_refs": [
                    {"frame_ids": ["引用的帧 id"], "start_ms": 0, "end_ms": 0}
                ],
            }
        ],
    }

    parts = [
        "你是一位给普通运动者讲动作的教练。先观察主运动者实际做了什么，再参考模型候选。",
        "允许识别候选之外的动作；没看到脚部不等于不能识别上肢动作。",
        "没有完整周期时不要统计完整次数，但仍可以识别类别和解释可见阶段。",
        "用正常中文，直接告诉用户这段练习最值得保留和调整的一点。",
        "不要向用户复述字段名、英文动作 ID、帧编号、模型分值或内部事件名。",
        "不要凭一张静态图推断速度、借力、稳定性变化；动态判断必须引用时间范围。",
        "若没有发现明确问题，给具体执行要点，不能为了点评而编造错误。",
        "允许说明某一处被遮挡、看不清；不要把局部不可见扩大为整段无效。",
        "每条建议区分 observed_correction（观察到的问题）与 general_tip（一般要点）。",
    ]
    if visible:
        parts.append(f"画面可见部位：{json.dumps(visible, ensure_ascii=False)}。")
    if missing:
        parts.append(f"画面缺失部位：{json.dumps(missing, ensure_ascii=False)}（只关闭对应部位评价，不要据此拒识）。")
    if measurements:
        parts.append(f"可用测量：{json.dumps(measurements, ensure_ascii=False, sort_keys=True)}。")
    if cand_lines:
        parts.append("候选（仅作参考，不代表已确定，必须先独立判断再对照）：\n" + "\n".join(cand_lines))
    if knowledge_lines:
        parts.append("相关动作技术要点（供你对照，不是替你下结论）：\n" + "\n".join(knowledge_lines))
    if confirmed_goal:
        parts.append(f"用户已确认的目标：{confirmed_goal}。")
    parts.append(f"输出必须是且仅是 JSON：{json.dumps(schema_hint, ensure_ascii=False)}")
    return "\n".join(parts)


def run_visual_review(
    frames: list,
    context: dict,
    catalog: Any = None,
    *,
    timeout: float = 40.0,
) -> CoachReview:
    """一次视觉请求完成判断 + 关键帧观察 + 阶段 + 点评草案。

    Parameters
    ----------
    frames:
        按时间排序的关键帧，每项为 dict ``{frame_id, timestamp_ms, jpeg,
        visible_regions?}`` 或三元组 ``(frame_id, timestamp_ms, jpeg)``。
    context:
        ``{video_duration_ms, candidates, measurements, visible_regions,
        missing_regions, local_label, confirmed_goal}``。
    catalog:
        动作目录访问器（默认 ``app.services.motion.catalog``）。

    Raises
    ------
    RuntimeError
        未配置密钥 / HTTP 失败。
    ValueError / CoachReviewValidationError
        输出无法解析、结构校验失败或语义校验失败（调用方据此降级）。
    """
    api_key = settings.deepseek_api_key
    if not api_key:
        raise RuntimeError("deepseek_api_key not configured")

    catalog = catalog or catalog_mod
    norm = [_norm_coach_frame(f) for f in (frames or [])][:MAX_COACH_FRAMES]
    valid_frame_ids = {fid for fid, _, _ in norm}
    duration = context.get("video_duration_ms")

    content: list[dict] = [{"type": "text", "text": build_coach_prompt(context, catalog)}]
    for fid, ts, jpeg in norm:
        content.append({"type": "text", "text": f"frame_id={fid} @ {ts}ms"})
        if jpeg:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii"),
                        "detail": "low",
                    },
                }
            )

    payload = {
        "model": settings.deepseek_vision_model or settings.deepseek_model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.0,
        "stream": False,
        "max_tokens": 1400,
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    url = settings.deepseek_base_url.rstrip("/") + "/chat/completions"
    with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        response = client.post(url, headers=headers, json=payload)
    if not response.is_success:
        raise RuntimeError(f"deepseek_vision_http_{response.status_code}")
    data = response.json()
    raw = data["choices"][0]["message"]["content"]

    parsed = _extract_json(raw)
    review = CoachReview.model_validate(parsed)
    # 结构校验之后强制执行语义校验：禁止只靠"输出 JSON"提示词信任内容。
    return assert_coach_review_valid(
        review,
        valid_frame_ids=valid_frame_ids,
        video_duration_ms=duration,
    )


__all__ = [
    "VISION_OPERATION",
    "FrameFinding",
    "VisionReview",
    "ReviewValidationError",
    "validate_review",
    "build_vision_facts_prompt",
    "build_vision_messages",
    "call_vision_review",
    "decode_preview_jpeg",
    "build_coach_prompt",
    "run_visual_review",
]
