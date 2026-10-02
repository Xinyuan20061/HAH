HISTORICAL: 本文是 H4 多模态升级轮的开发提示词，其中的迁移号（如 `0016_food_and_keyframes`）是当时计划的编号，不是当前 head。当前 head 以 `alembic heads` 命令结果为准。

# HealthMate 4.0 多模态升级 · Codex 开发提示词

> 适用对象：Codex（或同等能力开发代理）。本提示词是完整开发规格，按模块执行，每个模块有独立验收。
> 项目根：`D:\学习资料\计算机应用大赛\health-assistant`（Windows，PowerShell）。
> 交付口径：所有 AI 能力必须"可演示、可解释、可验证"，沿用项目既有的诚实表述原则（规则≠模型、估算≠事实、已验证≠未验证）。

---

## 0. 全局约束（先执行，贯穿全程）

1. **先读再改**：动手前必读 `README.md`、`docs/VERIFICATION.md`、`docs/HEALTHMATE_3_SYSTEM_UPDATE.md`、`最终的项目构想.md`，理解"证据优先、人在回路、安全边界"三条产品原则。
2. **测试红线**：不减少现有测试。后端 `backend\.venv\Scripts\python.exe -m pytest -q`（当前 90 passed）、Worker `ai-worker\.venv\Scripts\python.exe -m pytest -q`（当前 74 passed）、小程序 `node --test tests/jobPolling.test.js`（当前 7 passed）。每个模块交付前全量重跑。
3. **代码风格**：Python 改动用 `backend\.venv\Scripts\python.exe -m ruff check <文件>` 与 `ruff format`；JS 改动用 `node --check`；WXML/JSON 改动保证可解析。
4. **诚实表述**：禁止在代码、文案、注释中声称未验证的准确率；所有 AI 输出保留 provider/置信度/降级标记；不输出医学诊断或康复处方。
5. **安全边界不可削弱**：媒体 SSRF 防护、体积/像素上限、密钥分离、生产拒绝 SQLite、限流、隐私导出/删除闭环——新增代码不得绕过。
6. **数据库迁移**：本任务最多新增 **1 个迁移** `0016_food_and_keyframes`（见 M1/M2），必须同时通过 SQLite 全量/增量迁移测试；改动完成后在 MySQL 专用测试库重跑并记录。
7. **版本管理**：backend 是独立 git 仓库（根目录不是）。每个模块完成即提交（`backend/` 与根目录分别说明），最终产出一份变更清单。

---

## 1. 项目现状速览（关键事实，供 Codex 核对，不得臆造字段）

**架构**：微信小程序 → 微信云托管 FastAPI → MySQL（持久）；媒体走 CloudBase 云存储；本机 AI Worker 通过公网 HTTPS 轮询 `AIJob` 队列（`ai_jobs` 表），领取→下载→推理→complete 回传；`MotionScore`/`MotionEvent`/`MotionSemanticAnalysis`/`FoodAnalysisSession` 持久化结果。

**关键数据流**：

```
小程序上传媒体 → /media/register-cloud → 创建 AIJob → Worker 轮询 /worker/jobs/claim
→ 下载（SSRF 防护）→ analyze_motion / analyze_food → POST /worker/jobs/{id}/complete
→ 后端拆解：MotionScore（评分）+ MotionEvent（事件/关键帧证据）+ FoodAnalysisSession（识餐）
→ 小程序轮询 /media/motion-jobs/{job_id} 与 /vision/food-jobs/{job_id} 展示
```

**已核实的现有结构（开发时以此为准）**：

