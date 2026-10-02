const api = require('../../utils/request')
const cloudMedia = require('../../utils/cloudMedia')
const { ensureLogin } = require('../../utils/auth')
const pending = require('../../utils/pendingJobs')
const catalog = require('../../utils/motionCatalogData')
const { buildViewModel, normalizeTimeline, mergeEvidencePreviews, STAGE_LABELS, TERMINAL_STATUSES, PIPELINE_VERSION } = require('../../utils/motionUnifiedView')

const TARGET_KEYS = ['chest', 'back', 'shoulders', 'arms', 'core', 'quadriceps', 'glutes', 'hamstrings']
const GOAL_KEYS = ['strength', 'hypertrophy', 'muscular_endurance', 'balance', 'core_stability']
const EXERCISE_TYPES = ['auto', 'squat', 'pushup', 'lunge', 'leg_abduction', 'arm_abduction', 'arm_vw']
const EXERCISE_LABELS = { squat: '深蹲', pushup: '俯卧撑', lunge: '弓步蹲', leg_abduction: '腿外展', arm_abduction: '直臂侧平举', arm_vw: '手臂 V/W' }

// 统一动作分析 V2 契约（见规格 §8 / MOTION_V2_CONTRACT §2/§6）。前端只按此形状对接，不实现后端。
// 展示模型映射见 utils/motionUnifiedView.js（纯函数，可单测）。
const POINTER_VERSION = 3
const CONSENT_VERSION = 'motion-real-frames-v2'
const RESPONSE_SCHEMA = 'motion-analysis-v2'

// 手动纠错目录：本地生成表兜底（GET /fitness/motion-capabilities 就绪后可覆盖）。
// 第 0 项为“自由描述”，其余为目录动作（21 类）。
function buildLocalCategoryOptions() {
  return [{ id: '', name_zh: '其他（自由描述）' }]
    .concat(catalog.ACTIONS.map(a => ({ id: a.id, name_zh: a.name_zh })))
}

