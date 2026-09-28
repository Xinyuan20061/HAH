# HealthMate 实际验收记录

初次执行日期：2026-09-18；最近本地复核：2026-09-27，Windows PowerShell。以下只记录已经执行的结果，不把单元测试或本地报告写成正式微信环境验收。

## 2026-09-27 竞争性开发阶段 0 验收（版本稳定）

|检查|真实结果|范围与限制|
|---|---|---|
|Backend / SQLite|156 passed|新增 kinetics400 任务/校验/存储与 media 端点测试；修复 `test_additional_safety` 的 3 个 chat 用例：测试环境显式隔离 `local_llm_model_dir`，不再被本机 `.env` 的本地模型配置带偏（有/无本机 .env 均同结果）|
|Worker|99 passed|含 kinetics400 处理器与中文映射 3 用例；Kinetics 能力探测升级为真实权重加载 + 单次前向 smoke，缓存复用|
|小程序|41 passed|media 页未校准百分比改称「候选分值」、预训练文案去「模型」词，页面展示规范测试转绿|
|工作区整理|PASS|一次性诊断脚本移入 `ai-worker/scripts/diag/` 受控目录；删除运行日志与临时输出；保留验收脚本 `backend/scripts/verify_ai_fallback.py`|
|Kinetics 模型门控|PASS|`KINETICS400_OVERRIDE_ENABLED=false`（默认）：motion auto 模式仅记录 400 类候选，不再改写规则结果；覆盖逻辑保留但需评测门禁通过后才打开|
|迁移 head|`0021_empty_default_nickname`|README / DELIVERY_CHECKLIST / LOCAL_AI_WORKER 版本口径已同步；生产 MySQL 最新迁移仍待阶段 3 复验|


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
|后端 / SQLite|`backend\.venv\Scripts\python.exe -m pytest -q`|161 passed，退出 0|backend/app `4348BF662C6A529CFCAC5F1B95281BA4AC9B6F3C4A244F73438A91E84AE3FFBD`（90 文件）|
|后端迁移|`backend\.venv\Scripts\python.exe scripts\verify_migrations.py`|PASS：legacy 0003 保留、head=`0022_agent_decision_id`、重复升级不变，退出 0|backend/migrations `b47e271948ac425f65cac60a08e46ba4e601bbd0a28e4175447b85579e5d9920`（25 文件；2026-09-28 修复 0022 回填 SQL 为 MySQL 兼容后重算）|
|Worker|`ai-worker\.venv\Scripts\python.exe -m pytest -q`|99 passed，退出 0|ai-worker/healthmate_worker `3912BBA3A35733606801FC291558B11C7C428936A94D6B6FD958BB7B97D7E08A`（33 文件）|
|小程序|`node --test`（miniprogram/）|43 passed，退出 0|miniprogram `823DBE9C2F03316B1D02A76524CFCB539926992E1C8CCFC882500BB3A9D91293`（109 文件）|

## 2026-09-28 阶段 2 验收（多模态可信输入验证）

