# HealthMate 比赛量化评测协议（v0.7）

本文件用于避免答辩时把运行指标、离线准确率和主观体验混为一谈。

## A. 运行期指标（系统自动采集）

这些指标来自真实请求/任务事件：

- AI 识餐成功率
- AI 识餐响应 P50
- 识餐校正率
- 识餐最终入库率
- Health Agent 响应 P50 / P95
- 周报 AI 生成 P50
- 视频任务成功率
- 视频处理 P50
- MediaPipe 关键点有效帧率
- 计划任务完成率
- Agent Action 执行/拦截数
- Safety Guard 拦截数

用途：证明系统在真实工程链路中是否稳定、是否可用。

## B. 离线 Benchmark（必须有标注集）

### 1. 动作次数检测

建议测试集至少 30–50 条短视频，覆盖：

- 不同身高/体型
- 正面/侧面
- 明暗光线
- 不同速度
- 标准动作 + 常见错误动作

人工标注真实 reps。

指标：

- `MAE_reps = mean(abs(pred_reps - true_reps))`
- Exact Count Accuracy
- ±1 Count Accuracy

### 2. 动作错误检测

对“下蹲不足”“躯干前倾”等每类错误单独标注。

指标：Precision / Recall / F1，禁止只报 accuracy（类别不平衡时会误导）。

### 3. 关键点稳定性

运行面板中的“关键点有效帧率”只表示可检测覆盖率，不等价于稳定性。

若要正式评价稳定性，应录制固定姿态或受控重复动作，计算：

- landmark dropout rate
- normalized coordinate jitter
- joint-angle temporal jitter（只在同一静态阶段比较）

### 4. 食物识别

将“菜名识别”和“营养估算”分开评价。

菜名：Top-1 / Top-k accuracy 或 Macro-F1。

营养估算：对有称重/营养真值的样本计算 Calories / Protein / Carbs / Fat MAE、MAPE。

用户 correction rate 只能说明“用户改了多少”，不能替代准确率。

### 5. Safety Guard

建立高风险 + 普通健康问题混合测试集。

至少分别统计：

- 高风险召回率（Recall）
- 普通问题误拦截率（False Positive Rate）
- 各类别：emergency / self_harm / medication / diagnosis / extreme_diet

## C. 面板录入

先按 [`benchmark/README.md`](../benchmark/README.md) 准备JSONL标注并运行 `ai-worker/scripts/evaluate_motion_dataset.py`。工具会保存原始预测、机器可读报告、答辩版Markdown，以及标注/预测文件SHA-256和运行环境。示例文件仅用于格式与命令验证，不是实验结果。

离线测试完成后，通过：

`POST /api/v1/evaluation/benchmarks`

写入真实测量结果。小程序量化评测页会显示每项的 sample size 与 notes。

示例：

```json
{
  "task_type": "motion_error_detection",
  "metric_name": "squat_depth_f1",
  "value": 86.4,
  "unit": "%",
  "sample_size": 120,
  "notes": "固定测试集 v1；侧面机位；人工双人复核标注"
}
```

## D. 答辩表达

推荐：

> 在线面板展示工程运行指标；算法准确率来自固定标注测试集，两类指标不混用。没有完成标注评测的指标显示暂无样本，不用演示数据代替实验结果。
