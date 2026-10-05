# HEALTHMATE P4 全旅程完整性（代码层面）交付记录 — 2026-10-05

> 范围：`docs/HEALTHMATE_COMPETITIVENESS_AND_MINIPROGRAM_COMPLETENESS_DEVELOPMENT_SPEC_2026-10-05.md` §6 页面级任务、§9 测试矩阵中**不依赖真机即可完成**的代码与测试部分。真机验收（Android/iOS 录像、问题单）需用户设备配合，单独列出。

## 0. 本轮完成事实（一句话）

28 页三件套齐全、无孤儿页面；补齐 evaluation 成绩元数据链路（迁移 0040 + 模型 + API + 前端声明）、state 页三类"下一步"映射并去除内部 ID、共用错误恢复合同缺口修复（goals / report / records-exercise / trends / privacy 五页），新增 4 个测试文件/扩展，Node 全量与后端 pytest 全量均回归通过。

## 1. 28 页责任清单矩阵（实证）

脚本：`miniprogram/scripts/audit_pages.js` → `miniprogram/results/page-audit-matrix.json`（逐页 js/wxml/wxss 存在性、行数、入口引用、API 调用、测试引用）。

| 核验项 | 结果 |
| --- | --- |
| app.json 注册页数 | 28 |
| 三件套齐全页数 | 28 / 28 |
| 无任何导航入口的孤儿页 | 0 |
| 全部页入口引用数 | ≥1（tabBar 例外：home / chat 为入口宿主） |
| 写接口页面数 | 18（逐页列于矩阵 writePages） |
| 无专属测试引用的页面 | 8 → 本轮后已由通用契约测试覆盖（见 §4） |

## 2. §6 页面级任务：本轮补齐项

### 2.1 evaluation 成绩声明（§6：对外成绩必须带数据集/日期/版本/证据等级）

- 迁移 `0040_evaluation_benchmark_provenance.py`：`evaluation_benchmarks` 加 `dataset`(120) / `evidence_level`(40) / `retriever_version`(60)，无默认值问题（server_default=""，MySQL 兼容）。
- `models.py::EvaluationBenchmark` 加同 3 列。
- `BenchmarkIn` 加 3 字段；`evaluation.py::dashboard` 返回 `dataset / evidence_level / retriever_version / measured_at`。
- 前端 `pages/evaluation/index.{js,wxml}`：`EVIDENCE_LEVEL_LABEL`（合成回放/真机/本地测试/真人试用/未标注证据等级），benchmark 行展示 `数据集 X · 证据等级 · 版本 Y · 日期`；缺省一律显示「未标注」，**开发中指标不得表述为上线能力**。

### 2.2 state 页三类"下一步"映射（§6：首页/状态页）

- `nextStep()`：由后端状态推导（策略 kind、周期 status、决策建议），三类聚合：
  - `needs_repair` →「需修复」→ CTA 直达 `/pages/policy/episode/index`
  - `actionable` →「可行动」→ CTA 直达 chat（建议确认）或 episode（周期记录）
  - `waiting` →「等待暂缓」→ CTA 直达 episode / policy overview
- 数据层不再保留 `episode_id`（wxml 本就不渲染；JS 一并移除，杜绝未来误渲染）。
- CTA 全部使用页面路由（不显示内部 ID）。

### 2.3 共用错误恢复合同（§6：加载失败/写失败恢复）

探针：`scripts/probe_recovery_contract.js` → `results/recovery-contract-probe.json`。实证发现并修复 5 处真实缺口：

| 页面 | 原缺陷 | 修复 |
| --- | --- | --- |
| goals | 加载失败仅 toast、无 error 态/重试 | error 分支 + `retry()` |
| report | 加载失败静默（console.warn）、页面永远 "--" | error 分支 + `retry()` |
| records/exercise | 加载失败静默、空态会误导 | error 分支 + `retry()` |
| trends | 加载失败静默 | error 分支 + `retry()` |
| settings/privacy | error 分支无重试按钮 | 补 retry + JS `retry()` |

合理例外（不强行加状态，实证记录于测试白名单）：media（重新分析 reanalyze）、chat（重新发送）、plan（重新生成）、workout / exercise-detail（纯静态或参数页、无网络依赖）、checkin / profile-edit / settings-ai（表单/按钮页、写失败 toast 反馈）。

## 3. §9 测试矩阵：本轮新增

| 测试文件 | 断言 |
| --- | --- |
| `tests/evaluationProvenance.test.js`（新） | 成绩必带 dataset/日期/版本/证据等级；缺省显示未标注；不表述为上线能力 |
| `tests/statePagePresentation.test.js`（扩） | 三类映射齐全、CTA 用路由、内部 ID 不出现、映射由后端状态驱动 |
| `tests/recoveryContract.test.js`（新） | 28 页三件套齐全；数据加载页必有 error 分支；error 分支必有重试或白名单替代；写页必有防连点或反馈 |

## 4. 回归结果

| 套件 | 命令 | 结果 |
| --- | --- | --- |
| 小程序 Node（30 文件） | `node --test tests/*.test.js` | 见本轮回归日志（全部 pass，无失败） |
| 后端 pytest 全量 | `cd backend; pytest tests -q` | 见本轮回归日志（基线 538 + 0040 相关无回归） |

## 5. 如实声明：未完成 / 需人工配合项

1. **真机验收**（P4 退出门槛）：Android/iOS 各录一条 28 页旅程（八条状态检查：加载/空态/部分数据/弱网/权限不足/版本过时/失败重试/成功反馈），填问题单 —— 需要用户设备 + 微信开发者工具配合。
2. **审计矩阵补充人工目检**：脚本只能证明"存在"，不能证明"视觉正确"（对齐、层级、文案长度），需真机截图复核。
3. **后端 0040 迁移生产应用**：必须按 §8 流程（SQLite 演练 → 备份 MySQL 演练 → 生产），记入 P5。
4. 双人盲评（P3 收尾）、知识条目扩容 —— 需人工。

## 6. 关键产物路径

- 审计矩阵：`miniprogram/results/page-audit-matrix.json`、`page-audit-summary.json`
- 恢复合同探针：`miniprogram/results/recovery-contract-probe.json`
- 新增脚本：`miniprogram/scripts/audit_pages.js`、`probe_recovery_contract.js`
- 迁移：`backend/migrations/versions/0040_evaluation_benchmark_provenance.py`
- 修改：`backend/app/models/models.py`、`backend/app/api/v1/evaluation.py`、`backend/app/services/evaluation.py`；`miniprogram/pages/{evaluation,state,goals,report,trends,records/exercise,settings/privacy}/index.{js,wxml}`
