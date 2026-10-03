# HealthMate 一等奖导向能力升级方案

> 日期：2026-10-02  
> 定位：只讨论产品与技术能力，不包含答辩、汇报包装、真机兼容、宣传材料和现场演示安排  
> 关联文档：`HEALTHMATE_FULL_REMEDIATION_DEVELOPMENT_SPEC_2026-10-02.md` 负责工程缺陷整改；本文负责一等奖级能力上限  
> 核心目标：把 HealthMate 从“功能齐全的健康小程序”升级为“能观察、解释、行动、复盘并持续适应个人的证据驱动健康 Agent”。

---

## 1. 总判断

当前项目已经不缺页面和基础功能，继续增加人物、入口、图表或普通问答能力，对一等奖竞争力帮助很小。能力层面的主要问题是：

1. Harness 已有 Router、Worker、Decision 和工具白名单，但仍偏“多轮生成框架”，没有完全成为持久化行动运行时；
2. 动作目录已有 21 类，但只有 8 类登记了计次和评分器，分类、计次、阶段、评分还没有形成同一条稳定能力链；
3. 识餐仍主要依靠视觉模型直接猜菜名、重量和营养值，照片中不可观察的重量、油盐和隐藏配料决定了精度上限；
4. 个性化主要来自档案、最近 7 天记录和固定规则，还没有形成结构化长期记忆、约束规划和结果反馈学习；
5. 微实验已经有闭环雏形，但与计划调整、动作反馈、饮食记录和下一轮策略尚未完全联动；
6. 多模态结果目前更多是“单独功能”，还没有稳定进入统一健康状态、计划决策和后验复盘。

因此，一等奖路线只保留一条主线：

> **证据驱动的自适应健康行动 Harness**：系统从用户的记录、识餐和动作证据中构建带数据质量的个人健康状态，主动提出一个最值得做的行动；用户确认后执行，系统持续观察结果并调整下一轮计划。模型不能确定时会主动追问、拒绝评分或降级，而不是输出看似精确的答案。

这条主线由三个旗舰能力支撑：

1. **动作教练 2.0**：少量黄金动作的深度识别、阶段分割、计次、错误定位和进步追踪；
2. **交互式识餐 2.0**：视觉草稿、最大不确定性追问、确定性营养计算和个人份量记忆；
3. **自适应行动 Harness 3.0**：统一健康状态、约束规划、Action 闭环、长期记忆和后验复盘。

---

## 2. 当前能力底座与取舍

### 2.1 已有底座

| 能力 | 当前状态 | 可复用部分 |
| --- | --- | --- |
| Harness | 16 个注册工具；Router → Workers → Decision；最小权限 | Registry、人格、领域 Worker、安全短路、审计 trace |
| 主动健康 | 5 类信号：运动断档、睡眠不足、体重变化、动作下降、记录缺口 | 确定性事实提取、数据覆盖、限制说明 |
| 微实验 | 5 类两档方案，可启动、完成、停止和复盘 | `decision_id`、基线冻结、真实记录进度、非因果结论 |
| 动作目录 | 21 类具备视觉识别/时间线/讲解声明 | 统一 catalog、别名、Kinetics 映射、知识键 |
| 动作深能力 | 8 类登记计次与评分器 | hysteresis cycle、静态保持计时、规则评分接口 |
| 动作证据 | Motion V2、关键帧、时间线、预览、用户纠错 | V2 数据模型、stage queue、evidence frame、Harness 只读工具 |
| 识餐 | VLM 逐项结果、区间、不确定性、人工校正、确认保存 | `FoodItem`、FoodAnalysisSession、纠错历史、Action Registry |
| 规划 | 周计划、动态目标、训练调整、运动知识图谱 | plan guardrail、目标设置、训练语义和资源库 |
| RAG | 人工审核知识文档与来源 | 词法检索、引用 ID、URL 与模型输出隔离 |

### 2.2 必须停止的投入

- 暂停继续扩展动作类别；21 类目录保留，但深能力先集中到黄金动作；
- 暂停继续增加 Agent 人格和领域角色；角色数量不等于能力深度；
- 不继续用一次 VLM 请求同时猜菜名、重量、油盐和营养值；
- 不增加泛化健康页面或普通图表；
- 不把 Kinetics-400 Top-1 直接作为最终动作判断；
- 不用“更多 Agent”解决本应由规则、优化器或领域服务解决的问题；
- 不做无用户控制的在线自训练；
- 不开发通用医疗诊断、伤病判断或疾病治疗能力。

### 2.3 能力投资优先级

| 优先级 | 能力 | 原因 |
| --- | --- | --- |
| S | 统一健康状态 + Action 闭环 | 决定项目是否是真正 Agent Harness |
| S | 黄金动作深度教练 | 最能体现视觉、时序、规则和 Agent 融合的技术深度 |
| S | 交互式识餐 | 能绕开单图热量估算的不可观察性上限 |
| A | 约束型自适应计划 | 把感知结果转化为实际行动 |
| A | 长期结构化记忆 | 形成个人化而非一次性对话 |
| A | 结果反馈策略 | 让系统真正“越用越适合”，但保持可解释 |
| B | 开放动作讲解 | 保留广覆盖，但只做讲解与证据，不强行评分 |
| C | 更多模型、更多人格、更多页面 | 边际收益低，容易稀释主线 |

---

## 3. 旗舰能力架构

```text
                  ┌──────────────────────────────┐
                  │ Personal Health State Engine │
                  │ facts / quality / trends /   │
                  │ constraints / preferences    │
                  └──────────────┬───────────────┘
                                 │
        ┌────────────────────────┼────────────────────────┐
        │                        │                        │
        ▼                        ▼                        ▼
 Motion Coach 2.0       Interactive Food 2.0     Records & Goals
 pose/phase/reps        visual draft/questions   sleep/activity/body
 errors/progress        deterministic nutrients  plans/adherence
        │                        │                        │
        └────────────────────────┼────────────────────────┘
                                 ▼
                  Evidence & Uncertainty Layer
                  observed / inferred / missing
                                 │
                                 ▼
                   Adaptive Decision Engine
                 signals → candidate actions →
                 constraint solve → next best action
                                 │
                                 ▼
                    Harness Action Runtime
                 propose → confirm → execute → audit
                                 │
                                 ▼
                    Outcome & Preference Update
                 adherence / helpfulness / response
                                 └───────→ next cycle
```

