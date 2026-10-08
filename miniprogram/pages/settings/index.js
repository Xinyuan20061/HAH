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
    busy: false,
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
    const tabBar = typeof this.getTabBar === 'function' && this.getTabBar()
    if (tabBar && typeof tabBar.syncCompanion === 'function') tabBar.syncCompanion(companion)
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
  dietRecords() { wx.navigateTo({ url: '/pages/records/diet' }) },
  async linkAndroid() {
    if (this.data.busy) return
    this.setData({ busy: true })
    try {
      await api.ensureToken()
      const result = await api.post('/auth/link/start', {})
      let copied = false
      try {
        await new Promise((resolve, reject) => wx.setClipboardData({
          data: result.link_code,
          success: resolve,
          fail: reject
        }))
        copied = true
      } catch {
        // Keep the one-time code visible so the user can still enter it manually.
      }
      await new Promise(resolve => wx.showModal({
        title: copied ? 'Android 账号关联码已复制' : 'Android 账号关联码已生成',
        content: `${result.link_code}\n\n${copied ? '已复制到剪贴板。' : '复制失败，请手动输入上方代码。'}请在 Android 客户端的“关联微信小程序账号”页面输入。关联码 10 分钟内有效且只能使用一次。`,
        showCancel: false,
        confirmText: '知道了',
        complete: resolve
      }))
    } catch (error) {
      await new Promise(resolve => wx.showModal({
        title: '关联码生成失败',
        content: error.message || '请确认小程序已登录并重试。',
        showCancel: false,
        complete: resolve
      }))
    } finally {
      this.setData({ busy: false })
    }
  }
})
