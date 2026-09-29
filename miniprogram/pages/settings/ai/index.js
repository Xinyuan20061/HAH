const api = require('../../../utils/request')

const DEFAULT_FORM = {
  enabled: false,
  base_url: 'https://api.deepseek.com',
  model: 'deepseek-chat',
  api_key: '',
  voice_enabled: false,
  voice_base_url: '',
  voice_stt_model: 'whisper-1',
  voice_tts_model: 'tts-1',
  voice_name: 'alloy',
  voice_api_key: ''
}

Page({
  data: {
    activeSection: 'text',
    textActive: true,
    voiceActive: false,
    form: { ...DEFAULT_FORM },
    hasKey: false,
    keyHint: '',
    hasVoiceKey: false,
    voiceKeyHint: '',
    systemVoiceConfigured: false,
    testing: false,
    testingVoice: false,
    saving: false,
    checkingBackend: false,
    backendExpanded: false,
    backendOnline: null,
    backendMessage: '尚未检测',
    backendUrl: '',
    devBackendUrl: '',
    models: ['deepseek-chat', 'deepseek-reasoner'],
    voices: ['alloy', 'nova', 'shimmer'],
    cloudMode: api.isCloud()
  },

  async onLoad(options = {}) {
    const url = api.getBaseUrl()
    this.setData({
      activeSection: options.section === 'voice' ? 'voice' : 'text',
      textActive: options.section !== 'voice',
      voiceActive: options.section === 'voice',
      backendUrl: url,
      devBackendUrl: url
    })
    await this.checkBackend(false)
    await this.load()
  },

  async ensureReady() { await api.ensureToken() },

  async load() {
    try {
      await this.ensureReady()
      const r = await api.get('/users/me/ai-config')
      this.setData({
        form: {
          enabled: !!r.enabled,
          base_url: r.base_url || DEFAULT_FORM.base_url,
          model: r.model || DEFAULT_FORM.model,
          api_key: '',
          voice_enabled: !!r.voice_enabled,
          voice_base_url: r.voice_base_url || '',
          voice_stt_model: r.voice_stt_model || DEFAULT_FORM.voice_stt_model,
          voice_tts_model: r.voice_tts_model || DEFAULT_FORM.voice_tts_model,
          voice_name: r.voice_name || DEFAULT_FORM.voice_name,
          voice_api_key: ''
        },
        hasKey: !!r.has_api_key,
        keyHint: r.api_key_hint || '',
        hasVoiceKey: !!r.has_voice_api_key,
        voiceKeyHint: r.voice_api_key_hint || '',
        systemVoiceConfigured: !!r.system_voice_configured
      })
    } catch (error) {
      this.setData({ backendOnline: false, backendMessage: error.message || '读取配置失败' })
    }
  },

  selectSection(e) {
    const activeSection = e.currentTarget.dataset.section === 'voice' ? 'voice' : 'text'
    this.setData({ activeSection, textActive: activeSection === 'text', voiceActive: activeSection === 'voice' })
  },
  toggleText(e) { this.setData({ 'form.enabled': e.detail.value }) },
  toggleVoice(e) { this.setData({ 'form.voice_enabled': e.detail.value }) },
  inp(e) { this.setData({ [`form.${e.currentTarget.dataset.k}`]: e.detail.value }) },
  useModel(e) { this.setData({ 'form.model': e.currentTarget.dataset.model }) },
  useVoice(e) { this.setData({ 'form.voice_name': e.currentTarget.dataset.voice }) },
  toggleBackend() { this.setData({ backendExpanded: !this.data.backendExpanded }) },
  onBackendInput(e) { this.setData({ devBackendUrl: e.detail.value }) },

  async saveBackendUrl() {
    if (this.data.cloudMode) return wx.showToast({ title: '云托管模式无需修改地址', icon: 'none' })
    try {
      const next = api.setBaseUrl(this.data.devBackendUrl)
      wx.removeStorageSync('token')
      this.setData({ backendUrl: next, backendOnline: null, backendMessage: '正在检测' })
      await this.checkBackend(true)
      await this.load()
    } catch (error) {
      wx.showToast({ title: error.message || '地址无效', icon: 'none' })
    }
  },

  async resetBackendUrl() {
    if (this.data.cloudMode) return wx.showToast({ title: '云托管模式无需修改地址', icon: 'none' })
    api.clearBaseUrl()
    wx.removeStorageSync('token')
    const next = api.getBaseUrl()
    this.setData({ backendUrl: next, devBackendUrl: next, backendOnline: null })
    await this.checkBackend(true)
    await this.load()
  },

  async checkBackend(showToast = true) {
    if (this.data.checkingBackend) return false
    this.setData({ checkingBackend: true })
    try {
      const result = await api.health()
      this.setData({ backendOnline: true, backendMessage: result.service || 'HealthMate API' })
      if (showToast) wx.showToast({ title: '连接正常' })
      return true
    } catch (error) {
      this.setData({ backendOnline: false, backendMessage: error.message || '后端不可达' })
      if (showToast) wx.showModal({ title: '连接失败', content: error.message || '请确认后端服务已启动。', showCancel: false })
      return false
    } finally {
      this.setData({ checkingBackend: false })
    }
  },

  async save() {
    if (this.data.saving) return
    const form = { ...this.data.form }
    if (form.enabled && (!form.base_url || !form.model)) return wx.showToast({ title: '请补全文字模型配置', icon: 'none' })
    if (form.enabled && !this.data.hasKey && !String(form.api_key || '').trim()) return wx.showToast({ title: '请填写文字 API Key', icon: 'none' })
    const personalVoice = this.data.hasVoiceKey || String(form.voice_api_key || '').trim()
    if (form.voice_enabled && personalVoice && !form.voice_base_url) return wx.showToast({ title: '请填写语音 Base URL', icon: 'none' })
    if (form.voice_enabled && !personalVoice && !this.data.systemVoiceConfigured) return wx.showToast({ title: '请填写语音 API Key', icon: 'none' })
    this.setData({ saving: true })
    try {
      await this.ensureReady()
      const result = await api.put('/users/me/ai-config', form)
      this.setData({
        hasKey: !!result.has_api_key,
        keyHint: result.api_key_hint || '',
        hasVoiceKey: !!result.has_voice_api_key,
        voiceKeyHint: result.voice_api_key_hint || '',
        systemVoiceConfigured: !!result.system_voice_configured,
        'form.api_key': '',
        'form.voice_api_key': '',
        'form.enabled': !!result.enabled,
        'form.voice_enabled': !!result.voice_enabled
      })
      wx.showToast({ title: '配置已保存' })
    } catch (error) {
      wx.showModal({ title: '保存失败', content: error.message || '请检查服务地址与登录状态。', showCancel: false })
    } finally {
      this.setData({ saving: false })
    }
  },

  async testText() {
    if (this.data.testing) return
    this.setData({ testing: true })
    try {
      await this.ensureReady()
      const form = this.data.form
      const result = await api.post('/users/me/ai-config/test', { base_url: form.base_url, model: form.model, api_key: form.api_key })
      wx.showModal({ title: '文字模型可用', content: `服务：${result.model}`, showCancel: false })
    } catch (error) {
      wx.showModal({ title: '连接失败', content: error.message || '请检查文字模型配置。', showCancel: false })
    } finally {
      this.setData({ testing: false })
    }
  },

  async testVoice() {
    if (this.data.testingVoice) return
    this.setData({ testingVoice: true })
    try {
      await this.ensureReady()
      const form = this.data.form
      await api.postLong('/users/me/ai-config/voice-test', {
        base_url: form.voice_base_url,
        tts_model: form.voice_tts_model,
        voice_name: form.voice_name,
        api_key: form.voice_api_key
      })
      wx.showModal({ title: '语音服务可用', content: '合成测试已通过。', showCancel: false })
    } catch (error) {
      wx.showModal({ title: '语音连接失败', content: error.message || '请检查语音 API 配置。', showCancel: false })
    } finally {
      this.setData({ testingVoice: false })
    }
  },

  reset() {
    wx.showModal({
      title: '恢复系统模式？',
      content: '个人文字与语音 API 配置将被移除。',
      success: async result => {
        if (!result.confirm) return
        try {
          await this.ensureReady()
          await api.del('/users/me/ai-config')
          this.setData({ form: { ...DEFAULT_FORM }, hasKey: false, keyHint: '', hasVoiceKey: false, voiceKeyHint: '' })
          await this.load()
          wx.showToast({ title: '已恢复系统模式' })
        } catch (error) {
          wx.showModal({ title: '操作失败', content: error.message || '请检查后端连接。', showCancel: false })
        }
      }
    })
  }
})
