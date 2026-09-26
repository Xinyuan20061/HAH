const api = require('../../../utils/request')

Page({
  data: {
    form: { enabled: false, base_url: 'https://api.deepseek.com', model: 'deepseek-chat', api_key: '' },
    hasKey: false,
    keyHint: '',
    testing: false,
    saving: false,
    checkingBackend: false,
    backendOnline: null,
    backendMessage: '尚未检测',
    backendUrl: '',
    devBackendUrl: '',
    models: ['deepseek-chat', 'deepseek-reasoner'],
    cloudMode: api.isCloud()
  },
  async onLoad() {
    const url = api.getBaseUrl()
    this.setData({ backendUrl: url, devBackendUrl: url })
    await this.checkBackend(false)
    await this.load()
  },
  async ensureReady() {
    await api.ensureToken()
  },
  async load() {
    try {
      await this.ensureReady()
      const r = await api.get('/users/me/ai-config')
      this.setData({
        form: {
          enabled: !!r.enabled,
          base_url: r.base_url || 'https://api.deepseek.com',
          model: r.model || 'deepseek-chat',
          api_key: ''
        },
        hasKey: !!r.has_api_key,
        keyHint: r.api_key_hint || ''
      })
    } catch (e) {
      this.setData({ backendOnline: false, backendMessage: e.message || '读取配置失败' })
    }
  },
  toggle(e) { this.setData({ 'form.enabled': e.detail.value }) },
  inp(e) { this.setData({ [`form.${e.currentTarget.dataset.k}`]: e.detail.value }) },
  useModel(e) { this.setData({ 'form.model': e.currentTarget.dataset.model }) },
  onBackendInput(e) { this.setData({ devBackendUrl: e.detail.value }) },
  async saveBackendUrl() {
    if (this.data.cloudMode) return wx.showToast({ title: '云托管模式无需修改地址', icon: 'none' })
    try {
      const next = api.setBaseUrl(this.data.devBackendUrl)
      wx.removeStorageSync('token')
      this.setData({ backendUrl: next, backendOnline: null, backendMessage: '地址已更新，正在检测…' })
      await this.checkBackend(true)
      await this.load()
    } catch (e) {
      wx.showToast({ title: e.message || '地址无效', icon: 'none' })
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
    if (this.data.checkingBackend) return
    this.setData({ checkingBackend: true })
    try {
      const r = await api.health()
      this.setData({ backendOnline: true, backendMessage: `已连接：${r.service || 'HealthMate API'}` })
      if (showToast) wx.showToast({ title: '后端连接正常' })
      return true
    } catch (e) {
      this.setData({ backendOnline: false, backendMessage: e.message || '后端不可达' })
      if (showToast) wx.showModal({ title: '后端未连接', content: e.message || '请确认 FastAPI 已启动。', showCancel: false })
      return false
    } finally {
      this.setData({ checkingBackend: false })
    }
  },
  async save() {
    if (this.data.saving) return
    let f = { ...this.data.form }
    if (!f.base_url || !f.model) return wx.showToast({ title: '请填写 Base URL 和服务 ID', icon: 'none' })
    if (!this.data.hasKey && !String(f.api_key || '').trim()) return wx.showToast({ title: '请填写 API Key', icon: 'none' })

    if (!this.data.hasKey && String(f.api_key || '').trim() && !f.enabled) f.enabled = true

    this.setData({ saving: true })
    try {
      await this.ensureReady()
      const r = await api.put('/users/me/ai-config', f)
      this.setData({
        hasKey: !!r.has_api_key,
        keyHint: r.api_key_hint || '',
        'form.api_key': '',
        'form.enabled': !!r.enabled,
        backendOnline: true
      })
      wx.showToast({ title: r.enabled ? '已保存并启用' : '配置已保存' })
    } catch (e) {
      wx.showModal({ title: '保存失败', content: e.message || '请检查后端连接与登录状态。', showCancel: false })
    } finally {
      this.setData({ saving: false })
    }
  },
  async test() {
    if (this.data.testing) return
    this.setData({ testing: true })
    try {
      await this.ensureReady()
      const f = this.data.form
      const r = await api.post('/users/me/ai-config/test', { base_url: f.base_url, model: f.model, api_key: f.api_key })
      wx.showModal({ title: '连接成功', content: `服务：${r.model}\n已成功收到响应。`, showCancel: false })
    } catch (e) {
      wx.showModal({ title: '连接失败', content: e.message || '请检查 API Key、Base URL、服务 ID 与网络。', showCancel: false })
    } finally {
      this.setData({ testing: false })
    }
  },
  async reset() {
    wx.showModal({
      title: '恢复默认模式？',
      content: '会删除你保存的 DeepSeek 配置。之后将使用系统默认配置；若服务端未配置，则使用演示模式。',
      success: async r => {
        if (!r.confirm) return
        try {
          await this.ensureReady()
          await api.del('/users/me/ai-config')
          this.setData({ hasKey: false, keyHint: '', form: { enabled: false, base_url: 'https://api.deepseek.com', model: 'deepseek-chat', api_key: '' } })
          wx.showToast({ title: '已恢复默认' })
        } catch (e) {
          wx.showModal({ title: '操作失败', content: e.message || '请检查后端连接。', showCancel: false })
        }
      }
    })
  }
})
