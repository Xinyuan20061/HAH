const api = require('../../utils/request')

function serviceStatus(ai) {
  const textReady = !!(ai && ai.enabled && ai.has_api_key)
  const personalVoice = !!(ai && ai.voice_enabled && ai.has_voice_api_key)
  const voiceReady = personalVoice || !!(ai && ai.system_voice_configured)
  return {
    textReady,
    voiceReady,
    textLabel: textReady ? '个人配置' : '系统模式',
    voiceLabel: personalVoice ? '个人配置' : voiceReady ? '系统可用' : '待配置'
  }
}

Page({
  data: {
    loading: true,
    error: '',
    ai: {},
    services: serviceStatus(),
    backendOnline: null,
    companion: 'xiaojian',
    isXiaojian: true,
    isXiaokang: false,
    voiceAutoplay: true
  },

  onShow() {
    const savedCompanion = wx.getStorageSync('healthmate_agent_id')
    const storedAutoplay = wx.getStorageSync('healthmate_voice_autoplay')
    this.setData({
      companion: savedCompanion === 'xiaokang' ? 'xiaokang' : 'xiaojian',
      isXiaojian: savedCompanion !== 'xiaokang',
      isXiaokang: savedCompanion === 'xiaokang',
      voiceAutoplay: storedAutoplay !== false
    })
    this.load()
  },

  async load() {
    this.setData({ loading: true, error: '' })
    try {
      await api.ensureToken()
      const [ai, health] = await Promise.all([
        api.get('/users/me/ai-config'),
        api.health().catch(() => null)
      ])
      this.setData({
        loading: false,
        ai,
        services: serviceStatus(ai),
        backendOnline: !!health
      })
    } catch (error) {
      this.setData({ loading: false, error: error.message || '设置暂时无法读取', backendOnline: false })
    }
  },

  retry() { this.load() },

  chooseCompanion(e) {
    const companion = e.currentTarget.dataset.id === 'xiaokang' ? 'xiaokang' : 'xiaojian'
    wx.setStorageSync('healthmate_agent_id', companion)
    this.setData({ companion, isXiaojian: companion === 'xiaojian', isXiaokang: companion === 'xiaokang' })
  },

  toggleAutoplay(e) {
    const value = !!e.detail.value
    wx.setStorageSync('healthmate_voice_autoplay', value)
    this.setData({ voiceAutoplay: value })
  },

  aiSettings() { wx.navigateTo({ url: '/pages/settings/ai/index' }) },
  voiceSettings() { wx.navigateTo({ url: '/pages/settings/ai/index?section=voice' }) },
  editProfile() { wx.navigateTo({ url: '/pages/profile/edit' }) },
  goals() { wx.navigateTo({ url: '/pages/goals/index' }) },
  privacy() { wx.navigateTo({ url: '/pages/settings/privacy/index' }) },
  evaluation() { wx.navigateTo({ url: '/pages/evaluation/index' }) },
  capabilities() { wx.navigateTo({ url: '/pages/settings/capabilities/index' }) },
  dietRecords() { wx.navigateTo({ url: '/pages/records/diet' }) }
})
