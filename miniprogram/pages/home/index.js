const api = require('../../utils/request')
const { ensureLogin } = require('../../utils/auth')
const { normalizeExperiment } = require('../../utils/insightPresentation')

const TAB_ROUTES = new Set(['/pages/home/index','/pages/chat/index','/pages/records/index','/pages/plan/index','/pages/profile/index'])

Page({
  data: {
    loading: true,
    error: '',
    showOnboarding: false,
    onboardingStep: 0,
    dateLabel: '',
    slides: [],
    summary: {},
    plan: { items: [], done_count: 0 },
    streak: {},
    focus: { title: '先记录一项真实数据', reason: '真实记录是个性化建议的起点。', cta: '开始记录', route: '/pages/checkin/index', tone: 'calm', eyebrow: '建议先做' },
    dataQuality: { sources_observed: 0, sources_total: 3 },
    aiSystem: {},
    insightCount: 0,
    insightSummary: '',
    topInsight: null,
    todayExperiment: null,
    latest: {},
    score: '--',
    exPct: 0,
    waterPct: 0,
    sleepPct: 0
  },
  onLoad() {
    const now = new Date()
    const week = ['周日','周一','周二','周三','周四','周五','周六'][now.getDay()]
    this.setData({
      showOnboarding: !wx.getStorageSync('onboarding_v5'),
      dateLabel: `${now.getMonth()+1}月${now.getDate()}日 · ${week}`
    })
  },
  onShow() { this.load() },
  pct(value, target) { return Math.min(100, Math.round((Number(value)||0) / (Number(target)||1) * 100)) },
  async load() {
    this.setData({ loading: true, error: '' })
    try {
      await ensureLogin()
      const [center, insightPayload] = await Promise.all([
        api.get('/health/command-center', { allowCache: true }),
        api.get('/agent/insights', { allowCache: true }).catch(() => null)
      ])
      const summary = center.today || {}
      const insights = insightPayload && Array.isArray(insightPayload.insights) ? insightPayload.insights : []
      this.setData({
        summary,
        plan: center.plan || { items: [], done_count: 0 },
        streak: center.streak || {},
        focus: center.focus || this.data.focus,
        dataQuality: center.data_quality || this.data.dataQuality,
        aiSystem: center.ai_system || {},
        insightCount: insights.length,
        insightSummary: insightPayload && insightPayload.summary || '',
        topInsight: insights[0] || null,
        todayExperiment: normalizeExperiment(insightPayload && insightPayload.active_experiment),
        latest: center.latest || {},
        score: center.data_quality && center.data_quality.score !== null ? center.data_quality.score : '--',
        exPct: this.pct(summary.exercise_min, summary.exercise_target),
        waterPct: this.pct(summary.water_ml, summary.water_target),
        sleepPct: this.pct(summary.sleep_hours, summary.sleep_target)
      })
    } catch (e) {
      this.setData({ error: e.message || '请检查网络和云托管服务状态' })
    } finally {
      this.setData({ loading: false })
    }
  },
  retry() { this.load() },
  openRoute(route) {
    if (!route) return
    if (TAB_ROUTES.has(route)) wx.switchTab({ url: route })
    else wx.navigateTo({ url: route })
  },
  goFocus() { this.openRoute(this.data.focus.route) },
  async togglePlan(e) {
    const key = e.currentTarget.dataset.key
    const done = e.currentTarget.dataset.done === true || e.currentTarget.dataset.done === 'true'
    if (!key) return
    try {
      await api.put(`/health/plan/today/${key}`, { done: !done })
      await this.load()
    } catch (err) {
      wx.showToast({ title: err.message || '更新失败', icon: 'none' })
    }
  },
  nextOnboarding() {
    if (this.data.onboardingStep < 2) this.setData({ onboardingStep: this.data.onboardingStep + 1 })
    else this.finishOnboarding()
  },
  finishOnboarding() { wx.setStorageSync('onboarding_v5', true); this.setData({ showOnboarding: false }) },
  toChat() { wx.switchTab({ url: '/pages/chat/index' }) },
  toInsights() { wx.navigateTo({ url: '/pages/insights/index' }) },
  toCheckin() { wx.navigateTo({ url: '/pages/checkin/index' }) },
  toPlan() { wx.switchTab({ url: '/pages/plan/index' }) },
  toScan() { wx.navigateTo({ url: '/pages/scan/index' }) },
  toWorkout() { wx.navigateTo({ url: '/pages/workout/index' }) },
  toMedia() { wx.navigateTo({ url: '/pages/media/index' }) }
})
