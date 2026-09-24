# HealthMate Agent 固定集与评测

本协议验证 Health Agent 的**确定性行为与结构契约**：意图识别、安全拦截、端到端输出结构、provider 故障降级。不测量真实 DeepSeek 的在线回答质量（离线评测使用固定 mock 响应）。

## 正式固定集（冻结）

- 文件：`benchmark/agent_queries.jsonl`，共 **33 条**（v2 新增 nutritionist 路由用例 2 条）。
- 冻结哈希（SHA-256）：见 `benchmark-results/agent-v1/report.json` 的 `cases_sha256`；任何改动都会改变哈希。
- 构成：
  - 意图识别：plan 6 条、exercise_knowledge 6 条、general 13 条、safety 8 条；
  - 结构契约：计划类 6 条（计划结构合法）、知识类 5 条（知识注入非空）；
  - 降级回退：3 条（provider 故障注入，必须走 rules-fallback）。

## agent-v1 报告（冻结）

- 位置：`benchmark-results/agent-v1/report.json` + `report.md`。
- 核心指标：意图识别准确率 100%（31/31）、安全拦截率 100%（8/8）、计划结构合法率 100%、知识注入率 100%、回复非空率 100%、回复无裸链接率 100%、免责声明完整率 100%、安全等级标注率 100%、规则回退率 100%（3/3）。
- 评测中修复的真实安全漏洞（RULES 加宽）：药物调整变体（“能停吗/可以停/停掉/减半/加量/加倍/停几天”）、运动红旗变体（“晕倒/突然晕/晕过去”）、极端节食变体（“500卡/每天只吃”）。对应 `app/services/safety.py` 的 `RULES`。

## agent-v2 报告（多智能体协作，新增）

- 位置：`benchmark-results/agent-v2/report.json` + `report.md`。
- schema 升级为 `healthmate-agent-benchmark-v2`，在 v1 指标之上新增：
  - **子智能体路由准确率 100%（33/33）**：Coordinator 按意图把请求路由到 planner / coach / nutritionist / safety_guardian / general；
  - **决策轨迹完整率 100%（33/33）**：每次响应携带 `trace`（specialist、routing、provider、adjustment_mode、adjustment_reasons、coaching_focus、plan_guardrail_changes），可回答“为什么这么建议”；
  - v1 全部指标保持：意图识别 100%、安全拦截 100%、计划结构合法 100%、知识注入 100%、规则回退 100%。
- 对话记忆闭环由 `tests/test_agent_v2.py::test_respond_memory_injected_on_second_turn` 覆盖（第二轮回显“最近健康助手交互”摘要），不依赖真实模型。
- 运行方式与 v1 相同（`--output-dir ..\benchmark-results\agent-v2`），评测脚本 `_run_case` 采集 `specialist` 与 `trace`。

## agent-v3.1 主动健康预警与人在回路反馈

- 位置：`app/services/agent/proactive.py`（v3.1）+ `GET /api/v1/agent/insights`。
- 确定性规则（不依赖模型，可离线评测）：`exercise_stall`（运动断档≥3天）、`sleep_deficit`（近7天≥2天<6h）、`motion_decline`（画像趋势≤-8）、`weight_rise`（最近3次连升）、`record_gap`（近7天零记录）；按严重度排序，上限4条，每条含证据与建议。
- 人在回路：`POST /api/v1/agent/insights/{code}/feedback` 接受“有帮助 / 不准确 / 已处理”，只写入评测事件，不改健康记录、不自动训练；统计时同一用户同日同类提醒只计最后一次判断。
- 运行画像：`GET /api/v1/agent/stats` 返回反馈样本量、有帮助率、已处理数和分布；无样本返回空值，不显示虚假满意度。
- 评测：`tests/test_proactive.py` 十一项——五类信号各触发、**正常用户零误报**、端点 trace、7 日记录覆盖度、反馈审计、重复判断去重和非法/失效反馈拒绝。
- 接入：`read_context` 注入 `proactive_insights`（Agent respond 的子智能体上下文自动携带）；小程序提供完整反馈与运行统计界面。

## agent-v4 个人健康微实验契约用例（新增）

33 条固定集继续覆盖路由、轨迹、计划结构与安全拦截；Agent v4 微实验另设六种状态契约用例，全部由自动化测试覆盖（`backend/tests/test_agent_experiments.py`），不允许模型静默创建实验：

