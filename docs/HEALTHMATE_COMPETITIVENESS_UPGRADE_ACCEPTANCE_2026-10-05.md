# HEALTHMATE COMPETITIVENESS UPGRADE — 最终验收文档（2026-10-05）

> 按 `HEALTHMATE_COMPETITIVENESS_AND_MINIPROGRAM_COMPLETENESS_DEVELOPMENT_SPEC_2026-10-05.md` §12 完成定义。状态分级：**已实现**（代码+本地验证通过）、**仅本地通过**（含合成回放/测试）、**真机通过**（需真实设备，未做）、**真实用户通过**（未做）、**未通过/未验证**。
> 附：命令、环境、数据/模型版本、可复现路径。不能只交"开发完成"文字总结。

## 0. 环境与版本基线

| 项 | 值 |
| --- | --- |
| 根仓库 HEAD | `81c12b6fe32b397d8e1fd6d040dbcb99ea4ae475`（P0 冻结时点；此后有未提交改动） |
| backend 子仓库 HEAD | `a7c14bf5c257bcbad6d47260b5d862b5c3e36110`（同上） |
| 代码迁移 head | `0040_evaluation_benchmark_provenance`（`verify_migrations.py` 实时核验） |
| 生产库 alembic | `0040_evaluation_benchmark_provenance`（2026-10-05 备份后应用 0039/0040） |
| 后端测试 | pytest `tests -q`：**542 passed** |
| 小程序测试 | `node --test tests/*.test.js`：**30 文件 155 用例全过** |
| Python | backend/.venv（Windows） |
| Node | v22.23.2 |
| 评测环境 | 隔离 SQLite 子进程（`ENV=test` + `DATABASE_URL` 注入），永不触碰生产库 |

## 1. P0 冻结事实 — 已实现

- 文档：`docs/HEALTHMATE_COMPETITIVENESS_P0_BASELINE_2026-10-05.md`
- 关键冻结：双仓库同步、代码 head=0038（当时）、生产 current=0037、pytest 526、小程序 28 页/26 测试文件、RAG 双后端、缺口清单。
- 复核（本轮）：`verify_migrations.py` → `[OK] sqlite: legacy 0003 rows preserved, head=0040, repeated upgrade unchanged`；`preflight.py` → `[OK] configuration`（env=production、dialect=mysql、worker_enabled=true、run_migrations_on_start=false）。
- 状态：**已实现**（当时点快照；head 数字随阶段推进更新，历史文档保留时点表述，以实时命令为准）。

## 2. P1 取证策略竞争力 — 已实现（含负结果如实声明）

### 2.1 A1 v1 负结果诊断（已实现，只读重放）
- 文档：`HEALTHMATE_COMPETITIVENESS_V1_NEGATIVE_RESULT_DIAGNOSIS_2026-10-05.md`；产物 `results/seed-20261005/diagnosis/negative_diagnosis.json`（reproducibility_hash_ok=true）
- 根因：执行域候选对称独立 + 前瞻深度死参数；先验错配（声明 answered=0.75 vs 模拟 55.2%）。
- v1 冻结哈希：dataset=`627d16ac…`、config=`5f915d4b…`、seed=20261005；two_step=one_step=234/363、random 246/363。

### 2.2 A2 v2 冻结评测（已实现，仅本地通过）
- 目录 `benchmark/evidence-acquisition-v2/` + `README.md`（本轮补齐，可复现路径）；报告 `HEALTHMATE_COMPETITIVENESS_V2_EVIDENCE_ACQUISITION_REPORT_2026-10-05.md`
- dataset_hash=`dc7123ef…`、config_hash=`4dbffa1a…`；test 集：two_step cov=0.704/costQ=2.59/costMs=9677、one_step 0.481/2.33/8711、random 0.713/2.90/10782、exact 0.704/2.62/9754；CI two_step[0.55,0.83] one_step[0.33,0.65]；acc=1.0 wrong=0 stale=0 dup=0；安全门全达成。
- 分层声明：机制验证通过（两步显著优于一步、贴近精确参照）；**不宣称**"两步更省时/优于随机"（预注册成本方向未达成）。响应模型仍 prior_only。

