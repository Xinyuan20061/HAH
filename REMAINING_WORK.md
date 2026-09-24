# 剩余工作说明

更新时间：2026-09-24

本文件只记录本轮因额度停止后仍未完成或需要外部环境验收的事项，避免把“已实现”与“已正式上线验证”混在一起。

## 2026-09-24 Agent v4 微实验冻结主线（代码与本地验证已完成，人工/真机待办）

依据 `docs/FIRST_PRIZE_IMPROVEMENT_PLAN_2026-09-24.md` 第 1–2 天冲刺。

已完成：

- 迁移 `0019_agent_micro_experiments` 唯一 head；模型、`experiments.py v4.0`、`/agent/experiments` CRUD、insights 接口 proposal 注入、Action Registry 审计（start/finish/cancel）、隐私导出与账户删除均已覆盖微实验。
- 五类信号（record_gap / exercise_stall / sleep_deficit / weight_rise / motion_decline）均提供温和版/标准版两档 3–7 天方案，含明确指标与停止条件；同一用户同时最多一个活跃实验；未确认不落库。
- 评测页 `/evaluation/dashboard` 新增微实验统计：启动/进行/完成/停止数、完成率与结果分布（supports_hypothesis / not_supported_yet / insufficient_data / cancelled_by_user），只统计已确认启动的真实样本，零样本不显示为 0。
- 结果只输出“支持假设 / 尚未支持 / 数据不足”，不声称因果；数据不足分支（insufficient_data）与主动停止（cancel）补齐自动化断言。
- 可解释个性化：`variant_history` 统计用户历史方案选择，前端展示“你之前更常选择温和版（已启动 N 次）”，偏好仅影响排序与默认提示，不越过安全规则。
- 新增端到端验证脚本 `backend/scripts/verify_agent_v4_experiments.py`：完整 HTTP 链路跑通“正常实验 → supports_hypothesis（样本 2，审计 [start, finish]）”与“数据不足实验 → insufficient_data（样本 0，不生成正向结论）”，并验证主动停止与评测样本口径。
- 输出“感知—推理—行动—验证”闭环架构图（本对话中以 renderer 呈现）。
- 修复测试 flaky：conftest 中测试环境禁用内存限流（生产限流不变）。
- 回归：backend **138 passed**、Worker **90 passed**、小程序 **20 passed**；端到端脚本 EXIT=0。
- 按用户要求本轮未生成任何交付压缩包（未到最终版本）。

仍待办（不在本轮代码范围）：

- 生产 MySQL 备份上验证 0019 的 fresh / incremental / repeat 升级并留日志。
- 两个真实微信账号验证登录、数据隔离、CloudBase 文件隔离、导出与删除；真机弱网/断网验收。
- 33 条 × 2 双人盲评仍为空白模板，需两名评审员真实打分（严重错误为 0、安全 ≥4.7、核心维度 ≥4.3、一致率 ≥85%）。
- 真实用户 7 日试用（20–30 人），收集启动率/完成率/帮助率与负向样本。
- 模型去弱保持暂停：动作基线 41.67%、识餐 MAPE 72.70% 不达标，主张已收窄为辅助能力；不恢复训练前不对外宣传高精度。

## 2026-09-24 评审体验升级（已完成）

- 新增小程序“健康提醒”页面：五类 Agent v3 信号展示观察依据、保守建议、风险等级与具体行动；首页和个人页均有入口。
- 首页并行读取 `/agent/insights`，提醒失败不会影响今日指挥中心主流程；有风险显示首要变化，无风险显示保守空状态。
- 聊天页已消费 Agent v2 `trace`，展示建议由哪个职责角色整理，以及训练调整原因、指导重点与护栏变化。
- 健康提醒支持把上下文问题带到健康助手输入框，仍由用户确认发送，不自动替用户执行。
- 主动提醒新增 7 日记录覆盖度与数据等级，前端明确展示提醒基于多少天真实记录。
- 小程序自动检查由 9 项增至 19 项；2026-09-24 全量实测 backend 130、Worker 90、小程序 19 全绿。
- 新增 `docs/REVIEWER_AUDIT_AND_ROADMAP_2026-09-24.md`，记录内部模拟评分、评委高频追问、P0/P1/P2 路线与新版演示主线。
- `healthmate-delivery-v7-final.zip` 已纳入本轮页面、运行画像、测试与评审文档；旧 zip 仅作审计留档，最终哈希以包外交付回执为准。

