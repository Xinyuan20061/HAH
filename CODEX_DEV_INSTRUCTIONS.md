# HealthMate Codex 开发指令（2026-09-23・v6：对话层全面升级已交付版）

> 本文件是交给 Codex（或任何编码代理）的
>
> **剩余工作**
>
> 任务书。v3 中的任务 A（识餐云端直连）、B（6 类动作 + 基线）、C（识餐基线）、D（前端清爽化）已按
>
> `REMAINING_WORK.md`
>
>  完成并验证；
>
> **不要重做已完成项**
>
> 。v5 新增：AI/Agent/RAG 90 分优化已交付（RAG 正式评测、Agent 固定集评测、Agent 统计端点、安全规则加宽），
>
> **不要重做这些项**
>
> 。v6 对话层全面升级四主线全部交付：A 语义混合 RAG（✅ 已交付并评测）、B 对话路由统一（✅ 已交付）、C 回退体验自然化（✅ 已交付）、D 本地对话引擎（✅ 已交付，Qwen2.5-0.5B ONNX 主备接管），
>
> **A/B/C/D 全部不要重做**
>
> 。按顺序执行剩余块，每完成一块即运行对应验证命令，全部通过后再进入下一块。所有改动必须保持现有测试全绿（2026-09-24：backend 130、worker 90、miniprogram 19）。
> 关键约束：
>
> **不要在未确认的情况下改动数据库 schema；schema 变更必须走 Alembic 迁移；不要删改用户的 .env；不要破坏生产已部署服务的兼容性；不重跑 / 不重写已有基线报告（保留用于对照）；用户已明确暂停 AI 训练，未获同意不得启动任何训练任务；外部模型权重（D:\HealthMateData）不进交付包。**



***

## 0. 项目状态（已完成 ✅ / 剩余 ⬜）

### 0.1 已完成并验证（不要重做）



| 项                                                                    | 状态   | 证据                                                                |
| -------------------------------------------------------------------- | ---- | ----------------------------------------------------------------- |
| 识餐云端直连 + 失败静默回落 Worker 队列 + `route=worker`                           | ✅    | backend 96 passed 含回落测试                                           |
| Worker `AI_MODE=local_first/cloud_first/off`、`--self-check`、watchdog | ✅    | worker 90 passed                                                  |
| 动作规则识别 3→6 类 + 小程序入口同步                                               | ✅    | 规则基线报告                                                            |
| REHAB24-6 固定测试集 120 段基线（41.67% / Macro-F1 54.15% / 覆盖 64.17%）        | ✅    | `benchmark-results/motion-v1/report.md`，SHA-256 `7bee63cc…c4b575` |
| Nutrition5k 42 图识餐基线（MAE 94.73 kcal / MAPE 72.70%）                   | ✅    | `benchmark-results/food-v1/report.json`，SHA-256 `fd3f5236…60a6ae` |
| Food-101 候选层**代码与依赖清单**（默认未启用）                                       | ✅ 实现 | `ai-worker/requirements-food-classifier.txt`                      |
| 前端 5 页清爽化（去 AI 术语、由简入繁）                                              | ✅    | 小程序 9 passed（7+2 页面单测）                                            |
| 数据登记 + 迁移 `0017_motion_six_action_baseline`                          | ✅    | `benchmark/dataset_registry.json`                                 |
| 云端图片下载安全加固（地址固定 / TLS / 禁重定向 / 流式上限）                                 | ✅    | 定向 3 passed                                                       |
| 全量回归 + 文档计数修正（README/VERIFICATION 96→103、仓库 356 文件）                   | ✅    | 2026-09-23 实测 103/90/9                                             |
| 权威知识库扩展 + RAG 正式冻结查询集（49 条）+ rag-v1 报告                                   | ✅    | Hit@3 100%、拒绝率 100%、来源 100%；`benchmark-results/rag-v1/`      |
| Agent 固定集评测（31 条）+ agent-v1 报告（mock provider 离线）                              | ✅    | 意图 100%、安全拦截 100%、结构 100%、降级回退 100%；`benchmark-results/agent-v1/` |
| Agent 可观测性 `GET /api/v1/agent/stats`（意图/provider/延迟/不可用率）                   | ✅    | 数据来自 health_agent_runs 与 evaluation events                     |
| 安全规则加宽（评测驱动的 3 类绕过变体修复：能停吗/晕倒/500卡）                                  | ✅    | `app/services/safety.py` RULES + 定向测试                              |
| RAG 语义混合检索（bge-small-zh ONNX + 词法池 + 语义重排 + 创作意图拒绝）                  | ✅    | rag-v2：Hit@1 90.2%、Hit@3 100%、MRR 0.9477、拒绝率 100%、来源 100%    |
| 对话路由统一（chat/chat/stream 注入 RAG 权威知识 + 前端健康问法正则扩大）                     | ✅    | `app/api/v1/chat.py` + `pages/chat/index.js`；backend 103 passed      |
| 回退体验自然化（去掉“DeepSeek 不可用或返回格式无效”生硬前缀）                            | ✅    | `orchestrator.py` rules-fallback + 前端 provider 文案；agent 评测同步   |

