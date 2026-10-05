const api = require('../../utils/request')
const { buildPlanActivity } = require('../../utils/planActivity')
const ACTIVITY_REVEAL_DELAY_MS = 300
const PREVIEW_DAY_LABELS = ['今天', '明天', '后天', '第 4 天', '第 5 天', '第 6 天', '第 7 天']
const PREVIEW_CATEGORY_LABELS = {
  exercise: '训练', diet: '饮食', sleep: '睡眠', habit: '习惯', recovery: '恢复'
}
const PREVIEW_ACTORS = {
  xiaojian: { id: 'xiaojian', name: '小健', icon: '/assets/characters/xiaojian-portrait-v1.png' },
  xiaokang: { id: 'xiaokang', name: '小康', icon: '/assets/characters/xiaokang-portrait-v1.png' }
}
const REVIEW_TOKEN_TICK_MS = 22
const PLAN_HANDOFF_VERSION = 'healthmate.plan-handoff.v1'
const PLAN_HANDOFF_MAX_AGE_MS = 15 * 60 * 1000

function displayTokens(text) {
  return String(text || '').match(/[\u3400-\u4dbf\u4e00-\u9fff]|[A-Za-z0-9]+(?:[._:/+-][A-Za-z0-9]+)*|\s+|./g) || []
}

function handoffStorageKey(runId) {
  return `healthmate_plan_preview_handoff:${runId}`
}

function readPlanHandoff(runId) {
  try {
    const value = wx.getStorageSync(handoffStorageKey(runId))
    const preview = value && value.plan_preview
    const fresh = Number(value && value.savedAt) > 0 && Date.now() - Number(value.savedAt) <= PLAN_HANDOFF_MAX_AGE_MS
    if (value && value.version === PLAN_HANDOFF_VERSION && fresh && Number(preview && preview.run_id) === runId) return value
  } catch (error) {}
  return null
}

function clearPlanHandoff(runId) {
  try { wx.removeStorageSync(handoffStorageKey(runId)) } catch (error) {}
}

function formatPreviewMessage(reply, preview, items) {
  const lines = []
  if (reply) lines.push(reply)
  lines.push('', preview.title || '本周健康计划', '')
  items.forEach(item => {
    const detail = String(item.description || '').trim()
    lines.push(`${item.dayLabel}｜${item.title}${detail ? `：${detail}` : ''}`)
  })
  return lines.join('\n').trim()
}