|检查|真实结果|范围与限制|
|---|---|---|
|动作同集三路对照（Kinetics-400 实验候选层）|24 段子集（REHAB24-6：6 类 × 2 实例 × 双视角）全部处理完成：规则 Top-1 50.00% / 覆盖率 83.33% / Macro-F1 0.5765；Kinetics 单独 Top-1 16.67% / 覆盖率 16.67%（仅 lunge 有映射预测，Macro-F1 1.0000 不代表整体，已在报告加注）；规则+Kinetics 融合(模拟) Top-1 58.33% / Macro-F1 0.6127|均未达 §7.2 门槛（Top-1≥80%、覆盖率≥90%、Macro-F1≥0.78、任一目标动作召回≥65%）；CPU 时延 P50≈100-106 s/段；报告 [`benchmark-results/motion-v2-kinetics/report.md`](../benchmark-results/motion-v2-kinetics/report.md)，含清单/预测 SHA-256 与门控参数；全量 120 段基线见 motion-v1|
|Kinetics 门控维持|`KINETICS400_OVERRIDE_ENABLED=false` 维持，候选层只记录不覆盖；评测仅测量不改生产行为|独立固定集（任务 1：受试者不重合）在仅有 REHAB24-6 单数据集前提下无法完成，如实标注"子集对照、无独立验证集、softmax 未校准、不可用于设定上线阈值"|
|识餐三口径|样本 42、完成 34、带区间 34：原始 MAE 94.73 kcal；区间覆盖 44.12%、宽度均值 140.26（中位 130 / P95 270）kcal；中点校正代理 MAE 94.01（改进仅 0.76%）|未达 §7.3（区间覆盖≥80% 且可快速校正）；识餐保持实验入口不提升为正式推荐；报告 [`benchmark-results/food-v1/report_range.json`](../benchmark-results/food-v1/report_range.json)，不挑样本|
|动作结果分层|`POST/GET /api/v1/media/kinetics-jobs` 创建与查询（用户隔离、job_type 校验、不存在 404）与候选层校验（top_label∈candidates、概率 0-1、候选≤10）验证通过；小程序 media 页以"候选分值/预训练动作库"呈现，低质拒识有原因提示|分层逻辑在阶段 0.2/0.4 已落地，本轮复验通过|
|失败样本|评测过程中 4 段因内存不足（OpenCV Insufficient memory）失败后补跑成功，最终 0 失败；失败样本一律保留不删除|补跑单进程执行，避免并行评测耗尽系统资源（并行两进程曾致系统无响应，已记录为操作教训）|
|自动回归|阶段 2 未改动 backend/app、healthmate_worker、miniprogram 生产代码（仅新增 ai-worker/scripts 评测脚本与 benchmark 产物），冻结基线测试数与哈希维持阶段 1 基线|回归命令与结果见下方冻结基线（未变红）|

## 2026-09-28 阶段 3 部分验收（真实环境与用户证据 · 进行中）

|检查|真实结果|范围与限制|
|---|---|---|
|MySQL fresh/incremental/repeat 迁移|专用审计容器 `healthmate-audit-mysql`（localhost:13307，root/专用审计密码，**非生产**）重建 `healthmate_incremental` 库后：fresh 0001→0022 全链路、legacy 0003 行保留、重复升级不变，`[OK] mysql: legacy 0003 rows preserved, head=0022_agent_decision_id, repeated upgrade unchanged`|验证中发现并修复 0022 迁移回填 SQL 方言 bug（`'dec-'||CAST(id AS VARCHAR)` 在 MySQL 报 1064，改为 MySQL `CONCAT('dec-', id)` / SQLite `'dec-'||id` 分支）；SQLite 侧重复验证仍 PASS；日志 `benchmark-results/mysql-mig-0022.log`|
|微信与 CloudBase|**未开始**：需要两个真实微信账号（wx.login + AppSecret）与 CloudBase 资源|属用户配合项，本机无法代做|
|真机与弱网|**未开始**：至少两种机型（相机/相册权限、字体、安全区、离线恢复、Worker 停止与重启）|属用户配合项|
|Agent 双人盲评|**未开始**：33 条固定案例 × 2 名独立评审，需真实 DeepSeek 在线回答采集 + 双人评分（评审模板已存在：REVIEWER_AUDIT CSV/XLSX 空白模板）|调用真实 DeepSeek 有 API 成本，且需要用户提供评审员，启动前须用户授权|
|小规模试用|**未开始**：知情同意的非医疗用户、冻结观察指标、退出方式与隐私方案|属用户配合项|

## 2026-09-24 主动健康体验升级验收

