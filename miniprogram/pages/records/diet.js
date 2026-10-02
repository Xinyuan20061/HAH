const api = require('../../utils/request')
const { ensureLogin } = require('../../utils/auth')
const swipeDelete = require('../../utils/swipeDelete')

const MEALS = [
  { key: 'breakfast', label: '早餐' },
  { key: 'lunch', label: '午餐' },
  { key: 'dinner', label: '晚餐' },
  { key: 'snack', label: '加餐' },
  { key: 'other', label: '其他' }
]
const FILTER_KEYS = ['', ...MEALS.map(item => item.key)]
const FILTER_LABELS = ['全部餐次', ...MEALS.map(item => item.label)]
const MEAL_LABELS = MEALS.map(item => item.label)

// Provenance copy the user is allowed to see (spec §6.5). The server also sends
// `source_label`; this map keeps older rows readable.
const SOURCE_LABELS = {
  manual: '手动记录',
  ai_vision: '图片估算，已确认',
  ai_vision_corrected: '图片估算，已人工修改',
  legacy: '历史记录'
}

function today() {
  const now = new Date(Date.now() + 8 * 3600 * 1000)
  return now.toISOString().slice(0, 10)
}

function mealLabel(key) {
  const hit = MEALS.find(item => item.key === key)
  return hit ? hit.label : '其他'
}