能力原则：

- 感知模型提供证据，不直接拥有最终健康决策权；
- 能观察到的事实、推断、缺失数据必须分层；
- 计划由确定性约束器保证合法性，模型负责解释与候选生成；
- 每轮只选择一个最值得执行的主要行动，避免建议堆砌；
- 反馈改变排序与默认方案，不改变安全边界；
- 任何数值都能追溯到记录、测量器、模型版本或用户确认。

---

## 4. 能力一：Personal Health State Engine

### 4.1 为什么必须先做

当前 `read_context` 一次性拼接 profile、today、recent_7d、motion_profile、goals 等字典，适合早期问答，但无法稳定回答：

- 哪些数据是直接观察，哪些是模型推断；
- 数据质量是否足以触发行动；
- 两个信号是否来自同一时间窗口；
- 用户当前有哪些不可违反的约束；
- 上一次行动产生了什么后果；
- 同一结论在下次记录变化后是否需要失效。

一等奖级 Harness 需要一个版本化、可复算的个人健康状态层。

### 4.2 核心数据结构

```python
class EvidenceRef(BaseModel):
    source_type: Literal[
        "profile", "checkin", "diet_record", "exercise_record",
        "motion_analysis", "food_analysis", "goal", "experiment"
    ]
    source_id: str
    observed_at: datetime
    trace_id: str | None = None

class StateValue(BaseModel):
    key: str
    value: float | int | str | bool | None
    unit: str = ""
    evidence_type: Literal["observed", "derived", "model_inferred", "user_confirmed"]
    confidence_level: Literal["high", "medium", "low", "unavailable"]
    evidence: list[EvidenceRef] = []
    limitations: list[str] = []
    valid_from: datetime
    valid_until: datetime | None = None

class HealthConstraint(BaseModel):
    key: str
    severity: Literal["hard", "soft"]
    source: str
    description: str

class HealthStateSnapshot(BaseModel):
    version: str
    as_of: datetime
    window_days: int
    values: dict[str, StateValue]
    constraints: list[HealthConstraint]
    missingness: dict[str, int]
    active_actions: list[str]
```

### 4.3 第一批状态特征

#### 直接观察

- 今日/近 7 日睡眠、饮水、步数、体重；
- 饮食记录天数、总热量、蛋白质记录覆盖；
- 运动频率、时长、训练动作；
- 动作分析次数、用户确认类别、可用测量；
- 计划完成情况和微实验进度。

#### 派生特征

- `sleep_debt_7d`：只对有记录日计算，并携带 observed days；
- `exercise_gap_days`；
- `diet_record_coverage_7d`；
- `plan_adherence_7d`；
- `motion_quality_trend_{exercise}`：至少 3 次同版本、同动作才可用；
- `load_recovery_ratio`：运动负荷与恢复信号的保守比值；
- `data_reliability_score`：按覆盖、来源和版本计算，不使用模型自报 confidence 直接替代。

#### 硬约束

- 用户明确疼痛/胸痛/晕厥/严重呼吸困难；
- 安全规则命中；
- 目标动作测量器不可用；
- 数据覆盖不足；
- 计划时间、器械和训练天数限制；
- 正在进行且互斥的 Action/实验。

### 4.4 接口与工具

```http
GET /api/v1/health/state?window_days=7
GET /api/v1/health/state/history?key=sleep_debt_7d&days=30
GET /api/v1/health/signals?status=active
```

Harness Tools：

```text
health.state.read
health.state.history
health.signals.read
health.constraints.read
health.outcomes.compare
```

`health.context.read` 保留兼容，但内部改为消费 `HealthStateSnapshot`，不再各自重复计算。

### 4.5 状态失效与复算

- 新增/修改/删除健康记录后，只重算受影响特征；
- 每个派生值记录 `feature_version` 和输入 evidence hash；
- Motion 模型版本变化后，旧分数不与新分数直接比较；
- 用户修改识餐记录后，相关饮食特征失效并重算；
- 不保存模型私有思维，只保存输入引用、规则版本和结果。

---

## 5. 能力二：Motion Coach 2.0

### 5.1 产品能力分层

动作目录分为三层，避免“21 类都能评分”的虚假能力：

| 层级 | 能力 | 动作范围 |
| --- | --- | --- |
| Gold | 识别、阶段、计次/计时、错误测量、逐次反馈、历史趋势 | 首批 8 个黄金动作 |
| Silver | 开放类别判断、时间线、关键帧、一般技术讲解 | 目录 21 类 |
| Unknown | 只展示人物运动与关键帧，不确认类别、不评分 | 目录外或证据不足 |

### 5.2 首批黄金动作

建议固定为：

1. 深蹲 `squat`；
2. 俯卧撑 `pushup`；
3. 弓步蹲 `lunge`；
4. 二头弯举 `bicep_curl`；
5. 侧平举 `lateral_raise`；
6. 肩推 `shoulder_press`；
7. 平板支撑 `plank`；
8. 臀桥 `hip_bridge`。

选择依据：覆盖下肢推、上肢推、单侧下肢、手臂、肩部动态、静态核心和后链；多数可用单人 RGB + 2D/归一化骨架观察；现有代码已有部分规则或计数器基础。

Hammer curl、front raise、row、side plank 暂保留 Silver 或下一批，避免首轮验证面过宽。

### 5.3 单次分析流水线

