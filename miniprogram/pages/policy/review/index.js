const api = require('../../../utils/request')
const journey = require('../../../utils/policyJourney')
const acquisitionApi = require('../../../utils/policyAcquisition')

function labelBinary(value, positive, negative) {
  if (value === 1) return positive
  if (value === 0) return negative
  return '证据不足，无法判断'
}

Page({
  data: { loading: true, busy: false, error: '', episode: null, verdict: null, explanation: null, canFinish: false, revoked: false,
    repairTargets: [], acquisitionEvents: [], selectedRepairTarget: null, repairValue: '', rereviewContext: null, rereviewPreview: null },
  onLoad(options) { this.episodeId = options && options.id; this.load() },
  onPullDownRefresh() { this.load().then(() => wx.stopPullDownRefresh()) },

  async load() {
    this.setData({ loading: true, error: '' })
    try {
      await api.ensureToken()
      const episode = await api.get(`/policy/episodes/${this.episodeId}`, { allowCache: false })
      const explanation = await api.get(`/policy/episodes/${this.episodeId}/explanation`, { allowCache: false })
      let preview = null
      if ((episode.allowed_actions || []).includes('review_preview')) {
        preview = await api.post(`/policy/episodes/${this.episodeId}/review-preview`, {})
      }
      const stored = (episode.adjudications || []).find(row => row.revision === episode.effective_adjudication_revision)
      const verdict = stored ? {
        execution_label: stored.execution_label,
        support_label: stored.support_label,
        availability_label: stored.availability_label,
        conclusion: stored.conclusion,
        reasons: stored.reasons,
        observed_score: null,
        valid: stored.valid,
        stale: stored.stale,
        preview: false
      } : preview ? Object.assign({}, preview, { valid: true, stale: false, preview: true }) : null
      const protocol = explanation.protocol || {}
      const parameters = protocol.parameters || {}
      const changed = explanation.context_snapshot && explanation.context_snapshot.changed_variables || []
      const session = changed.find(item => item && item.name === 'session_minutes')
      const reasons = (verdict && verdict.reasons || []).map(journey.reasonLabel)
      const refs = explanation.evidence_refs || []
      let repairTargets = []
      let acquisitionEvents = []
      let rereviewContext = null
      try {
        const history = await acquisitionApi.getHistory(this.episodeId)
        acquisitionEvents = (history.items || []).map(item => Object.assign({}, item, {
          dateLabel: journey.formatTime(item.created_at),
          eventLabel: ({session_started: '本人开启按需核查', consent_renewed: '本人再次确认核查',
            session_paused: '本人暂停核查', session_resumed: '本人恢复核查',
            question_issued: '发出一条核查问题', question_answered: '答复已记录',
            question_timed_out: '问题超时，未写入事实', question_obsoleted: '证据变化，旧问题失效',
            source_invalidated: '来源修订，旧判断已阻断', observation_repaired: '观察记录已修订，等待再复查',
            episode_rereviewed: '本人确认生成新的复查版本'})[item.type] || '取证状态更新'
        }))
      } catch (ignored) {}
      if (episode.status === 'reviewed') {
        try {
          const result = await acquisitionApi.getRepairTargets(this.episodeId)
          repairTargets = (result.items || []).map(item => Object.assign({}, item, {
            endpointLabel: item.endpoint === 'baseline' ? '基线' : '周期内',
            dateLabel: journey.formatTime(item.observed_at),
            valueLabel: item.value === null || item.value === undefined ? '无数值' : `${item.value} / 10`
          }))
        } catch (ignored) {}
        try { rereviewContext = await acquisitionApi.getRereviewContext(this.episodeId) }
        catch (ignored) {}
      }
      this.setData({
        loading: false,
        episode: Object.assign({}, episode, {
          statusLabel: journey.statusLabel(episode.status),
          startLabel: journey.formatTime(episode.start_at),
          endLabel: journey.formatTime(episode.end_at),
          sessionMinutes: session && session.value || parameters.session_minutes || null
        }),
        explanation: {
          metricLabel: protocol.template && protocol.template.metric_key === 'burden' ? '训练负担自评（0–10）' : '协议内观察指标',
          expectedDays: protocol.expected_days || 7,
          minimumDays: protocol.minimum_days || 5,
          evidenceCount: refs.length || (episode.observations || []).filter(row => row.valid).length,
          limitations: explanation.limitations || [],
          contextChanged: !!(explanation.context_snapshot && explanation.context_snapshot.followup_state_snapshot_hash && explanation.context_snapshot.followup_state_snapshot_hash !== explanation.context_snapshot.baseline_state_snapshot_hash)
        },
        verdict: verdict ? Object.assign({}, verdict, {
          label: journey.conclusionLabel(verdict.conclusion),
          executionLabel: labelBinary(verdict.execution_label, '执行记录达到门槛', '执行记录未达到门槛'),
          supportLabel: labelBinary(verdict.support_label, '观察到预设方向的支持', '未观察到预设方向的支持'),
          availabilityLabel: labelBinary(verdict.availability_label, '观察资料达到门槛', '观察资料不足'),
          reasons,
          scoreLabel: typeof verdict.observed_score === 'number' ? Number(verdict.observed_score.toFixed(2)) : null
        }) : null,
        canFinish: (episode.allowed_actions || []).includes('finish_proposal'),
        revoked: !!(explanation.conclusion && (!explanation.conclusion.valid || explanation.conclusion.stale)),
        repairTargets, acquisitionEvents, rereviewContext, rereviewPreview: null
      })
    } catch (error) {
      this.setData({ loading: false, error: error.message || '复查证据暂时无法读取' })
    }
  },

  async finish() {
    const episode = this.data.episode
    if (!episode || !this.data.canFinish || this.data.busy) return
    this.setData({ busy: true, error: '' })
    try {
      const proposal = await api.post(`/policy/episodes/${episode.episode_id}/finish-proposal`, {
        episode_version: episode.version
      })
      const result = await journey.confirmProposal(
        proposal,
        '确认生成本周期复查',
        '系统将按开始前冻结的门槛复核执行、观察覆盖和来源有效性。若证据不足，会如实给出“无法判断”。'
      )
      if (result) await this.load()
      this.setData({ busy: false })
    } catch (error) {
      this.setData({ busy: false, error: error.message || '复查申请暂时无法执行' })
    }
  },

  beginObservationRepair(e) {
    const id = Number(e.currentTarget.dataset.id)
    const target = this.data.repairTargets.find(item => item.observation_ref_id === id)
    if (!target || !target.can_replace_with_self_report || this.data.busy) return
    this.repairStartedAt = Date.now()
    this.setData({ selectedRepairTarget: target, repairValue: String(target.value === null ? '' : target.value), error: '' })
  },

  inputRepairValue(e) { this.setData({ repairValue: e.detail.value }) },
  cancelObservationRepair() { this.setData({ selectedRepairTarget: null, repairValue: '' }) },

  async saveObservationRepair() {
    const target = this.data.selectedRepairTarget
    const episode = this.data.episode
    const value = Number(this.data.repairValue)
    if (!target || !episode || !isFinite(value) || value < 0 || value > 10 || this.data.busy) {
      wx.showToast({ title: '请输入 0 到 10 之间的本人自评', icon: 'none' })
      return
    }
    const confirmed = await new Promise(resolve => wx.showModal({
      title: '更正这条本人自报？',
      content: '系统会保留旧修订、撤回旧复查结果，并要求你确认后重新复查。不会新增观察日期或改变原协议门槛。',
      confirmText: '更正并保留历史', cancelText: '取消',
      success: result => resolve(!!result.confirm), fail: () => resolve(false)
    }))
    if (!confirmed) return
    this.setData({ busy: true, error: '' })
    try {
      const scope = `observation-repair:${this.episodeId}:${target.observation_ref_id}:${episode.version}:${target.observation_revision}`
      const pending = acquisitionApi.getPending(scope)
      const body = pending && pending.body || {
        expected_episode_version: episode.version,
        observation_ref_id: target.observation_ref_id,
        expected_observation_revision: target.observation_revision,
        expected_source_revision: target.source_revision,
        expected_observation_hash: target.observation_hash,
        confirmation: true,
        burden_value: value,
        observed_at: target.observed_at,
        client_elapsed_ms: Math.min(600000, Math.max(0, Date.now() - (this.repairStartedAt || Date.now())))
      }
      await acquisitionApi.repairObservation(this.episodeId, body)
      this.setData({ selectedRepairTarget: null, repairValue: '' })
      await this.load()
      wx.showToast({ title: '已保存新修订，旧结论已撤回', icon: 'success' })
    } catch (error) {
      this.setData({ error: error.message || '观察修复未能完成' })
      if (error.statusCode === 409) await this.load()
    } finally {
      this.setData({ busy: false })
    }
  },

  async previewRereview() {
    if (!this.data.rereviewContext || this.data.busy) return
    this.setData({ busy: true, error: '' })
    try {
      const preview = await acquisitionApi.previewRereview(this.episodeId, this.data.rereviewContext)
      this.setData({ rereviewPreview: Object.assign({}, preview, {
        conclusionLabel: journey.conclusionLabel(preview.conclusion),
        executionLabel: labelBinary(preview.execution_label, '执行记录达到门槛', '执行记录未达到门槛'),
        supportLabel: labelBinary(preview.support_label, '观察到预设方向的支持', '未观察到预设方向的支持'),
        availabilityLabel: labelBinary(preview.availability_label, '观察资料达到门槛', '观察资料不足')
      }) })
    } catch (error) {
      this.setData({ error: error.message || '再复查预览暂时无法生成' })
      if (error.statusCode === 409) await this.load()
    } finally {
      this.setData({ busy: false })
    }
  },

  async confirmRereview() {
    const context = this.data.rereviewContext
    if (!context || !this.data.rereviewPreview || this.data.busy) return
    this.setData({ busy: true, error: '' })
    try {
      const proposal = await acquisitionApi.proposeRereview(this.episodeId, context)
      const result = await journey.confirmProposal(
        proposal,
        '确认生成新的复查版本',
        '旧复查记录继续保留；系统将按原冻结协议重算，证据不足时不会强行生成支持结论。'
      )
      if (result) await this.load()
    } catch (error) {
      this.setData({ error: error.message || '再复查确认暂时无法执行' })
      if (error.statusCode === 409) await this.load()
    } finally {
      this.setData({ busy: false })
    }
  },

  goHistory() { wx.navigateTo({ url: '/pages/policy/history/index' }) },
  retry() { this.load() }
})
