const api = require('../../../utils/request')

function decorate(plugin) {
  const selected = new Set(plugin.config && plugin.config.data_scopes || [])
  return Object.assign({}, plugin, {
    scope_options: (plugin.data_scopes || []).map(scope => Object.assign({}, scope, { selected: selected.has(scope.id) }))
  })
}

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`
  }
  return JSON.stringify(value)
}

async function idempotentWrite(pluginId, operation, identity, payload, send) {
  const userId = wx.getStorageSync('healthmate_user_id') || 'anonymous'
  const storageKey = `healthmate_harness_pending:${userId}:${pluginId}:${operation}:${identity}`
  const fingerprint = canonical(payload)
  let pending = wx.getStorageSync(storageKey)
  if (!pending || pending.fingerprint !== fingerprint) {
    pending = { key: api.idempotencyKey(`harness-${operation}`), fingerprint }
    wx.setStorageSync(storageKey, pending)
  }
  try {
    const result = await send({ 'Idempotency-Key': pending.key })
    wx.removeStorageSync(storageKey)
    return result
  } catch (error) {
    if (error.statusCode >= 400 && error.statusCode < 500 && error.code !== 'IDEMPOTENCY_IN_PROGRESS') {
      wx.removeStorageSync(storageKey)
    }
    throw error
  }
}

Page({
  data: { loading: true, error: '', plugins: [], busy: '' },

  onShow() { this.load() },

  async load() {
    this.setData({ loading: true, error: '' })
    try {
      await api.ensureToken()
      const result = await api.get('/harness/plugin-catalog', { allowCache: false })
      const plugins = (result && result.plugins || []).map(item => decorate(Object.assign({}, item, {
        expanded: false,
        previewed: false,
        config_dirty: !!item.manifest_reconsent_required,
        preview: null,
        audit: null
      })))
      this.setData({ loading: false, plugins })
    } catch (error) {
      this.setData({ loading: false, error: error.message || '健康能力暂时无法读取' })
    }
  },

  retry() { this.load() },

  _index(pluginId) { return this.data.plugins.findIndex(item => item.plugin_id === pluginId) },

  _replacePlugin(pluginId, patch) {
    const index = this._index(pluginId)
    if (index < 0) return
    this.setData({ [`plugins[${index}]`]: decorate(Object.assign({}, this.data.plugins[index], patch)) })
  },

  async openConfig(e) {
    const pluginId = e.currentTarget.dataset.plugin
    const index = this._index(pluginId)
    if (index < 0) return
    const plugin = this.data.plugins[index]
    if (plugin.enabled && !plugin.expanded) {
      const confirmed = await new Promise(resolve => wx.showModal({
        title: '先暂停再调整',
        content: '调整授权范围或回答方式前，会先暂停这项能力；尚未确认的行动申请会失效。更改后需重新预览并确认开启。',
        confirmText: '暂停并调整', cancelText: '暂不调整',
        success: result => resolve(!!result.confirm), fail: () => resolve(false)
      }))
      if (!confirmed) return
      try {
        const payload = { config_version: plugin.config_version }
        const result = await idempotentWrite(pluginId, 'pause', `${plugin.installation_id}:${plugin.config_version}`, payload, headers =>
          api.post(`/harness/installations/${plugin.installation_id}/pause`, payload, headers)
        )
        this._replacePlugin(pluginId, Object.assign({}, result.installation, { previewed: false, config_dirty: false, preview: null }))
      } catch (error) {
        wx.showToast({ title: error.message || '暂停失败', icon: 'none' })
        return
      }
    }
    this.setData({ [`plugins[${index}].expanded`]: !this.data.plugins[index].expanded })
  },

  changeScope(e) {
    const pluginId = e.currentTarget.dataset.plugin
    const scopeId = e.currentTarget.dataset.scope
    const plugin = this.data.plugins[this._index(pluginId)]
    if (!plugin) return
    if (plugin.enabled) { wx.showToast({ title: '请先暂停能力，再调整授权范围', icon: 'none' }); return }
    const scopes = new Set(plugin.config.data_scopes || [])
    if (scopes.has(scopeId)) scopes.delete(scopeId)
    else scopes.add(scopeId)
    this._replacePlugin(pluginId, {
      config: Object.assign({}, plugin.config, { data_scopes: Array.from(scopes) }),
      config_dirty: true,
      previewed: false,
      preview: null
    })
  },

  changeChoice(e) {
    const pluginId = e.currentTarget.dataset.plugin
    const field = e.currentTarget.dataset.field
    const value = e.currentTarget.dataset.value
    const plugin = this.data.plugins[this._index(pluginId)]
    if (!plugin || !field) return
    if (plugin.enabled) { wx.showToast({ title: '请先暂停能力，再调整回答方式', icon: 'none' }); return }
    this._replacePlugin(pluginId, {
      config: Object.assign({}, plugin.config, { [field]: value }),
      config_dirty: true,
      previewed: false,
      preview: null
    })
  },

  changeProposal(e) {
    const pluginId = e.currentTarget.dataset.plugin
    const plugin = this.data.plugins[this._index(pluginId)]
    if (!plugin) return
    if (plugin.enabled) { wx.showToast({ title: '请先暂停能力，再更改行动授权', icon: 'none' }); return }
    this._replacePlugin(pluginId, {
      config: Object.assign({}, plugin.config, { allow_action_proposals: !plugin.config.allow_action_proposals }),
      config_dirty: true,
      previewed: false,
      preview: null
    })
  },

  async _saveConfig(pluginId) {
    let plugin = this.data.plugins[this._index(pluginId)]
    if (!plugin) throw new Error('能力信息已更新，请刷新后重试')
    if (plugin.enabled && plugin.config_dirty) throw new Error('请先暂停能力，再保存新配置')
    if (!plugin.installation_id) {
      const payload = {
        plugin_id: plugin.plugin_id,
        config: plugin.config
      }
      const created = await idempotentWrite(pluginId, 'create', 'new', payload, headers =>
        api.post('/harness/installations', payload, headers)
      )
      plugin = created.installation
      this._replacePlugin(pluginId, Object.assign({}, plugin, { expanded: true, previewed: false, config_dirty: false, preview: null }))
      return plugin
    }
    if (!plugin.config_dirty) return plugin
    const payload = {
      config_version: plugin.config_version,
      config: plugin.config
    }
    const saved = await idempotentWrite(pluginId, 'config', `${plugin.installation_id}:${plugin.config_version}`, payload, headers =>
      api.patch(`/harness/installations/${plugin.installation_id}`, payload, headers)
    )
    plugin = saved.installation
    this._replacePlugin(pluginId, Object.assign({}, plugin, { expanded: true, previewed: false, config_dirty: false, preview: null }))
    return plugin
  },

  async preview(e) {
    const pluginId = e.currentTarget.dataset.plugin
    if (!pluginId || this.data.busy) return
    this.setData({ busy: `${pluginId}:preview` })
    try {
      const installation = await this._saveConfig(pluginId)
      const payload = { config_version: installation.config_version }
      const result = await idempotentWrite(pluginId, 'preview', `${installation.installation_id}:${installation.config_version}`, payload, headers =>
        api.post(`/harness/installations/${installation.installation_id}/preview`, payload, headers)
      )
      this._replacePlugin(pluginId, { previewed: true, preview: result, busy: '' })
    } catch (error) {
      this.setData({ busy: '' })
      wx.showToast({ title: error.message || '暂时无法预览', icon: 'none' })
    }
  },

  async toggle(e) {
    const pluginId = e.currentTarget.dataset.plugin
    let plugin = this.data.plugins[this._index(pluginId)]
    if (!plugin || this.data.busy) return
    if (plugin.enabled) {
      const confirmed = await new Promise(resolve => wx.showModal({
        title: '暂停这项能力？',
        content: '暂停后不再开展新的读取和建议，未确认的行动申请会失效；已有历史仍可查看，进行中的周期仍可记录或停止。',
        confirmText: '暂停', cancelText: '继续开启',
        success: result => resolve(!!result.confirm), fail: () => resolve(false)
      }))
      if (!confirmed) return
      this.setData({ busy: pluginId })
      try {
        const payload = { config_version: plugin.config_version }
        const result = await idempotentWrite(pluginId, 'pause', `${plugin.installation_id}:${plugin.config_version}`, payload, headers =>
          api.post(`/harness/installations/${plugin.installation_id}/pause`, payload, headers)
        )
        this._replacePlugin(pluginId, Object.assign({}, result.installation, { expanded: plugin.expanded, previewed: false, config_dirty: false }))
      } catch (error) {
        this.setData({ busy: '' })
        wx.showToast({ title: error.message || '暂停失败', icon: 'none' })
      }
      this.setData({ busy: '' })
      return
    }

    if (!plugin.previewed) {
      wx.showToast({ title: '请先查看配置预览', icon: 'none' })
      this._replacePlugin(pluginId, { expanded: true })
      return
    }
    const sourceLabels = plugin.data_scopes.filter(item => (plugin.config.data_scopes || []).includes(item.id)).map(item => item.label)
    const goal = plugin.config_schema.goals.find(item => item.id === plugin.config.goal)
    const style = plugin.config_schema.output_styles.find(item => item.id === plugin.config.output_style)
    const trigger = plugin.config_schema.triggers.find(item => item.id === plugin.config.trigger)
    const notification = plugin.config_schema.notification_frequencies.find(item => item.id === plugin.config.notification_frequency)
    const proposalNote = plugin.config.allow_action_proposals ? '它也可以提出待你确认的行动申请。' : '它不会提出行动申请。'
    const confirmed = await new Promise(resolve => wx.showModal({
      title: '确认开启这项能力',
      content: `优先目标：${goal ? goal.label : '按需帮助'}；触发：${trigger ? trigger.label : '你提出相关问题时'}；回答方式：${style ? style.label : '清晰自然'}；提醒：${notification ? notification.label : '不额外提示'}。授权使用：${sourceLabels.join('、') || '不读取个人数据'}。${proposalNote}你可随时暂停或删除配置。`,
      confirmText: '确认开启', cancelText: '再看看',
      success: result => resolve(!!result.confirm), fail: () => resolve(false)
    }))
    if (!confirmed) return
    this.setData({ busy: pluginId })
    try {
      plugin = await this._saveConfig(pluginId)
      // Saving a changed config invalidates the earlier preview; require a new
      // preview for exactly the version being authorized.
      if (!plugin.previewed) {
        this.setData({ busy: '' })
        wx.showToast({ title: '配置有变化，请重新预览后开启', icon: 'none' })
        return
      }
      const payload = { config_version: plugin.config_version }
      const result = await idempotentWrite(pluginId, 'resume', `${plugin.installation_id}:${plugin.config_version}`, payload, headers =>
        api.post(`/harness/installations/${plugin.installation_id}/resume`, payload, headers)
      )
      this._replacePlugin(pluginId, Object.assign({}, result.installation, { expanded: plugin.expanded, previewed: false, config_dirty: false }))
      wx.showToast({ title: '已按此配置开启', icon: 'success' })
    } catch (error) {
      wx.showToast({ title: error.message || '开启失败', icon: 'none' })
    }
    this.setData({ busy: '' })
  },

  async showAudit(e) {
    const plugin = this.data.plugins[this._index(e.currentTarget.dataset.plugin)]
    if (!plugin || !plugin.installation_id) {
      wx.showToast({ title: '开启后会显示这项能力的使用记录', icon: 'none' })
      return
    }
    try {
      const result = await api.get(`/harness/installations/${plugin.installation_id}/audit`, { allowCache: false })
      const toolLabels = {
        'health.context.read': '整理健康状态', 'health.state.read': '读取状态摘要',
        'health.knowledge.search': '查找审核健康资料', 'health.resources.search': '查找审核教学资源',
        'policy.candidates.preview': '预览个人策略选项', 'policy.episode.read': '查看个人周期',
        'motion.analysis.read': '查看动作分析', 'motion.timeline.read': '查看动作时间轴',
        'plan.simulate': '模拟计划', 'decision.contract': '比较行动选项'
      }
      const eventLabels = {
        configured: '配置已保存', previewed: '已完成合成预览', resumed: '已明确授权开启',
        paused: '能力已暂停', deleted: '能力配置已删除', tool_access: '读取了已授权数据',
        tool_blocked: '授权范围拦截了一次读取',
        api_access: '访问了已授权的数据', api_blocked: '当前配置拦截了一项能力操作',
        context_access: '已授权数据用于本次助手回答',
        maintenance: '完成了历史记录一致性维护'
      }
      const events = (result.events || []).map(item => {
        const detail = item.detail || {}
        let summary = ''
        if (detail.tool) summary = toolLabels[detail.tool] || '使用了已审核功能'
        if (item.event === 'context_access') summary = '本次回答读取了这项能力已获准的数据范围'
        if (detail.status && detail.status !== 'ok' && detail.status !== 'approval_required') summary = '这次读取没有完成，可稍后重试'
        return Object.assign({}, item, {
          label: eventLabels[item.event] || '能力活动',
          summary,
          at: String(item.at || '').replace('T', ' ').replace('Z', '').slice(0, 16)
        })
      })
      this._replacePlugin(plugin.plugin_id, { audit: events, show_audit: !plugin.show_audit })
    } catch (error) {
      wx.showToast({ title: error.message || '使用记录暂时无法读取', icon: 'none' })
    }
  },

  async remove(e) {
    const plugin = this.data.plugins[this._index(e.currentTarget.dataset.plugin)]
    if (!plugin || this.data.busy) return
    if (!plugin.installation_id) {
      this._replacePlugin(plugin.plugin_id, { expanded: false, preview: null, previewed: false })
      return
    }
    const confirmed = await new Promise(resolve => wx.showModal({
      title: '删除能力配置？',
      content: '这只会删除能力的配置与授权，不会删除你的健康记录、动作分析或计划。',
      confirmText: '删除配置', cancelText: '保留',
      success: result => resolve(!!result.confirm), fail: () => resolve(false)
    }))
    if (!confirmed) return
    this.setData({ busy: plugin.plugin_id })
    try {
      const payload = { config_version: plugin.config_version }
      await idempotentWrite(plugin.plugin_id, 'delete', `${plugin.installation_id}:${plugin.config_version}`, payload, headers =>
        api.del(`/harness/installations/${plugin.installation_id}?config_version=${plugin.config_version}`, {}, headers)
      )
      const catalog = await api.get('/harness/plugin-catalog', { allowCache: false })
      const reset = (catalog.plugins || []).map(item => decorate(Object.assign({}, item, { expanded: false, previewed: false, config_dirty: false, preview: null, audit: null })))
      this.setData({ plugins: reset, busy: '' })
      wx.showToast({ title: '配置已删除', icon: 'success' })
    } catch (error) {
      this.setData({ busy: '' })
      wx.showToast({ title: error.message || '删除失败', icon: 'none' })
    }
  }
})
