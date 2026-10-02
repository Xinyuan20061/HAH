HISTORICAL: 本文是特定轮次的交付/验证快照，其中引用的迁移 head 是当时的事实，不是当前 head。当前 head 以 `alembic heads` 命令结果为准。

# HealthMate 自动动作识别 v2

## 目标与边界

本版本让用户上传视频后无需预先选择动作，系统可在深蹲、俯卧撑、弓步蹲、腿外展、手臂侧平举和手臂 V/W 之间自动匹配。当前实现是基于 MediaPipe 二维骨骼序列的可解释规则特征匹配，不是训练模型；内部候选数值是匹配分，不是概率，也不代表损伤风险。主界面只展示动作结论，候选细节收在折叠区。

当证据不足时，系统必须拒绝猜测。拒识任务正常完成，但不生成动作评分、不写入30日运动画像；用户可保留同一素材，切换为手动动作后重新分析。

## 处理链路

```text
上传视频
  → MediaPipe逐帧关键点
  → 同帧生成六类候选特征序列
  → 计算膝/肘/肩活动范围、腿部外展、躯干角、身体直线度、双膝不对称
  → 候选匹配分排序
  → 阈值与候选差值判断
      ├─ 通过：进入对应动作Analyzer、计数、评分和关键事件
      └─ 拒识：返回原因，提示手动选择，不写入评分
```

识别方法标识固定为 `rule_feature_matching_v1`。手动选择标记为 `user_selected`，便于数据库和评测区分来源。

## 拒识条件

满足任意条件即拒识：

- 六类候选中任一类有效关键点样本少于4帧；
- 膝或肘等主要关节最大活动范围小于18度；
- 最高匹配分低于55；
- 第一名与第二名匹配分差小于8。

阈值是 v2 工程基线，已在冻结的 120 段 REHAB24-6 测试集上报告，但没有使用测试集反向调参。调整阈值时必须使用独立验证集，并保留报告与配置版本，不能只挑选演示视频。

## 返回结构

识别成功：

```json
{
  "recognition": {
    "mode": "auto",
    "requested_type": "auto",
    "selected_type": "squat",
    "accepted": true,
    "confidence": 0.84,
    "margin": 21.5,
    "method": "rule_feature_matching_v1",
    "candidates": [
      {"exercise_type": "squat", "match_score": 86.0, "rank": 1},
      {"exercise_type": "lunge", "match_score": 64.5, "rank": 2}
    ]
  }
}
```

拒识时 `selected_type` 为 `null`、`accepted` 为 `false`，`pose.available` 和 `score.available` 同时为 `false`。后端会拒绝自动任务缺少识别记录、识别类型与姿态类型不一致、拒识却返回评分等不可信结果。

## 数据持久化

迁移 `0011_motion_recognition` 为 `motion_scores` 增加：

- `requested_exercise_type`：用户请求的 `auto` 或手动动作；
- `recognition_method`：规则识别或用户选择；
- `recognition_confidence`：本次识别置信度。

`exercise_type` 始终保存最终实际评分的动作。拒识不创建 `motion_scores` 行，因此不会污染历史趋势。

## 评测

在 `ai-worker` 目录运行真实视频自动识别：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_motion_dataset.py `
  --annotations ..\benchmark\motion_annotations.jsonl `
  --run-videos `
  --auto-recognition `
  --output-dir ..\benchmark-results\motion-v1
```

报告包含自动识别覆盖率、全样本准确率、已接受样本准确率、逐类F1、Macro-F1和带拒识列的混淆矩阵。全样本准确率与F1会把拒识计错，覆盖率用于揭示系统是否通过大量拒识回避困难样本。

2026-09-23 的正式固定集为六类各 20 段，共 120 段；全样本准确率 41.67%、已接受准确率 64.94%、Macro-F1 54.15%、覆盖率 64.17%。报告 SHA-256 为 `7bee63cc716b2d2835ec952fa0cc6017516fd95a45a9bf1b74e49fc0d9c4b575`。这些数字只适用于登记测试集，说明当前规则仍需改进。

## 验收清单

- 默认自动模式能处理六类动作并在阈值不足时拒识；
- 静止、遮挡、动作幅度不足和候选接近的视频会拒识；
- 手动重试复用同一云端素材，但创建不同动作参数的任务；
- 识别成功只按最终动作写入画像，拒识不写入评分；
- 前端主视图不展示概率式数值，折叠详情可查看候选匹配分；
- 正式答辩只引用真实冻结数据集产生的报告；
- 生产 MySQL 在发布前升级到当前唯一 head `0022_agent_decision_id` 并重跑全量、增量和重复迁移验收；其中 0017 仍是六动作基线登记迁移。
