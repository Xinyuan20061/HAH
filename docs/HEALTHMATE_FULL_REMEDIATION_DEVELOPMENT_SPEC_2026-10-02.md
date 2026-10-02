# HealthMate 全链路整改与 Harness 产品化开发规格

> 日期：2026-10-02  
> 状态：实施基线（Implementation Baseline）  
> 适用仓库：`health-assistant`  
> 当前数据库迁移基线：`0025_motion_unified_v2_evidence`  
> 下一迁移：`0026_product_closure_and_agent_actions`  
> 目标：修复已确认的识餐、动作分析、Harness、导航、隐私、评测和部署缺口，并建立能持续发现未列出问题的发布门禁。

---

## 1. 执行结论

HealthMate 已经具备可运行的产品骨架：微信小程序、FastAPI 后端、本地 AI Worker、动作分析 V2、识餐、健康记录、Router → Workers → Decision Harness、Action Registry、语音、评测与隐私接口均有真实代码。但当前仍存在“局部实现完成、端到端闭环未完成”的系统性问题。

本轮整改不继续增加新功能数量，优先完成以下五件事：

1. **记录闭环**：识餐和动作分析都必须做到可创建、可确认、可回看、可修改、可删除、可追溯。
2. **契约收口**：Worker、Backend、小程序只使用一个冻结契约；兼容字段必须有删除期限。
3. **Harness 产品化**：从“能路由、能读工具”升级为“可持久化提案、可确认执行、可恢复、可审计”的运行时。
4. **真实质量门禁**：自动化通过只证明工程契约；识餐、动作识别和 Agent 回答必须使用真实样本报告才能宣称质量。
5. **生产闭环**：去除重复路由，补齐云文件删除对账、部署预检、可观测性和端到端测试。

完成本规格后，产品主链路应满足：

```text
用户输入/媒体
  → 身份、权限、同意范围和幂等检查
  → 受控 Worker / Harness 执行
  → 结构化证据与用户可理解结果
  → 用户校正或确认
  → 统一 Action 执行
  → 业务记录 + 时间线 + 审计账本
  → 可回看、修改、删除和再次被 Agent 安全读取
```

---

## 2. 范围、非目标与真实性原则

### 2.1 本轮范围

- 小程序信息架构、记录页、识餐页、动作反馈页、Agent 对话页；
- 饮食记录 CRUD、识餐确认入库与来源追踪；
- Motion Worker V2 回执、预览上传、后处理、纠错重分析；
- Harness Action 提案、确认、拒绝、执行、阶段轨迹和失败恢复；
- API 路由、错误模型、幂等、分页和版本并发控制；
- 媒体生命周期、账号删除、孤立文件对账；
- 真实识餐、动作、Agent 评测与发布门禁；
- 文档、部署预检、监控和回滚。

### 2.2 非目标

- 不把识餐结果包装成医学营养测量；
- 不把动作视频分析包装成医疗诊断或康复处方；
- 不用更多模型调用掩盖数据、契约或产品流程问题；
- 不在本轮新增社交、商城、食材采购等无关模块；
- 不把“自动化测试通过”写成“模型准确率已达标”。

### 2.3 强制真实性原则

1. 页面只显示真实存在的能力；不可用能力显示原因，不显示伪造数值。
2. 模型置信度、候选分数不得展示成准确率。
3. 未完成真实评测的改进只能标记为 `experimental`，不能替换正式基线结论。
4. 用户确认是写操作的必要条件，模型不得伪造确认。
5. 原始媒体、签名 URL、密钥、私有提示词和思维过程不得进入普通审计输出。

---

## 3. 已确认问题总表

| ID | 严重度 | 问题 | 当前证据 | 完成条件 |
| --- | --- | --- | --- | --- |
| FOOD-01 | P0 | `/pages/records/diet` 已注册但无任何可见入口 | 全仓页面路由引用数为 0 | 记录页、识餐成功页均可进入饮食明细 |
| FOOD-02 | P0 | 后端提示“直接编辑饮食记录”，实际无更新接口和编辑 UI | `records.py` 只有 POST/GET/DELETE | 提供详情、PATCH、版本冲突与前端编辑 |
| FOOD-03 | P0 | 识餐保存固定提交 `meal_type=other` | `pages/scan/index.js` | 保存前选择/确认餐次，服务端仅作默认推断 |
| FOOD-04 | P0 | 识餐真实基线质量不足，改进版未重跑 | 42 图基线：热量 MAPE 72.70%，区间覆盖 44.12% | 新冻结测试集报告、失败分层、达标后再升级能力文案 |
| FOOD-05 | P1 | Worker “已配置”不等于真实推理可用 | Doctor 只检查端点/模型配置 | Ready 探针执行无计费本地自检；正式 smoke 使用受控样本 |
| MOTION-01 | P0 | 预览上传响应字段不一致：后端 `urls`，Worker 读取 `uploads` | 代码交叉检查 | 冻结 `uploads`；兼容一版 `urls`；契约测试连接真实两端 |
| MOTION-02 | P0 | Worker V2 返回新字段，后处理仍读 V1 `recognition/pose/score` | `stage_tasks._evidence_from_receipt` | 只消费 `motion-worker-v2` 分组，不静默丢证据 |
| MOTION-03 | P0 | 重分析返回子任务 ID，前端继续轮询父任务 | `media/index.js::reanalyze` | 切换到子任务 ID；父子链、标签提示和进度正确 |
| MOTION-04 | P0 | `exercise_hint` 已在请求模型声明，但创建子任务时被丢弃 | `_spawn_child_run` 无 hint 参数 | 确认标签进入子任务请求并在审计中可见 |
| MOTION-05 | P0 | 中文安装路径使 MediaPipe 能力关闭 | Worker Doctor 实测 | Worker 安装在纯英文路径；生产启动时严格预检失败即拒绝发布 |
| MOTION-06 | P1 | 当前真实基线覆盖和准确率不足 | 现有基线与 Kinetics 辅助报告 | 冻结独立测试集、分动作/机位/遮挡报告和人工复核 |
| API-01 | P0 | `media.router` 在总路由重复注册 | `api/v1/router.py` 两次 include | 只注册一次；OpenAPI operationId 唯一测试通过 |
| API-02 | P1 | 业务错误响应不统一 | `HTTPException(detail=...)` 与 Motion error envelope 并存 | 新增统一 `ApiError`，触达端点全部采用同一结构 |
| API-03 | P1 | 饮食列表固定 50 条，无详情、过滤和游标 | `GET /diet/records` | 游标分页、日期/餐次过滤、单条详情 |
| HARNESS-01 | P0 | Action Registry 只能阻止写入，没有统一提案到执行闭环 | 各业务使用独立确认接口 | 持久化 proposal；确认/拒绝/执行/过期/审计统一 |
| HARNESS-02 | P1 | 简单问题仍经历 Router + Worker + Decision 多次模型调用 | 当前 MultiAgentKernel | 快路径；复杂任务才协作；每轮调用预算可见 |
| HARNESS-03 | P1 | 多 Worker 顺序执行 | `for task in route.workers` | 只读快照隔离后受控并行；失败不拖垮其他 Worker |
| HARNESS-04 | P1 | “stream” 是完整结果生成后再切字展示 | `agent_respond_stream` | 改为真实阶段事件；最终答案安全复核后输出，文案不再冒充实时推理 |
| HARNESS-05 | P1 | Agent 运行只在结束后保存，失败中途不可恢复 | `HealthAgentRun` 末尾创建 | 运行与阶段先落库，支持失败状态、重试和取消 |
| EVAL-01 | P0 | Agent 100% 是 mock 结构评测，不证明真实回答 | 双人人工评审 66 行为空 | 真实模型回答冻结、双人评分、严重错误率门禁 |
| EVAL-02 | P1 | 小程序测试偏静态，不验证真实导航和云链路 | 91 项通过仍未发现孤立页面 | 增加真机/开发者工具 E2E 与契约集成测试 |
| PRIV-01 | P0 | 云文件删除依赖客户端回执，无法服务端证明 | 当前架构说明已承认 | 删除任务账本、服务端/平台回执、孤立对象定期对账 |
| DOC-01 | P1 | 架构文档仍描述迁移 head 为 0016，实际已到 0025 | 文档与 migrations 不一致 | CI 自动校验当前 head；文档只引用命令结果，不手写旧 head |
| OPS-01 | P1 | Worker 能力上线主要依赖配置存在，缺少真实能力 smoke | Doctor 输出 | strict doctor + 合成素材 smoke + 能力注册门禁 |
| OBS-01 | P1 | 缺少跨小程序、后端、Worker、Provider 的统一 trace | 各模块 trace 格式不统一 | `request_id/trace_id/run_id/job_id` 全链关联且脱敏 |

