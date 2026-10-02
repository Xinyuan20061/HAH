"""Privacy coverage audit (spec §13.8, §10.4).

Every column that can hold user media, a provider payload or a model signal must
be classified as *exported*, *deleted* or *retained with a stated reason*. A new
media/model field that never enters the retention plan is exactly the drift this
audit exists to catch.

Usage::

    python scripts/audit_privacy_coverage.py
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.database import Base  # noqa: E402
import app.models  # noqa: E402,F401  (populates Base.metadata)

# name -> {class, treatment}. Extend together with migration + retention policy.
PRIVACY_COVERAGE: dict[str, dict[str, str]] = {
    "media_assets.source_url": {
        "class": "短期下载凭据",
        "treatment": "导出时清空；删除时随账户销毁；不进日志",
    },
    "media_assets.storage_key": {
        "class": "对象引用",
        "treatment": "删除任务账本只保存 sha256(storage_key)，不保存可用引用",
    },
    "media_assets.cloud_file_id": {
        "class": "平台对象标识",
        "treatment": "账户删除需客户端回执 + 服务端对账；无法验证则 manual_review",
    },
    "ai_jobs.result_json": {
        "class": "模型回执",
        "treatment": "只保存校验后的结构化结果；动作 V2 回执不含图像字节",
    },
    "ai_jobs.payload_json": {
        "class": "任务参数",
        "treatment": "不含密钥与签名 URL；随账户删除",
    },
    "food_analysis_sessions.initial_json": {
        "class": "模型输出",
        "treatment": "随账户删除；导出仅含结构化字段",
    },
    "food_analysis_sessions.corrected_json": {
        "class": "用户校正",
        "treatment": "随账户删除；导出包含",
    },
    "food_analysis_corrections.corrected_json": {
        "class": "用户校正历史",
        "treatment": "随账户删除；导出包含",
    },
    "motion_analysis_feedback.result_json": {
        "class": "结构化动作结果",
        "treatment": "随账户删除；无图像字节、无签名 URL",
    },
    "motion_evidence_frames.preview_asset_id": {
        "class": "预览引用",
        "treatment": "默认 7 天保留期；过期删除字节并失效引用",
    },
    "provider_invocations": {
        "class": "脱敏调用账本",
        "treatment": "不保存提示词、音频、图像或密钥",
    },
    "user_ai_configs.api_key_encrypted": {
        "class": "用户密钥",
        "treatment": "仅服务端密文；导出只返回是否已配置；随账户删除",
    },
    "user_ai_configs.voice_api_key_encrypted": {
        "class": "用户语音密钥",
        "treatment": "仅服务端密文；导出只返回是否已配置；随账户删除",
    },
    "media_assets.source_url_expires_at": {
        "class": "凭据过期时间",
        "treatment": "随 source_url 一起清空；导出不保留",
    },
    "knowledge_documents.source_url": {
        "class": "公开来源链接",
        "treatment": "人工审核的公开出处，非用户数据；导出包含",
    },
    "fitness_relations.source_url": {
        "class": "公开来源链接",
        "treatment": "人工审核的公开出处，非用户数据；导出包含",
    },
    "provider_invocations.token_or_char_count": {
        "class": "用量计数",
        "treatment": "只保存长度计数，不保存内容；随账户删除",
    },
}


# Non-sensitive internal tokens that legitimately match a sensitive keyword.
ALLOWED_INTERNAL_COLUMNS = {
    "lease_token",
    "claim_request_id",
    "request_fingerprint",
    "lease_expires_at",
}

SENSITIVE_TOKENS = ("source_url", "api_key", "secret", "token", "audio", "image_b64")


def collect() -> list[str]:
    problems: list[str] = []
    metadata = Base.metadata
    for qualified in PRIVACY_COVERAGE:
        table_name, _, column_name = qualified.partition(".")
        table = metadata.tables.get(table_name)
        if table is None:
            problems.append(f"隐私清单引用了不存在的表: {table_name}")
            continue
        if column_name and column_name not in table.columns:
            problems.append(f"隐私清单引用了不存在的列: {qualified}")

    for table in metadata.tables.values():
        for column in table.columns:
            name = column.name
            if not any(token in name for token in SENSITIVE_TOKENS):
                continue
            if name in ALLOWED_INTERNAL_COLUMNS:
                continue
            qualified = f"{table.name}.{name}"
            if qualified in PRIVACY_COVERAGE:
                continue
            problems.append(
                f"敏感列未进入隐私清单: {qualified}（导出/删除/保留期必须分类）"
            )
    return problems


def main() -> int:
    problems = collect()
    print(f"classified_entries={len(PRIVACY_COVERAGE)} tables={len(Base.metadata.tables)}")
    for qualified, meta in sorted(PRIVACY_COVERAGE.items()):
        print(f"  {qualified}: {meta['class']} -> {meta['treatment']}")
    if problems:
        print("\nFAIL")
        for item in problems:
            print(" -", item)
        return 1
    print("OK: 所有敏感列都已进入导出/删除/保留期清单")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