|检查|真实结果|范围与限制|
|---|---|---|
|Backend / SQLite|130 passed|新增 Agent v3.1 用户反馈、同日去重、失效反馈拒绝、24 小时反馈回显和双人人工评审聚合；既有 Agent、RAG、本地模型回落及 API 继续全绿|
|Worker|90 passed|与 2026-09-23 能力范围一致，本轮未启动任何训练|
|小程序|19 passed|主动提醒反馈、反馈回显、运行画像与空样本口径纳入自动检查；既有任务恢复、页面精简和六动作入口继续全绿|
|JavaScript / JSON|PASS|新增健康提醒页面、展示适配工具、首页并行加载与聊天轨迹消费均通过语法检查；`app.json` 可解析|
|能力前台闭环|PASS（代码与自动检查）|首页提醒 → 详情依据/建议 → 具体行动或带上下文进入健康助手；仍需微信开发者工具与真机视觉验收|
|SQLite 增量迁移|PASS|验收脚本从 0003 旧行升级到动态识别的唯一 head `0018_seed_knowledge_documents`，重复升级不变且旧数据保留；已修复旧脚本硬编码 0017 与异常路径文件锁|
|仓库检查|PASS|69 个 JSON、36 个 JavaScript、392 个源码/文档文件；语法、秘密模式、弃用 UTC 调用与损坏标签扫描通过|
|双人人工评审附件|PASS（空白模板）|CSV 66 行 / 33 案例 / A-B 双槽；XLSX 含说明、完成度概览、评分校验和条件格式，视觉与公式错误扫描通过；未填分，不是质量成绩|
|v8 交付包|PASS|`healthmate-delivery-v8-final.zip` 包含 Agent v3.1 反馈闭环、双评审工具链/模板及本轮测试文档；排除 `.env`、数据库、虚拟环境、预览图、缓存、日志、上传文件、旧 zip 与外部模型/数据|

本轮没有改数据库 schema，迁移 head 仍为 `0018_seed_knowledge_documents`；没有运行 SlowFast 或 Food-101 训练。评审模拟评分、扣分风险与上线优先级见 [`REVIEWER_AUDIT_AND_ROADMAP_2026-09-24.md`](REVIEWER_AUDIT_AND_ROADMAP_2026-09-24.md)。

## 2026-09-23 本轮验收

|检查|真实结果|范围与限制|
|---|---|---|
|Backend / SQLite|103 passed|含云端直连识餐、云失败回 Worker、六类动作协议、RAG 检索器回归与新迁移、Agent 固定集评测（7 项）；未代替生产 MySQL 验收|
|RAG 正式评测（rag-v1）|49 条：Hit@3 100%、Hit@1 75.61%、MRR 0.874、拒绝率 100%、来源完整率 100%|查询集冻结哈希 `90ed3472…`；报告 [`benchmark-results/rag-v1/report.md`](../benchmark-results/rag-v1/report.md)；词法边界（好处/危害 vs 疾病指南）如实标注|
|Agent 固定集评测（agent-v1）|31 条：意图识别 100%、安全拦截 100%、结构契约 100%、降级回退 100%|mock provider 离线评测，不调用真实 DeepSeek；报告 [`benchmark-results/agent-v1/report.md`](../benchmark-results/agent-v1/report.md)；评测中发现并修复 3 类安全绕过变体（能停吗/晕倒/500卡）|
|Worker|90 passed|含六类规则、三档 `AI_MODE`、云失败回落、识餐营养报告、Food-101 候选提示和真实 OpenCV/MediaPipe 依赖|
|小程序|9 passed|原 7 项加主页面精简与禁用术语 2 项；仍需微信开发者工具和真机视觉验收|
|仓库检查|PASS|64 个 JSON、30 个 JS、356 个源码/文档文件；秘密模式、语法和未完成标记扫描通过|
|SQLite 增量迁移|PASS|0003 旧数据升级到 `0017_motion_six_action_baseline`，重复升级无变化|
|真实动作固定集|120/120 处理成功|REHAB24-6，六类各 20，受试者 7/8/9；全样本准确率 41.67%、已接受准确率 64.94%、Macro-F1 54.15%、覆盖率 64.17%|
|真实新动作 smoke|PASS|REHAB24-6 腿外展 41 个有效采样、手臂侧平举 106 个有效采样；只证明真实解码/推理，不证明次数正确|
|真实识餐固定集|42 张，34 张有效结果|Nutrition5k 本地 RGB 子集；完成率 80.95%、热量 MAE 94.73 kcal、MAPE 72.70%，因此只登记为待改进基线|
|数据登记|PASS|6 项资源，记录外部路径、字节数、内容或清单哈希、用途及许可复核状态；外部数据不入包|
|交付包|PASS，17,855,295 bytes，SHA-256 `C4317CEA95C2D824B065ACEE4BDBC2C20A6CE6840F158790B68B1DCEC43110DE`|重新生成 `healthmate-delivery-final.zip`（399 个条目）；旧包 `healthmate-delivery-final.zip` 已改名 `healthmate-delivery-20260923-pre-ai-opt.zip` 保留审计；含 0018 知识迁移、rag_queries/agent_queries、rag-v1/agent-v1 报告、AGENT_EVALUATION.md；无 .db/.env/.venv*/旧 zip|