“未提到的问题”不以本表为上限。第 13 节的自动扫描、E2E、故障注入、数据一致性和发布门禁用于持续发现新增缺陷。

---

## 4. 目标架构与模块边界

```text
微信小程序
├─ Workspace：健身房 / 仪表盘 / 小管家
├─ Records：饮食、运动、身体状态、趋势
├─ Food Flow：选图 → 识别草稿 → 校正 → 确认 → 饮食记录
└─ Motion Flow：选视频 → 分析 → 证据时间轴 → 纠错 → 子任务重分析
        │
        ▼
FastAPI
├─ API contract：认证、错误、幂等、分页、版本控制
├─ Domain services：records / vision / motion / health / privacy
├─ Harness runtime
│  ├─ Router / scoped workers / Decision
│  ├─ Tool Registry
│  ├─ durable run stages
│  └─ Action Proposal → Confirm → Execute → Audit
├─ AI job queue：租约、重试、进度、能力匹配
└─ Media lifecycle：私有存储、短期预览、清理、删除对账
        │
        ▼
Local AI Worker
├─ food_vision
├─ motion_unified_v2
├─ kinetics400（候选证据，不单独作最终结论）
└─ doctor / capability smoke / metrics
```

边界规则：

- 页面不得直接拼接模型提示词；
- Harness 不直接拥有饮食、动作、计划数据，通过领域服务 Tool Adapter 访问；
- Worker 不持有数据库凭据和用户 JWT；
- Worker 回执是“不可信结构化输入”，必须经过 Pydantic 与语义校验；
- 写操作只能由领域服务执行，Harness 只创建提案并等待确认；
- Decision Agent 不能绕过 Action Executor；
- Motion/food 原始媒体与模型输出不得直接成为 Agent 指令。

---

## 5. 跨模块 API 基础契约

### 5.1 标识与请求头

所有新增或整改端点支持：

| 名称 | 规则 |
| --- | --- |
| `X-Request-ID` | 客户端可传；缺失时服务端生成 UUID；响应必须回传 |
| `Idempotency-Key` | 创建任务、确认入库、Action 执行必须支持；用户范围内唯一 |
| `X-Client-Version` | 小程序版本；用于兼容窗口与问题定位 |
| `trace_id` | 一次跨服务调用链；不得包含用户 ID、密钥或媒体 URL |
| `run_id/job_id` | 领域任务 ID；不能代替身份验证 |

### 5.2 统一错误响应

```json
{
  "error": {
    "code": "DIET_RECORD_VERSION_CONFLICT",
    "message": "记录已在其他页面修改，请刷新后重试",
    "retryable": false,
    "request_id": "req_...",
    "details": {"current_version": 3}
  }
}
```

要求：

- `code` 稳定、全大写、可供前端分支处理；
- `message` 面向用户，不泄露供应商原始报错、路径、SQL 和密钥；
- `details` 只放白名单字段；
- 401/403/404 必须区分登录、权限和资源不存在，但跨用户资源统一返回 404；
- Worker/Provider 原始错误写内部诊断，用户响应只返回安全映射。

建议新增：

```python
class ApiErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool = False
    request_id: str
    details: dict[str, Any] = Field(default_factory=dict)

class ApiError(BaseModel):
    error: ApiErrorDetail
```

### 5.3 游标分页

统一响应：

```json
{
  "items": [],
  "next_cursor": "base64url(created_at,id)",
  "has_more": false
}
```

- 默认 `limit=20`，最大 `50`；
- 排序固定为 `recorded_at DESC, id DESC`；
- cursor 必须签名或只编码非敏感排序键；
- 禁止 offset 分页造成新增记录时重复/遗漏。

### 5.4 乐观并发控制

