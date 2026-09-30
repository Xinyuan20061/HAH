# 真实评测执行骨架（motion-unified-v1）

> 状态：**命令骨架，待实现确认**。以下命令在统一动作链（P0-B/P0-C）与 `MotionWorkerResultV1`
> 契约落地后才可真实执行；当前只固定"在哪跑、产物落哪、不打真实云"的流程。

## 1. 前置条件（发布门禁）

- [ ] 统一任务创建/轮询/恢复/取消/重试接口可用；422 回归 fixture 通过。
- [ ] Worker 声明 `motion_unified_v1` 能力；视频只解码一次。
- [ ] 冻结标注完成，`manifest_frozen.json` SHA-256 封存。
- [ ] DeepSeek 与腾讯云各完成一次最小真实连通验证并停测。

## 2. 执行流程（草案命令）

在 `ai-worker` 目录（沿用既有 evaluate_motion_dataset.py 风格；新脚本名待 P0-B 落地时确定）：

```powershell
# 臂 A/B/C/D：同一冻结集、逐臂产出 predictions
.\.venv\Scripts\python.exe scripts\evaluate_motion_unified.py `
  --manifest ..\benchmark-results\motion-unified-v1\manifest_frozen.json `
  --arm fused `
  --output-dir ..\benchmark-results\motion-unified-v1
```

> （待实现确认）脚本名、参数以 P0-B 实际落点为准；本基架不预先写死未实现的 CLI。

## 3. 产物

- 每臂一份 `predictions_<arm>.jsonl`：逐样本原始结果或失败原因（不删失败样本）。
- `report.json`：机器可读，字段见 `metrics_recipe.md`。
- `report.md`：答辩可读，必备 8 章节见 `metrics_recipe.md` 第 7 节。
- `cost_log.csv`：由 `cost_tracking_template.csv` 回填，并与 `provider_invocations` 对账。
- `human_review.csv`：双人盲评回收，与 `human_review_template.csv` 同列。

## 4. 红线重申

- 不执行真实模型消融前，本目录不产出任何"新方案准确率"数字。
- 历史 `motion-v1`（41.67%）、`motion-v2-kinetics`（同集 24 段）结果目录**只读不动**。
- 真实云调用只用于"各一次连通验证"；离线评测全用脱敏 fixture。
