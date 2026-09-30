# 统一动作链评测协议（motion-unified-v1）

> 状态：**协议与基架，待实现确认**。本目录只定义评测协议、数据格式、指标口径与脚本骨架；
> 真实消融、独立集指标、成本数据均在统一动作链（P0-A~P0-D）实现完成后，由后续阶段在
> `benchmark-results/motion-unified-v1/` 下执行并回填。本目录不存放任何已完成的新方案指标。
>
> 对应规格：`docs/HEALTHMATE_HARNESS_MOTION_VOICE_ENGINEERING_SPEC_2026-09-29.md`
> 第 9 节（质量评估）、第 10 节（P1 质量与答辩）、第 11 节（开发顺序）、第 8.4 节（延展能力）。

## 0. 红线（写报告前必读）

1. **REHAB24-6 120 段（受试者 7/8/9，六类各 20 段）已用于历史开发**，其 41.67% 全样本准确率、
   54.15% Macro-F1 只能作为历史工程基线（见 `benchmark-results/motion-v1/report.md`），
   **不得再作为统一动作链的最终独立测试集**，也不得在新报告中当作新方案效果。
2. 先冻结标注、再跑算法；不得看完算法结果后改真值。
3. 拒识（abstained）与处理失败**计入全样本准确率分母并计错**；禁止只报"已接受样本准确率"。
4. 候选分值 `candidate_score` 是模型自身空间的 softmax 分值，**不是准确率**；
   对外文案只允许"融合识别与可追踪点评"，禁止"高准确率 / 医疗级精度 / 候选分值=真实准确率"。
5. 外部语音（腾讯云 ASR/TTS）与 DeepSeek 真实调用遵循"各一次最小连通验证后停测"，
   回归一律使用录制并脱敏的响应 fixture；不得反复消耗免费额度做离线评测。

## 1. 本目录文件清单与用途

| 文件 | 用途 | 对应规格第 9 节维度 |
| --- | --- | --- |
| `freeze_split_protocol.md` | 独立受试者冻结分组方案（新未见集 + 未知/非锻炼组） | 六类识别 / 400 类识别 |
| `unified_annotations.example.jsonl` | 新冻结集标注格式（六类 + 产品目标子集 + 未知类 + 非锻炼） | 六类识别 / 400 类识别 / 数值评分 |
| `ablation_matrix.json` | 四臂消融矩阵（本地 / 本地+Kinetics / 本地+DeepSeek / 三者融合） | DeepSeek 增益 |
| `metrics_recipe.md` | 全部指标口径、95% 置信区间、拒识矩阵、分机位、幻觉率定义 | 全部识别维度 |
| `human_review_template.csv` | 关键帧/点评双人盲评模板（在 agent 盲评模板风格上扩展） | 关键帧/点评 |
| `voice_verify_checklist.md` | SDK stub 与错误注入全覆盖清单 + 连通后停测检查清单 | 语音 |
| `safety_privacy_checklist.md` | 脱敏检验 / 越权 404 / 同意撤回 / 删除导出 / 健康禁忌抽检 | 安全与隐私 |
| `cost_tracking_template.csv` | 每任务成本、月预算、422 比率、拒识率、降级率、ASR/TTS 成功率 | 成本与失败案例 |
| `expected_run_layout.md` | 后续真实评测执行命令与产物布局（基架，命令待实现确认） | P1 质量与答辩 |

## 2. 统一产物布局（真实评测落地后）

真实评测不在本目录写结果；结果统一落到：

```
benchmark-results/motion-unified-v1/
  manifest_frozen.json            # 冻结清单（含 SHA-256、受试者划分、seed）——基架已留位
  predictions_local.jsonl         # 臂 A：仅本地姿态/规则
  predictions_kinetics.jsonl       # 臂 B：本地 + Kinetics-400 候选层
  predictions_deepseek.jsonl       # 臂 C：本地 + DeepSeek 视觉复核（无 Kinetics 融合）
  predictions_fused.jsonl          # 臂 D：三者融合（上线决策门）
  report.json                     # 机器可读指标（schema 见 metrics_recipe.md）
  report.md                       # 答辩可读报告（模板见 metrics_recipe.md）
  cost_log.csv                    # 由 cost_tracking_template.csv 回填
  human_review.csv                # 双人盲评回收
```

> （待实现确认）上述预测文件的具体字段以 P0-B/P0-C 落地的 `MotionWorkerResultV1`
> 与统一结果契约（规格 4.4）为准；本目录只冻结"每臂一份 predictions、一份 report"的骨架。

## 3. 与既有 benchmark 风格的一致性

- 标注为 JSONL，每行一样本；视频路径为数据根目录内相对 POSIX 路径，原视频不入库、不打包。
- 真实报告必须登记：样本量、清单 SHA-256、pipeline_version、各模型版本、标注者、冻结日期。
- 示例文件（`.example.jsonl`）只验证格式与命令，**数值不是实验结果**。
