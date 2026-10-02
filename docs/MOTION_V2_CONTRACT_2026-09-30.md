HISTORICAL: 本文是特定轮次的交付/验证快照，其中引用的迁移 head 是当时的事实，不是当前 head。当前 head 以 `alembic heads` 命令结果为准。

# HealthMate 动作分析 V2：冻结契约（2026-09-30）

> 依据《HEALTHMATE_MOTION_V2_REBUILD_SPEC_2026-09-30.md》§7.2/§8/§10 编制。本文件是 V2 重建各工作包（B/E/A/C/D/F/G）共同遵守的唯一接口依据；与任何代码现状冲突时，以本文件与规格 §14 为准。契约冻结后改动须经评审。

## 1. 版本与范围

- `pipeline_version`：`motion-unified-v2`（服务端选定；客户端请求只提交用户选择与期望 schema，不接受任意字符串伪造版本；生产部署配置决定有效 pipeline）。
- 兼容：兼容读取 V1 历史结果；V1 base64 回执按原上限兼容，迁移期不强行让旧 Worker 发新结构。
- 结果响应 `result_version: 1`。
- `cloud_review_mode`：`off | skeleton | redacted_frames`（默认推荐 `redacted_frames`，界面明示"AI 会查看这段视频的关键画面"）。旧版仅骨架同意不得静默升级为真实帧传输。

## 2. 创建请求（POST /api/v1/media/motion-analyses）

```json
{
  "media_id": 318,
  "requested_exercise": "auto",
  "exercise_hint": null,
  "cloud_review_mode": "redacted_frames",
  "consent_version": "motion-real-frames-v2",
  "response_schema": "motion-analysis-v2"
}
```

- `Idempotency-Key` 表示同一用户同一次提交：重试网络请求复用；用户主动重新分析或改变模式时生成新键。
- 服务端按媒体与参数指纹（`request_fingerprint`）复用已完成阶段；重复点击"开始分析"不得重新计费，新的云端模式不得错误复用到旧任务。
- 修改模式（cloud_review_mode 变化）必须创建正确的新任务/子任务，不得复用旧 analysisId。

## 3. 结果响应（motion-analysis-v2，result_version=1）

字段规范（示例文本数字为契约示例，非对任何真实视频的结论）：

```json
{
  "analysis_id": 518,
  "status": "completed",
  "stage": "ready",
  "pipeline_version": "motion-unified-v2",
  "result_version": 1,
  "recognition": {
    "state": "likely",
    "canonical_id": "bicep_curl",
    "display_name": "看起来是哑铃弯举",
    "reason": "肘关节屈伸清楚，最高点附近画面略有遮挡。",
    "source": "vision"
  },
  "capabilities": {
    "recognition": "available",
    "timeline": "available",
    "coaching": "available",
    "repetitions": "unavailable",
    "quality_score": "unavailable"
  },
  "summary": {
    "text": "手臂屈伸和哑铃轨迹可以看清。下面按抬起和放下拆解；下一遍可重点关注上臂的位置，以及放下时是否仍有控制。",
    "primary_next_step": "放下时慢数两拍，并留意上臂是否跟着前后摆动。",
    "source": "visual_coach"
  },
  "metrics": [],
  "timeline": {
    "duration_ms": 16000,
    "frames": [
      {
        "id": "f_012",
        "timestamp_ms": 4800,
        "preview_asset_id": "preview_18",
        "preview_url": "<本人可访问的短期地址>",
        "phase": "抬起阶段",
        "observation": "前臂向上转动，哑铃逐渐靠近胸前。",
        "explanation": "这一阶段可以留意上臂是否也明显前移。",
        "next_step": "下一遍尽量让上臂停在身体两侧，再弯曲肘部抬起哑铃。",
        "advice_kind": "general_tip",
        "evidence_refs": [{"frame_ids": ["f_008", "f_012"], "start_ms": 3200, "end_ms": 4800}]
      }
    ]
  },
  "notices": [{"kind": "metric_unavailable", "text": "这类动作本次提供画面讲解，暂不显示数值评分。"}]
}
```