可编辑业务记录增加整数 `version`，初始为 1。更新必须提交当前版本：

```sql
UPDATE diet_records
SET ..., version = version + 1, updated_at = :now
WHERE id = :id AND user_id = :user_id AND version = :expected_version;
```

影响行数为 0 时返回 `409 DIET_RECORD_VERSION_CONFLICT`，不得静默覆盖。

---

## 6. 识餐与饮食记录完整整改

### 6.1 产品流程

```text
拍照/相册
  → 上传私有图片并创建 food_vision job
  → 返回“估算草稿”而非“最终营养结果”
  → 用户校正菜名、份量、食材项和营养值
  → 用户选择早餐/午餐/晚餐/加餐
  → 明确确认
  → diet.ai.finalize Action 幂等执行
  → 显示保存后的记录卡片
  → 可进入饮食记录详情编辑或删除
```

识餐四步文案改为：`识别 → 校正 → 确认 → 已保存`，不再使用容易被理解为“食材仓库”的“入库”。

### 6.2 数据库迁移 0026

对 `diet_records`：

```python
op.add_column("diet_records", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
op.create_index(
    "ix_diet_records_user_recorded_id",
    "diet_records",
    ["user_id", "recorded_at", "id"],
)
```

约束：

- `meal_type` 仅允许 `breakfast/lunch/dinner/snack/other`；应用层和数据库检查一致；
- `vision_analysis_id` 保留来源追踪，不因用户后续编辑而修改原始分析快照；
- `items_json` 更新必须经过 `FoodItem` schema，最多 12 项；
- `FoodAnalysisSession.finalized_record_id` 在最终确认后写入；重复确认返回同一记录；
- 历史行 `version=1`，不回填推测餐次。

### 6.3 饮食记录接口

#### 6.3.1 列表

```http
GET /api/v1/diet/records?limit=20&cursor=...&date_from=2026-10-01&date_to=2026-10-02&meal_type=lunch
```

响应：

```json
{
  "items": [
    {
      "id": 123,
      "name": "鸡胸肉蔬菜饭",
      "meal_type": "lunch",
      "calories": 520,
      "protein": 42,
      "carbs": 58,
      "fat": 12,
      "fiber": 8,
      "portion": "1 盘",
      "cooking_method": "少油煎制",
      "weight_g": 420,
      "source": "ai_vision_corrected",
      "vision_analysis_id": 88,
      "items": [],
      "recorded_at": "2026-10-02T04:10:00Z",
      "version": 2
    }
  ],
  "next_cursor": null,
  "has_more": false
}
```

#### 6.3.2 详情

```http
GET /api/v1/diet/records/{record_id}
```

只返回当前用户记录。不存在或不属于当前用户均返回 404。

#### 6.3.3 更新

```http
PATCH /api/v1/diet/records/{record_id}
Content-Type: application/json

{
  "version": 2,
  "name": "鸡胸肉蔬菜饭（半份）",
  "meal_type": "lunch",
  "calories": 390,
  "protein": 31.5,
  "carbs": 43.5,
  "fat": 9,
  "fiber": 6,
  "portion": "半盘",
  "items": []
}
```

Schema：

```python
MealType = Literal["breakfast", "lunch", "dinner", "snack", "other"]

class DietRecordPatch(BaseModel):
    version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    meal_type: MealType | None = None
    calories: float | None = Field(default=None, ge=0, le=5000)
    protein: float | None = Field(default=None, ge=0, le=500)
    carbs: float | None = Field(default=None, ge=0, le=1000)
    fat: float | None = Field(default=None, ge=0, le=500)
    fiber: float | None = Field(default=None, ge=0, le=200)
    portion: str | None = Field(default=None, max_length=120)
    cooking_method: str | None = Field(default=None, max_length=120)
    weight_g: float | None = Field(default=None, ge=0, le=5000)
    items: list[FoodItem] | None = Field(default=None, max_length=12)
```

更新成功后必须：

1. 更新同一条 `HealthTimeline` 的 `ref_type=diet, ref_id=record_id` payload，不重复新增事件；
2. 记录 `diet_record_updated` 评测事件，只保存字段名，不保存完整敏感内容；
3. 保留 `source` 和 `vision_analysis_id`，另将来源展示为“AI 草稿，已人工修改”；
4. 返回更新后的完整记录与新 `version`。

#### 6.3.4 删除

保留现有：

```http
DELETE /api/v1/diet/records/{record_id}
```

补充要求：

- 删除对应时间线事件；
- 若来自识餐，将 `FoodAnalysisSession` 标记为 `record_deleted`，不得再次 finalize 到同一记录；
- 删除操作进入 `AgentActionAudit` 或统一业务审计；
- 前端显示不可撤销确认，成功后刷新能量仪表盘。

### 6.4 识餐 finalize 接口

```http
POST /api/v1/vision/food-analysis/{analysis_id}/finalize
Idempotency-Key: food-finalize-{analysis_id}-{uuid}

{
  "meal_type": "lunch",
  "confirmed": true
}
```

成功响应必须直接返回记录快照，避免前端保存后不知道写入了什么：

```json
{
  "ok": true,
  "already_finalized": false,
  "analysis_id": 88,
  "action_audit_id": 701,
  "record": {
    "id": 123,
    "name": "鸡胸肉蔬菜饭",
    "meal_type": "lunch",
    "calories": 520,
    "version": 1
  }
}
```

重复请求返回 `already_finalized=true` 和同一 `record`，不得创建重复饮食记录。

### 6.5 小程序改动

#### 记录页 `pages/records/index`

- “今日餐次”标题右侧增加“查看明细”；
- 点击进入 `/pages/records/diet`；
- 每个餐次允许点击并带 `meal_type/date` 筛选；
- 保存/编辑/删除返回后 `onShow` 强制刷新仪表盘；
- 空态明确区分“没有记录”和“加载失败”。

#### 饮食明细页 `pages/records/diet`

- 从“手动新增 + 最近 8 条”改为“日期筛选 + 分餐次列表 + 游标加载”；
- 单条记录支持查看、编辑、删除；
- AI 来源展示“图片估算，已确认”或“图片估算，已人工修改”；
- 不显示供应商名、内部置信分、模型 ID；
- 编辑时显示原始食材项并重新汇总；
- 版本冲突时提示刷新，不覆盖其他页面刚做的修改。

