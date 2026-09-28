# -*- coding: utf-8 -*-
"""Sync plan document §1.1/§6/§9/§11 for Phase 2 completion (2026-09-28).

Run: ai-worker/.venv/Scripts/python.exe scripts/diag/_doc_phase2_sync.py
"""
from pathlib import Path

PLAN = Path(r"D:\学习资料\计算机应用大赛\health-assistant\docs\COMPETITIVE_DEVELOPMENT_PLAN_2026-09-27.md")

REPLACEMENTS = [
    # --- §1.1 动作行 ---
    (
        "| 动作 | 120 段固定集：全样本自动识别准确率 41.67%、覆盖率 64.17%；规则与姿态链路可运行 | 六类动作高精度识别，或对任意动作提供专业评分 |",
        "| 动作 | 120 段固定集：全样本自动识别准确率 41.67%、覆盖率 64.17%；2026-09-28 同集三路对照（24 段子集）：规则 Top-1 50.00%、Kinetics 单独 16.67%、融合(模拟) 58.33%，均未达 §7.2 门槛 | 六类动作高精度识别，或对任意动作提供专业评分；Kinetics 增益已达标 |",
    ),
    # --- §1.1 识餐行 ---
    (
        "| 识餐 | 42 图，34 图有效；热量估算 MAPE 72.70%，区间覆盖率 44.12% | 照片能准确测量热量或营养素 |",
        "| 识餐 | 42 图，34 图有效；热量估算 MAPE 72.70%，区间覆盖率 44.12%；2026-09-28 三口径复核：中点校正代理 MAE 94.01（改进仅 0.76%） | 照片能准确测量热量或营养素；区间覆盖已达标可提升正式入口 |",
    ),
    # --- §1.1 Kinetics 行 ---
    (
        "| 新增 Kinetics-400 | 工作区已有预训练推理、任务和页面代码；能力探测为真实权重加载 + 单次前向 smoke（132 MB，已通过）；`KINETICS400_OVERRIDE_ENABLED=false`（默认），motion auto 仅记录候选、不覆盖规则结果 | 已有独立固定集增益、校准、真机耗时或上线证据 |",
        "| 新增 Kinetics-400 | 工作区已有预训练推理、任务和页面代码；能力探测为真实权重加载 + 单次前向 smoke（132 MB，已通过）；`KINETICS400_OVERRIDE_ENABLED=false`（默认），motion auto 仅记录候选、不覆盖规则结果；2026-09-28 同集对照（24 段）：Top-1 16.67%、覆盖率 16.67%（仅 lunge 有映射预测），未达门槛 → 保持候选层（报告 [`motion-v2-kinetics`](../benchmark-results/motion-v2-kinetics/report.md)） | 已有独立固定集增益、校准、真机耗时或上线证据 |",
    ),
    # --- §6 阶段 2 完成标注 ---
    (
        "### 阶段 2：让多模态成为可信辅助输入\n\n| 任务 | 主要位置 | 交付与验收 |",
        "### 阶段 2：让多模态成为可信辅助输入（已于 2026-09-28 完成，除任务 1 独立集）\n\n| 任务 | 主要位置 | 交付与验收 |",
    ),
    # --- §9 阶段行更新 ---
    (
        "| 2. 多模态验证 | 4–7 天，取决于数据和设备 | 新模型独立报告与同集对照；门控/回退通过 | Kinetics 继续保持实验候选层 |",
        "| 2. 多模态验证 | 4–7 天，取决于数据和设备 | 新模型独立报告与同集对照；门控/回退通过（**2026-09-28 完成**：同集三路对照、识餐三口径、动作分层复验；任务 1 独立固定集因单数据集限制未完成，如实标注子集对照口径） | Kinetics 继续保持实验候选层（实测未达门槛） |",
    ),
    # --- §9 段落更新 ---
    (
        "**阶段 0、阶段 1 已于 2026-09-27 完成**：阶段 0 六项任务（测试环境隔离、media 页语义、工作区整理、Kinetics 门控、版本口径、冻结基线）；阶段 1 行动账本闭环（决策读模型 `GET /api/v1/agent/decisions/{id}`、结论级证据标注、小程序行动时间线、完整/不足双案例），三套回归全绿、迁移 head=`0022_agent_decision_id`，基线哈希见 VERIFICATION.md。当前进入**阶段 2（多模态可信输入验证）**。",
        "**阶段 0、阶段 1 已于 2026-09-27 完成**：阶段 0 六项任务（测试环境隔离、media 页语义、工作区整理、Kinetics 门控、版本口径、冻结基线）；阶段 1 行动账本闭环（决策读模型 `GET /api/v1/agent/decisions/{id}`、结论级证据标注、小程序行动时间线、完整/不足双案例），三套回归全绿、迁移 head=`0022_agent_decision_id`，基线哈希见 VERIFICATION.md。**阶段 2（多模态可信输入验证）已于 2026-09-28 完成**：同集三路对照（24 段子集：规则 50.00% / Kinetics 16.67% / 融合 58.33%，均未达 §7.2 门槛 → Kinetics 保持候选层）、识餐三口径复核（区间覆盖 44.12%、中点校正改进 0.76%，未达 §7.3 → 不提升正式入口）、动作结果分层复验通过；任务 1（受试者不重合的独立固定集）在仅有 REHAB24-6 单数据集前提下无法完成，如实标注。当前进入**阶段 3（真实环境与用户证据）**。",
    ),
    # --- §11 风险行 ---
    (
        "| 感知模型误导用户 | 动作和识餐基线偏弱；Kinetics 无正式报告 | 候选/拒识/校正分级；未达门槛不自动覆盖 |",
        "| 感知模型误导用户 | 动作和识餐基线偏弱；Kinetics 同集对照已出正式报告（未达门槛） | 候选/拒识/校正分级；未达门槛不自动覆盖（`KINETICS400_OVERRIDE_ENABLED=false` 维持） |",
    ),
    # --- §11 文档漂移行 ---
    (
        "| 文档与代码版本漂移 | 已统一到 `0022_agent_decision_id`（2026-09-27 阶段 0/1 完成，README/VERIFICATION/DELIVERY_CHECKLIST/AUTO_MOTION_RECOGNITION 等同步） | 阶段 2 起事实来源以 VERIFICATION.md 冻结基线为准，报告带日期与哈希 |",
        "| 文档与代码版本漂移 | 已统一到 `0022_agent_decision_id`（2026-09-27 阶段 0/1、2026-09-28 阶段 2 完成，README/VERIFICATION/DELIVERY_CHECKLIST/AUTO_MOTION_RECOGNITION 等同步） | 事实来源以 VERIFICATION.md 冻结基线为准，报告带日期与哈希 |",
    ),
]


def main() -> int:
    text = PLAN.read_text(encoding="utf-8")
    applied = 0
    for old, new in REPLACEMENTS:
        if old not in text:
            print("NOT FOUND: %s..." % old[:60])
            continue
        text = text.replace(old, new, 1)
        applied += 1
    PLAN.write_text(text, encoding="utf-8")
    print(f"applied {applied}/{len(REPLACEMENTS)} replacements -> {PLAN}")
    return 0 if applied == len(REPLACEMENTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
