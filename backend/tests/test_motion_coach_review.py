# -*- coding: utf-8 -*-
"""C 包 P0 回归：CoachReview 契约语义校验 + R01/R06/R07/R09 修复（T07/T08/T10）。

离线、无网络、无真实模型调用。所有 CoachReview 均为现场构造的不可信模型输出。
"""

import pytest

from app.services.motion.coach_review import (
    CoachReview,
    EvidenceRef,
    FrameNote,
    CoachReviewValidationError,
    contains_fabrication,
    find_technical_leaks,
    semantically_validate,
)
from app.services.motion.text_summary import (
    build_summary,
    clean_summary_text,
    fallback_summary,
    is_polluted,
    summarize_from_local_findings,
)
from app.services.motion.vision_review import (
    FrameFinding,
    ReviewValidationError,
    VisionReview,
    build_coach_prompt,
    validate_review,
)
from app.services.motion import knowledge_zh

VALID_FRAME_IDS = {"f_000", "f_004", "f_008", "f_012"}
DURATION = 16000


def _note(**kw):
    base = dict(
        frame_id="f_008",
        phase="抬起阶段",
        observation="前臂向上转动，哑铃逐渐靠近胸前。",
        explanation="这一阶段可以留意上臂是否也明显前移。",
        next_step="下一遍尽量让上臂停在身体两侧，再弯曲肘部抬起哑铃。",
        advice_kind="general_tip",
        evidence_refs=[EvidenceRef(frame_ids=["f_008", "f_012"], start_ms=3200, end_ms=4800)],
    )
    base.update(kw)
    return FrameNote(**base)


def _valid_review(**kw):
    base = dict(
        canonical_id="bicep_curl",
        novel_label_zh=None,
        identification="likely",
        identification_reason="肘关节屈伸清楚，最高点附近画面略有遮挡。",
        summary="手臂屈伸和哑铃轨迹可以看清。下面按抬起和放下拆解；下一遍可重点关注上臂位置和放下时的控制。",
        primary_next_step="放下时慢数两拍，并留意上臂是否跟着前后摆动。",
        frame_notes=[_note()],
    )
    base.update(kw)
    return CoachReview(**base)


# --------------------------------------------------------------------------- #
# T07：点评可读——污染正文降级或拒绝，不直接展示
# --------------------------------------------------------------------------- #
class TestT07Pollution:
    def test_find_technical_leaks_detects_debug_tokens(self):
        assert find_technical_leaks("frame:0")
        assert find_technical_leaks("squat_bottom")
        assert find_technical_leaks("candidate_score")
        assert find_technical_leaks("f_012")
        assert find_technical_leaks("NOT_RECOGNIZED")
        # 正常中文不应误报
        assert find_technical_leaks("手臂屈伸和哑铃轨迹可以看清") == []

    def test_clean_summary_rejects_polluted(self):
        assert clean_summary_text("动作 squat 帧 frame:0 在 squat_bottom candidate_score=0.9") is None
        assert is_polluted("动作 squat 帧 frame:0")

    def test_normal_summary_passes_clean(self):
        text = "手臂屈伸和哑铃轨迹可以看清。下面按抬起和放下拆解，下一遍可重点关注上臂位置。"
        assert clean_summary_text(text) == text

    def test_build_summary_downgrades_polluted_coach_review(self):
        polluted = _valid_review(
            identification_reason="candidate_score 较高",
            summary="动作类型 squat，帧 frame:0 在 squat_bottom，candidate_score=0.9。",
        )
        result = build_summary(
            polluted,
            metrics=None,
            evidence=[{"finding": "下蹲到一半，大腿接近平行。", "advice": "蹲得再深一点"}],
        )
        assert result["source"] == "local_observations"
        assert "frame:0" not in result["text"]
        assert "squat_bottom" not in result["text"]
        assert "candidate_score" not in result["text"]
        assert "下蹲到一半" in result["text"]


# --------------------------------------------------------------------------- #
# T08：保留本地解释——review 缺失时复用 Worker 中文 finding/advice，不退回英文 event
# --------------------------------------------------------------------------- #
class TestT08LocalFindings:
    def test_summarize_reuses_chinese_finding_not_event(self):
        evidence = [
            {
                "phase": "最低点",
                "finding": "下蹲到一半，大腿接近平行。",
                "advice": "蹲得再深一点",
                "event": "squat_bottom",
            }
        ]
        result = summarize_from_local_findings(evidence, "squat")
        assert result["source"] == "local_observations"
        assert "下蹲到一半" in result["text"]
        assert "蹲得再深一点" in result["primary_next_step"]
        # 英文事件码不得进入用户可见正文
        assert "squat_bottom" not in result["text"]
        assert "squat_bottom" not in result["primary_next_step"]

    def test_summarize_empty_falls_back_to_knowledge(self):
        result = summarize_from_local_findings([], "bicep_curl")
        assert result["source"] == "local_observations"
        assert result["text"]
        assert "bicep_curl" not in result["text"]