#### 识餐页 `pages/scan/index`

- 增加餐次 picker，默认值按北京时间推断，但必须允许用户修改；
- 最后一步改为“已保存”；
- 保存成功卡片显示餐次、菜名、热量和“查看记录”；
- 使用 finalize 返回的 `record.id` 跳转详情；
- 不再只等待 700ms 后无条件跳到汇总页；
- `confidence < 0.6`、缺比例尺或高不确定性时，必须展开校正确认提示；
- 无法识别时提供“手动记录”，不得生成伪造营养值。

### 6.6 识餐质量门禁

当前 42 图报告只能作为旧基线。新版本必须：

1. 冻结不少于 100 张、覆盖中式混合餐/单品/汤类/遮挡/外卖盒/无比例尺的测试集；
2. 标注总热量范围、主要可见项、份量不确定性；菜名无可靠真值时不得用代理标签宣称菜名准确率；
3. 报告 `coverage、calorie MAE/MAPE、range coverage、macro MAE、critical hallucination rate`；
4. 将失败和拒识计入全样本分母；
5. Food-101 候选模型必须登记版本和权重哈希，未配置时 capability 明确为 false；
6. 发布最低门槛：
   - 完成率 ≥ 90%；
   - 热量区间覆盖率 ≥ 75%；
   - 严重幻觉率（图片无该食物却高置信写入）≤ 2%；
   - 所有低置信结果强制人工确认；
   - 若热量 MAPE 仍 > 35%，产品文案只能称“粗略草稿”，不能称“营养分析”。

---

## 7. 动作分析 V2 契约收口

本节以 `docs/MOTION_V2_CONTRACT_2026-09-30.md` 为唯一上游契约，不创建 V3 字段。修复目标是让已定义的 V2 真正贯通。

### 7.1 Worker 回执唯一模型

```python
class MotionWorkerResultV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["motion-worker-v2"]
    video_quality: VideoQuality
    subject: SubjectEvidence
    pose_evidence: PoseEvidence
    recognition_candidates: list[RecognitionCandidate] = Field(max_length=50)
    frames: list[MotionFrameEvidence] = Field(max_length=120)
    measurements: MotionMeasurements
    model_versions: dict[str, str] = Field(default_factory=dict)
```

Worker `/complete` 收到 V2 时不得再转换成 V1；不认识 `schema_version` 返回 422 `UNSUPPORTED_WORKER_RESULT_SCHEMA`。

### 7.2 预览上传契约

冻结响应字段为 `uploads`：

```json
{
  "uploads": [
    {
      "frame_id": "f_012",
      "asset_id": "preview_18_f_012",
      "upload_url": "/api/v1/media/previews/...?exp=...&sig=...",
      "expires_at": 1790000000
    }
  ]
}
```

兼容策略：

- 后端一个版本周期同时返回 `uploads` 和旧 `urls` alias；
- Worker 使用 `resp.get("uploads") or resp.get("urls") or []`；
- 新测试只断言 `uploads`；
- 下一次主版本删除 `urls`，删除前统计旧 Worker 使用量；
- 帧 ID、asset prefix、run ID、user ID 必须与签名绑定。

### 7.3 后处理适配器

替换 `_evidence_from_receipt`：

```python
def evidence_from_worker_v2(receipt: dict) -> MotionEvidenceBundle:
    parsed = MotionWorkerResultV2.model_validate(receipt)
    return MotionEvidenceBundle(
        video_quality=parsed.video_quality,
        subject=parsed.subject,
        pose_evidence=parsed.pose_evidence,
        recognition_candidates=parsed.recognition_candidates,
        frames=parsed.frames,
        measurements=parsed.measurements,
        model_versions=parsed.model_versions,
    )
```

禁止行为：

- 不得读取不存在的 V1 `recognition/pose/score` 后返回空字典；
- 不得把 Kinetics softmax、姿态规则分和视觉模型判断直接按数值排序；
- 不得因最终类别未知而清空姿态或视频帧证据；
- 不得用用户确认标签伪装成模型识别成功；
- 不得在标签与评分器不匹配时生成分数。

### 7.4 纠错与重分析

请求：

```http
POST /api/v1/media/motion-analyses/{parent_id}/reanalyze
Idempotency-Key: ...

{
  "cloud_review_mode": "redacted_frames",
  "exercise_hint": "bicep_curl",
  "reason": "user_label_correction"
}
```

后端修改：

```python
def _spawn_child_run(
    db: Session,
    *,
    user: User,
    run: MotionAnalysisRun,
    cloud_mode: str | None,
    exercise_hint: str | None,
) -> MotionAnalysisRun:
    requested = validate_catalog_id(exercise_hint) if exercise_hint else run.requested_type
    ...
```

规则：

- catalog ID 必须验证；自由中文标签只用于视觉讲解提示，不得写进 `requested_type`；
- child 保存 `parent_run_id`；
- 用户确认记录保留在独立 `motion_user_feedback`，不得覆盖父结果；
- 新任务必须返回新 `analysis_id`；
- 小程序收到后立即设置 `analysisId=child.analysis_id`，清理旧 VM，并轮询 child；
- 页面提供“查看上一次结果”，但不得混合父子证据；
- child 完成后 Harness evidence chain 引用 child trace。

前端伪代码：

```javascript
const child = await api.post(`/media/motion-analyses/${parentId}/reanalyze`, body, headers)
this.setData({
  analysisId: child.analysis_id,
  parentAnalysisId: parentId,
  vm: null,
  timelineFrames: [],
  activeFrame: null,
  jobStatus: '正在按确认动作重新分析'
})
await this.continueAnalysis(child.analysis_id, asset)
```

### 7.5 Worker 部署与能力门禁

Windows 生产 Worker 必须安装在纯英文绝对路径，例如：

```text
C:\HealthMate\worker
C:\HealthMate\venv
C:\HealthMate\models
```

禁止从含中文、空格或微信临时目录的路径启动正式 Worker。

新增：

```powershell
python doctor.py --strict --offline
python scripts/smoke_motion_v2.py --fixture tests/fixtures/squat-short.mp4
```

`--strict` 失败条件：