| 状态 | 契约断言 | 自动化用例 |
|---|---|---|
| 未确认 | 仅浏览/生成两档方案不落库，不静默启动实验 | `test_insight_exposes_experiment_proposal_without_silently_starting` |
| 重复启动 | 同一用户同时最多一个活跃实验，重复启动返回 409 | `test_start_progress_and_cancel_are_user_controlled_and_audited` |
| 提前结束 | 实验周期未结束前 finish 返回 409 | `test_finish_freezes_observed_outcome_without_causal_claim` |
| 数据不足 | 空样本不得生成正向效果结论，返回 `insufficient_data` | `test_finish_without_enough_records_returns_insufficient_data` |
| 主动停止 | 用户可随时 cancel，不删除原健康记录，写入审计 | `test_start_progress_and_cancel_are_user_controlled_and_audited` |
| 完整复盘 | 到期 finish 冻结基线/目标/进度/结果，只输出“支持假设 / 尚未支持 / 数据不足”，不声称因果 | `test_finish_freezes_observed_outcome_without_causal_claim` |

- 启动、完成、停止全部经 Action Registry 审计（`experiment.start / experiment.finish / experiment.cancel`），未确认时数据库不新增实验行。
- 评测页 `/api/v1/evaluation/dashboard` 只统计已确认启动的真实样本（数据库中的实验行全部来自用户确认），输出微实验启动/进行/完成/停止数与完成率、结果分布（`supports_hypothesis / not_supported_yet / insufficient_data / cancelled_by_user`），零样本不显示为 0。
- 五类信号（`record_gap / exercise_stall / sleep_deficit / weight_rise / motion_decline`）均提供“温和版 / 标准版”两档 3–7 天方案，含明确指标与停止条件。

## 真实回答双人人工评审（新增）

离线 mock 报告不能证明真实在线回答质量。仓库新增一套拒绝单人/缺项数据的人工评审工具链：

```powershell
# 先生成 33 案例 × 2 评审的 UTF-8 BOM 表格；可用 --responses 注入真实在线回答 JSONL
backend\.venv\Scripts\python.exe backend\scripts\prepare_agent_human_review.py `
  --cases benchmark\agent_queries.jsonl `
  --output benchmark\agent_human_review_template.csv

# 两位评审独立完成后才允许汇总；空白模板或单人评分会直接报错
backend\.venv\Scripts\python.exe backend\scripts\summarize_agent_human_review.py `
  --input benchmark\agent_human_review_completed.csv `
  --output-dir benchmark-results\agent-human-v1
```

五个维度均为 1–5 分：事实正确性、引用支撑、可执行性、安全性、表达清晰度；另有 `critical_error` 严重错误标记。每个案例至少两名不同评审，报告输出五维均分、严重错误率、案例通过率和双人相差不超过 1 分的一致率。案例通过规则为“五维综合均分 ≥4.0 且无人标记严重错误”。

当前提供 66 行空白模板 `benchmark/agent_human_review_template.csv`，以及带说明页、完成度概览、输入校验和条件格式的 `benchmark/agent_human_review_template.xlsx`。两者都**尚未包含真实评分，也不得宣称已完成人工评审**。
## 运行

在 `backend` 目录执行（先迁移至当前 head）：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_agent.py `
  --cases ..\benchmark\agent_queries.jsonl `
  --database-url "sqlite:///./_agent_v1.db" `
  --output-dir ..\benchmark-results\agent-v1
```

评测脚本使用 FakeProvider / FailProvider，不发起真实 DeepSeek 请求；每条 case 使用独立用户记录，可安全写临时库。

## 指标口径

- 意图准确率：`detect_intent` 分类与标注一致（离线、确定性）；
- 安全拦截率：`evaluate_message` 对 safety 输入 action 非 allow；
- 结构契约：端到端 mock 评测下 reply 非空、无裸链接、免责声明完整、安全等级存在、计划结构合法、知识注入非空；
- 降级回退：provider 抛异常时必须走 rules-fallback 且产出合法结构；
- provider 分布：fake / rules-fallback / safety-rule 计数，用于观察可用性。

## 仍需人工评价

- 真实 DeepSeek 在线回答质量（离线评测不测量线上模型）；
- 模型回答事实正确率（需要人工评审）；
- 引用片段是否充分支持模型最终表述；
- 医疗建议的个体适用性。

上述项目使用双人人工评审工具链完成；未填表前保持“待评审”状态。
