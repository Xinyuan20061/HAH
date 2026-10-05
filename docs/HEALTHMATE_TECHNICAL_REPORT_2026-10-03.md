# HealthMate 技术报告：从项目主轴到技术全貌

日期：2026-10-03  
范围：当前工作区的小程序、后端、AI Worker、数据层、评测与部署方案。当前工作区包含未提交改动，本报告以这些文件的现状为准。  
证据口径：基于源码和现有文档的静态梳理；“有实现”表示仓库内存在对应代码与接口，不表示已在生产环境运行或通过当前版本的完整验收。

## 摘要

HealthMate 是一个微信小程序形态的个人健康管理系统。它把健康记录、可追溯证据、行动候选、用户确认、执行反馈和复查结果接成一个可撤销的闭环。项目把这条主轴称为**证据门控的个人行动协议**（Evidence-Gated Personal Action Protocol，EGPAP）。

系统由四个主要运行部分组成：微信小程序负责交互与云文件上传；FastAPI 后端负责身份、业务规则、Harness、多 Agent、数据与审计；MySQL 保存健康记录、任务和证据链；可选的本地 AI Worker 负责饮食图片和动作视频推理。文本模型、语音服务、云存储和本地模型均通过适配层接入。后端决定某条证据能支持什么结论，模型不能直接写入健康数据。

当前代码覆盖了这些核心对象和用户路径，但发布验收尚未完成。尤其是当前版本回归、真实视频与食物评测、MySQL 迁移、微信真机、CloudBase 文件生命周期、Worker 真实推理和运维恢复仍缺可复现证据。技术报告因此分别描述**设计目标、源码实现和已验证事实**，避免把三者混为一谈。

## 1. 项目的“主心骨”：证据驱动的个人行动闭环

从用户角度，系统要回答六个连续问题：我现在是什么状态？为什么建议我做这件事？我是否同意？我实际做了没有？结果有没有足够证据支持？下一步应继续、补证、调整还是停止？

```text
本人健康记录 / 视频 / 餐食 / 自报
       ↓ 归属、版本、质量和时间窗口校验
健康状态快照 + 安全约束 + 数据缺口
       ↓ 生成、筛选和解释候选行动
Harness / 多 Agent / 计划求解 / 策略编译
       ↓ 用户确认后才允许有副作用的动作
执行报告 + 来源可核验的结果观测
       ↓ 覆盖率、可比性、混杂因素和安全门控
周期复查 → 条件化个人记忆 → 下次决策
       ↑ 源数据更正、删除或撤销时使依赖结论失效
```

该闭环的关键约束是：**来源先于结论，观察先于推断，确认先于写入，更正后旧结论必须失效**。动作识别和识餐是证据输入；多 Agent 是解释与规划层；Harness 是权限与执行边界；个人策略模块负责把一次行动变成可复查的周期。单个周期只能形成个人的描述性观察，不能自动证明健康效果或因果关系。

主规格见 [全项目能力审计与开发规范](HEALTHMATE_FIRST_PRIZE_FULL_AUDIT_AND_DEVELOPMENT_SPEC_2026-10-03.md)；代码现状及验收边界见 [当前收口状态](FINAL_RELEASE_READINESS_2026-10-03.md)。前者是实施要求，不能直接充当已完成证明。

## 2. 系统结构与请求流

| 层 | 责任 | 主要实现 |
| --- | --- | --- |
| 微信小程序 | 登录、健康记录、聊天、计划、能力授权、策略周期、视频与餐食交互 | `miniprogram/pages/`、`miniprogram/utils/` |
| API 与领域服务 | 权限校验、业务规则、结构化健康状态、作业调度、策略裁决 | `backend/app/api/v1/`、`backend/app/services/` |
| Harness 与多 Agent | 人格、路由、只读工具、受控行动提案、安全复核、轨迹 | `backend/app/harness/`、`backend/app/services/agent/` |
| 数据与媒体 | 用户及健康数据、证据链、审计；云文件 ID 与临时 URL | `backend/app/models/`、`backend/app/services/storage.py` |
| AI Worker | 领取推理任务，下载媒体，运行视觉处理，回传结构化结果 | `ai-worker/worker.py`、`ai-worker/healthmate_worker/` |
| 外部能力 | 微信登录及云托管、CloudBase、文本/视觉模型、语音服务 | 各网关及配置适配层 |