约束：
- `recognition.state` 只允许 `identified | likely | unknown`（对外 display；内部可保留细分 reason 字段但前端不展示）。
- `recognition.display_name`：identified="深蹲 / 哑铃弯举"，likely="看起来是哑铃弯举"，unknown 尽量描述可见运动。
- `capabilities` 五个键分别独立：recognition/timeline/coaching/repetitions/quality_score，取值 `available | unavailable`。六类专属评价器覆盖范围 ≠ 允许识别总量。
- `metrics[]` 每项必须带 `id/value/unit/source/scorer_version/applicable_to`；空数组表示无指标，禁止用 0 代替缺失。
- `timeline.frames[]` 按 `timestamp_ms` 升序；每段 4–6 个关键时刻（静态/高度重复可少于 4）；`preview_url` 为本人可访问短期地址。
- 正文（summary.text、frame 的 observation/explanation/next_step、notices.text）只出现自然中文；不出现字段名、英文动作 ID、帧编号、模型分值、内部事件名、provider ID、trace。
- 未完成时 `result=null` 或标识清楚的部分结果；轮询到期保留进行中/错误状态，不渲染假完成。
- 展示层不显示 `candidate_score`、`NOT_RECOGNIZED`、`frame:N`、`local_worker`、`deepseek_vision`、`trace-xxx` 等调试字段；原始候选分值、引擎版本、trace 只进 `GET /admin/motion-analyses/{id}/diagnostics`。

## 4. CoachReview 契约（§7.2，服务端结构化输出）

```python
from typing import Literal
from pydantic import BaseModel, Field, model_validator

class EvidenceRef(BaseModel):
    frame_ids: list[str] = Field(min_length=1, max_length=6)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def ordered_window(self):
        if self.end_ms < self.start_ms:
            raise ValueError("invalid_evidence_window")
        return self

class FrameNote(BaseModel):
    frame_id: str
    phase: str = Field(max_length=24)
    observation: str = Field(min_length=4, max_length=160)
    explanation: str = Field(min_length=4, max_length=180)
    next_step: str = Field(min_length=4, max_length=120)
    advice_kind: Literal["observed_correction", "general_tip", "capture_tip"]
    evidence_refs: list[EvidenceRef] = Field(min_length=1, max_length=3)

class CoachReview(BaseModel):
    canonical_id: str | None = None
    novel_label_zh: str | None = Field(default=None, max_length=40)
    identification: Literal["identified", "likely", "unknown"]
    identification_reason: str = Field(max_length=180)
    summary: str = Field(min_length=20, max_length=240)
    primary_next_step: str = Field(max_length=120)
    frame_notes: list[FrameNote] = Field(default_factory=list, max_length=8)
```

语义校验（结构校验之后强制执行，禁止只靠"输出 JSON"提示词信任内容）：
1. 所有 frame_id 属于本次素材与主运动者；evidence_refs 时间位于视频时长内。
2. 动态结论（速度/借力/节奏/稳定性变化）必须引用含 ≥2 个不同时刻的帧（evidence_refs.frame_ids 去重后 ≥2 或时间窗口跨度 > 0）。
3. 分类改变后不继承旧分数/旧次数（T05）。
4. 正文未泄露技术字段（正则扫描字段名/英文 ID/帧号/分值/事件名）。
5. 不编造视觉不可得的负重、痛感、生理状态、肌肉激活、医学结论。
6. 静态帧只能确认姿态；未观察到错误时给执行要点，禁止硬编"膝内扣/塌腰/耸肩"。

## 5. MotionWorkerResultV2 回执分组（§10，Worker → Backend）

```json
{
  "schema_version": "motion-worker-v2",
  "video_quality": {"available": true, "decoded_ok": true, "duration_ms": 16000, "blur_summary": "ok"},
  "subject": {"available": true, "subject_id": "s_01", "visible_regions": ["shoulder", "elbow", "wrist"]},
  "pose_evidence": {"available": true, "fps": 6, "frame_ids": ["p_001", "p_002"], "measurement_summary": "..."},
  "recognition_candidates": [
    {"source": "pose", "source_label": "squat", "canonical_id": "squat", "raw_score": 0.91, "score_type": "rule"},
    {"source": "kinetics", "source_label": "front raises", "canonical_id": null, "raw_score": 0.73, "score_type": "softmax"}
  ],
  "frames": [
    {"frame_id": "f_012", "timestamp_ms": 4800, "preview_asset_id": "preview_18", "subject_id": "s_01",
     "visible_regions": ["shoulder", "elbow", "wrist"], "blur": "ok", "motion_delta": 0.31}
  ],
  "measurements": {"available": true, "exercise_id": "squat", "reps": 5, "duration_ms": 16000, "quality": {}}
}
```

