const api = require('../../../utils/request')

Page({
  data: { loading: true, error: '', plugins: [], busy: '' },

  onShow() { this.load() },

  async load() {
    this.setData({ loading: true, error: '' })
    try {
      await api.ensureToken()
      const result = await api.get('/harness/plugins', { allowCache: false })
      this.setData({ loading: false, plugins: (result && result.plugins) || [] })
    } catch (error) {
      this.setData({ loading: false, error: error.message || '健康能力暂时无法读取' })
    }
  },

  retry() { this.load() },

  async toggle(e) {
    const plugin = e.currentTarget.dataset.plugin
    if (!plugin || this.data.busy) return
    const nextEnabled = !plugin.enabled
    if (!nextEnabled) {
      const confirmed = await new Promise(resolve => wx.showModal({
        title: '暂停这项能力？',
        content: '暂停后不会再生成新的相关建议；已有记录和周期仍可查看、停止或导出。',
        confirmText: '暂停',
        cancelText: '保留开启',
        success: result => resolve(!!result.confirm),
        fail: () => resolve(false)
      }))
      if (!confirmed) return
    }
    this.setData({ busy: plugin.plugin_id })
    try {
      const path = `/harness/plugins/${encodeURIComponent(plugin.plugin_id)}/${nextEnabled ? 'enable' : 'disable'}`
      const result = await api.post(path, nextEnabled ? { data_scope: plugin.data_needed || [] } : {})
      const updated = result && result.plugin
      const plugins = this.data.plugins.map(item => item.plugin_id === plugin.plugin_id && updated ? updated : item)
      this.setData({ plugins, busy: '' })
      wx.showToast({ title: nextEnabled ? '已开启' : '已暂停', icon: 'success' })
    } catch (error) {
      this.setData({ busy: '' })
      wx.showToast({ title: error.message || '操作失败', icon: 'none' })
    }
  }
})
