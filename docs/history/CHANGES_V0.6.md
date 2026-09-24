# HealthMate v0.6 — Health Intelligence Loop

本版本完成四条主线：**HealthTimeline 完整化 → Health Agent 工具层 → 动态目标引擎 → 周报事实层**。

## 已完成

- 新增统一 `daily_facts()` 时间序列投影，缺失数据不再当 0。
- Timeline 增加旧数据 backfill 与更多业务事件。
- Health Agent 通过只读工具层获取结构化健康事实。
- 计划类 Agent 响应可预览，用户确认后才加入本周计划。
- 新增持久化 `health_agent_runs / health_plans / health_plan_items`。
- 新增动态目标规则引擎、调整审计表与“应用建议”流程。
- DeepSeek 只解释目标调整，不参与数值计算。
- 周报新增当前周期/上一周期事实、覆盖率、目标完成率、数据置信度。
- 周报事实持久化为 versioned snapshot，AI 只解释程序事实。
- 目标页、聊天页、计划页、周报页接入上述能力。
- 新增旧 `create_all()` 开发数据库接管 Alembic 的安全脚本。

## v0.7 建议

- 把 AI 识餐改为“识别 → 用户校正 → 保存校正样本 → 置信度校准”。
- 引入 Agent Action Registry，把饮食记录、训练完成、提醒等动作统一成可审计 Action。
- 增加量化评测面板：姿态检测、AI 延迟、周报耗时、Agent 计划接受率、目标调整接受率。
- 加入数据导出/删除、敏感日志脱敏与高风险输入 Safety Guard 完整策略。
