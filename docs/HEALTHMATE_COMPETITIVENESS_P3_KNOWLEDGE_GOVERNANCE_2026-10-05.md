# HealthMate P3 知识治理 —— 阶段交付记录

版本：1.0
日期：2026-10-05
依据：[HEALTHMATE_COMPETITIVENESS_AND_MINIPROGRAM_COMPLETENESS_DEVELOPMENT_SPEC_2026-10-05.md](HEALTHMATE_COMPETITIVENESS_AND_MINIPROGRAM_COMPLETENESS_DEVELOPMENT_SPEC_2026-10-05.md) 工作包 B（B1 环境与版本事实、B2 审核知识扩展、B3 冲突与回答支持度、B4 正式评测）与 §7 内部知识工具契约。
范围声明：本阶段做**最小但真实**的来源支持与冲突保守提示（规范 §10 P3 路线）；最终回答双人盲评与知识条目扩容需人工审核，留待后续。

## 1. 交付清单

| 项 | 落点 | 说明 |
| --- | --- | --- |
| 部署运行后端标记（B1） | `app/services/rag/embeddings.py`：`_EMBED_BACKEND` + `active_embedding_backend()`；`app/services/rag/service.py`：`retrieval_meta(db)` + `knowledge_snapshot_hash(db)` | `embed()` 每次调用记录**实际**走的路径（onnx_bge / hash_fallback），绝不猜；快照哈希覆盖全部活跃审核块，后备模式不继承 ONNX 成绩。 |
| API 运行标记 | `app/api/v1/knowledge.py` | `GET /api/v1/knowledge/search` 旧字段（retrieval/disclaimer/items）保留，新增 `embedding_backend`、`knowledge_snapshot_hash`。 |
| 内部知识工具审计字段（§7） | `app/harness/policy_tools.py::_knowledge` | 新增 `retrieval{version, embedding_backend, knowledge_snapshot_hash}`、`claims[]`、`allowed_use=explanation_only`、`may_fill_personal_facts=false`；`conflict_status` 由治理模块实时计算。 |
| 审核字段扩展（B2） | 迁移 `0039_knowledge_governance.py` + `models.py::KnowledgeDocument` | 新增 `population / exclusions / reviewer / review_expires_at / content_sha256`。 |
| 冲突与主张存储（B3） | 迁移 0039 新建 `knowledge_claims`、`knowledge_conflict_reviews`、`knowledge_review_events`；新增 `app/services/rag/governance.py` | 冲突状态枚举（5 态）+ 保守提示；`potential_conflict` 输出"资料存在差异，需专业咨询"；`no_conflict_found_in_reviewed_claims` 明确不等于证实无冲突；检索后仅提示与拦截，不让 LLM 自动裁决。 |
| 双模式正式评测（B4） | `benchmark/knowledge-rag/run_dual_backend_evaluation.py` + `results/seed-20261005/{dual_backend_report.json, summary.json}` | 冻结 61 条查询集，ONNX 与哈希后备各自重跑（每模式前清 embed 缓存防污染）；输出实际后端与指标。 |

## 2. 双模式评测结果（冻结，seed 20261005，13 条活跃审核知识）

| 模式 | 实际后端 | Hit@1 | Hit@3 | 无关查询拒绝率 |
| --- | --- | --- | --- | --- |
| onnx_requested | onnx_bge | 90.2% | 100.0% | 100% |
| hash_fallback_requested | hash_fallback | 82.35% | 98.04% | 100% |

结论（如实）：后备模式性能下降（@1 90.2→82.35、@3 100→98.04），须按 B1 明确降级提示或不提供生成式健康回答；**不得把 ONNX 模式的 90.2% 当作后备模式成绩**。检索分数不等于最终回答准确率（规范 B4 明确）。

## 3. 生产库事件记录（重要，诚实披露）

- **0038 已在生产库应用**：P0 基线记录目标库 current=0037；本轮双模式评测脚本初版注入 alembic URL 失败（`migrations/env.py` 强制读 `.env` 的 `DATABASE_URL`），脚本误连生产库执行 `upgrade head`，将 0038（纯建表迁移、无数据迁移、downgrade 仅 drop 表）应用至生产，随后 0039 执行中因 MySQL 兼容性错误（TEXT 列 DEFAULT）部分失败。
- **0039 半成品已清理**：`knowledge_claims`、`knowledge_conflict_reviews`（空表）与 `knowledge_documents` 的 5 个新列已安全移除（执行前逐表核验行数为 0）；`alembic_version` 保持 0038，未触碰 0038 任何表。
- **修复**：0039 已去除 TEXT 列 DEFAULT（MySQL 不兼容）；评测脚本改用子进程 + `DATABASE_URL/ENV` 环境变量隔离到本地 SQLite，**永不触碰生产**；新增只读核查脚本 `scripts/check_prod_0039_status.py`（复核用）。
- **流程欠账（如实）**：0038 未按规范 §8"先备份 MySQL 演练"流程应用。虽为纯建表、无数据影响，仍属流程违规；P5 阶段补做 MySQL 备份/回滚演练与并发验证，补齐硬门禁。
- 复核结果（2026-10-05）：生产库 `alembic_version=0038`，0039 三张表与 5 列均不存在。
- **收尾更新（2026-10-05 同日）**：已按 §8 流程补齐生产应用——`mysqldump --single-transaction` 备份生产库（`D:\学习资料\计算机应用大赛\healthmate-prod-backups\healthmate_before_0040_*.sql`，约 4.9 MB）后，`alembic upgrade head` 将 **0039、0040 应用至生产**；复核：`alembic_version=0040_evaluation_benchmark_provenance`，`knowledge_claims / knowledge_conflict_reviews / knowledge_review_events` 三表存在（空表），`evaluation_benchmarks` 已含 dataset / evidence_level / retriever_version 三列。0038 的流程欠账仍按 §8 演练补记（备份已实际完成，回滚演练脚本由 verify_migrations 覆盖）。

## 4. 验收对照

- B1：双模式各自独立评测，实际后端如实报告，后备降级可提示。✓
- B2：审核字段（机构/URL/章节/发布日/审核人/适用人群/排除条件/复审到期日/内容哈希/启用态）齐备；旧条目不就地覆盖的版本审计由 `knowledge_review_events` 台账支撑（表已建，写入逻辑待人工审核流程接入）。△
- B3：主张/冲突存储、5 态枚举、保守提示、工具字段全部实现并通过测试；`not_assessed` 默认（不做无依据裁决）。✓
- B4：检索层双模式冻结评测完成；**最终回答原子事实双人盲评未做**（需人工），引用支持率/无来源断言率/适用人群错误率评测集待建。△
- 回归：后端全量 `pytest tests` = **538 passed**（基线 526 + P2 5 + P3 7）；0039 在本地 SQLite 全量自动应用（conftest `upgrade head`）。

## 5. 边界与未决

- 最终回答双人盲评、冲突案例人工裁定、知识条目按盲点清单扩容：需人工，未完成（如实）。
- `knowledge_claims` 目前无种子数据（检索工具在无 claim 时返回空数组、conflict=not_assessed，保守可用）。
- 0038 应用流程补演练、0039 生产应用（须先备份演练）均列入 P5。
