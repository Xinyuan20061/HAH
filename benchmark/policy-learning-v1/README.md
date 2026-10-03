# Policy Learning v1 合成评测

本目录对应个人策略学习文档的 WP5。它只评估证据门控与决策排序，不评估模型识别准确率。

## 固定案例

`cases.json` 覆盖：

- 缺失不计失败；
- 执行不足不更新支持端点；
- 达标、未达标、模糊带；
- 混杂/上下文变化标记不可比；
- 停止事件阻断支持更新；
- 硬约束候选过滤；
- 全局重置后的学习隔离（由后端集成测试覆盖）。

## 运行

```text
python benchmark/policy-learning-v1/run_benchmark.py
```

输出机器可读 JSON，包括 B0 门控正确性、B1 缺失鲁棒性、B2 结果判定、B3 安全过滤和 B4 可复现性。脚本只使用标准库和生产纯算法模块，不访问数据库、不联网。

## 对照与消融

`--ablation naive_missing_zero` 将未知执行机会当作未完成，用于展示错误基线；`--ablation no_hard_filter` 允许硬约束候选进入排序，用于验证安全门控的必要性。默认运行是文档规定的 evidence-gated policy。
