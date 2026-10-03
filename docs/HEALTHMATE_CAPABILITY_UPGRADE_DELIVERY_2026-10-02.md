# HealthMate 能力升级交付报告（对照 2026-10-02 能力方案）

> 方案：`docs/HEALTHMATE_FIRST_PRIZE_CAPABILITY_PLAN_2026-10-02.md`
> 承接：`docs/HEALTHMATE_REMEDIATION_DELIVERY_2026-10-02.md`（整改阶段的交付报告）
> 迁移基线：以 `alembic heads` 命令结果为准（本文不手写修订号）
> 原则：**只写已经跑过并留下的证据。** 需要真实数据、真实设备或人工评审才能得出的结论，
> 一律放在 §4，不并入"已完成"。自动化测试通过 ≠ 模型准确率达标。

---

## 1. 验收命令与实测结果

全部命令均在本机离线环境执行，可在同一 checkout 直接复跑。

| 范围 | 命令 | 结果 |
| --- | --- | --- |
| 后端 | `backend\.venv\Scripts\python.exe -m pytest -q` | **488 passed**, 0 failed |
| Worker | `ai-worker\.venv\Scripts\python.exe -m pytest -q` | **279 passed**, 0 failed |
| 小程序 | `node --test "tests/*.test.js"` | **121 passed**, 0 failed |
| 小程序 UI 规范 | `node scripts\audit_miniprogram_ui.mjs` | 7 项（HIGH=0 MEDIUM=2 LOW=5），`--strict` exit 0 |
| Worker 交付门禁 | `D:\HealthMate\.venv\Scripts\python.exe scripts\strict_doctor.py` | 5/5 硬门禁通过，1 条 advisory（源码路径非 ASCII） |
| 能力诚实性（代码层） | `python scripts\audit_capability_honesty.py` | OK（Gold 阈值 + planning-only 声明） |
| 能力诚实性（全 4 条款） | `python scripts\audit_capability_honesty.py --database-url <全新迁移库>` | OK（exit 0，含"无伪造 Gold"与食物库复核状态） |
| OpenAPI | `python scripts\audit_openapi.py` | OK（operationId / 路由无重复） |
| 迁移 head | `python scripts\audit_migration_head.py` | OK（单 head） |
| 隐私覆盖 | `python scripts\audit_privacy_coverage.py` | OK |
| 数据一致性 | `python scripts\audit_data_consistency.py --database-url <全新迁移库>` | OK（exit 0） |
| 小程序 UI 规范 | `node scripts\audit_miniprogram_ui.mjs` | 7 项（HIGH=0 MEDIUM=2 LOW=5） |
新增测试文件的规模：

| 文件 | 项数 | 覆盖条款 |
| --- | --- | --- |
| `backend/tests/test_health_state_engine.py` | 15 | §13.1 |
| `backend/tests/test_food_interactive.py` | 18 | §13.3 |
| `backend/tests/test_plan_solver_and_harness3.py` | 44 | §13.4 / §13.5 |
| `backend/tests/test_model_call_budget.py` | 8 | §13.5 预算 |
| `backend/tests/test_outcome_learning_wiring.py` | 8 | §9.2 / §13.5 |
| `backend/tests/test_long_term_memory.py` | 27 | §9.2 / §9.3 / §9.5 |
| `backend/tests/test_state_invalidation_wiring.py` | 13 | §4.5 增量失效 |
| `backend/tests/test_phase_capability_honesty.py` | 11 | §13.6（含注入测试） |
| `miniprogram/tests/statePagePresentation.test.js` | 10 | 闭环页诚实性（缺失≠0 / insufficient_data / 记忆分层 / 只读 / 点按反馈 / 4rpx 刻度 / 无死 CSS） |
| `ai-worker/tests/test_motion_gold.py` | 123 | §13.2（骨架层） |

---

## 2. 按 Phase 的交付内容

### Phase 1 统一健康状态（§13.1）

- 新增 `health_state_features` / `health_state_snapshots`（迁移 0027）；
- `backend/app/services/health_state/`：11 个特征定义、`FEATURE_VERSION`、
  覆盖度决定置信度（≥6 天 HIGH / ≥3 天 MEDIUM）、每条值带
  `evidence_type ∈ {observed, derived, model_inferred, user_confirmed}` 与 limitations；