契约要点：
- `pose_evidence.available` = 有没有姿态测量，**不再表示有没有认出六类动作**。
- `measurements.available` 独立：不能因名称未定而丢掉可见关节序列。
- `recognition_candidates` 每项带 `source/source_label/canonical_id/raw_score/score_type`；Kinetics 与姿态候选**值含义不同，不按数值直接排序**，按命名空间归一化后一起保留；目录无法映射的保留原标签（`canonical_id: null`）作视觉参考，禁止强行映射到最近六类。
- `frames[]` 是通用证据池：即使六类拒识也完整保留（`event_frames=[]` 只代表事件检测器无事件，不代表"视频没有人"）；含 frame_id/真实时间戳/缩略帧引用/可见部位/主运动者位置/模糊度/运动变化量。
- 预览图与云端图分离：`preview_asset_id` 只传引用 + `hash + timestamp + dimensions`，**回执不含图像字节**；预览默认最多 8 个展示帧，每帧 ≤100KB。
- 帧选择先按信息量（时间覆盖+姿态极值+运动方向变化+器械关键位置）再按真实时间升序；相邻重复帧去重。
- 姿态采样 FPS、通用候选帧 FPS、界面展示帧数是不同参数，不得共用单一 MAX_PREVIEWS 限制全局（配置建议：姿态 6–8 FPS、通用候选 2 FPS、预览长边约 720px、最长 60 秒）。
- 新 Worker 宣告能力 `motion_unified_v2`；后端按能力派单，V2 任务不得发给只认 V1 的 Worker。

现状对照（2026-09-30 侦查）：
- `finding/advice/phase` 现状是 `frames[]` 逐帧行内键，不是回执顶层键（V2 保持该结构；Worker 拒识分支当前把 frames 清空是缺陷 R04，V2 必须保留证据池）。
- 现状回执模型 `backend\app\schemas\worker.py:213-247`（MotionWorkerResultV1）**无 kinetics 字段**（extra="ignore" 直接丢弃）；V2 回执 schema 必须新增 `kinetics.candidates` 与完整 `frames[]` 证据池结构。该 schema 文件归 E 包修改。
- 现状 Worker 配置多为硬编码：姿态采样约 8 FPS、时长上限 120s、画布 320×480、预览长边 720（不在 config.py）；V2 改为配置项（默认 6–8 FPS / 60s / 720px）。

## 6. 接口增量（§8.3）

| 端点 | 语义与校验 |
| --- | --- |
| `GET /fitness/motion-capabilities` | 返回动作目录版本、名称、可用识别/讲解/计次/评分能力；供手动选择与能力说明 |
| `POST /media/motion-analyses` | 创建或复用任务；用户/素材/模式/同意范围/版本参与检查；幂等 |
| `GET /media/motion-analyses/{id}` | 只读结果与进度；不发起模型调用；未完成 result=null 或清楚的部分结果 |
| `POST /media/motion-analyses/{id}/reanalyze` | 纠错/换模式后产生子任务；复用素材与已完成无副作用阶段；支持 completed/partial/unknown 的重新分析（不限于 failed 的 /retry）；新幂等键与父子关系 |
| `POST /media/motion-analyses/{id}/confirm-label` | 接收真实类别 ID 或人工中文描述；**未选类别不得提交 user_confirmed 占位 ID** |
| `POST /media/motion-analyses/{id}/feedback` | `{kind, frame_id?, corrected_label?, comment?}`；kind ∈ useful/wrong_label/wrong_frame/unhelpful_advice；验证 frame_id 属于该结果 |
| `GET /media/motion-analyses/{id}/previews/{frame_id}` | 用户鉴权后返回预览或临时签名地址；已过期返回明确状态；绝不返回别人的帧 |
| `GET /admin/motion-analyses/{id}/diagnostics` | 管理权限读取错误码、候选、版本、trace；普通用户页面不调用 |

