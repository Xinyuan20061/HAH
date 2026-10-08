const api = require('../../utils/request')
const { normalizeNavigation, persistPlanHandoff, safeActionUrl, TAB_ROUTES } = require('../../utils/agentNavigation')

const SPECIALIST_NAMES = {
  planner: '计划规划',
  coach: '运动指导',
  nutritionist: '营养建议',
  safety_guardian: '安全守护',
  safety: '安全守护'
}
const TOKEN_TICK_MS = 22
const STEWARD = { id: 'steward', name: '小管家', role: '健康计划管家', greeting: '今天想先处理什么？', placeholder: '问问记录、计划或健康建议…', icon: '/assets/icons/agent-steward.png', capabilities: { text: true, voice_input: false, voice_output: false } }
const STEWARD_SUGGESTIONS = [
  { key: 'meal', icon: '/assets/icons/meal.png', title: '安排今天的晚饭', prompt: '我今天晚饭怎么吃更合适？' },
  { key: 'plan', icon: '/assets/icons/calendar.png', title: '规划这周的训练', prompt: '这周只能练三天，结合我的最近数据和目标安排一个本周计划' },
  { key: 'recovery', icon: '/assets/icons/recovery.png', title: '看看今天怎么恢复', prompt: '结合我的睡眠和运动记录，给我一个今天的恢复建议' }
]

function normalizeAgent(agent) {
  const capabilities = agent && agent.capabilities || {}
  return Object.assign({}, agent, {
    icon: agent.icon || STEWARD.icon,
    voiceInput: !!capabilities.voice_input,
    voiceOutput: !!capabilities.voice_output
  })
}

function presentTrace(trace, explanation) {
  if (!trace || !trace.specialist) return null
  const reasons = []
  ;(trace.adjustment_reasons || []).forEach(x => { if (x && x.label) reasons.push(x.label) })
  ;(trace.coaching_focus || []).forEach(x => { if (x && x.label) reasons.push(x.label) })
  ;(trace.plan_guardrail_changes || []).forEach(x => { if (x) reasons.push(x) })
  const detail = explanation || {}
  return {
    specialistLabel: SPECIALIST_NAMES[trace.specialist] || '综合建议',
    reasons: [...new Set(reasons)].slice(0, 4),
    hasReasons: reasons.length > 0,
    basis: (detail.basis || []).slice(0, 5),
    limitations: (detail.limitations || []).slice(0, 3),
    alternatives: (detail.alternatives || []).slice(0, 2),
    estimatedLoad: detail.estimated_load ? detail.estimated_load.label : '',
    responseStyle: detail.response_style || ''
  }
}

function displayTokens(text) {
  return String(text || '').match(/[\u3400-\u4dbf\u4e00-\u9fff]|[A-Za-z0-9]+(?:[._:/+-][A-Za-z0-9]+)*|\s+|./g) || []
}

function nextMessageId(role) {
  return `${role}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`
}