- 硬约束层 `constraints.py`：安全规则、覆盖不足、趋势样本不足、无测量器动作、用户排除项；
- 读接口 `/api/v1/health/state|history|features|constraints` 与 `/signals`、`/outcomes`；
- Harness 工具 `health.state.read|history`、`health.constraints.read`、`health.signals.read`、
  `health.outcomes.compare`，并按 persona 授权。

关键不变量：`value=null` 表示数据不足**而不是 0**；置信度来自记录覆盖度，
**不采用模型自报置信度**；不同模型版本的动作分数不直接比较。

### Phase 2 动作 Gold 8（§13.2）

- Worker 新增 `healthmate_worker/processors/motion_gold/`（18 个模块）：
  特征提取、相位分割、计次状态机、8 个动作的评价器、开集判定、
  证据绑定、§5.10 门禁算术；
- 后端 `motion_gold_evaluations`（迁移 0028）+ `services/motion/gold_eval.py`
  + `POST /api/v1/capabilities/motion-gold/{id}/evaluate`；
- **能力等级按动作逐项判定**，且只读持久化的、通过门禁的评测记录。

诚实分界（重要）：本阶段交付的是**可运行的规则化骨架与门禁**，不是"达到 Gold 指标"。
没有评测报告时 `evaluate_gold_gate({})` 返回 `silver` 并列出缺失的 8 项指标；
`GET /capabilities/honesty` 会如实显示 `gold_exercises: []`。
`离心控制`（弯举）与 `速度控制`（侧平举）**刻意不计分**——把不稳定的二阶导数包装成数字
比不给数字更糟。单目视角无法分离的动作（弓步后腿深度、肩推手腕对齐）在
`explanation_key` 中标注为 `.proxy`。

### Phase 3 交互式识餐 2.0（§13.3）

- `food_references` / `food_analysis_questions` / `user_food_priors`（迁移 0029）；
- `backend/app/services/food/`：
  - `seed_data.py`：45 条中文食物参考均值 + 做法用油/糖调整 + 别名表；
  - `references.py`：**仅精确名/精确别名**匹配，不做子串猜测（"鸡"不会命中鸡胸肉）；
  - `calculator.py`：营养值由**本地表 × 用户确认份量**确定性计算，模型自由文本数值不参与；
  - `clarifications.py`：最多 2 问，份量问题按"区间缩减"排序、用油/酱汁问题按
    "系统性偏差缩减"单独设阈值并保留一个名额；
  - `priors.py`：个人份量先验，**≥3 次确认**才生效，取中位数，离群值不入库；
- 接口：`/api/v1/food/references|lookup|analysis/{id}/questions|answers|calculate|priors`。

未映射食物**不计入合计**并在 limitations 中列出；缺份量且无可用先验时拒绝编造数值。

### Phase 4 约束计划与 Harness 3.0（§13.4 / §13.5）

- `backend/app/services/planning/`：
  - `solver.py`：确定性 beam search + 显式命名权重（含 `variety`）；
  - `constraints.py`：10 条硬约束，每条以规则名报告，可解释；
  - `replan.py`：只调整未来项目，**已完成冻结**，未完成不判失败，输出 diff 供确认；
- `backend/app/services/agent/capability_graph.py`：能力可用性来自 Worker 上报 +
  通过门禁的评测记录，**未测量一律视为不可用**；
- `backend/app/services/agent/decision.py`：封闭候选集合、具名过滤原因、确定性排序、
  NBA + alternatives + 权重明细；
- 新增 Action `plan.replan.apply`（diff 在确认时由服务端重算，拒绝客户端传入 diff）；
- 新增只读工具 `plan.simulate`、`decision.contract`、`decision.next_best_action`、
  `harness.capabilities.read`；
- 接口：`/api/v1/agent/capabilities|decision|next-action`、`/api/v1/plan/solve|current/proposal`。

### Phase 5 结果学习（§9.2/§9.5）

