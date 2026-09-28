const api = require('../../utils/request')

const SPECIALIST_NAMES = {
  planner: '计划规划',
  coach: '运动指导',
  nutritionist: '营养建议',
  safety_guardian: '安全守护',
  safety: '安全守护'
}
const TOKEN_TICK_MS = 22

function presentTrace(trace) {
  if (!trace || !trace.specialist) return null
  const reasons = []
  ;(trace.adjustment_reasons || []).forEach(x => { if (x && x.label) reasons.push(x.label) })
  ;(trace.coaching_focus || []).forEach(x => { if (x && x.label) reasons.push(x.label) })
  ;(trace.plan_guardrail_changes || []).forEach(x => { if (x) reasons.push(x) })
  return {
    specialistLabel: SPECIALIST_NAMES[trace.specialist] || '综合建议',
    reasons: [...new Set(reasons)].slice(0, 4),
    hasReasons: reasons.length > 0
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
    suggestions: [
      {
        key: 'meal',
        icon: '/assets/icons/meal.png',
        title: '安排今天的晚饭',
        hint: '结合目标和今天的记录',
        prompt: '我今天晚饭怎么吃更合适？'
      },
      {
        key: 'plan',
        icon: '/assets/icons/calendar.png',
        title: '规划这周的训练',
        hint: '生成一份由我确认的计划',
        prompt: '这周只能练三天，结合我的最近数据和目标安排一个本周计划'
      },
      {
        key: 'recovery',
        icon: '/assets/icons/recovery.png',
        title: '看看今天怎么恢复',
        hint: '参考睡眠与运动情况',
        prompt: '结合我的睡眠和运动记录，给我一个今天的恢复建议'
      }
    ]
  },

  onInput(e) {
    const input = e.detail.value
    this.setData({ input, canSend: !!input.trim() })
  },

  onFocus() { this.setData({ composerFocused: true }) },
  onBlur() { this.setData({ composerFocused: false }) },

  onShow() {
    const prompt = wx.getStorageSync('healthmate_insight_prompt')
    if (prompt) {
      wx.removeStorageSync('healthmate_insight_prompt')
      this.setData({ input: prompt, canSend: true })
    }
  },

  onUnload() {
    this.stopGeneration(true)
    if (this._scrollTimer) clearTimeout(this._scrollTimer)
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

  shouldAgent(q) {
    return /计划|安排|本周|这周|减脂|增肌|练三天|训练三天|怎么练|深蹲|俯卧撑|伏地挺身|弓步|箭步|胸部|背部|肩部|手臂|核心|股四头肌|臀部|腘绳肌|练胸|练背|每周运动|运动多久|运动指南|膳食|营养|怎么吃|慢病|高血压|糖尿病|squat|pushup|lunge|喝水|饮水|睡眠|睡觉|失眠|腰酸|久坐|坐着|体重|减肥|血脂|血压|跑步|散步|快走|运动|锻炼|饮食|吃饭|早餐|午餐|晚餐|盐|油|糖|脂肪|卡路里|热量|大腿|膝盖|拉伸|热身|步数|走多少|吃多少|喝多少|合适|注意|怎么办|可以吗|好不好|怎么减|怎么增/i.test(q)
  },

  composerAction() {
    if (this.data.sending) return this.stopGeneration()
    this.send()
  },

  send() {
    const q = this.data.input.trim()
    if (!q || this.data.sending) return
    const useAgent = this.shouldAgent(q)
    const messages = [
      ...this.data.messages,
      { id: nextMessageId('user'), role: 'user', content: q },
      { id: nextMessageId('assistant'), role: 'assistant', content: '', pending: true }
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
      statusText: useAgent ? '正在整理你的记录' : '正在理解你的问题'
    })
    this.scrollBottom()
    const endpoint = useAgent ? '/agent/respond/stream' : '/chat/stream'
    this._streamTask = api.streamPost(endpoint, { message: q, session_id: this.data.sessionId }, {
      onMeta: meta => {
        if (meta.session_id) this.setData({ sessionId: meta.session_id })
        if (meta.safety_level && meta.safety_level !== 'normal') this.setData({ statusText: '正在执行安全检查' })
      },
      onDelta: chunk => this.queueTokens(chunk),
      onDone: result => this.completeWhenDrained(result),
      onError: error => this.handleStreamError(error, q, useAgent)
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
    const index = this.data.messages.length - 1
    const messages = this.data.messages.slice()
    messages[index] = Object.assign({}, messages[index], {
      pending: false,
      agentPlan: r.plan || null,
      planAdjustment: r.plan_adjustment || null,
      resources: r.resources || [],
      knowledgeSources: r.knowledge_sources || [],
      exerciseRecommendations: r.exercise_recommendations && r.exercise_recommendations.items || [],
      trace: presentTrace(r.trace),
      runId: r.run_id,
      applied: false,
      safetyLevel: r.safety_level || 'normal'
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
    this.scrollBottom()
  },

  async handleStreamError(error, q, useAgent) {
    if (this._streamStopped) return
    const index = this.data.messages.length - 1
    if (this.data.messages[index] && this.data.messages[index].content) {
      this._streamTask = null
      this.setData({ sending: false, streaming: false, statusText: '连接中断，可继续提问' })
      return
    }
    try {
      const r = useAgent
        ? await api.postLong('/agent/respond', { message: q })
        : await api.postLong('/chat', { message: q, session_id: this.data.sessionId })
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
      setTimeout(() => wx.switchTab({ url: '/pages/plan/index' }), 450)
    } catch (error) {
      wx.showToast({ title: error.message || '加入计划失败', icon: 'none' })
    }
  },

  copyResource(e) {
    const url = e.currentTarget.dataset.url
    if (!url) return
    wx.setClipboardData({ data: url, success: () => wx.showToast({ title: '教学链接已复制' }) })
  },

  copyKnowledge(e) {
    const url = e.currentTarget.dataset.url
    if (!url) return
    wx.setClipboardData({ data: url, success: () => wx.showToast({ title: '来源已复制' }) })
  }
})
