# AI 模块实现说明（v1.0 Competition Edition）

HealthMate 把“语言智能”和“视觉推理”拆开，避免所有能力都堆进一个远程大模型调用。

## 云端语言智能

FastAPI 的 AI Gateway 负责 DeepSeek 文本能力：健康问答、计划生成、周报总结和可确认动作。用户可以保存自己的 DeepSeek API Key；Key 加密后写入数据库，查询接口不会返回明文。

核心原则：

- Provider 与业务逻辑分离；
- 结构化动作必须经过 Pydantic/业务校验；
- 会修改用户数据的 AI 建议必须进入 Action Registry，由用户确认后执行；
- Safety 规则在调用模型之外独立存在，不把安全完全交给提示词。

## 本地多模态

图片/视频视觉推理进入根目录 `ai-worker/`，通过持久化 `AIJob` 队列与云端解耦。这样云托管只负责稳定 API 和任务编排，本机负责视觉推理（MediaPipe CPU，本地VLM由模型服务选择GPU）。

## 继续迭代方向

- Prompt Registry 与版本化；
- token/成本统计、按用户限流；
- AIJob 端到端 tracing；
- 权威营养数据库，把“识别食物”和“查营养值”进一步解耦；
- 按实际显存和延迟优化本机Worker，保持本轮指定部署架构。

缺Key或上游失败时对话明确503；周报/计划/目标解释明确rules-fallback/degraded，不使用DemoProvider。默认非流式，/stream缓冲后输出NDJSON。真实DeepSeek调用未验收；源码/本地测试结果见docs/VERIFICATION.md。