## 2026-09-24 Agent v3.1 竞争力升级（已完成代码，人工评分待执行）

- 主动提醒新增“有帮助 / 不准确 / 已处理”反馈；仅写入现有评测事件，不改数据库 schema、不改健康记录、不自动用于训练。
- Agent 统计按“同一用户同日同类提醒最后一次判断”聚合，输出反馈样本量、有帮助率、已处理数与分布，避免反复点击刷高指标。
- 小程序评测面板同步展示真实用户反馈；零样本明确显示暂无样本。
- 新增双人真实回答评审工具链与 66 行空白 CSV / 正式 XLSX 工作簿，覆盖 33 个冻结案例；缺评分、越界评分、重复/单人评审会拒绝生成报告。
- 尚未填充真实在线回答和双人评分，因此不能宣称人工回答质量已经达标。
- 本轮改动已进入 `healthmate-delivery-v8-final.zip`；v7 作为上一稳定版保留。

## 已完成且已有结果

- 识餐默认云端直连，失败时同一任务静默回到本地 Worker 队列；保留显式 `route=worker`。
- Worker 支持 `AI_MODE=local_first/cloud_first/off`、`--self-check` 和 Windows watchdog。
- 动作规则识别从 3 类扩展到 6 类，小程序动作入口同步扩展。
- REHAB24-6 固定测试集：六类各 20 段，共 120 段，120/120 处理完成。
- 动作规则基线：全样本准确率 41.67%、已接受准确率 64.94%、Macro-F1 54.15%、覆盖率 64.17%。报告：`benchmark-results/motion-v1/`，报告 SHA-256：`7bee63cc716b2d2835ec952fa0cc6017516fd95a45a9bf1b74e49fc0d9c4b575`。
- Nutrition5k 固定 42 图基线：34 张返回有效结构，完成率 80.95%，热量 MAE 94.73 kcal、MAPE 72.70%。报告：`benchmark-results/food-v1/report.json`，报告 SHA-256：`fd3f52362fd85336cd055b0c6cf95932b3f4e0c16e8308b7d43222c70a8606ae`。
- 因识餐 MAPE 超过 30%，已实现可选的预训练 Food-101 菜名候选层；默认未启用。
- 五个主要小程序页面已精简，默认视图去除任务书列出的技术术语；小程序 9 项测试通过。
- 数据登记文件已生成：`benchmark/dataset_registry.json`。
- 模型/评测登记迁移已增加：`0017_motion_six_action_baseline`。
- 真实视频 smoke 已通过：腿外展、手臂侧平举，以及 HTTP 上传→任务→真实 Worker→MediaPipe→完成闭环。
- 在最后阶段额外加固了云端图片下载：公网地址解析与固定、TLS 主机名保留、禁止重定向、流式字节上限；对应定向测试 3 项通过。

## 本轮 90 分优化（2026-09-23 追加）

用户主线已从“暂停 AI 训练”切换到“先优化 AI/Agent/RAG 其他方面”，以下均为真实执行结果：

### v6 对话层全面升级（2026-09-23 追加，四主线之 A/B/C 已完成）

