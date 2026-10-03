"""证据帧绑定（能力计划 §5.7 末段 / §5.10 末项）。

§5.7 明确："每个 finding 必须带证据帧；视角不支持时返回 unavailable"。
§5.10 把它变成完成线："每个 finding 100% 有证据帧或明确 unavailable"。

本模块把这条规则写成**可执行断言**，而不是文档约定：任何跨越包边界的
finding 都应先过 :func:`assert_evidence_complete`。

本模块**不做什么**：

* 不生成/伪造证据帧 id。它只能把调用方给出的真实帧号格式化成 id；没有
  真实帧就不产生 id（此时必须走 ``status="unavailable"``）。
* 不读视频、不抽帧、不渲染预览。帧的像素内容与预览资产由
  ``processors/motion_unified.py`` 的证据池负责，本层只持有**引用**。
* 不因为"问题看起来很小"就跳过证据要求：``good`` 与 ``attention`` 一律需要
  证据帧，只有 ``unavailable`` 例外。
"""

from __future__ import annotations

from typing import Iterable

from .contracts import RepFinding

# 证据帧 id 格式：``f0007`` = 第 7 帧。固定宽度保证可排序、可解析、可去重。
FRAME_ID_PREFIX = "f"
FRAME_ID_WIDTH = 4


class EvidenceGapError(AssertionError):
    """finding 既没有证据帧也不是 unavailable —— 属于不可交付的结论。"""


def frame_id_of(index: int) -> str:
    """帧号 → 稳定证据帧 id（``f0007``）。负数按 0 处理，不产生非法 id。"""
    safe = max(0, int(index))
    return f"{FRAME_ID_PREFIX}{safe:0{FRAME_ID_WIDTH}d}"


def frame_ids_from(indices: Iterable[int]) -> list[str]:
    """帧号序列 → 去重且保持首次出现顺序的 id 列表。"""
    seen: set[str] = set()
    out: list[str] = []
    for index in indices:
        identifier = frame_id_of(index)
        if identifier not in seen:
            seen.add(identifier)
            out.append(identifier)
    return out


def frame_index_from_id(identifier: str) -> int | None:
    """``f0007`` → 7；格式不符返回 None（不猜）。"""
    if not isinstance(identifier, str) or not identifier.startswith(FRAME_ID_PREFIX):
        return None
    tail = identifier[len(FRAME_ID_PREFIX):]
    return int(tail) if tail.isdigit() else None


def has_evidence(finding: RepFinding) -> bool:
    """该 finding 是否满足"有证据帧 OR unavailable"。"""
    return bool(finding.evidence_frame_ids) or finding.status == "unavailable"


def with_evidence(
    finding: RepFinding, frame_indices: Iterable[int] | None
) -> RepFinding:
    """给 finding 附加证据帧。

    * ``status="unavailable"`` 的 finding **不会**被附加证据（它本来就不该有
      结论性数值），原样返回。
    * 其它状态若在调用后仍没有证据帧，由 :func:`assert_evidence_complete` 报错，
      而不是在这里偷偷降级成 unavailable——降级必须由测量逻辑显式决定。
    """
    if finding.status == "unavailable" or not frame_indices:
        return finding
    return finding.model_copy(update={"evidence_frame_ids": frame_ids_from(frame_indices)})


def attach_evidence(
    findings: Iterable[RepFinding], frame_indices: Iterable[int]
) -> list[RepFinding]:
    """批量绑定同一组代表帧（用于"整段测量"型 finding）。"""
    return [with_evidence(finding, frame_indices) for finding in findings]


def assert_evidence_complete(findings: Iterable[RepFinding]) -> None:
    """断言每个 finding 都有证据帧或明确 unavailable。

    Raises
    ------
    EvidenceGapError
        一旦存在"有结论但无证据"的 finding。这是硬失败：宁可不返回该 finding，
        也不返回一个无法回溯到帧的结论。

    本函数不做自动修复（不补帧、不改状态），只做检测。
    """
    for finding in findings:
        if has_evidence(finding):
            continue
        raise EvidenceGapError(
            "finding 缺少证据帧且不是 unavailable: "
            f"measurement_key={finding.measurement_key} status={finding.status} "
            f"rep_index={finding.rep_index}"
        )


def representative_evidence(
    findings: Iterable[RepFinding], *, limit: int
) -> list[str]:
    """挑选代表证据帧 id（供时间线/报告使用）。

    规则（确定性）：优先 ``attention``（最值得调整的时刻），其次 ``good``；
    同优先级按出现顺序；最后按 id 去重并截断到 ``limit``。``unavailable`` 的
    finding 没有证据帧，因此天然不参与。
    """
    if limit < 0:
        raise ValueError("limit 不能为负")
    ordered: list[str] = []
    for status in ("attention", "good"):
        for finding in findings:
            if finding.status != status:
                continue
            for identifier in finding.evidence_frame_ids:
                if identifier not in ordered:
                    ordered.append(identifier)
    return ordered[:limit]


__all__ = [
    "FRAME_ID_PREFIX",
    "FRAME_ID_WIDTH",
    "EvidenceGapError",
    "assert_evidence_complete",
    "attach_evidence",
    "frame_id_of",
    "frame_ids_from",
    "frame_index_from_id",
    "has_evidence",
    "representative_evidence",
    "with_evidence",
]