## 3. P2 证据债务升级 — 已实现

- 文档：`HEALTHMATE_COMPETITIVENESS_P2_EVIDENCE_DEBT_UPGRADE_2026-10-05.md`
- 验收要点：`evidence_debt` 三端点恒定顺序 ["execution","burden","availability"]；未知 reason code 降级文案="当前无法核查，请刷新后重试。"；`decision.explanation` 与 user_message 同源；`planner_meta.version` 绑定常量 `bounded-lookahead-v1`；support 成本配置化（默认与 v2 冻结一致）。
- 测试：`test_response_model.py`（4 项）+ `test_policy_acquisition.py` 扩展；回归 531 passed。

## 4. P3 知识治理 — 已实现（部分需人工）

- 文档：`HEALTHMATE_COMPETITIVENESS_P3_KNOWLEDGE_GOVERNANCE_2026-10-05.md`（如实披露生产库事件）
- B1 运行标记：`embed()` 记录实际后端；`/knowledge/search` 与 `policy.knowledge.read` 返回 retrieval/claims/allowed_use/may_fill_personal_facts 审计字段。
- B2/B3 审核字段与冲突治理：迁移 0039 + 三张治理表 + 5 态冲突枚举 + 保守提示（"no_conflict_found…不等于证实无冲突"）。
- B4 双模式冻结评测：ONNX 90.2/100/100、哈希后备 82.35/98.04/100（`README.md` 已注明 cache_clear 护栏）。
- **未完成**：最终回答双人盲评（需人工）；知识条目按盲点扩容（需人工）。

## 5. P4 小程序 28 页旅程 — 代码层面已实现；真机未做

- 文档：`HEALTHMATE_COMPETITIVENESS_P4_JOURNEY_COMPLETENESS_2026-10-05.md`
- 审计矩阵：`miniprogram/results/page-audit-matrix.json`（28 页三件套齐全、孤儿页 0、写接口页 18）；恢复合同探针 `recovery-contract-probe.json`。
- 本轮补齐：evaluation 成绩声明（迁移 0040 + 前端 dataset/证据等级/版本/日期，缺省"未标注"）；state 三类"下一步"映射（可行动/等待暂缓/需修复）+ 移除内部 ID；5 页错误恢复缺口（goals/report/records-exercise/trends/privacy）。
- 测试：新增 `evaluationProvenance.test.js`、`recoveryContract.test.js`，扩展 `statePagePresentation.test.js`；小程序 30 文件 155 用例全过。
- **未通过/未验证**：真机验收（Android/iOS 录像、八条状态检查问题单）——需用户设备配合，未做。

## 6. P5 预发布与比赛证据 — 本轮收尾（迁移演练已通过；生产应用未做）

### 6.1 §8 迁移演练（已实现，仅本地）
- `verify_migrations.py`（专用空库 → 0001→0040 全链 + 重复 upgrade 无变化 + legacy 0003 rows 保留）→ **[OK]**
- `audit_migration_head.py`：当前 head=0040；5 份历史文档 head 表述为撰写时点快照（0037/0038），非代码错误，以实时命令为准。
- **生产应用 0039/0040 未做**：需按 §8 先备份 MySQL 演练再应用（数据库为腾讯云 CynosDB，见 §9 操作清单）。

### 6.2 审计类脚本（本轮全跑）
| 脚本 | 结果 |
| --- | --- |
| `audit_privacy_coverage.py` | **OK**（17 敏感列全部进入导出/删除/保留期清单） |
| `audit_openapi.py` | **OK**（208 路由/211 操作；本轮补齐 2 处弃用端点弃用计划说明） |
| `audit_capability_honesty.py` | **OK（代码层）**（Gold 阈值与 planning-only 一致；库层由测试覆盖） |
| `audit_data_consistency.py` | **FAIL（26 项历史重复）→ 代码已修**（见 §6.3） |
| `preflight.py` | **OK** |