动作报告 SHA-256：`7bee63cc716b2d2835ec952fa0cc6017516fd95a45a9bf1b74e49fc0d9c4b575`，样本量 120。完整报告见 [`benchmark-results/motion-v1/report.md`](../benchmark-results/motion-v1/report.md)。

识餐报告 SHA-256：`fd3f52362fd85336cd055b0c6cf95932b3f4e0c16e8308b7d43222c70a8606ae`，样本量 42。完整报告见 [`benchmark-results/food-v1/report.json`](../benchmark-results/food-v1/report.json)。Nutrition5k 没有标准菜名类别，Top-1/Top-3 使用冻结食材代理标签且本次均为 0%，不能宣传为菜品分类准确率。因 MAPE 超过 30%，已加入可选的预训练 Food-101 候选层；尚未安装其额外训练运行时或重跑改进版结果。

云端直连识餐已用受控响应验证同步完成、失败静默回队列和结果持久化；没有对已上线生产服务发起写入型验收。Worker 提供 `--self-check` 与两个 watchdog 启动脚本。`AI_MODE=local_first/cloud_first/off` 的路由和回落均有自动测试。

SlowFast-R50 权重已按完整 SHA-256 登记，但当前 Python 环境没有 PyTorch/MMAction2，未执行六类微调，也没有生成 TorchScript；迁移把它标为 `not_finetuned` 且不激活。比赛演示必须使用已测规则基线或后续真实训练产物，不能把 Kinetics-400 预训练权重宣称为六类模型。

## 2026-09-21 及此前审计记录（保留）

## 环境

- 后端测试：Windows Python 3.13.9；Docker后端 Python3.12（python:3.12-slim）。
- Worker：Windows Python3.12.14，英文路径虚拟环境；OpenCV4.11.0、MediaPipe0.10.21、NumPy1.26.4、Pillow11.3.0。
- Docker29.4 / Docker Desktop Linux engine；MySQL8.4 容器。
- NVIDIA GeForce RTX4060 Laptop GPU，8188MiB，驱动610.62；MediaPipe实际使用CPU XNNPACK。
- 本地VLM `127.0.0.1:1234/v1` 未运行；food_vision OFF；torch未安装（姿态不需要）。
- 仓库根目录没有 Git 元数据；仅 `backend/` 有 git（4 个提交），无法对 ai-worker/miniprogram/docs 提供历史 diff；检查基于当前文件。

## 已执行结果