```text
视频质量检查
  → 主体跟踪与可见部位
  → MediaPipe landmarks + normalization
  → open-set gate
  → temporal action classifier
  → phase segmentation
  → rep/hold segmentation
  → exercise-specific measurements
  → per-rep quality findings
  → representative evidence frames
  → evidence-grounded coach summary
```

### 5.4 骨架时序表示

每帧特征：

- 归一化 2D/可选 z landmarks；
- 关键关节角；
- 一阶速度和二阶变化；
- 左右对称性；
- 躯干方向与身体尺度；
- landmark visibility；
- 主体框运动量。

序列模型优先顺序：

1. **TCN/1D Conv baseline**：轻量、易部署、适合固定长度 landmark 窗口；
2. **ST-GCN-lite**：数据量足够后比较；
3. 不优先直接训练 RGB 视频大模型，算力和数据成本过高且解释性弱。

模型输出：

```python
class TemporalMotionOutput(BaseModel):
    action_logits: list[float]
    unknown_score: float
    phase_probs: list[list[float]]
    embedding: list[float]       # 只用于相似度/漂移分析，不直接展示
    model_version: str
```

### 5.5 Open-set gate

最终动作识别必须同时满足：

```text
视频质量可用
AND 主体可用
AND 必要部位可见
AND temporal margin 达标
AND 距离已知动作原型不过远
AND 与动作专属运动模式一致
```

否则输出 `unknown` 或 `likely`，继续保留时间线，不进入动作评分。

### 5.6 计次与阶段统一接口

```python
class PhasePoint(BaseModel):
    frame_index: int
    timestamp_ms: int
    phase: str
    value: float | None = None

class RepSegment(BaseModel):
    rep_index: int
    start_ms: int
    peak_ms: int
    end_ms: int
    complete: bool
    phases: list[PhasePoint]

class CounterResult(BaseModel):
    available: bool
    exercise_id: str
    reps: int | None
    hold_seconds: float | None
    segments: list[RepSegment]
    reason_unavailable: str | None = None

def evaluate_counter(
    exercise_id: str,
    landmarks: LandmarkSequence,
    phase_output: TemporalMotionOutput,
) -> CounterResult: ...
```

动态动作使用滞回阈值 + 最短阶段时长 + 方向反转；静态动作使用姿态进入/退出阈值 + 容错窗口。模型 phase 只提供候选，最终计数由可审计状态机完成。

### 5.7 动作专属质量测量

禁止一个通用公式给所有动作评分。每个 Gold 动作定义：

```python
class MeasurementDefinition(BaseModel):
    key: str
    unit: str
    visible_regions: set[str]
    valid_views: set[str]
    per_rep: bool
    thresholds: dict[str, float]
    severity_map: dict[str, str]

class RepFinding(BaseModel):
    rep_index: int | None
    measurement_key: str
    observed_value: float | None
    status: Literal["good", "attention", "unavailable"]
    evidence_frame_ids: list[str]
    explanation_key: str
```

示例：

- 深蹲：最低点膝/髋角、躯干倾角、左右膝轨迹、深度一致性；
- 俯卧撑：肘角范围、肩髋踝直线偏差、底部深度、重复节奏；
- 弓步蹲：前膝控制、左右稳定、后腿深度、步距一致性；
- 弯举：肘部漂移、活动范围、离心控制、左右对称；
- 侧平举：最高角度、耸肩代理、躯干摆动、速度控制；
- 肩推：腕肘堆叠代理、左右同步、活动范围、躯干后仰；
- 平板：肩髋踝线偏差、髋部下沉/抬高、有效保持时长；
- 臀桥：髋伸展幅度、顶部保持、左右骨盆差、节奏。

每个 finding 必须带证据帧；视角不支持时返回 unavailable，不得换一个无关指标凑分。

### 5.8 评分协议

不再优先展示 0–100 总分。用户主视图优先显示：

```text
完成次数/保持时长
稳定项 1–2 个
最值得调整 1 个
下一组的单一动作提示
```

内部可保留分数用于同版本趋势，但必须满足：

- 同动作、同测量器、同模型主版本；
- 至少三个有效 measurement；
- 不可用项不按 0 分处理；
- 用户确认标签不能自动产生评分；
- 跨机位比较时标记 view mismatch。

### 5.9 能力接口

```http
GET /api/v1/fitness/motion-capabilities
GET /api/v1/media/motion-analyses/{id}/reps
GET /api/v1/media/motion-analyses/{id}/measurements
GET /api/v1/motion/progress?exercise_id=squat&days=30
```

Harness Tools：

```text
motion.reps.read
motion.measurements.read
motion.progress.read
motion.next_focus.read
```

`motion.next_focus.read` 只返回一个有足够证据的改进重点，并带 measurement、时间范围、样本数和版本。

### 5.10 能力完成线

不考虑真机，但必须通过离线独立数据：

- Gold 8 动作 all-sample macro F1 ≥ 0.85；
- unknown recall ≥ 0.85；
- 动态动作计次 MAE ≤ 1 次/视频；
- 静态动作时长 MAE ≤ 1.5 秒；
- phase 边界中位误差 ≤ 300ms；
- 高严重度 finding 精确率 ≥ 0.85；
- 不支持机位的误评分率 ≤ 2%；
- 每个 finding 100% 有证据帧或明确 unavailable。

指标未达标时，动作保留 Silver，不进入 Gold。

---

## 6. 能力三：Interactive Food 2.0

### 6.1 核心转变

旧能力：

```text
一张照片 → VLM 同时猜菜名、重量、做法、油盐、热量和营养素
```

目标能力：

```text
一张照片
  → 识别可见食物与容器
  → 输出初始区间和最大不确定性
  → 只追问 1–2 个最能缩小区间的问题
  → 用户选择/输入
  → 营养数据库确定性计算
  → 个人份量先验校正
  → 可编辑草稿并保存
```

真正的能力创新不是“再换一个视觉模型”，而是系统知道自己缺什么信息，并主动用最少交互补齐。

### 6.2 结果分层