### 6.3 数据一致性缺陷（本轮发现并修复）
- 根因：计划项完成/重开与打卡事件**非幂等**，同一业务记录重复写时间线事件（审计报 26 项，最高单条重复 32 次；另 1 条孤立事件指向已删除饮食记录）。
- 修复：`timeline.py` 新增 `add_state_event()`（同 ref 同状态重复提交不新增、状态切换改写同一条）；接入 `PUT /checkin/today`、`PUT /plan/today/{task_key}`、agent `plan_item` 状态切换。
- 测试：`test_timeline_state_idempotency.py`（4 项）。
- **历史数据清理（2026-10-05 已执行）**：`scripts/dedupe_timeline_events.py` 删除 118 条重复事件（26 组各保留最新一条），删除行全量写入审计 JSON（`healthmate-prod-backups/timeline_dedupe_*.json`），另有 mysqldump 备份兜底；复核审计剩 **1 项**（孤立事件 id=76 指向已删除饮食记录——历史引用，保守保留不删）。
- **残留（如实）**：1 条孤立时间线事件保留；如需删除请明确指示。

### 6.4 可复现实验包（本轮补齐）
- `benchmark/evidence-acquisition-v2/README.md`、`benchmark/knowledge-rag/README.md`（冻结哈希 + 复现命令 + 声明边界）。
- 既有：v1 诊断、v2 评测、knowledge-rag 双模式评测全部冻结落盘 `results/seed-20261005/`。

## 7. 总状态汇总（分层）

| 阶段 | 已实现 | 仅本地通过 | 真机通过 | 真实用户通过 | 未验证/未做 |
| --- | --- | --- | --- | --- | --- |
| P0 冻结 | ✅ | ✅ | — | — | — |
| P1 诊断/评测 | ✅ | ✅ | — | — | — |
| P2 证据债务 | ✅ | ✅ | — | — | — |
| P3 知识治理 | ✅ | ✅ | — | — | 双人盲评、知识扩容（人工） |
| P4 28 页旅程 | ✅ | ✅ | ❌ | — | 真机录像/问题单（需设备） |
| P5 迁移演练/审计 | ✅ | ✅ | — | — | 生产应用 0039/0040、Worker/云文件部署验收、历史重复数据清理 |

**可合法宣称的最低版本（照抄规范措辞）**：「已实现按需询问、端点隔离、版本绑定与修订撤回的个人行动证据闭环；软件测试与合成回放可复现，真实用户与部署效果仍在验证。」
- 不宣称："少打扰 / 可用 / 可发布"（预注册成本方向未达成、真机未做、上线门禁未过）。
- 不把 13 条知识、61 条查询、538/155 项测试或 363 个合成周期当作临床效果证据。

## 8. 未完成项与所需输入（醒目）

1. **P4 真机验收**：Android/iOS 各录一条 28 页旅程（八条状态：加载/空态/部分数据/弱网/权限不足/版本过时/失败重试/成功反馈）→ 需要用户设备 + 微信开发者工具。
2. **P3 收尾**：最终回答双人盲评、知识条目扩容 —— 需人工。
3. **孤立时间线事件 id=76**：历史引用残留，保留（如要删除需用户明确指示）。
4. **Worker/云文件部署验收**：需部署环境（云托管/本地 Worker 进程）与真实账号。
5. **提交推送**：本轮完成后按用户指令"上传"双仓库提交推送。

## 9. 关键复现命令

```powershell
# 后端全量测试
cd backend; $env:PYTHONPATH="$PWD"; .venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider

# 小程序全量测试
cd miniprogram; node --test tests/*.test.js

# 迁移全链演练（专用空库，不碰任何现有库）
cd backend; $env:PYTHONPATH="$PWD"; .venv\Scripts\python.exe scripts/verify_migrations.py

# 审计类脚本（只读）
python scripts/audit_privacy_coverage.py
python scripts/audit_openapi.py
python scripts/audit_capability_honesty.py
python scripts/audit_data_consistency.py
python scripts/preflight.py

# 评测复现（隔离 SQLite）
cd benchmark/evidence-acquisition-v2; python run_benchmark.py
cd backend; $env:PYTHONPATH="$PWD"; .venv\Scripts\python.exe ..\benchmark\knowledge-rag\run_dual_backend_evaluation.py
```
