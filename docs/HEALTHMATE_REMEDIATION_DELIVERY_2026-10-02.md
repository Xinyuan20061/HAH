# HealthMate 全链路整改交付报告（对照 2026-10-02 规格）

> 规格：`docs/HEALTHMATE_FULL_REMEDIATION_DEVELOPMENT_SPEC_2026-10-02.md`
> 迁移基线：以 `alembic heads` 命令结果为准（本文不手写修订号，避免过期引用）
> 原则：本文只写已经执行并通过的证据；未执行、未达标、需要真实数据或真机才能完成的部分，单独在 §4 如实列出，不并入"已完成"。

---

## 1. 验收命令与结果

| 范围 | 命令 | 结果 |
| --- | --- | --- |
| 后端 | `backend\.venv\Scripts\python.exe -m pytest -q` | **337 passed**, 0 failed |
| Worker | `ai-worker\.venv\Scripts\python.exe -m pytest -q` | **156 passed**, 0 failed |
| 小程序 | `node --test miniprogram/tests/*.test.js` | **111 passed**, 0 failed |
| Worker 严格预检（英文 venv） | `D:\HealthMate\.venv\Scripts\python.exe ai-worker\scripts\strict_doctor.py --strict --offline` | **exit 0**，全部 5 项 OK |
| OpenAPI 治理 | `python backend/scripts/audit_openapi.py` | OK：`routes=129 operations=132`，operationId 唯一、无重复注册、5 个弃用端点均有移除计划 |
| 迁移 head | `python backend/scripts/audit_migration_head.py` | OK：单一 head，文档未手写过期 head |
| 隐私覆盖 | `python backend/scripts/audit_privacy_coverage.py` | OK：所有敏感列已进入导出/删除/保留期清单 |
| 数据一致性 | `backend/tests/test_release_audits.py::test_consistency_audit_detects_real_corruption` | OK：能真实发现重复时间线 / 孤立引用 / 越权媒体 / P0 账户残留 |
| 小程序 UI | `node scripts/audit_miniprogram_ui.mjs` | HIGH=0；MEDIUM=2（均为改动前既有），LOW=5（既有 dead-css） |
| 仓库自检 | `python backend/scripts/verify_repository.py` | OK：660 个源文件，无密钥模式、无废弃 UTC 调用 |
| Worker 能力烟测 | `python ai-worker/scripts/smoke_capabilities.py` | OK：capability 与引擎实测一致，回执契约通过；英文 venv 下 `publishable` 含 `motion_pose/motion_unified_v1/motion_unified_v2` |
| Worker 严格预检 | `python ai-worker/scripts/strict_doctor.py --strict --offline` | **英文 venv 通过（exit 0）**；仓库内中文 venv 仍 FAIL（原因见 §4.1） |
| Motion V2 端到端 smoke | `D:\HealthMate\.venv\Scripts\python.exe ai-worker\scripts\smoke_motion_v2.py` | **exit 0**：六个 V2 分组齐全、回执无图像字节、worker 本地契约通过、**backend `MotionWorkerResultV2` 接受该回执** |

严格预检两种环境实测对比（同一份源码，源码路径均为中文 `D:\学习资料\...\ai-worker`）：

```
A) 仓库内中文 venv  ...\ai-worker\.venv\Scripts\python.exe        → exit 1
   [FAIL] interpreter_path_ascii      interpreter=...\ai-worker\.venv\...  (ascii=False)
   [FAIL] mediapipe_pose_inference     FileNotFoundError: ...\site-packages\mediapipe/modules/pose_landmark\pose_landmark_cpu.binarypb
   effective=['food_vision','kinetics400']

B) 英文 venv        D:\HealthMate\.venv\Scripts\python.exe        → exit 0
   [OK]   interpreter_path_ascii      interpreter=D:\HealthMate\.venv\...  (ascii=True)
   [OK]   mediapipe_pose_inference    MediaPipe 原生库加载并完成一次推理
   [OK]   ffmpeg_or_opencv_decode / motion_v2_receipt_schema / capability_consistency
   [ADVISORY] 源码路径非 ASCII（部署建议 C:\HealthMate\worker）
   effective=['food_vision','kinetics400','motion_pose','motion_unified_v1','motion_unified_v2']
```

