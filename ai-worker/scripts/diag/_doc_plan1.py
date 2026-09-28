# -*- coding: utf-8 -*-
"""Stage-1 plan doc update: §1.1 snapshot, §6 stage-1 done, §9 stage-1 done + stage-2 current, §11 drift row."""
import io

PATH = r"D:\学习资料\计算机应用大赛\health-assistant\docs\COMPETITIVE_DEVELOPMENT_PLAN_2026-09-27.md"

s = io.open(PATH, encoding="utf-8").read()

# --- §1.1 auto regression row -> refreshed counts/head ---
OLD1 = "| 自动回归 | 2026-09-27 冻结基线：后端 SQLite 156/156、Worker 99/99、小程序 41/41、迁移检查 head=`0021` 全绿（命令/退出码/代码哈希见 [VERIFICATION](../docs/VERIFICATION.md)） | 生产 MySQL 最新迁移、真实微信环境全量回归已验收 |"
NEW1 = "| 自动回归 | 2026-09-27 冻结基线（阶段 1 后）：后端 SQLite 161/161、Worker 99/99、小程序 43/43、迁移检查 head=`0022_agent_decision_id` 全绿（命令/退出码/代码哈希见 [VERIFICATION](../docs/VERIFICATION.md)） | 生产 MySQL 最新迁移、真实微信环境全量回归已验收 |\n| 决策账本读模型 | 新增 `GET /api/v1/agent/decisions/{id}`：同一 `decision_id` 串起信号、证据、方案、确认、进度与复盘；`/insights` 每条提醒带 `evidence_contract`（facts/data_coverage/limitations）与 `action_timeline`；迁移 0022 已上线 | 真实微信环境端到端、双人评审留档 |"
assert s.count(OLD1) == 1, "OLD1 count=%d" % s.count(OLD1)
s = s.replace(OLD1, NEW1)

# --- §6 stage-1 completion note ---
OLD2 = "| 完整与不足案例 | 测试夹具、验收记录 | 正常完成和数据不足两条路径均有端到端测试；不将合成记录称为真实用户数据 |\n\n### 阶段 2：让多模态成为可信辅助输入"
NEW2 = "| 完整与不足案例 | 测试夹具、验收记录 | 正常完成和数据不足两条路径均有端到端测试；不将合成记录称为真实用户数据 |\n\n**阶段 1 已于 2026-09-27 完成**：决策读模型端点、结论级证据标注（`evidence_contract`）、小程序行动时间线（历史行动/依据与数据覆盖/我们还不知道）与完整/不足双案例契约测试全部落地；后端 161、小程序 43 全绿，迁移 head=`0022_agent_decision_id`。\n\n### 阶段 2：让多模态成为可信辅助输入"
assert s.count(OLD2) == 1, "OLD2 count=%d" % s.count(OLD2)
s = s.replace(OLD2, NEW2)

# --- §9 stage-1 done + stage-2 current ---
OLD3 = "**阶段 0 已于 2026-09-27 完成**：六项任务全部落地（测试环境隔离、media 页语义、工作区整理、Kinetics 门控、版本口径、冻结基线），三套回归全绿，基线哈希见 VERIFICATION.md。当前进入**阶段 1（行动账本闭环）**。"
NEW3 = "**阶段 0、阶段 1 已于 2026-09-27 完成**：阶段 0 六项任务（测试环境隔离、media 页语义、工作区整理、Kinetics 门控、版本口径、冻结基线）；阶段 1 行动账本闭环（决策读模型 `GET /api/v1/agent/decisions/{id}`、结论级证据标注、小程序行动时间线、完整/不足双案例），三套回归全绿、迁移 head=`0022_agent_decision_id`，基线哈希见 VERIFICATION.md。当前进入**阶段 2（多模态可信输入验证）**。"
assert s.count(OLD3) == 1, "OLD3 count=%d" % s.count(OLD3)
s = s.replace(OLD3, NEW3)

# --- §11 drift row -> 0022 ---
OLD4 = "| 文档与代码版本漂移 | 已统一到 `0021_empty_default_nickname`（2026-09-27 阶段 0 完成，README/VERIFICATION/DELIVERY_CHECKLIST/LOCAL_AI_WORKER 等同步） | 阶段 1 起事实来源以 VERIFICATION.md 冻结基线为准，报告带日期与哈希 |"
NEW4 = "| 文档与代码版本漂移 | 已统一到 `0022_agent_decision_id`（2026-09-27 阶段 0/1 完成，README/VERIFICATION/DELIVERY_CHECKLIST/AUTO_MOTION_RECOGNITION 等同步） | 阶段 2 起事实来源以 VERIFICATION.md 冻结基线为准，报告带日期与哈希 |"
assert s.count(OLD4) == 1, "OLD4 count=%d" % s.count(OLD4)
s = s.replace(OLD4, NEW4)

io.open(PATH, "w", encoding="utf-8", newline="").write(s)
print("OK plan doc updated")