- **A 语义混合 RAG（已完成）**：`app/services/rag/embeddings.py` 加载 Xenova/bge-small-zh-v1.5 ONNX（约 95MB，`D:\HealthMateData\models\bge-small-zh-v1.5`，外部权重不进交付包）；`service.py` 升级为 `audited_hybrid_v2`（词法池准入 + 语义重排 0.55/0.45 + 词法空语义兜底 0.45 + 创作意图拒绝）。
- **A 评测（rag-v2，61 条）**：`benchmark/rag_queries.jsonl` 扩充 12 条真实口语变体（高血压跑步/盐吃多坏处/练大腿/没时间锻炼/老坐着/糖尿病人运动/每周运动量/俯卧撑姿势/吃多少盐/晚餐搭配/写诗负向/股市负向），并诚实对齐 2 条标注（练大腿=深蹲+箭步蹲；没时间锻炼=一般指南+久坐替代）。结果：**Hit@1 90.2%（v1 75.61% → +14.6pt）、Hit@3 100%（51/51）、MRR 0.9477、拒绝率 100%、来源 100%、无失败**；报告 `benchmark-results/rag-v2/`，rag-v1 保留对照。
- **B 对话路由统一（已完成）**：`app/api/v1/chat.py` 的 chat/chat/stream 在 DeepSeek 直连前注入 `search_knowledge` 权威知识片段（约束回答与已审核来源一致，不输出链接）；`miniprogram/pages/chat/index.js` `shouldAgent` 正则扩大（喝水/睡眠/腰酸/久坐/体重/血压/跑步/步数/合适/怎么办等健康词全走 Agent），普通问答不再裸答。
- **C 回退自然化（已完成）**：rules-fallback 前缀改为“以下建议依据已审核资料和你的记录生成，请结合自身情况确认后再执行。”，前端角标保持“基础建议”，无 DeepSeek 字样。
- **回归**：backend **103 passed** 全绿（含混合检索断言 `audited_hybrid_v2` 与回退文案断言同步更新）。
- **D 本地对话引擎（✅ 已完成 2026-09-23）**：deepseek 与本地为**主备 failover** 权重关系。Qwen2.5-0.5B-Instruct ONNX（q4f16 340MB，`D:\HealthMateData\models\qwen2.5-0.5b-instruct-onnx`，不进交付包）由 `local_llm.py` 生成器 + gateway `LocalProvider` 接入：DeepSeek 503 → 本地接管基础问答 → 规则兜底；plan 意图保持规则。端到端验证：模拟云端故障 general 意图 provider=local 正常作答。local-v1 六项评测 + 全量 **109 passed**。


- **权威知识库扩展**：迁移 `0018_seed_knowledge_documents` 在 0010 的 4 条之上新增 9 条（WHO 久坐、国家卫健委高血压运动/营养 2024、中华医学会 2 型糖尿病运动 2024、中国疾控食物多样 12/25 种、体育总局深蹲、中新网俯卧撑、杭州中医院箭步蹲等），活跃知识共 13 条，source_url 均为 https 官方来源并经搜索核验。
- **RAG 正式冻结查询集**：`benchmark/rag_queries.jsonl` 49 条（41 正 8 负，五类覆盖），哈希 `90ed3472891b032f26b7e4d3c2e3a3824d567a529729c18a495617cd6d9d3769`；检索器迭代三轮（长文档归一化、扩展词不强化标签、复合标签加成、原始查询判定）。
- **rag-v1 报告**：`benchmark-results/rag-v1/`：Hit@3 **100%**（41/41）、Hit@1 75.61%、MRR 0.874、无关拒绝率 100%、来源完整率 100%。已知词法边界如实标注（好处/危害泛意图 vs 疾病指南）。
- **Agent 固定集评测**：`benchmark/agent_queries.jsonl` 31 条 + `benchmark-results/agent-v1/`：意图识别 100%、安全拦截 100%（8/8）、结构契约 100%（计划合法/知识注入/回复非空/无裸链/免责声明/安全等级）、provider 故障降级回退 100%（3/3）。评测使用 mock provider，不发起真实 DeepSeek 请求。
- **评测驱动的安全修复**：评测发现并修复 3 类输入绕过变体——药物调整（能停吗/可以停/停掉/减半/加量/加倍/停几天）、运动红旗（晕倒/突然晕/晕过去）、极端节食（500卡/每天只吃）。对应 `app/services/safety.py` RULES 加宽。
- **Agent 可观测性**：新增 `GET /api/v1/agent/stats`（意图分布、provider 分布、延迟 P50/P95、AI 不可用率，全部来自真实事件记录）。
- **回归**：backend 103 passed（原 96 + 新增 Agent 评测 7 项）；RAG/Agent 评测协议文档 `benchmark/RAG_EVALUATION.md`、`benchmark/AGENT_EVALUATION.md` 已更新。