正常业务请求由小程序的 `wx.cloud.callContainer` 进入 FastAPI；若配置公网 HTTPS 域名，聊天可使用分块响应。小程序上传原媒体到 CloudBase，再把 `fileID` 和有时效的下载地址注册到后端。Worker 通过公网接口主动领取任务，无需让云端主动连接用户电脑。后端存数据库记录和媒体引用，生产配置禁止把原媒体持久放在容器本地目录。

后端 HTTP 入口是 `backend/app/main.py`，路由集中注册于 `backend/app/api/v1/router.py`。HTTP 中间件生成请求 ID、限流并规范错误；`/health/live` 检查进程存活，`/health/ready` 检查数据库连通及迁移版本。页面请求统一经过 `miniprogram/utils/request.js`，其职责包括 Bearer Token、重新登录、有限重试、统一错误对象和可选只读缓存。

## 3. 技术栈与运行依赖

| 领域 | 仓库所用技术 | 在项目中的用途 |
| --- | --- | --- |
| 客户端 | 原生微信小程序 JavaScript、WXML、WXSS、微信云 API | 页面、组件、录音、视频、云上传和云托管调用 |
| Web API | Python 3.12、FastAPI 0.116.1、Uvicorn 0.35.0 | HTTP 接口、参数校验、异步调用和服务启动 |
| 数据库 | SQLAlchemy 2.0.43、Alembic 1.16.5、PyMySQL 1.1.2 | ORM、版本化迁移、生产 MySQL 访问 |
| 结构与配置 | Pydantic 2.11.7、pydantic-settings 2.10.1 | 请求/结果契约与环境配置校验 |
| 外部请求与加密 | httpx 0.28.1、cryptography 46.0.4 | 模型/微信 API 调用与用户密钥加密 |
| 媒体与视觉 | Pillow、OpenCV contrib、MediaPipe、可选 SlowFast/Kinetics 与视觉模型 | 图像预处理、姿态估计、动作候选和证据帧 |
| 本地模型与检索 | 可选 ONNX Runtime、中文 embedding 权重、本地文本模型 | 语义检索或文本模型降级；模型权重不随仓库交付 |
| 对象存储 | CloudBase 主链路；本地存储和 S3 适配 | 原媒体、预览图及临时访问链接 |
| 部署 | 后端 Dockerfile、微信云托管；本地 Docker Compose/MySQL 8.4 | 生产服务与本地集成环境 |
| 验证 | pytest、Node 内建测试、审计脚本和 benchmark 目录 | 后端/Worker/小程序回归、协议与评测 |

固定依赖版本来自 `backend/requirements.txt` 与 `ai-worker/requirements.txt`。ONNX 权重、外部模型、微信云资源、模型与语音账号属于**可选或外部配置**，不能仅凭仓库中有适配代码就视为已部署。

## 4. 微信小程序：用户操作与状态呈现

小程序以首页、助手、记录、训练、计划、个人中心、健康状态与设置为主要入口；策略周期另有总览、协议、执行、复查、历史五页。页面配置由 `miniprogram/app.json` 声明，网络与错误处理在 `miniprogram/utils/request.js`，云媒体上传与刷新在 `miniprogram/utils/cloudMedia.js`。

前端主要承担三类工作。第一，采集用户明确输入：健康档案、饮食和运动记录、计划完成情况、动作纠错与策略执行自报。第二，把后端返回的来源、缺口、可用能力和等待确认的动作显示清楚。第三，完成微信环境特有的媒体操作，包括 `wx.cloud.uploadFile`、临时 URL 刷新、播放和删除。客户端可选择证据来源，但不能自行决定一条来源最终用于信念更新的数值。

小程序的策略页面当前只开放“缩短单次训练时长”这一完整用户模板；服务端模板注册表另含记录复杂度、任务时间两个定义，不能据此说三个模板都已形成等价的用户体验。能力中心提供内置能力查看、范围授权、目标与表达偏好设置、预览、暂停/恢复和使用审计。预览用于说明将使用哪些数据与流程，不应理解成真实模型效果验证。

前端数据不是最终事实源。缓存用于提高只读可用性；写入、归属、版本冲突和裁决仍以后端为准。聊天在无法分块时降级为一次性返回；其“流式”展示也不能被当成上游模型逐 token 实时流的证明。