Page({
  ...swipeDelete(),
  data: {
    loading: true,
    error: '',
    emptyHint: '这一天还没有饮食记录。',
    filterKeys: FILTER_KEYS,
    filterLabels: FILTER_LABELS,
    mealLabels: MEAL_LABELS,
    filterIndex: 0,
    mealLabel,
    date: today(),
    dateLabel: '今天',
    records: [],
    nextCursor: null,
    hasMore: false,
    loadingMore: false,
    activeId: null,
    editor: null,
    editing: false,
    saving: false,
    conflict: '',
    dragId: '',
    dragStyle: '',
    leavingId: ''
  },
  onLoad(query) {
    this._unloaded = false
    if (query && query.date) this.setData({ date: query.date })
    if (query && query.meal_type) {
      const index = FILTER_KEYS.indexOf(query.meal_type)
      if (index > 0) this.setData({ filterIndex: index })
    }
    this.setData({ dateLabel: this.dateLabel(this.data.date) })
    this.load()
  },
  onShow() { if (this._loaded) this.load() },
  onUnload() { this._unloaded = true },
  dateLabel(value) {
    if (value === today()) return '今天'
    return value
  },
  async load() {
    this._loaded = true
    this._unloaded = false
    this.setData({ loading: true, error: '', records: [], nextCursor: null, hasMore: false })
    try {
      await ensureLogin()
      const body = await this.fetchPage(null)
      if (this._unloaded) return
      this.setData({ loading: false, records: body.items, nextCursor: body.next_cursor, hasMore: body.has_more })
    } catch (e) {
      if (this._unloaded) return
      this.setData({ loading: false, error: e.message || '饮食记录暂时无法加载' })
    }
  },
  async fetchPage(cursor) {
    const params = [`date_from=${this.data.date}`, `date_to=${this.data.date}`, 'limit=20']
    const meal = FILTER_KEYS[this.data.filterIndex]
    if (meal) params.push(`meal_type=${meal}`)
    if (cursor) params.push(`cursor=${encodeURIComponent(cursor)}`)
    const body = await api.get(`/diet/records?${params.join('&')}`, { allowCache: false })
    return {
      items: this.decorate(body.items || []),
      next_cursor: body.next_cursor || null,
      has_more: !!body.has_more
    }
  },
  decorate(items) {
    return items.map(item => ({
      ...item,
      meal_label: mealLabel(item.meal_type),
      provenance: item.source_label || SOURCE_LABELS[item.source] || '手动记录',
      macroText: `P ${Math.round(Number(item.protein) || 0)}g · C ${Math.round(Number(item.carbs) || 0)}g · F ${Math.round(Number(item.fat) || 0)}g`,
      fromVision: !!item.vision_analysis_id
    }))
  },
  async loadMore() {
    if (!this.data.hasMore || this.data.loadingMore || !this.data.nextCursor) return
    this.setData({ loadingMore: true })
    try {
      const body = await this.fetchPage(this.data.nextCursor)
      if (this._unloaded) return
      this.setData({
        records: this.data.records.concat(body.items),
        nextCursor: body.next_cursor,
        hasMore: body.has_more,
        loadingMore: false
      })
    } catch (e) {
      this.setData({ loadingMore: false })
      wx.showToast({ title: e.message || '加载失败', icon: 'none' })
    }
  },
  onReachBottom() { this.loadMore() },
  retry() { this.load() },
  filterChange(e) {
    this.setData({ filterIndex: Number(e.detail.value) || 0 }, () => this.load())
  },
  dateChange(e) {
    this.setData({ date: e.detail.value, dateLabel: this.dateLabel(e.detail.value) }, () => this.load())
  },
  shift(days) {
    const base = new Date(this.data.date.replace(/-/g, '/') + ' 12:00:00')
    base.setDate(base.getDate() + days)
    const next = base.toISOString().slice(0, 10)
    this.setData({ date: next, dateLabel: this.dateLabel(next) }, () => this.load())
  },
  open(e) {
    const id = Number(e.currentTarget.dataset.id)
    const record = this.data.records.find(item => item.id === id)
    if (!record) return
    this.setData({ activeId: this.data.activeId === id ? null : id })
    return record
  },
  /** Open the editor with the stored values, including the original items. */
  edit(e) {
    const record = this.open(e)
    if (!record) return
    this.setData({
      editing: true,
      conflict: '',
      editor: {
        id: record.id,
        version: record.version,
        name: record.name,
        meal_type: record.meal_type,
        meal_index: Math.max(0, MEALS.findIndex(item => item.key === record.meal_type)),
        calories: String(record.calories),
        protein: String(record.protein),
        carbs: String(record.carbs),
        fat: String(record.fat),
        fiber: String(record.fiber || 0),
        portion: record.portion || '',
        cooking_method: record.cooking_method || '',
        weight_g: String(record.weight_g || 0),
        items: (record.items || []).map(item => ({ ...item }))
      }
    })
  },
  closeEditor() { this.setData({ editing: false, editor: null, conflict: '' }) },
  editorInput(e) {
    const field = e.currentTarget.dataset.field
    if (!['name', 'portion', 'cooking_method', 'calories', 'protein', 'carbs', 'fat', 'fiber', 'weight_g'].includes(field)) return
    this.setData({ [`editor.${field}`]: e.detail.value })
  },
  editorMeal(e) {
    const index = Number(e.detail.value) || 0
    this.setData({ 'editor.meal_index': index, 'editor.meal_type': MEALS[index].key })
  },
  editorItem(e) {
    const index = Number(e.currentTarget.dataset.index)
    const field = e.currentTarget.dataset.field
    const items = (this.data.editor.items || []).map(item => ({ ...item }))
    if (!items[index]) return
    if (!['name', 'portion', 'portion_basis', 'evidence', 'weight_g', 'calories', 'protein', 'carbs', 'fat', 'fiber'].includes(field)) return
    items[index][field] = ['name', 'portion', 'portion_basis', 'evidence'].includes(field)
      ? e.detail.value
      : (Number(e.detail.value) || 0)
    // Re-total from the edited items so the stored macros match the item list.
    const sum = key => Math.round(items.reduce((total, item) => total + (Number(item[key]) || 0), 0) * 10) / 10
    this.setData({
      'editor.items': items,
      'editor.calories': String(sum('calories')),
      'editor.protein': String(sum('protein')),
      'editor.carbs': String(sum('carbs')),
      'editor.fat': String(sum('fat')),
      'editor.fiber': String(sum('fiber')),
      'editor.weight_g': String(sum('weight_g'))
    })
  },
  async save() {
    const editor = this.data.editor
    if (!editor || this.data.saving) return
    if (!editor.name) { wx.showToast({ title: '请填写食物名称', icon: 'none' }); return }
    this.setData({ saving: true, conflict: '' })
    const body = {
      version: editor.version,
      name: editor.name,
      meal_type: editor.meal_type,
      calories: Number(editor.calories) || 0,
      protein: Number(editor.protein) || 0,
      carbs: Number(editor.carbs) || 0,
      fat: Number(editor.fat) || 0,
      fiber: Number(editor.fiber) || 0,
      portion: editor.portion,
      cooking_method: editor.cooking_method,
      weight_g: Number(editor.weight_g) || 0,
      items: editor.items
    }
    try {
      await api.patch(`/diet/records/${editor.id}`, body)
      wx.showToast({ title: '已更新' })
      this.setData({ saving: false, editing: false, editor: null })
      this.load()
    } catch (e) {
      this.setData({ saving: false })
      if (e.code === 'DIET_RECORD_VERSION_CONFLICT') {
        // Never overwrite the other page's edit: ask the user to refresh.
        this.setData({ conflict: '这条记录已在其他页面修改，请刷新后重新编辑。' })
        return
      }
      if (e.code === 'DIET_RECORD_NOT_FOUND') {
        this.setData({ conflict: '这条记录已被删除。' })
        this.load()
        return
      }
      wx.showModal({ title: '保存失败', content: e.message || '请稍后再试', showCancel: false })
    }
  },
  remove(e) { this.confirmRemove(e.currentTarget.dataset.id) },
  confirmRemove(id, done) {
    wx.showModal({
      title: '删除这条饮食记录？',
      content: '删除后仪表盘会同步减少，且无法撤销。',
      confirmText: '删除',
      confirmColor: '#c0392b',
      success: async res => {
        if (!res.confirm) {
          // Cancelled: let the caller snap the swiped card back into place.
          if (done) done()
          return
        }
        try {
          await api.del(`/diet/records/${id}`)
          wx.showToast({ title: '已删除' })
          this.load()
        } catch (error) {
          wx.showToast({ title: error.message || '删除失败', icon: 'none' })
        }
      }
    })
  },
  addManual() { wx.navigateTo({ url: '/pages/scan/index?manual=1' }) },
  scan() { wx.navigateTo({ url: '/pages/scan/index' }) },
  /** Return to the records dashboard; its onShow re-reads the dashboard. */
  back() { wx.navigateBack({ delta: 1 }) }
})