Page({
  data: {
    input: '',
    canSend: false,
    composerFocused: false,
    sending: false,
    sessionId: null,
    provider: '标准模式',
    statusText: '随时可以开始',
    streaming: false,
    scrollTop: 0,
    messages: [],
    stages: [],
    activeAgent: normalizeAgent(STEWARD),
    suggestions: STEWARD_SUGGESTIONS
  },

  onLoad() {
    this._unloaded = false
    this.loadHarnessManifest()
  },

  onInput(e) {
    const input = e.detail.value
    this.setData({ input, canSend: !!input.trim() })
  },

  onFocus() { this.setData({ composerFocused: true }) },
  onBlur() { this.setData({ composerFocused: false }) },

  onShow() {
    const tabBar = typeof this.getTabBar === 'function' && this.getTabBar()
    if (tabBar) {
      tabBar.setData({ selected: 2, wheelOpen: false, quickOpen: false })
      if (typeof tabBar.syncCompanion === 'function') tabBar.syncCompanion()
    }
    const prompt = wx.getStorageSync('healthmate_insight_prompt')
    if (prompt) {
      wx.removeStorageSync('healthmate_insight_prompt')
      this.setData({ input: prompt, canSend: true })
    }
  },

  onUnload() {
    this._unloaded = true
    this.stopGeneration(true)
    if (this._scrollTimer) clearTimeout(this._scrollTimer)
    if (this._routeTimer) clearTimeout(this._routeTimer)
  },

  async loadHarnessManifest() {
    try {
      const result = await api.get('/harness/manifest')
      if (result && Array.isArray(result.agents) && result.agents.length) {
        const steward = result.agents.find(item => item.id === 'steward')
        if (steward) this.setData({ activeAgent: normalizeAgent(Object.assign({}, STEWARD, steward)) })
      }
    } catch (e) {}
  },

  quick(e) {
    if (this.data.sending) return
    this.setData({ input: e.currentTarget.dataset.q, canSend: true })
    this.send()
  },

  settings() { wx.navigateTo({ url: '/pages/settings/ai/index' }) },

  scrollBottom() {
    if (this._scrollTimer) return
    this._scrollTimer = setTimeout(() => {
      this._scrollTimer = null
      this.setData({ scrollTop: this.data.scrollTop + 100000 })
    }, 48)
  },

  name(provider) {
    return {
      'deepseek-user': '个性化模式',
      'deepseek-system': '标准模式',
      demo: '演示模式',
      'demo-safety': '安全模式',
      fallback: '基础模式',
      'rules-fallback': '基础模式',
      'safety-rule': '安全模式',
      'local-qwen': '本地模式'
    }[provider] || '标准模式'
  },

  composerAction() {
    if (this.data.sending) return this.stopGeneration()
    this.send()
  },

  send(channel = 'text') {
    const q = this.data.input.trim()
    if (!q || this.data.sending) return
    const requestChannel = channel === 'voice' ? 'voice' : 'text'
    const activeAgent = this.data.activeAgent
    const messages = [
      ...this.data.messages,
      { id: nextMessageId('user'), role: 'user', content: q },
      { id: nextMessageId('assistant'), role: 'assistant', content: '', pending: true, agentId: activeAgent.id, agentName: activeAgent.name, agentIcon: activeAgent.icon, voiceAvailable: activeAgent.voiceOutput }
    ]
    this._tokenQueue = []
    this._pendingDone = null
    this._streamStopped = false
    this.setData({
      messages,
      input: '',
      canSend: false,
      sending: true,
      streaming: true,
      // Stage events are truthful pipeline progress, not live model tokens
      // (spec §8.7). The label below is replaced by whatever stage really runs.
      stages: [],
      statusText: activeAgent.id === 'steward' ? '正在理解你的问题' : '正在理解你的问题'
    })
    this._pendingChannel = requestChannel
    this.scrollBottom()
    this._streamTask = api.streamPost('/agent/respond/stream', { message: q, agent_id: activeAgent.id, channel: requestChannel }, {
      onMeta: meta => {
        if (meta.session_id) this.setData({ sessionId: meta.session_id })
        if (meta.safety_level && meta.safety_level !== 'normal') this.setData({ statusText: '正在执行安全检查' })
      },
      onStage: stage => this.onStageEvent(stage),
      onDelta: chunk => this.queueTokens(chunk),
      onDone: result => this.completeWhenDrained(result),
      onError: error => this.handleStreamError(error, q, activeAgent.id, requestChannel)
    })
  },

  /** Record one real pipeline stage. Never fabricates progress. */
  onStageEvent(stage) {
    if (!stage || !stage.stage) return
    const stages = (this.data.stages || []).concat([{
      stage: stage.stage,
      status: stage.status,
      label: stage.label || '处理中'
    }])
    const running = stages.filter(item => item.status === 'running').pop()
    const latest = running || stages[stages.length - 1]
    this.setData({
      stages,
      statusText: latest && latest.label ? latest.label : this.data.statusText
    })
  },

  queueTokens(chunk) {
    if (this._streamStopped || !chunk) return
    this._tokenQueue = (this._tokenQueue || []).concat(displayTokens(chunk))
    if (!this._tokenTimer) this.drainTokens()
  },

  drainTokens() {
    if (this._streamStopped) return
    const queue = this._tokenQueue || []
    if (!queue.length) {
      this._tokenTimer = null
      if (this._pendingDone) {
        const result = this._pendingDone
        this._pendingDone = null
        this.finishResponse(result)
      }
      return
    }
    const batchSize = queue.length > 80 ? 5 : queue.length > 32 ? 3 : 1
    const token = queue.splice(0, batchSize).join('')
    const index = this.data.messages.length - 1
    const current = this.data.messages[index] && this.data.messages[index].content || ''
    this.setData({
      [`messages[${index}].content`]: current + token,
      [`messages[${index}].pending`]: false,
      statusText: '正在生成回答'
    })
    this.scrollBottom()
    this._tokenTimer = setTimeout(() => this.drainTokens(), TOKEN_TICK_MS)
  },

  completeWhenDrained(result) {
    this._pendingDone = result || {}
    if (!this._tokenTimer && !(this._tokenQueue || []).length) {
      const done = this._pendingDone
      this._pendingDone = null
      this.finishResponse(done)
    }
  },

  finishResponse(done) {
    if (this._streamStopped) return
    const r = done && done.result || {}
    const navigation = normalizeNavigation(r)
    persistPlanHandoff(r, navigation.action)
    const index = this.data.messages.length - 1
    const messages = this.data.messages.slice()
    messages[index] = Object.assign({}, messages[index], {
      pending: false,
      agentPlan: r.plan || null,
      planAdjustment: r.plan_adjustment || null,
      resources: r.resources || [],
      knowledgeSources: r.knowledge_sources || [],
      exerciseRecommendations: r.exercise_recommendations && r.exercise_recommendations.items || [],
      trace: r.intent === 'navigation' ? null : presentTrace(r.trace, r.decision_explanation),
      navigationAction: navigation.action && navigation.action.target !== 'plan_preview' ? navigation.action : null,
      runId: r.run_id,
      applied: false,
      safetyLevel: r.safety_level || 'normal',
      // Only `actions[]` may render a confirm card. The reply text is never
      // scanned for executable intent (spec §8.5).
      actions: (r.actions || []).map(action => ({
        proposalId: action.proposal_id,
        actionKey: action.action_key,
        title: action.title,
        riskLevel: action.risk_level,
        summary: action.summary,
        expiresAt: action.expires_at,
        state: 'pending'
      }))
    })
    const provider = this.name(done && done.provider || r.provider)
    this._streamTask = null
    this.setData({
      messages,
      provider,
      sending: false,
      streaming: false,
      statusText: `${provider} · 已就绪`
    })
    this._pendingChannel = null
    this.scrollBottom()
    if (navigation.autoNavigate && navigation.action && navigation.action.target !== 'plan_preview') {
      this._routeTimer = setTimeout(() => this.openNavigationAction(navigation.action), 720)
    }
  },

  tapNavigationAction(e) {
    const index = Number(e.currentTarget.dataset.index)
    const message = this.data.messages[index]
    this.openNavigationAction(message && message.navigationAction)
  },

  openNavigationAction(action) {
    const url = safeActionUrl(action)
    if (!url || this._unloaded) return
    if (this._routeTimer) clearTimeout(this._routeTimer)
    this._routeTimer = null
    const route = url.split('?')[0]
    const fail = () => wx.showToast({ title: '页面暂时打不开', icon: 'none' })
    if (TAB_ROUTES.has(route)) wx.switchTab({ url: route, fail })
    else wx.navigateTo({ url, fail })
  },

  async handleStreamError(error, q, agentId, channel) {
    if (this._streamStopped) return
    const index = this.data.messages.length - 1
    if (this.data.messages[index] && this.data.messages[index].content) {
      this._streamTask = null
      this.setData({ sending: false, streaming: false, statusText: '连接中断，可继续提问' })
      return
    }
    try {
      const r = await api.postLong('/agent/respond', { message: q, agent_id: agentId, channel })
      if (r.session_id) this.setData({ sessionId: r.session_id })
      this.queueTokens(r.reply || '已完成分析。')
      this.completeWhenDrained({ provider: r.provider, result: r })
    } catch (fallbackError) {
      const messages = this.data.messages.slice()
      messages[index] = Object.assign({}, messages[index], {
        pending: false,
        content: '服务暂时没有连接成功。你的健康记录仍然安全，可以稍后再试。'
      })
      this._streamTask = null
      this.setData({ messages, sending: false, streaming: false, statusText: '暂时无法连接' })
      this.scrollBottom()
    }
  },

  stopGeneration(silent = false) {
    this._streamStopped = true
    if (this._streamTask && this._streamTask.abort) this._streamTask.abort()
    if (this._tokenTimer) clearTimeout(this._tokenTimer)
    this._streamTask = null
    this._tokenTimer = null
    this._tokenQueue = []
    this._pendingDone = null
    this._pendingChannel = null
    if (!silent && this.data.sending) {
      const index = this.data.messages.length - 1
      const messages = this.data.messages.slice()
      if (messages[index] && !messages[index].content) {
        messages[index] = Object.assign({}, messages[index], { pending: false, content: '已停止生成。' })
      }
      this.setData({ messages, sending: false, streaming: false, statusText: '已停止生成' })
    }
  },

  async applyAgentPlan(e) {
    const runId = Number(e.currentTarget.dataset.run)
    if (!runId) return
    try {
      const r = await api.post(`/agent/runs/${runId}/apply-plan`, {})
      const messages = this.data.messages.map(message => message.runId === runId
        ? Object.assign({}, message, { applied: true, actionAuditId: r.action_audit_id || null })
        : message)
      this.setData({ messages })
      wx.showToast({ title: r.already_applied ? '计划已在本周' : '已加入本周计划' })
      setTimeout(() => wx.navigateTo({ url: '/pages/plan/index' }), 450)
    } catch (error) {
      wx.showToast({ title: error.message || '加入计划失败', icon: 'none' })
    }
  },

  copyResource(e) {
    const url = e.currentTarget.dataset.url
    if (!url) return
    wx.setClipboardData({ data: url, success: () => wx.showToast({ title: '教学链接已复制' }) })
  },

  // ----------------------------------------------------------------------- //
  // Action proposals (spec §8.4/§8.5)
  // ----------------------------------------------------------------------- //

  _patchAction(proposalId, patch) {
    const messages = this.data.messages.map(message => {
      if (!message.actions || !message.actions.length) return message
      return Object.assign({}, message, {
        actions: message.actions.map(action =>
          action.proposalId === proposalId ? Object.assign({}, action, patch) : action)
      })
    })
    this.setData({ messages })
  },

  async confirmAction(e) {
    const proposalId = e.currentTarget.dataset.proposal
    if (!proposalId) return
    this._patchAction(proposalId, { state: 'executing' })
    try {
      const result = await api.post(`/agent/actions/${proposalId}/confirm`, {
        version: 1,
        confirmation: true
      })
      this._patchAction(proposalId, { state: 'executed', result: result && result.result || null })
      wx.showToast({ title: '已执行' })
    } catch (error) {
      // Expired / rejected / payload-mismatch are terminal states the user must
      // see, not an infinite spinner (spec §8.8).
      const terminal = error && [
        'ACTION_PROPOSAL_EXPIRED',
        'ACTION_PROPOSAL_NOT_PENDING',
        'ACTION_PAYLOAD_HASH_MISMATCH',
        'ACTION_EXECUTION_FAILED'
      ].indexOf(error.code) >= 0
      this._patchAction(proposalId, {
        state: terminal ? 'expired' : 'failed',
        error: error && error.message ? error.message : '执行失败'
      })
      wx.showToast({ title: (error && error.message) || '执行失败', icon: 'none' })
    }
  },

  async rejectAction(e) {
    const proposalId = e.currentTarget.dataset.proposal
    if (!proposalId) return
    try {
      await api.post(`/agent/actions/${proposalId}/reject`, { version: 1 })
      this._patchAction(proposalId, { state: 'rejected' })
      wx.showToast({ title: '已拒绝' })
    } catch (error) {
      this._patchAction(proposalId, { state: 'expired' })
      wx.showToast({ title: (error && error.message) || '已失效', icon: 'none' })
    }
  },

  copyKnowledge(e) {
    const url = e.currentTarget.dataset.url
    if (!url) return
    wx.setClipboardData({ data: url, success: () => wx.showToast({ title: '来源已复制' }) })
  }
})
