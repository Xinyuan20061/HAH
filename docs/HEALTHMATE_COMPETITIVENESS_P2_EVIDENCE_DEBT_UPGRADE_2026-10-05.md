# HealthMate P2 取证/证据债务升级 —— 阶段交付记录

版本：1.0
日期：2026-10-05
依据：[HEALTHMATE_COMPETITIVENESS_AND_MINIPROGRAM_COMPLETENESS_DEVELOPMENT_SPEC_2026-10-05.md](HEALTHMATE_COMPETITIVENESS_AND_MINIPROGRAM_COMPLETENESS_DEVELOPMENT_SPEC_2026-10-05.md) §7（现有接口上的增量契约）、§3.2（证据债务视图）、A3（响应模型快照与可配置规划参数）。
前置：P0 基线、P1-A1（v1 负结果诊断）、P1-A2（v2 冻结评测）均已完成落盘。

## 1. 交付清单

| 项 | 落点 | 说明 |
| --- | --- | --- |
| 响应模型快照 | 新增 `backend/app/services/policy_learning/acquisition/response_model.py` | 不可变 `ResponseModelSnapshot`（A3 前导）：schema_version / kind / version / population_scope / sample_count / probabilities / latency_ms / calibration_state / created_at；默认 `DEFAULT_RESPONSE_MODEL` = declared_prior（prior_only、sample_count=0）。 |
| 决策响应增量契约 | `service.py::_decision_response` | 现有 `GET sessions/{id}` 与 `POST next` 响应新增：`decision{explanation, endpoint, confidence_kind, estimated_burden_ms}`、`evidence_debt[]`、`planner_meta{version, response_model_kind, model_version}`；旧字段（含 user_message、allowed_actions）全部保留，旧客户端可解析。`explanation` 与 `user_message` 同源（同一稳定文案），为 §7 建议字段；`planner_meta.version` 绑定 `planner.py::PLANNER_VERSION` 常量（bounded-lookahead-v1），可审计追踪。 |
| 证据债务卡 | 新增 `service.py::_evidence_debt` | 每读重算；execution / burden / availability 三端点，各带 state、稳定 reason_code、可执行 action、过期规则；来源失效→`needs_repair/repair`，到期缺口→`askable/answer_or_skip`，未到期→`waiting_window/wait`，已充分→`sufficient/none`。 |
| reason code 稳定枚举 | 新增 `service.py::REASON_CODES` + `_reason_message` 全覆盖 | 22 个稳定 code，全部有中文文案；未知 code 走保守降级文案（旧客户端不崩）。小程序映射文案在 P4 对接。 |
| 规划器参数可配置/可消融 | `service.py::_planned_support_candidate` | `SupportPairDomain` 的 `support_cost_ms`（默认 4000）与 `support_defer_penalty`（默认 20.0）改为从 session budget 的 `planner` 块读取；默认值与 v2 冻结评测一致，可冻结、可消融（defer_penalty=0 即取消问价值）。 |
| 测试 | `tests/test_policy_acquisition.py`（+2）、`tests/test_response_model.py`（新建 +4） | API 级断言：decision 新字段、evidence_debt 三端点结构、planner_meta、stale→needs_repair；快照不可变与校验；消融测试。 |

## 2. 验收对照（规范 §7 / §3.2 / A3）

- **接口增量契约**：GET session 与 POST next 响应均经 `_decision_response` 统一构造 → 新字段自动覆盖全部读取/发题/暂停/恢复/修复路径；幂等回放响应不变（内容一致，含新字段）。
- **reason_code 枚举**：稳定追加式；未知 code 保守降级文案为"当前无法核查，请刷新后重试。"（§7 要求），不继续提交；服务端为唯一权威来源。
- **evidence_debt 卡**：含"缺什么证据（endpoint/state）、是否可取得（action）、谁控制下一步（action 语义）、是否值得问（reason_code）、为何暂缓（reason_code/wait）"；来源/时窗体现在 state 与 expiry_rule；修复源记录自动重算（`_snapshot` 对每个 observation_ref 重新解析，失效即 stale）；知识撤回→`knowledge_contract_changed` 阻断判定（既有逻辑保持）。
- **修复成本边界（如实声明）**：修复路径按原槽最小化（H2 机制，既有 rereview/repair 实现）已保持；规划器的"修复价值"项未纳入——修复不经过规划器选择（按需触发），如后续需要消融可加，本阶段如实不装完成。
- **响应模型快照**：默认 prior_only、sample_count=0，明确非真实常量；真实校准需知情同意事件日志（A3 后续）。
- **规划器价值可配置**：端点/配对价值与延迟罚分可配置、默认与冻结基准一致、可消融。
- **回归**：后端全量 `pytest tests` = **531 passed**（基线 526 + 新增 5）；acquisition 相关 66 项通过。

## 3. 边界与未决

- 本阶段未做：小程序 reason code 映射文案与证据债务卡 UI（P4 旅程验收时对接）；响应模型事件日志校准（需知情同意、真实用户日志，属 A3 后续）；数据库迁移 0038 仍未应用（须按 §8 流程演练后执行）。
- 兼容性：`GET /api/v1/knowledge/search` 未改动；`acquisition-response-v1` schema 保持版本号不变（字段为增量，向后兼容）。
