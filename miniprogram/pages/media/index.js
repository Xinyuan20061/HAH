const api = require('../../utils/request')
const cloudMedia = require('../../utils/cloudMedia')
const { ensureLogin } = require('../../utils/auth')
const pending = require('../../utils/pendingJobs')
const { buildViewModel, STAGE_LABELS, TERMINAL_STATUSES, PIPELINE_VERSION } = require('../../utils/motionUnifiedView')

const TARGET_KEYS = ['chest', 'back', 'shoulders', 'arms', 'core', 'quadriceps', 'glutes', 'hamstrings']
const GOAL_KEYS = ['strength', 'hypertrophy', 'muscular_endurance', 'balance', 'core_stability']
const EXERCISE_TYPES = ['auto', 'squat', 'pushup', 'lunge', 'leg_abduction', 'arm_abduction', 'arm_vw']
const EXERCISE_LABELS = { squat: '深蹲', pushup: '俯卧撑', lunge: '弓步蹲', leg_abduction: '腿外展', arm_abduction: '直臂侧平举', arm_vw: '手臂 V/W' }

// 统一动作分析契约（见规格 4.4 / 第 5 节）。前端只按此形状对接，不实现后端。
// 展示模型映射见 utils/motionUnifiedView.js（纯函数，可单测）。
const POINTER_VERSION = 2

Page({
  data: {
    file: '', type: '', name: '', size: 0,
    uploading: false, analyzing: false,
    result: null, vm: null,
    jobStatus: '', exerciseType: 'auto', exerciseIndex: 0,
    exerciseOptions: ['自动识别', '深蹲', '俯卧撑', '弓步蹲', '腿外展', '直臂侧平举', '手臂 V/W'],
    analysisId: null, taskKey: null,
    consentDeepseek: true, localOnly: false,
    traceInfo: null, traceLoaded: false,
    worker: { enabled: false, online: false }, motionProfile: null,
    trainingIntent: { confirmed: false, target_body_parts: [], goals: [] }, savingIntent: false, targetIndex: 0, goalIndex: 0,
    targetOptions: ['胸部', '背部', '肩部', '手臂', '核心', '股四头肌', '臀部', '腘绳肌'],
    goalOptions: ['力量', '增肌', '肌耐力', '平衡与稳定', '核心稳定'],
    cloudMode: api.isCloud(), showDetails: false, detailsHeight: '0px', showGoal: false
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
    } catch (e) { this.setData({ jobStatus: e.message || '请先登录' }) }
  },
  onUnload() { this._unloaded = true },
  async onShow() { if (this.data.cloudMode) await this.refreshWorkerStatus() },
  changeExercise(e) {
    if (this.data.analyzing) return
    const i = Number(e.detail.value)
    // 改动作 = 新的纠错入口；幂等键随之变化，会创建新任务，不复用旧结果
    this.setData({ exerciseIndex: i, exerciseType: EXERCISE_TYPES[i], analysisId: null, vm: null, taskKey: null })
  },
  toggleConsent(e) {
    // 用户关闭云端视觉协作 → 页面明确标“仅本地分析”，下次创建任务携带 consent_deepseek_frames=false
    const consent = !!e.detail.value
    this.setData({ consentDeepseek: consent, localOnly: !consent })
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
      vm: null, analysisId: null, taskKey: null, jobStatus: ''
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

  // 统一入口：一个“开始分析”按钮。首次点击创建统一任务；重复点击/断网恢复/重进页面复用同一任务。
  async startAnalysis() {
    if (this.data.analyzing) return
    if (this.data.type !== 'video') return wx.showToast({ title: '请选择视频素材', icon: 'none' })
    let asset = this.data.result
    if (!asset) { asset = await this.upload(); if (!asset) return }
    this.setData({ analyzing: true, vm: null, jobStatus: '排队中' })
    try {
      await this.refreshWorkerStatus()
      // 已存在同一素材+同一动作的任务 → 直接轮询，不重复创建
      if (!this.data.analysisId) {
        const requested = this.data.exerciseType
        const idem = `motion:${asset.media_id}:${requested}:${this.data.consentDeepseek ? 1 : 0}:${PIPELINE_VERSION}`
        const created = await api.post('/media/motion-analyses', {
          media_id: asset.media_id,
          requested_exercise: requested,
          consent_deepseek_frames: !!this.data.consentDeepseek,
          pipeline_version: PIPELINE_VERSION
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

  // 纯读取轮询 GET /media/motion-analyses/{id}，不触发任何模型调用
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
        if (status === 'failed') break
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
      const raw = body.result && (body.result.recognition || body.result.keyframes) ? body.result : body
      const vm = buildViewModel(raw)
      vm.localOnly = !this.data.consentDeepseek
      this.setData({ vm, jobStatus: STAGE_LABELS[status] || status, traceInfo: null, traceLoaded: false })
      if (status === 'completed') pending.forget('motion')
    } catch (e) {
      if (this._unloaded) return
      this.setData({ jobStatus: e.message || '分析未完成，可稍后重试' })
    } finally { if (!this._unloaded) this.setData({ analyzing: false }) }
  },

  // 用户确认/纠错：记录到后端，不伪造评分
  async confirmLabel() {
    const vm = this.data.vm, id = this.data.analysisId
    if (!vm || !id) return
    try {
      await api.post(`/media/motion-analyses/${id}/confirm-label`, { label_id: vm.labelId || 'user_confirmed', correction_reason: 'user_confirmed' })
      wx.showToast({ title: '已记录确认' })
    } catch (e) { wx.showToast({ title: e.message || '记录失败', icon: 'none' }) }
  },
  correctLabel() {
    // 引导用户在上方 picker 手动选择动作后重新点击“开始分析”
    this.setData({ vm: null, analysisId: null, jobStatus: '请在上方手动选择动作，再点“开始分析”' })
  },
  // 反馈：类别错 / 关键帧不准 / 建议无用，按用户隔离与去重由后端处理
  async sendFeedback(e) {
    const kind = e.currentTarget.dataset.kind, id = this.data.analysisId
    if (!id) return
    const body = { useful: kind === 'useful' }
    if (kind === 'label') body.label_correction = true
    if (kind === 'frame') body.frame_id = (this.data.vm && this.data.vm.keyframes[0] && this.data.vm.keyframes[0].id) || ''
    if (kind === 'advice') body.comment = '建议无用'
    try { await api.post(`/media/motion-analyses/${id}/feedback`, body); wx.showToast({ title: '感谢反馈' }) } catch (e) {}
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