## 7. 数据库契约（迁移在 0024 后新增，执行前读实际 head）

| 对象 | 新增/调整 | 必要约束 |
| --- | --- | --- |
| `motion_analysis_runs` | 加列 `request_fingerprint, result_version, effective_pipeline_version, cloud_review_mode, error_code` | 用户范围内幂等；终态与 result_version 一致 |
| `motion_evidence_frames` | 新表 `run_id, frame_id, timestamp_ms, preview_asset_id, subject_id, observation_json, expires_at` | UNIQUE(run_id, frame_id)；时间范围校验；私有媒体访问 |
| `motion_stage_tasks` | 新表 `run_id, stage, status, lease_token, attempts, available_at, error_code` | UNIQUE(run_id, stage, version)；可恢复、不重复完成 |
| `provider_invocations` | 请求指纹唯一约束 + `reserved/sent/succeeded/failed/outcome_unknown` 状态 + model/prompt 版本 | 请求前原子抢占额度（唯一键：user_id+evidence_hash+operation+model+prompt_version+policy_version+consent_mode） |
| `motion_user_feedback` | 新表 `kind, frame_id, corrected_label, comment, created_at` | 与计算结果分开写，避免整体覆盖丢失反馈 |
| 动作目录 | 带版本配置/登记表 | ID/别名映射统一；能力状态与已部署评价器一致 |

- `motion_analysis_feedback.result_json` 去掉图像字节只存引用（MySQL 容量风险）；迁移前检查已有行大小，设计批处理，不在上线时一次读出大字段。
- 清理（purge）覆盖 V1/V2 预览、AIJob 原始结果、反馈快照；到期/媒体删除/账号删除都覆盖。
- 迁移只新增不修改历史。现状 head=`0024_motion_voice_harness`（revision `0024_motion_voice_harness`，down=`0023_user_voice_config`）；新增 revision 名：**`0025_motion_unified_v2_evidence`**，down_revision=`"0024_motion_voice_harness"`（执行前再读一次实际 head 确认）。
- 现状模型集中在单文件 `backend\app\models\models.py`；已存在 `motion_analysis_runs`(:693)/`provider_invocations`(:762)/`motion_scores`(:506)/`motion_events`(:526)/`motion_analysis_feedback`(:734)；**缺失** `motion_evidence_frames`/`motion_stage_tasks`/`motion_user_feedback`（三表全新，迁移创建）。新增模型只允许 E 包在 models.py 追加，其他包不得编辑该文件。

## 8. 跨包接口签名（实施代理按此对接，禁止改他人文件）

| 接口 | 签名要点 | 归属 |
| --- | --- | --- |
| 动作目录访问 | `list_capabilities() / get_action(canonical_id) / map_kinetics_label(label)->id\|None / knowledge(canonical_id)->entries / catalog_version()` | 目录代理（创建 catalog.py 服务），F/C/G 只 import |
| 文本点评 | `text_summary.build_summary(coach_review, metrics, evidence, catalog) -> {text, primary_next_step, source}` | C 拥有 text_summary.py |
| 视觉复核 | `vision_review.run_visual_review(frames, context, catalog) -> CoachReview`（一次请求完成判断+帧观察+阶段+点评草案） | C 拥有 vision_review.py |
| 证据存储 | `save_preview(asset, ...)->preview_asset_id / get_preview(asset_id)->url / purge_expired(...)`；上传地址后端生成、绑定当前用户/任务 | B 拥有存储服务（新文件） |
| 阶段任务 | `enqueue_stage(run_id, stage, payload) / claim_stage(...) / complete_stage(..., version)`（compare-and-set） | E 拥有（新服务文件） |
| 预算记录 | `reserve_invocation(unique_key)->ok / mark_sent / mark_outcome`（请求前原子创建） | E 拥有 provider_gateway.py 的 V2 函数（新增，不改旧接口） |
| 决策编排 | `decision.decide_motion(local, vision, kinetics, evidence) -> recognition + metrics` | A 拥有 decision.py + orchestrator.py |
| 前端展示 | `motionUnifiedView.normalizeTimeline / selectFrame / replayFrame` 及 display 映射 | D 拥有 motionUnifiedView.js + media 页面 |

