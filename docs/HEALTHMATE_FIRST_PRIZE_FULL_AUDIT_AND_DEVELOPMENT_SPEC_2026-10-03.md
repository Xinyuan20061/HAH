# HealthMate 全项目能力审计与开发规范

日期：2026-10-03  
状态：实施规格，不是已完成报告  
范围：小程序、FastAPI、AI Worker、Harness、个人策略学习、动作/识餐、数据、评测、隐私和工程交付。

## 0. 结论与边界

项目已有相当多的后端结构、保护性降级和测试，但**目前不能把“代码里有闭环对象”当成“用户可完成且证据可信的闭环”**。最重要的缺口不是换更大的模型，而是：个人策略学习没有完整用户入口，证据源可由客户端自行填报，插件授权只停留在开关层，动作时间轴状态与画面资产链断裂，数值评分与能力门禁不一致。先修这五条，否则核心创新无法被用户感知，也无法用可靠实验验证。

“一等奖”是目标而非软件验收状态。本文定义可交付、可反证的能力和实验，不承诺奖项。现有算法是对已有思想的工程综合；只有冻结对照、消融和真实用户结果显示出独特增益后，才可作创新性效果主张。

本次证据来自当前工作区源码、截图、既有冻结报告和定向测试；未操作生产数据库、CloudBase、真实微信设备或外部模型，未对所有运行路径作动态证明。新发现均以代码路径标注；“建议新增”不等于“当前已存在”。

## 1. 产品主轴：可检验、可撤销的个人健康行动协议

唯一主创新点定义为 **Evidence-Gated Personal Action Protocol（EGPAP，证据门控的个人行动协议）**：

```text
用户目标/约束 → 可执行候选 → 来源可核验的行动协议 → 用户确认
→ 按窗口记录执行/结果 → 证据门控裁决 → 可撤销的条件化记忆
→ 下一次行动/补证/暂缓
```

动作和识餐不是各自孤立的“AI 功能”，而是观测器；Harness 插件不是一个新开关菜单，而是经过权限、数据血缘、能力声明、用户控制统一约束的观测器/行动候选接入方式。对用户可见的核心价值是：“为什么建议我这样做、我做了没有、结果有没有支持、下一步为何改变”。

