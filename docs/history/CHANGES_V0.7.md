# HealthMate v0.7 — Trust, Actions & Evaluation

本版本不继续堆叠动画，而是补齐比赛答辩最容易被追问的四条产品链路：**AI 识餐校正闭环、Agent Action Registry、量化评测、隐私与医疗安全边界**。

## 1. AI 识餐可校正闭环

旧流程：`图片 → AI JSON → 直接保存`。

v0.7：

`图片 → AI 原始估算 → FoodAnalysisSession → 用户校正 → Correction Feedback → 二次确认 → DietRecord → HealthTimeline → 周报 / Agent / 动态目标`

- AI 原始结果和用户校正结果分开保存，不覆盖原始预测。
- 可校正：菜名、份量、估算重量、烹饪方式、热量、蛋白质、碳水、脂肪、纤维。
- 每次校正记录 changed fields，形成后续离线评测/优化可使用的 feedback 数据。
- 最终入库动作必须经过 Action Registry 和用户确认。
- `DietRecord` 保留 `vision_analysis_id`，可追溯来源。

## 2. Agent Action Registry

新增统一动作注册与审计：

- `plan.apply`
- `goal.adjustment.apply`
- `diet.ai.finalize`
- `privacy.export`
- `privacy.account.delete`

每个动作声明：风险等级、是否需要确认、允许来源。LLM 不能因为“生成了计划”就静默写库。

`GET /api/v1/agent/actions/registry` 可直接用于答辩演示。

## 3. 比赛量化评测面板

新增 `/evaluation/dashboard` 与小程序“比赛量化评测面板”。

运行期自动采集：

- 识餐成功率、校正率、入库率、识餐响应 P50
- Health Agent 响应 P50 / P95
- 周报 AI 生成 P50
- 视频任务成功率、视频处理 P50
- 用户计划任务完成率
- Action 执行/拦截数
- 高风险输入拦截数

动作检测准确率、关键点稳定性、识餐准确率等**必须依赖固定标注测试集**，通过 Benchmark 接口录入。系统对没有样本的指标显示“暂无样本”，不会把缺失数据画成 0，更不会伪造比赛分数。

## 4. 隐私、安全和医疗边界

### 用户数据权利

- `GET /privacy/export/preview`：查看导出范围。
- `POST /privacy/export`：二次确认后生成 ZIP/JSON 个人数据包。
- `DELETE /privacy/account`：二次确认后删除账户数据库记录和 `u{user_id}/` 对象存储前缀，连动作关键帧等派生文件一起清理。

导出包不包含 API Key 明文、服务器密钥或媒体二进制。

### API Key

- 只在后端加密保存。
- 前端只看到 `has_api_key + 脱敏 hint`。
- 可使用独立 `CREDENTIALS_ENCRYPTION_KEY`，避免 JWT Secret 与凭证加密完全耦合。
- 生产环境 `SECRET_KEY` 长度不足 32 字符时拒绝启动。
- 自定义 AI Base URL 在生产环境要求 HTTPS，并拒绝 localhost / 私网字面量地址，降低 API Key 被 SSRF 转发的风险。
- 连接失败不回显上游原始异常，避免把密钥或内部网络信息带回客户端。

### 医疗 Safety Guard

在 Chat 和 Health Agent 入口均执行规则层：

- 急症信号 → 中止普通健康建议，优先专业急救/就医路径。
- 自伤风险 → 中止训练/饮食计划。
- 药物停药/加药/换药/剂量 → 拒绝给出调整指令。
- “帮我确诊” → 转为症状整理和就医沟通支持。
- 极端节食、催吐、泻药减重 → 不生成方案，转为温和一般健康建议。

安全审计默认保存 **规则类别 + 消息 SHA-256；如显式开启审计片段才保存脱敏片段**，请求日志不记录 body、Authorization、Cookie、API Key 或原始聊天文本。

## 5. UI 策略

没有继续增加无目的动画。新增的是统一的产品状态语言：

- loading
- empty / insufficient data
- error + retry
- success feedback
- destructive confirmation
- AI → 校正 → 确认 → 入库的过程可视化

`app.wxss` 新增通用 state primitives；识餐页新增 4 步流程；隐私页和评测页均使用同一状态体系。

## 数据库

新增 Alembic：`0006_product_safety`。

```bash
cd backend
alembic upgrade head
```

如果是历史 `create_all()` 开发库：

```bash
python scripts/adopt_dev_database.py
alembic upgrade head
```