## 9. 文件所有权（并行开发防冲突，禁止越界改写；如需他人文件变更，在交付说明中提出，由集成阶段统一处理）

| 工作包 | 拥有文件（新增/修改） | 禁止触碰 |
| --- | --- | --- |
| B | ai-worker `processors\motion_unified.py`、`visualize.py`（已有 `render_motion_preview` 真实帧+人脸模糊，仅旧 motion_pose 链在用；统一链须改用真实帧渲染）、Worker 回执生成（`result_contract.py` 或等效，产出 MotionWorkerResultV2）、**`processors\kinetics.py` 与 `kinetics_runtime.py` 的散落映射（:29-37/:22-99）切换为消费生成的 `catalog_data.py`（Kinetics 候选按命名空间归一化，R05/T03）**、Worker 测试（含把"六类拒识 frames 必空"拆成"无姿态测量"与"无视频图像"两情境）；backend 新建媒体存储服务（如 `app\services\motion\media_storage.py`）与证据帧仓储、证据过期清理（motion_evidence_frames.expires_at 到期清理） | 迁移、models.py、schemas/worker.py、decision.py、orchestrator.py、media.py |
| E | backend `worker.py`（/worker/jobs/{id}/complete 端点 :402 区域重构为"事务内仅鉴权/租约/schema/证据落库/本地 done/插入后处理任务，提交后立即返回"）、新建阶段任务服务（如 `app\services\motion\stage_tasks.py`，compare-and-set）、`provider_gateway.py`（V2 请求前原子抢占函数，保留旧接口）、**`schemas\worker.py`（V2 回执 schema）**、**迁移 0025+（唯一迁移负责人）**、**models.py（唯一模型追加负责人）**、`ai_jobs.py` 清理扩展（覆盖 V2/AIJob/反馈快照，R15） | motion_unified.py、decision.py、media.py、miniprogram |
| A | backend `decision.py`、`orchestrator.py`、`tests\test_motion_unified_chain.py`（改 test_decision_gate_rules :130/:137-139 期望为"本地可靠则保留"）、T01/T03/T04/T05 测试 | 迁移、models.py、text_summary.py（只 import 契约签名）、vision_review.py |
| C | backend `vision_review.py`、`text_summary.py`、新建 `coach_review.py`（§4 契约模型+语义校验）、新建 `knowledge_zh.py`（按动作目录 knowledge_keys 提供中文讲解条目）、T07/T08/T10 测试 | 迁移、orchestrator.py（只提供接口）、catalog.yaml |
| D | miniprogram `utils\motionUnifiedView.js`、`pages\media\index.js/index.wxml/index.wxss`（含 `<video id="motionVideo">`+seek 接线）、前端测试（mediaUnifiedFlow.test.js 正则断言 :44-64/:124-140 改为真跑展示映射与事件处理；.slice(0,4) 契约 :95-102 放宽） | 任何 backend/worker 文件 |
| F | backend `media.py` 增量（reanalyze/confirm-label 改造/feedback/previews GET/admin diagnostics 路由）、新建 feedback 仓储、motion profile 写入（motion_scores/motion_events，评分只在标签匹配+评价器适用+版本登记时写）、Harness 只读工具（motion.analysis.read/timeline.read/history.compare）、T11 测试 | 迁移、models.py（按契约 DDL 编码，测试用 mock） |
| G | ai-worker 新建 counter/scorer 模块（如 `processors\counters.py`）、目录中其负责动作的 `repetition_counter/quality_scorer` 登记（**以条目形式提交给目录包，由目录包统一 review 并执行 generate.py 重新生成**，G 不直接运行生成脚本，逐类验证后开启）、G 测试 | motion_unified.py 核心流程、迁移、catalog.yaml（只提交条目不落盘） |
| 目录 | 根仓 `motion_catalog\catalog.yaml` + `motion_catalog\generate.py`；生成物：backend `app\services\motion\catalog_data.py`、ai-worker `catalog_data.py`、miniprogram `utils\motionCatalogData.js`；backend 新建 `app\services\motion\catalog.py`（访问器：list_capabilities/get_action/map_kinetics_label/knowledge/catalog_version）；backend `tests\test_motion_catalog.py`（唯一性+kinetics 标签真实性校验）；kinetics 映射核对（对 `ai-worker\healthmate_worker\models\kinetics400_labels.txt` 400 行实际表） | 迁移、其他包源码 |