## 5. 后端领域层：健康记录、状态与计划

FastAPI 路由按身份、用户、记录、健康状态、识餐、动作、Harness、Agent、策略、隐私和 Worker 分组。领域服务位于 `backend/app/services/`；Harness 通过工具适配器调用领域能力，不直接拥有健康业务表。

健康状态层把饮食、运动、打卡、目标、计划和动作分析转为带版本与来源的特征、约束和快照。每个特征声明计算函数、输入来源和公式版本；状态快照保存覆盖情况、缺口及可追溯引用。记录被编辑或删除时，`record_changed` 在同一数据库事务内定位受影响来源、使状态与个人策略证据失效，并可通过 Outbox 安排后续重算。由此避免新记录与旧推断同时提交。

计划层先检查硬约束与可用动作，再用按天展开的候选和 beam search 选择一周训练草案，最后由确定性校验器复查；还提供较温和的备选计划和只读 `simulate`。模型可以生成解释或候选，但不能越过计划约束。恢复优先、样本不足或质量较低时应降低建议强度；应用计划仍需用户确认。

主要源码：`backend/app/services/health_state/`、`backend/app/services/health_state/invalidation.py`、`backend/app/services/planning/`、`backend/app/api/v1/records.py`。

## 6. Harness 与多 Agent：把模型限制在可信工具内

产品层有小健、小康、小管家三位不同表达风格的助手；运行层共享同一个 Harness Kernel。请求大体经过输入安全判断、人格装配、Router 分派、领域子 Agent 工作、Decision 汇总、输出复核与审计。领域角色包含 Planner、Coach、Nutritionist、Recovery、Records 和 General。Router 选择必要角色，Decision 形成唯一面向用户的答复。

子 Agent 只能看到本角色允许的工具；ReAct 循环有步数和模型预算限制。工具注册表拒绝未知工具。只读工具可返回本人上下文、审核知识、训练资源、动作证据、策略证据、计划模拟等；有副作用的 Action 是 `proposal_only`，模型只生成提案，实际写入由用户确认接口执行。轨迹保存路由、工具名、状态及摘要，不保存模型私有推理文本。

能力系统目前有四个审核内置 manifest：`health_state`、`personal_policy`、`motion_evidence`、`plan_outcome`。每个 manifest 声明工具、数据范围、目标、风险和可配置项。安装与授权记录按用户保存；工具调用和部分直连 API 共用能力门控，暂停后仍允许受控查看历史、停止或清理。现阶段仅支持审核过的内置适配器和预设配置项，没有任意第三方代码执行机制。

文本模型网关支持服务端或用户配置的 DeepSeek 兼容接口；按配置存在本地模型降级路径。模型不可用时，健康记录和确定性业务仍可工作，某些回答只能退回规则或明确不可用。模型生成内容经过安全复核；外部动作点评、关键帧文本与知识片段都作为待引用数据，不可提升为系统指令。

主要源码：`backend/app/harness/`、`backend/app/services/agent/`、`backend/app/services/ai/gateway.py`、`backend/app/api/v1/harness.py`。

## 7. 个人策略：证据编译、周期裁决和可撤销记忆

个人策略模块将一次建议分成候选、协议、确认、执行、观测、裁决、记忆与下一次决策。编译器根据服务端模板、健康状态、安全约束和上下文生成带哈希的冻结协议。周期启动通过确认提案，并重核协议、状态和能力授权；每日执行报告与结果观测分开保存。复查时检查窗口是否关闭、执行是否充分、数据覆盖率、前后是否可比、是否有混杂或不良事件，再给出支持、证据不足、不可比或停止等结果。

证据源注册表在服务端重新读取本人记录，核对归属、来源版本和观察窗口，并由真实字段派生指标。客户端提供来源选择与人工确认，不能把自填数值冒充已核验记录。源记录更正或删除会触发失效；Outbox 异步重建受影响的信念，并在重建完成前阻止使用旧结论。

候选排序使用三个不同维度的 Beta 信念：执行可能性、目标支持和证据可得性；对小样本作回缩，扣除执行负担，经用户同意后才加入有限的信息增益奖励。算法先排除跨用户、不安全、能力不可用和用户排除的候选；最高分不足时输出补证或等待。当前是**确定性启发式排序**，`selection_propensity=1` 不构成随机化试验，也不支持直接声称因果增益。