| 位置 | 关键事实 |
|---|---|
| `ai-worker/healthmate_worker/results.py` | `FoodResult`：dish_name/calories/protein/carbs/fat/fiber/estimated_weight_g/confidence/portion/cooking_method/tips/provider/model/image_sha256/is_estimate/warning/visible_items/portion_basis/calorie_range_low/high/uncertainty_reasons；`model_validator` 强制点估计落在区间内 |
| `ai-worker/healthmate_worker/processors/food.py` | `analyze_food` 调本地 OpenAI-compatible VLM（`LOCAL_VLM_BASE_URL`+`LOCAL_VLM_MODEL`+`LOCAL_VLM_API_KEY`），base64 内联图片，`temperature=0.15`；`preprocess_image` 已做 EXIF 转正/缩图/压缩/体积上限 |
| `ai-worker/healthmate_worker/processors/motion.py` | `extract_metrics` 每采样帧输出 `skeleton`（12 点：left/right shoulder/elbow/wrist/hip/knee/ankle，**归一化坐标** x/y/visibility）+ 关节角；`analyze_motion` 输出 `frames`（含 event/timestamp/角度/reason）+ `pose`（errors：code/label/evidence/severity）+ `score`；README 明示"关键帧当前显示真实事件时间/指标，**尚未持久上传 JPG**" |
| `backend/app/models/models.py` | `MotionScore`（job_id 唯一、evidence_json Text）；`MotionEvent`（event_index/event_type/timestamp_seconds/severity/evidence_json）；`FoodAnalysisSession`（initial_json/corrected_json Text、status、finalized_record_id）；`DietRecord`（单行营养字段 calories/protein/carbs/fat/fiber/weight_g/portion/cooking_method/vision_analysis_id）；`AIJob`（result_json MEDIUMTEXT，生产 MySQL） |
| `backend/app/api/v1/worker.py` | `complete()` 接口：校验 Worker 回传 → 写 MotionScore/MotionEvent；`motion_pose` 任务回传含 `frames` 数组 |
| `backend/app/api/v1/health.py` | `command_center`（3.0 新增，确定性规则，不调大模型） |
| `backend/app/services/agent/orchestrator.py` | 单 Agent：关键词规则路由（detect_intent）+ 工具 `read_context` + `execute_action` 审计 + RAG + 训练护栏；**当前无 DeepSeek Function Calling** |
| `miniprogram/pages/media/index.js` | `a.frames = a.frames.map(...)`：`fullUrl: x.url || ''`、`hasPreview: !!x.url`；无 url 时 `drawSkeleton()` 用 canvas 画 12 点骨骼 |
| `miniprogram/pages/media/index.wxml` | 关键帧展示：有 `hasPreview` 显示 `<image>`，否则 canvas + 角度证据卡 + 教练建议卡（stage/finding/risk/advice） |
| `miniprogram/pages/scan/index.wxml` | 3.0 证据 UI：热量区间、visible_items 标签、uncertainty_reasons、人工校正网格 |
| `ai-worker/.env` | `LOCAL_VLM_MODEL` 当前取值异常（疑似 `LOCAL_VLM_API_KEY=` 串），VLM 服务未启动，`food_vision` OFF——**M3.1 之前先修复此配置** |

---

## 2. 交付目标总览

| 模块 | 内容 | 优先级 |
|---|---|---|
| M1 | 识餐逐项热量证据化：每个可见食材/菜品单独热量与依据，总热量=各项之和 | P0 |
| M2 | 动作关键帧定格圈画 + AI 标注：Worker 生成标注帧图并持久化，小程序展示 | P0 |
| M3.1 | 识餐直连 DeepSeek 视觉 API（provider 抽象，本地 VLM 作回退） | P0 |
| M3.2 | 级联：本地感知 + DeepSeek 解读（识餐/动作两路） | P1 |
| M3.3 | Agent 工具编排：DeepSeek Function Calling 调用 Worker 能力 | P1 |
| M4 | 评测与演示证据包：自建小数据集 + benchmark + 演示脚本更新 | P1 |
| 附加 | 隐私文案、安全防护、兼容兜底、文档与 git 提交 | P0 |

---

## 3. 模块详情

### M1 · 识餐逐项热量证据化

