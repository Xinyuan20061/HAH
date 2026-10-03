const api = require('../../../utils/request')
const journey = require('../../../utils/policyJourney')

function localDate() {
  const d = new Date()
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function localDateFrom(value) {
  const d = new Date(value)
  if (!isFinite(d.getTime())) return localDate()
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function observedAtFor(date, endpoint, episode) {
  let observedAt
  if (date === localDate()) observedAt = new Date()
  else {
    const [year, month, day] = date.split('-').map(Number)
    observedAt = new Date(year, month - 1, day, 12, 0, 0)
  }
  const start = new Date(episode.start_at)
  if (endpoint === 'baseline') {
    const earliest = new Date(start.getTime() - 30 * 24 * 60 * 60 * 1000)
    if (observedAt < earliest) observedAt = earliest
    if (observedAt > start) observedAt = start
  } else {
    const latest = new Date(Math.min(Date.now(), new Date(episode.end_at).getTime()))
    if (observedAt < start) observedAt = start
    if (observedAt > latest) observedAt = latest
  }
  return observedAt.toISOString()
}

function executionLabel(value) {
  if (value === 'completed') return '已完成'
  if (value === 'explicitly_not_completed') return '未完成'
  if (value === 'unknown') return '不确定'
  return '尚未记录'
}

Page({
  data: {
    loading: true,
    busy: false,
    pendingObservation: false,
    error: '',
    episode: null,
    opportunities: [],
    observations: [],
    endpoints: ['周期开始前（基线）', '周期开始后（观察）'],
    endpoint: 'baseline',
    slots: ['第 1 条', '第 2 条', '第 3 条', '第 4 条', '第 5 条', '第 6 条', '第 7 条'],
    slot: 0,
    date: localDate(),
    dateMin: '',
    dateMax: localDate(),
    value: '',
    metricVersion: 'burden-v1',
    canReview: false,
    canStop: false
  },

  onLoad(options) { this.episodeId = options && options.id; this.load() },
  onPullDownRefresh() { this.load().then(() => wx.stopPullDownRefresh()) },

  async load() {
    this.setData({ loading: true, error: '' })
    try {
      await api.ensureToken()
      let episode
      if (this.episodeId) episode = await api.get(`/policy/episodes/${this.episodeId}`, { allowCache: false })
      else episode = (await api.get('/policy/episodes/current', { allowCache: false })).episode
      if (!episode) throw new Error('没有进行中的个人周期。')
      this.episodeId = episode.episode_id
      const unit = await api.get(`/policy/units/${episode.strategy_unit_id}`, { allowCache: false })
      const template = unit.protocol && unit.protocol.template || {}
      const reports = episode.reports || []
      const opportunities = (episode.opportunities || []).map(row => {
        const report = reports.find(item => item.opportunity_id === row.id)
        const scheduled = new Date(row.scheduled_at).getTime()
        return Object.assign({}, row, {
          dateLabel: journey.formatTime(row.scheduled_at),
          executionLabel: executionLabel(report && report.execution),
          canReport: !row.report_id && isFinite(scheduled) && scheduled <= Date.now(),
          reportValue: report && report.execution || ''
        })
      })
      const observations = (episode.observations || []).map(row => Object.assign({}, row, {
        key: `${row.endpoint}_${row.slot}`,
        endpointLabel: row.endpoint === 'baseline' ? '基线' : '周期内',
        dateLabel: journey.formatTime(row.observed_at),
        valueLabel: row.value === null || row.value === undefined ? '未记录数值' : `${row.value} / 10`,
        sourceLabel: row.source_type === 'user_report' ? '本人自报' : '记录核验',
        stateLabel: row.valid ? '' : '来源已变化，请重新补充'
      }))
      const changed = episode.context_snapshot && episode.context_snapshot.changed_variables || []
      const parameter = changed.find(item => item && item.name === 'session_minutes')
      const start = new Date(episode.start_at)
      const baselineDate = new Date(start)
      const baselineMin = new Date(start)
      baselineDate.setDate(baselineDate.getDate() - 1)
      baselineMin.setDate(baselineMin.getDate() - 30)
      this.setData({
        loading: false,
        episode: Object.assign({}, episode, {
          statusLabel: journey.statusLabel(episode.status),
          startLabel: journey.formatTime(episode.start_at),
          endLabel: journey.formatTime(episode.end_at),
          sessionMinutes: parameter && parameter.value || null,
          observationsCount: observations.filter(item => item.valid).length
        }),
        opportunities,
        observations,
        metricVersion: template.metric_version || 'burden-v1',
        date: localDateFrom(baselineDate),
        dateMin: localDateFrom(baselineMin),
        dateMax: localDateFrom(baselineDate),
        canReview: (episode.allowed_actions || []).includes('review_preview'),
        canStop: (episode.allowed_actions || []).includes('stop_proposal')
      })
      this.clearResolvedPendingObservation(episode)
      this.restorePendingObservation(episode)
    } catch (error) {
      this.setData({ loading: false, error: error.message || '周期记录暂时无法读取' })
    }
  },

  clearResolvedPendingObservation(episode) {
    const prefix = `healthmate_policy_observation:${episode.episode_id}:`
    const keys = wx.getStorageInfoSync().keys || []
    for (const key of keys) {
      if (!key.startsWith(prefix)) continue
      const pending = wx.getStorageSync(key)
      const pendingReport = pending && pending.self_reports && pending.self_reports[0]
      if (!pendingReport) continue
      const found = (episode.observations || []).some(row =>
        row.endpoint === pendingReport.endpoint &&
        row.slot === pendingReport.slot &&
        Number(row.value) === Number(pendingReport.value) && row.valid
      )
      if (found) wx.removeStorageSync(key)
    }
  },

  restorePendingObservation(episode) {
    const prefix = `healthmate_policy_observation:${episode.episode_id}:`
    const keys = wx.getStorageInfoSync().keys || []
    for (const key of keys) {
      if (!key.startsWith(prefix)) continue
      const pending = wx.getStorageSync(key)
      const report = pending && pending.self_reports && pending.self_reports[0]
      if (!report) continue
      this.setData({
        pendingObservation: true,
        endpoint: report.endpoint,
        slot: Number(report.slot),
        date: localDateFrom(report.observed_at),
        value: String(report.value)
      })
      return
    }
    this.setData({ pendingObservation: false })
  },

  async reportExecution(e) {
    const opportunity = this.data.opportunities.find(row => row.id === e.currentTarget.dataset.id)
    if (!opportunity || !opportunity.canReport || this.data.busy) return
    wx.showActionSheet({
      itemList: ['这一天完成了', '这一天没有完成', '这一天无法确定'],
      success: result => this.submitExecution(opportunity, ['completed', 'explicitly_not_completed', 'unknown'][result.tapIndex]),
      fail: () => {}
    })
  },

  async submitExecution(opportunity, execution) {
    if (!this.data.episode) return
    const storageKey = `healthmate_policy_report:${this.data.episode.episode_id}:${opportunity.id}`
    let reportId = wx.getStorageSync(storageKey)
    if (!reportId) {
      reportId = api.idempotencyKey('policy-report')
      wx.setStorageSync(storageKey, reportId)
    }
    this.setData({ busy: true, error: '' })
    try {
      await api.post(`/policy/episodes/${this.data.episode.episode_id}/reports`, {
        episode_version: this.data.episode.version,
        report_id: reportId,
        opportunity_id: opportunity.id,
        execution,
        perceived_burden: null,
        confounder_codes: []
      }, { 'Idempotency-Key': reportId })
      wx.removeStorageSync(storageKey)
      await this.load()
      this.setData({ busy: false })
      wx.showToast({ title: '执行情况已记录', icon: 'success' })
    } catch (error) {
      this.setData({ busy: false, error: error.message || '记录失败；刷新后可安全重试' })
      if (error.code === 'POLICY_VERSION_CONFLICT') this.load()
    }
  },

  chooseEndpoint(e) {
    if (this.data.pendingObservation) return
    const index = Number(e.detail.value)
    const endpoint = index === 0 ? 'baseline' : 'followup'
    const episode = this.data.episode
    let date = this.data.date
    let dateMin = this.data.dateMin
    let dateMax = this.data.dateMax
    if (episode) {
      const start = new Date(episode.start_at)
      const end = new Date(episode.end_at)
      const selected = endpoint === 'baseline' ? new Date(start) : new Date()
      if (endpoint === 'baseline') {
        selected.setDate(selected.getDate() - 1)
        const earliest = new Date(start)
        earliest.setDate(earliest.getDate() - 30)
        dateMin = localDateFrom(earliest)
        dateMax = localDateFrom(selected)
      } else {
        if (selected < start) selected.setTime(start.getTime())
        if (selected > end) selected.setTime(end.getTime())
        dateMin = localDateFrom(start)
        dateMax = localDateFrom(new Date(Math.min(Date.now(), end.getTime())))
      }
      date = localDateFrom(selected)
    }
    this.setData({ endpoint, date, dateMin, dateMax, value: '' })
  },
  chooseSlot(e) { if (!this.data.pendingObservation) this.setData({ slot: Number(e.detail.value) }) },
  chooseDate(e) { if (!this.data.pendingObservation) this.setData({ date: e.detail.value }) },
  inputValue(e) { if (!this.data.pendingObservation) this.setData({ value: e.detail.value }) },

  async saveObservation() {
    const episode = this.data.episode
    const numeric = Number(this.data.value)
    if (!episode || this.data.busy || this.data.value === '' || !isFinite(numeric) || numeric < 0 || numeric > 10) {
      wx.showToast({ title: '请输入 0 到 10 之间的负担自评分', icon: 'none' })
      return
    }
    const slot = this.data.slot
    const used = (episode.observations || []).some(row =>
      row.endpoint === this.data.endpoint && row.slot === slot && row.valid
    )
    if (used) {
      wx.showToast({ title: '这条记录槽已有有效证据，请选择另一条', icon: 'none' })
      return
    }
    const storageKey = `healthmate_policy_observation:${episode.episode_id}:${this.data.endpoint}:${slot}`
    let payload = wx.getStorageSync(storageKey)
    if (!payload) {
      const unique = api.idempotencyKey('self-report').replace(/[^a-zA-Z0-9_-]/g, '')
      payload = {
        episode_version: episode.version,
        self_reports: [{
          endpoint: this.data.endpoint,
          slot,
          source_id: unique,
          observed_at: observedAtFor(this.data.date, this.data.endpoint, episode),
          metric_version: this.data.metricVersion,
          value: numeric
        }]
      }
    }
    payload.episode_version = episode.version
    wx.setStorageSync(storageKey, payload)
    this.setData({ busy: true, error: '' })
    try {
      await api.post(`/policy/episodes/${episode.episode_id}/observations`, payload, {
        'Idempotency-Key': `obs-${episode.episode_id}-${this.data.endpoint}-${slot}`
      })
      wx.removeStorageSync(storageKey)
      this.setData({ pendingObservation: false })
      await this.load()
      this.setData({ busy: false, value: '' })
      wx.showToast({ title: '本人自报已单独标记保存', icon: 'success' })
    } catch (error) {
      this.setData({ busy: false, error: error.message || '观察记录未能保存；刷新后可安全重试' })
      if (!error.statusCode || error.statusCode >= 500) this.setData({ pendingObservation: true })
      if (error.statusCode >= 400 && error.statusCode < 500 && error.code !== 'POLICY_VERSION_CONFLICT') {
        wx.removeStorageSync(storageKey)
        this.setData({ pendingObservation: false })
      }
      if (error.code === 'POLICY_VERSION_CONFLICT') this.load()
    }
  },

  async discardPendingObservation() {
    const episode = this.data.episode
    if (!episode || !this.data.pendingObservation || this.data.busy) return
    const confirmed = await new Promise(resolve => wx.showModal({
      title: '放弃安全重试？',
      content: '这只会清除本机保存的待重试内容，不会删除服务器上已经成功保存的记录。',
      confirmText: '放弃重试',
      cancelText: '继续保留',
      success: result => resolve(!!result.confirm),
      fail: () => resolve(false)
    }))
    if (!confirmed) return
    const key = `healthmate_policy_observation:${episode.episode_id}:${this.data.endpoint}:${this.data.slot}`
    wx.removeStorageSync(key)
    this.setData({ pendingObservation: false })
    await this.load()
  },

  async stopEpisode() {
    const episode = this.data.episode
    if (!episode || !this.data.canStop || this.data.busy) return
    this.setData({ busy: true, error: '' })
    try {
      const proposal = await api.post(`/policy/episodes/${episode.episode_id}/stop-proposal`, {
        episode_version: episode.version,
        reason_code: 'user_requested'
      })
      const result = await journey.confirmProposal(proposal, '确认停止周期', '停止后不会生成效果结论；已记录的数据仍会保留。')
      if (result) {
        wx.showToast({ title: '周期已停止', icon: 'success' })
        wx.redirectTo({ url: '/pages/policy/overview/index' })
      }
      this.setData({ busy: false })
    } catch (error) {
      this.setData({ busy: false, error: error.message || '停止申请暂时无法执行' })
    }
  },

  openReview() {
    if (this.data.episode) wx.navigateTo({ url: `/pages/policy/review/index?id=${this.data.episode.episode_id}` })
  },
  retry() { this.load() }
})
