# Evidence Acquisition v2 — 冻结评测（可复现）

## 目录

```
evidence-acquisition-v2/
├── domain.py            # 复合端点域：执行 7 槽精确证明 + 负担 baseline/follow-up 配对
├── cases_generator.py   # 9 类可审计案例生成（train 252 / test 108，按用户拆分）
├── baselines_v2.py      # 7 臂同预算基线（random/one_step/two_step/exact/fixed_order/entropy/current_gate）
├── simulate.py          # 决策模拟器（先验错配、重复签发、陈旧度修正均含回归护栏）
├── run_benchmark.py     # 预注册方向 + 安全门 + bootstrap CI + 冻结产物落盘
├── configs.json         # 冻结配置（seed=20261005）
└── results/seed-20261005/
    ├── report.json          # 主报告（策略×覆盖/成本/acc/wrong/dup/stale + CI）
    ├── traces.jsonl         # 全周期决策轨迹
    ├── cases.jsonl          # 冻结用例集
    └── failure_cases.jsonl  # 含 deferred 的失败用例
```

## 复现

```bash
cd benchmark/evidence-acquisition-v2
python run_benchmark.py
```

产物覆盖 `results/seed-20261005/`（相同输入与 seed 应产出相同哈希）。

## 冻结事实（2026-10-05）

- dataset_hash=`dc7123eff615fd469449645e0cb4920996b590cf10894dccef100379bd7584ad`
- config_hash=`4dbffa1aed23076a924984ed53a8776d94c5d01650f3fb30aafb4aa6562fa957`
- test 集：two_step cov=0.704 / costQ=2.59 / costMs=9677；one_step 0.481/2.33/8711；random 0.713/2.90/10782；exact 0.704/2.62/9754；CI two_step [0.55,0.83]、one_step [0.33,0.65]；acc=1.0、wrong=0、stale=0、dup=0。

## 声明边界（如实分层）

- **机制验证通过**：两步显著优于一步且贴近精确参照。
- **不宣称**「两步更省时 / 优于随机」：预注册成本方向未达成（two_step costMs 高于 one_step），按预注册诚实保留。
- 响应模型仍为 prior_only；决策模拟在冻结域内，不代表线上运行时。
