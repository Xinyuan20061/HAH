# HealthMate 取证策略 v1 负结果诊断报告

版本：1.0（只读诊断，非调参冲榜）
日期：2026-10-05
依据：[HEALTHMATE_COMPETITIVENESS_AND_MINIPROGRAM_COMPLETENESS_DEVELOPMENT_SPEC_2026-10-05.md](HEALTHMATE_COMPETITIVENESS_AND_MINIPROGRAM_COMPLETENESS_DEVELOPMENT_SPEC_2026-10-05.md) 工作包 A1：先做负结果诊断，回答“两步与一步为何完全同分”；未得到诊断结果前不修改阈值和报告口径。

## 1. 资产与方法

- 输入（冻结、只读）：`benchmark/evidence-acquisition-v1/results/seed-20261005/report.json`、`traces.jsonl`（2541 行）、`cases.jsonl`、`missingness_configs.json`、`response_models.json`。
- 复现门禁：用冻结生成器重新生成 363 个周期（120×3 机制 + 3 fixtures），计算 dataset hash 与冻结报告一致（`reproducibility_hash_ok=true`，dataset `627d16ac...c10847`、config `5f915d4b...f530690e`，git `81c12b6`）。
- 方法：只读重放（新增 `analyze_v1_negative_result.py`），对每个 episode 逐题记录选问、响应、oracle 状态与停止原因；重放口径为主获取循环（**不含来源修订/修复阶段**，因此重放绝对数与冻结报告有差：238 vs 234 resolved、357 vs 368 题；冻结报告含 11 个 repair 题与修订事件）。两步与一步的对比在同口径下进行，结论不受影响。
- 输出：`benchmark/evidence-acquisition-v1/results/seed-20261005/diagnosis/negative_diagnosis.json`（冻结文件未被修改）。

## 2. 核心结论：两步与一步为何完全同分

### 2.1 决策序列恒等（实证）

冻结报告与重放均确认 `two_step_decision_value` 与 `one_step_decision_value` 的端到端指标逐字段一致。重放进一步逐 episode、逐题对比：**357 个决策步骤中 0 处差异**（`differing_decisions=0`）。两步规划的“优势”从未转化为任何一次不同的提问。

### 2.2 机制根因：执行域候选对称独立，前瞻深度是死参数

`ExecutionDomain`（`app/services/policy_learning/acquisition/planner.py`）的候选问题满足：

- 每个问题只写自己的槽（answered 分支），不改变其他槽的可问性；
- 全部候选同成本（3000ms）、同先验；
- unknown/declined/no_response 分支状态不变，且被问过的槽不可再问；
- terminal_loss 对缺失槽的惩罚对称（+5.0×缺失数）。

在该结构下，任意候选 q1 的“两步期望损失”=“一步期望损失 + 对称的第二层优化项”，相对排序与前瞻深度无关，因此 `depth=2` 与 `depth=1` 在每个状态选择同一问题。**机制层不存在可被两步前瞻利用的互补结构**（执行域上没有“配对/跟进”依赖，而互补价值恰是 `SupportPairDomain`（负担域）设计的两步卖点，后者只在受控机制单测中出现）。

### 2.3 数据层因素（分层统计）

| 分层 | 数值 | 含义 |
| --- | --- | --- |
| 初始已确定 | 150/363（41.3%）；其中 label=1 有 70、label=0 有 80 | 41% 的周期无需提问即判定，策略差异空间被压缩 |
| 初始候选数 | 0 候选 40 例、1 候选 96 例、2 候选 106 例、3 候选 79 例、4 候选 31 例、5 候选 9 例、6 候选 2 例 | 中位候选仅 2 个；**≥3 个可问候选的周期仅 121/363（33%）**，两步的“第二层”空间在多数周期不存在 |
| 停止原因 | endpoint_sufficient 207、budget_exhausted 144、candidates_empty 12 | 57 例在第一题后判定完成；144 例预算耗尽时仍未判定 |
| 响应分布（357 题） | answered 197（55.2%）、no_response 73、unknown 65、declined 22 | 拒答+无响应占 44.8% |
| 问后标签变化 | 88/197 answered 促成 label 翻转（边际有效性 44.7%） | 单槽回答通常不足以跨越 5/7 完成阈值 |
| 判定分层 | 0 题判定 150、1 题判定 57、2 题判定 31（合计 238） | 两题预算下可判定上限内，大多数判定由 0–1 题完成 |
| 目标端点 | 全部为 execution | 本基准未混入 burden 端点 |

### 2.4 附带发现：声明先验与模拟响应分布错配（须在 v2 验证）

规划器使用声明先验（answered=0.75 等）做期望损失，而模拟响应按机制调整（MAR 下 p_answered=0.45+0.30×完成率，MNAR 下未完成槽额外调低；实际 answered 率 55.2%）。因此基于先验的“最优选择”与实际响应分布存在系统性偏差——这同时解释了为何 `random_same_budget`（252 resolved）和 `entropy_first`/`current_gate`/`small_state_exact_reference`（248 resolved）均高于 decision_value（238）：**不是随机更聪明，而是价值策略的依据（先验）与真实响应生成（机制）不一致**。此条为诊断性观察，v2 设计时应分别用正确标定的先验与真实响应分布重估，不得作为“v1 已胜”的改写依据。

### 2.5 其他策略关系

- `entropy_first`、`current_gate_with_fixed_acquisition_adapter`、`small_state_exact_reference` 三者决策序列 0 差异（7 槽小状态、固定阈值下等价）；
- 上述三者与 decision_value 差 58 步；与 random 差 179 步（random 由 case 的 `random_order` 驱动）。

## 3. 对 v2 设计的要求（按规范 A2，仅声明不实施）

1. **引入可被两步利用的互补结构**：执行域若无配对/跟进依赖，两步机制无从发挥；v2 案例集应显式包含“基线/跟进”配对端点（执行缺口 + 负担跟进），并让候选价值、修复价值、端点价值可配置、可消融。
2. **先验标定而非声明**：v2 的响应模型须从合成响应分布按机制分别标定，或对先验错配做敏感性区间；样本不足时收缩到 `prior_only` 并明确标注。
3. **预算结构多样化**：当前 2 题/10s 下 41% 周期 0 题即判定、多数周期候选 ≤2；v2 预算曲线与候选生成需覆盖更多“候选充足、判定不确定”的周期，否则任何策略差异都无法显现。
4. **保留负结果**：v1 冻结文件与本文诊断只读保留，不改数据冒称 v1 获胜。

## 4. 未决项

- 拒答/超时对“规划停止原因”的贡献已统计，但“问后标签变化是否因响应类型而异”的交叉表未展开（可后续补充）；
- 先验错配假设（§2.4）需在 v2 用重标定先验对照验证；
- 真人回答概率/耗时无校准（继承 A3：无真实数据不宣称真人负担降低）。