Page({
  data: {
    file: '', type: '', name: '', size: 0,
    uploading: false, analyzing: false,
    result: null, vm: null,
    jobStatus: '', exerciseType: 'auto', exerciseIndex: 0,
    exerciseOptions: ['自动识别', '深蹲', '俯卧撑', '弓步蹲', '腿外展', '直臂侧平举', '手臂 V/W'],
    analysisId: null, taskKey: null,
    parentAnalysisId: null,
    previousResultAvailable: false,
    consentDeepseek: true, localOnly: false,
    traceInfo: null, traceLoaded: false,
    worker: { enabled: false, online: false }, motionProfile: null,
    trainingIntent: { confirmed: false, target_body_parts: [], goals: [] }, savingIntent: false, targetIndex: 0, goalIndex: 0,
    targetOptions: ['胸部', '背部', '肩部', '手臂', '核心', '股四头肌', '臀部', '腘绳肌'],
    goalOptions: ['力量', '增肌', '肌耐力', '平衡与稳定', '核心稳定'],
    cloudMode: api.isCloud(), showDetails: false, detailsHeight: '0px', showGoal: false,
    // V2 时间轴
    timelineFrames: [], activeFrame: null, brokenImgs: {},
    // 纠错面板（目录选择 + 自由描述）
    categoryOptions: buildLocalCategoryOptions(),
    showCorrect: false, correctIndex: 0, freeLabel: ''
  },
  async onLoad() {
    this._unloaded = false
    try {
      await ensureLogin()
      const pointer = pending.load('motion')
      if (pointer && pointer.extra && pointer.extra.v === POINTER_VERSION) {
        const exerciseType = pointer.extra.requestedExercise || 'auto'
        const exerciseIndex = Math.max(0, EXERCISE_TYPES.indexOf(exerciseType))
        this.setData({
          result: pointer.asset, type: 'video', analysisId: pointer.jobId,
          exerciseType, exerciseIndex, consentDeepseek: pointer.extra.consent !== false,
          jobStatus: '有进行中的分析，正在取回结果…'
        })
        this.continueAnalysis(pointer.jobId, pointer.asset)
      }
      await this.refreshWorkerStatus()
      await this.loadMotionProfile()
      await this.loadTrainingIntent()
      await this.loadCapabilities()
    } catch (e) { this.setData({ jobStatus: e.message || '请先登录' }) }
  },
  onUnload() { this._unloaded = true },
  async onShow() { if (this.data.cloudMode) await this.refreshWorkerStatus() },
  changeExercise(e) {
    if (this.data.analyzing) return
    const i = Number(e.detail.value)
    // 改动作 = 新的纠错入口；幂等键随之变化，会创建新任务，不复用旧结果
    this.setData({ exerciseIndex: i, exerciseType: EXERCISE_TYPES[i], analysisId: null, vm: null, taskKey: null, timelineFrames: [], activeFrame: null })
  },
  toggleConsent(e) {
    // 改云端模式 = 用户改变授权范围：必须创建正确的新任务，不复用旧 analysisId（R12）。
    // 清空 analysisId/vm/taskKey 后，下次 startAnalysis 会用新的 consent 位生成新幂等键。
    const consent = !!e.detail.value
    this.setData({ consentDeepseek: consent, localOnly: !consent, analysisId: null, vm: null, taskKey: null, timelineFrames: [], activeFrame: null })
  },
  async refreshWorkerStatus() {
    try { const worker = await api.get('/system/ai-worker', { allowCache: false }); this.setData({ worker }) } catch (e) {}
  },
  async loadMotionProfile() {
    try { this.setData({ motionProfile: await api.get('/fitness/motion-profile?days=30', { allowCache: false }) }) } catch (e) {}
  },
  async loadTrainingIntent() {
    try {
      const trainingIntent = await api.get('/fitness/training-intent', { allowCache: false })
      const targetIndex = Math.max(0, TARGET_KEYS.indexOf((trainingIntent.target_body_parts || [])[0]))
      const goalIndex = Math.max(0, GOAL_KEYS.indexOf((trainingIntent.goals || [])[0]))
      this.setData({ trainingIntent, targetIndex, goalIndex })
    } catch (e) {}
  },
  // GET /fitness/motion-capabilities（F 包）：动作目录与能力；未就绪用本地生成表兜底。
  async loadCapabilities() {
    try {
      const caps = await api.get('/fitness/motion-capabilities', { allowCache: false })
      const actions = (caps && caps.actions) || (caps && caps.catalog && caps.catalog.actions)
      if (Array.isArray(actions) && actions.length) {
        this.setData({
          categoryOptions: [{ id: '', name_zh: '其他（自由描述）' }]
            .concat(actions.map(a => ({ id: a.canonical_id || a.id, name_zh: a.name_zh || a.id })))
        })
      }
    } catch (e) { /* 离线/未就绪：保留本地目录 */ }
  },
  changeTarget(e) { this.setData({ targetIndex: Number(e.detail.value) }) },
  changeGoal(e) { this.setData({ goalIndex: Number(e.detail.value) }) },
  async saveTrainingIntent() {
    if (this.data.savingIntent) return
    this.setData({ savingIntent: true })
    try {
      const trainingIntent = await api.put('/fitness/training-intent', {
        target_body_parts: [TARGET_KEYS[this.data.targetIndex]],
        goals: [GOAL_KEYS[this.data.goalIndex]],
        constraints: [], preferred_equipment: [], notes: ''
      })
      this.setData({ trainingIntent })
      wx.showToast({ title: '训练意图已确认' })
    } catch (e) {
      wx.showModal({ title: '保存失败', content: e.message || '请稍后重试', showCancel: false })
    } finally { this.setData({ savingIntent: false }) }
  },
  async choose() {
    if (this.data.uploading || this.data.analyzing) return
    let r
    try { r = await wx.chooseMedia({ count: 1, mediaType: ['image', 'video'], sourceType: ['album', 'camera'], maxDuration: 60, sizeType: ['compressed'] }) }
    catch (e) { return }
    if (!r.tempFiles?.length) return
    pending.forget('motion')
    const f = r.tempFiles[0]
    const type = r.type || f.fileType || (String(f.tempFilePath).match(/\.(mp4|mov)$/i) ? 'video' : 'image')
    this.setData({
      file: f.tempFilePath, type, size: Number(f.size || 0),
      name: (f.tempFilePath || '').split('/').pop(), result: null,
      vm: null, analysisId: null, taskKey: null, jobStatus: '', timelineFrames: [], activeFrame: null
    })
  },
  async upload() {
    if (this.data.uploading) return null
    if (this.data.result) return this.data.result
    if (!this.data.file) { await this.choose(); if (!this.data.file) return null }
    this.setData({ uploading: true })
    try {
      await ensureLogin()
      let r
      if (api.isCloud()) {
        r = await cloudMedia.uploadAndRegister(this.data.file, this.data.type === 'video' ? 'video' : 'image', this.data.name)
      } else {
        r = await api.upload('/media/upload', this.data.file)
      }
      r.sizeMB = (Number(r.size || r.size_bytes || this.data.size || 0) / 1024 / 1024).toFixed(2)
      this.setData({ result: r })
      wx.showToast({ title: '素材已入云' })
      return r
    } catch (e) {
      wx.showModal({ title: '上传失败', content: e.message || '请稍后重试', showCancel: false })
      return null
    } finally { this.setData({ uploading: false }) }
  },

  // 幂等键：同一次提交复用；用户主动重新分析/改模式时生成新键（§8.1）。
  buildIdem(asset, requested, suffix) {
    return `motion:${asset.media_id}:${requested}:${this.data.consentDeepseek ? 1 : 0}:${PIPELINE_VERSION}${suffix ? ':' + suffix : ''}`
  },

  // 统一入口：一个“开始分析”按钮。首次点击创建统一任务；重复点击/断网恢复/重进页面复用同一任务。
  async startAnalysis() {
    if (this.data.analyzing) return
    if (this.data.type !== 'video') return wx.showToast({ title: '请选择视频素材', icon: 'none' })
    let asset = this.data.result
    if (!asset) { asset = await this.upload(); if (!asset) return }
    this.setData({ analyzing: true, vm: null, timelineFrames: [], activeFrame: null, jobStatus: '排队中' })
    try {
      await this.refreshWorkerStatus()
      // 已存在同一素材+同一动作+同一模式的任务 → 直接轮询，不重复创建
      if (!this.data.analysisId) {
        const requested = this.data.exerciseType
        const idem = this.buildIdem(asset, requested)
        const created = await api.post('/media/motion-analyses', {
          media_id: asset.media_id,
          requested_exercise: requested,
          exercise_hint: null,
          cloud_review_mode: this.data.consentDeepseek ? 'redacted_frames' : 'off',
          consent_version: CONSENT_VERSION,
          response_schema: RESPONSE_SCHEMA
        }, { 'Idempotency-Key': idem })
        this.setData({ analysisId: created.analysis_id, taskKey: idem })
        pending.remember('motion', created.analysis_id, asset, {
          requestedExercise: requested, v: POINTER_VERSION, consent: this.data.consentDeepseek
        })
      }
      await this.continueAnalysis(this.data.analysisId, asset)
    } catch (e) {
      if (this._unloaded) return
      this.setData({ jobStatus: e.message || '分析未完成，可稍后重试' })
    } finally { if (!this._unloaded) this.setData({ analyzing: false }) }
  },

  // 纯读取轮询 GET /media/motion-analyses/{id}，不触发任何模型调用。
  async continueAnalysis(analysisId, asset) {
    this.setData({ analyzing: true })
    try {
      const deadline = Date.now() + 300000
      let delay = 1000, refreshes = 0
      let body
      while (Date.now() < deadline && !this._unloaded) {
        body = await api.get(`/media/motion-analyses/${analysisId}`, { allowCache: false })
        const status = body.status || 'queued'
        if (!this._unloaded) this.setData({ jobStatus: STAGE_LABELS[status] || '处理中' })
        if (TERMINAL_STATUSES.includes(status)) break
        // 媒体临时地址过期时刷新一次，再继续轮询
        if (String(body.error_code || '') === 'media_url_expired' && asset && asset.cloud_file_id && refreshes < 2) {
          await cloudMedia.refresh(asset.media_id, asset.cloud_file_id); refreshes += 1; delay = 1000; continue
        }
        await new Promise(resolve => setTimeout(resolve, delay))
        delay = Math.min(5000, Math.round(delay * 1.25))
      }
      if (this._unloaded) return
      const status = body.status || 'failed'
      if (status === 'failed') throw new Error((body.error) || '分析失败，可稍后重试')
      if (status === 'cancelled') throw new Error('任务已取消，可重新开始')
      // 轮询到期仍未到终态：保留进行中状态与指针，不渲染假完成结果（R12/T12）。
      if (!TERMINAL_STATUSES.includes(status)) {
        this.setData({ jobStatus: '仍在分析，可稍后回来', vm: null, timelineFrames: [], activeFrame: null })
        return
      }
      const result = body.result || {}
      const raw = (result.recognition || result.timeline || result.keyframes) ? result : body
      const vm = buildViewModel(raw)
      vm.localOnly = !this.data.consentDeepseek
      this.setData({
        vm, jobStatus: STAGE_LABELS[status] || status, traceInfo: null, traceLoaded: false,
        timelineFrames: vm.timelineFrames, activeFrame: vm.activeFrame, brokenImgs: {}
      })
      if (status === 'completed') pending.forget('motion')
      // 结果帧未带 preview_url 时，只读拉一次 /evidence 补齐缩略图（不计费、不调模型）。
      this.fillEvidencePreviews()
    } catch (e) {
      if (this._unloaded) return
      this.setData({ jobStatus: e.message || '分析未完成，可稍后重试' })
    } finally { if (!this._unloaded) this.setData({ analyzing: false }) }
  },

  // 时间轴本地交互：点击缩略图只切换选中态，不请求模型（§4.1）。
  selectFrame(e) {
    const id = e.currentTarget.dataset.id
    const frame = (this.data.timelineFrames || []).find(f => f.id === id)
    if (frame) this.setData({ activeFrame: frame })
  },
  // “回看这一刻”：seek 到该时刻，播放/暂停仍由用户控制（§4.1/§8.4）。
  replayFrame() {
    const frame = this.data.activeFrame
    if (!frame) return
    const ctx = wx.createVideoContext('motionVideo', this)
    if (ctx && typeof ctx.seek === 'function') ctx.seek(Number(frame.timestamp_ms) / 1000)
  },
  // 结果帧已带 preview_url 时直接用；有空缺帧则只读拉一次 /evidence 补齐 frame_id→preview_url。
  // 该 GET 只读、不计费、不调模型；缩略图点击仍只本地选中。失败则降级占位，不阻塞文字讲解。
  async fillEvidencePreviews() {
    const id = this.data.analysisId
    const frames = this.data.timelineFrames || []
    if (!id || !frames.length) return
    if (frames.every(f => f.previewUrl)) return
    try {
      const evidence = await api.get(`/media/motion-analyses/${id}/evidence`, { allowCache: false })
      if (this._unloaded) return
      const merged = mergeEvidencePreviews(frames, evidence)
      if (merged === frames) return
      const cur = this.data.activeFrame
      this.setData({
        timelineFrames: merged,
        activeFrame: cur ? (merged.find(f => f.id === cur.id) || cur) : (merged[0] || null)
      })
    } catch (e) {
      // /evidence 不可用：保留占位，文字讲解照常展示。
    }
  },
  // 帧图加载失败：标记该帧为 broken，显示占位，不崩溃、不影响讲解。
  onFrameImgError(e) {
    const id = e.currentTarget.dataset.id
    if (!id) return
    this.setData({ [`brokenImgs.${id}`]: true })
  },

  // ── 纠错与反馈（R12/R13 / §6） ──
  // 打开纠错面板：目录选择 + 自由描述。
  correctLabel() {
    if (!this.data.vm) return
    this.setData({ showCorrect: true, correctIndex: 0, freeLabel: '' })
  },
  changeCorrect(e) { this.setData({ correctIndex: Number(e.detail.value) }) },
  onFreeLabel(e) { this.setData({ freeLabel: e.detail.value || '' }) },
  cancelCorrect() { this.setData({ showCorrect: false }) },
  // 提交真实标签：目录 id → canonical_id；自由描述 → novel_label_zh。未选不提交占位（R13）。
  async confirmLabel() {
    const id = this.data.analysisId
    if (!id) return
    const picked = this.data.categoryOptions[this.data.correctIndex] || {}
    const corrected = picked.id || (this.data.freeLabel || '').trim()
    if (!corrected) { wx.showToast({ title: '请选择或输入正确动作', icon: 'none' }); return }
    const body = picked.id ? { canonical_id: picked.id } : { novel_label_zh: corrected }
    try {
      await api.post(`/media/motion-analyses/${id}/confirm-label`, body)
      this.setData({ showCorrect: false })
      wx.showToast({ title: '已记录，正在重新分析' })
      // 纠错后产生子任务：新幂等键，复用素材与无副作用阶段。
      await this.reanalyze()
    } catch (e) {
      wx.showModal({ title: '提交失败', content: e.message || '请稍后重试', showCancel: false })
    }
  },
  // POST /media/motion-analyses/{id}/reanalyze：新幂等键 + 父子关系。
  async reanalyze() {
    const parentId = this.data.analysisId
    if (!parentId) return
    const asset = this.data.result
    const idem = this.buildIdem(asset || { media_id: parentId }, this.data.exerciseType, 'r' + Date.now())
    const body = {
      cloud_review_mode: this.data.consentDeepseek ? 'redacted_frames' : 'off',
      reason: 'user_label_correction'
    }
    // The corrected catalogue id must travel with the request: it becomes the
    // child run's requested_type (spec §7.4). Free text is never sent here.
    const hint = this.data.exerciseType
    if (hint && hint !== 'auto') body.exercise_hint = hint
    try {
      const child = await api.post(`/media/motion-analyses/${parentId}/reanalyze`, body, { 'Idempotency-Key': idem })
      const childId = child && child.analysis_id
      if (!childId) throw new Error('重新分析未返回新任务')
      // Switch to the child immediately and drop the parent view model so the
      // two runs' evidence never mix (spec §7.4).
      this.setData({
        parentAnalysisId: parentId,
        analysisId: childId,
        vm: null,
        timelineFrames: [],
        activeFrame: null,
        taskKey: null,
        traceInfo: null,
        traceLoaded: false,
        jobStatus: '正在按确认动作重新分析',
        previousResultAvailable: true
      })
      pending.remember('motion', childId, asset)
      await this.continueAnalysis(childId, asset)
    } catch (e) {
      if (this._unloaded) return
      // A rejected hint is a user-correctable error, not a retry loop.
      const rejectedHint = e && (e.code === 'UNKNOWN_CATALOG_ID' || e.statusCode === 422)
      if (rejectedHint) {
        this.setData({
          jobStatus: '',
          showCorrect: true
        })
        wx.showModal({
          title: '无法按这个标签重分析',
          content: '该动作不在受支持的目录中，请重新选择，或在页面上直接查看画面讲解。',
          showCancel: false
        })
        return
      }
      this.setData({ jobStatus: e.message || '重新分析未完成，可稍后重试' })
    }
  },
  /** Look at the parent run again without merging its evidence into the child. */
  async viewPrevious() {
    const parentId = this.data.parentAnalysisId
    if (!parentId) return
    this.setData({
      analysisId: parentId,
      vm: null,
      timelineFrames: [],
      activeFrame: null,
      traceInfo: null,
      traceLoaded: false,
      jobStatus: '正在读取上一次结果'
    })
    await this.continueAnalysis(parentId, this.data.result)
  },
  // 反馈：kind ∈ useful / wrong_frame / unhelpful_advice；关联当前选中帧（R13）。
  async sendFeedback(e) {
    const kind = e.currentTarget.dataset.kind
    const id = this.data.analysisId
    if (!id) return
    const body = { kind }
    if (kind === 'wrong_frame') body.frame_id = (this.data.activeFrame && this.data.activeFrame.id) || ''
    try {
      await api.post(`/media/motion-analyses/${id}/feedback`, body)
      wx.showToast({ title: '感谢反馈' })
    } catch (e) {
      wx.showToast({ title: (e.message || '反馈失败，可重试'), icon: 'none' })
    }
  },
  async loadTrace() {
    if (!this.data.analysisId) return
    if (this.data.traceLoaded) { this.setData({ showDetails: !this.data.showDetails }); return }
    try {
      const traceInfo = await api.get(`/media/motion-analyses/${this.data.analysisId}/trace`, { allowCache: false })
      this.setData({ traceInfo, traceLoaded: true })
    } catch (e) { this.setData({ traceInfo: { note: e.message || '来源摘要暂不可用' }, traceLoaded: true }) }
  },

  // Animated Text Disclosure：按真实内容高度过渡
  measureDetails(cb) {
    wx.createSelectorQuery().in(this).select('#detailsBody').boundingClientRect((r) => {
      cb(r && r.height ? Math.ceil(r.height) : 0)
    }).exec()
  },
  toggleDetails() {
    const open = !this.data.showDetails
    if (open) {
      this.setData({ showDetails: true })
      this.measureDetails((h) => this.setData({ detailsHeight: h + 'px' }))
      return
    }
    this.measureDetails((h) => {
      this.setData({ detailsHeight: h + 'px' }, () => {
        setTimeout(() => this.setData({ detailsHeight: '0px', showDetails: false }), 20)
      })
    })
  },
  onDisclosureEnd(e) {
    if (!e || !e.detail || e.detail.propertyName !== 'height') return
    if (this.data.showDetails && this.data.detailsHeight !== 'auto') this.setData({ detailsHeight: 'auto' })
  },
  toggleGoal() { this.setData({ showGoal: !this.data.showGoal }) }
})