- `action_outcomes` / `action_policy_stats` / `user_preference_memory`（迁移 0030）；
- `services/agent/outcome.py`：Beta 后验、`insufficient_data` **不**更新后验、
  偏好键白名单、`rank_variants()` 只重排调用方已认定安全的选项；
- **§9.5 点名的四个工具全部注册**：`outcomes.history.read`、`preferences.read`、
  `experiment.result.read`、`next_action.rank`（第四个即 Decision Contract 的排序入口）；
- **长期结构化记忆进入统一读取上下文**：`read_context` 现在返回 `memory` 与
  `preferences`，Decision Contract 返回 `memory_used` / `memory_ignored`，
  `plan.simulate` 会用它补齐**未指定**的形态/强度参数；
- 结果记录已接入：提案确认、微实验结束（取真实结论）、目标调整应用；
- 同事件按 `(user, action, source, source_id)` 幂等，不会重复计入后验；
- 长期记忆只来自 `explicit` / `repeated_choice`，可通过
  `GET/PUT/DELETE /api/v1/health/preferences` 查看、设置与清除，清除后立即不影响排序。

记忆的影响边界是**代码强制**的，不是文档约定：

| 边界 | 实现方式 |
| --- | --- |
| 只有行为类键能影响决策 | `BEHAVIOURAL_PREFERENCE_KEYS`；`excluded_exercises` 等陈述类键 `influential=False` |
| 过期/低置信度不生效 | `memory_view()` 逐条标记 `influential` |
| 未登记的来源不生效 | `ALLOWED_MEMORY_SOURCES` + `memory_view` 把越权来源记入 `ignored` |
| 记忆不得改变安全边界 | 记忆只投影到形态/强度字段；硬约束在约束层独立判定 |
| 清除后立即失效 | 删除行后 `memory_used` 为空（有测试断言） |

§9.3「不学习的对象」（医学诊断、安全阈值、药物、未确认推断、单次异常、模型权重）
以 `NEVER_LEARNED` 数据形式随记忆一起返回，可被接口读取、可被测试断言。

### Phase 1 补做：增量失效接线（§4.5）

`invalidate()` 之前**只被定义、从未被调用**——这是本轮才发现的问题：编辑一餐后，
持久化的特征行仍描述旧值。现在建立
`app/services/health_state/invalidation.py` 作为唯一入口，并接入全部记录变更路径：

| 路径 | 失效来源 |
| --- | --- |
| `POST/PATCH/DELETE /diet/records` | `diet_record` |
| `POST/DELETE /exercise/records` | `exercise_record` |
| `PUT /health/checkin/today` | `checkin` |
| 新建/确认 Action 提案 | 刷新 `active_actions`（无特征失效，只重算） |

三条性质由测试锁定：失效**按域限定**（改饮食不会丢掉运动类特征）、
**best-effort**（状态层失败不会让已提交的记录编辑报错）、
**未知来源不静默通过**（拼错域名会记日志而不是假装失效成功）。
写接口的响应体新增 `state_invalidated`，让传播对客户端**可观察**而非隐形副作用。

---

## 3. 实施中被验收条款抓出的真实缺陷（已修复）

这一节比功能清单更重要：以下问题都不是设计遗漏，而是 §13 的验收条款真的把它们抓出来了。

