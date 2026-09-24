const api = require('../../utils/request')
const { normalizeInsights, normalizeExperiment } = require('../../utils/insightPresentation')

const TAB_ROUTES = new Set(['/pages/home/index', '/pages/chat/index', '/pages/records/index', '/pages/plan/index', '/pages/profile/index'])

Page({
  data: {
    loading: true,
    refreshing: false,
    error: '',
    summary: '',
    policy: '',
    insights: [],
    dataQuality: null,
    activeExperiment: null,
    experimentSending: false,
    feedbackSendingCode: '',
    updatedAt: ''
  },
  onLoad() { this.load() },
  onPullDownRefresh() { this.load(true) },
  async load(refreshing = false) {
    this.setData({ loading: !refreshing, refreshing, error: '' })
    try {
      await api.ensureToken()
      const payload = await api.get('/agent/insights', { allowCache: !refreshing })
      const now = new Date()
      this.setData({
        summary: payload.summary || '',
        policy: payload.policy || '',
        insights: normalizeInsights(payload),
        activeExperiment: normalizeExperiment(payload.active_experiment),
        dataQuality: payload.data_quality || null,
        updatedAt: `${now.getMonth() + 1}月${now.getDate()}日 ${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`
      })
    } catch (e) {
      this.setData({ error: e.message || '健康提醒暂时没有加载出来' })
    } finally {
      this.setData({ loading: false, refreshing: false })
      if (wx.stopPullDownRefresh) wx.stopPullDownRefresh()
    }
  },
  retry() { this.load() },
  openAction(e) {
    const route = e.currentTarget.dataset.route
    if (!route) return
    if (TAB_ROUTES.has(route)) wx.switchTab({ url: route })
    else wx.navigateTo({ url: route })
  },
  askAssistant(e) {
    const prompt = e.currentTarget.dataset.prompt
    if (prompt) wx.setStorageSync('healthmate_insight_prompt', prompt)
    wx.switchTab({ url: '/pages/chat/index' })
  },
  async submitFeedback(e) {
    const code = e.currentTarget.dataset.code
    const verdict = e.currentTarget.dataset.verdict
    if (!code || !verdict || this.data.feedbackSendingCode) return
    this.setData({ feedbackSendingCode: code })
    try {
      const result = await api.post(`/agent/insights/${code}/feedback`, { verdict })
      const labels = { helpful: '已标记有帮助', inaccurate: '已反馈不准确', resolved: '已标记处理完成' }
      const insights = this.data.insights.map(item => item.code === code ? Object.assign({}, item, { feedback: verdict, feedbackLabel: labels[verdict] }) : item)
      this.setData({ insights })
      wx.showToast({ title: labels[result.verdict] || '反馈已记录', icon: 'none' })
    } catch (err) {
      wx.showToast({ title: err.message || '反馈暂时没有保存', icon: 'none' })
      if (err.statusCode === 409) this.load(true)
    } finally {
      this.setData({ feedbackSendingCode: '' })
    }
  },
  async startExperiment(e) {
    if (this.data.experimentSending || this.data.activeExperiment) return
    const code = e.currentTarget.dataset.code
    const variant = e.currentTarget.dataset.variant
    const insight = this.data.insights.find(item => item.code === code)
    const proposal = insight && insight.experimentProposal
    const choice = proposal && proposal.variants && proposal.variants.find(item => item.key === variant)
    if (!proposal || !choice) return
    const confirm = await new Promise(resolve => wx.showModal({
      title: `启动${choice.label}微实验？`,
      content: `${choice.days} 天内：${choice.action}\n\n结果只表示相关变化，不证明因果。`,
      confirmText: '确认启动',
      success: result => resolve(result.confirm),
      fail: () => resolve(false)
    }))
    if (!confirm) return
    this.setData({ experimentSending: true })
    try {
      const result = await api.post('/agent/experiments', { insight_code: code, variant })
      this.setData({ activeExperiment: normalizeExperiment(result.experiment) })
      wx.showToast({ title: '微实验已启动', icon: 'success' })
    } catch (err) {
      wx.showToast({ title: err.message || '启动失败', icon: 'none' })
      if (err.statusCode === 409) this.load(true)
    } finally {
      this.setData({ experimentSending: false })
    }
  },
  async finishExperiment() {
    const current = this.data.activeExperiment
    if (!current || !current.can_finish || this.data.experimentSending) return
    this.setData({ experimentSending: true })
    try {
      const result = await api.post(`/agent/experiments/${current.id}/finish`, {})
      const outcome = result.experiment && result.experiment.outcome
      await new Promise(resolve => wx.showModal({
        title: '微实验复盘',
        content: `${(outcome && outcome.summary) || '复盘已生成。'}\n\n这只是实验期相关变化，不证明因果。`,
        showCancel: false,
        confirmText: '知道了',
        complete: resolve
      }))
      await this.load(true)
    } catch (err) {
      wx.showToast({ title: err.message || '暂时无法复盘', icon: 'none' })
    } finally {
      this.setData({ experimentSending: false })
    }
  },
  async cancelExperiment() {
    const current = this.data.activeExperiment
    if (!current || current.status !== 'active' || this.data.experimentSending) return
    const confirm = await new Promise(resolve => wx.showModal({
      title: '停止当前微实验？',
      content: '停止后不会删除你已经记录的健康数据。',
      confirmText: '停止实验',
      confirmColor: '#9c4f45',
      success: result => resolve(result.confirm),
      fail: () => resolve(false)
    }))
    if (!confirm) return
    this.setData({ experimentSending: true })
    try {
      await api.post(`/agent/experiments/${current.id}/cancel`, {})
      this.setData({ activeExperiment: null })
      wx.showToast({ title: '微实验已停止', icon: 'none' })
    } catch (err) {
      wx.showToast({ title: err.message || '停止失败', icon: 'none' })
    } finally {
      this.setData({ experimentSending: false })
    }
  }
})
