const api = require('../../utils/request')
const { ensureLogin } = require('../../utils/auth')
const energyChart = require('../../utils/energyChart')

const MEALS = [
  { key: 'breakfast', label: '早餐', shortLabel: '早', color: '#c4e267' },
  { key: 'lunch', label: '午餐', shortLabel: '午', color: '#e9efd9' },
  { key: 'dinner', label: '晚餐', shortLabel: '晚', color: '#506336' },
  { key: 'snack', label: '加餐', shortLabel: '加', color: '#5f665f' }
]

Page({
  data: {
    loading: true, error: '', dashboard: null, today: null, target: null, resting: null, meals: [],
    activeSummary: '触摸柱形查看单日餐次与运动数据', netText: '--',
    targetExceeded: false, budgetFillStyle: 'width:0%', budgetWindowStyle: 'left:0%;width:0%',
    businessDate: '', hasRecords: false
  },
  onLoad() { this._loadedOnce = false },
  onShow() { this.load() },
  onHide() { this.resetChart() },
  onUnload() { this._unloaded = true; this.resetChart() },
  resetChart() {
    if (this._chartTimer) clearTimeout(this._chartTimer)
    this._chartTimer = null
    this._energyCharts = null
  },
  async load() {
    // `loading` temporarily removes the canvas via wx:if. A chart context from
    // the previous render therefore points at a detached node and must never be
    // reused when this cached page is shown again.
    this.resetChart()
    const requestId = Date.now()
    this._requestId = requestId
    this.setData({ loading: true, error: '' })
    try {
      await ensureLogin()
      const dashboard = await api.get('/health/energy-dashboard', { allowCache: false })
      if (this._unloaded || this._requestId !== requestId) return
      const today = dashboard.today || {}
      const target = dashboard.target || {}
      const targetToday = Math.max(1, Number(target.today) || 1)
      const meals = MEALS.map((item, index) => {
        const value = Math.round(Number(today[item.key]) || 0)
        const width = value ? Math.max(4, Math.min(100, value / targetToday * 100)) : 0
        return { ...item, value, rowStyle: `width:${width.toFixed(1)}%;background:${item.color};animation-delay:${index * 60}ms` }
      })
      const balance = this.balanceCopy(today.balance, today.net)
      const budget = this.budgetCopy(today, target)
      const hasRecords = ['breakfast', 'lunch', 'dinner', 'snack'].some(key => Number(today[key]) > 0)
        || Number(today.intake) > 0 || Number(today.exercise) > 0
      this.setData({
        loading: false, dashboard, today, target, resting: dashboard.resting || {}, meals,
        netText: balance.net,
        targetExceeded: budget.exceeded, budgetFillStyle: budget.fillStyle,
        budgetWindowStyle: budget.windowStyle,
        businessDate: dashboard.business_date || dashboard.date || '',
        hasRecords,
        activeSummary: this.daySummary(today)
      }, () => {
        this._chartTimer = setTimeout(() => {
          this._chartTimer = null
          this.renderChart()
        }, 60)
      })
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
  budgetCopy(today, target) {
    const intake = Math.max(0, Number(today.intake) || 0)
    const lower = Math.max(0, Number(target.lower) || 0)
    const upper = Math.max(lower + 1, Number(target.upper) || 1)
    const max = Math.ceil(Math.max(upper, intake) * 1.08 / 50) * 50
    const pct = value => Math.min(100, Math.max(0, value / max * 100))
    return {
      exceeded: intake > upper,
      fillStyle: `width:${pct(intake).toFixed(1)}%`,
      windowStyle: `left:${pct(lower).toFixed(1)}%;width:${Math.max(2, pct(upper) - pct(lower)).toFixed(1)}%`
    }
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
    const options = { days: dashboard.days, compact: true }
    if (this._energyCharts && this._energyCharts.week) energyChart.draw(this, 'week', options)
    else energyChart.init(this, '#energyCanvas', 'week', options)
  },
  retry() { this.load() },
  trends() { wx.navigateTo({ url: '/pages/trends/index' }) },
  scan() { wx.navigateTo({ url: '/pages/scan/index' }) },
  checkin() { wx.navigateTo({ url: '/pages/checkin/index' }) },
  exercise() { wx.navigateTo({ url: '/pages/records/exercise' }) },
  motion() { wx.navigateTo({ url: '/pages/media/index' }) },
  /**
   * Entry point to the diet ledger (spec §6.5). Without this the registered
   * `/pages/records/diet` page had zero references anywhere in the product.
   */
  diet(e) {
    const mealType = (e && e.currentTarget && e.currentTarget.dataset.meal) || ''
    const query = [`date=${this.data.businessDate || ''}`]
    if (mealType) query.push(`meal_type=${mealType}`)
    wx.navigateTo({ url: `/pages/records/diet?${query.join('&')}` })
  }
})
