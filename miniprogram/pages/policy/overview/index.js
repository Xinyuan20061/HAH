const api = require('../../../utils/request')
const journey = require('../../../utils/policyJourney')

Page({
  data: { loading: true, error: '', warning: '', candidate: null, current: null, history: [] },

  onShow() { this.load() },
  onPullDownRefresh() { this.load().then(() => wx.stopPullDownRefresh()) },

  async load() {
    this.setData({ loading: true, error: '' })
    try {
      await api.ensureToken()
      const wrap = promise => promise.then(value => ({ value }), error => ({ error }))
      const [candidateResult, currentResult, historyResult] = await Promise.all([
        wrap(api.get('/policy/candidates', { allowCache: false })),
        wrap(api.get('/policy/episodes/current', { allowCache: false })),
        wrap(api.get('/policy/history?limit=5', { allowCache: false }))
      ])
      if (candidateResult.error) throw candidateResult.error
      const candidateRes = candidateResult.value
      const currentRes = currentResult.value || {}
      const historyRes = historyResult.value || { items: [] }
      const episode = currentRes && currentRes.episode
      this.setData({
        loading: false,
        warning: currentResult.error || historyResult.error
          ? '当前周期或历史记录暂不可读；请检查网络与个人策略授权设置。'
          : '',
        candidate: candidateRes,
        current: episode ? this.decorateEpisode(episode) : null,
        history: (historyRes.items || []).map(row => ({
          id: row.episode_id,
          date: journey.formatTime(row.started_at),
          status: journey.statusLabel(row.status),
          conclusion: row.conclusion_valid
            ? journey.conclusionLabel(row.conclusion)
            : row.conclusion ? '来源已变化，旧结论已撤回' : '尚无最终结论',
          revoked: !!row.conclusion && !row.conclusion_valid
        }))
      })
    } catch (error) {
      this.setData({ loading: false, error: error.message || '个人策略暂时无法读取' })
    }
  },

  decorateEpisode(episode) {
    const reports = episode.reports || []
    const completed = reports.filter(row => row.execution === 'completed').length
    const opportunities = (episode.opportunities || []).length
    const changed = episode.context_snapshot && episode.context_snapshot.changed_variables || []
    const parameter = changed.find(row => row && row.name === 'session_minutes')
    return Object.assign({}, episode, {
      statusLabel: journey.statusLabel(episode.status),
      startLabel: journey.formatTime(episode.start_at),
      endLabel: journey.formatTime(episode.end_at),
      completed,
      total: opportunities,
      ratio: opportunities ? Math.round(completed / opportunities * 100) : 0,
      sessionMinutes: parameter && parameter.value ? parameter.value : null
    })
  },

  retry() { this.load() },
  openProtocol() { wx.navigateTo({ url: '/pages/policy/protocol/index' }) },
  openEpisode() {
    const current = this.data.current
    if (current) wx.navigateTo({ url: `/pages/policy/episode/index?id=${current.episode_id}` })
  },
  openHistory() { wx.navigateTo({ url: '/pages/policy/history/index' }) },
  openHistoryItem(e) {
    const id = e.currentTarget.dataset.id
    if (id) wx.navigateTo({ url: `/pages/policy/review/index?id=${id}` })
  },
  openCapabilities() { wx.navigateTo({ url: '/pages/settings/capabilities/index' }) }
})