### 尚未完成

### 1. SlowFast 六分类训练（用户明确暂停，代码已就绪）

用户已明确“现在先不进行 AI 训练”，本项暂停、不对外宣称。代码侧已完成：`slowfast_r50.py`（可加载 Kinetics-400 预训练权重，strict 已修通，前向验证 33.7M 参数）、`training.py` 的 `train_slowfast_six_action` 入口、`_VideoClipDataset`、`scripts/train_slowfast_six_action.py`。恢复时按下方步骤执行，且需先取得用户同意。

1. 按 RTX 4060 对应 CUDA 版本安装 PyTorch、MMEngine、MMCV、MMAction2（mmcv 安装此前验证为不可行路径，恢复时优先无 mmcv 的纯 torch 实现）。
2. 使用 `ai-worker/healthmate_worker/models/training.py` 中必须显式传入 `--pretrained` 的微调入口，禁止无权重从零训练。
3. 按受试者划分使用 `benchmark/rehab24_action_manifest.jsonl`，冻结 backbone，只训练六分类头。
4. 输出 TorchScript、模型卡、SHA-256，并写入 Worker 环境配置。
5. 在固定测试集重新评估；只有真实指标通过门槛后，才能把模型登记改为 active/validated。

### 2. Food-101 改进版尚未重跑

可选候选层代码与依赖清单已经存在，但尚未安装 torch/transformers，也未下载并复核具体 Food-101 预训练模型。因此现有识餐报告仍是改进前的 DeepSeek 基线，不能用来证明候选层有效。

后续需要安装 `ai-worker/requirements-food-classifier.txt`，配置 `FOOD_CLASSIFIER_MODEL`，再用同一 42 图固定集重跑并生成新版本报告；旧报告必须保留用于对照。

### 3. 最后一轮全量回归 ✅ 已完成（2026-09-23 复核）

已按下方命令实际重跑，全部通过并已更新正式文档：

- Backend：**103 passed**（原 96 + 新增 Agent 固定集评测 7 项）；SQLite 增量迁移到 `0018_seed_knowledge_documents`（13 条知识，fresh/repeat/downgrade 已验证），EXIT=0，PASS。
- Worker：**90 passed**；`verify_local_motion.py` 真实视频 smoke EXIT=0：腿外展 41 采样、手臂侧平举 106 采样、HTTP 上传→真实 Worker→MediaPipe→done 闭环 16 采样。
- 小程序：**9 passed**（`node --test` 目录内运行；`node --test tests` 目录参数在 Node 22 下会误解析为模块路径，实际命令必须是在 miniprogram 目录内直接 `node --test`）。
- 仓库扫描：64 个 JSON、30 个 JavaScript、356 个源码/文档文件，PASS。
- `README.md` 与 `docs/VERIFICATION.md` 中 Backend 计数已改为 96，仓库文件数改为 356，Git 元数据表述已修正（根目录无 git、仅 backend/ 有 4 个提交）。

### 4. 交付包需要重新生成 ✅ 已完成（2026-09-23，AI 优化后 v2）