```python
class ObservableFoodItem(BaseModel):
    label: str
    visible: bool
    container: str | None
    portion_anchor: str | None
    estimated_mass_range_g: tuple[float, float] | None
    evidence: str
    uncertainty_keys: list[str]

class FoodAssumption(BaseModel):
    key: str
    value: str | float
    source: Literal["visual", "user_answer", "user_history", "default_prior"]
    confidence_level: Literal["high", "medium", "low"]

class FoodEstimateDraft(BaseModel):
    analysis_id: int
    visible_items: list[ObservableFoodItem]
    assumptions: list[FoodAssumption]
    questions: list["ClarificationQuestion"]
    estimate: "NutrientEstimate"
```

### 6.3 主动追问引擎

```python
class ClarificationOption(BaseModel):
    key: str
    label: str
    effect: dict[str, float]

class ClarificationQuestion(BaseModel):
    question_id: str
    kind: Literal["portion", "cooking_oil", "hidden_sauce", "staple_amount", "food_variant"]
    prompt: str
    options: list[ClarificationOption]
    expected_range_reduction: float
```

选择问题的目标：最大化预期热量区间缩减，同时把问题限制为最多两个。

```python
score(question) = expected_range_reduction * answerability * relevance
```

典型问题：

- “这碗米饭更接近半碗、一碗还是一碗半？”
- “这道菜是清炒、普通用油还是明显油炸？”
- “图片外还有没有酱汁或饮料需要一起记录？”

不得追问图片已经能清楚观察的信息，也不得连续提出大量问题增加记录负担。

### 6.4 确定性营养计算

新增本地审核食物库：

```python
class FoodReference(BaseModel):
    food_key: str
    name_zh: str
    aliases: list[str]
    per_100g: Nutrients
    density_priors: dict[str, float]
    cooking_adjustments: dict[str, NutrientAdjustment]
    source_id: str
    reviewed_at: date
```

计算：

```text
item nutrients
= food_reference(per_100g)
× confirmed_or_estimated_mass / 100
+ cooking oil/sauce adjustment
```

VLM 负责将可见项映射到 `food_key` 候选；最终营养值由本地数据库计算，不接受 VLM 直接生成的宏量营养数值覆盖确定性结果。

### 6.5 个人份量记忆

记录用户确认后的低敏偏好：

```python
class UserFoodPrior(BaseModel):
    user_id: int
    food_key: str
    context_key: str          # home_bowl / takeaway_box / breakfast 等
    median_mass_g: float
    sample_count: int
    dispersion: float
    last_confirmed_at: datetime
```

规则：

- 至少 3 次用户确认后才影响默认份量；
- 只影响初始建议，不跳过本次确认；
- 用户可查看、修改和清除；
- 不将异常值自动写入先验；
- 先验不改变食物营养数据库。

### 6.6 包装食品增强

对于包装食品，优先能力顺序：

```text
条码/营养标签 OCR
  > 用户选择标准商品
  > 图片食物估算
```

新增可选结构：

```python
class NutritionLabelExtraction(BaseModel):
    serving_size: float | None
    serving_unit: str
    nutrients_per_serving: Nutrients | None
    nutrients_per_100g: Nutrients | None
    confidence_fields: dict[str, str]
    needs_confirmation: list[str]
```

OCR 结果必须逐字段确认，禁止整段低质量 OCR 直接入库。

### 6.7 API

```http
POST /api/v1/vision/food-jobs
GET  /api/v1/vision/food-analysis/{id}
POST /api/v1/vision/food-analysis/{id}/answers
POST /api/v1/vision/food-analysis/{id}/recalculate
PUT  /api/v1/vision/food-analysis/{id}/correct
POST /api/v1/vision/food-analysis/{id}/finalize
GET  /api/v1/food/priors
DELETE /api/v1/food/priors/{food_key}
```

回答接口：

```json
{
  "answers": [
    {"question_id": "q_portion_rice", "option_key": "one_bowl"},
    {"question_id": "q_oil", "option_key": "normal"}
  ]
}
```

返回更新后的 assumptions、estimate、区间缩减和仍未知项。

### 6.8 Harness Tools

```text
food.analysis.read
food.assumptions.read
food.clarifications.propose
food.priors.read
diet.history.read
```

Agent 只能解释识餐草稿和提出追问；真正修改、重新计算和保存由领域接口及用户确认完成。

### 6.9 能力完成线

- 可见食物 item recall ≥ 0.85；
- 严重幻觉率 ≤ 2%；
- 两个问题后热量区间宽度中位数较初始缩小 ≥ 30%；
- 回答后热量区间覆盖率 ≥ 80%；
- 采用营养数据库计算的记录占比 100%；
- 包装食品 OCR 关键字段准确率 ≥ 95% 后才允许快捷确认；
- 用户不回答时保留宽区间，不生成伪精确点值。

---

## 7. 能力四：Constraint-based Adaptive Plan Engine

### 7.1 从“生成计划”升级为“求解计划”

LLM 不直接决定最终训练安排。流程改为：

```text
Health State
  → 候选活动/动作
  → 硬约束过滤
  → 软目标打分
  → 周计划求解
  → deterministic validation
  → Agent 解释与两档选择
```

### 7.2 计划输入

```python
class PlanRequest(BaseModel):
    goal: Literal["fat_loss", "strength", "fitness", "posture", "maintain"]
    days_per_week: int = Field(ge=1, le=7)
    minutes_per_session: int = Field(ge=10, le=120)
    equipment: set[str]
    preferred_days: list[int]
    excluded_exercises: set[str]
    intensity_preference: Literal["gentle", "standard"]

class PlanContext(BaseModel):
    health_state: HealthStateSnapshot
    motion_focus: list[str]
    recent_load: dict[str, float]
    recovery_constraints: list[HealthConstraint]
    adherence_history: dict[str, float]
```

### 7.3 硬约束

