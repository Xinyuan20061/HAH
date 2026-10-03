// 统一动作分析 V2 结果契约 → 页面展示模型（规格 §3.2 / §4 / §8.4）。
// 纯函数，不依赖 wx，便于单测。前端只按此形状对接，不实现后端。
// 阅读顺序：动作 → 点评 → 指标 → 时间轴 → 建议。
const PIPELINE_VERSION = 'motion-unified-v2'

const STAGE_LABELS = {
  queued: '任务排队中',
  decoding: '正在读取视频',
  local_inference: '正在识别动作',
  evidence_ready: '证据已就绪，等待复核',
  visual_review: '正在云端视觉复核',
  feedback_generation: '正在生成点评',
  completed: '完成',
  partial: '完成（部分降级）',
  failed: '分析失败',
  cancelled: '已取消'
}
// V2 运行终态（abstained 已不再是运行状态，而是 recognition.state）。
const TERMINAL_STATUSES = ['completed', 'partial', 'failed', 'cancelled']

// 来源 → 中文（§4.3：界面不出现 local_worker / deepseek_vision / kinetics400 等英文 ID）。
const SOURCE_LABELS = {
  vision: 'AI 分析',
  deepseek_vision: 'AI 分析',
  visual_coach: 'AI 分析',
  pose: '动作轨迹',
  local_worker: '动作轨迹',
  mediapipe: '动作轨迹',
  kinetics: '视频画面',
  kinetics400: '视频画面',
  video_model: '视频画面',
  user_selected: '手动选择'
}

// advice_kind → 中文标签（§4.2：区分观察到的问题 / 一般要点 / 拍摄提示）。
const ADVICE_KIND_LABELS = {
  observed_correction: '观察到的问题',
  general_tip: '一般动作要点',
  capture_tip: '拍摄提示'
}

// 指标 id → 中文标签（§4.1：次数 / 持续时间 / 节奏 / 质量维度）。
const METRIC_LABELS = {
  reps: '次数', repetition_count: '次数', count: '次数',
  duration_ms: '持续时间', duration: '持续时间',
  rhythm: '节奏', rhythm_control: '节奏',
  overall: '动作质量', quality: '动作质量', score: '动作质量',
  completeness: '完成度', stability: '稳定性', risk_index: '动作偏差提示'
}

function fmtSeconds(ms) {
  const n = Number(ms)
  if (!Number.isFinite(n) || n < 0) return '--'
  return (n / 1000).toFixed(1).replace(/\.0$/, '') + 's'
}

// 把后端来源字段翻译成中文；未知来源不回吐英文 ID。
function mapSource(src) {
  if (!src) return ''
  return SOURCE_LABELS[src] || ''
}

// 三态归一：V2 只允许 identified | likely | unknown；
// 兼容 V1 历史结果（recognized→identified，abstained/uncertain→unknown）。
function normalizeState(state) {
  if (state === 'identified' || state === 'recognized') return 'identified'
  if (state === 'likely') return 'likely'
  return 'unknown'
}

// reason 原码不直出：看起来像诊断码/帧号/引擎名的一律不展示（R11）。
function sanitizeReason(reason) {
  if (!reason) return ''
  const s = String(reason).trim()
  if (!s) return ''
  if (/^[A-Z][A-Z0-9_]{3,}$/.test(s)) return ''          // NOT_RECOGNIZED / NO_VALIDATED_SCORER
  if (/^(frame|pose|trace)[_:]/i.test(s)) return ''      // frame:0 / trace-xxx / pose:shoulder
  if (/local_worker|deepseek_vision|candidate_score|mediapipe|kinetics/i.test(s)) return ''
  return s
}

// §8.4：过滤非法时间戳、按时间升序、timeLabel 格式化；全量映射，不再 .slice(0,4)。
function normalizeTimeline(timeline) {
  const frames = (timeline && Array.isArray(timeline.frames)) ? timeline.frames : []
  return frames
    .filter(f => f && Number.isFinite(Number(f.timestamp_ms)) && Number(f.timestamp_ms) >= 0)
    .slice()
    .sort((a, b) => Number(a.timestamp_ms) - Number(b.timestamp_ms))
    .map((f, index) => ({
      id: f.id || ('frame_' + index),
      timestamp_ms: Number(f.timestamp_ms),
      previewUrl: (f.preview && f.preview.url) || f.preview_url || f.image_url || '',
      previewState: (f.preview && f.preview.state) || (f.preview_url || f.image_url ? 'available' : 'unavailable'),
      phase: f.phase || '',
      observation: f.observation || f.finding || '',
      explanation: f.explanation || '',
      nextStep: f.next_step || f.advice || '',
      adviceKind: f.advice_kind || '',
      adviceKindLabel: ADVICE_KIND_LABELS[f.advice_kind] || '',
      timeLabel: (Number(f.timestamp_ms) / 1000).toFixed(1) + ' 秒'
    }))
}

function formatMetric(m) {
  const id = m.id || ''
  let value = m.value
  if (value == null) return null
  if (id === 'duration_ms' && Number.isFinite(Number(value))) value = (Number(value) / 1000).toFixed(1) + 's'
  if (!Object.prototype.hasOwnProperty.call(METRIC_LABELS, id)) return null
  return { label: METRIC_LABELS[id], value, unit: id === 'risk_index' ? '' : (m.unit || '') }
}