- MediaPipe native library 无法加载；
- FFmpeg/OpenCV 无法解码受控样本；
- `motion_unified_v2` schema 验证失败；
- 预览生成/压缩/上传签名验证失败；
- capability 声明与实际导入结果不一致。

Worker 只有通过 strict doctor 才能注册 `motion_unified_v2`。服务端不得把 V2 任务派给只声明 `motion_pose` 的旧 Worker。

### 7.6 动作发布质量门禁

- 按主体划分 train/dev/test，禁止同一人的相邻视频跨集合；
- 报告所有样本准确率、覆盖率、已覆盖准确率、unknown 召回、错误拒识率；
- 按动作、机位、遮挡、多人、光照和视频时长分层；
- 数值评分另行报告计次误差、事件时间误差和测量覆盖率；
- Kinetics 只作为候选来源，不能单独触发可靠评分；
- 正式发布前至少 30 个自采、已授权、与调参集独立的视频做双人人工复核；
- MediaPipe 关闭时不得宣称姿态评分可用。

---

## 8. Harness Action 闭环与运行时整改

### 8.1 目标状态机

```text
created
  → routing
  → working
  → deciding
  → awaiting_confirmation（可选）
  → executing
  → completed

任意运行态 → failed / cancelled / expired
awaiting_confirmation → rejected / expired
```

### 8.2 新数据模型

#### `health_agent_run_stages`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint PK |  |
| `run_id` | FK | 所属 HealthAgentRun |
| `stage_key` | varchar(80) | router / worker:coach / decision / action |
| `status` | varchar(30) | queued/running/completed/failed/cancelled |
| `attempt` | int | 从 1 开始 |
| `provider` | varchar(60) | 脱敏 provider 类别 |
| `started_at/finished_at` | datetime |  |
| `error_code` | varchar(80) | 安全错误码 |
| `trace_json` | text | 脱敏工具名、状态、耗时；不含思维过程 |

唯一约束：`UNIQUE(run_id, stage_key, attempt)`。

#### `agent_action_proposals`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint PK |  |
| `proposal_id` | varchar(64) UNIQUE | 对外随机 ID，不使用可枚举自增 ID |
| `user_id` | FK + index |  |
| `run_id` | FK + index | 来源 Agent run |
| `action_key` | varchar(80) | 必须存在于 Action Registry |
| `risk_level` | varchar(20) | low/medium/high/critical |
| `status` | varchar(30) | pending/confirmed/rejected/executing/executed/failed/expired |
| `payload_json` | text | 服务端验证后的执行载荷 |
| `payload_hash` | varchar(64) | 确认后防篡改 |
| `display_json` | text | 用户可见摘要，不含私密字段 |
| `expires_at` | datetime | 默认 30 分钟；隐私操作更短 |
| `confirmed_at/executed_at` | datetime |  |
| `audit_id` | FK nullable | 执行后关联 AgentActionAudit |
| `version` | int | 乐观锁 |

约束：同一个 `proposal_id + payload_hash` 只能执行一次。

### 8.3 Tool Registry 返回协议

Action Tool 不直接调用业务写函数，而是产生候选：

```python
class ActionProposalRequest(BaseModel):
    action_key: str
    arguments: dict[str, Any]
    user_visible_reason: str = Field(max_length=300)

class ActionProposalView(BaseModel):
    proposal_id: str
    action_key: str
    title: str
    risk_level: str
    requires_confirmation: bool = True
    summary: str
    expires_at: datetime
```

Registry 必须：

1. 验证 action 已注册且当前 persona/decision stage 有权提出；
2. 使用 action 专属 Pydantic schema 校验 arguments；
3. 生成服务端 payload hash；
4. 持久化 proposal；
5. 返回 `approval_required` 和 `proposal_id`；
6. 不执行写操作。

### 8.4 Action API

```http
GET  /api/v1/agent/actions/{proposal_id}
POST /api/v1/agent/actions/{proposal_id}/confirm
POST /api/v1/agent/actions/{proposal_id}/reject
```

确认请求：

```json
{
  "version": 1,
  "confirmation": true,
  "typed_confirmation": null
}
```

响应：

```json
{
  "proposal_id": "ap_...",
  "status": "executed",
  "action_key": "plan.apply",
  "audit_id": 901,
  "result": {"plan_id": 77}
}
```

安全规则：

- 过期 proposal 返回 410；
- 非本人统一返回 404；
- payload hash 不一致返回 409，必须重新提案；
- `privacy.account.delete` 仍要求专用二次确认和云文件清理协议，不能仅凭通用 `confirmation=true`；
- `experiment.start/finish/cancel` 保持只允许 `source=user`；
- 执行器按 action key 调用领域服务，不允许模型传函数名；
- 重复 confirm 返回首次执行结果，不重复写入。

### 8.5 Agent 响应契约

```json
{
  "run_id": 501,
  "intent": "plan",
  "reply": "我整理了一份三天训练安排，确认后可加入计划。",
  "plan": {...},
  "actions": [
    {
      "proposal_id": "ap_...",
      "action_key": "plan.apply",
      "title": "加入本周计划",
      "risk_level": "low",
      "summary": "新增 3 项训练安排",
      "expires_at": "2026-10-02T13:00:00Z"
    }
  ],
  "trace": {
    "harness_trace_id": "trace-...",
    "route": {...},
    "tool_calls": [],
    "model_calls": 3,
    "elapsed_ms": 2100,
    "budget": {"max_model_calls": 5, "used_model_calls": 3}
  }
}
```

小程序只根据 `actions[]` 渲染确认卡，不从 reply 文本猜测可执行动作。

### 8.6 路由与性能策略

默认预算：

| 请求类型 | 路径 | 最大模型调用 |
| --- | --- | ---: |
| 安全规则命中 | safety short circuit | 0 |
| 单领域简单问答 | 确定性预路由 → 1 Worker → Decision 可合并 | 2 |
| 跨领域复杂问答 | Router → 2 Workers → Decision | 4 |
| 极少数三领域任务 | Router → 3 Workers → Decision | 5 |

要求：

- 先用确定性意图和显式页面上下文判断明显单领域请求；
- 只有跨领域或歧义请求才调用模型 Router；
- Decision 在单 Worker 且无冲突、无 action 时可与 Worker 合并；
- 每轮全局最大 5 次模型调用，超出立即安全降级；
- 每次 provider 调用有连接/读取/总时限；
- 并行 Worker 不得共享同一个 SQLAlchemy Session。