**目标**：识餐结果从"整份餐食一个总热量"升级为"每个可见食材/菜品都有独立热量与依据"，让评委看到"有理有据"而非"给个总热量"。

**改动点**：

1. **`ai-worker/healthmate_worker/results.py`**：
   - 新增 `FoodItem(BaseModel)`：`name`(1-120)、`portion`(默认""，max 120)、`portion_basis`(max 240)、`weight_g`(0-5000)、`calories`(0-5000)、`protein/carbs/fat/fiber`、`confidence`(0-1)、`evidence`(max 300，说明"为什么这么估"：如"可见 1 只鸡腿，去皮后按 120g 估算")、`in_image`(bool，是否图中直接可见)。
   - `FoodResult` 新增 `items: list[FoodItem]`（max 12 项）。
   - `model_validator` 新增校验：
     - 若 `items` 非空：`sum(items.calories)` 与 `calories` 偏差 ≤10% 否则抛错；`calorie_range_low/high` 默认取 `sum(items 各自的区间)`；每个 item 的 `calories` 必须 ≥0。
     - 兼容：模型只返回旧单点结构（无 items）时自动生成 `[items=[整份作为一项]]` 或保持空，不得崩溃。
   - 保持 `extra="ignore"` 与 strict 校验不变。

2. **`ai-worker/healthmate_worker/processors/food.py`**：重写 `PROMPT`，强制逐项输出：
   - 要求"把图片中实际可见的每个食材/菜品分别列出，每项给出 name/portion/weight_g 估算/calories 及 evidence（你依据画面中什么线索估算的）"；
   - "整份餐食总热量 = 各项之和，calories 字段必须是各项之和（±10%）"；
   - 保留现有：visible_items、portion_basis、calorie_range_low/high、uncertainty_reasons、confidence<0.6 强制校正提示、禁止猜测油盐/隐藏配料；
   - 输出 JSON 字段名与 `FoodResult`/`FoodItem` 完全一致。

3. **`backend/app/schemas/ai_results.py`**：同步新增 `FoodItem`、`FoodResult.items`（与 worker 端一致，含相同 validator）。后端回传校验不丢字段。

4. **数据库与接口**：
   - 迁移 `0016_food_and_keyframes`：`diet_records` 加 `items_json`（Text，default `"[]"`，可空）；新增 `motion_keyframes` 表（M2 用，见下）。
   - `vision` 相关接口：`GET /vision/food-analysis/{id}` 返回 `items`（来自 `initial_json`/`corrected_json`）；`PUT /vision/food-analysis/{id}/correct` 支持逐项校正（请求体可含 `items: [{name, calories, ...}]`，按 index 匹配，校正后重算总热量并写 `corrected_json`）；`POST /vision/food-analysis/{id}/finalize` 落库 `diet_records` 时把 `items_json` 写入新列。
   - 旧数据兼容：`items_json` 为空时前端回退整份展示，禁止 500。

5. **`miniprogram/pages/scan/index.*`**：
   - 结果区新增"逐项热量"卡片：每项显示 名称 / 份量 / 热量 / evidence / 置信度（低置信度黄色标记）；底部"合计"= Σ各项，与整份热量并排展示以互相印证。
   - 校正区：支持逐项编辑（至少 name/calories/weight_g），保存后本地重算合计，再走 `correct`。
   - 无 `items` 的旧结果：保持现有整份展示，不报错。

**验收**：
- Worker：新增单测——items 求和容差、区间=Σ、旧结构兼容、超项数拒绝；
- 后端：finalize 落库 items_json、correct 逐项重算、旧会话兼容；
- 真实餐食图（本地 VLM 或 M3.1 的 DeepSeek 视觉）跑通"逐项 → 校正 → 入库"，前端合计与后端一致。

---

### M2 · 动作关键帧定格圈画 + AI 标注

**目标**：把"事件时间+角度数字"升级为"定格画面 + 骨骼叠加 + 圈画纠正区域 + AI 中文标注"的科技感可视化，作为答辩核心画面。