理论边界：个体周期可提供**个人描述性证据**，不自动产生因果结论；观察数据的混杂、缺失、选择偏差须保留。仅当未来有经同意的随机化/交叉周期及合适分析时，才考虑更强因果表述。个体试验的完整协议/报告思想可参考 [CENT N-of-1 指南](https://www.bmj.com/content/350/bmj.h1738)，情境化干预研究可参考 [HeartSteps 微随机化试验](https://pubmed.ncbi.nlm.nih.gov/30192907/)。这些是方法依据，不是本项目已具备相同证据的证明。

### 1.1 技术贡献必须落在“证据编译器”而非模型拼接

建议将内核命名为**可撤销证据编译器**：不同模态只产生带来源和能力级别的观测；编译器把观测接入预先冻结的行动协议，再决定本次能够给用户何种等级的结论。内部保留有向依赖图：

```text
source record@revision → resolved metric@version → episode observation
→ adjudication@gate_version → conditional belief@epoch → decision@algorithm_version
```

每条边保存用户、时间窗口、计算版本和哈希。源删除/更正时，只失效其后代节点，异步重算且在完成前屏蔽旧结论；不把“更正了某条饮食记录”扩散为“全部饮食证据失效”。用户端只展示四级可理解主张：`看到/记录到` → `可计算` → `达到验证门槛` → `个人周期得到描述性支持`。任何一级缺失时降级，不用语言模型填空。跨模态插件只有提供符合此类型系统的观测和候选行动，才可参与主闭环。

形式化决策约束：先求安全/授权集合 `A_safe(s)`；对每个候选 `a`，估计执行概率 `p_exec`、在同协议/同上下文的目标支持均值 `p_support`、证据可得概率 `p_obs`、执行负担 `c`。初版确定性排序可取

```text
U(a|s) = relevance(a,s) × p_exec × shrink(p_support)
         + consent_explore × λ × p_exec × p_obs × EIG(a)
         - μ × c(a)
```

`shrink` 对小样本回收到中性先验；`EIG` 是该端点 Beta 后验下一条合格观测的期望信息增益。若安全集合为空、证据不足或最高分不正，则输出 `ask/collect/wait`，不强行推荐。此公式在 `services/policy_learning/algorithm.py` 已有雏形；本次开发重点是把它的输入变成可信的真实用户证据，并验证它相对简单基线的增益。不能把乘积概率当作临床收益，也不能把信息增益奖励用于诱导高风险尝试。

## 2. 当前状态审计：已具备什么、缺什么

| 域 | 已有基础 | 本次确认的缺口/风险 | 优先级 |
| --- | --- | --- | --- |
| 动作时间轴 | 统一结果、全帧映射、缩略图/回看按钮 | `media/index.wxml:45-69` 读 `vm.*`；`media/index.js:250-278` 却只更新根级 `activeFrame/timelineFrames`，点击后显示不动 | P0 |
| 画面证据 | 签名预览、`MotionEvidenceFrame`、短期存储 | Worker `motion_unified.py:672-751` 上传失败仍保留预设 asset ID；`media.py:1144-1163` 未查真实帧/字节就发 URL 和 `has_image` | P0 |
| 回看 | `createVideoContext().seek()` | 恢复任务时只还原资产/结果、未还原可播放 `file`；WXML 仅 `file && video` 渲染播放器，历史结果“回看”无播放器 | P0 |
| 动作评分 | 规则分、Gold 评测骨架、能力诚实 API | `orchestrator.py:742-768,888-895` 规则分由 `decision.scoreable` 放行，与 Gold 评测结果未统一；截图出现“100 分”和保留解释同时存在 | P0 |
| 指标文案 | 部分中文映射 | `motionUnifiedView.js:42-49,103-108` 未映射 `risk_index`，未知 ID 原样显示；用户看见“0 风险分”等含义不明术语 | P0 |
| 个人策略 | 编译器、三端点信念、周期、裁决、Outbox | 小程序业务代码仅调用 `/policy/decide`、`/episodes/current`；没有编译、确认、执行记录、复查/停止、依据/撤销的完整操作流 | P0 |
| 证据可信 | `PolicyObservationRef`、revision、失效队列 | `policy.py:154-178` 接收客户端任意 `source_type/id/revision/value/confirmed`；`repository.py:135-149` 直接转为证据，没有重新按当前用户和真实记录验证值 | P0 |
| 比较与版本 | 算法有上下文/单变量/协议门控 | `_build_evidence` 将前后上下文都设为原 `episode.context_key`，变化变量沿用默认值；`rebuild_beliefs:193-217` 聚合不同协议/指标版本的 unit；`/decide` 读信念也未按全部版本键过滤 | P0 |
| 来源更正 | 记录更改会触发状态/策略失效 | `health_state/invalidation.py:72-91` 以整个 source_type 扫出该用户全部有效引用；改一条饮食记录可让同域所有周期证据失效，且异常被吞后需补偿扫描 | P0 |
| 读取语义 | 状态页自称只读 | `GET /policy/decide` 直接调用会 `commit` 的 POST 决策；`GET /system/ai-worker` 调 `requeue_expired_jobs()` 并提交；食物 `/references` 在首次访问时 seed 并提交 | P1 |
| 插件体验 | 4 个审核内置能力、启停页 | `plugins.py` 固定内置，前端只有整体开关；没有用户可配置的能力流程/数据粒度/预览/试运行界面 | P0 |
| 插件隔离 | `ToolRegistry` 允许名单 | `plugins.py:136-178` 的 `data_scope` 是展示标签，运行时只看 enabled；未映射工具 fail-open；直连业务 API 的权限矩阵与工具权限不统一 | P0 |
| 网络幂等 | 创建任务使用 `Idempotency-Key` | `request.js` 在 401 重新登录及网络重试时没有传回 `extraHeaders`，使原幂等键丢失；可能重复创建或改变一次用户操作的身份 | P0 |
| 识餐 | 草稿→更正→确认入库、饮食记录入口/列表、审慎匹配 | 冻结 `food-v1/report.json` 仅 42 张、34 完成，MAPE 72.7%、区间覆盖 44.12%；库 seed 未逐项复核，不能称成熟营养测量 | P1 |
| 动作泛化 | 120 段历史基线、同集候选对照 | `motion-v1/report.md` 自动识别全样本准确率 41.67%、可评价覆盖 45.83%；`motion-v2-kinetics` 24 段融合为模拟，不能作为部署精度 | P1 |
| Gold 门禁 | 独立 `motion_gold` 包和门禁 API | `gold_eval.py:44-58` 无帧级信号则 unavailable；现有 `POST /motion-gold/{id}/evaluate` 不提供帧信号，尚不能证明真实 Gold 结果链 | P1 |
| 隐私删除 | 导出、账号删除、客户端云文件删除记录 | `privacy.py:110-150` 比较客户端提交的 file ID 集合，不等于服务端验证实际对象已删除；已有 `manual_review` 边界必须维持 | P1 |
| 文档/测试 | 后端/小程序/Worker 大量测试与历史规格 | `FINAL_RELEASE_READINESS` 的测试数和“收口”表述已过时；时间轴测试主要检查 WXML/代码接线，不能证明点击后的渲染 | P1 |

补充事实：`benchmark/dataset_registry.json` 的 REHAB24 与 Nutrition5k 本地数据不随仓库交付且许可需复核；`backend` 当前迁移头是 `0032_harness_plugins`，此前对配置数据库的 `alembic check` 报“数据库落后于代码”。不能把“有迁移脚本”当成“部署库已迁移”。本次定向运行 `mediaUnifiedFlow.test.js` + `motionUnifiedView.test.js` 为 **20/20 通过**，恰好说明现有测试未捕获截图中的交互故障。

### 2.1 不要误报为缺失的功能

识餐确认入库、饮食记录查看/编辑/删除、食物参考查询、动作纠错、个人策略后端对象、插件启停均已存在。开发任务不是重写这些基础功能，而是修复端到端语义、能力边界与用户路径。暂停插件后允许查看既有数据、停止/导出周期，不应简单把所有读写 API 全关掉。

## 3. 总体改造规则

1. **来源先于结论**：任何可影响个人记忆的数据都必须有 server-resolved source ID、归属、版本、数值和时间；客户端只能选来源与作人工确认，不能给“被系统核验的数值”直接赋值。
2. **观测与推断分离**：视频里看到的动作、规则算出的偏差、可训练建议、经过评测的质量分、医学风险是五种不同语义；不混成一个“分”。
3. **能力声明由执行链决定**：Worker 在线、模型加载、源资产存在、评测门禁、签名 URL、目标动作支持和用户授权全部满足时才显示该能力。
4. **用户一屏一任务**：每张关键卡只有一个主操作，历史和技术诊断下沉；异常给明确恢复路径。
5. **读操作无副作用，写操作幂等**：页面刷新不产生新决策；同一次动作在重试/重登录后保持同一个 idempotency key。
6. **新增插件 fail-closed**：未审核 manifest、未知工具、未绑定数据字段、未授权范围一律不能执行。
7. **评测与宣传同源**：用户页面、能力 API、文档和对外能力主张共用一份版本化能力注册表，不在前端硬编码“可评分”。

## 4. P0-A：修复动作时间轴和素材闭环

### 4.1 页面单一状态源

推荐只保存 `vm.timelineFrames` 与 `vm.activeFrame`，删除根级重复字段；或者保留根级但 WXML 全量改成根级。禁止两套并存。建议第一种：

```js
selectFrame(e) {
  const id = e.currentTarget.dataset.id
  const frame = (this.data.vm?.timelineFrames || []).find(x => x.id === id)
  if (frame) this.setData({ 'vm.activeFrame': frame })
}
replayFrame() {
  const frame = this.data.vm?.activeFrame
  if (!frame) return
  if (!this.data.playableUrl) return this.showReplayUnavailable()
  wx.createVideoContext('motionVideo', this).seek(frame.timestamp_ms / 1000)
}
```

`fillEvidencePreviews()` 必须合并到 `vm.timelineFrames`，同时用 ID 重新取 `vm.activeFrame`；不变数组可跳过 setData。切换帧不请求模型，不修改结果，不重复上传。选择态、主图、文字、反馈关联帧、seek 时间必须同一 ID。`onFrameImgError` 应按 `expired / missing / network` 分类，过期后一次刷新 `/evidence`，而非永久标记坏图。

### 4.2 视频回放来源

恢复任务时，若临时本地路径已失效，必须通过已授权资产重新取得**可播放的短期 URL**，或明确展示“视频已不可回放，仍可看文字证据”。不要对缺失播放器显示“回看这一刻”。`GET /media/{id}/playback`（新增）应验证用户归属与媒体状态，返回 `playable_url, expires_at, mime_type, duration_ms`；CloudBase 只能返回合规可播放 URL，不把 Worker 下载 URL 直接泄露为长期链接。

### 4.3 预览资产协议

Worker 上传每帧后必须拿到服务端回执，且该回执与 `run_id + frame_id + sha256` 绑定。上传失败时结果帧可保留文字，但 `preview_asset_id=null, preview_state='unavailable'`，不能保留本地预设 ID 冒充远端资产。`GET /evidence` 逐帧查询 `MotionEvidenceFrame` 且确认未过期、对象存在后才生成签名 URL；不存在则 `has_image=false, preview_url=null, unavailable_reason`。不能仅按结果 JSON 中的 ID 发链接。

新增响应示例：

```json
{
  "analysis_id": 123,
  "frames": [{
    "id": "f_001", "timestamp_ms": 2500, "phase": "最低点",
    "observation": "可见躯干前倾", "next_step": "下次保持可控节奏",
    "preview": {"state": "available", "url": "https://...", "expires_at": "...", "sha256": "..."}
  }]
}
```

兼容期保留旧 `preview_url/has_image`，但必须由真实对象状态派生；前端优先新 `preview`。图片到期 7 天后展示预期的“画面已过期”，而非“加载失败”。

### 4.4 回归用例

用微信 Page 模拟器/开发者工具事件测试：点第 2/3/末帧后，主图、标题、解释、反馈 frame_id 都变化；seek 对应秒数；恢复任务后能播或明确不可播；预览上传失败、对象被清理、签名过期均有正确状态；不允许 404 图片循环请求。截图中的三帧案例是固定回归样本。

## 5. P0-B：把个人策略变成真正的“证据→行动→结果”产品

### 5.1 用户体验

首页“下一步”卡与状态页只展示一个可理解的行动候选，例如“把晚间训练从 30 分钟调整为 15 分钟，试 7 天”，并给出“依据/当前不确定/预计负担”。点击进入协议页：用户可确认、修改合法参数、看到观察指标与结束日期。周期页展示今天是否执行、需要补的记录、停止按钮；窗口结束后显示“支持/不支持/证据不足/不可比较”及下一步。历史页展示何时、在何种条件下得过何种结论、哪些已失效；可重置个人记忆。

前端新增 `pages/policy/overview|protocol|episode|review|history`；`state` 页只作为入口。避免显示策略 ID、原始 status 或 Beta 参数。禁止用“有效”“改善健康”来翻译一次观察性目标支持。

建议前端按下面状态机实施，服务端返回 `allowed_actions`，前端不自行推断权限：

| 状态 | 用户主操作 | 服务端允许 | 异常恢复 |
| --- | --- | --- | --- |
| `draft` | 查看候选与为何提出 | 补资料、预览协议 | 缺数据列出具体补录入口 |
| `compiled` | 确认“尝试 7 天” | 创建确认提案/开始 | 协议或状态变化则重新确认 |
| `active` | 报告今日是否做、查看证据 | 执行报告、观察来源选择、停止 | 弱网按报告 ID 重试，不能重复计数 |
| `awaiting_review` | 查看窗口与证据缺口 | 补可补的来源、复查、停止 | 源过期/更正则要求重取证 |
| `reviewed` | 查看结论与依据 | 查看、反馈、重置/再开新周期 | 后续源更正显示“结论已撤回” |
| `stopped` | 查看停止原因 | 查看、导出 | 不更新目标支持信念 |

端到端接口（兼容旧 API 的具体路由可由实现阶段对齐，但请求语义不可改变）：

```text
GET  /api/v1/policy/candidates                         # 纯读取，可解释候选/缺口
POST /api/v1/policy/compile                            # 用户确定参数；返回协议哈希
POST /api/v1/policy/units/{id}/proposal                # 创建待确认行动提案
POST /api/v1/agent/actions/{proposal_id}/confirm      # 现有统一确认入口
GET  /api/v1/policy/episodes/current                   # 返回状态/今日任务/allowed_actions
POST /api/v1/policy/episodes/{id}/reports              # 幂等执行报告
POST /api/v1/policy/episodes/{id}/observations         # 仅来源引用，服务端解析
POST /api/v1/policy/episodes/{id}/review-preview       # 无结论持久写入的预览
POST /api/v1/policy/episodes/{id}/finish-proposal      # 冻结复查提案
POST /api/v1/agent/actions/{proposal_id}/confirm      # 用户确认后最终裁决
POST /api/v1/policy/episodes/{id}/stop-proposal        # 用户停止通路
GET  /api/v1/policy/episodes/{id}/explanation          # 来源、版本、有效性和限制
GET  /api/v1/policy/history?cursor=...                # 个人历史与失效标记
```

`POST /compile` 和 `/reports` 均需用户级幂等键；确认提案需要 `protocol_hash + state_snapshot_hash + capability_snapshot_hash` 三重再检查。`GET /history` 只返回当前用户，不触发重算。`review-preview` 虽为 POST，但应承诺无持久裁决写入，并与最终裁决共用同一纯函数。

### 5.2 证据源注册表（核心后端接口）

新增 `services/policy_learning/source_registry.py`。每个 endpoint 定义受信任的服务端解析器；不受信任/不存在/跨用户/版本不匹配的 source 必须拒绝。用户自报负担可作为 `user_report` 类型，需单独标注，不能伪装成饮食/打卡原始记录。

```python
@dataclass(frozen=True)
class ResolvedPoint:
    source_type: str
    source_id: str
    source_revision: int
    observed_at: datetime
    metric_key: str
    metric_version: str
    value: float
    evidence_grade: str  # observed | user_confirmed | self_report

def resolve_point(db: Session, user_id: int, ref: SourceRef,
                  expected_metric: str, window: tuple[datetime, datetime]) -> ResolvedPoint:
    # 必须按 source_type 分派 allowlist resolver；查询所有权、版本、时间窗口、
    # 未删除状态，并由服务端从源记录重新计算 value。任何失败显式抛错。
    ...
```

`POST /policy/episodes/{id}/observations` 改为 `{episode_version, refs:[{endpoint,slot,source_type,source_id,source_revision}]}`；服务端解析 value/observed_at/metric_version，不接受客户端写 `confirmed` 代替核验。人工负担用独立 `POST /reports`，保留自报属性。历史兼容端点的任意数值输入逐步废弃并拒绝进入信念。

`_build_evidence()` 只能取 `opportunity.current_report_id` 的报告，不能遍历无序报告覆盖同一 slot；重新查源版本并核验前后窗口。`baseline_context/followup_context` 要来自各期冻结状态，`changed_variables` 来自实际执行计划事件，不能由默认值凑“可比”。源记录改/删必须在同一事务内失效或写入必达 Outbox，不能仅靠 `best-effort` 异常吞掉。裁决中的 `evidence_refs_json` 填充真实来源、版本和哈希，供重放。

现有 `record_changed()` 只有 domain 入参，不能精准定位更正的 source ID。新增 `record_changed(db,user_id,source_type,source_id,revision,event_id)`；调用方在同一事务写源变更事件。异步消费者按 `(type,id)` 找依赖后代并重放。过渡期定时扫描 `source_revision` 与当前记录不一致的引用，修复因旧版 best-effort 失效而漏掉的证据；扫描结果和补偿次数入告警。

### 5.3 信念隔离与决策

信念键必须严格为 `(user_id,strategy_id,protocol_version,metric_version,context_key,endpoint,learning_epoch)`；`rebuild_beliefs()` 和 `/decide` 查询都按全部字段过滤。旧版本只能作为历史显示，不能混入新协议后验。`selection_propensity=1.0` 是确定性选择，不能拿它对未被选候选做 IPS/DR 反事实估计；在没有随机化及真实曝光日志前，仅比较描述性指标。排序候选应从 `template_id + 已冻结参数 + 当前能力/约束` 生成，不能只取最近 20 条“曾经编译”的数据库行。

决策的返回建议：

```json
{
  "decision_id": "pd_...", "state": "propose|ask|wait|blocked",
  "candidate": {"strategy_unit_id": "...", "title": "...", "burden": "low"},
  "why": [{"source_ref": "checkin:42@3", "text": "..."}],
  "unknowns": [{"code": "missing_baseline", "next_action": "补充最近 5 天记录"}],
  "claim_level": "planning_only", "requires_confirmation": true,
  "snapshot_hash": "...", "algorithm_version": "egpl-v1..."
}
```

`GET /policy/decide` 改为纯预览，或改名 `GET /policy/decision-preview`；只有 `POST /policy/decisions` 在用户确认/明确操作时持久化，且用 `Idempotency-Key`。所有页面刷新不增加决策行。`GET /strategies/{id}/memory` 只读已物化信念，不触发重建。

### 5.4 结果与安全语义

`execution`（有没有做）、`support`（预设目标是否被观察支持）、`availability`（证据是否足够）三端点继续分开。健康效果不可由“接受建议/完成任务”推断。止损/不适事件立即终止周期并提示寻求适当专业帮助；不作自动医疗判断。每个周期至多贡献一条 endpoint 观测；失效源必须重放并更新信念，界面显示“该结论已撤回”。

## 6. P0-C：Harness 变成可配置但可信的能力系统

### 6.1 双层扩展模型

用户真正需要的是“自定义我希望被怎么帮助”，不是上传 Python 脚本。先实现：

- **用户层组合能力**：从审核组件中选择目标、允许的数据源、触发条件、输出形式、通知频率、是否允许提出行动；可预览输入/输出、试运行、暂停、删除。用户配置不能提高组件原有权限。
- **开发者层扩展包**：签名审核的 manifest + schema + 受限工具适配器 + 版本迁移 + 回放测试；不在用户设备执行任意代码。未审核第三方代码沙箱是独立后续项目，不把它作为本期已支持能力。

manifest 建议：

```json
{
  "plugin_id": "motion_reflection", "version": "1.1.0", "api_version": "2",
  "review_status": "approved", "data_scopes": ["motion.analysis.read"],
  "tool_bindings": ["motion.analysis.read", "motion.timeline.read"],
  "output_contracts": ["observation.v1", "candidate_action.v1"],
  "config_schema": {"type": "object", "properties": {"frequency": {"enum": ["after_session", "weekly"]}}},
  "privacy": {"retention_days": 7, "external_transfer": false},
  "safety": {"may_propose_action": true, "may_execute_action": false}
}
```

### 6.2 权限机制

后端内部统一 `authorize_capability(user, plugin_id, operation, resource_scope, phase)`。`phase` 为 `new_work/read_history/close_existing/export/delete`：暂停后禁止新读取/新提案，允许用户查看历史、停止、导出、删除。数据范围使用稳定机器 ID，不用“本人上传的动作视频”这种显示文案作鉴权键。`ToolRegistry` 未映射工具直接拒绝；所有直连业务 API、Outbox 任务和 Agent 工具统一经过能力服务，防止只关工具却可绕开 API。用户插件默认**未启用**；迁移现有用户时给明确知情选择，而非静默扩大权限。

新增接口：

```text
GET    /api/v1/harness/plugin-catalog
GET    /api/v1/harness/installations
POST   /api/v1/harness/installations                 # 幂等创建配置
PATCH  /api/v1/harness/installations/{id}            # 版本检查、收窄范围
POST   /api/v1/harness/installations/{id}/preview    # 无副作用试运行
POST   /api/v1/harness/installations/{id}/pause
POST   /api/v1/harness/installations/{id}/resume
DELETE /api/v1/harness/installations/{id}            # 配置删除，数据处理另行确认
GET    /api/v1/harness/installations/{id}/audit      # 本人可见的数据使用记录
```

响应须包含 `effective_scopes`、`capability_state`、`last_run_at`、`last_error`、`config_version`、`reviewed_manifest_hash`。所有写操作需所有权、CAS 版本、幂等键和审计事件。界面从“健康能力”进：能力目录 → 详情（价值/数据/限制）→ 自定义配置 → 预览 → 明确授权 → 使用记录/暂停。无自定义选项的内置能力仍保留一键开关，但不能暗示它已是扩展平台。

### 6.3 示例门控代码

```python
def is_tool_enabled(db, user_id: int, tool_name: str, scopes: set[str]) -> bool:
    binding = APPROVED_TOOL_BINDINGS.get(tool_name)
    if binding is None:             # 未注册工具 fail closed
        return False
    install = get_enabled_installation(db, user_id, binding.plugin_id)
    return bool(install and binding.required_scopes <= install.effective_scopes
                and install.manifest_hash == binding.reviewed_manifest_hash)
```

## 7. P0-D：统一动作结果的能力与展示语义

结果拆为 `recognition`、`measured_events`、`rule_observations`、`validated_scores`、`limitations`。`risk_index` 重命名为“动作偏差提示”且默认不显示为 0–100 风险分；它不是损伤概率。数值质量分仅在该动作、机位、模型版本通过**逐动作冻结评测门禁**且这段视频质量达标时显示。未通过时可保留次数（若次数测量本身合格）、可见画面解释和下一次拍摄建议，但不得出现“100 分”制造高可信印象。

后端返回：

```json
{
  "capability": {"tier": "visual_feedback", "score_available": false,
    "reason_code": "NO_VALIDATED_SCORER", "evaluation_id": null},
  "metrics": [{"id": "reps", "value": 5, "unit": "次", "claim_level": "measured"}],
  "observations": [{"frame_id": "f_001", "kind": "rule_observation", "text": "..."}]
}
```

前端指标 ID 全白名单映射，未知 ID 不展示且记录诊断；不能把内部 `candidate_score`, `NOT_RECOGNIZED`, `trace-...`, `risk_index` 交给用户。训练意图与“当前可判定动作”分开：用户选了哑铃弯举不等于系统有哑铃弯举评分器。复核失败与姿态不足必须显示不同原因。将 `motion_gold` 评测需要的真实帧级信号在 Worker→后端用版本化、安全的中间资产接通，并建立过期/删除策略；没有评测报告时继续 unavailable。

## 8. P1：识餐、Agent、计划与基础体验

### 8.1 识餐

保留“图片草稿→最多两个高价值追问→人工更正份量/做法→确认入库”的优势，改进：复核食物参考库每项来源与营养单位；未复核显示“估算草稿”；不认识的食材维持 `unmapped`，不能偷借相近条目。图片分析失败仍能手动记餐；保存后明确跳转记录明细。个人份量先验只能用于**候选份量**并展示来源与可撤销，不可覆盖用户本次确认。冻结集至少分中餐多食材/遮挡/光照/份量区间，报告完成率、食材 P/R/F1、重量/能量 MAE、区间宽度及覆盖、人工更正耗时；当前 42 张报告只算旧基线。

### 8.2 Agent 与计划

Agent 的角色是解释和编译经审核的候选，不能写结论、造证据或绕过确认。RAG 的资料来源、检索版本和拒答原因必须可追踪；提示注入/跨用户访问/预算耗尽都要故障注入。计划建议应显示“依据哪条用户目标/限制、预计负担、可替代选项”，若资料/Worker 不可用则降级为规则或明确不可用。用户纠错动作/食物后，相关计划或记忆需要失效提示，而不是把旧建议继续显示成当前结论。

### 8.3 全局体验与无障碍

建立 6 条关键用户旅程的状态图：首次登录、识餐入库、动作分析回看、能力自定义、开始/完成个人周期、数据导出/删除。每条覆盖空态、加载、权限拒绝、弱网、超时、后台恢复、重复点击、已过期。按钮最小可点击区域、文字对比、读屏标签、长文本/小屏均需设计检查。把“保存成功”“排队中”“证据不足”“已暂停”变成能采取下一步行动的状态，而非纯技术错误码。

## 9. 工程接口、数据和可靠性

### 9.1 通用错误契约

```json
{"error":{"code":"EVIDENCE_SOURCE_STALE","message":"这条记录已更新，请重新选择","retryable":false,
          "request_id":"...","details":{"source_type":"checkin","source_id":"42"}}}
```

前端按稳定 `code` 分流；可重试错误保留原操作 ID 和输入；不可重试错误引导用户修改。不在日志暴露图片 URL、JWT、API Key、原始聊天。

### 9.2 请求幂等修复

`miniprogram/utils/request.js` 递归重试必须原样传 `headers: extraHeaders`；同一次用户点击的 idempotency key 在 401、网络失败、页面恢复时保持稳定，直到服务端确认最终结果。禁止在通用 retry 中给 POST 自动生成新 key。服务端以 `(user_id, route, idem_key)` 唯一约束，并对同 key 不同 payload_hash 返回 409；结果保留期覆盖客户端恢复窗口。增加 mock `wx.request` 的 401→重登录、超时→重试、双击并发测试，断言只创建一条任务。

### 9.3 数据表/迁移建议

从实际单一迁移 head 后增量演进，不修改旧迁移：

- `policy_source_snapshots`：`user_id, source_type, source_id, revision, metric_key/version, value_hash, observed_at, resolver_version`；服务端解析结果，唯一 `(user_id,source_type,source_id,revision,metric_key)`。
- `policy_episode_contexts`：baseline/followup 冻结 context、changed_variable 及来源、协议哈希。
- `policy_decision_previews` 可不落库；正式 `policy_decisions` 加 `idempotency_key, candidate_set_hash, capability_snapshot_hash`。
- `motion_preview_assets` 或现有 `MotionEvidenceFrame` 增 `state, sha256, byte_size, storage_receipt, verified_at, expired_at`，状态转移不可跳跃。
- `harness_plugin_definitions/installations/audit_events`：manifest 哈希、审核状态、机器 scope、配置 schema/version、用户授权版本；现有 `harness_plugin_installations` 保留兼容迁移。
- `model_evaluations` 明确数据集哈希、受试者划分、阈值预注册、评价器/模型版本、签署人和不可变报告地址。

所有新表含所有权索引、删除/导出策略和迁移回滚演练。逐步弃用客户端任意观察值；旧行标 `legacy_unverified`，不得自动转为有效后验。

### 9.4 读写与 Worker 运维

`GET /system/ai-worker` 的清租约逻辑迁至周期调度器；状态接口只查。`GET /food/references` 的 seed 移到迁移/初始化任务；用户 GET 不初始化库。Outbox 消费者须有延迟、积压、重试、死信、并发去重指标；进程在线不等于分析链健康。定义 SLO：排队 P95、成功率、预览实际可取率、策略结论重放一致率；为失败和降级设置告警。账号删除需云对象服务端回执或持续显示“需人工核查”，不能由客户端 ID 列表推断完成。

## 10. 科学评测与竞争力证明

### 10.1 冻结协议

数据集先按用户/受试者划分再训练、调阈值，杜绝同人/同视频不同片段跨集泄漏。清单保存来源、许可、纳入排除理由、哈希、标注版本、双人复核分歧；所有失败样本入分母。数据许可未明前只本地评测不再分发。所有指标写样本量与置信区间；多次挑最优阈值后不在同一测试集宣称最终性能。

### 10.2 主创新的对照与消融

同一组目标与记录比较：B0 固定建议、B1 规则健康状态、B2 只按接受/完成历史排序、B3 有协议无证据门控、B4 完整 EGPAP；附消融：去来源核验、去上下文隔离、去信息增益、去能力门禁。合成测试只证明程序性质；真实用户或经过许可的真实纵向记录需测：7/14 天执行完成、用户负担、可核验结论比例、错误确定结论率、源更正后撤回率、重复建议率、用户理解/控制成功率。若无真实干预对照，就只报告离线回放与可用性，不声称健康效果提升。

如果未来引入受同意的随机探索，需先冻结安全候选集和选择概率，再用 IPS/DR 等离线评估；当前确定性 `selection_propensity=1` 不能支持对未选行动的无偏反事实评价。[Doubly Robust Policy Evaluation and Learning](https://arxiv.org/abs/1103.4601) 是相关方法来源，不构成当前项目完成该评估的证据。

### 10.3 动作/识餐评测

动作逐动作报告：识别覆盖/全样本准确率、开放集拒识、次数 MAE、阶段时刻误差、错误提示 F1、评分与双人评分一致性、拒识风险-覆盖曲线、机位/光照/人群分层、P50/P95 延迟。数值评分门槛按动作冻结，未过关一律只给画面观察。选择性预测的风险-覆盖思想可参考 [Selective Classification for Deep Neural Networks](https://papers.neurips.cc/paper_files/paper/2017/hash/4a8423d5e91fda00bb7e46540e2b0cf1-Abstract.html)。识餐除数值误差还应评测“追问是否真正减少误差”和“人工纠错所需时间”；别只报模型识别率。

### 10.4 建议放行门槛（项目内部预注册目标，非已有成绩）

| 能力 | 放行条件 |
| --- | --- |
| 时间轴 | 关键帧点击/回放/恢复/过期 100% 合同测试；可展示预览均由真实对象验证 |
| 核心策略 | 未验证来源进入信念 0；跨版本混样 0；撤回重放 100%；完整用户旅程成功率 ≥95% |
| 插件 | 未授权读取/写入阻断 100%；未知工具 fail-closed；暂停后新工作 0；历史停止/导出可用 |
| 动作评分 | 没有逐动作冻结报告时展示分数 0；每个显示的分数绑定评价器/数据集版本 |
| 识餐 | 未复核营养条目不标“准确”；错误区间覆盖与宽度联合达预注册阈值才升级主张 |
| 可靠性 | 401/超时/双击不重单；队列失联能终态；跨用户访问与预览签名攻击 0 成功 |

上表的 ≥95% 是工程目标，必须事先定义样本、设备/环境、统计口径；绝非当前实测。关键安全/权限为零容忍测试，但仍需承认有限测试不能数学证明线上零风险。

## 11. 分阶段实施与验收包

### 阶段 0：冻结事实与避免回归

锁定当前 commit、迁移 head、报告哈希和 6 条旅程录像/截图；把本审计的问题各写一个会失败的回归测试。更新 `FINAL_RELEASE_READINESS` 为“历史报告”，修正 README 对识餐“自动入库”的不准确描述。输出 `audit_baseline.json`，区分源码、本地测试、真实评测、部署证据四种等级。

### 阶段 1：修 P0 基础可信性

先修 request 幂等、时间轴状态、预览对象真值、视频回看与评分显示；每项有端到端测试。此阶段结束，截图里的时间轴应可逐帧点击、显示真实画面或合理过期提示，不出现未门禁分数和内部 ID。

### 阶段 2：接通核心策略

落地 source resolver、版本隔离、真实上下文/变量、可靠失效；再做协议/执行/复查页面。以一个模板（如“缩短训练时长以降低执行负担”）完成完整用户旅程，且能因为源记录修改而撤回历史结论，然后再扩 2 个模板。没有此阶段的用户闭环，不对外称“个人策略学习已落地”。

### 阶段 3：可自定义能力中心

先做审核组件的用户组合和范围授权，再做 manifest/注册/迁移/审计 API。通过“只读取本人动作结果、每次训练后给文字复盘、不开启云端传输”的示例能力验证范围、暂停、恢复和删除。任意第三方代码执行不纳入本阶段。

### 阶段 4：真实测量与泛化

冻结并复核动作、识餐和纵向行动数据；完成逐动作/食物评测、基线/消融、错误分析。未过门槛的能力保留审慎模式，不用 UI 包装为成熟能力。完成许可核查、报告可复现和模型/数据版本注册。

### 阶段 5：上线工程门禁

迁移预发布 MySQL、回滚演练、真实 Worker 及云文件上传/删除回执、弱网/后台恢复、隐私导出和告警。真机验证虽然不是算法创新点，但仍是上线事实的最后证据；若本次只做能力开发，标记为“未验收”，不影响阶段 1–4 的实施顺序。

## 12. 必须新增的测试矩阵

| 测试层 | 必测断言 |
| --- | --- |
| 纯函数 | 排序/裁决/门控边界、未知值、重复来源、版本变化、混杂、窗口未结束 |
| API + 数据库 | 跨用户 source/preview/plugin 均拒绝；同 key 重试只写一次；GET 不新增决策/任务/seed；改记录后旧结论失效 |
| Worker 合同 | 预览上传回执校验、失败 `has_image=false`、Gold 无信号 unavailable、动作不支持无分 |
| 小程序交互 | 点第 N 帧真的更换主图/文案/seek；恢复视频可播或提示；策略从确认到复查；插件自定义收窄范围 |
| 故障注入 | 401、超时、过期 URL、租约丢失、Outbox 重试、数据库迁移中断、云服务不可用 |
| 安全/隐私 | 插件暂停、未知工具、数据范围、提示注入、预览签名/跨用户、删除/导出覆盖 |
| 冻结评测 | 用户/受试者独立划分、失败样本入分母、阈值未借测试集调优、报告哈希和版本可复算 |

建议命令（按仓库环境运行）：

```powershell
node --test miniprogram/tests/*.test.js
backend/.venv/Scripts/python.exe -m pytest backend/tests -q
ai-worker/.venv/Scripts/python.exe -m pytest ai-worker/tests -q
backend/.venv/Scripts/python.exe backend/scripts/audit_privacy_coverage.py
backend/.venv/Scripts/python.exe backend/scripts/audit_openapi.py
```

实际解释器路径/环境须按各子项目配置核对；数据库迁移只在备份过的预发布库实施，不能为跑测试直接升级用户正在使用的生产库。

本次验证快照（2026-10-03）：后端 `499 passed`，小程序 `123 passed`，均不能覆盖上述真实交互与模型有效性。Worker 自带 `.venv` 的解释器入口指向不可用的 Python 安装；改用后端解释器收集 Worker 测试又因缺少 `cv2` 报错，因此 **Worker 全量测试本次未验证**。将 Worker 运行环境改为可重建的锁定依赖/容器或稳定的本地解释器，并在 CI 从零安装后运行；不要依赖已损坏的个人虚拟环境。`pytest` 缓存写入还出现权限警告，虽不影响 499 项结果，但应清理测试目录权限/缓存路径。此前配置 MySQL 的 `alembic check` 未通过；部署迁移状态仍须单独验收。

## 13. 交付完成定义

每个工作包必须提交：设计/接口变更、数据库迁移与回滚、用户可见状态、单元/合同/交互/故障测试、可复现实验数据清单、隐私与能力声明更新。验收报告逐项写：当前能力、所用证据等级、未通过项、限制和恢复方式。只有当“用户能完成流程、系统结论可追溯并可撤销、真实评测支持能力主张、错误能安全降级”同时成立，才称其为完成；“测试总数变多”本身不构成完成。

优先级排序一言以蔽之：**先让用户看到真实证据并完成行动周期，再让插件以受控方式扩展同一个闭环，最后用冻结对照证明闭环比单次识别或聊天更有价值。**
