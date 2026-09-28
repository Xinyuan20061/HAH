const api = require('../../utils/request')
const { buildPlanActivity } = require('../../utils/planActivity')

Page({
  data: {
    headline: '今天只做三件真正有用的事', items: [], doneCount: 0, agentPlan: null,
    showWeek: false, showAdd: false, addTitle: '', addDesc: '', saving: false,
    activityLoading: true, activityError: '', activityWeeks: [], activityMonths: [],
    activitySummary: { completeDays: 0, partialDays: 0, activeDays: 0 }
  },
  onShow() {
    this.loadToday()
    this.loadActivity()
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
        activityWeeks: result.weeks,
        activityMonths: result.months,
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
