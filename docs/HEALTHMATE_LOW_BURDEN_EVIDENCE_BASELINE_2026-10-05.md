# 低负担证据闭环：实现基线与审计快照

日期：2026-10-05（Asia/Shanghai）  
对象：`HEALTHMATE_LUNA_LOW_BURDEN_EVIDENCE_DEVELOPMENT_SPEC_2026-10-05.md`

## 仓库基线

本次接续的是已经有未提交实现的工作树，不是干净检出。因此这里只记录能够核验的 Git 基线和恢复时工作树状态，不伪造“实现前测试通过数”或声称工作树干净。

| 仓库 | 路径 | HEAD | 恢复审计时状态 |
| --- | --- | --- | --- |
| 项目根仓库 | `health-assistant/` | `81c12b6fe32b397d8e1fd6d040dbcb99ea4ae475` | 54 项 modified/untracked 状态记录 |
| 后端嵌套仓库 | `health-assistant/backend/` | `a7c14bf5c257bcbad6d47260b5d862b5c3e36110` | 29 项 modified/untracked 状态记录 |

两组状态分别由各自 Git 仓库统计，内容有重叠，不能相加。报告与离线基准的 `git_commit` 使用项目根仓库 HEAD；后端实现同时记录其嵌套仓库 HEAD。

## 迁移基线

- 取证迁移：`0038_low_burden_evidence_acquisition`，父版本 `0037_policy_decision_idempotency`。
- 实际演练：独立临时 SQLite 上升级到 head，检查新增取证表和 observation revision，降级到 `0037`，再升级并复查；脚本报告通过。
- 同步脚本的全模型比较仍发现 80 项与本次迁移无关的既有 metadata 差异。该结果不是全库 `alembic check` 通过。

## 实际回归基线

实现期间在工作树上完成以下验证；它们是当前代码的回归结果，不是和一个被测量过的干净旧版本做的前后性能比较：

- 后端全量：`526 passed`；新增耗时记账断言后的取证专项：`17 passed`。
- 小程序全量 Node 回归：`147 passed, 0 failed`。
- 参考算法：`11 passed`；请求契约参考：`8 passed`；客户端幂等/新操作身份/新鲜读取检查通过。
- 隔离迁移演练通过；Python compileall、修改 JS 的语法检查及 `git diff --check` 通过。
- 离线合成回放：seed `20261005`，数据哈希 `627d16ace3fcd467f424731a43126b831da0ae3e3b3d7b36d7703197a4c10847`，配置哈希 `5f915d4ba9ef8f853e9c5d45c91bc8138da7ccc4fd236d40f167c4faf530690e`。指标和负向结果见实现报告及逐案例轨迹。

## 未作为本地基线的事项

没有目标 MySQL 测试实例、微信开发者工具/真机验收录像、真实用户试用数据或临床效果数据。本地 SQLite/API、Node UI 合同测试和合成回放不替代这些证据。迁移演练使用临时数据库，不触碰用户开发库或生产数据。
