const api = require('../../utils/request')
const { ensureLogin } = require('../../utils/auth')
const energyChart = require('../../utils/energyChart')

const MEALS = [
  { key: 'breakfast', label: '早餐', color: '#c4e267' },
  { key: 'lunch', label: '午餐', color: '#e9efd9' },
  { key: 'dinner', label: '晚餐', color: '#506336' },
  { key: 'snack', label: '加餐', color: '#5f665f' }
]

Page({
  data: {
    loading: true, error: '', dashboard: null, today: null, target: null, resting: null, meals: [],
    activeSummary: '触摸柱形查看单日餐次与运动数据', balanceTitle: '', netText: '--', netBadgeStyle: '',
    intakeBarStyle: 'width:0%', targetMarkStyle: 'left:0%'
  },
  onLoad() { this._loadedOnce = false },
  onShow() { this.load() },
  onUnload() { this._unloaded = true; this._energyCharts = null },
  async load() {
    const requestId = Date.now()
    this._requestId = requestId
    this.setData({ loading: true, error: '' })
    try {
      await ensureLogin()
      const dashboard = await api.get('/health/energy-dashboard', { allowCache: false })
      if (this._unloaded || this._requestId !== requestId) return
      const today = dashboard.today || {}
      const target = dashboard.target || {}
      const chartMax = Math.max(Number(today.intake) || 0, Number(target.upper) || 1) * 1.08
      const meals = MEALS.map(item => ({
        ...item,
        value: Number(today[item.key]) || 0,
        style: `width:${Math.min(100, (Number(today[item.key]) || 0) / chartMax * 100).toFixed(1)}%;background:${item.color}`
      }))
      const balance = this.balanceCopy(today.balance, today.net)
      this.setData({
        loading: false, dashboard, today, target, resting: dashboard.resting || {}, meals,
        balanceTitle: balance.title, netText: balance.net, netBadgeStyle: balance.style,
        intakeBarStyle: `width:${Math.min(100, (Number(today.intake) || 0) / chartMax * 100).toFixed(1)}%`,
        targetMarkStyle: `left:${Math.min(98, (Number(target.today) || 0) / chartMax * 100).toFixed(1)}%`,
        activeSummary: this.daySummary(today)
      }, () => setTimeout(() => this.renderChart(), 60))
      this._loadedOnce = true
    } catch (error) {
      if (this._unloaded || this._requestId !== requestId) return
      this.setData({ loading: false, error: error.message || '能量数据暂时无法加载' })
    }
  },
  balanceCopy(balance, net) {
    if (balance === 'complete_profile') return { title: '完善档案后计算今日收支', net: '--', style: 'background:#e9efd9;color:#506336' }
    const value = Math.round(Number(net) || 0)
    if (balance === 'positive') return { title: '今日已知摄入高于已知消耗', net: `+${value}`, style: 'background:#111613;color:#fff' }
    if (balance === 'negative') return { title: '今日已知摄入低于已知消耗', net: `${value}`, style: 'background:#e9efd9;color:#506336' }
    return { title: '今日已知能量基本平衡', net: `${value > 0 ? '+' : ''}${value}`, style: 'background:#c4e267;color:#111613' }
  },
  daySummary(day) {
    if (!day) return '触摸柱形查看单日数据'
    const recorded = day.observed && day.observed.diet
    const intake = recorded ? `${Math.round(Number(day.intake) || 0)} kcal` : '餐食未记录'
    const exercise = day.observed && day.observed.exercise ? `${Math.round(Number(day.exercise) || 0)} kcal` : '运动未记录'
    return `${day.label} · 摄入 ${intake} · 运动 ${exercise}`
  },
  renderChart() {
    const dashboard = this.data.dashboard
    if (!dashboard || !dashboard.days) return
    const options = { days: dashboard.days }
    if (this._energyCharts && this._energyCharts.week) energyChart.draw(this, 'week', options)
    else energyChart.init(this, '#energyCanvas', 'week', options)
  },
  chartTouch(event) {
    const index = energyChart.touch(this, 'week', event)
    if (index < 0) return
    this.setData({ activeSummary: this.daySummary(this.data.dashboard.days[index]) })
  },
  retry() { this.load() },
  scan() { wx.navigateTo({ url: '/pages/scan/index' }) },
  checkin() { wx.navigateTo({ url: '/pages/checkin/index' }) },
  exercise() { wx.navigateTo({ url: '/pages/records/exercise' }) },
  motion() { wx.navigateTo({ url: '/pages/media/index' }) }
})