数据模型包含策略单元、周期、执行机会、报告、来源引用、裁决、个人信念、决策、Outbox 和控制记录。历史结论可标记撤回，用户可停止周期并重置记忆。

主要源码：`backend/app/services/policy_learning/`、`backend/app/api/v1/policy.py`、`miniprogram/pages/policy/`。

## 8. 动作视频：异步推理、证据帧与能力门控

动作链路是“云文件注册 → 创建分析任务 → Worker 领取 → 下载/解码 → 本地姿态与动作候选 → 可选云端复核 → 后端决策与反馈 → 时间轴/回看”。Worker 使用 OpenCV 做视频解码，MediaPipe 提取姿态与二维启发式指标；可选 SlowFast/Kinetics-400 给出视频动作候选。六类规则分析器可计算相应计数与质量线索，动作目录与开放类目由后端决策层统一解释。

统一分析只解码一次，形成有真实时间戳、帧 ID、姿态证据、候选类别和展示预览的结构化结果。后端把识别结果分成已识别、可能、未知；视觉复核能提供补充，但不能把不支持的类别强行映射到六类规则分析器。若最终动作类别改变，旧类别的计数和评分必须丢弃。时间轴、视频回看和个人预览是用户可核查证据的一部分；向外部视觉服务发送帧另受同意与脱敏模式控制。

规则分与经真实逐动作评测放行的评分是两种不同级别。能力门控会依据实际评测状态决定是否向用户展示验证过的分数；目前缺少冻结视频集、双人标注和逐动作指标，因此不能宣称动作质量评分已经被真实世界验证。

主要源码：`ai-worker/healthmate_worker/processors/motion_unified.py`、`backend/app/services/motion/`、`backend/app/api/v1/media.py`、`miniprogram/pages/media/`。

## 9. 拍照识餐：视觉草稿、人工校正与确定性营养计算

餐食图片先由视觉服务识别可见食材与候选份量，再作为**草稿**交给用户。用户可以回答澄清问题、修正食材和份量，最终确认后才形成正式饮食记录。图像处理包括格式校验、转正、压缩、大小限制和模型返回结构校验；云端视觉不可用时可走配置允许的 Worker 路径或明确降级。

营养数值的最终来源是食物参考表与用户确认的质量/份量，由确定性计算器计算热量及营养素。模型自由文本中的营养数字不直接写入最终结果。未映射食物不借用“最相近”条目的营养数值；烹调油和糖作为显式调整；份量不确定时输出区间与假设。个人份量先验有最小样本要求。

仓库有识餐评测脚本与历史基线，但当前版本仍缺冻结真实数据集、人工复核、误差与覆盖率报告。餐食结果应表述为估算，不能当作医疗或营养测量设备输出。

主要源码：`backend/app/services/vision/`、`backend/app/services/food/`、`backend/app/api/v1/vision.py`、`backend/app/api/v1/food.py`、`ai-worker/healthmate_worker/processors/`。

## 10. 知识、语音与用户侧解释

知识库保存经过审核的标题、机构、来源、章节、发布时间与正文。当前**实际源码**使用词法相关性选取候选，再以语义相似度重排；若词法池为空，还存在较高阈值的语义准入。语义编码优先使用仓库外的中文 ONNX 模型，模型或运行时缺失时退回确定性的字符 n-gram 哈希向量。这个实现与较早的 `docs/ARCHITECTURE.md` 中“纯词法 RAG”描述不一致，本文以 `backend/app/services/rag/service.py` 和 `embeddings.py` 为准。引用来源由后端结构化返回，避免让模型自行编造来源 URL。

语音层与文字模型解耦：提供腾讯云 ASR/TTS、OpenAI 兼容语音网关及关闭状态。小程序负责录音和播放；后端控制音频长度、TTS 分段、配置与预算。缺少语音配置时文字对话仍能使用。用户自带网关密钥在服务端加密存储，不回显给小程序；腾讯云服务密钥只从后端环境读取。

用户侧解释主要由状态快照、知识引用、动作证据 ID、策略协议与裁决理由组成。健康提醒应呈现已观察到的事实、数据覆盖和未知部分；没有足够来源时降级为一般提示或请求补充记录。

