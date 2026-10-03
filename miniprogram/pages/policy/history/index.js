const api = require('../../../utils/request')
const journey = require('../../../utils/policyJourney')

Page({
  data: { loading: true, busy: false, error: '', rows: [], cursor: null, hasMore: false },
  onShow() { this.load(false) },
  onPullDownRefresh() { this.load(false).then(() => wx.stopPullDownRefresh()) },

  async load(append) {
    this.setData({ loading: true, error: '' })
    try {
      await api.ensureToken()
      const cursor = append ? this.data.cursor : null
      const query = cursor ? `?limit=20&cursor=${encodeURIComponent(cursor)}` : '?limit=20'
      const response = await api.get(`/policy/history${query}`, { allowCache: false })
      const page = (response.items || []).map(row => ({
        id: row.episode_id,
        date: journey.formatTime(row.started_at),
        status: journey.statusLabel(row.status),
        conclusion: row.conclusion_valid
          ? journey.conclusionLabel(row.conclusion)
          : row.conclusion ? '来源已变化，旧结论已撤回' : '未形成最终结论',
        invalidated: !!row.conclusion && !row.conclusion_valid,
        canExplain: !!row.episode_id
      }))
      this.setData({
        loading: false,
        rows: append ? this.data.rows.concat(page) : page,
        cursor: response.next_cursor || null,
        hasMore: !!response.next_cursor
      })
    } catch (error) {
      this.setData({ loading: false, error: error.message || '历史记录暂时无法读取' })
    }
  },

  loadMore() { if (!this.data.loading && this.data.hasMore) this.load(true) },
  openEpisode(e) {
    const id = e.currentTarget.dataset.id
    if (id) wx.navigateTo({ url: `/pages/policy/review/index?id=${id}` })
  },

  async resetMemory() {
    if (this.data.busy) return
    const approved = await new Promise(resolve => wx.showModal({
      title: '重置个人策略经验？',
      content: '这会让后续排序从中性经验重新开始。历史周期和原始健康记录仍保留；过往结论不会被改写。',
      confirmText: '继续生成确认请求',
      cancelText: '取消',
      success: result => resolve(!!result.confirm),
      fail: () => resolve(false)
    }))
    if (!approved) return
    this.setData({ busy: true, error: '' })
    try {
      const proposal = await api.post('/policy/memory/reset-proposal', { scope: 'all', strategy_id: null, version: 1 })
      const result = await journey.confirmProposal(proposal, '最后确认重置', '确认后仅重置当前个人策略经验；周期与原始记录仍保留。')
      if (result) wx.showToast({ title: '后续经验已从中性状态开始', icon: 'success' })
      this.setData({ busy: false })
    } catch (error) {
      this.setData({ busy: false, error: error.message || '重置请求暂时无法执行' })
    }
  },
  retry() { this.load(false) }
})
