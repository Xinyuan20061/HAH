const api = require('../../../utils/request')
const cloudMedia = require('../../../utils/cloudMedia')

Page({
  data: { loading: true, error: '', policy: null, preview: null, exporting: false, deleting: false },
  onShow() { this.load() },
  async load() {
    this.setData({ loading: true, error: '' })
    try {
      await api.ensureToken()
      const [policy, preview] = await Promise.all([
        api.get('/privacy/policy', { allowCache: false }),
        api.get('/privacy/export/preview', { allowCache: false })
      ])
      this.setData({ policy, preview })
    } catch (e) { this.setData({ error: e.message || '隐私设置加载失败' }) }
    finally { this.setData({ loading: false }) }
  },
  retry() { this.load() },
  async exportData() {
    const ok = await new Promise(r => wx.showModal({
      title: '导出个人数据',
      content: '导出包包含健康档案、记录、计划、处理任务、评测和安全审计；不会包含访问密钥明文、媒体临时地址或媒体文件。继续吗？',
      confirmText: '确认导出', success: x => r(x.confirm)
    }))
    if (!ok) return
    this.setData({ exporting: true })
    try {
      const path = await api.downloadPost('/privacy/export', { confirmation: 'EXPORT' })
      wx.showModal({ title: '导出完成', content: `已生成结构化个人数据导出包：\n${path}`, showCancel: false })
    } catch (e) {
      wx.showModal({ title: '导出失败', content: e.message || '请稍后重试', showCancel: false })
    } finally { this.setData({ exporting: false }) }
  },
  deleteAccount() {
    wx.showModal({
      title: '删除账户与数据',
      content: '此操作会永久删除账户、健康记录、处理任务、识餐校正记录，并先删除微信云存储中的关联媒体。操作不可撤销。',
      confirmText: '继续', confirmColor: '#c0392b',
      success: r => { if (r.confirm) this.secondDelete() }
    })
  },
  secondDelete() {
    wx.showModal({
      title: '再次确认',
      content: '请确认：删除后当前登录凭证将失效；若云文件删除失败，系统会中止账户删除以便重试。',
      confirmText: '永久删除', confirmColor: '#c0392b',
      success: async r => {
        if (!r.confirm) return
        this.setData({ deleting: true })
        try {
          const media = await api.get('/privacy/cloud-media', { allowCache: false })
          const files = media.file_ids || []
          if (files.length) await cloudMedia.deleteFiles(files)
          await api.del('/privacy/account', { confirmation: 'DELETE MY DATA', cloud_files_deleted: files })
          wx.clearStorageSync()
          wx.showModal({
            title: '数据已删除', content: '账户数据库记录与关联云媒体删除完成。', showCancel: false,
            success: () => wx.reLaunch({ url: '/pages/home/index' })
          })
        } catch (e) {
          wx.showModal({ title: '删除未完成', content: e.message || '请稍后重试；系统不会把失败的云文件静默遗留。', showCancel: false })
        } finally { this.setData({ deleting: false }) }
      }
    })
  }
})