并行实现策略：

1. 进入协作前冻结脱敏只读上下文快照；
2. Worker 基于不可变快照并行推理；
3. 如必须追加 Tool 调用，由 Tool Executor 为每次调用创建独立 DB Session；
4. `asyncio.gather(..., return_exceptions=True)`，并发上限 2；
5. 一个 Worker 失败时 Decision 使用剩余报告并明确数据缺口。

### 8.7 流式接口诚实化

健康建议必须先完成输出安全复核，因此不直接把未经复核的模型 token 推给用户。`/respond/stream` 改为“阶段事件流”：

```jsonl
{"type":"meta","run_id":501,"request_id":"req_..."}
{"type":"stage","stage":"routing","status":"completed","label":"正在理解你的问题"}
{"type":"stage","stage":"worker:coach","status":"running","label":"正在核对训练记录"}
{"type":"stage","stage":"decision","status":"completed","label":"正在整理建议"}
{"type":"answer","reply":"...","actions":[]}
{"type":"done","result":{...}}
```

前端可以对已通过安全复核的最终 `reply` 做展示动画，但 UI 和文档必须称“逐字展示”，不能称“模型实时流式生成”。

### 8.8 取消、恢复与失败

新增：

```http
POST /api/v1/agent/runs/{run_id}/cancel
POST /api/v1/agent/runs/{run_id}/retry
GET  /api/v1/agent/runs/{run_id}
```

- cancel 只阻止未开始的后续阶段，不假装能撤回已发出的 provider 请求；
- retry 从失败阶段开始，已成功只读工具结果可按版本复用；
- action 执行失败不得自动再次执行非幂等写操作；
- 服务重启后 `running` 且租约过期的阶段进入可恢复队列；
- 用户侧显示“已停止/可重试/需要重新确认”，不得无限 loading。

---

## 9. API、路由与文档治理

### 9.1 删除重复 Router

`backend/app/api/v1/router.py` 中 `media.router` 只保留一次：

```python
routers = [
    ...,
    media.router,
    media.admin_router,
    media.worker_preview_router,
]
```

新增测试：

```python
def test_openapi_operation_ids_are_unique(app):
    schema = app.openapi()
    ids = [op["operationId"] for path in schema["paths"].values() for op in path.values() if isinstance(op, dict) and "operationId" in op]
    assert len(ids) == len(set(ids))
```

### 9.2 兼容与弃用

- 所有旧字段在响应中标记 `deprecated` 的同时记录调用计数；
- 兼容期至少一个小程序发布周期；
- 删除前必须确认线上旧客户端使用量为 0；
- 不允许永久保留两套含义不同的字段；
- OpenAPI schema 标明版本和弃用日期。

### 9.3 文档自动校验

CI 增加：

- `alembic heads` 必须只有一个 head；
- 文档中的迁移 head 通过脚本注入或校验，禁止继续写死 0016；
- README 功能表必须与 capability manifest 对齐；
- 所有页面路由至少有一个非测试导航引用，白名单页面除外；
- 所有 API 示例通过 schema 测试解析。

孤立页面检查建议脚本：

```text
读取 app.json pages
→ 扫描非测试 JS/WXML 导航引用
→ references=0 且不在 entry-page allowlist 时 CI 失败
```

---

## 10. 隐私、媒体生命周期与账号删除

### 10.1 新增删除任务账本

`media_deletion_tasks`：

| 字段 | 说明 |
| --- | --- |
| `id/task_id` | 内部 ID 与随机对外 ID |
| `user_id` | 所属用户 |
| `media_asset_id` | 可空；账号删除后仍保留最小审计引用 |
| `storage_backend/storage_key_hash` | 不保存可用签名 URL |
| `status` | pending/requested/verified/deleted/failed/manual_review |
| `provider_receipt_json` | 平台删除回执的脱敏字段 |
| `attempts/next_attempt_at/error_code` | 重试控制 |
| `verified_at` | 服务端确认时间 |

### 10.2 删除流程

```text
用户请求删除
  → 冻结写入并创建 deletion manifest
  → 删除/撤销云对象
  → 服务端验证对象不存在或读取平台成功回执
  → 删除预览、AIJob payload/result、动作证据、识餐会话关联
  → 删除业务记录和账户
  → 写最小化 PrivacyAudit（不保留原内容）
```

客户端回执只能作为辅助证据，不能单独标记 `verified`。

### 10.3 孤立对象对账

每日任务：

- DB 有 MediaAsset、云端无对象：标记 `missing`，相关任务停止重试；
- 云端有对象、DB 无 MediaAsset：进入隔离清单，超过保留期删除；
- 预览过期：删除字节并清空/失效引用；
- 账号已删除但仍有对象：P0 告警；
- 对账日志只记录对象哈希和内部 ID，不记录签名 URL。

### 10.4 保留期

| 数据 | 默认保留 |
| --- | --- |
| 动作预览 JPEG | 7 天 |
| 原始动作视频/识餐图片 | 用户可配置；默认完成后 7 天或立即删除选项 |
| 结构化饮食/动作记录 | 用户删除前保留 |
| Provider 原始响应 | 不长期保存；只保存校验后的结构化结果与诊断摘要 |
| 脱敏审计 | 按隐私政策设定，不含原媒体和密钥 |

---

## 11. 可观测性与运行指标

每次请求关联：

```text
request_id
  ├─ harness_trace_id / health_agent_run_id
  ├─ ai_job_id / motion_analysis_id / food_analysis_id
  ├─ provider_invocation_id
  └─ action_proposal_id / action_audit_id
```

最低指标：

### API

- 请求量、4xx/5xx、P50/P95/P99；
- 按稳定 error code 聚合；
- 幂等命中率与版本冲突率；
- OpenAPI operationId 唯一性。

### Worker

- 在线节点和真实 capability；
- claim 延迟、执行耗时、租约续期失败、重试次数；
- food/motion 完成率、拒识率、schema reject；
- MediaPipe/模型加载状态；
- 预览上传成功率。

### Harness

- 每轮模型调用数、工具调用数、Router/Worker/Decision 耗时；
- rules fallback 比例；
- Action 提案/确认/拒绝/过期/失败率；
- 取消和恢复成功率；
- 用户反馈样本量，零样本显示为空而不是 0%。

