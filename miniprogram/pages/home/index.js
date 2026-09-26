const api = require('../../utils/request')
const { ensureLogin } = require('../../utils/auth')
const config = require('../../config/index')

const TAB_ROUTES = new Set(['/pages/home/index','/pages/chat/index','/pages/records/index','/pages/plan/index','/pages/profile/index'])

const NAV_ITEMS = [
  { key: 'scan', label: '拍照识餐', icon: '餐', route: '/pages/scan/index' },
  { key: 'media', label: '动作反馈', icon: '动', route: '/pages/media/index' },
  { key: 'insights', label: '健康提醒', icon: '醒', route: '/pages/insights/index' },
  { key: 'trends', label: '7 日趋势', icon: '趋', route: '/pages/trends/index' },
  { key: 'goals', label: '健康目标', icon: '标', route: '/pages/goals/index' },
  { key: 'report', label: '健康周报', icon: '报', route: '/pages/report/index' },
  { key: 'workout', label: '训练计划', icon: '练', route: '/pages/workout/index' },
  { key: 'profile', label: '身体档案', icon: '档', route: '/pages/profile/edit' }
]

Page({
  data: {
    loading: true,
    error: '',
    showOnboarding: false,
    dateLabel: '',
    heroImage: config.HOME_HERO_IMAGE,
    heroImageError: false,
    streak: {},
    insightCount: 0,
    navOpen: false,
    navItems: NAV_ITEMS
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
  async load() {
    this.setData({ loading: true, error: '' })
    try {
      await ensureLogin()
      const [center, insightPayload] = await Promise.all([
        api.get('/health/command-center', { allowCache: true }),
        api.get('/agent/insights', { allowCache: true }).catch(() => null)
      ])
      const insights = insightPayload && Array.isArray(insightPayload.insights) ? insightPayload.insights : []
      this.setData({
        streak: center.streak || {},
        insightCount: insights.length
      })
    } catch (e) {
      this.setData({ error: e.message || '请检查网络和云托管服务状态' })
    } finally {
      this.setData({ loading: false })
    }
  },
  retry() { this.load() },
  onHeroError() { this.setData({ heroImageError: true }) },
  openRoute(route) {
    if (!route) return
    if (TAB_ROUTES.has(route)) wx.switchTab({ url: route })
    else wx.navigateTo({ url: route })
  },
  openNav() { this.setData({ navOpen: true }) },  closeNav() { this.setData({ navOpen: false }) },
  openNavItem(e) {
    this.setData({ navOpen: false })
    this.openRoute(e.currentTarget.dataset.route)
  },
  toCheckin() { wx.navigateTo({ url: '/pages/checkin/index' }) },
  finishOnboarding() { wx.setStorageSync('onboarding_v5', true); this.setData({ showOnboarding: false }) }
})