Page({
  data: {
    headline: '今天只做三件真正有用的事', items: [], doneCount: 0, agentPlan: null,
    showWeek: false, showAdd: false, addTitle: '', addDesc: '', saving: false,
    activityLoading: true, activityError: '', activityBands: [], activityReveal: false,
    activitySummary: { completeDays: 0, partialDays: 0, activeDays: 0 },
    previewMode: false, previewRunId: 0, previewLoading: false,
    previewError: '', preview: null, applyingPreview: false,
    previewActor: PREVIEW_ACTORS.xiaojian,
    previewReplyText: '', previewReplyStreaming: false, floatingReview: null
  },
  onLoad(options = {}) {
    this._unloaded = false
    const runId = Number(options.run_id || 0)
    const previewMode = options.mode === 'preview' && Number.isInteger(runId) && runId > 0
    const savedActor = options.agent_id || wx.getStorageSync('healthmate_agent_id')
    const previewActor = PREVIEW_ACTORS[savedActor] || PREVIEW_ACTORS.xiaojian
    this.setData({
      previewMode,
      previewRunId: previewMode ? runId : 0,
      previewActor,
      floatingReview: previewMode ? {
        id: `plan-${runId}`,
        actorId: previewActor.id,
        state: 'loading',
        content: '',
        autoOpen: true
      } : null
    })
    if (previewMode) {
      this.loadPreview()
    }
  },
  onShow() {
    this.startActivityReveal()
    this.loadToday()
    this.loadActivity()
  },
  onHide() { this.clearActivityRevealTimer() },
  onUnload() {
    this._unloaded = true
    this.clearActivityRevealTimer()
    this.clearPreviewTimers()
  },
  clearPreviewTimers() {
    if (this.previewReplyTimer) clearTimeout(this.previewReplyTimer)
    this.previewReplyTimer = null
    this._previewReplyTokens = []
  },
  clearActivityRevealTimer() {
    if (this.activityRevealTimer) clearTimeout(this.activityRevealTimer)
    this.activityRevealTimer = null
  },
  startActivityReveal() {
    this.clearActivityRevealTimer()
    this.setData({ activityReveal: false })
    this.activityRevealTimer = setTimeout(() => {
      this.activityRevealTimer = null
      this.setData({ activityReveal: true })
    }, ACTIVITY_REVEAL_DELAY_MS)
  },
  async loadPreview() {
    const runId = this.data.previewRunId
    if (!runId || this.data.previewLoading) return
    const loadingReview = {
      id: `plan-${runId}`,
      actorId: this.data.previewActor.id,
      state: 'loading',
      content: '',
      autoOpen: true
    }
    this.setData({
      previewLoading: true,
      previewError: '',
      previewReplyText: '',
      previewReplyStreaming: false,
      floatingReview: loadingReview
    })
    try {
      const handoff = readPlanHandoff(runId)
      let detail
      try {
        detail = await api.get(`/agent/runs/${runId}`, { allowCache: false })
      } catch (requestError) {
        if (!handoff) throw requestError
        detail = handoff
      }
      if (this._unloaded) return
      const preview = detail && detail.plan_preview || handoff && handoff.plan_preview
      if (!preview || !Array.isArray(preview.items) || !preview.items.length) {
        throw new Error('这份计划草案已失效，请回到健身房重新告诉我你的目标')
      }
      const write = preview.write || {}
      const isDraft = preview.status === 'draft'
      const isApplied = preview.status === 'applied'
      const contractValid = Number(preview.run_id) === runId
        && preview.read_only === true
        && write.automatic === false
        && ((isDraft && write.status === 'not_applied' && write.confirmation_required === true)
          || (isApplied && write.status === 'applied' && write.confirmation_required === false))
      if (!contractValid) throw new Error('这份计划草案状态异常，请回到健身房重新生成')
      const items = preview.items.map((item, index) => {
        const offset = Math.max(0, Math.min(6, Number(item.date_offset) || 0))
        return {
          ...item,
          key: `${offset}-${index}-${item.title || ''}`,
          dayLabel: PREVIEW_DAY_LABELS[offset],
          categoryLabel: PREVIEW_CATEGORY_LABELS[item.category] || '健康'
        }
      })
      const actorId = detail.presentation && detail.presentation.actor || handoff && handoff.presentation && handoff.presentation.actor
      const previewActor = PREVIEW_ACTORS[actorId] || this.data.previewActor
      const reply = String(detail.reply || handoff && handoff.reply || '我整理了一版计划，你先看看。').trim()
      this.setData({
        preview: { ...preview, items },
        previewActor,
        previewLoading: false
      }, () => this.revealPreviewReply(formatPreviewMessage(reply, preview, items)))
    } catch (error) {
      if (this._unloaded) return
      const content = error.message || '计划草案暂时无法打开'
      this.setData({
        previewLoading: false,
        previewError: content,
        floatingReview: {
          id: `plan-${runId}`,
          actorId: this.data.previewActor.id,
          state: 'error',
          content,
          autoOpen: true
        }
      })
    }
  },
  retryPreview() { this.loadPreview() },
  nextFloatingReview(patch) {
    return Object.assign({}, this.data.floatingReview || {}, patch)
  },
  revealPreviewReply(reply) {
    if (this.previewReplyTimer) clearTimeout(this.previewReplyTimer)
    this._previewReplyTokens = displayTokens(reply)
    this._previewReplyText = ''
    const streaming = this._previewReplyTokens.length > 0
    this.setData({
      previewReplyText: '',
      previewReplyStreaming: streaming,
      floatingReview: {
        id: `plan-${this.data.previewRunId}`,
        actorId: this.data.previewActor.id,
        state: this.data.preview && this.data.preview.write.status === 'applied' ? 'applied' : 'ready',
        content: '',
        streaming,
        applied: this.data.preview && this.data.preview.write.status === 'applied',
        applying: false,
        autoOpen: true
      }
    })
    this.drainPreviewReply()
  },
  drainPreviewReply() {
    if (this._unloaded) return
    const queue = this._previewReplyTokens || []
    if (!queue.length) {
      this.previewReplyTimer = null
      this.setData({
        previewReplyStreaming: false,
        floatingReview: this.nextFloatingReview({ streaming: false })
      })
      return
    }
    const batchSize = queue.length > 80 ? 4 : queue.length > 36 ? 2 : 1
    this._previewReplyText += queue.splice(0, batchSize).join('')
    this.setData({
      previewReplyText: this._previewReplyText,
      floatingReview: this.nextFloatingReview({ content: this._previewReplyText })
    })
    this.previewReplyTimer = setTimeout(() => this.drainPreviewReply(), REVIEW_TOKEN_TICK_MS)
  },
  dismissAppliedPreview() {
    this.clearPreviewTimers()
    clearPlanHandoff(this.data.previewRunId)
    this.setData({ previewMode: false, preview: null, floatingReview: null })
  },
  confirmPreview() {
    const preview = this.data.preview
    if (!preview || this.data.applyingPreview || this._confirmPreviewOpen) return
    if (!preview.write || preview.write.status !== 'not_applied' || preview.write.confirmation_required !== true) return
    this._confirmPreviewOpen = true
    wx.showModal({
      title: '加入这份计划？',
      content: '确认后才会写入你的计划；稍后仍可逐项调整。',
      confirmText: '确认加入',
      confirmColor: '#506336',
      success: result => { if (result.confirm) this.applyPreview() },
      complete: () => { this._confirmPreviewOpen = false }
    })
  },
  async applyPreview() {
    if (this.data.applyingPreview || !this.data.previewRunId) return
    this.setData({
      applyingPreview: true,
      floatingReview: this.nextFloatingReview({ applying: true })
    })
    try {
      await api.post(`/agent/runs/${this.data.previewRunId}/apply-plan`, {})
      if (this._unloaded) return
      this.setData({
        'preview.status': 'applied',
        'preview.write.status': 'applied',
        'preview.write.confirmation_required': false,
        applyingPreview: false,
        floatingReview: this.nextFloatingReview({ state: 'applied', applied: true, applying: false })
      })
      clearPlanHandoff(this.data.previewRunId)
      wx.showToast({ title: '计划已加入', icon: 'success' })
      this.loadToday()
      this.loadActivity()
    } catch (error) {
      if (this._unloaded) return
      this.setData({
        applyingPreview: false,
        floatingReview: this.nextFloatingReview({ applying: false })
      })
      wx.showModal({ title: '暂时无法加入', content: error.message || '请稍后重试', showCancel: false })
    }
  },
  async loadToday() {
    try {
      const [today, agent] = await Promise.all([api.get('/health/plan/today'), api.get('/agent/plans/current')])
      this.setData({ headline: today.headline, items: today.items || [], doneCount: today.done_count || 0, agentPlan: agent.plan || null })
    } catch (error) {
      wx.showToast({ title: error.message || '今日计划加载失败', icon: 'none' })
    }
  },
  async loadActivity() {
    this.setData({ activityLoading: true, activityError: '' })
    try {
      const result = buildPlanActivity(await api.get('/health/plan/activity/year', { allowCache: false }))
      this.setData({
        activityLoading: false,
        activityBands: result.bands,
        activitySummary: result.summary
      })
    } catch (error) {
      this.setData({ activityLoading: false, activityError: error.message || '年度完成记录暂时无法加载' })
    }
  },
  retryActivity() { this.loadActivity() },
  toggleAdd() { this.setData({ showAdd: !this.data.showAdd, addTitle: '', addDesc: '' }) },
  onTitle(e) { this.setData({ addTitle: e.detail.value }) },
  onDesc(e) { this.setData({ addDesc: e.detail.value }) },
  async saveCustom() {
    const title = (this.data.addTitle || '').trim()
    if (!title) return wx.showToast({ title: '请输入计划内容', icon: 'none' })
    if (this.data.saving) return
    this.setData({ saving: true })
    try {
      await api.post('/health/plan/today/custom', { title, description: this.data.addDesc || '', task_type: 'other' })
      this.setData({ showAdd: false, addTitle: '', addDesc: '' })
      wx.showToast({ title: '已添加到今日计划' })
      this.loadToday()
      this.loadActivity()
    } catch (error) {
      wx.showModal({ title: '添加失败', content: error.message || '请稍后重试', showCancel: false })
    } finally {
      this.setData({ saving: false })
    }
  },
  async toggle(e) {
    const key = e.currentTarget.dataset.key
    const done = e.currentTarget.dataset.done === 'true'
    try {
      await api.put(`/health/plan/today/${key}`, { done: !done })
      wx.showToast({ title: done ? '已恢复' : '已完成，可点恢复', icon: 'none' })
      this.loadToday()
      this.loadActivity()
    } catch (error) {
      wx.showToast({ title: '更新失败', icon: 'none' })
    }
  },
  async restore(e) {
    const key = e.currentTarget.dataset.key
    try {
      await api.put(`/health/plan/today/${key}`, { done: false })
      wx.showToast({ title: '已恢复未完成', icon: 'none' })
      this.loadToday()
      this.loadActivity()
    } catch (error) {
      wx.showToast({ title: '恢复失败', icon: 'none' })
    }
  },
  async removeCustom(e) {
    const key = e.currentTarget.dataset.key
    wx.showModal({
      title: '删除这条计划？', content: '删除后不可恢复，仅删除手动添加的计划。', confirmText: '删除', confirmColor: '#c0392b',
      success: async result => {
        if (!result.confirm) return
        try {
          await api.del(`/health/plan/today/custom/${key}`)
          wx.showToast({ title: '已删除' })
          this.loadToday()
          this.loadActivity()
        } catch (error) {
          wx.showToast({ title: '删除失败', icon: 'none' })
        }
      }
    })
  },
  async toggleAgent(e) {
    const id = Number(e.currentTarget.dataset.id)
    const done = e.currentTarget.dataset.done === 'true'
    try {
      await api.put(`/agent/plans/items/${id}`, { done: !done })
      this.loadToday()
    } catch (error) {
      wx.showToast({ title: '更新失败', icon: 'none' })
    }
  },
  toggleWeek() { this.setData({ showWeek: !this.data.showWeek }) }
})