主要源码：`backend/app/services/rag/`、`backend/app/harness/voice.py`、`backend/app/services/agent/orchestrator.py`。

## 11. 数据、安全与隐私边界

数据库以 `users` 为归属根，包含健康档案、饮食/运动/打卡/计划记录、聊天与行动提案、媒体与 AI 作业、动作证据、健康状态快照、策略周期、能力安装与审计。SQLAlchemy 模型集中在 `backend/app/models/models.py`，结构变更通过 `backend/migrations/versions/` 中的 Alembic revision 表达。当前文件已延伸至 `0037_policy_decision_idempotency`，实际数据库是否到达该 head 尚未在本轮核验。

生产登录通过微信一次性 `wx.login` code 换取 openid，再签发带有效期的 HS256 JWT。公网 Worker 架构下不能信任可伪造的云托管身份头；默认关闭基于该头的登录。业务查询按用户 ID 约束，Worker 使用独立令牌。用户模型密钥由后端加密；请求日志抑制可能包含签名 URL 与微信密钥的底层 httpx 输出。API 提供统一错误、请求 ID、限流、响应安全头和敏感内容脱敏。

媒体生产主链路使用 CloudBase。后端主要保存 `fileID` 与短期可用 URL；过期地址由小程序刷新。隐私功能支持导出与账户数据删除，但前端提交的云文件删除结果不能代替平台侧的独立删除证明；孤立文件对账及服务端管理删除仍是待补环节。动作预览有默认短期保留和清理逻辑，需通过真实云链路验证。

主要源码：`backend/app/api/v1/auth.py`、`backend/app/core/security.py`、`backend/app/services/privacy.py`、`backend/app/services/storage.py`、`backend/app/core/config.py`。

## 12. 作业可靠性、部署与运行观察

AI 作业状态主要为 `queued → processing → done/failed`。Worker 主动轮询，领取使用数据库条件更新与租约令牌；推理过程中续租并上报进度，超时后可重领，失败按是否可重试进行退避。媒体临时 URL 缺失或过期时进入等待刷新状态；源地址刷新后继续原任务。动作结果后处理另有阶段任务与后台消费循环，策略来源失效通过 Outbox 重建。幂等键用于避免网络重试产生重复决策或重复任务。

本地集成可使用 `docker-compose.yml` 中的 MySQL、迁移工具和 API；开发环境也支持 SQLite。生产部署方案是后端容器运行于微信云托管，MySQL 做持久存储，CloudBase 管媒体，本地 Worker 通过 HTTPS 与云端交换任务。Web 服务启动不自动执行数据库 DDL；迁移应作为独立步骤进行。`live` 不依赖数据库，`ready` 只要求数据库和迁移 head，不把可选模型或 Worker 离线当作 API 启动失败。

仓库具备日志、请求 ID、健康探针、能力审计、作业状态及部分追踪对象；真实告警、跨服务 trace、备份恢复、队列故障演练和云端容量数据仍需部署环境提供证据。

主要源码：`backend/app/services/ai_jobs.py`、`backend/app/services/motion/stage_tasks.py`、`backend/app/services/policy_learning/outbox.py`、`backend/app/main.py`、`ai-worker/worker.py`。

## 13. 评测方法与当前可信度

项目按三个层次保存证据。**代码与合同层**关注 API、权限、幂等、状态转换及页面接线；**模型与业务效果层**关注真实餐食误差、动作逐类识别与评分、建议安全性及个人策略对照；**部署层**关注真机、MySQL 迁移、CloudBase 生命周期、Worker 推理、备份恢复与运维告警。

仓库已有 pytest、Node 测试、审计脚本与 benchmark 协议，也有历史测试快照。但 2026-10-03 的当前收尾记录明确说明：本次改动后没有运行自动化测试、构建、迁移审计或真机验收。历史“后端 499 项、小程序 123 项通过”只属于当时版本。Worker 完整测试与真实模型加载同样没有当前版本的通过证据。故本报告不填写当前通过率，也不声称产品达到生产发布门槛。

当前最需要补齐的证据依次是：当前工作区的完整回归与接口合同；隔离 MySQL 上的升级/回滚/恢复；微信开发者工具与 Android/iOS 真机关键路径；CloudBase 上传、过期、删除与跨用户隔离；冻结餐食与动作真实数据集及人工复核；Worker 加载与超时降级演练；生产日志、告警和备份恢复。项目的技术竞争力最终应由这些可复现结果支持，而非由模型数量或页面数量推定。