**改动点**：

1. **`ai-worker/healthmate_worker/processors/motion.py`**：
   - 采样循环中，对 `fps` 与 `stride` 已知的前提下，额外记录每个采样帧的**原始帧序号**（`index`），供事后精确抽帧（`cap.set(cv2.CAP_PROP_POS_FRAMES, index)`）。
   - 对 `explain_keyframes` 输出的每个事件帧（`event` 以 `_top`/`_bottom`/`_deepest`/`_completed` 结尾或 `max_rule_risk`），按 `timestamp_seconds` 定位到最近的已采样原始帧。

2. **新建 `ai-worker/healthmate_worker/visualize.py`**：`render_keyframe_annotation(frame_image, skeleton, metrics, annotation, exercise_type) -> bytes (JPEG)`：
   - 骨骼叠加：12 个关键点（归一化坐标 × 帧宽高）绘制半透明圆点 + 连线（MediaPipe 标准连接：肩-肘-腕、肩-髋-膝-踝、双肩、双髋），科技风配色（青/绿系，深色底或半透明遮罩）；
   - 关节角标注：在膝/髋/肘位置绘制角度数值文本（如 `膝 92°`）与角度弧；
   - **圈画纠正区域**：按 `pose.errors[].code` 画高亮——`depth_insufficient` 圈膝髋区域、`trunk_lean` 沿躯干-髋连线画警示框/箭头、`body_alignment` 画肩-髋-踝连线、`back_leg_depth` 圈后腿；`severity=high` 用红色描边，`medium` 橙、`low` 黄；
   - AI 标注角标：顶部条显示 `动作 · 帧号 · 时间戳`；底部显示 `stage / finding / advice`（中文，来自 `explain_keyframes`），含角度数值；
   - 输出 JPEG：长边 ≤960px、质量自适应压到 ≤80KB/帧；失败时返回空并**不阻断任务完成**（关键帧图是可降级增强，不是核心结果）。

3. **回传协议**（推荐方案，避免新接口与事务割裂）：`complete()` 回传的 `frames[]` 每项新增 `image_b64`（JPEG base64，≤80KB）与 `annotation_json`（含 stage/finding/advice/severity/angles）。后端 `worker.py` 的 `complete()` 拆解时写入新表。

4. **数据库**：迁移 `0016` 新增 `motion_keyframes`：`id`、`job_id`(FK ai_jobs, index)、`event_index`、`timestamp_seconds`(Float)、`image_b64`(LONGTEXT, MySQL / Text, SQLite)、`annotation_json`(Text)、`sha256`(String 64)、`created_at`。与 M1 同属一个迁移。

5. **接口**：`GET /media/motion-jobs/{job_id}` 结果 `frames[]` 增加 `url` 生成逻辑——MVP 直接返回 `data:image/jpeg;base64,...`（总数 ≤5 帧 × 80KB，控制体积），同时保留 `skeleton` 字段供 canvas 兜底；生产路线注释：后续换 CloudBase 管理 SDK 上传为真 URL（README 已标注该能力未实现，不得假装已实现）。

6. **`miniprogram/pages/media/index.*`**：
   - `hasPreview` 现在会命中（有 `url`/dataURI），`<image>` 直接显示标注帧图；
   - 帧条（frame-strip）缩略图用同一 dataURI；
   - 点击帧后，标注图 + 现有证据卡/教练卡并存；`drawSkeleton` canvas 兜底保留（无图场景）；
   - 性能：`image_b64` 大字符串避免 setData 全量重复，帧数据只 setData 一次并在 pickFrame 时切换。

**验收**：
- Worker 单测：visualize 模块对模拟 skeleton 生成合法 JPEG、体积 ≤80KB、坐标缩放正确、含中文标注；事件帧缺失时优雅跳过；
- 后端单测：complete 拆 frames 落库 motion_keyframes、接口返回 dataURI、无图旧任务兼容；
- 真实视频 smoke：肉眼检查标注图"科技感"达标（骨骼连线+圈画+角度+AI 文案），≤5 帧、总载荷可控。