| # | 缺陷 | 后果 | 修复 |
| --- | --- | --- | --- |
| 1 | 能力图按"有评测版本"升级整个目录 | 单个动作通过评测，全部动作变成 Gold | 逐动作判定（`_gold_evaluator_exercises`） |
| 2 | `conftest` 清表清单漏 `food_references` | 测试隔离删掉随包发布的食物库，确定性计算全为 0 | 加入不可变种子数据白名单 |
| 3 | 计划求解器无多样性项 | "每天同样三个动作"得分最高 | 显式 `variety` 权重 |
| 4 | 自重动作器械判定过严 | 用户说"只有哑铃"时计划为空 | 自重隐式可用 |
| 5 | 覆盖不足时连恢复建议也判非法 | 新用户得不到任何帮助 | 覆盖门禁只约束训练负荷 |
| 6 | 追问引擎会追问已知信息 | 两个名额浪费一个在已有份量上 | 按错误类型分设阈值 + 保留偏差名额 |
| 7 | `_reduction()` 对用油/酱汁返回 0 | 偏差类问题永远不达标、永远不问 | 改由表中数值计算的 `_bias_reduction()` |
| 8 | `TurnBudget` 恒为 5 | §13.5 的"简单任务 ≤2"只存在于注释 | `TurnBudget.for_task` 由领域关键词推导 |
| 9 | `insufficient_data` 仍 `alpha += 1` | 无法测量的实验会偷偷加强后验 | 先看结论再更新 |
| 10 | 结果记录异常被 `except` 吞掉 | 非法结论被静默丢弃，结果从未进入后验 | 结论受词表约束 + 幂等写入 |
| 11 | 非训练项被计入 session/天数上限 | 习惯类项目会被误判为超时 | 校验只看 `category == "exercise"` |
| 12 | `_has_personal_prior` 在 `has_mass` 为真时不进入 | 份量已被观察到仍会追问 | 条件重排 |
| 13 | `invalidate()` 只被定义、**从未被调用** | 编辑一餐后持久化特征仍描述旧值，后续计划与信号都基于已更正的旧数据 | 新增 `health_state/invalidation.py` 并接入全部记录变更路径 |
| 14 | 写接口用 `response_model` 时 `state_invalidated` 被静默丢弃 | 失效传播变成不可观察的副作用；改用无模型返回又破坏了 `UTCDateTime` 的 `Z` 序列化（被 `test_reliability.py` 抓到） | 字段进入 `DietOut` schema，恢复 `response_model` |
| 15 | `active_actions` 在提案变化后不刷新 | Decision Contract 会继续提议已有待确认提案的动作，`already_active` 过滤永不触发 | 提案创建与确认后刷新快照 |

第 13 项是最典型的一类问题：函数存在、单测直接调用它、一切"看起来已实现"，
但它不在任何生产路径上——只有把断言写在真实 HTTP 端点上才会暴露。
第 14 项则是修第 13 项时引入的回归，由既有测试当场拦下。

第 10 项特别值得记录：它的表现是"功能正常、数据为空"，只有把幂等性和词表一起断言才会暴露。

---

## 4. 未完成 / 无法在本环境验证的部分（不得表述为已完成）

下列项目需要真实数据、真人评审、平台凭据或真机，当前离线环境**无法**产生可信结论。
本节同时在 `capabilities/honesty` 接口与方案文档 §13ter 中对外可见。

| 项目 | 现状与原因 | 开工所需输入 |
| --- | --- | --- |
| §13.2 准确率指标 | 仓库无标注数据集、无训练好的时序动作分类器；门禁在无指标时返回 `silver` 并列出缺失的 8 项 | ≥8 动作、独立 subject split 的标注视频 + 分类器训练 |
| 动作 Gold 真人双人复核 | 需要真人评审与一致性统计 | 两名评审员 |
| 食物库营养师逐项复核 | `seed_data.py` 为公开成分表参考均值，标记 `seed_unreviewed`；`review_reference()` 已就绪但需真实来源 id | 营养师 + 可引用的来源 |
| 真实食物基准（≥100 张冻结图片） | 需冻结图片集 | 数据集 |
| Agent 真实模型双人评审 | 需真实模型调用与人工评审预算 | 评审流程与预算 |
| 云对象存储删除证明 | 需平台凭据 | 云存储账号 |
| 生产监控告警 | 需部署环境 | 监控平台 |
| 真机端到端 | 明确不在本次范围 | 真机 |

### 4.0 小程序闭环页已交付（本项已从"未完成"移出）

新增 `pages/state/index`（「状态与下一步」），把三个只读能力串成用户可见的闭环：

| 区块 | 数据来源 | 回答的问题 |
| --- | --- | --- |
| 现在建议我做什么 | `GET /agent/decision` | Next Best Action + 权重明细 + 次优候选 |
| 我现在处于什么状态 | `GET /health/state` | 每个值的证据类型 / 覆盖度置信度 / 实际天数 / 限制 |
| 做过之后发生了什么 | `GET /health/outcomes` | 观察结果 + Beta 统计 + `memory_used` / `memory_ignored` |

两条设计取舍需要说明：