新增/修改的回归测试（WP0 红灯基线，规格 §14）：

```
backend/tests/test_api_contract_governance.py       # API-01 / API-02 / DOC-01
backend/tests/test_diet_record_lifecycle.py         # FOOD-02 / FOOD-03 / API-03
backend/tests/test_motion_v2_live_contract.py       # MOTION-01..04
backend/tests/test_agent_action_proposals.py        # HARNESS-01 / §8
backend/tests/test_action_protocol_unification.py   # WP3 退出条件（三处写操作统一）
backend/tests/test_release_audits.py                # §13 持续审计 + 0026 回滚
miniprogram/tests/pageReachability.test.js          # FOOD-01 孤立页面
miniprogram/tests/foodRecordLifecycle.test.js       # FOOD-01/02/03/05
miniprogram/tests/motionReanalysis.test.js          # MOTION-03/04 + §8.5
```

---

## 2. 工作包落实

### WP0 红灯测试基线

先写测试、确认失败，再改代码。首次运行新测试：小程序 19 项失败、后端 16 项失败，全部对应规格 §3 已确认缺陷；实现后全部转绿。

### WP1 路由与饮食闭环

| 问题 | 修复 |
| --- | --- |
| FOOD-01 孤立页面 | `pages/records/index` 新增"查看明细"入口 + 每个餐次可按 `meal_type/date` 点入 `/pages/records/diet`；识餐保存后跳转具体记录。可达性由 `pageReachability.test.js` 全页面扫描守住 |
| FOOD-02 无编辑接口 | 新增 `GET/PATCH /diet/records/{id}`；`DietRecordPatch` 强制 `version`；CAS 更新，影响行数为 0 返回 `409 DIET_RECORD_VERSION_CONFLICT` 并带 `current_version` |
| API-03 固定 50 条 | 改为游标分页（`next_cursor/has_more`，默认 20 上限 50），支持 `date_from/date_to/meal_type`；`cursor` 只编码 `(recorded_at, id)`，篡改返回 422 `INVALID_CURSOR` |
| 时间线重复 | PATCH 改写同一条 `ref_type=diet,ref_id=record_id` 事件；`diet_record_updated` 只记录字段名 |
| 删除 | 级联删除该记录时间线、把来源 `FoodAnalysisSession` 置 `record_deleted`、写 `AgentActionAudit(diet.record.delete)` |
| FOOD-03 固定 `meal_type=other` | 识餐页新增餐次 picker（默认按北京时间推断，用户可改）；`finalize` 只在客户端未指定时推断；`meal_type` 由 `MealType` Literal + 迁移 0026 的 CHECK 约束双重收口 |
| FOOD-04 | 已有 42 图基线只作为旧基线；`README`/文案未升级为"营养分析"。真实新基线见 §4.2 |
| 识餐 finalize | 返回 `record` 快照（`id/name/meal_type/calories/version`）；重复请求 `already_finalized=true` 且同一条记录 |
| 小程序 | `utils/request.js` 新增 `patch`/`postIdempotent`，错误对象统一暴露 `code/retryable/details/requestId`；饮食明细页重写为"日期 + 餐次 + 游标 + 逐条查看/编辑/删除"，来源只显示"图片估算，已确认 / 已人工修改"；版本冲突提示刷新；识餐四步改为 `识别 → 校正 → 确认 → 已保存` |

### WP2 Motion V2 契约收口