- **Agent v3.1 主动健康预警（✅ 已完成）**：确定性扫描五类信号并返回 7 日记录覆盖度；新增用户反馈、24 小时回显、同日同类最后判断去重、运行画像统计与双人真实回答评审工具链。`tests/test_proactive.py` 十一项，backend 全量 **130 passed**，小程序 **19 passed**。
- **Agent v2 多智能体协作（✅ 已完成 2026-09-23，竞赛创新点）**：Coordinator 意图路由 + Planner/Coach/Nutritionist/SafetyGuardian 子智能体（`app/services/agent/specialists.py` v2.0）；每次响应返回决策轨迹 `trace`（specialist/routing/adjustment_reasons/guardrail 变更），可审计“为什么这么建议”；注入最近 3 条交互摘要实现对话记忆闭环（支持“上次的计划/按上次调整”指代）。评测：`benchmark/agent_queries.jsonl` 扩至 33 条，schema v2，`benchmark-results/agent-v2/`：子智能体路由 100%、决策轨迹 100%、意图 100%、安全拦截 100%、回退 100%；`tests/test_agent_v2.py` 六项；backend 全量 **115 passed**。v4 交付包不包含 Agent v2，需重打包 v5。
- v5 交付包 `healthmate-delivery-final.zip`（17,855,295B，SHA `C4317CEA…10DE`）为 AI 优化前版本；v6 对话层升级后已重新打包 v3（17,868,036B，SHA `AFC7444D…FD609`）；**D 本地引擎交付后需重新打包 v4**（见下文第 4 节更新）。
- 上一版 final 包已改名 `healthmate-delivery-20260923-pre-ai-opt.zip` 保留审计（再上一版为 `…-pre-final.zip`）。
- 核对结果：包内不含 `.venv*`、`__pycache__`、`.pytest_cache`、node_modules、数据库、uploads、日志、`.env`、旧 zip 与 `D:\HealthMateData`；含 0018 知识迁移、rag_queries/agent_queries 固定集、rag-v1/agent-v1 报告、AGENT_EVALUATION.md、RAG_EVALUATION.md。

### 5. 外部环境验收

- 在生产 MySQL 备份后执行迁移到当前唯一 head `0018_seed_knowledge_documents`，重跑 fresh/incremental/repeat migration。
- 在真实微信云托管验证默认识餐 15 秒内完成、云失败回落、`route=worker`、真实 fileID 和临时 URL 刷新。
- 使用微信开发者工具编译全部 WXML/WXSS，并做真机一屏布局、相机/相册权限、校正保存、计划勾选、隐私导出验收。
- 复核 REHAB24-6、Squat Dataset、Nutrition5k 和 SlowFast 权重的上游许可；当前登记状态是“发布前复核”，外部数据不得进入提交包。

## 不应对外宣称的内容

- 不应宣称六类动作达到高准确率；当前规则基线全样本准确率为 41.67%。
- 不应宣称 SlowFast 已训练或已上线。
- 不应宣称 Food-101 候选层已经改善营养精度。
- 不应把 Nutrition5k 的食材代理 Top-1/Top-3 当作标准菜名分类准确率。
- 不应把本地 SQLite、Mock、smoke 或固定单一数据集结果写成生产微信环境验收。
- 不应把 RAG/Agent 离线评测写成真实 DeepSeek 在线质量证明：rag-v1/agent-v1 用冻结查询集和 mock provider 测量检索与结构契约，在线回答质量需人工评审。
- 不应把词法检索 Hit@1 75.61% 说成 100%：报告如实区分 Hit@1 与 Hit@3。

### 2026-09-24 续：演示数据工具链 + 长辈友好基线

- 受控演示脚本 `backend/scripts/seed_demo_account.py`：强制只写本地 healthmate.db（绝不连生产 MySQL），幂等灌入演示账号 demo-presenter-2026（昵称标注"数据为演示"，含 demo_data_seeded 时间线留痕）；14 天合成记录实测触发 exercise_stall / sleep_deficit / weight_rise 三信号。
- 同脚本自动生成微实验三态案例：1 已完成（record_gap 温和版，supports_hypothesis，带不夸大归因）+ 1 进行中（sleep_deficit 标准版）+ 1 已取消（weight_rise），配 5 条 experiment.* 审计行；/agent/stats 与 /agent/experiments 实测数字正确。
- 演示稿 docs/DEMO_SCRIPT.md：30 秒定位、7 步 3 分钟主演示、6 条高频追问口径、降级备份、演示前检查清单。
- 长辈友好基线（全局生效）：全局基础字号 28→32rpx、正文行高 1.65、主按钮触控区 88→100rpx 且字号 31rpx、菜单行 112→128rpx、次要文字对比度加深；"置信度"用户可见文案此前已统一为"参考程度/数据完整程度"。
- 回归：backend 138 passed、小程序 20 passed。
