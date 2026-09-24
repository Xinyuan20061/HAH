# HealthMate v0.6 — Health Intelligence Loop

## 1. 统一健康数据中心

`backend/app/services/health_data.py` 是新的权威时间序列投影层。饮食、运动、健康打卡与计划状态仍保存在各自规范化业务表中，但所有 Dashboard、趋势、周报、动态目标与 Agent 都通过同一套 `daily_facts()` 读取。

关键原则：**缺失值保持 `null`，而不是静默变成 0。** 展示层如果需要 0，可以调用 `display_daily()`；统计层必须保留“没有记录”和“真实为 0”的区别。

`HealthTimelineEvent` 继续承担审计型事件流：新增/删除饮食与运动、打卡、目标修改、媒体上传、姿态分析完成、Agent 计划确认与计划完成都会写入 Timeline。老数据可调用：

```http
POST /api/v1/timeline/backfill
```

幂等补齐基础饮食、运动、打卡事件。

## 2. Health Agent 工具层

Agent 不直接写 SQL，也不让 LLM 自称“查过数据库”。`services/agent/tools.py` 提供只读工具包：

- profile
- today
- goals
- recent_7d
- weekly_facts
- recent_events

`POST /api/v1/agent/respond` 将这些结构化事实与用户请求交给 DeepSeek。计划类请求返回 **待确认计划**，不会自动持久化。

用户点击“加入本周计划”后才调用：

```http
POST /api/v1/agent/runs/{run_id}/apply-plan
```

服务器从已保存的 AgentRun 读取原计划，避免客户端任意篡改写入内容。之后计划任务可以逐项完成，并回写 Timeline。

这形成：`理解 → 读取事实 → 规划 → 用户确认 → 执行 → 记录 → 周报反馈 → 再规划`。

## 3. 动态目标引擎

`services/dynamic_goals.py` 只允许规则算法修改数值。当前自动建议范围：

- 饮水
- 每日运动
- 步数

睡眠、热量、蛋白质不根据“完成率”机械上调，避免不合理优化。规则要求至少 5 个有效记录日；达标率低于约 35% 时小幅降低，稳定达到约 85% 时小幅提高，单次幅度约 5–10%，并受安全上下界约束。

DeepSeek 只通过 `/health/goals/dynamic/explain` 把规则结果解释成人话，**无权修改 recommended_target**。

## 4. 周报事实层

`services/weekly_facts.py` 先计算：

- 当前周期有效记录覆盖率
- 有记录日期平均值
- 上一周期对比
- 目标达标率
- Streak
- 数据置信度
- 程序规则生成的 highlights

事实快照保存到 `weekly_report_snapshots`，版本字段为 `facts_version`。AI 周报只能解释这份 JSON，并被明确告知“未记录日期不是 0”。

## 5. 数据迁移

生产环境：

```bash
alembic upgrade head
```

如果旧开发数据库最初由 `Base.metadata.create_all()` 创建，可能缺少 `alembic_version`。先运行：

```bash
python scripts/adopt_dev_database.py
alembic upgrade head
```

脚本只根据已存在的关键表判断可以安全 stamp 到哪个历史 revision，不创建/删除业务表；无法确认时会拒绝操作。

## 6. 比赛答辩可强调

1. **事实层与解释层分离**：程序算数，LLM 解释。
2. **Human-in-the-loop**：Agent 计划和动态目标都不能静默修改用户数据。
3. **Missingness-aware**：没有记录不等于 0。
4. **可审计**：关键动作写入 HealthTimeline/AgentRun/GoalAdjustment。
5. **可扩展**：后续可把睡眠设备、微信运动、体重秤等数据接入同一 daily facts 投影。