补充规定：
- 动作目录 `catalog.yaml` 中 `knowledge_keys` 为钩子；具体中文讲解条目内容由 C 包在 `knowledge_zh.py` 编写（键对齐）。
- `catalog.yaml` 为目录包与 G 包的共享文件：G 以条目形式提交新动作的 `repetition_counter/quality_scorer` 登记（含验证依据），由目录包统一 review 并唯一执行 `generate.py` 重新生成三份产物；G 不得直接改 yaml 或运行生成脚本。
- 共享只读辅助改动（如 `app\services\motion\__init__.py` 的**追加式**导出）允许各包做，但必须在交付说明中列出，且只能追加不能改删。
- 三仓全量测试在集成阶段（工作包全部落定后）统一跑，各包在自己测试文件内跑局部用例；DB 测试按现状 conftest 方式（SQLite 临时库 + alembic upgrade head + deepseek_api_key=""）。

## 10. 测试与验收要点（§11）

- T01 本地深蹲独立成立（无云端保留名称与次数）；T02 非六类仍有证据（时间轴非空、可收真帧）；T03 400 类候选参与；T04 新类别可接受（候选外标签不丢弃）；T05 类别变更清分；T06 时间严格排序（0.9/2.8/4.8/9.3，阶段不由数组首尾伪造）；T07 点评可读（污染正文降级或拒绝）；T08 保留本地解释（不退回英文 event）；T09 关云端仍看真帧（外部调用次数=0）；T10 局部与静态视频；T11 用户纠错（payload 类型正确、关联当前帧、无占位类别）；T12 轮询到期；T13 云端超时与并发（原子记录最多放行一次、不盲重试）；T14 升级与删除（V1 可读、权限隔离、删除全覆盖）。
- 旧测试改造：`test_decision_gate_rules`（`tests\test_motion_unified_chain.py:130`，"无复核必须 uncertain"断言 :137-139）改为"本地可靠则保留"（A 负责）；Worker"六类拒识后 frames 必为空"（`tests\test_motion_unified.py:124`、:281 `_valid_rejected_result()`）拆成"无姿态测量"与"无视频图像"两情境（B 负责）；前端测试改为运行展示映射与事件处理、不用正则搜按钮宣告可用（D 负责）。
- T→主责包映射矩阵：T01/T03/T04/T05→A；T02/T06/T09→B；T07/T08/T10→C；D 包负责前端展示映射测试（display 三态/时间排序/无调试字段）与 T11 前端 payload 侧；T12/T13/T14→E；T11 后端校验侧与画像/Harness→F；G 包负责其新增评价器的验证用例。跨包用例（如 T05 需要 C 的识别+E 的存储）在集成阶段统一验证。
- 全程零真实外部模型调用（DeepSeek/腾讯云全部 mock/打桩）；离线通过只宣称代码逻辑修复。
- 现状基线（侦查确认）：backend `pytest -q` 218 项 = 217 通过 + 1 失败（`tests\test_voice_tencent.py::test_voice_status_endpoint_no_cloud_call`，本机带腾讯云凭证导致环境敏感失败，与 motion 无关，最终报告中如实记录）；ai-worker `pytest -q` 117 通过；miniprogram `node --test tests\*.test.js` 79 通过。三仓均无真实视频 fixture（backend 用伪造 storage_key+合成 JPEG；Worker 测试现场合成 64×64 随机 mp4）——V2 三类样例视频 fixture 需合成制备并标注"同类离线样例验证"。
