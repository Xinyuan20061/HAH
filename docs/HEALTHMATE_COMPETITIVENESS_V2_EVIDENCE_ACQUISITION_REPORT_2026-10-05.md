# HealthMate 取证策略 v2 冻结评测报告

版本：1.0（冻结评测，非完成报告；未调参冲榜）
日期：2026-10-05
依据：[HEALTHMATE_COMPETITIVENESS_AND_MINIPROGRAM_COMPLETENESS_DEVELOPMENT_SPEC_2026-10-05.md](HEALTHMATE_COMPETITIVENESS_AND_MINIPROGRAM_COMPLETENESS_DEVELOPMENT_SPEC_2026-10-05.md) 工作包 A2（v2 数据生成、可审计案例清单、同预算基线、主指标与 95% 区间）；继承 A1 诊断结论：执行域对称独立使两步=一步，v2 引入配对负担互补结构。

## 1. 冻结资产

| 项 | 值 |
| --- | --- |
| 目录 | `benchmark/evidence-acquisition-v2/`（新增；v1 冻结文件未修改） |
| seed | 20261005 |
| dataset_hash | `dc7123eff615fd469449645e0cb4920996b590cf10894dccef100379bd7584ad` |
| config_hash | `4dbffa1aed23076a924984ed53a8776d94c5d01650f3fb30aafb4aa6562fa957` |
| git_commit | `81c12b6fe32b397d8e1fd6d040dbcb99ea4ae475` |
| 输出 | `results/seed-20261005/{report.json, traces.jsonl, cases.jsonl, failure_cases.jsonl}` |
| 证据级别 | synthetic_replay（无用户数据、无真机） |

## 2. 设计（对应 A2 要求）

**复合端点域**（`domain.py`）：状态 = 执行端点（7 槽精确完整证明）+ 负担端点（baseline/follow-up 配对，配对须两半都回答才计数）。这是 v1 缺失的互补结构：两步前瞻可"先问 baseline、回答后问 follow-up 完成配对"。

**可审计案例清单**（9 类，360 周期 = 40 用户 × 9；train 252 / test 108，按用户 70/30 分组，调参与测试不共享用户）：

| 类型 | 数量 | 隐藏真值/候选 |
| --- | --- | --- |
| execution_gap | 70 | 缺失执行报告；候选=缺口槽 |
| paired_burden | 54 | 执行已充分但负担配对缺失；候选=baseline/followup |
| refusal_unknown | 58 | 高拒答/未知率响应模型 |
| source_revision | 44 | corrected/deleted 来源修订事件 |
| mnar | 41 | 未完成被更频繁隐藏 |
| window_boundary | 29 | 部分槽未到期不可问 |
| knowledge_withdrawal | 27 | 知识撤回→负担配对重新待核 |
| certificate_invalidation_repair | 18 | 修订失效+修复询问 |
| duplicate_cross_day | 19 | 跨日重复/重试语义（幂等防重） |

**同预算基线**（7 臂 + 不安全反例）：two_step / one_step / random_same_budget / entropy_first / current_gate / fixed_order / small_state_exact_reference（fixed_fill 仍为反例不参与）。共用同一案例、同一响应随机量与延迟、同一预算（4 题 / 16s）、失败入分母。

**预注册**：主方向"two_step 主成本 ≤ one_step 且覆盖率不降"；安全门"0 错误判定、0 过期证书使用、0 重复签发"；全部在运行前固定。

**主指标**：每"正确且可核验的已确定端点"的累计交互成本，问题数与用户报告时间（模拟延迟代理，明确标注）分别报告；强制报告覆盖率、错误确定率、暂缓率、拒答、P50/P95、修复成本、安全事件；用户级 bootstrap 95% 区间。

## 3. 实施中发现并修复的两个缺陷（真实价值，非调参）

1. **规划器看不见已问集合（产品代码缺陷）**：`choose_next` 从空 `asked` 重新规划，会把已问过的问题再次选为候选（unknown/declined/no_response 分支状态不变时尤其明显），服务层"唯一有效问题"约束会拦截它 → v2 首次运行 `duplicate_attempts` 高达 102（two_step）。已在 `planner.py` 增加向后兼容的 `asked` 参数并传参（默认空集合，v1 行为不变；相关 68 项测试通过）。修复后 duplicate=0。
2. **修订后 gold 口径错误（评测缺陷）**：`source_revision` 案例在 corrected 后翻转 truth，但 gold 用修订前 `case["truth"]`、判定用修订后 state → 误报 7 例错误判定。已改为 gold 与判定使用同一 truth 纪元（修订后）。修复后所有策略 wrong_determined=0。

## 4. 冻结结果（test 集，108 周期 / 12 用户；全量见 report.json）

| 策略 | coverage | accuracy | 成本(题/正确判定) | 成本(ms/正确判定) | P95(ms/周期) | wrong | stale | dup |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **two_step_decision_value** | **70.4%** | 100% | 2.59 | 9,677 | 15,667 | 0 | 0 | 0 |
| small_state_exact_reference | 70.4% | 100% | 2.62 | 9,754 | 15,667 | 0 | 0 | 0 |
| random_same_budget | 71.3% | 100% | 2.90 | 10,782 | 15,872 | 0 | 0 | 0 |
| fixed_order | 69.4% | 100% | 2.89 | 10,732 | 15,852 | 0 | 0 | 0 |
| one_step_decision_value | 48.1% | 100% | 2.33 | 8,711 | 14,146 | 0 | 0 | 0 |
| entropy_first | 49.1% | 100% | 2.53 | 9,513 | 14,832 | 0 | 0 | 0 |
| current_gate | 49.1% | 100% | 3.11 | 11,628 | 14,832 | 0 | 0 | 0 |

用户级 bootstrap 95% 区间（覆盖率）：two_step [0.55, 0.83]、one_step [0.33, 0.65]、random [0.55, 0.83]、exact [0.55, 0.83]。

## 5. 结论（分层声明，不夸大）

1. **机制验证通过**：复合域上两步与一步显著分化（覆盖率 70.4% vs 48.1%），且两步与有限小状态精确参照持平（70.4%、2.59 vs 2.62 题）——A1 诊断的"死参数"问题已解决，配对互补结构确为两步机制的有效载体。
2. **相对基线**：两步相对随机/固定顺序覆盖率无显著差异（CI 重叠）但成本更低（2.59 vs 2.90 题）；相对一步覆盖率显著更高但成本略高（2.59 vs 2.33 题）。
3. **预注册方向未完全达成**：成本方向（two_step ≤ one_step）未达成，如实保留；覆盖率不降达成。**本报告不宣称"两步更省时"或"优于随机"**；可宣称的能力仅为"在不损失覆盖率的前提下接近精确参照、在覆盖率显著提升时成本增幅有限"。
4. **安全门达成**：0 错误判定、0 过期证书使用、0 重复签发（修复后）。
5. **边界**：合成数据、声明先验（prior_only、sample_count=0）、模拟延迟非真人报告时间；无真人校准前不得宣称真人负担降低。

## 6. 未决与下一步

- 响应模型仍为 `prior_only`：A3 要求知情同意后的事件日志估计 + 敏感性区间，未实施；
- 规划器修复（asked 支持）已进入后端代码，需纳入 P2 回归；
- v2 案例的负担端点尚未接入真实服务层（当前为纯合成域模拟）；P2 将其映射到 `policy/acquisition` 服务与证据债务卡。