- 每项动作必须满足器械和动作能力；
- 恢复优先状态不安排高强度；
- 同一肌群高负荷之间至少留恢复间隔；
- 单次总时长不超过用户时间；
- 未通过 Gold 的动作不能根据视频分数自动加负荷；
- 安全红旗直接停止自动计划并转介；
- 用户明确排除动作永不自动加入。

### 7.4 软目标

```text
maximize:
  goal_alignment
  + adherence_probability
  + movement_balance
  + motion_focus_relevance
  + preference_match
  - fatigue_risk
  - complexity_cost
```

首版无需复杂整数规划库，可用候选生成 + beam search；但输出必须通过统一 validator。

```python
def solve_weekly_plan(request: PlanRequest, context: PlanContext) -> PlanCandidate: ...
def validate_plan(plan: PlanCandidate, constraints: list[HealthConstraint]) -> ValidationReport: ...
```

### 7.5 滚动重规划

每天记录变化后只调整未来项目：

- 已完成项目冻结；
- 未完成项目不自动判定失败；
- 睡眠不足时降低下一次负荷或改恢复；
- 连续完成困难时减少复杂度，而不是不断增加提醒；
- 动作反馈指出一个可靠薄弱点时，把对应低负荷技术练习加入下一周期；
- 每次重规划都生成 diff 和原因，等待用户确认。

### 7.6 API 与 Tool

```http
POST /api/v1/plans/simulate
POST /api/v1/plans/{id}/replan
GET  /api/v1/plans/{id}/diff
```

```text
plan.constraints.read
plan.simulate
plan.diff.read
plan.replan.propose
```

`plan.simulate` 是只读工具；`plan.apply/replan.apply` 仍需 Action Proposal。

---

## 8. 能力五：Harness 3.0

### 8.1 Harness 不再以“多 Agent 数量”为中心

目标是最小必要推理路径：

- 规则能完成的安全、聚合、约束和写入，不调用 LLM；
- 单领域任务一个 Worker 完成；
- 只有跨领域冲突才启用 Router + 多 Worker + Decision；
- 工具返回结构化事实，模型不直接访问数据库；
- 所有写入都变成持久化 Action Proposal；
- 每轮有模型调用预算、耗时预算和失败回退。

### 8.2 Capability Graph

在 Tool Registry 之上增加能力声明：

```python
class CapabilitySpec(BaseModel):
    key: str
    tools: set[str]
    required_state_keys: set[str]
    produces: set[str]
    side_effect: Literal["none", "proposal", "write"]
    risk_level: str
    availability_check: str
```

示例：

```text
motion.gold_coaching
  requires: motion_analysis + gold_evaluator + sufficient_evidence
  tools: motion.reps.read, motion.measurements.read, motion.next_focus.read
  produces: coaching_focus

food.interactive_estimate
  requires: food_vision + food_reference_db
  tools: food.analysis.read, food.clarifications.propose
  produces: nutrient_estimate_draft
```

Router 选择 capability，而不是仅选择人格角色。不可用 capability 在 manifest 中带明确原因。

### 8.3 统一 Decision Contract

```python
class DecisionCandidate(BaseModel):
    candidate_id: str
    objective: str
    evidence_refs: list[EvidenceRef]
    assumptions: list[str]
    missing_data: list[str]
    constraints_checked: list[str]
    expected_benefit: str
    burden_level: Literal["low", "medium", "high"]
    action_key: str | None
    action_arguments: dict = {}

class HealthDecision(BaseModel):
    decision_id: str
    selected_candidate_id: str | None
    observations: list[str]
    evidence_quality: Literal["high", "medium", "low"]
    alternatives: list[DecisionCandidate]
    why_selected: str
    stop_conditions: list[str]
    proposal_id: str | None
```

Decision Agent 只能在候选和约束范围内选择，不得凭空创建不存在的工具或记录。

### 8.4 Next Best Action

系统每次主动建议只选择一个主要行动：

```python
utility = (
    expected_value
    * evidence_quality
    * adherence_probability
    - user_burden
    - risk_penalty
    - notification_fatigue
)
```

候选来源：主动信号、未完成计划、动作薄弱点、饮食记录缺口、正在进行的微实验。硬约束先过滤，utility 只做剩余排序。

### 8.5 长期结构化记忆

替换“最近 3 次对话摘要”作为主要记忆来源：

```python
class UserPreferenceMemory(BaseModel):
    key: str
    value: str
    source: Literal["explicit", "confirmed_action", "repeated_choice"]
    evidence_count: int
    confidence_level: Literal["high", "medium", "low"]
    created_at: datetime
    last_confirmed_at: datetime
    expires_at: datetime | None
```

首批记忆：

- 偏好温和版还是标准版；
- 可接受的单次训练时长；
- 常用器械和训练日；
- 常见餐份量；
- 不希望被频繁提醒的时间/信号；
- 用户明确排除的动作和食物。

规则：

- 明确输入一次即可记为 explicit；
- 推断偏好至少三次一致选择；
- 记忆可查看、修改、清除；
- 安全规则不受偏好覆盖；
- 对话文本本身不直接成为长期事实。

### 8.6 Action Runtime

落实完整整改文档中的：

```text
proposal → confirm/reject → execute → observe result → audit
```

首批统一接入：

- `plan.apply`；
- `plan.replan.apply`；
- `goal.adjustment.apply`；
- `diet.ai.finalize`；
- `experiment.start/finish/cancel`。

隐私删除继续保持更严格专用确认，不作为普通 Agent Action。

### 8.7 模型调用预算

| 场景 | 最大调用 |
| --- | ---: |
| 安全拦截/纯记录查询 | 0–1 |
| 单领域建议 | 2 |
| 跨领域计划 | 4 |
| 含动作或识餐证据的复杂复盘 | 5 |

工具、规则和优化器不计模型调用，但必须记录耗时。超过预算时返回已有可靠结论和缺失项，不继续递归调用。

---

## 9. 能力六：Outcome Learning without Silent Training

### 9.1 目标

让系统随使用逐步适合个人，但不自动微调大模型、不把短期相关性包装成因果。