|检查|真实结果|范围|
|---|---|---|
|Backend tests / SQLite|93 passed，第三方/SQLite适配器 warning|新增逐项识餐校正/持久化、关键帧图片校验/展示去重与7天清理；其余含配置、鉴权、API、队列、隐私、时区、动作识别、Agent和限流|
|Backend tests / MySQL8.4|70 passed（2026-09-18基线）|使用专用healthmate_audit_tests；0009及其后新增闭环需在交付环境重新执行|
|Worker tests|78 passed|新增逐食材一致性、DeepSeek视觉provider、匿名关键帧渲染和识餐Benchmark口径；其余覆盖三类规则识别、动作语义、训练模型安全回退与安全下载|
|小程序自动测试|7 passed|原job刷新、限时退避、失败、unload、删除回执、隔离指针|
|compileall|PASS|backend/app、backend/migrations、ai-worker（排除.venv）|
|Python静态检查|PASS|Ruff E9/F821，语法与未定义名称|
|JSON/JS语法|PASS|61个JSON，29个JS；含小程序全部JS，扫描340个源码/文档文件|
|源码secret模式扫描|PASS|当前源码/examples/docs未发现匹配真实秘密的字面量；不扫描合法本地.env/db/venv、无Git历史；启发式不是绝对证明|
|业务datetime.utcnow/TODO/FIXME扫描|PASS|生产源码无弃用调用/未完成标记；deferred列在FIX_REPORT|
|Compose配置|PASS|docker compose config --quiet（必填变量使用本地测试随机值）|
|Docker build|PASS|根目录docker build -t healthmate-api ./backend|
|Docker实际运行|PASS|production配置结构测试fixture，PORT8091，live200/ready200(mysql/cloud_ref)|
|Docker数据库中断/恢复|PASS|只停止本轮自建审计MySQL；live200/ready503，恢复后ready200|
|Docker Web重启|PASS|重启后ready200，日志没有Running upgrade/自动DDL|
|Docker Worker鉴权|PASS|缺Token401、有效Token200；日志不含随机密钥|
|Doctor / Worker启动|PASS（姿态）|实际连接本地DockerAPI、真实heartbeat、worker --once空队列0；VLM OFF|
|FastAPI本地启动|PASS|实际Uvicorn127.0.0.1:18081，SQLite已迁移，live200/ready200|
|MySQL fresh migration/current/repeat|PASS（0008基线）|独立空库healthmate_fresh_audit；新增0009、0010、0011需上线前重跑MySQL迁移验收|
|MySQL incremental migration|PASS（0008基线）|healthmate_incremental旧数据保留；新增0009、0010、0011需上线前重跑MySQL增量验收|
|SQLite incremental migration|PASS|脚本临时库，0003旧行→0011 head→重复head，数据保留，Windows临时库可清理|
|Windows依赖安装/pip check|PASS|英文路径Python3.12Worker；backend依赖也成功安装|
|真实MediaPipe解码/推理|PASS|公共姿态图生成2秒静态MP4，16采样/有效率1.0/reps0，无伪造次数|
|真实HTTP→任务→实际Worker→MediaPipe→done|PASS|脚本真实上传视频、领取/续租/回传、用户读取结果；临时DB/视频/进程清理成功|
|生产缺配置预检|PASS（拒绝错误配置）|缺DATABASE_URL等时非零退出，逐字段修复提示，不输出秘密、不连接SQLite|

## 重跑命令

```powershell
# 根目录：JSON/JS/秘密模式/UTC检查
backend\.venv\Scripts\python.exe backend/scripts/verify_repository.py
backend\.venv\Scripts\python.exe -m compileall -q -x '[.]venv' backend/app backend/migrations ai-worker
node --test miniprogram/tests/jobPolling.test.js

Set-Location backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/verify_migrations.py
# 仅用专用测试库；脚本会拒绝其他数据库名
$env:TEST_DATABASE_URL='mysql+pymysql://TEST_USER:URL_ENCODED_PASSWORD@127.0.0.1:13307/healthmate_audit_tests?charset=utf8mb4'
.\.venv\Scripts\python.exe -m pytest -q
Remove-Item Env:TEST_DATABASE_URL

Set-Location ../ai-worker
& ..\ai-worker\.venv\Scripts\python.exe -m pytest -q
& ..\ai-worker\.venv\Scripts\python.exe scripts/verify_local_motion.py

Set-Location ..
docker build -t healthmate-api ./backend
```

