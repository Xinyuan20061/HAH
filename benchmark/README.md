# HealthMate 动作算法标注与评测

这里存放固定测试集的标注清单，不存放包含人物身份信息的原始视频。视频应置于未提交版本库的受控目录，并取得参与者授权。

## 1. 标注规则

每条视频由两名标注者独立标注，分歧由第三人复核。必须先冻结标注，再运行算法，不能看完算法结果后修改真值。

JSONL每行一条样本：

- `sample_id`：去标识化且唯一；
- `video_path`：相对标注文件的本地视频路径；
- `exercise_type`：`squat`、`pushup`、`lunge`、`leg_abduction`、`arm_abduction` 或 `arm_vw`；
- `should_evaluate`：机位、遮挡和完整入镜条件是否足以评价；
- `reps`：仅可评价样本必填；
- `errors`：真实存在的规则错误代码；
- `events`：人工标注的关键事件类型与秒数；
- `quality_score`：可选，0—100人工动作质量分；用于相关性，不当作医学评分；
- `strata`：体型、机位、光照、速度和遮挡等分层标签。

错误代码采用算法当前可输出的集合：`depth_insufficient`、`trunk_lean`、`body_alignment`、`back_leg_depth`。不存在的错误标为空数组，不能省略负样本。

## 2. 运行真实视频

在 `ai-worker` 目录执行：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_motion_dataset.py `
  --annotations ..\benchmark\motion_annotations.jsonl `
  --run-videos `
  --auto-recognition `
  --output-dir ..\benchmark-results\motion-v1
```

会生成：

- `predictions.jsonl`：逐样本原始算法结果或失败原因；
- `report.json`：机器可读指标；
- `report.md`：答辩可读报告。

也可以对已有预测重新评分：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_motion_dataset.py `
  --annotations ..\benchmark\motion_annotations.jsonl `
  --predictions ..\benchmark-results\motion-v1\predictions.jsonl `
  --output-dir ..\benchmark-results\motion-v1-rescored
```

## 3. 指标口径

- 次数MAE只在系统接受评价的样本上计算，同时必须报告全部应评价样本的完全正确率；拒绝和处理失败计为错误。
- 单独报告应评价样本覆盖率和低质量视频拒绝召回率，防止系统通过大量拒绝获得漂亮MAE。
- 错误提示报告Micro Precision、Recall、F1和逐错误指标，不使用容易被类别不平衡误导的Accuracy。
- 关键事件按类型在允许时间误差内一对一匹配，报告F1和匹配事件时间MAE。
- 动作质量只报告与人工评分的Spearman相关性，不宣称它是损伤风险概率。
- 自动模式报告覆盖率、全样本准确率、已接受样本准确率、逐类F1、Macro-F1和包含拒识列的混淆矩阵；拒识在全样本准确率和F1中计错。
- 不带 `--auto-recognition` 的手动动作评测不会生成自动分类结论，报告会明确标注该指标没有样本。

示例文件只有格式示范，不能作为比赛准确率或正式样本量。

2026-09-23 固定测试集由 `prepare_rehab24_benchmark.py` 从 REHAB24-6 官方分段生成：受试者 7/8/9、六类各 20 条、共 120 条。真实规则基线报告位于 [`benchmark-results/motion-v1`](../benchmark-results/motion-v1/report.md)，报告 SHA-256 为 `7bee63cc716b2d2835ec952fa0cc6017516fd95a45a9bf1b74e49fc0d9c4b575`。全样本准确率 41.67%、Macro-F1 54.15%，只能作为工程基线。

仓库同时提供 `motion_predictions.example.jsonl`，仅用于验证评测命令和报告格式，数值不是算法实验结果。

## 4. 组合动作语义数据管线

`semantic_manifest.example.jsonl` 展示开放标签清单格式，额外要求：

- `subject_id`：受试者去标识化编号；同一受试者不得跨训练、验证和测试集。
- `instance_id`：同一次动作实例编号；多视角文件必须保持在同一划分。
- `source_dataset`、`license_record`、`consent_status`：数据来源、条款复核日期和允许用途。
- `action_text`、`action_family`：自然语言动作名和动作族。
- `is_unknown_action`：该样本是否用于未见动作拒识测试。
- `movement_patterns`、`observed_regions`、`target_body_parts`：多标签语义；观察区域与训练效果不得混为一谈。
- `video_path`/`pose_path`：只能是数据根目录内的相对POSIX路径。

生成确定性的受试者级划分：

```powershell
cd ai-worker
.\.venv\Scripts\python.exe scripts\prepare_semantic_dataset.py `
  ..\benchmark\semantic_manifest.example.jsonl `
  private-manifests\semantic-split-v1.jsonl `
  --seed healthmate-competition-v1 `
  --unseen-family unseen_family
```

评估动作模式、观察区域和未知动作拒识：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_semantic_dataset.py `
  ..\benchmark\semantic_manifest.example.jsonl `
  ..\benchmark\semantic_predictions.example.jsonl
```

示例仍然只验证格式，不能作为模型指标。真实报告必须登记样本量、清单哈希、模型版本和许可记录。

骨骼提取、ST-GCN-lite训练、模型卡和Worker加载流程见 [`docs/SKELETON_MODEL_TRAINING.md`](../docs/SKELETON_MODEL_TRAINING.md)。

## 5. 识餐证据链评测

`food_annotations.example.jsonl` 与 `food_predictions.example.jsonl` 仅用于验证格式。真实标注集应由营养相关专业人员复核菜名、可见食材和整份热量参考值，并记录光照、容器、遮挡等分层；训练图与测试图必须按来源去重。

```powershell
cd ai-worker
.\.venv\Scripts\python.exe scripts\evaluate_food_nutrition.py `
  --dataset-root D:\HealthMateData\food `
  --run-vlm `
  --output-dir ..\benchmark-results\food-v1
```

报告同时给出全样本菜名准确率、完成覆盖率、逐项食材 Precision/Recall/F1、热量 MAE/MAPE、区间覆盖率、明细汇总一致率、置信度 Brier 分数与分层结果。未完成样本仍计入全样本准确率分母；禁止只展示成功案例或用示例结果宣传真实精度。

2026-09-23 的 42 图真实基线报告位于 [`benchmark-results/food-v1/report.json`](../benchmark-results/food-v1/report.json)，报告 SHA-256 为 `fd3f52362fd85336cd055b0c6cf95932b3f4e0c16e8308b7d43222c70a8606ae`。热量 MAPE 为 72.70%，已触发预训练 Food-101 菜名候选层的实现条件；改进版尚未重跑，因此不能用本报告证明改进有效。