### 9.2 可学习对象

- 行动接受概率；
- 温和/标准方案偏好；
- 用户可完成的训练时长；
- 提醒疲劳；
- 常见食物份量；
- 哪类建议被标记为有帮助/不准确；
- 计划项目完成率。

### 9.3 不学习的对象

- 医疗诊断；
- 安全阈值；
- 药物和疾病建议；
- 未经确认的模型推断；
- 单次异常记录；
- 动作模型权重和大模型权重。

### 9.4 策略模型

初期使用可解释 Bayesian/Beta 统计，不上复杂强化学习：

```python
class ActionPolicyStat(BaseModel):
    user_id: int
    action_family: str
    variant: str
    offered: int
    accepted: int
    completed: int
    helpful: int
    inaccurate: int
    alpha: float
    beta: float
```

排序使用后验均值并加入最小探索，但高风险建议永不探索。

```text
preference_score = (accepted + alpha) / (offered + alpha + beta)
completion_score = (completed + alpha) / (accepted + alpha + beta)
```

样本不足时回到全局安全默认值，并显示“还没有足够个人记录”。

### 9.5 微实验升级

现有五类微实验升级为统一协议：

- 基线窗口和观察窗口版本化；
- 一个实验只改变一个主要行为；
- 每日行动可转为计划任务；
- 完成后写入 Outcome；
- 下一次同类建议引用上一次结果；
- `insufficient_data` 不更新偏好为正向；
- 主动停止只更新负担偏好，不推断方案无效。

新增 Tool：

```text
outcomes.history.read
preferences.read
experiment.result.read
next_action.rank
```

---

## 10. 三条完整能力闭环

### 10.1 动作改进闭环

```text
上传深蹲视频
→ Gold 动作识别
→ 分出 5 次重复
→ 发现第 3–5 次深度下降，证据帧可追溯
→ Health State 写入 squat.depth_consistency
→ Planner 提出下一次“减次数、保深度”的方案
→ 用户确认加入计划
→ 下次同机位复测
→ 只比较同版本测量，生成进步/不确定结论
```

### 10.2 饮食记录闭环

```text
拍照
→ 看见米饭、鸡肉、蔬菜
→ 系统发现米饭份量和用油是最大不确定性
→ 追问“一碗/半碗”“清炒/普通用油”
→ 营养数据库重算并缩小区间
→ 用户确认午餐
→ 个人份量先验累计
→ Agent 后续只引用确认后的饮食记录
```

### 10.3 自适应计划闭环

```text
近 7 日睡眠不足 + 本周仅完成 1 次训练
→ State Engine 生成恢复约束与执行困难信号
→ Solver 生成温和/标准两个合法候选
→ Next Best Action 选择低负担恢复方案
→ 用户确认
→ 未来计划发生可见 diff
→ 根据真实完成和反馈更新偏好
→ 下一周期减少复杂度或恢复原计划
```

---

## 11. 实施阶段

### Phase 0：修通底座

必须先完成完整整改文档中的 P0：

- Motion V2 字段、预览、child 重分析、MediaPipe 路径；
- 识餐记录查看/编辑/餐次；
- 重复 API Router；
- Action Proposal 基础；
- 统一错误和幂等。

退出条件：已有能力不再因契约错误而丢失。

### Phase 1：统一健康状态

交付：

- `HealthStateSnapshot`；
- feature registry、版本、evidence hash 和增量失效；
- state/signals/constraints/outcomes Tools；
- `health.context.read` 迁移到状态层；
- 计划和主动提醒统一消费状态。

退出条件：同一事实只在一处计算，任何建议能定位到状态值和证据。

### Phase 2：动作 Gold 8

实施顺序：

1. squat / bicep_curl / plank 三种代表性动作；
2. pushup / lunge / lateral_raise；
3. shoulder_press / hip_bridge；
4. 统一时序模型、open-set、phase、counter、measurement、evidence；
5. progress 与 next_focus Tool。

退出条件：Gold 门槛逐动作通过，未通过的自动降为 Silver。

### Phase 3：交互式识餐

交付：

- 审核食物营养库；
- 可见项与 assumption 分离；
- 最大不确定性问题选择；
- 回答后确定性重算；
- 个人份量先验；
- 包装食品 OCR 可作为后续子包。

退出条件：用户回答最多两个问题后，区间明显缩小且覆盖率达标。

### Phase 4：约束计划与 Harness 3.0

交付：

- plan solver/validator/replan diff；
- capability graph；
- Decision Contract；
- Next Best Action；
- Action Runtime；
- 结构化长期记忆与模型调用预算。

退出条件：感知证据能进入状态和计划，用户确认后形成真实业务变化。

### Phase 5：Outcome Learning

交付：

- preference memory；
- action policy stats；
- 微实验 Outcome；
- 下一轮策略排序；
- 用户查看/清除个人化依据。

退出条件：系统能说明“这次为何更偏向温和/标准方案”，且依据来自确认行为而非模型猜测。

---

## 12. 代码落点

### Backend 新增建议

```text
app/services/health_state/
├─ contracts.py
├─ features.py
├─ registry.py
├─ builder.py
├─ invalidation.py
└─ repository.py

app/services/planning/
├─ contracts.py
├─ candidates.py
├─ constraints.py
├─ solver.py
├─ validator.py
└─ replan.py

app/services/food/
├─ references.py
├─ assumptions.py
├─ clarifications.py
├─ calculator.py
└─ priors.py

app/harness/
├─ capabilities.py
├─ decision_contract.py
├─ next_action.py
├─ memory.py
└─ action_runtime.py
```

### Worker 新增建议

```text
healthmate_worker/processors/motion_gold/
├─ features.py
├─ temporal.py
├─ open_set.py
├─ phases.py
├─ counters.py
├─ measurements.py
├─ evidence.py
└─ evaluators/
   ├─ squat.py
   ├─ pushup.py
   ├─ lunge.py
   ├─ bicep_curl.py
   ├─ lateral_raise.py
   ├─ shoulder_press.py
   ├─ plank.py
   └─ hip_bridge.py
```