### 0.2 数据与权重现状（`D:\HealthMateData\`，项目外，不进提交包）



| 资源                                                                                                              | 路径                                                           | 状态                    |
| --------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ | --------------------- |
| REHAB24-6 视频（6 类 1072 片段：squat 195 /pushup 107 /lunge 174 /leg\_abduction 210 /arm\_abduction 178 /arm\_vw 208） | `D:\HealthMateData\motion\raw\rehab24-6\`                    | ✅ 可用                  |
| REHAB24-6 2D 骨骼（260 个 .npy，30/120fps，已解压）                                                                       | `D:\HealthMateData\motion\raw\rehab24-6-2d\`                 | ✅ 可用                  |
| Squat Dataset 深蹲质量（train Good 1001 / Bad back 984 / Bad heel 852 + test）                                        | `D:\HealthMateData\motion\raw\squat\`                        | ✅ 可用                  |
| SlowFast-R50 Kinetics-400 预训练权重（132.3MB）                                                                        | `D:\HealthMateData\motion\pretrained\slowfast_…b62a501f.pth` | ✅ 已登记 `not_finetuned` |
| Nutrition5k 元数据 + 42 张测试 RGB                                                                                    | `D:\HealthMateData\food\`                                    | ✅ 可用                  |

### 0.3 剩余工作总览



1. **SlowFast 六分类微调**（REMAINING #1，动作识别核心升级）——**用户已明确暂停**（“现在先不进行 AI 训练”），代码已就绪（slowfast_r50.py / training.py / scripts），恢复须先取得用户同意；不恢复期间不得宣称已训练。

2. **Food-101 候选层实跑**（REMAINING #2，识餐精度改进）——待用户另行决策；训练集下载已由用户叫停，候选层使用预训练权重，不需要训练集。

3. **交付包最终生成**（REMAINING #4，旧包已改名保留，本轮 v6 优化后需重新生成 v3 交付包）。

6. **本地对话引擎 D**（对话层升级四主线之四，**待用户确认后实施**）：Qwen2.5-0.5B 本地 ONNX 推理，云端失败时真正接管基础问答；改动大，未确认不得开工，详见任务 R5-D。

4. **外部环境验收**（REMAINING #5，需真实微信环境 / 生产 MySQL）。

5. **RAG/Agent 在线质量人工评审**（新增）：rag-v1/agent-v1 为离线检索与结构评测；正式答辩前用固定回答集做双人事实核查与引用支持度评分。



***

## 1. 架构铁律（已定稿，任何新代码必须遵守）

DeepSeek 与本地识别 = **权重关系**，一个 `AI_MODE` 三档配置决定，不做复杂双向协同：



| AI\_MODE          | 行为                         | 适用        |
| ----------------- | -------------------------- | --------- |
| `local_first`（默认） | 本地优先；本地置信度 < 阈值才调 DeepSeek | 日常        |
| `cloud_first`     | DeepSeek 优先；云端失败静默回落本地     | 演示 / 识餐默认 |
| `off`             | 纯本地，禁一切云端调用                | 离线 / 隐私   |



* 同一任务两引擎都出结果 → 以置信度高者为准；**云端失败永远静默回落本地，绝不允许云端故障导致功能报错**。

* 识餐默认 `cloud_first`（云端直连，失败原任务回队列）；动作默认 `local_first`（本地毫秒级）。

* 前端只展示最终结果与 `source` 角标，不暴露权重机制。



***

## 任务 R5：对话层全面升级（v6 新增）

> 用户反馈“效果仍然不好”，原因定位：普通健康问答（喝水/睡眠/腰酸/体重等）前端正则分流后根本不走知识检索、DeepSeek 裸答；回退文案生硬“DeepSeek 不可用或返回格式无效”。
> 四主线：**A 语义检索（✅ 已交付）、B 路由统一（✅ 已交付）、C 回退自然化（✅ 已交付）、D 本地引擎（⬜ 待确认）**。

### R5-A 语义混合 RAG（已完成，不要重做）

- `backend/app/services/rag/embeddings.py`：Xenova/bge-small-zh-v1.5 ONNX（约 95MB）嵌入，L2 归一化 + 哈希兜底，`EMBEDDING_MODEL_DIR` 配置。
- `service.py`：`audited_hybrid_v2` = 词法池准入（score≥0.08）→ 语义重排（词法 0.55 + 向量 0.45）→ 词法空时语义兜底（阈值 0.45）→ 创作意图拒绝（写诗/写作文/写报告等）。
- 评测：61 条（51 正 10 负）Hit@1 90.2% / Hit@3 100% / MRR 0.9477 / 拒绝率 100% / 来源 100%，报告 `benchmark-results/rag-v2/`。
- 验收：`scripts/evaluate_rag.py` 重跑须复现同一指标区间；`pytest tests/test_reliability.py::test_audited_knowledge_search_and_agent_citations` 断言 `audited_hybrid_v2`。

### R5-B 对话路由统一（已完成，不要重做）

- `chat.py` 的 `chat`/`chat/stream` 在 DeepSeek 直连前注入 `search_knowledge(..., 3)` 权威知识片段（不输出链接/编号，只约束回答与已审核来源一致）。
- `miniprogram/pages/chat/index.js` `shouldAgent` 正则扩大：喝水/睡眠/腰酸/久坐/体重/血压/跑步/步数/吃多少/合适/怎么办等健康词全部走 Agent（知识+计划+资源），chat 流式兜底同样有知识。
- 验收：backend 103 passed；用 curl 发“每天喝多少水合适”应返回知识支撑的回答。

### R5-C 回退体验自然化（已完成，不要重做）

- `orchestrator.py` rules-fallback 前缀改为“以下建议依据已审核资料和你的记录生成，请结合自身情况确认后再执行。”
- `test_agent_evaluation.py` 断言同步为“依据已审核资料”。
- 前端 provider 角标保持“基础建议”，不出现 DeepSeek 字样。

### R5-D 本地对话引擎（✅ 已交付，2026-09-23）

> 回答用户“deepseek 和本地是**一个权重关系**”：**主备（failover）关系，不是融合加权**。
> 主引擎 DeepSeek（质量高）→ 不可用时本地 Qwen2.5-0.5B ONNX（基础问答）→ 再不可用 rules-fallback。三档链路统一由 `get_provider()` 返回，前端只显示最终 provider 角标。

- 权重：`onnx-community/Qwen2.5-0.5B-Instruct` ONNX（hf-mirror 下载）到 `D:\HealthMateData\models\qwen2.5-0.5b-instruct-onnx\`；**实测 int8 变体质量崩坏（生成乱码/答非所问），默认加载 q4f16（340MB，实测质量正常、CPU 约 1s/问）**；外部权重不进交付包。
- `app/services/ai/local_llm.py`（新）：Qwen 模板 + KV cache 生成循环（24 层 / 2 KV heads / 151k 词表），greedy 采样，整体解码（逐 token 解码会损坏 UTF-8），超长 prompt 拒绝，进程级按目录缓存。
- `app/services/ai/gateway.py`：新增 `LocalProvider` + `get_local_provider()`（模型目录缺失/加载失败返回 None 静默落入下一档）。
- 接入：`chat.py` DeepSeek 503 → 本地接管；`orchestrator.py` 云端故障时非 plan 意图由本地直接作答（0.5B 无法产出合法 JSON 计划，plan 意图保持 rules-fallback）。
- 端到端验证：模拟 DeepSeek 503 → general 意图 provider=local 返回真实健康回答；plan 意图 provider=rules-fallback 3 项计划。
- 评测：`tests/test_local_llm.py` local-v1 六项（可用性/超长拒绝/配置缺失回落/坏目录回落/stream），**backend 全量 109 passed**。
- 配置：`.env.example` 新增 `LOCAL_LLM_MODEL_DIR` / `LOCAL_LLM_MAX_NEW_TOKENS`。

### R6 Agent v2 多智能体协作（✅ 已交付，2026-09-23 · 竞赛创新点）

> 目标：把 Agent 从“单协调器一次出全部结果”升级为**多智能体协作 + 可审计决策轨迹 + 对话记忆闭环**，作为一等奖答辩的创新点与核心竞争力。

- **架构（职责分离）**：`Coordinator` 意图路由 → `Planner`（周计划，guardrail 调整、待确认）/ `Coach`（动作画像驱动训练指导，引用 motion_profile 与 exercise_recommendations，不夸大）/ `Nutritionist`（RAG 营养知识 + 用户记录，[K1] 标注）/ `SafetyGuardian`（输入安全评估先行 + 输出审查）；`app/services/agent/specialists.py`（v2.0）提供 `build_coordinator_system` / `build_specialist_instruction` / `build_conversation_memory`。
- **决策轨迹（可解释）**：每次响应返回 `trace`（specialist、routing、provider、adjustment_mode、adjustment_reasons、coaching_focus、plan_guardrail_changes），回答“为什么这么建议”，随 HealthAgentRun 存储；/stats 可复核。
- **对话记忆闭环**：注入最近 3 条 Agent 交互摘要（仅摘要，用户文本不作指令），支持“上次的计划练得有点累/按上次调整”类指代。
- **评测**：`benchmark/agent_queries.jsonl` 扩至 33 条（新增 nutritionist 路由用例）；schema 升级 `healthmate-agent-benchmark-v2`；`tests/test_agent_v2.py` 六项（路由/指令契约/记忆注入/trace）；**agent-v2 报告：子智能体路由 100%、决策轨迹完整率 100%、意图识别 100%、安全拦截 100%、规则回退 100%**；backend 全量 **115 passed**。
- 输出文件：`benchmark-results/agent-v2/`、`tests/test_agent_v2.py`、`app/services/agent/specialists.py`、orchestrator.py（route_specialist + trace 组装）。

### R7 Agent v3 主动健康预警（✅ 已交付，2026-09-23 · 继续升级）

> 目标：把 Agent 从“被动问答”升级为“**主动健康管家**”：不等人问，先基于记录发现风险并给出干预建议。全部为确定性规则，无模型推断，可离线评测。

- **`app/services/agent/proactive.py`（v3.1）**：`build_proactive_insights(context)` 确定性扫描五类信号并按严重度排序（上限 4 条）：
  - `exercise_stall`（运动断档 ≥3 天且有目标）→ 建议轻量重启；
  - `sleep_deficit`（近 7 天 ≥2 天睡眠<6h 且最新仍短）→ 恢复优先；
  - `motion_decline`（动作画像任一 `recent_change_points ≤ -8`）→ 降量复核；
  - `weight_rise`（最近 3 次体重连续上升）→ 温和提示；
  - `record_gap`（近 7 天零记录）→ 引导开始记录。
  - 每条含 `{code, severity, title, evidence, advice, source}`；无信号时返回“当前没有需要主动干预的信号”。
- **接入**：`read_context` 注入 `proactive_insights`（respond 的子智能体上下文自动携带）；新端点 `GET /api/v1/agent/insights`（返回 insights + trace：proactive_guardian）。
- **评测**：`tests/test_proactive.py` 十一项（五类信号、正常用户零误报、trace、记录覆盖度、反馈审计/去重/非法拒绝）；另有双人真实回答评审工具链，**backend 全量 130 passed**。


***
## 任务 R1：SlowFast 六分类微调（REMAINING #1・P0）

> 目标：把动作识别从 "规则基线（41.67%）" 升级为可宣称的六分类模型。
>
> **当前机器无 PyTorch/MMEngine/MMCV/MMAction2，未训练；权证仅登记&#x20;**
>
> `not_finetuned`
>
> **，不得宣称为已训练模型。**

### R1.1 环境安装



* 按 RTX 4060 Laptop（8188MiB，驱动 610.62）安装 CUDA 版 PyTorch + MMEngine + MMCV + MMAction2（版本配套，参考官方 install 文档；镜像源 TLS 报错时修复证书或使用官方源，**禁止关闭 SSL 校验**）。

* 安装后记录版本号到 `docs/VERIFICATION.md`（新增行，不改历史行）。

### R1.2 训练入口（代码）



* 在 `ai-worker/healthmate_worker/models/training.py` 增加 SlowFast 微调入口，**必须显式传入&#x20;**`--pretrained`（指向已登记权重路径），**禁止无权重从零训练**（无 `--pretrained` 直接报错退出）。

* 用 `benchmark/rehab24_action_manifest.jsonl` 按**受试者**划分 train/val/test（同一受试者不得跨集合）。

* 冻结 backbone，只训练六分类头；RTX 4060 上 batch/epoch 按显存实测调整。

* 输出：TorchScript、模型卡（类别、样本量、受试者划分、硬件、超参、训练时长）、SHA-256。

* 把 TorchScript 路径与哈希写入 Worker 环境配置（`.env.example` 占位 + 文档说明），`model_registry` 对应行保持 `not_finetuned` **直到评估达标**。

### R1.3 评估与门槛



* 在**同一 120 段固定测试集**（`benchmark-results/motion-v1` 的冻结标注集）上评估，输出新报告（如 `motion-v2`），**旧规则基线报告保留对照**。

* 门槛：闭集 Top-1 ≥ 85%；未见过动作拒识 AUROC ≥ 0.80；低置信回退规则识别器。

* **诚实约束**：只有真实指标达标才把 `model_registry` 改为 `active/validated`；不达标就如实报告 "未达门槛"，并在文档中标注仍以规则基线为准。

* 达标后更新：`docs/VERIFICATION.md`、`README.md`（如需）、前端动作页来源角标逻辑（新增 `slowfast` 来源）。

### R1.4 验收（R1）



* `training.py --pretrained` 可跑通并产出 TorchScript + 模型卡 + SHA256；`--pretrained` 缺失时报错。

* 新评测报告生成且旧报告仍在；worker 测试全绿（90 passed 基础上可能新增训练管线测试）。



***

## 任务 R2：Food-101 候选层实跑（REMAINING #2・P1）

> 现状：候选层代码与 
>
> `requirements-food-classifier.txt`
>
>  已有但
>
> **未安装 torch/transformers、未下载复核具体预训练模型**
>
> ，
>
> `food-v1`
>
>  报告仍是 DeepSeek 基线，不能证明候选层有效。

### R2.1 执行



* 安装 `ai-worker/requirements-food-classifier.txt`（torch/transformers 等）。

* 配置 `FOOD_CLASSIFIER_MODEL`（用 HF/timm **已训练**权重做菜名候选，不需要下载 Food-101 训练集）。

* 复核模型可加载、输出类别与 Nutrition5k 食材代理标签可映射。

### R2.2 重跑与对照



* 用**同一 42 图固定集**重跑，生成新报告（如 `food-v2` 或 `food-v1-improved`），输出热量 MAE/MAPE、菜名候选 Top-1/Top-3、区间覆盖率、Brier。

* **旧&#x20;**`food-v1`**&#x20;报告必须保留对照**；新报告标注与基线差异。

* 改进有效（MAPE 明显下降）才在 `dataset_registry.json` 把候选层登记为 enabled；否则保持 "默认未启用" 并如实记录。

### R2.3 验收（R2）



* 新报告存在且含旧报告对比；worker 测试全绿；README/VERIFICATION 补充改进版结论（如达标）。



***

## 任务 R3：交付包最终生成与核对（REMAINING #4）

> 旧包 
>
> `healthmate-delivery-20260923.zip`
>
>  已改名 
>
> `…-pre-final.zip`
>
>  保留审计。正在生成 
>
> `healthmate-delivery-final.zip`
>
> 。

### R3.1 生成



```
.\scripts\package\_delivery.ps1 -OutputPath healthmate-delivery-final.zip
```

### R3.2 核对（生成后必须检查）



* 包内**不含**：`.venv`、`__pycache__`、`.pytest_cache`、`node_modules`、`uploads`、`dist`、`cache`、`*.db`、`*.log`、`.env`、旧 zip、`D:\HealthMateData`（外部目录由脚本显式排除）。

* 包内**包含**：backend（含 Dockerfile、alembic、scripts）、ai-worker（含 requirements\*、doctor/worker、models）、miniprogram、docs、benchmark（仅标注 / 清单 / 报告，无原始视频）、README、CODEX\_DEV\_INSTRUCTIONS、REMAINING\_WORK。

* 记录最终包大小与 SHA-256 到 `docs/VERIFICATION.md`（更新 "交付包" 行，注明取代旧包）。



***

## 任务 R4：外部环境验收（REMAINING #5・P1，需真实环境）

> 这些
>
> **必须在真实微信云托管 / 真机 / 生产数据库**
>
> 上做，本地 SQLite / 单测通过不能替代。逐项完成后在 
>
> `docs/VERIFICATION.md`
>
>  补 "外部验收" 小节并逐项打勾。

### R4.1 生产 MySQL 迁移



* 备份生产 MySQL → 在受控环境对备份执行迁移到当前唯一 head（以 `alembic heads` 命令结果为准，禁止手写 revision）→ 重跑 fresh/incremental/repeat 三种迁移校验（用专用审计库，禁止直接动业务库）。

### R4.2 真实微信云托管识餐验收



* 默认（cloud）识餐 15 秒内完成、可校正 / 保存。

* 模拟云端失败 → 静默回落本机 Worker → 同一任务完成（前端无报错）。

* 显式 `route=worker` 场景、真实 fileID 注册、临时 URL 原地刷新（`waiting_source_refresh`）。

### R4.3 微信开发者工具 + 真机走查



* 开发者工具编译全部 WXML/WXSS 无错误。

* 真机：一屏布局、相机 / 相册权限、校正保存、计划勾选、隐私导出。

* 记录机型与结果（不做 "开发工具通过 = 真机通过" 的等价替换）。

### R4.4 上游许可复核



* 复核 REHAB24-6、Squat Dataset、Nutrition5k、SlowFast 权重的上游许可；在 `benchmark/dataset_registry.json` 把 "发布前复核" 更新为最终结论。

* **外部数据不得进入提交包**（`D:\HealthMateData` 永远在项目外）。



***

## 2. 不应对外宣称的内容（红线，写文档 / 汇报时逐条自查）



* 不宣称六类动作高准确率（规则基线全样本 41.67%）。

* 不宣称 SlowFast 已训练或已上线（当前仅预训练权重 `not_finetuned`）。

* 不宣称 Food-101 候选层已改善营养精度（改进版未重跑）。

* 不把 Nutrition5k 食材代理 Top-1/Top-3 当作标准菜名分类准确率（当前为 0%）。

* 不把本地 SQLite、Mock、smoke、固定单一数据集结果写成生产微信环境验收。

* 不把 RAG/Agent 离线评测写成真实 DeepSeek 在线质量证明：rag-v1/agent-v1 用冻结查询集和 mock provider 测量检索与结构契约，在线回答质量需人工评审（0.3-5）。

* 不把 rag-v2 混合检索 Hit@1 90.2% 说成 100%：报告如实区分 Hit@1 与 Hit@3；rag-v1 词法基线 75.61% 保留对照，不混用口径。



***

## 给 Codex 的执行约定



* 每块完成即跑该块验收，失败先修再继续；**不重做已完成项**（0.1 清单）。

* 只改任务书点名范围；未点名文件保持原样。

* 新模型 / 新数据 / 新阈值必须登记 `model_registry` + `model_evaluations` + `dataset_registry.json`。

* 不输出未经真实测试集验证的数字；所有评测数字附样本量与报告哈希；旧报告保留对照。

* **权重关系铁律**：云端失败静默回落本地；`AI_MODE` 三档必须都可用且可切。

* 有歧义时先按本文件执行，假设写入 `CODEX_ASSUMPTIONS.md`，不停等人工确认（除非会破坏生产数据）。