---

### M3 · DeepSeek 多模态三路径

> 背景事实（2026-09 核实，官方文档）：`deepseek-flash` 原生接受图片输入（JPEG/PNG/GIF/WebP），图片按 token 计费（一张 ≤384 tokens），支持 base64 内联/外部 URL/Files API，Chat Completions 为 OpenAI-compatible；2026-09-10 发布的 V4.1-Flash 为原生多模态新结构。旧名 `deepseek-v4-flash-vision-exp` 已退役。图片来源：https://api-docs.deepseek.com/guides/vision/

#### M3.1 · 识餐直连 DeepSeek 视觉 API（provider 抽象）

**改动点**：

1. **`ai-worker/healthmate_worker/config.py`**：新增 `vlm_provider: str = "local"`（取值 `local|deepseek`）、`deepseek_vision_model: str = "deepseek-flash"`、`deepseek_api_key`（复用 `.env`）、`deepseek_base_url: str = "https://api.deepseek.com"`；`doctor.py` 与 `capabilities.py` 感知新配置。
2. **`ai-worker/healthmate_worker/processors/food.py`**：`analyze_food` 按 provider 分支：
   - `deepseek`：请求 `{base}/chat/completions`，模型 `deepseek-flash`，messages 含 `image_url` base64 内联（沿用现有 `preprocess_image` 输出），`temperature` 0.15，`response_format: {"type":"json_object"}`；
   - 错误分类沿用现有 `ProcessingError` 码（vlm_auth/vlm_model_missing/vlm_http/vlm_timeout），401/403/429 语义不变；
   - 结果仍走 `FoodResult.model_validate` 严格校验（与本地 VLM 同一条信任链）。
3. **`ai-worker/healthmate_worker/capabilities.py`**：`food_vision` 能力改为——`local` 模式要求本地 VLM 在线；`deepseek` 模式要求 key 非空；两种都不可用时 OFF。capability 名可扩展为 `food_vision`（含义不变，细节进 `capabilities_json` 的 provider 字段）。
4. **`ai-worker/.env`**：修复 `LOCAL_VLM_MODEL` 的异常取值（恢复合法模型 ID 或置空）；新增 DeepSeek provider 配置。修复后本地 VLM 启动并 `doctor.py --vlm-smoke` 通过（M3.2 依赖）。
5. **降级链**：`deepseek` provider 失败（网络/401/429）→ 若本地 VLM 可用则回退本地 → 都失败返回明确错误，前端走人工校正。降级路径必须写日志。

**验收**：真实餐食图走通 `deepseek-flash` → 逐项 items JSON → 后端入库；模拟断网/无 key 验证降级；`doctor.py --vlm-smoke` 双 provider 均可。

#### M3.2 · 级联：本地感知 + DeepSeek 解读

**目标**：Worker 产出结构化证据（识餐 items / 动作关键帧+评分），DeepSeek 文本模型"只看证据、不看原图"，生成用户可读的解读——隐私叙事（本机处理）与云端智能（DeepSeek）并存。

**改动点**：

1. **后端新接口 `POST /api/v1/vision/food-analysis/{analysis_id}/interpret`**：读取该会话 `initial_json`（含 items/区间/uncertainty），调 `deepseek-chat`（复用 `app/services/ai/gateway.py` 的 `get_provider`），输出结构：`{summary, structure:[{item, note}], target_match, suggestions:[...]}`，返回 `provider`；DeepSeek 不可用时返回规则降级摘要（"依据当前记录生成，DeepSeek 暂不可用"）+ `degraded: true`。禁止模型改动任何营养数值。
2. **动作解读 `POST /api/v1/fitness/motion-analysis/{job_id}/interpret`**：输入为 `MotionScore` 的 `evidence_json`（维度分+basis）与 `MotionEvent` 关键帧 annotation（stage/finding/advice），DeepSeek 生成"这次训练最值得调整的一件事 + 下一次动作提示"，同样不可改数值、带 provider 与降级。
3. **`miniprogram/pages/scan/index.wxml` / `pages/media/index.wxml`**：结果区下方新增可折叠"AI 解读"卡（标题、正文、provider 徽标、降级提示）。渲染失败不阻塞主结果。