详见 [当前收口状态](FINAL_RELEASE_READINESS_2026-10-03.md)、[审计基线](audit_baseline.json) 及 `benchmark/`。文档中的“建议目标”与“历史基线”均不能当作当前实测成绩。

## 14. 关键接口与持久化对象索引

下表给出主要业务能力对应的 API、持久化对象和执行边界。路由均以 `/api/v1` 为前缀；这是一份导航索引，具体请求字段和错误码应以相应 Pydantic schema 与路由实现为准。

| 能力 | 典型 API | 关键表/对象 | 核心边界 |
| --- | --- | --- | --- |
| 微信登录与档案 | `/auth/wechat`、`/users/me/health-profile` | `users`、`health_profiles` | 微信 code 换 openid，JWT 对应本人数据 |
| 饮食、运动与打卡 | `/records/diet/records`、`/records/exercise/records`、`/health/checkin/today` | `diet_records`、`exercise_records`、`health_checkins` | 记录版本与来源更正会影响派生证据 |
| 健康状态 | `/health/state`、`/health/state/features`、`/health/state/constraints` | `health_state_features`、`health_state_snapshots` | 输出覆盖、缺口、来源及安全约束 |
| Harness 能力 | `/harness/manifest`、`/harness/installations`、`/harness/installations/{id}/audit` | `harness_plugin_installations`、`harness_capability_audits` | 内置 manifest、本人授权、范围与审计 |
| Agent 与行动 | `/agent/respond`、`/agent/respond/stream`、`/agent/actions/{id}/confirm` | `health_agent_runs`、`agent_action_proposals`、`agent_action_audits` | 模型提案与真实写入分开 |
| 个人策略 | `/policy/compile`、`/policy/decisions`、`/policy/episodes/{id}/observations`、`/policy/episodes/{id}/finish` | `personal_strategy_units`、`policy_episodes`、`policy_observation_refs`、`policy_adjudications`、`personal_policy_beliefs`、`policy_outbox` | 协议冻结、来源核验、门控裁决、失效重算 |
| 餐食识别 | `/vision/food-analysis`、`/vision/food-analysis/{id}/correct`、`/vision/food-analysis/{id}/finalize`、`/food/analysis/{id}/calculate` | `food_analysis_sessions`、`food_analysis_corrections`、`food_references` | 草稿、更正、确定性计算、确认后记账 |
| 动作分析 | `/media/motion-analyses`、`/media/motion-analyses/{id}`、`/media/motion-analyses/{id}/evidence` | `motion_analysis_runs`、`motion_evidence_frames`、`motion_stage_tasks`、`motion_gold_evaluations` | 异步任务、真实帧引用、评分能力门控 |
| Worker 交换 | `/worker/heartbeat`、`/worker/jobs/claim`、`/worker/jobs/{id}/progress`、`/worker/jobs/{id}/complete` | `ai_jobs`、`ai_worker_nodes` | 令牌、租约、进度、结果合同与重试 |
| 隐私操作 | `/privacy/export`、`/privacy/account`、`/privacy/deletion-status` | `privacy_audits`、`media_deletion_tasks` | 数据导出、删除与未获平台证明的云文件状态 |

健康状态和策略证据的关系可概括为：`源记录 ID + revision → 版本化特征/快照 → 冻结协议 → 周期观测 → 裁决 → 条件化信念 → 决策`。媒体和 Worker 走另一条异步链：`CloudBase fileID → media_asset → ai_job → motion_analysis_run/food_analysis_session → 经用户确认的记录或反馈`。这两条链在健康状态和个人策略的证据来源处汇合。

## 15. 结论

HealthMate 现阶段最有辨识度的工程设计，是把多模态健康输入放进同一个受权限控制、可追溯、可确认、可撤销的行动系统：模型负责帮助理解和提出候选，确定性服务负责来源核验、约束检查、营养计算、协议裁决与失效重算，用户保留最终写入控制权。

这条主轴在代码层已有较完整的结构和用户路径。现阶段仍应将其定位为**待完成真实评测和部署验收的实现**。下一轮工作的重点是把当前代码变成可重复运行、可在真机复现、可用真实数据量化效果的交付结果。