| 问题 | 修复 |
| --- | --- |
| MOTION-01 字段不一致 | `POST /worker/jobs/{id}/preview-upload-urls` 冻结返回 `uploads`，同版本保留 `urls` alias；Worker 改读 `resp.get("uploads") or resp.get("urls") or []` |
| MOTION-02 丢证据 | 新增 `app/services/motion/evidence_v2.py`：`evidence_from_worker_v2()` 按冻结契约校验并把六个分组映射进内部证据；`_evidence_from_receipt` 不再读 V1 的 `recognition/pose/score`，未识别 schema 版本直接报错而不是静默降级 |
| V2 schema 与真实 Worker 对齐 | `MotionWorkerResultV2` 补齐 Worker 实际发出的 `pipeline_version / model_versions / external_provider_calls / cloud_review_mode / source`、`video_quality.fps/total_frames`、`pose_evidence.sample_count/keypoint_valid_rate`、`recognition_candidates.class_index`；`extra="forbid"`，未知分组 422；迁移窗口的 V1 镜像键被显式声明但**忽略** |
| MOTION-03/04 子任务 | `_spawn_child_run(..., exercise_hint=)`：hint 必须通过目录校验（否则 422 `UNKNOWN_CATALOG_ID`）并写入 child 的 `requested_type`；前端立即切换 `analysisId=child.analysis_id`、清空 `vm/timelineFrames/activeFrame`、轮询 child，并提供"查看上一次结果"而不混合父子证据 |
| §7.4 用户确认独立存储 | `confirm-label` 改为只写 `motion_user_feedback`，不再改写计算出的结果快照（`user_selected` 无法被误当成模型识别成功） |
| MOTION-05 中文路径 | 见 §4.1：本机实测确认 MediaPipe 无法从中文路径加载；新增严格预检把这件事变成发布门禁而不是隐性降级 |
| MOTION-06 | 真实视频报告与人工复核见 §4.3 |

### WP3 Harness Action 闭环与持久运行

* **持久提案**：新表 `agent_action_proposals`。`Tool Registry` 的写操作 handler 不再返回空壳 `approval_required`，而是校验 action 专属 Pydantic 参数、生成 `payload_hash`、落库并返回 `proposal_id`。
* **统一 Action API**：`GET/POST /agent/actions/{proposal_id}`、`/confirm`、`/reject`。过期 410、非本人 404、载荷哈希不符 409、`privacy.account.delete` 必须专用二次确认短语、重复 confirm 返回首次结果且不重复写入（由测试逐条断言）。* **有界运行与阶段**：新表 `health_agent_run_stages`（`UNIQUE(run_id, stage_key, attempt)`）。`respond()` 在第一次 provider 调用**之前**创建 run 并置 `routing`，每个阶段独立提交，失败/取消留下可查记录；`GET /agent/runs/{id}` 返回阶段账本，`/cancel` 只停止未开始阶段并如实说明已发出的请求无法撤回，`/retry` 从失败阶段重来且不自动重放非幂等写。
* **快路径与预算**：`deterministic_route()` 先用确定性单领域判断（命中则不调用路由模型）；每轮全局预算 5（安全短路 0）；单 Worker 且无冲突、无 action 时 Decision 与 Worker 合并；`trace.model_calls` 与 `trace.budget` 为真实计数。
* **并行 Worker**：`asyncio.gather(..., return_exceptions=True)`，单 Worker 失败只产生 `error` 报告，Decision 拿到剩余证据并明确缺口。
* **诚实流式**：`/respond/stream` 改为 NDJSON 阶段事件流（`meta` → `stage`* → `answer` → `delta`* → `done`）。最终答复先过安全复核再输出；`answer` 事件携带完整已复核文本，`delta` 仅为展示动画；小程序状态文案直接使用真实阶段标签。
* **前端**：`pages/chat/index.js|wxml` 新增 `actions[]` 确认/拒绝卡（pending/executing/executed/rejected/expired/failed 六态），只从 `actions[]` 渲染，不从 reply 文本猜测可执行动作；新增真实阶段 chip。
* **三处写操作统一协议**：`plan.apply`、`goal.adjustment.apply`、`diet.ai.finalize` 都走 propose → confirm；`/agent/runs/{id}/apply-plan` 与 `/health/goals/dynamic/{id}/apply` 保留单击 UX，但内部复用同一提案协议与同一执行器，重复点击不重复写入（`test_action_protocol_unification.py`）。

### WP4 隐私与媒体治理

* **删除账本**：新表 `media_deletion_tasks`（`task_id/storage_backend/storage_key_hash/receipt/attempts/verified_at`）。`user_id` 故意**不是外键**——账本必须比被删除的账户活得更久（否则删除账户直接 FK 失败，已由测试覆盖）。
* **服务端验证**：`verify_deletion()` 只有在"服务端观察到对象不存在"或"拿到平台成功回执"时才置 `verified`；对象确实还在 → `failed`+退避；无法观测 → `manual_review`。客户端回执只推进到 `requested`。
* **账号删除**：`DELETE /privacy/account` 改走 `finalize_account_deletion()`，返回 `server_verified` / `manual_review` 计数与 `verification = server_verified | partial`，不再伪报成功；新增 `GET /privacy/deletion-status`。
* **对账**：`reconcile_media()` 报告本地对象缺失、过期预览、以及**诚实的限制**——服务端不持有云存储列举凭据，因此孤立对象为 `unverifiable` 而不是编造数量；账户已删但仍有对象计为 P0。
* **保留期**：7 天预览、Provider 原始响应不长期保存，记入 `ARCHITECTURE.md` 与隐私清单。