1. **本页严格只读。** 它只调用 `api.get`，"去和小管家确认"只是复用 chat 既有的
   prefill 约定（`healthmate_insight_prompt`）把请求填进输入框，**不代替用户发送、
   更不代替用户确认**。写入仍然只能通过 Action 提案 + 用户确认，与 §13.5 一致。
2. **内部键名不外泄。** `/health/state` 现在返回 `titles` 映射（来自 feature registry）。
   标题刻意**不放进** `StateValue`——否则改一句文案就会改变 `snapshot_hash`，
   进而让缓存与失效判定出现无意义的抖动。有测试钉住这一点。

`miniprogram/tests/statePagePresentation.test.js`（10 项）把这一页的诚实性约束文本化：
缺失值必须渲染「数据不足」而非 0、`insufficient_data` 不得表述为有效、记忆必须区分
「正在影响建议」与「已记录但不影响决策」、只允许 GET、每个可点元素必须具备点按反馈三件套、
间距必须在 4rpx 刻度上、WXSS 不得有死类，并且不得使用 worklet/GSAP/DOM API
（WebView 渲染器闸门）。

**仍需人工确认的部分**：动效观感与真机表现无法用 Node 测试验证
（骨架屏、入场淡入、点按反馈的实际手感），需在微信开发者工具中肉眼确认。

### 4.1 关于 `audit_data_consistency.py` 的 FAIL

`python scripts\audit_data_consistency.py` 不带参数时会审计 `.env` 指向的**生产库**，
该库报出重复事件（如 `plan_task#17 daily_plan_completed x32`）与悬空外键
（`id=76` 指向不存在的饮食记录 `#2`）。

- 这是**整改前遗留的数据问题，不是代码缺陷**：同一命令指向一个全新迁移库时 **exit 0**；
- 已实测确认：
  `python scripts\audit_data_consistency.py --database-url <全新迁移库>` → OK；
- 处理建议：按该审计输出的 ID 列表做一次数据修复（去重 + 修悬空引用），
  或在 CI 中固定使用迁移库而非生产库。本报告不把生产库的既有脏数据计入本次交付。

### 4.2 关于 Worker 门禁的 ASCII 路径 advisory

`strict_doctor.py` 的 `interpreter_path_ascii` 是硬门禁，5/5 通过；
`source path 非 ASCII` 是 **advisory**，因为 MediaPipe 通过
`start_worker.ps1` 的 venvlink 能正常推理（已实测 `mediapipe_pose_inference` 通过）。
这与整改阶段用户纠正过的判定口径一致：决定性的检查是"MediaPipe 能否真的推理"，
不是源码目录名。

---

## 5. 最终形态与下一步

方案 §14 的七条能力，本次实际达成情况：

1. **看得见但不乱猜** — ✅ 动作：无证据不评分、单目不可分离项标 `.proxy`；食物：未映射不计入、
   缺份量不编造；
2. **能形成个人状态** — ✅ 版本化 Health State + 覆盖度置信度；
3. **能做合法决策** — ✅ 约束求解 + 校验器 + Decision Contract；
4. **能真正执行** — ✅ 统一 Action Runtime，未确认不写库，重复确认幂等；
5. **能观察结果** — ✅ 提案/微实验/目标调整的结果进入后验；
6. **能逐渐适应个人** — ✅ 样本 ≥3 才个性化，只重排安全选项，可查看可清除；
7. **能跨模态闭环** — ✅ 后端证据链 + `pages/state/index` 闭环页
   （建议 / 状态 / 做过之后的变化三区块），首页新增入口。

下一步的优先级：

1. 在微信开发者工具里肉眼确认闭环页的动效与真机表现（Node 测试覆盖不到观感）；
2. 冻结一份 ≥100 张的食物基准集与 ≥8 动作的标注集，让 §13.2 的指标从"缺失"变成"已测量"；
3. 生产库数据修复（§4.1）；
4. 营养师复核食物库并把 `reviewed_at` 落地，届时文案可从"粗略草稿"升级。

在此之前，对外表述必须遵守 `capabilities/honesty` 返回的 `claim_rules`：
**未通过 Gold 门禁的动作不得称 Gold；未复核的食物库不得称营养分析；
未测量的能力一律视为不可用。**