// /evidence 只读返回帧级预览映射（frame_id → 可渲染签名 URL）。
// 兼容 {frames:[...]} / {evidence:[...]} / 顶层数组；键名容忍 frame_id 或 id。
function buildPreviewMap(evidence) {
  const list = (evidence && (evidence.frames || evidence.evidence)) || (Array.isArray(evidence) ? evidence : [])
  const map = {}
  for (const e of list) {
    if (!e) continue
    const fid = e.frame_id || e.id
    const url = e.preview_url || e.previewUrl || e.url
    if (fid && url) map[fid] = url
  }
  return map
}

// 结果帧已带 preview_url 时优先用；为空的才用 evidence 映射补齐（不覆盖已有 URL）。
function mergeEvidencePreviews(frames, evidence) {
  const list = (evidence && (evidence.frames || evidence.evidence)) || (Array.isArray(evidence) ? evidence : [])
  const map = buildPreviewMap(evidence)
  const states = {}
  for (const item of list) {
    if (!item) continue
    const fid = item.frame_id || item.id
    const state = (item.preview && item.preview.state) || item.preview_state
    if (fid && state) states[fid] = state
  }
  if (!Object.keys(map).length && !Object.keys(states).length) return frames
  let changed = false
  const merged = frames.map(f => {
    const previewUrl = f.previewUrl || map[f.id] || ''
    const previewState = states[f.id] || (previewUrl ? 'available' : f.previewState)
    if (previewUrl !== f.previewUrl || previewState !== f.previewState) changed = true
    return { ...f, previewUrl, previewState }
  })
  return changed ? merged : frames
}

// 关键边界（R11 / §4.3）：
//  - 三态按 recognition.state 展示，绝不同时出现“已识别类别”与“暂未确认动作”；
//  - candidate_score / calibrated_confidence / trace / 引擎版本一律不进用户视图；
//  - 数值缺失由 notices 给具体原因，不再硬编“已识别类别，暂无可靠数值评分”。
function buildViewModel(raw) {
  const r = raw || {}
  const recognition = r.recognition || {}
  const capabilities = r.capabilities || {}
  const summary = r.summary || {}
  const metrics = Array.isArray(r.metrics) ? r.metrics : []
  const notices = Array.isArray(r.notices) ? r.notices : []
  const state = normalizeState(recognition.state)

  // 动作名：V2 用 display_name；兼容 V1 label_zh。
  let detectionName = recognition.display_name || recognition.label_zh || ''
  if (!detectionName) detectionName = state === 'identified' ? '已识别动作' : '暂未确认具体动作'

  // 徽章只由三态决定：identified 不打“待确认/暂未确认”，避免同屏矛盾。
  const detectionBadge = state === 'identified' ? '' : state === 'likely' ? '待确认' : '暂未确认'

  // likely：一个具体分歧；unknown：尽量描述可见运动。均来自后端中文 reason，经去码过滤。
  const detectionNote = sanitizeReason(recognition.reason)

  // 来源中文化（动作轨迹 / 视频画面 / AI 分析 / 手动选择）。
  const sourceText = [recognition.source, summary.source]
    .map(mapSource).filter(Boolean)
    .filter((v, i, arr) => arr.indexOf(v) === i)
    .join('、')

  // 指标：按实际能力显示；空数组表示无指标，不用 0 凑数。
  const metricRows = metrics.map(formatMetric).filter(Boolean)
  let metricNote = (notices.find(n => n && n.kind === 'metric_unavailable') || {}).text || ''
  if (!metricNote && metricRows.length === 0 && capabilities.quality_score === 'unavailable') {
    metricNote = '这类动作本次先看画面讲解，暂不显示数值评分。'
  }

  // 时间轴：V2 timeline.frames 全量；兼容 V1 keyframes（t_ms/image_url/finding/advice）。
  const v1Frames = Array.isArray(r.keyframes)
    ? r.keyframes.map(k => ({ ...k, timestamp_ms: k.t_ms, preview_url: k.image_url, observation: k.finding, next_step: k.advice }))
    : []
  const timelineFrames = normalizeTimeline(r.timeline || { frames: v1Frames })
  const activeFrame = timelineFrames[0] || null

  return {
    analysisId: r.analysis_id,
    pipelineVersion: r.pipeline_version || PIPELINE_VERSION,
    state,
    detectionName,
    detectionBadge,
    detectionNote,
    summaryText: summary.text || '',
    summaryNextStep: summary.primary_next_step || '',
    metricRows,
    metricNote,
    timelineFrames,
    activeFrame,
    sourceText,
    notices: notices.map(n => (n && n.text) || '').filter(Boolean),
    limitations: r.limitations || [],
    capabilities
  }
}

module.exports = {
  buildViewModel, normalizeTimeline, mapSource, sanitizeReason,
  buildPreviewMap, mergeEvidencePreviews,
  STAGE_LABELS, TERMINAL_STATUSES, fmtSeconds, PIPELINE_VERSION,
  SOURCE_LABELS, ADVICE_KIND_LABELS
}