### WP5 门禁与审计脚本

新增脚本（规格 §13 建议清单）：

```
backend/scripts/audit_openapi.py            # operationId 唯一、重复路由、弃用端点移除计划
backend/scripts/audit_migration_head.py     # 单一 head；文档不得声称过期 head
backend/scripts/audit_data_consistency.py   # 外键/重复时间线/孤立引用/数值；隐私清单
backend/scripts/audit_privacy_coverage.py   # 敏感列必须分类（唯一清单，避免两套分类漂移）
ai-worker/scripts/strict_doctor.py          # --strict：路径/解码/MediaPipe/回执/能力一致性
ai-worker/scripts/smoke_capabilities.py     # capability 只由引擎实测 + 回执契约产生
miniprogram/tests/pageReachability.test.js  # 页面可达性扫描（等价于 scripts/audit_page_reachability.mjs）
```

`audit_data_consistency` / `audit_privacy_coverage` 的逻辑同时由 `backend/tests/test_release_audits.py` 在**真实迁移到 head 的临时数据库**上执行，并注入人为损坏行验证告警真的会触发；`0026` 迁移的前进/回滚双向验证也在同一文件（规格 §15.1）。

---

## 3. 发布口径（§17）

整改后可声明的能力范围与文案：

* 识餐页面文案："图片估算草稿，确认后才写入记录"；未通过真实门禁前不得称"营养分析"。
* 动作页面文案："基于可见画面的一般健身反馈"；MediaPipe 不可用时显示"当前设备不可用"，不显示评分。
* Agent：离线结构契约与真实模型评测分开表述；真实评测未完成前只称"结构契约通过"。
* 动作 `motion_unified_v2` 只由通过 `strict_doctor --strict` 的 Worker 注册；后端不会把 unified 任务派给只声明 `motion_pose` 的 Worker（`claim_next_job` 能力匹配已保留并加注释）。

---

## 4. 未完成项（必须在发布前补齐，不得算作已完成）

### 4.1 MOTION-05：已定位为「门禁缺陷 + 环境事实」两件事（门禁已修，环境仍需英文 venv）

**先纠正上一版报告的错误**：初版 `strict_doctor.py` 把"源码路径含非 ASCII 字符"当作硬失败条件，这是**误判**。实测确认：

* `Path(__file__).resolve()` 在 Windows 上**会跟随 junction / 重解析点 / subst**，所以
  `C:\...\HealthMate\venvlink\Scripts\python.exe` 被解析回
  `D:\学习资料\...\ai-worker\.venv\Scripts\python.exe`——被 junction 指向的路径无法被"洗成"ASCII，
  用它当门禁条件等于永远失败。
* MediaPipe 真正加载失败时，报错文件是 **venv 内部**的
  `...\.venv\Lib\site-packages\mediapipe/modules/pose_landmark/pose_landmark_cpu.binarypb`，
  即问题在**解释器/venv 所在路径**，不在源码目录。而 `start_worker.ps1`（23–41 行）的 `venvlink`
  junction 恰好修的就是这一层。

因此已把门禁改为**只对可验证的事实设卡**：

| 检查项 | 语义 | 是否阻塞发布 |
| --- | --- | --- |
| `interpreter_path_ascii` | 解释器（原生库资源）所在路径 | 是（解释器非 ASCII 时） |
| `mediapipe_pose_inference` | 原生库真实加载 + 完成一次推理 | **是（决定性证据）** |
| `ffmpeg_or_opencv_decode` / `motion_v2_receipt_schema` / `capability_consistency` | 解码 / 回执契约 / 能力诚实性 | 是 |
| 源码路径非 ASCII | 部署目标（规格 §7.5） | 否，输出 `[ADVISORY]` |

