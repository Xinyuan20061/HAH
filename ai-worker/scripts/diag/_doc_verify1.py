# -*- coding: utf-8 -*-
"""Stage-1 verification doc: add stage-1 record + refreshed baseline hashes."""
import io

PATH = r"D:\学习资料\计算机应用大赛\health-assistant\docs\VERIFICATION.md"

s = io.open(PATH, encoding="utf-8").read()

STAGE1 = '''
## 2026-09-27 阶段 1 验收（行动账本闭环）

|检查|真实结果|范围与限制|
|---|---|---|
|决策读模型|新增 `GET /api/v1/agent/decisions/{decision_id}`：同一 `decision_id` 串起 signal → evidence → proposal → confirmed action → progress → review；decision_id 由服务端生成（`dec-<hex>`），按用户隔离校验，他人/未知 id 一律 404（不可枚举）|复用 `AgentMicroExperiment / AgentActionAudit / EvaluationEvent / HealthTimelineEvent`；新增迁移 `0022_agent_decision_id`（列 + 回填 + 唯一索引）|
|结论级证据标注|`/agent/insights` 每条提醒新增 `evidence_contract`：facts（真实记录）、data_coverage（observed/expected 均返回、缺失不按零）、knowledge_ids（记录类为空并明示）、limitations、evidence_type（record_observation / general_guidance）|前端以「依据与数据覆盖 / 我们还不知道」呈现，不暴露技术名词；无知识支撑时按一般提示降级|
|小程序行动时间线|insights 页每条提醒新增「历史行动」：方案、状态、日期与复盘结论（支持/尚未支持/记录不足/已停止）|测试数据来自真实实验行，不生成合成记录冒充用户数据|
|完整与不足案例|后端 5 项决策账本契约测试：完整复盘（supports_hypothesis）、记录不足（insufficient_data + limitations）、用户隔离 404、insights 证据契约、时间线关联|小程序的展示适配测试同步增加 2 项（依据归一化 + 时间线渲染）|
|自动回归|后端 161/161、小程序 43/43 全绿；Worker 99/99 不变；迁移检查 head=`0022_agent_decision_id` 且 legacy 0003 保留、重复升级不变|三套回归与迁移检查命令、退出码、日期见下方冻结基线|

### 冻结回归基线（2026-09-27 · 阶段 1 后）

阶段 1 完成后刷新基线；阶段 2 起任何改动不得使以下检查变红。测试数随执行日期与代码变化更新，不写永久固定数字。

|检查|命令|结果|代码哈希（内容聚合 SHA-256）|
|---|---|---:|---|
|后端 / SQLite|`backend\\.venv\\Scripts\\python.exe -m pytest -q`|161 passed，退出 0|backend/app `4348BF662C6A529CFCAC5F1B95281BA4AC9B6F3C4A244F73438A91E84AE3FFBD`（90 文件）|
|后端迁移|`backend\\.venv\\Scripts\\python.exe scripts\\verify_migrations.py`|PASS：legacy 0003 保留、head=`0022_agent_decision_id`、重复升级不变，退出 0|backend/migrations `34EDB89881DECA0E44C3385C9B8FCFF77FE0E93BF3D98DCA1B92800B29D20478`（25 文件）|
|Worker|`ai-worker\\.venv\\Scripts\\python.exe -m pytest -q`|99 passed，退出 0|ai-worker/healthmate_worker `3912BBA3A35733606801FC291558B11C7C428936A94D6B6FD958BB7B97D7E08A`（33 文件）|
|小程序|`node --test`（miniprogram/）|43 passed，退出 0|miniprogram `823DBE9C2F03316B1D02A76524CFCB539926992E1C8CCFC882500BB3A9D91293`（109 文件）|

## 2026-09-24 主动健康体验升级验收'''

OLD_HEAD = "### 冻结回归基线（2026-09-27）\n\n阶段 0 完成后冻结版本稳定基线，阶段 1 起任何改动不得使以下检查变红；测试数随执行日期与代码变化更新，不写永久固定数字。\n\n|检查|命令|结果|代码哈希（内容聚合 SHA-256）|\n|---|---|---:|---|\n|后端 / SQLite|`backend\\.venv\\Scripts\\python.exe -m pytest -q`|156 passed，退出 0|backend/app `B77760641DAD903384A73C0037FDD6BC50A83D9766D7A2BF173245DD523A5AEF`（90 文件）|\n|后端迁移|`backend\\.venv\\Scripts\\python.exe scripts\\verify_migrations.py`|PASS：legacy 0003 保留、head=`0021_empty_default_nickname`、重复升级不变，退出 0|backend/migrations `0E2FE5506CF5F2E2562F3D0C25A646B3F1A8D197AE2667F49F62B405FB4DA2F3`（24 文件）|\n|Worker|`ai-worker\\.venv\\Scripts\\python.exe -m pytest -q`|99 passed，退出 0|ai-worker/healthmate_worker `3912BBA3A35733606801FC291558B11C7C428936A94D6B6FD958BB7B97D7E08A`（33 文件）|\n|小程序|`node --test`（miniprogram/）|41 passed，退出 0|miniprogram `AECDF4DAB301508F25CB4E064C29A6EE14EAEED41873DD9F4B2BE0346F462652`（109 文件）|\n\n## 2026-09-24 主动健康体验升级验收"

assert s.count(OLD_HEAD) == 1
s = s.replace(OLD_HEAD, STAGE1)
io.open(PATH, "w", encoding="utf-8", newline="").write(s)
print("OK VERIFICATION.md updated")
