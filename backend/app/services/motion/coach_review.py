# -*- coding: utf-8 -*-
"""CoachReview 契约模型与服务端语义校验（契约 §4 / 规格 §7.2）。

本模块是 C 包对外交付的结构化点评契约，供 A 包 orchestrator 与 E 包阶段任务
消费。它只做"纯代码、可离线判定"的结构与语义校验——绝不对模型输出抱信任。

结构校验（pydantic）：字段长度、Literal 取值、时间窗先后、列表上下限。
语义校验（``semantically_validate``）：结构通过之后强制执行：
  1. 所有 frame_id 属于本次素材与主运动者；evidence_refs 时间落在视频时长内。
  2. 动态结论（速度/借力/节奏/稳定性变化）引用含 >=2 个不同时刻的帧
     （frame_ids 去重后 >=2 或时间窗口跨度 > 0）。
  3. 正文未泄露技术字段（字段名/英文动作 ID/帧号/分值/事件名/provider/trace）。
  4. 不编造视觉不可得的负重、痛感、生理状态、肌肉激活、医学结论。
  5. 静态帧只确认姿态；未观察到错误时给执行要点，不硬编"膝内扣/塌腰/耸肩"。

注意：本模块不发起任何网络/模型调用，也不 import orchestrator/decision。
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

__all__ = [
    "EvidenceRef",
    "FrameNote",
    "CoachReview",
    "CoachReviewValidationError",
    "find_technical_leaks",
    "contains_fabrication",
    "is_dynamic_claim",
    "semantically_validate",
    "assert_coach_review_valid",
]


# --------------------------------------------------------------------------- #
# 契约 §4 结构模型（逐字照契约字段，禁止改动上下限）
# --------------------------------------------------------------------------- #
class EvidenceRef(BaseModel):
    frame_ids: list[str] = Field(min_length=1, max_length=6)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def ordered_window(self):
        if self.end_ms < self.start_ms:
            raise ValueError("invalid_evidence_window")
        return self


class FrameNote(BaseModel):
    frame_id: str
    phase: str = Field(max_length=24)
    observation: str = Field(min_length=4, max_length=160)
    explanation: str = Field(min_length=4, max_length=180)
    next_step: str = Field(min_length=4, max_length=120)
    advice_kind: Literal["observed_correction", "general_tip", "capture_tip"]
    evidence_refs: list[EvidenceRef] = Field(min_length=1, max_length=3)


class CoachReview(BaseModel):
    canonical_id: str | None = None
    novel_label_zh: str | None = Field(default=None, max_length=40)
    identification: Literal["identified", "likely", "unknown"]
    identification_reason: str = Field(max_length=180)
    summary: str = Field(min_length=20, max_length=240)
    primary_next_step: str = Field(max_length=120)
    frame_notes: list[FrameNote] = Field(default_factory=list, max_length=8)


class CoachReviewValidationError(ValueError):
    """语义校验失败。``codes`` 为机器可读违规码列表，供降级与诊断记录。"""

    def __init__(self, codes: list[str]):
        super().__init__(";".join(codes))
        self.codes = list(codes)


# --------------------------------------------------------------------------- #
# 技术字段泄露扫描（规格 §4.3 / §7.2 语义规则 4）
# --------------------------------------------------------------------------- #
# 正文（summary / identification_reason / observation / explanation / next_step）
# 只允许自然中文。以下任一命中即视为把调试/工程字段写进了用户可见正文：
#   frame:0 / frame_12 / f_012            -> 帧号
#   bicep_curl / squat_bottom /
#   candidate_score / raw_score /
#   local_worker / evidence_refs           -> 英文蛇形动作 ID / 字段名
#   NOT_RECOGNIZED / EVIDENCE_AGREEMENT    -> 大写下划线事件/原因码
#   deepseek / trace-xxx / provider /
#   score / confidence / scorer            -> provider / 模型分值词
_LEAK_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"frame\s*[:_]\s*\d+"),                       # frame:0 / frame_12
    re.compile(r"\bf_\d+\b"),                                # f_012
    re.compile(r"[a-z]+(?:_[a-z0-9]+)+"),                     # snake_case 英文 ID/字段
    re.compile(r"[A-Z][A-Z0-9]{2,}(?:_[A-Z0-9]+)*"),          # UPPER_CASE 事件/原因码
    re.compile(r"\bdeepseek\b", re.IGNORECASE),               # provider 名
    re.compile(r"trace[-_]?[a-z0-9]{4,}", re.IGNORECASE),     # trace-xxx
    re.compile(r"\bprovider\b", re.IGNORECASE),               # provider
    re.compile(r"\b(?:score|confidence|scorer|candidate)\b", re.IGNORECASE),
)


def find_technical_leaks(text: str | None) -> list[str]:
    """返回正文中命中的技术字段片段（去重）；空列表 = 干净。"""
    if not isinstance(text, str) or not text:
        return []
    hits: list[str] = []
    for pat in _LEAK_PATTERNS:
        for m in pat.finditer(text):
            hits.append(m.group(0))
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for h in hits:
        key = h.lower()
        if key not in seen:
            seen.add(key)
            out.append(h)
    return out


# --------------------------------------------------------------------------- #
# 视觉不可得内容扫描（语义规则 5：不编造负重/痛感/生理/肌肉/医学结论）
# --------------------------------------------------------------------------- #
_FABRICATION_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\d+(?:\.\d+)?\s*(?:kg|公斤|千克|斤|磅)"),     # 具体负重数字
    re.compile(r"负重|负荷重量|举起了?\s*\d"),
    re.compile(r"疼痛|酸痛|刺痛|发麻|受伤|拉伤|扭伤|不适"),
    re.compile(r"肌肉激活|发力感|充血|泵感|心率|血压|呼吸急促"),
    re.compile(r"诊断|治疗|治愈|康复|处方|炎症|损伤|病症|临床"),
)


def contains_fabrication(text: str | None) -> bool:
    """正文是否声称了视觉无法得到的负重/痛感/生理/肌肉/医学结论。"""
    if not isinstance(text, str) or not text:
        return False
    return any(pat.search(text) for pat in _FABRICATION_PATTERNS)


# --------------------------------------------------------------------------- #
# 动态结论检测（语义规则 2：速度/借力/节奏/稳定性变化需 >=2 时刻证据）
# --------------------------------------------------------------------------- #
_DYNAMIC_KEYWORDS = (
    "快", "慢", "加速", "减速", "越来越", "逐渐", "节奏", "借力",
    "晃动", "摇晃", "稳定", "摆动", "抖动", "控制不住", "忽快忽慢",
    "连贯", "停顿", "越来越高", "越来越低",
)


def is_dynamic_claim(text: str | None) -> bool:
    """判断一段观察/解释/建议是否在描述随时间变化的动态属性。"""
    if not isinstance(text, str) or not text:
        return False
    return any(k in text for k in _DYNAMIC_KEYWORDS)


def _evidence_has_time_window(refs: list[EvidenceRef]) -> bool:
    """该 note 的引用是否含 >=2 不同时刻（去重 frame_ids>=2 或时间窗跨度>0）。"""
    for ref in refs:
        if len(set(ref.frame_ids)) >= 2:
            return True
        if ref.end_ms - ref.start_ms > 0:
            return True
    return False


# --------------------------------------------------------------------------- #
# 语义校验入口
# --------------------------------------------------------------------------- #
def _collect_body_texts(review: CoachReview) -> list[str]:
    texts: list[str] = [review.summary, review.identification_reason]
    for note in review.frame_notes:
        texts.extend([note.observation, note.explanation, note.next_step])
    return texts


def semantically_validate(
    review: CoachReview,
    *,
    valid_frame_ids: set[str] | frozenset[str],
    video_duration_ms: int | None = None,
) -> list[str]:
    """对已通过结构校验的 review 做语义判定，返回违规码列表（空=通过）。

    Parameters
    ----------
    valid_frame_ids:
        本次素材中属于主运动者的全部合法 frame_id。FrameNote.frame_id 与
        每个 EvidenceRef.frame_ids 都必须是其子集。
    video_duration_ms:
        视频时长。给出后校验 evidence_refs.start_ms/end_ms 落在 [0, duration]。
    """
    codes: list[str] = []
    valid = set(valid_frame_ids or set())

    # 规则 1a：FrameNote.frame_id 必须属于本次素材/主运动者。
    for note in review.frame_notes:
        if note.frame_id not in valid:
            codes.append("note_frame_not_in_material")
            break

    # 规则 1b：每个 EvidenceRef.frame_ids 属于素材；时间落在视频时长内。
    for note in review.frame_notes:
        for ref in note.evidence_refs:
            unknown = [fid for fid in ref.frame_ids if fid not in valid]
            if unknown:
                codes.append("evidence_frame_not_in_material")
                break
            if video_duration_ms is not None:
                if ref.start_ms < 0 or ref.end_ms > video_duration_ms:
                    codes.append("evidence_time_out_of_video")
                    break

    # 规则 2：动态结论必须有 >=2 时刻证据。
    for note in review.frame_notes:
        if is_dynamic_claim(note.observation) or is_dynamic_claim(note.explanation):
            if not _evidence_has_time_window(note.evidence_refs):
                codes.append("dynamic_claim_single_frame")
                break

    # 规则 3：开放类别一致性。novel_label_zh 仅在无 canonical_id 时允许；
    # identification=unknown 时不应硬挂一个目录 ID。
    if review.canonical_id and review.novel_label_zh:
        codes.append("novel_label_with_canonical_id")
    if review.identification == "unknown" and review.canonical_id:
        codes.append("unknown_but_has_canonical_id")

    # 规则 4：正文不泄露技术字段。
    for text in _collect_body_texts(review):
        if find_technical_leaks(text):
            codes.append("technical_field_in_body")
            break

    # 规则 5：不编造视觉不可得内容。
    for text in _collect_body_texts(review):
        if contains_fabrication(text):
            codes.append("fabricated_unobservable_content")
            break

    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for c in codes:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def assert_coach_review_valid(
    review: CoachReview,
    *,
    valid_frame_ids: set[str] | frozenset[str],
    video_duration_ms: int | None = None,
) -> CoachReview:
    """语义校验；失败抛 CoachReviewValidationError（调用方据此降级）。"""
    codes = semantically_validate(
        review, valid_frame_ids=valid_frame_ids, video_duration_ms=video_duration_ms
    )
    if codes:
        raise CoachReviewValidationError(codes)
    return review