修复后实测：**英文 venv 的严格预检 exit 0，`effective_capabilities` 恢复为
`motion_pose / motion_unified_v1 / motion_unified_v2`**。并新增
`ai-worker/tests/test_strict_doctor_gate.py`（6 项）锁住这个区分，防止误判回归。

**仍然存在的环境事实**（不是代码缺陷）：仓库内 `.venv` 自身位于中文路径，直接用它启动 Worker 时
MediaPipe 无法加载。本机可用 `start_worker.ps1` 的 venvlink workaround 正常运行；正式部署按规格
§7.5 使用纯英文绝对路径。

**推荐的本机命令**（workaround 与门禁同时成立）：

```powershell
# 源码可留在中文路径；解释器必须位于纯英文路径
$py = 'D:\HealthMate\.venv\Scripts\python.exe'
& $py ai-worker\scripts\strict_doctor.py --strict --offline
& $py ai-worker\scripts\smoke_capabilities.py
& $py ai-worker\scripts\smoke_motion_v2.py            # 端到端 V2，含跨端契约校验

# 或直接用现有 workaround 启动常驻 Worker（它会自动建 venvlink）
powershell -File ai-worker\start_worker.ps1
```

注意：`C:\HealthMate\aiworker-venv` 是**空 venv**（只有 Python 3.12.10，无 mediapipe/numpy/cv2），
不能用于门禁；可用的是 `D:\HealthMate\.venv`（mediapipe 0.10.21 + numpy 1.26.4 + opencv-contrib 4.11.0，
与 FIX_REPORT 版本一致）。

**正式部署（发布门禁目标）**：把源码与 venv 都放到纯英文路径，此时连 advisory 也会消失：

```
C:\HealthMate\worker   C:\HealthMate\venv   C:\HealthMate\models
```

在此之前：本机动作能力可正常使用（经 venvlink 或英文 venv），但**不得**把"中文路径下直接跑仓库内 venv"
当作可用安装；界面能力文案仍由 `effective_capabilities()` 决定，未通过即显示"当前设备不可用"。

### 4.2 FOOD-04 / §6.6：识餐真实质量门禁未执行

需要"≥100 张冻结测试集 + 覆盖中式混合餐/单品/汤类/遮挡/外卖盒/无比例尺 + 分级报告"，这需要真实标注数据与可用的视觉 provider 计费调用。本仓库没有该数据集，也没有在本次会话中发起真实计费调用，因此**没有生成任何新的准确率数字**。

已就绪的机制：低置信/缺比例尺/高不确定性在客户端强制进入校正确认（`correctionGate`），未识别时只提供"手动记录"，不生成伪造营养值。数值报告待真实数据补齐后，用冻结报告替换文案。

### 4.3 MOTION-06 / §7.6：真实视频报告与双人人工复核未执行

需要 ≥30 个自采、已授权、与调参集独立的视频与双人复核。本轮补齐了报告需要的分层维度契约（动作/机位/遮挡/多人/光照/时长、覆盖率、unknown 召回、错误拒识率）与"Kinetics 只作候选、不单独触发评分"的约束，但没有真实样本，因此未产出报告。

### 4.4 EVAL-01 / §12.7：Agent 真实模型双人评审未执行

需要真实在线回答 33 条固定集 + 每条两名独立评审。本次环境未配置可计费的 provider Key，`benchmark-results/agent-*/` 中的双人人工评审仍为空。**不得**把结构契约测试通过写成准确率或安全达标。

### 4.5 PRIV-01 剩余部分：云对象删除的"服务端证明"

已实现账本、服务端可观测部分的验证、`manual_review` 升级与对账报告。**云端对象的列举/删除仍需要平台凭据**，服务端目前无法自己证明 CloudBase 对象已消失，因此账号删除会如实返回 `verification=partial` 并把这类任务标为 `manual_review`。要做到 `server_verified`，需要引入一个持有平台凭据的受控删除组件（本规格 §10.2 的完整形态）。

### 4.6 §12.4/§12.5：小程序行为测试与真机 E2E

小程序测试已从纯静态断言扩展到可达性、餐次选择、游标/编辑/冲突、子任务轮询、`actions[]` 卡片、阶段事件等行为断言（111 项）。但**真机 E2E（Android/iOS 各一台、弱网、切后台、超时、Worker 离线）与屏幕录制/脱敏 trace 归档未执行**，需要真机与测试账号。