### 隐私

- 待删除对象数量；
- 删除验证 P95；
- 孤立对象数量与最大年龄；
- 账号删除失败必须告警。

日志禁止项：JWT、API key、签名 URL、音频/图片 base64、完整健康记录、模型私有推理。

---

## 12. 测试与评测矩阵

### 12.1 单元测试

- Pydantic 边界、数值范围、extra 字段策略；
- Tool Registry 权限与 Action proposal；
- 饮食记录版本冲突和 items 汇总；
- Motion V2 adapter；
- 错误码映射；
- 路由选择和调用预算。

### 12.2 契约测试

必须用真实的双方 schema，而不是各自 Fake：

| Producer | Consumer | 测试重点 |
| --- | --- | --- |
| Motion Worker | Backend complete | V2 字段完整、extra forbid、证据不丢失 |
| Backend preview URLs | Worker uploader | `uploads`、签名、过期、跨任务拒绝 |
| Food Worker | Backend food job | 数值、items 汇总、不确定性 |
| Backend Agent | 小程序展示层 | actions、stage events、错误 envelope |
| Backend Diet API | 饮食明细页 | 分页、编辑、冲突、删除 |

契约 fixture 必须由生产 schema 序列化后再由对端解析，禁止手写“看起来差不多”的 JSON。

### 12.3 API 集成测试

- 用户 A 不能读取/编辑/删除用户 B 的饮食、动作、proposal、媒体；
- finalize 重复请求只产生一条 DietRecord；
- edit 更新时间线而非重复新增；
- child reanalysis 使用新 ID 和 label hint；
- Action confirm 重复调用只执行一次；
- 服务重启后 stage/job 可恢复；
- OpenAPI operationId 全局唯一。

### 12.4 小程序自动化

除现有源码断言外，新增行为测试：

- 从仪表盘点击进入饮食明细；
- 识餐保存后能看到刚保存记录；
- 修改餐次/热量后仪表盘同步；
- 删除后仪表盘减少；
- 版本冲突提示刷新；
- 动作重分析切换 child ID；
- Agent proposal 卡确认、拒绝、过期；
- 网络断开、页面卸载、JWT 刷新和任务恢复。

### 12.5 真机 E2E

至少覆盖 Android 与 iOS 各一台真机：

1. 微信登录；
2. 相机拍餐 → 云上传 → Worker/云端识别 → 校正 → 保存 → 编辑 → 删除；
3. 选择动作视频 → Worker V2 → 时间轴预览 → 标签纠错 → child 重分析；
4. Agent 读取刚保存的记录并生成 proposal；
5. 用户确认 proposal 后业务状态变化；
6. 账号导出和删除；
7. 弱网、切后台、超时、Worker 离线。

E2E 必须保存脱敏 trace、屏幕录制和测试版本，不保存真实用户原媒体到仓库。

### 12.6 故障注入

- Provider 429/500/超时/无效 JSON；
- Worker 进程在 claim 后退出；
- 租约过期与重复 complete；
- 预览上传一半失败；
- 数据库短暂断连；
- 云媒体 URL 过期；
- Action 执行成功但响应丢失；
- 并行 Worker 一个失败；
- 删除对象平台回执未知。

### 12.7 真实质量评测

#### Agent

- 33 条固定集生成真实在线回答；
- 每条两名独立评审；
- 事实性、引用支持、可执行性、安全性、清晰度 1–5；
- 严重错误率必须为 0 才能发布健康建议升级；
- 总体通过率 ≥ 85%，安全维度均分 ≥ 4.5；
- 同时报告延迟、模型调用数和成本。

#### Food

执行第 6.6 节门禁。

#### Motion

执行第 7.6 节门禁。

---

## 13. 发现“未提到问题”的持续审计

每次发布必须运行以下检查，任何一项失败都不得以“已有单测通过”豁免：

1. **路由可达性**：app.json 每个页面都有入口或白名单理由。
2. **API 完整性**：OpenAPI operationId 唯一；弃用端点有使用量和删除日期。
3. **CRUD 对称性**：页面文案声称可编辑时，必须存在 update API、权限测试和 UI。
4. **契约差异**：Producer schema 与 Consumer schema 自动 diff；字段删除/改名必须失败。
5. **数据库一致性**：外键、唯一约束、孤立引用、重复时间线事件扫描。
6. **能力诚实性**：manifest capability 必须由 strict doctor/smoke 生成，不由配置字符串推断。
7. **模型声明**：界面中的准确、可靠、智能等词必须能映射到冻结报告。
8. **隐私数据流**：新增媒体/模型字段必须进入导出、删除和保留期清单。
9. **并发与幂等**：所有写端点做重复请求、响应丢失、双击和版本冲突测试。
10. **降级体验**：Worker/模型/网络不可用时必须有明确下一步，不得无限 loading。
11. **真机差异**：相机权限、后台切换、临时路径、长图/大视频、iOS/Android 解码。
12. **文档漂移**：迁移 head、接口示例、环境变量、能力表由 CI 校验。

建议新增脚本：

```text
scripts/audit_page_reachability.mjs
backend/scripts/audit_openapi.py
backend/scripts/audit_data_consistency.py
backend/scripts/audit_privacy_coverage.py
ai-worker/scripts/smoke_capabilities.py
scripts/compare_contracts.py
```

---

## 14. 实施工作包与文件落点

### WP0：建立红灯测试和基线

先写失败测试，不改业务行为。

涉及：

- `backend/tests/test_api_contract_governance.py`
- `backend/tests/test_diet_record_lifecycle.py`
- `backend/tests/test_motion_v2_live_contract.py`
- `backend/tests/test_agent_action_proposals.py`
- `miniprogram/tests/pageReachability.test.js`
- `miniprogram/tests/foodRecordLifecycle.test.js`
- `miniprogram/tests/motionReanalysis.test.js`

退出条件：每个确认缺陷至少有一个会失败的回归测试。

### WP1：路由和饮食闭环

涉及：

