const api = require('../../utils/request')

/**
 * 状态与下一步 —— 方案 §14 第 7 条「跨模态闭环」的客户端呈现。
 *
 * 这一页把后端三个只读接口串成一条闭环，回答问题：
 *   1. 现在建议我做什么（Decision Contract / Next Best Action）
 *   2. 我现在处于什么状态（Health State + constraints）
 *   3. 我上次那样做之后发生了什么（Outcomes + 记忆）
 *
 * 三条诚实性约束贯穿整页，且都由后端数据驱动、不在前端编造：
 *   · value 为 null 一律显示「数据不足」，绝不显示 0（缺失 ≠ 0）；
 *   · 置信度只显示后端给出的覆盖度等级，不使用任何「AI 置信度」措辞；
 *   · 记忆按 influential 区分「正在影响」与「仅记录」，后者不得表述为已生效。
 *
 * 本页为**只读**：确认与写入一律交给小管家（Action 提案 + 用户确认），
 * 所以这里不调用任何会写库的接口。
 */

// 证据类型 -> 用户可读说法。后端契约是
// observed / derived / model_inferred / user_confirmed。
const EVIDENCE_LABEL = {
  observed: '你记录的',
  derived: '由记录推算',
  model_inferred: '模型推断',
  user_confirmed: '你确认过'
}

// 只在这里列出要展示的状态值，顺序即展示顺序。
const STATE_KEYS = [
  'sleep_debt_7d',
  'sleep_hours_last',
  'weight_kg_latest',
  'diet_calories_avg',
  'exercise_days',
  'diet_record_coverage_7d',
  'plan_adherence_7d',
  'data_reliability_score'
]

const CONFIDENCE_LABEL = {
  high: '覆盖充分',
  medium: '覆盖一般',
  low: '覆盖偏低',
  unavailable: '数据不足'
}

// 排序权重的中文名，与后端 decision.WEIGHTS 的键一一对应。
// 只影响展示，不参与计算。
const BREAKDOWN_LABEL = {
  expected_impact: '预期效果',
  efficacy_from_history: '个人历史有效性',
  urgency: '紧急度',
  plan_fit: '与计划契合',
  effort: '所需投入',
  negative_feedback: '负面反馈'
}

const CONCLUSION_LABEL = {
  changed: '有变化',
  unchanged: '没有变化',
  insufficient_data: '记录不足，无法判断',
  stopped: '已停止'
}

function num(value, decimals) {
  if (value === null || value === undefined || value === '') return null
  const n = Number(value)
  if (!isFinite(n)) return null
  return decimals === undefined ? n : Number(n.toFixed(decimals))
}

// 缺失不显示 0：返回 null 而不是 0，模板据此渲染「数据不足」。
function displayValue(entry) {
  if (!entry) return null
  return num(entry.value)
}

function evidenceLabel(entry) {
  if (!entry) return ''
  return EVIDENCE_LABEL[entry.evidence_type] || '来源未标注'
}

function confidenceLabel(entry) {
  if (!entry) return ''
  return CONFIDENCE_LABEL[entry.confidence_level] || '数据不足'
}