### 4.7 §11 生产监控告警

`trace` 关联字段（`request_id / harness_trace_id / run_id / health_agent_run_id / ai_job_id / motion_analysis_id / provider_invocation_id / action_proposal_id / action_audit_id`）已在响应与账本中就位，但**生产监控与告警系统本身未部署**（无 Prometheus/告警通道），因此"生产监控和告警已配置"这一条未达成。

### 4.8 已知遗留（既有、非本轮引入）

* 小程序 UI 审计仍有 MEDIUM=2（`settings/ai` 三个未定义类）与 LOW=5（`media`/`scan`/`goals`/`report`/`evaluation` 的 dead-css）。`HIGH=0`。
* `pages/report` 仍无入口（本轮未触及，规格未列入范围）。
* `backend/audit-sqlite.db`、`backend/healthmate.db` 是历史开发库，未迁移到 0026；审计脚本请用 `--database-url` 指向已迁移库，或用 `tests/test_release_audits.py` 的临时库路径。

---

## 5. 规格 §16 Definition of Done 逐条对照

### 工程

* [x] 后端 337 / Worker 150 / 小程序 111 全量通过
* [x] OpenAPI 无重复 operationId（`audit_openapi.py` + 单测）
* [x] Alembic 单一 head；0026 前进/回滚在 SQLite 验证通过
* [x] 页面可达性扫描无未解释孤立页面
* [x] Producer/Consumer 契约测试使用生产 schema（V2 fixture 直接走 `MotionWorkerResultV2` / `validate_motion_result_local`）
* [x] 无新增高危依赖或密钥泄漏（`verify_repository.py`）
* [ ] MySQL 迁移未在本机验证（本机无 MySQL 实例；0026 已按 MySQL 限制调整：TEXT 列不带 `server_default`）

### 饮食

* [x] 识餐保存可选择餐次
* [x] 保存后能看到具体记录（返回快照 + 保存卡 + "查看记录"）
* [x] 记录可编辑、删除，仪表盘 `onShow` 同步
* [x] 重复 finalize 不重复写
* [ ] 新真实评测报告未完成（§4.2）；页面能力文案当前为"粗略草稿"口径，与现状一致

### 动作

* [x] MediaPipe strict doctor 通过（英文 venv `D:\HealthMate\.venv`，exit 0；源码路径非 ASCII 已降级为 advisory。正式部署仍需纯英文路径，见 §4.1）
* [x] `motion-worker-v2` 回执不被 V1 adapter 丢字段
* [x] 预览上传契约冻结为 `uploads` 并有兼容测试
* [x] 标签确认进入 child 请求，前端轮询 child、不混合父子证据
* [ ] 独立真实视频报告与人工复核未完成（§4.3）

### Harness

* [x] 运行阶段可查询、失败可恢复、用户可取消
* [x] Action proposal 可确认/拒绝/过期
* [x] 重复确认不重复执行（plan.apply / goal.adjustment.apply / diet.ai.finalize 三处均断言）
* [x] 简单请求模型调用数 ≤2（确定性单领域 0 次路由 + 1 次 Worker，单 Worker 合并 Decision），复杂 ≤5
* [x] 阶段事件真实，最终答复经过安全复核
* [ ] 真实模型双人人工评审未完成（§4.4）

### 隐私与运维

* [x] 跨账户隔离测试通过（饮食、动作、proposal、目标建议）
* [x] 云文件删除有服务端验证或明确 `manual_review`，不伪报成功
* [x] 孤立对象对账任务可运行（`reconcile_media`）并有 P0 判定
* [x] 全链 trace 可关联且不含敏感数据
* [ ] 生产监控和告警未部署（§4.7）
* [ ] 回滚演练：数据库层面已做（0026 downgrade 测试）；**生产回滚演练未执行**
* [ ] 真机 E2E 未执行（§4.6）

**结论**：工程契约层、饮食闭环、Harness 闭环、隐私治理与审计门禁已达成并可复现；动作能力受本机中文路径阻塞，四项需要真实数据/真机/生产环境的门禁（识餐真实评测、动作真实报告、Agent 双人评审、真机 E2E 与生产监控）未完成，已在 §4 逐条列出，未以自动化测试通过替代。