**验收**：两接口单测（正常+降级）；真实 DeepSeek key 调通一次并记录响应样例；前端展示与降级文案正确。

#### M3.3 · Agent 工具编排（DeepSeek Function Calling）

**目标**：从"规则路由单 Agent"升级为"DeepSeek 推理 + 工具感知"的多模态 Agent，答辩可展示工具调用轨迹。

**改动点**：

1. **工具定义**（新文件 `backend/app/services/agent/tools_cloud.py` 或扩展 `tools.py`）：注册 4 个工具，均只读/只执行已验证动作：
   - `analyze_motion`（触发 Worker 任务，入参 video asset/动作类型，返回 job_id，不阻塞）；
   - `get_food_analysis`（读取某识餐会话 items/区间）；
   - `query_health_data`（今日/近 7 天汇总、streak、动作画像，复用现有 service 层函数，**禁止任意 SQL**）；
   - `get_training_adjustment`（复用现有 `app/services/training_adjustment.py`）。
   每个工具：名称、描述（中文+英文）、JSON Schema 参数、只读标记。
2. **`orchestrator.py` 升级**：保留 `detect_intent` 安全入口（`evaluate_message` 不通过直接拒绝）；在 `plan`/`general` 意图分支接入 DeepSeek Function Calling（`tools` 参数 + `tool_choice`），模型输出工具调用时执行工具 → 结果拼接回上下文 → 二次请求生成最终回答；超时/失败降级回现有规则路径。**所有工具执行仍走 `execute_action` 审计**（`agent_action_audits` 表），工具调用轨迹（tool/args 摘要/结果摘要）写入 `HealthAgentRun` 的 JSON 字段。
3. **`miniprogram/pages/chat/index.wxml/js`**：AI 回复卡片下方展示"本次调用：工具名 ×N"，可展开看轨迹（工具名、入参摘要、结果摘要），作为"可解释 Agent"的答辩证据。
4. **安全**：工具结果一律视为不可信输入处理；模型只能通过工具拿数据，不能要求工具执行删除/修改类操作（本期只读工具，写操作继续走用户显式确认的既有 `execute_action` 通道）。

**验收**：端到端单测（mock 工具：用户问"我上次深蹲怎么样"→ 工具调用 → 结果 → 回答）；真实对话 smoke 一次并记录工具轨迹；安全测试（模型要求删除数据 → 被拒绝/降级）。

---

### M4 · 评测与演示证据包（补充项）

**目标**：把"AI 创新"从"功能完整"提升为"有数字可验证"，同时备好比赛演示资产。

**改动点**：

1. **自建小数据集**（新建 `data/`，不入库）：30–50 段三动作（深蹲/俯卧撑/弓步蹲）自录视频，标注 JSONL：`reps`、人工质量分（0-100）、错误标签（按现有 error code 体系）、两名标注者各标一份用于一致性统计；30–50 张餐食图，标注逐项食材/热量。
2. **benchmark 扩展**：`benchmark/README.md` 与现有评测器增加指标——动作：识别 Macro-F1/拒识率、次数 MAE、关键帧时间定位误差、评分 Spearman；识餐：逐项热量 MAE、项目命中率、总热量 MAE。输出含 SHA-256 与版本戳（沿用现有协议）。
3. **README**：新增"当前实测结果（自建小样本）"表，必须注明样本量与拍摄条件，禁止宣称等同正式准确率。
4. **`docs/COMPETITION_DEMO_SCRIPT.md`**：更新演示脚本——新增"关键帧圈画定格""识餐逐项证据""DeepSeek 视觉直连 + 降级""Agent 工具轨迹"四个演示点及对应台词要点。
5. **降级录屏**：准备"断网 / 无 DeepSeek key / Worker 离线"三条录屏（放到 `docs/history/` 或演示素材目录，注明时间）。

