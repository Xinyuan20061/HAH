const api = require('../../../utils/request')
const journey = require('../../../utils/policyJourney')

function draftKey() {
  return `healthmate_policy_draft:${wx.getStorageSync('healthmate_user_id') || 'anonymous'}`
}

function compileKey() { return `${draftKey()}:pending_compile` }

function isCapabilityError(error) {
  return ['PLUGIN_DISABLED', 'PLUGIN_SCOPE_NOT_GRANTED', 'PLUGIN_ACTIONS_DISABLED', 'PLUGIN_UNAVAILABLE'].includes(error && error.code)
}

Page({
  data: {
    loading: true,
    busy: false,
    error: '',
    capabilityBlocked: false,
    candidate: null,
    choices: [
      { minutes: 10, label: '10 分钟' },
      { minutes: 15, label: '15 分钟' },
      { minutes: 20, label: '20 分钟' }
    ],
    selectedMinutes: 15,
    compiled: null,
    proposal: null
  },

  async onLoad() {
    this.setData({ loading: true, error: '', capabilityBlocked: false })
    try {
      await api.ensureToken()
      const [candidate, cachedDraft, pendingCompile] = await Promise.all([
        api.get('/policy/candidates', { allowCache: false }),
        Promise.resolve(wx.getStorageSync(draftKey())),
        Promise.resolve(wx.getStorageSync(compileKey()))
      ])
      const validDraft = cachedDraft && Date.now() - cachedDraft.savedAt < 24 * 60 * 60 * 1000
      this.setData({
        loading: false,
        candidate,
        compiled: validDraft ? cachedDraft.compiled : null,
        proposal: validDraft ? cachedDraft.proposal || null : null,
        selectedMinutes: validDraft ? cachedDraft.minutes : pendingCompile && pendingCompile.minutes || 15
      })
    } catch (error) {
      this.setData({ loading: false, error: error.message || '协议选项暂时无法读取' })
    }
  },

  chooseMinutes(e) {
    if (this.data.busy || this.data.compiled) return
    const minutes = Number(e.currentTarget.dataset.minutes)
    const pending = wx.getStorageSync(compileKey())
    if (pending && pending.minutes !== minutes) wx.removeStorageSync(compileKey())
    this.setData({ selectedMinutes: minutes, error: '' })
  },

  async clearStaleDraft(message) {
    wx.removeStorageSync(draftKey())
    wx.removeStorageSync(compileKey())
    this.setData({ compiled: null, proposal: null, busy: false, error: message, capabilityBlocked: false })
    try {
      const candidate = await api.get('/policy/candidates', { allowCache: false })
      this.setData({ candidate })
    } catch (_) {}
  },

  async compile() {
    const candidate = this.data.candidate
    if (!candidate || !candidate.can_compile || !candidate.can_propose || this.data.busy) return
    this.setData({ busy: true, error: '' })
    try {
      const minutes = this.data.selectedMinutes
      const key = compileKey()
      let pending = wx.getStorageSync(key)
      if (!pending || pending.minutes !== minutes) {
        pending = { key: api.idempotencyKey('policy-compile'), minutes }
        wx.setStorageSync(key, pending)
      }
      const compiled = await api.post('/policy/compile', {
        template_id: 'session_duration',
        template_version: '1.0.0',
        parameters: {
          variant: `session_${minutes}m`,
          time_budget: 'unknown',
          recovery: 'unknown',
          schedule: 'unknown'
        },
        goal_key: 'make_plan_sustainable'
      }, { 'Idempotency-Key': pending.key })
      wx.removeStorageSync(key)
      if (!compiled.compiled) {
        this.setData({ busy: false, error: '当前状态还需要补充或复核；此协议尚不能开始。' })
        return
      }
      wx.setStorageSync(draftKey(), { compiled, minutes, savedAt: Date.now() })
      this.setData({ compiled, busy: false })
    } catch (error) {
      if (error.statusCode >= 400 && error.statusCode < 500 && error.code !== 'IDEMPOTENCY_IN_PROGRESS') {
        wx.removeStorageSync(compileKey())
      }
      this.setData({ busy: false, error: error.message || '协议生成失败，请检查能力授权后重试', capabilityBlocked: isCapabilityError(error) })
    }
  },

  async createProposal() {
    if (!this.data.compiled || this.data.busy) return
    this.setData({ busy: true, error: '' })
    try {
      const proposal = await api.post(`/policy/units/${this.data.compiled.strategy_unit_id}/proposal`, {})
      wx.setStorageSync(draftKey(), {
        compiled: this.data.compiled,
        minutes: this.data.selectedMinutes,
        proposal,
        savedAt: Date.now()
      })
      this.setData({ proposal, busy: false })
    } catch (error) {
      if (error.code === 'POLICY_STATE_CHANGED' || error.code === 'POLICY_CAPABILITY_CHANGED' || error.code === 'POLICY_PROTOCOL_CHANGED') {
        await this.clearStaleDraft('健康记录、能力授权或协议已变化；旧协议已作废，请刷新条件后重新生成并确认。')
        return
      }
      this.setData({ busy: false, error: error.message || '无法创建确认请求；请检查个人策略的行动授权', capabilityBlocked: isCapabilityError(error) })
    }
  },

  async confirmStart() {
    const proposal = this.data.proposal
    if (!proposal || this.data.busy) return
    const minutes = this.data.selectedMinutes
    let confirmation
    try {
      confirmation = await journey.confirmProposal(
        proposal,
        '确认开始个人周期',
        `本周期持续 7 天，按每次 ${minutes} 分钟训练执行，并自行记录训练负担（0–10）。至少需要 5 条基线和 5 条周期内观察。结果只描述本周期记录，不证明因果或健康效果。`
      )
    } catch (error) {
      if (error.code === 'ACTION_PROPOSAL_EXPIRED' || error.code === 'ACTION_PROPOSAL_NOT_PENDING') {
        const draft = wx.getStorageSync(draftKey())
        if (draft) {
          delete draft.proposal
          wx.setStorageSync(draftKey(), draft)
        }
        this.setData({ proposal: null, error: '这条确认请求已过期或已处理；请重新生成请求后再确认。', capabilityBlocked: false })
      } else if (error.code === 'POLICY_STATE_CHANGED' || error.code === 'POLICY_CAPABILITY_CHANGED' || error.code === 'POLICY_PROTOCOL_CHANGED') {
        await this.clearStaleDraft('健康记录、能力授权或协议已变化；旧协议已作废，请刷新条件后重新生成并确认。')
      } else {
        this.setData({ error: error.message || '确认请求暂时无法执行', capabilityBlocked: isCapabilityError(error) })
      }
      return
    }
    if (!confirmation) return
    wx.removeStorageSync(draftKey())
    wx.removeStorageSync(compileKey())
    this.setData({ proposal: null })
    this.setData({ busy: true, error: '' })
    try {
      const current = await api.get('/policy/episodes/current', { allowCache: false })
      const episode = current && current.episode
      if (!episode) throw new Error('周期申请已处理，但暂时读不到当前周期；请返回个人策略页刷新。')
      this.setData({ busy: false })
      wx.redirectTo({ url: `/pages/policy/episode/index?id=${episode.episode_id}` })
    } catch (error) {
      this.setData({ busy: false, error: error.message || '周期未能开始；可重新检查申请状态' })
    }
  },

  retry() { this.onLoad() },
  openCapabilities() { wx.navigateTo({ url: '/pages/settings/capabilities/index' }) }
})