Page({
  data: {
    loading: true,
    // 每个区块独立记录错误，一个接口失败不影响另外两个区块。
    errors: { state: '', decision: '', outcomes: '', policy: '' },
    // 区块 1：下一步
    nextAction: null,
    alternatives: [],
    decisionPolicy: '',
    policyDecision: null,
    policyEpisode: null,
    // 区块 2：状态
    stateRows: [],
    stateAsOf: '',
    missingness: [],
    constraints: [],
    blocked: false,
    // 区块 3：做过之后
    outcomeRows: [],
    outcomeTotal: 0,
    memoryUsed: [],
    memoryIgnored: [],
    memoryPolicy: ''
  },

  onLoad() { this.load() },
  onPullDownRefresh() { this.load().then(() => wx.stopPullDownRefresh()) },

  async load() {
    this.setData({ loading: true, errors: { state: '', decision: '', outcomes: '', policy: '' } })
    // 三个接口都是只读的，并发拉取；分别处理失败，避免一处故障让整页空白。
    const [state, decision, outcomes, policyDecision, policyEpisode] = await Promise.allSettled([
      api.get('/health/state'),
      api.get('/agent/decision'),
      api.get('/health/outcomes'),
      api.get('/policy/decide'),
      api.get('/policy/episodes/current')
    ])
    this.applyState(state)
    this.applyDecision(decision)
    this.applyOutcomes(outcomes)
    this.applyPolicy(policyDecision, policyEpisode)
    this.setData({ loading: false })
  },

  applyState(res) {
    if (res.status !== 'fulfilled') {
      this.setData({ 'errors.state': this.reason(res, '状态暂时读不到') })
      return
    }
    const body = res.value || {}
    const values = body.values || {}
    // 标题由后端 registry 提供（/health/state 的 titles），前端只做兜底，
    // 避免把 sleep_debt_7d 这类内部键名直接显示给用户。
    const titles = body.titles || {}
    const rows = STATE_KEYS.map(key => {
      const entry = values[key]
      return {
        key,
        title: titles[key] || key,
        value: displayValue(entry),
        unit: (entry && entry.unit) || '',
        evidence: evidenceLabel(entry),
        confidence: confidenceLabel(entry),
        observedDays: entry ? entry.observed_days : null,
        windowDays: entry ? entry.window_days : null,
        limitations: (entry && entry.limitations) || []
      }
    })
    // 缺失项排在前面：先说清"没有数据"，再说有什么。
    rows.sort((a, b) => (a.value === null ? 0 : 1) - (b.value === null ? 0 : 1))

    const missingness = Object.keys(body.missingness || {}).map(key => ({
      key,
      text: this.missingnessText(key, body.missingness[key])
    }))
    const constraints = (body.constraints || []).map(item => ({
      key: item.key,
      severity: item.severity,
      description: item.description || item.key
    }))

    this.setData({
      stateRows: rows,
      stateAsOf: body.as_of || '',
      missingness,
      constraints,
      blocked: !!body.blocks_auto_planning
    })
  },

  missingnessText(key, payload) {
    if (payload === null || payload === undefined) return key
    if (typeof payload === 'object') {
      if (payload.reason) return `${key}：${payload.reason}`
      const days = payload.observed_days
      if (days !== undefined) return `${key}：仅 ${days} 天有记录`
      return key
    }
    return `${key}：${payload}`
  },

  applyDecision(res) {
    if (res.status !== 'fulfilled') {
      this.setData({ 'errors.decision': this.reason(res, '建议暂时算不出来') })
      return
    }
    const body = res.value || {}
    const nba = body.next_best_action
    const missing = (body.capabilities_available || []).length
    this.setData({
      nextAction: nba
        ? {
            id: nba.id,
            title: nba.title,
            reason: nba.reason,
            actionKey: nba.action_key,
            evidence: nba.evidence || [],
            breakdown: this.breakdownRows(nba.breakdown)
          }
        : null,
      alternatives: (body.alternatives || []).map(item => ({
        id: item.id,
        title: item.title,
        reason: item.reason
      })),
      decisionPolicy: body.policy || '',
      memoryUsed: (body.memory_used || []).map(item => ({
        key: item.key,
        value: item.value,
        source: item.source,
        steer: item.may_steer === 'presentation_and_volume' ? '影响形态与强度' : '仅作为陈述'
      })),
      memoryIgnored: body.memory_ignored || [],
      filtered: body.filtered || [],
      capabilitiesCount: missing
    })
  },

  // 权重明细是"为什么是这条建议"的可复核依据，按绝对值排序。
  breakdownRows(breakdown) {
    if (!breakdown || typeof breakdown !== 'object') return []
    return Object.keys(breakdown)
      .map(key => ({ key, label: BREAKDOWN_LABEL[key] || key, value: num(breakdown[key], 4) }))
      .filter(item => item.value !== null && item.value !== 0)
      .sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
  },

  applyOutcomes(res) {
    if (res.status !== 'fulfilled') {
      this.setData({ 'errors.outcomes': this.reason(res, '历史结果暂时读不到') })
      return
    }
    const body = res.value || {}
    const rows = (body.items || []).map(item => ({
      actionKey: item.action_key,
      result: item.result,
      conclusion: CONCLUSION_LABEL[item.conclusion] || item.conclusion,
      // insufficient_data 必须如实显示为"记录不足"，不得显示为正向结果。
      isInsufficient: item.conclusion === 'insufficient_data',
      observedAt: (item.observed_at || '').slice(0, 16).replace('T', ' ')
    }))
    this.setData({
      outcomeRows: rows,
      outcomeTotal: body.total || 0,
      outcomeNote: body.note || '',
      policyRows: (body.policy_preferences || []).map(row => ({
        family: row.action_family,
        variant: row.variant,
        offered: row.offered,
        completed: row.completed,
        personalised: !!row.personalised
      }))
    })
  },

  applyPolicy(decisionRes, episodeRes) {
    if (decisionRes.status !== 'fulfilled' || episodeRes.status !== 'fulfilled') {
      this.setData({ 'errors.policy': '个人策略验证暂时读不到' })
      return
    }
    const decision = decisionRes.value || {}
    const episode = (episodeRes.value || {}).episode
    const selectedId = String(decision.selected || '')
    const selectedLabel = selectedId.indexOf('session_duration') >= 0
      ? '尝试更短的单次训练'
      : selectedId ? '个人策略候选' : null
    const statusLabels = {
      active: '进行中', awaiting_review: '等待复查', reviewed: '已复查', stopped: '已停止'
    }
    const episodeView = episode ? {
      status: statusLabels[episode.status] || '状态待更新',
      statusKey: episode.status || '',
      version: episode.version,
      completed: (episode.reports || []).filter(item => item.execution === 'completed').length,
      total: (episode.opportunities || []).length
    } : null
    const policyKind = decision.kind || 'collect_evidence_or_wait'
    this.setData({
      policyDecision: {
        selected: selectedLabel,
        kind: policyKind,
        personalised: !!decision.personalised,
        explanation: decision.explanation || '排序只使用已经通过门控的个人证据。'
      },
      policyEpisode: episodeView,
      // Spec §6: map "today's next step" into three user-readable categories;
      // the main CTA goes straight to the relevant page, never to an internal ID.
      nextStep: this.nextStep(policyKind, episodeView)
    })
  },

  // 可行动 / 等待暂缓 / 需修复 —— 三类聚合，主 CTA 直达页面。
  nextStep(policyKind, episodeView) {
    const repair = policyKind === 'needs_repair' || policyKind === 'blocked'
      || (episodeView && episodeView.statusKey === 'awaiting_review' && episodeView.completed < episodeView.total)
    if (repair) {
      return { category: 'needs_repair', categoryLabel: '需修复', label: '有记录需要修复或复查', route: '/pages/policy/episode/index' }
    }
    if (this.data.nextAction) {
      return { category: 'actionable', categoryLabel: '可行动', label: '有一条建议等待你确认', route: '/pages/chat/index' }
    }
    if (episodeView && episodeView.statusKey === 'active' && episodeView.completed < episodeView.total) {
      return { category: 'actionable', categoryLabel: '可行动', label: `本周期已记录 ${episodeView.completed}/${episodeView.total} 条`, route: '/pages/policy/episode/index' }
    }
    if (episodeView && (episodeView.statusKey === 'active' || episodeView.statusKey === 'awaiting_review')) {
      return { category: 'waiting', categoryLabel: '等待暂缓', label: '本周期在等待观察或复查窗口', route: '/pages/policy/episode/index' }
    }
    return { category: 'waiting', categoryLabel: '等待暂缓', label: '暂无进行中的行动协议', route: '/pages/policy/overview/index' }
  },

  goNextStep() {
    const step = this.data.nextStep
    if (!step || !step.route) return
    if (step.route === '/pages/chat/index') {
      wx.switchTab({ url: step.route, fail: () => wx.navigateTo({ url: step.route }) })
      return
    }
    wx.navigateTo({ url: step.route })
  },

  reason(res, fallback) {
    if (res && res.reason && res.reason.message) return res.reason.message
    return fallback
  },

  retry() { this.load() },

  // 确认与写入必须走 Action 提案（用户确认后才落库），因此交给小管家。
  // 复用 chat 既有的 prefill 约定（healthmate_insight_prompt）：只预填输入框，
  // 不代替用户发送，也不代替用户确认。
  confirmWithSteward() {
    const action = this.data.nextAction
    if (!action) return
    const prompt = `我想执行这个建议：${action.title}。请解释依据，并给我确认入口。`
    wx.setStorageSync('healthmate_insight_prompt', prompt)
    wx.switchTab({
      url: '/pages/chat/index',
      fail: () => wx.navigateTo({ url: '/pages/chat/index' })
    })
  },

  goState() { wx.navigateTo({ url: '/pages/trends/index' }) },
  goInsights() { wx.navigateTo({ url: '/pages/insights/index' }) },
  goPolicy() { wx.navigateTo({ url: '/pages/policy/overview/index' }) }
})