---

## 4. 文件清单

**新建**：
- `ai-worker/healthmate_worker/visualize.py`（M2 关键帧标注渲染）
- `backend/app/services/agent/tools_cloud.py`（M3.3 工具定义，或并入 tools.py）
- `data/`（M4 数据集与标注，标注格式见 benchmark 协议）
- 迁移 `backend/migrations/versions/0016_food_and_keyframes.py`（M1+M2）

**修改**：
- `ai-worker/healthmate_worker/results.py`、`processors/food.py`、`processors/motion.py`、`config.py`、`capabilities.py`、`doctor.py`、`tests/test_worker.py`
- `backend/app/schemas/ai_results.py`、`app/api/v1/worker.py`、`app/api/v1/vision.py`（或对应路由文件）、`app/api/v1/fitness.py`、`app/models/models.py`、`app/services/agent/orchestrator.py`、`app/services/agent/tools.py`、`app/services/ai/gateway.py`（如需要 provider 扩展）、`tests/`（新增对应测试）
- `miniprogram/pages/scan/index.js/.wxml/.wxss`、`pages/media/index.js/.wxml/.wxss`、`pages/chat/index.*`（工具轨迹卡片）
- `miniprogram/pages/settings/privacy/`（隐私文案，M3.1 图片出网说明）
- `README.md`、`docs/VERIFICATION.md`、`docs/COMPETITION_DEMO_SCRIPT.md`、`docs/HEALTHMATE_4_SYSTEM_UPDATE.md`（新建，总结本轮）

---

## 5. 验收总清单（全部完成才算交付）

- [ ] 后端 pytest 全绿且 ≥ 当前 90；Worker pytest 全绿且 ≥ 当前 74；小程序 7 passed
- [ ] 新增测试覆盖：FoodItem 校验/求和/兼容、keyframes 落库与返回、provider 切换与降级、interpret 两接口、工具调用轨迹
- [ ] 真实端到端 smoke 各一次并留记录：识餐（逐项→校正→入库）、动作（上传→分析→关键帧图→展示）、DeepSeek 视觉直连、Agent 工具调用
- [ ] 迁移 0016 在 SQLite 全量/增量通过；MySQL 专用测试库重跑并记录
- [ ] 隐私文案已更新（云端识餐图片出网）；旧数据/旧任务前端兜底验证
- [ ] Ruff check/format、node --check、JSON 可解析全过
- [ ] git 提交完成，变更清单文档产出

**开发顺序建议**：M1 → M3.1 → M2 → M3.2 → M3.3 → M4；每步结束都可独立验证与演示，任何一步被阻断不影响其余。

---

## 6. 禁止事项

1. 不得声称未经评测的准确率数字；不得把"规则/估算/演示数据"表述为"模型/实测/权威结论"。
2. 不得输出医学诊断、损伤概率、康复处方；所有输出保留"一般训练/营养参考"边界与免责声明。
3. 不得削弱现有安全边界（SSRF、媒体体积、密钥、限流、隐私删除）；云端识餐新增的图片出网必须同步更新隐私说明。
4. 不得引入未授权许可的数据集（数据集登记与许可审计见 `docs/SKELETON_MODEL_TRAINING.md` 与 `最终的项目构想.md`）。
5. 不得虚构关键帧图片或识餐结果冒充真实推理（低置信度必须拒识/人工校正，与现有协议一致）。
6. 不为"看起来 AI"而堆砌未接入真实数据的模型/服务；每个新能力必须有真实调用路径或明确 OFF 降级。