# --------------------------------------------------------------------------- #
# T10：局部与静态视频——缺全身/完整周期不作为分类硬拒绝
# --------------------------------------------------------------------------- #
class TestT10LocalStatic:
    def test_validate_review_open_category_accepts_out_of_candidate_label(self):
        # R06：候选外标签不再被 validate_review 拒绝
        rv = VisionReview(
            label_id="bicep_curl",
            evidence_ids=["frame:0"],
            findings=[FrameFinding(frame_id="frame:0", observation="下蹲到一半", advice="蹲得再深一点")],
        )
        validate_review(rv, {"squat"}, {"frame:0"})  # 不应抛错

    def test_validate_review_still_rejects_invented_frames(self):
        rv = VisionReview(label_id="squat", evidence_ids=["frame:99"], findings=[])
        with pytest.raises(ReviewValidationError):
            validate_review(rv, {"squat"}, {"frame:0"})

    def test_plank_is_prone_family_not_standing(self):
        assert knowledge_zh.action_family("plank") == "prone"
        s = fallback_summary("recognized", "plank", "NO_VALIDATED_SCORER")
        assert "站稳" not in s
        assert "全身入镜" not in s

    def test_bicep_curl_upper_body_prompt_does_not_force_unknown(self):
        prompt = build_coach_prompt(
            {
                "candidates": [{"source": "pose", "canonical_id": "bicep_curl"}],
                "local_label": "bicep_curl",
                "visible_regions": ["shoulder", "elbow", "wrist"],
                "missing_regions": ["hip", "knee", "ankle"],
            }
        )
        # §7.1：没看到脚不等于不能识别上肢动作
        assert "没看到脚" in prompt
        # 相关动作知识条目（上臂位置）进入提示词
        assert "大臂贴在身体两侧" in prompt

    def test_semantic_validator_accepts_upper_body_static_review(self):
        review = _valid_review(
            canonical_id="plank",
            identification_reason="俯卧支撑姿态清楚，躯干成一条线。",
            summary="平板支撑姿态清楚，身体从肩到脚跟成一条线。下一遍留意髋部不要塌下去。",
            frame_notes=[
                _note(
                    frame_id="f_008",
                    observation="前臂撑地，身体成一条斜线。",
                    evidence_refs=[EvidenceRef(frame_ids=["f_008"], start_ms=4000, end_ms=4000)],
                )
            ],
        )
        codes = semantically_validate(
            review, valid_frame_ids=VALID_FRAME_IDS, video_duration_ms=DURATION
        )
        assert codes == []

    def test_dynamic_claim_single_frame_rejected(self):
        review = _valid_review(
            frame_notes=[
                _note(
                    observation="保持过程中身体越来越稳定。",
                    evidence_refs=[EvidenceRef(frame_ids=["f_008"], start_ms=0, end_ms=0)],
                )
            ],
        )
        codes = semantically_validate(
            review, valid_frame_ids=VALID_FRAME_IDS, video_duration_ms=DURATION
        )
        assert "dynamic_claim_single_frame" in codes


# --------------------------------------------------------------------------- #
# CoachReview 语义校验核心规则
# --------------------------------------------------------------------------- #
class TestSemanticValidation:
    def test_valid_review_passes(self):
        codes = semantically_validate(
            _valid_review(), valid_frame_ids=VALID_FRAME_IDS, video_duration_ms=DURATION
        )
        assert codes == []

    def test_unknown_frame_rejected(self):
        review = _valid_review(frame_notes=[_note(frame_id="f_999")])
        codes = semantically_validate(review, valid_frame_ids=VALID_FRAME_IDS)
        assert "note_frame_not_in_material" in codes

    def test_evidence_time_out_of_video_rejected(self):
        review = _valid_review(
            frame_notes=[
                _note(evidence_refs=[EvidenceRef(frame_ids=["f_008"], start_ms=0, end_ms=99999)])
            ]
        )
        codes = semantically_validate(
            review, valid_frame_ids=VALID_FRAME_IDS, video_duration_ms=DURATION
        )
        assert "evidence_time_out_of_video" in codes

    def test_technical_field_in_body_rejected(self):
        review = _valid_review(summary="手臂屈伸清楚，候选 candidate_score 较高可以看清动作。")
        codes = semantically_validate(review, valid_frame_ids=VALID_FRAME_IDS)
        assert "technical_field_in_body" in codes

    def test_fabricated_pain_or_weight_rejected(self):
        assert contains_fabrication("举了 20kg 哑铃，感到手臂酸痛")
        review = _valid_review(summary="手臂屈伸清楚，但用户感到手臂酸痛需要休息。")
        codes = semantically_validate(review, valid_frame_ids=VALID_FRAME_IDS)
        assert "fabricated_unobservable_content" in codes

    def test_novel_label_consistency(self):
        review = _valid_review(canonical_id="squat", novel_label_zh="某个新动作")
        codes = semantically_validate(review, valid_frame_ids=VALID_FRAME_IDS)
        assert "novel_label_with_canonical_id" in codes

    def test_assert_raises_carries_codes(self):
        review = _valid_review(summary="动作类型 squat，帧 frame:0 在 squat_bottom。")
        with pytest.raises(CoachReviewValidationError) as ei:
            from app.services.motion.coach_review import assert_coach_review_valid

            assert_coach_review_valid(
                review, valid_frame_ids=VALID_FRAME_IDS, video_duration_ms=DURATION
            )
        assert ei.value.codes
