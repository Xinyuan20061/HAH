# HealthMate Health Agent Harness

## 产品定位

HealthMate 是面向个人健康管理的 Agent 工作台，而不是以表单和页面为中心的传统记录工具。
用户看到的是由小健、小康和小管家组成的协作界面；背后的 Health Agent Harness 负责把健康上下文、
模型、工具、权限、安全规则、执行轨迹和语音能力连接成一个持续运行的系统。

这三个概念不混用：

- **Workspace**：小程序中的人机协作界面；
- **Agent**：具有任务边界和表达风格的执行角色；
- **Harness**：让 Agent 能安全读取上下文、调用工具、观察结果并继续行动的运行内核。

## 目录边界

```text
backend/app/harness/
├── contracts.py   # Agent、Tool、Observation 的稳定接口
├── personas.py    # 小健、小康、小管家的人格与能力声明
├── registry.py    # 工具 allow-list、权限与确认门
├── tools.py       # 现有健康服务到 Harness Tool 的适配器
├── kernel.py      # 子 Agent 内部使用的有界 ReAct loop
├── collaboration.py # Router → Workers → Decision 多 Agent 编排内核
└── voice.py       # 可替换的 STT/TTS provider adapter
```

`app.harness` 不直接拥有健康业务数据。饮食、运动、计划、知识库等领域能力仍位于 `app.services`，
通过工具适配器接入。这使小程序、未来 Web 客户端和模型供应商都可以替换，而不需要重写健康业务。

## 多 Agent 执行模型

```text
用户消息
  → 输入安全检查
  → 用户人格层（小健 / 小康 / 小管家）
  → Router Agent 选择 1–3 个必要的领域子 Agent
  → Planner / Coach / Nutritionist / Recovery / Records / General
  → 子 Agent 在各自最小权限 Tool Registry 中执行有界 ReAct
  → Decision Agent 汇总候选结论、消除冲突并形成唯一答复
  → 输出安全复核与计划 Guardrail
  → 保存可审计的工具轨迹
```

Router 只负责拆解和派发，不直接回答健康问题。领域子 Agent 只能看到其职责需要的只读工具；
proposal-only 写工具只对 Decision Agent 可见。Decision Agent 是唯一能形成最终用户答复的模型角色，
但仍不能越过用户确认门执行写操作。

内核不请求、返回或保存模型的私有思维过程。`trace.multi_agent` 记录路由、子 Agent 状态和决策状态，
`trace.tool_calls` 只包含工具名、执行状态与结果摘要。工具名必须来自 Registry；未知工具直接拒绝，
每个子 Agent 的 ReAct 循环最多三步。

### 领域子 Agent

| 子 Agent | 职责 | 工具边界 |
| --- | --- | --- |
| Planner | 一周计划与执行编排 | 上下文、知识、训练资源、Action 列表 |
| Coach | 动作、训练负荷与运动建议 | 上下文、知识、训练资源 |
| Nutritionist | 摄入、餐次与营养建议 | 上下文、知识 |
| Recovery | 睡眠、疲劳、压力与恢复 | 上下文、知识 |
| Records | 趋势解释与数据缺口 | 仅健康上下文 |
| General | 跨领域一般生活方式问题 | 上下文、知识 |

## Agent Profiles

| Agent | 主要职责 | 默认表达 | 语音 |
| --- | --- | --- | --- |
| 小健 | 训练、自律与动作改进 | 直接、有一点毒舌，但不羞辱或制造身体焦虑 | 输入 + 输出 |
| 小康 | 饮食、睡眠、恢复与长期节律 | 温柔、朋友化、短回答 | 输入 + 输出 |
| 小管家 | 汇总记录、解释依据、组织计划 | 克制、结构化，适合文字和计划卡片 | 文字优先 |

三者共享模型网关、安全规则、健康上下文和 Tool Registry。前端的 Agent 选择不是简单改名字，
而是向 Harness 传递 `agent_id`，由内核选择对应的 system contract、回答长度和能力范围。

## Tool Registry

当前内核注册四类只读工具：

- `health.context.read`
- `health.knowledge.search`
- `health.resources.search`
- `harness.actions.list`

原 Action Registry 中的计划写入、动态目标、识餐确认、微实验和隐私操作也会注册进 Harness manifest。
这些工具标记为 `action + proposal_only`：LLM 只能提出调用意图，内核会停在
`approval_required`，真实写入仍由已有的用户确认接口完成。

## API

- `GET /api/v1/harness/manifest`：返回内核版本、Agent、工具与安全策略；
- `POST /api/v1/agent/respond`：兼容原接口，新增 `agent_id` 与 `channel`；
- `POST /api/v1/agent/respond/stream`：安全复核后逐 token 展示；
- `POST /api/v1/harness/voice/transcribe`：语音转文字；
- `POST /api/v1/harness/voice/synthesize`：文字转语音。

## 语音配置

语音层使用 OpenAI-compatible STT/TTS 接口，和文本模型解耦。用户可在小程序「设置 → AI 与语音」
保存个人语音网关；密钥与文字模型 Key 一样加密存储且不回显。个人语音配置优先，未启用时回退到
服务端环境变量。两者都未配置时，文字 Harness 仍完整可用，语音接口返回明确的不可用状态。

```env
VOICE_API_KEY=
VOICE_API_BASE_URL=
VOICE_STT_MODEL=whisper-1
VOICE_TTS_MODEL=tts-1
VOICE_TTS_VOICE=alloy
```

个人配置保存在 `user_ai_configs` 的独立语音字段中。STT/TTS 模型和声音可以分别选择，
`POST /api/v1/users/me/ai-config/voice-test` 只返回连通状态，不把合成音频或上游错误细节暴露给前端。

小程序使用 `RecorderManager` 录制最长 30 秒的单声道 MP3；小健和小康可按住说话，并可播放回答。

## 后续演进边界

新增功能时优先实现为领域服务，再注册为 Tool；不要让页面直接拼模型提示词，也不要让模型直接访问数据库。
需要产生副作用的 Tool 必须声明风险等级、确认要求和审计方式。多 Agent 协作应共享同一 Registry，
不复制健康读取逻辑或安全规则。
