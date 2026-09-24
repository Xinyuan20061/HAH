# v0.7 Trust / Safety / Evaluation Architecture

## 总体原则

1. **事实先于生成**：健康指标由程序算，LLM 解释。
2. **建议不等于执行**：所有可写动作进入 Action Registry。
3. **用户确认优先**：计划、目标、识餐最终结果均在人在环确认后写库。
4. **安全前置**：高风险医疗输入在调用 LLM 之前先经过 Safety Guard。
5. **最小敏感日志**：运行日志只记录 request id、method、path、耗时；不记录正文和 Authorization。
6. **没有样本就说没有样本**：评测面板禁止将未知准确率伪装为 0 或演示数字。

## 识餐闭环数据模型

`FoodAnalysisSession` 保存模型原始结果；`FoodAnalysisCorrection` 保存每次人工校正及变化字段；最终 `DietRecord.vision_analysis_id` 连接到分析会话。

这允许计算：

- correction rate
- 常被修正字段
- 模型置信度与人工修改之间关系
- 不同菜品/烹饪方式的误差分析

后续可在得到正式标注集后新增 MAE / macro-F1 / top-k dish accuracy，但 v0.7 不伪造这些指标。

## Action Registry

Registry 是 LLM 和数据库写操作之间的权限边界，而不是 prompt 里的口头约束。

高风险等级：

- low：保存确认后的识餐、加入计划
- medium：动态目标修改、数据导出
- critical：账户和数据永久删除

写操作保存 `AgentActionAudit`：action、risk、confirmation、status、脱敏 input/output。账户永久删除属于例外：执行后连审计表自身也按“删除全部个人数据”的用户请求一起清理。

## Safety Guard

目前为高精度规则层，作用是“明显高风险先拦截”，不声称替代医学分类器。

规则之后仍有第二层 LLM system prompt：禁止诊断、处方、药物调整、极端减重。后续若要做更强的安全分类器，应单独建立标注集并评测召回率和误拦截率。

## 数据删除边界

删除账户会清理：

- user/profile/goal
- check-in/diet/exercise
- HealthTimeline
- Agent runs/plans/actions
- weekly snapshots/dynamic goal adjustments
- vision analysis/corrections
- evaluation/safety audit
- chats
- media metadata
- 对象存储 `u{user_id}/` 前缀（包括视频分析派生关键帧）

## API Key 边界

加密材料优先使用 `CREDENTIALS_ENCRYPTION_KEY`；未设置时兼容回退到 `SECRET_KEY`。正式部署建议使用独立高熵值并放在部署 Secret 管理中。若更换加密 key，旧用户 API Key 将无法解密，应要求重新输入，而不是尝试回显或恢复明文。

## 答辩建议

不要说“我们保证医疗安全”。更严谨的说法：

> HealthMate 明确限定为日常健康管理工具。系统在 LLM 前设置规则型高风险拦截，在 LLM 后把真实写操作收敛到 Action Registry，并通过用户确认、审计和数据删除能力降低产品风险。该机制不是医疗诊断系统，也不替代医生。