Worker测试先安装requirements-dev.txt；真实motion脚本还要求backend/.venv已安装后端依赖、公网能下载Google公开姿态图和空闲本地18082端口。该脚本是静态视频smoke，不是运动准确率基准。使用开发/test SQLite临时文件，生产不会走此路径。

`backend/scripts/verify_container.py` 是本轮本地审计工具，要求已构建镜像、**本轮自建** `healthmate-audit-mysql`、localhost13307和脚本内明确的测试fixture密码，暂时停该审计DB再恢复，并清理审计API容器；不要指向业务数据库/生产环境。其中WeChat配置是假的测试字段，验证配置形状和启动约束，不能证明真实微信身份交换成功。

MySQL增量脚本 `MIGRATION_TEST_DATABASE_URL` 必须指向空的专用healthmate_incremental库，否则拒绝运行。已有库已完成检查，不应清空业务库为重跑测试。

## 覆盖和告警

后端API闭环用测试Worker领取/进度/complete，验证用户获取识餐/姿态结果、校正、显式确认、幂等保存、删除、过期旧lease409、并发领取和URL原地刷新。另有真实Worker/MediaPipe HTTP闭环。测试MockTransport只模拟受控微信/DeepSeek/VLM错误输入，不作为已部署AI的证明。

Worker测试覆盖models2xx/401/404/模型缺失、原生Pose不可用降级、JSON嵌套/歧义、数值/字符串tips、EXIF/尺寸/体积、SSRF DNS/多播/重定向/下载限流、三个analyzer周期与遮挡、401不重试、claim requestID复用、长阻塞推理续租和临时文件finally清理。

保留1条第三方Starlette TestClient使用AnyIO弃用别名的DeprecationWarning；没有抑制warning、没有业务datetime.utcnow调用。MediaPipe原生运行可能输出反馈张量/投影提示，是上游模型运行信息，不能当成临床精度证据。

验证过程中修复：默认镜像源TLS错误（使用正常官方源，未关闭SSL）、中文venv原生模型路径失败（英文venv）、Alembic连接池导致Windows临时SQLite文件锁（dispose）、Windowsvenv启动器产生子进程（审计脚本停止自己创建的完整进程树）、本机环境代理影响localhost（明确trust_env=false）。前两次不完整smoke不计PASS；最终重新执行退出0后才登记通过。

## 未实际验证

- 正式微信云托管发布/扩容和真实微信wx.login（缺项目AppSecret和可用云资源配置）。
- 微信开发者工具WXML编译、真机UI/权限、callContainer、CloudBase上传/getTempFileURL/真实删除；JS语法通过不能替代这些验证。
- 可选本地VLM和 Food-101 分类器的真实推理、显存与耗时；本轮实测的是 DeepSeek 基线。
- 真实token streaming（默认关闭）；识餐已跑固定 42 图基线，但没有写入生产业务数据。
- 复杂遮挡、低光、自由机位和独立外部动作集；本轮六类结果只对登记的 REHAB24-6 固定测试集有效。
- MySQL5.7/8.0与Python3.11实机；当前实测MySQL8.4、Worker3.12。
- CloudBase管理SDK服务端删除证明/孤立文件自动对账、事件JPG长期artifact存储；当前仅有任务内受限预览，尚未真机验证Data URI兼容性。

拥有者上线前按DELIVERY_CHECKLIST逐项完成；不能把本轮本地通过换算成上述未验证项通过。

审计环境保留：healthmate-api镜像、backend开发虚拟环境，以及英文路径 `C:/Users/17875/.codex/runtimes/healthmate-worker-py312`。本轮自建MySQL审计容器可停止后保留数据库用于复查（重跑先docker start healthmate-audit-mysql）。正式部署不会使用测试fixture密码。临时API容器、motion smoke进程和本次成功smoke临时素材已清理。

自动审批拒绝了包含删除旧失败smoke临时目录的清理命令（只返回blocked by policy，未给更具体原因）；未执行该命令，也没有换工具绕过。backend/audit-sqlite.db及首次失败smoke的少量临时文件保留为审计产物，成功重跑的临时文件已由脚本正常清理。
