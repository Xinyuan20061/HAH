# HealthMate 竞争力升级 P0 冻结事实基线

版本：1.0（基线记录，非完成报告）
日期：2026-10-05
依据：[HEALTHMATE_COMPETITIVENESS_AND_MINIPROGRAM_COMPLETENESS_DEVELOPMENT_SPEC_2026-10-05.md](HEALTHMATE_COMPETITIVENESS_AND_MINIPROGRAM_COMPLETENESS_DEVELOPMENT_SPEC_2026-10-05.md) 第 2、10、12 节（开发开始前记录 Git revision、数据库 head、依赖版本和评测哈希；每项状态分清“代码/本地测试/真机/真实评测”）。

## 1. 代码基线（Git revision）

| 仓库 | 分支 | HEAD | 说明 |
| --- | --- | --- | --- |
| health-assistant（根） | main | `81c12b6fe32b397d8e1fd6d040dbcb99ea4ae475` | 与 origin/main 同步；工作树干净（仅 1 个未跟踪 docs 文件） |
| healthmate-backend（backend 子仓库） | main | `a7c14bf5c257bcbad6d47260b5d862b5c3e36110` | 与 origin/main 同步；工作树干净 |

v1 冻结基准报告 `benchmark/evidence-acquisition-v1/results/seed-20261005/report.json` 的 `git_commit` 字段 = `81c12b6...`，与根仓库当前 HEAD 一致，确认 v1 报告由当前代码生成。

## 2. 数据库基线

| 项 | 值 | 状态 |
| --- | --- | --- |
| 代码迁移 head（文件） | `0038_low_burden_evidence_acquisition.py` | 代码已含；LUNA 规格称其独立 SQLite 升降级演练已通过（继承文档声明，未在本轮重演） |
| 目标库 alembic current（连接腾讯云 TDSQL-C 生产库查询） | `0037_policy_decision_idempotency` | 目标库落后一个迁移；`0038` 待应用 |
| alembic env | 读取 `backend/.env` 的 `DATABASE_URL`（production，腾讯云 CynosDB MySQL） | — |

约束（继承规范 §8）：迁移从实际 `alembic heads` 追加，不改 0038 历史；先隔离 SQLite 结构测试，再对备份过的预发布 MySQL 演练升级、回滚/恢复和并发，之后才可对生产库应用。

## 3. 依赖版本（backend，requirements.txt 冻结）

fastapi==0.116.1 · uvicorn[standard]==0.35.0 · sqlalchemy==2.0.43 · alembic==1.16.5 · pydantic==2.11.7 · pydantic-settings==2.10.1 · httpx==0.28.1 · python-multipart==0.0.20 · pymysql==1.1.2 · cryptography==46.0.4 · Pillow==11.3.0 · boto3==1.40.30 · numpy==2.2.6 · tencentcloud-sdk-python==3.1.183

## 4. v1 冻结评测资产（只读保留，不修改）

| 资产 | 路径 | 备注 |
| --- | --- | --- |
| 报告 | `benchmark/evidence-acquisition-v1/results/seed-20261005/report.json` | report_schema_version=evidence-acquisition-report-v1 |
| 轨迹 | `benchmark/evidence-acquisition-v1/results/seed-20261005/traces.jsonl` | 2541 行 |
| 数据哈希 | dataset `627d16ace3fcd467f424731a43126b831da0ae3e3b3d7b36d7703197a4c10847` | config `5f915d4ba9ef8f853e9c5d45c91bc8138da7ccc4fd236d40f167c4faf530690e` |
| 种子 | seed=20261005 | — |
| 引擎 | oracle `exact-execution-v1`、planner `bounded-lookahead-v1` | — |

v1 关键指标（363 个冻结七日合成周期）：

| 策略 | resolved | coverage | accuracy | 错误标签 | 提问数 |
| --- | --- | --- | --- | --- | --- |
| two_step_decision_value（主） | 234/363 | 64.46% | 100% | 0 | 368 |
| one_step_decision_value | 234/363 | 64.46% | 100% | 0 | 368 |
| random_same_budget | 246/363 | 67.77% | 100% | 0 | 366 |
| entropy_first | 242/363 | 66.67% | 100% | 0 | 368 |
| current_gate_with_fixed_acquisition_adapter | 242/363 | 66.67% | 100% | 0 | 368 |
| small_state_exact_reference | 242/363 | 66.67% | 100% | 0 | 368 |
| fixed_fill（不安全反例） | 363/363 | 100% | 70.52% | 107 | 0 |

结论（继承 v1 报告 limitation）：two-step 与 one-step 完全同分（同 234/363、同 368 题、同成本），bounded two-step 未优于简单基线——**负结果，按规范 A1 先做根因诊断，不先调参冲榜**。

## 5. RAG 现状

| 项 | 值 | 状态 |
| --- | --- | --- |
| retriever_version | `audited_hybrid_v2`（`app/services/rag/service.py` L14） | 代码已标记 |
| embedding 后端 | `app/services/rag/embeddings.py`：ONNX（`_run_onnx`）+ 字符 n-gram 哈希后备（`_hash_fallback`）；模型不可用/异常时回退 hash | 代码已实现 |
| 部署运行标记（`embedding_backend` / `knowledge_snapshot_hash` 返回或记录） | 未核验 | 规范 B1 要求部署必须返回或记录；P3 实施时核查并补齐 |

## 6. 小程序现状

| 项 | 值 | 状态 |
| --- | --- | --- |
| app.json 注册页面 | 28 页（home、insights、scan、workout、exercise-detail、report、media、chat、records、plan、goals、profile、profile/edit、records/diet、records/exercise、policy/overview、policy/protocol、policy/episode、policy/review、policy/history、checkin、trends、settings、settings/capabilities、settings/ai、evaluation、settings/privacy、state） | 与规范 §6 责任清单一致 |
| 底部入口 | 2 个（健身房 / 小管家），custom tabBar | 与规范一致 |
| Node 合同测试 | 26 个 test 文件、156 个用例（`node --test miniprogram/tests/*.test.js`） | 数量基线；是否全绿待本轮执行确认 |
| 真机验收 | 未做 | 需 Android/iOS 各一轮（用户配合） |

## 7. 后端测试基线

| 项 | 值 | 状态 |
| --- | --- | --- |
| pytest collect | 526 tests collected（`backend/.venv/Scripts/python.exe -m pytest tests --collect-only -q`） | 与文档“526 项”历史快照一致；本轮未全量执行，实施各工作包时以实际运行结果为准 |

## 8. 缺口清单（P0 退出门槛：每项分清状态）

| 缺口 | 状态 |
| --- | --- |
| 目标库应用迁移 0038 | 代码已含；SQLite 演练（继承声明）；目标 MySQL 未应用，须按 §8 流程演练后应用 |
| RAG 部署运行标记（embedding_backend、knowledge_snapshot_hash） | 未实现/未核验（P3） |
| v1 负结果根因诊断 | 未开始（P1-A1） |
| 小程序旅程截图 | 未采集（需微信开发者工具/真机） |
| MySQL 双连接并发 / scheduler / Worker 可重建 / 云对象删除回执 | 未验收（P5） |
| 真实用户可用性试用 | 未开始（A4） |

## 9. 状态图例

- 代码：仓库中已存在实现
- 本地测试：Node/pytest 在本地通过
- 真机：Android/iOS 实机验收
- 真实评测：冻结基准 / 真人试用 / 目标环境部署证据

本文件为 P0 冻结基线；后续每阶段开发交付时，依据本文件核对“哪些基线不变、哪些已推进”。