### 小程序改动重点

```text
utils/healthStatePresentation.js
utils/foodClarification.js
utils/motionGoldView.js
utils/actionProposal.js

pages/scan/              # 追问与重算
pages/media/             # rep、单一改进重点、进步对比
pages/plan/              # 两档计划与 replan diff
pages/chat/              # Action proposal 与依据
pages/records/diet/      # 记录回看和个人份量来源
```

### 数据迁移建议

在完整整改 `0026` 之后按能力拆分，避免一个超大迁移：

```text
0027_health_state_features
0028_motion_gold_metrics
0029_food_references_and_priors
0030_harness_memory_and_policy
```

---

## 13. 能力级自动验收

用户已明确本方案不考虑真机测试和汇报，但能力开发仍需要离线自动验收，否则无法判断是否真的升级。

### 13.1 Health State

- 同一输入产生稳定 snapshot；
- 缺失日不按 0；
- 修改/删除记录后相关特征失效；
- 不同模型版本的动作分数不直接比较；
- 每个 derived 值有 evidence refs 和 feature version。

### 13.2 Motion Gold

- 使用独立 subject split；
- action、unknown、phase、reps、finding 分开报告；
- 全样本分母包含拒识和失败；
- 每个 Gold 动作单独过门槛；
- 无证据 finding 数量必须为 0。

### 13.3 Food Interactive

- 初始与回答后区间都可复算；
- 问题数 ≤2；
- 回答后区间缩减可测；
- VLM 数值不能覆盖本地确定性计算；
- 用户先验少于 3 次不生效；
- 隐藏食材不高置信写入。

### 13.4 Plan Solver

- 随机生成约束组合做 property tests；
- 任何输出都满足硬约束；
- replan 不修改已完成项目；
- 相同输入和版本输出稳定；
- LLM 输出非法时 validator 能拒绝并回退。

### 13.5 Harness

- 未授权工具不能调用；
- capability 不可用时不能路由；
- Action 未确认不写库；
- 重复确认只执行一次；
- 简单任务调用预算 ≤2；
- 跨领域任务调用预算 ≤5；
- 长期记忆只来自明确/重复确认行为；
- 清除记忆后不再影响排序。

---

### 13.6 能力诚实性（本次实施新增）

§13.1–§13.5 检查的是"功能是否按设计工作"，但一个能力系统最容易出的问题不是功能坏了，而是**声称比能做到的更多**。因此新增一组自动门禁：

- Gold 阈值不得被单方面下调（worker 的 §5.10 阈值由 AST 静态核对，不依赖 worker 运行时可导入）；
- 目录中声明的 scorer/计次器若含 gold / gated / verified / calibrated 字样，必须有通过门禁的评测记录；
- 没有测量器的计划动作必须出现在 `PLANNING_ONLY_IDS`，不得显示数值评分；
- 食物库未逐项复核时必须报告 `reviewed=False` 且保留 `seed_unreviewed` 来源；
- 未测量的能力一律视为不可用并给出原因；
- `/api/v1/capabilities/honesty` 必须公布当前允许的宣传口径。

命令：`backend\.venv\Scripts\python.exe scripts\audit_capability_honesty.py`
（代码层条款）与 `--database-url <已迁移库>`（含数据库条款）。
数据库条款另有 `backend/tests/test_phase_capability_honesty.py` 在全新迁移库上覆盖，
其中包含一个**注入测试**：伪造一个 Gold 声明，审计必须失败。

---

## 13bis. 本次实施的验收结果（点对点，可复跑）

本节只写实际执行并通过的结果。无法在当前离线环境完成、需要真实数据或真实设备的部分，
统一在 §13ter 列出，不计入下表。

> 配套交付报告：`docs/HEALTHMATE_CAPABILITY_UPGRADE_DELIVERY_2026-10-02.md`
> （含逐 Phase 交付内容、被验收条款抓出的 12 个真实缺陷、以及未完成项清单）

| 协议条款 | 状态 | 证据 |
| --- | --- | --- |
| §13.1 状态稳定 / 缺失不为 0 / 失效传播 / 跨版本不比较 / 证据引用 | ✅ | `backend/tests/test_health_state_engine.py`（15 项） |
| §13.2 Gold 分层与门禁算术、证据完备性、无证据 finding 为 0 | ✅（骨架） | `ai-worker/tests/test_motion_gold.py`（123 项）；**不含**准确率指标 |
| §13.3 区间可复算 / ≤2 问 / 区间缩减可测 / 模型数值不覆盖 / 先验≥3 / 隐藏食材追问 | ✅ | `backend/tests/test_food_interactive.py`（18 项） |
| §13.4 约束组合 property tests / 硬约束恒成立 / replan 冻结已完成 / 输出稳定 / 非法输出被拒 | ✅ | `backend/tests/test_plan_solver_and_harness3.py` |
| §13.5 未授权工具 / 能力不可用不路由 / 未确认不写库 / 重复确认一次 / 预算 2 与 5 / 记忆来源与清除 | ✅ | `test_plan_solver_and_harness3.py`、`test_model_call_budget.py`、`test_outcome_learning_wiring.py`、`test_long_term_memory.py` |
| §13.6 能力诚实性 | ✅ | `scripts/audit_capability_honesty.py`、`test_phase_capability_honesty.py`（11 项，含注入测试） |
| §4.5 增量失效（编辑记录后陈旧值不得被读取） | ✅ | `test_state_invalidation_wiring.py`（13 项，断言真实 HTTP 路径触发失效） |
| §9.5 四个点名工具 | ✅ | `outcomes.history.read` / `preferences.read` / `experiment.result.read` / `next_action.rank` 均已注册并按 persona 授权 |
| §14 第 7 条跨模态闭环（用户可见） | ✅ | `miniprogram/pages/state/index`「状态与下一步」+ 首页入口；`statePagePresentation.test.js`（10 项）钉住诚实性约束 |

