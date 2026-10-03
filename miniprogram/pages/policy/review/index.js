const api = require('../../../utils/request')
const journey = require('../../../utils/policyJourney')

function labelBinary(value, positive, negative) {
  if (value === 1) return positive
  if (value === 0) return negative
  return '证据不足，无法判断'
}

Page({
  data: { loading: true, busy: false, error: '', episode: null, verdict: null, explanation: null, canFinish: false, revoked: false },
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
        revoked: !!(explanation.conclusion && (!explanation.conclusion.valid || explanation.conclusion.stale))
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

  goHistory() { wx.navigateTo({ url: '/pages/policy/history/index' }) },
  retry() { this.load() }
})