- `backend/app/api/v1/router.py`
- `backend/app/api/v1/records.py`
- `backend/app/schemas/records.py`
- `backend/app/services/timeline.py`
- `backend/app/api/v1/vision.py`
- `backend/app/models/models.py`
- `backend/migrations/versions/0026_product_closure_and_agent_actions.py`
- `miniprogram/pages/records/index.*`
- `miniprogram/pages/records/diet.*`
- `miniprogram/pages/scan/index.*`

退出条件：识餐保存、明细、修改、删除、仪表盘刷新真链路通过。

### WP2：Motion V2 收口

涉及：

- `ai-worker/healthmate_worker/processors/motion_unified.py`
- `backend/app/schemas/worker.py`
- `backend/app/api/v1/media.py`
- `backend/app/services/motion/stage_tasks.py`
- `miniprogram/pages/media/index.js`
- Worker/Backend/MiniProgram 对应测试

退出条件：真实 V2 fixture 从 Worker 回执到页面证据时间轴不丢字段；纠错轮询 child。

### WP3：Harness Action 与持久运行

涉及：

- `backend/app/harness/contracts.py`
- `backend/app/harness/registry.py`
- `backend/app/harness/kernel.py`
- `backend/app/harness/collaboration.py`
- `backend/app/harness/tools.py`
- `backend/app/services/agent/actions.py`
- `backend/app/services/agent/orchestrator.py`
- `backend/app/api/v1/agent.py`
- `backend/app/models/models.py`
- `backend/app/schemas/agent.py`
- `miniprogram/pages/chat/index.*`
- `miniprogram/pages/home/index.*`

退出条件：至少 `plan.apply`、`goal.adjustment.apply`、`diet.ai.finalize` 使用统一 proposal 协议；重复确认不重复执行。

### WP4：隐私与媒体治理

涉及：

- `backend/app/api/v1/privacy.py`
- `backend/app/services/privacy.py`
- `backend/app/services/motion/media_storage.py`
- `backend/app/services/ai_jobs.py`
- 新增 `backend/app/services/media_reconciliation.py`
- 数据迁移和定时任务

退出条件：账号删除能给出服务端验证结果；孤立媒体对账有报告和告警。

### WP5：真实评测与部署

涉及：

- `benchmark/`
- `backend/scripts/evaluate_agent.py`
- `backend/scripts/prepare_agent_human_review.py`
- `ai-worker/healthmate_worker/evaluation/`
- `ai-worker/doctor.py`
- 部署文档与 CI

退出条件：真实报告、哈希、模型版本、样本许可和人工评审齐全；未达门槛能力自动降级。

工作包可以并行开发，但共享文件 `models.py`、迁移文件、`router.py` 和冻结契约必须由单一集成人维护，避免再次出现重复注册和字段漂移。

---

## 15. 迁移、灰度与回滚

### 15.1 数据库

1. 生产备份；
2. `alembic upgrade 0026_product_closure_and_agent_actions`；
3. 校验唯一 head、索引、默认 version；
4. 旧客户端仍能读取原 DietOut 字段；
5. 新表为空上线，不自动执行历史 proposal；
6. 回滚只删除新增空表/列；已有新版本数据时禁止直接 downgrade，先导出并停写。

### 15.2 API 灰度

- 服务端先发布兼容响应；
- Worker 更新后确认 V2 capability；
- 小程序再切换新字段和 child ID；
- 观察一个发布周期后移除旧 alias；
- 任何阶段异常可将 capability 关闭并回退为“画面讲解/手动记录”，不得伪装成功。

### 15.3 功能开关

建议：

```text
FOOD_RECORD_EDIT_ENABLED
MOTION_WORKER_V2_REQUIRED
HARNESS_ACTION_PROPOSALS_ENABLED
HARNESS_COLLABORATIVE_ROUTING_ENABLED
MEDIA_SERVER_VERIFIED_DELETE_ENABLED
```

开关只控制新路径，不得绕过确认、安全和隐私规则。

---

## 16. 发布完成定义（Definition of Done）

所有条件同时满足才能称为整改完成：

### 工程

- [ ] 后端、Worker、小程序全量测试通过；
- [ ] OpenAPI 无重复 operationId；
- [ ] Alembic 只有一个 head，SQLite/MySQL 迁移验证通过；
- [ ] 页面可达性扫描无未解释孤立页面；
- [ ] Producer/Consumer 契约测试使用生产 schema；
- [ ] 无新增高危依赖或密钥泄漏。

### 饮食

- [ ] 识餐保存可选择餐次；
- [ ] 保存后能看到具体记录；
- [ ] 记录可编辑、删除，仪表盘同步；
- [ ] 重复 finalize 不重复写；
- [ ] 新真实评测报告完成，页面能力文案与指标一致。

### 动作

- [ ] MediaPipe strict doctor 在正式英文路径通过；
- [ ] `motion-worker-v2` 回执不被 V1 adapter 丢字段；
- [ ] 预览上传成功；
- [ ] 标签确认进入 child 请求，前端轮询 child；
- [ ] 独立真实视频报告和人工复核完成。

### Harness

- [ ] Agent 运行阶段可查询、失败可恢复、用户可取消；
- [ ] Action proposal 可确认/拒绝/过期；
- [ ] 重复确认不重复执行；
- [ ] 简单请求模型调用数 ≤2，复杂请求 ≤5；
- [ ] 阶段事件真实，最终答复经过安全复核；
- [ ] 真实模型双人人工评审完成且严重错误为 0。

### 隐私与运维

- [ ] 跨账户隔离测试通过；
- [ ] 云文件删除有服务端验证或明确 `manual_review`，不伪报成功；
- [ ] 孤立对象对账任务运行；
- [ ] 全链 trace 可关联且不含敏感数据；
- [ ] 生产监控和告警已配置；
- [ ] 回滚演练完成。

---

## 17. 最终产品口径

整改完成前：

- 识餐称“图片估算草稿，确认后保存”；
- 动作分析称“基于可见画面的一般健身反馈”；
- Agent 离线 100% 只称“结构契约通过”；
- 未通过 strict doctor 的能力显示“当前设备不可用”。

整改并通过真实门禁后，才可以声明：

> HealthMate 是一个证据约束、用户确认、可追溯的个人健康 Agent 工作台。它能够把识餐、动作分析和健康记录作为受控工具接入 Harness，并让用户从建议一直追踪到确认行动与实际记录；所有模型能力均显示适用范围、失败状态和真实评测证据。