验收命令与实测结果：

| 范围 | 命令 | 结果 |
| --- | --- | --- |
| 后端 | `backend\.venv\Scripts\python.exe -m pytest -q` | **488 passed**, 0 failed |
| Worker | `ai-worker\.venv\Scripts\python.exe -m pytest -q` | **279 passed**, 0 failed |
| 小程序 | `node --test "tests/*.test.js"` | **121 passed**, 0 failed |
| Worker 交付门禁 | `D:\HealthMate\.venv\Scripts\python.exe scripts\strict_doctor.py` | 5/5 硬门禁通过，1 条 ASCII 路径 advisory |
| 能力诚实性 | `python scripts\audit_capability_honesty.py` | OK（代码层 4 条款） |
| OpenAPI | `python scripts\audit_openapi.py` | OK |
| 迁移 head | `python scripts\audit_migration_head.py` | OK（单 head） |
| 隐私覆盖 | `python scripts\audit_privacy_coverage.py` | OK |
| 数据一致性 | `python scripts\audit_data_consistency.py --database-url <全新迁移库>` | OK（exit 0） |

> 注：`audit_data_consistency.py` 不加参数时会审计 `.env` 指向的生产库，该库中已存在
> 整改前遗留的重复事件与悬空外键；这是**数据问题不是代码问题**，在全新迁移库上该审计
> 通过。详见配套交付报告 §4。

### 实施中被测试发现的真实缺陷（已修复）

这些不是"顺手改的"，而是 §13 的验收条款确实抓出来的问题：

1. **计划求解器缺多样性**：按分数排序时"每天做同样三个动作"得分最高。已加入显式
   `variety` 权重。
2. **自重动作被错判为不可用**：用户声明"只有哑铃"时，自重动作被判为器械缺失，计划为空。
   自重现在隐式可用。
3. **覆盖不足时连恢复建议也被判非法**：新用户因此得不到任何帮助。覆盖门禁现在只约束
   **训练负荷**，安全规则仍然约束一切。
4. **追问引擎会问已知信息**：草稿里已带份量时仍追问份量，浪费两个名额之一。已改为按
   "区间缩减"与"系统性偏差缩减"分设阈值并为用油/酱汁保留一个名额。
5. **简单任务预算形同虚设**：`TurnBudget` 永远给 5 次，§13.5 的 ≤2 只存在于注释里。
   现在由与路由器同源的领域关键词推导。
6. **`insufficient_data` 会偷偷加强后验**：微实验没有随访记录时，`completed` 仍然
   `alpha += 1`。已改为先看结论。
7. **结果记录会被静默丢弃**：执行器写入了一个未登记的结论文本，异常被 `except` 吞掉，
   该结果从未进入后验。现在结论值受词表约束，且写入前先声明词表。
8. **单个动作通过评测会让整个目录变成 Gold**：能力图按动作逐项判定。
9. **测试隔离会删掉随包发布的食物库**：`conftest` 的清表清单漏了 `food_references`，
   导致确定性营养计算回落到 0。已加入不可变种子数据白名单。

---

## 13ter. 本次**未**完成、且需要真实数据或设备的部分

以下项目在本机离线环境中无法完成，任何"已完成"的表述都会是不诚实的：

| 项目 | 为什么无法在此完成 | 需要的输入 |
| --- | --- | --- |
| §13.2 的准确率指标（macro_f1、rep_mae、hold_mae、phase 边界误差、高严重度精确率、非法机位误评分率、unknown 召回） | 仓库内没有标注数据集，也没有训练好的时序动作分类器；`evaluate_gold_gate({})` 在没有指标时**如实返回 silver** 并列出缺少的 8 项 | ≥8 个动作、独立 subject split 的标注视频；时序分类器训练 |
| 动作 Gold 的真人双人复核 | 需要真人评审 | 两名评审员与一致性统计 |
| 食物库营养师逐项复核 | `seed_data.py` 的数值是公开成分表参考均值，标注为 `seed_unreviewed` | 营养师复核 + 真实来源 id（`review_reference` 已就绪） |
| 真实食物基准评测（≥100 张冻结图片） | 需要冻结图片集 | 数据集 + 评测脚本 |
| Agent 真实模型双人评审 | 需要真实模型调用与人工评审 | 评审流程与预算 |
| 云对象存储删除证明 | 需要平台凭据 | 云存储账号 |
| 生产监控告警 | 需要部署环境 | 监控平台 |
| 真机端到端 | 明确不在本次范围 | 真机 |

**如何判断上面每项是否已具备开工条件**：`GET /api/v1/capabilities/honesty` 会返回当前
真实状态；`evaluate_motion_gold` 在缺少评测报告时返回 `tier="unavailable"`，不会返回 Gold。

---

## 14. 最终能力形态

完成后，HealthMate 不应被理解为“识餐 + 动作识别 + 大模型聊天”的功能集合，而应形成以下稳定能力：

1. **看得见但不乱猜**：动作和食物只输出有证据的观察，缺信息主动追问或拒绝评分；
2. **能形成个人状态**：所有记录、感知和结果进入版本化 Health State；
3. **能做合法决策**：计划和建议先过约束，再由 Agent 解释；
4. **能真正执行**：所有写操作通过统一 Action Runtime，由用户确认后落地；
5. **能观察结果**：行动完成后进入 Outcome，而不是对话结束即消失；
6. **能逐渐适应个人**：根据确认选择和完成情况调整排序，但不偷偷训练、不突破安全边界；
7. **能跨模态闭环**：识餐、动作、睡眠、计划不是独立页面，而是同一个行动循环的证据来源。

能力建设的最终判断标准只有一个：

> 当用户上传一次动作、记录一餐或完成一次微实验后，系统是否能把这条新证据可靠地转化为下一步更合适、可确认、可执行、可复盘的行动，而不是只生成一段更长的文字。

