const api = require('../../utils/request')
const cloudMedia = require('../../utils/cloudMedia')
const { ensureLogin } = require('../../utils/auth')
const pending = require('../../utils/pendingJobs')
const { pollJob } = require('../../utils/jobPolling')

const TARGET_KEYS = ['chest', 'back', 'shoulders', 'arms', 'core', 'quadriceps', 'glutes', 'hamstrings']
const GOAL_KEYS = ['strength', 'hypertrophy', 'muscular_endurance', 'balance', 'core_stability']
const EXERCISE_TYPES = ['auto','squat','pushup','lunge','leg_abduction','arm_abduction','arm_vw']
const EXERCISE_LABELS = { squat: '深蹲', pushup: '俯卧撑', lunge: '弓步蹲', leg_abduction: '腿外展', arm_abduction: '直臂侧平举', arm_vw: '手臂 V/W', bicep_curl: '哑铃弯举', deadlift: '硬拉', shoulder_press: '肩推', lateral_raise: '侧平举', leg_raise: '抬腿', side_plank: '侧平板', plank: '平板支撑', situp: '卷腹', crunch: '卷腹', pullup: '引体向上', row: '划船', chest_press: '卧推', hip_thrust: '臀桥', calf_raise: '提踵', mountain_climber: '登山跑', jumping_jack: '开合跳', burpee: '波比跳', high_knees: '高抬腿' }
function fmtAngle(value) {
  if (value === null || value === undefined || value === '') return '--'
  const n = Number(value)
  if (!Number.isFinite(n)) return '--'
  const rounded = Number(n.toFixed(2))
  return Number.isInteger(rounded) ? String(rounded) : String(rounded)
}

Page({
  data: {
    file: '', type: '', name: '', size: 0,
    uploading: false, analyzing: false,
    result: null, analysis: null, activeFrame: null,
    jobStatus: '', exerciseType: 'auto', exerciseIndex: 0, exerciseOptions: ['自动识别', '深蹲', '俯卧撑', '弓步蹲', '腿外展', '直臂侧平举', '手臂 V/W'], jobId: null,
    worker: { enabled: false, online: false, queue_depth: 0, processing: 0 }, motionProfile: null,
    kinetics: null, kineticsStatus: '', analyzingKinetics: false,
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
      if (pointer) {
        const exerciseType = pointer.extra.exerciseType || 'auto'
        const exerciseIndex = Math.max(0, EXERCISE_TYPES.indexOf(exerciseType))
        this.setData({ jobId: pointer.jobId, result: pointer.asset, type: 'video', exerciseType, exerciseIndex, jobStatus: '有云端任务，点击分析查看结果' })
      }
      await this.refreshWorkerStatus()
      await this.loadMotionProfile()
      await this.loadTrainingIntent()
    } catch (e) { this.setData({ jobStatus: e.message || '请先登录' }) }
  },
  onUnload() { this._unloaded = true },
  changeExercise(e) { if (this.data.analyzing) return; const i = Number(e.detail.value); this.setData({ exerciseIndex: i, exerciseType: EXERCISE_TYPES[i], jobId: null, analysis: null, activeFrame: null }) },
  async onShow() { if (this.data.cloudMode) await this.refreshWorkerStatus() },
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
      analysis: null, activeFrame: null, jobStatus: '', jobId: null
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
  async analyze() {
    if (this.data.analyzing) return
    if (this.data.type !== 'video') return wx.showToast({ title: '请选择视频素材', icon: 'none' })
    let asset = this.data.result
    if (!asset) { asset = await this.upload(); if (!asset) return }
    this.setData({ analyzing: true, analysis: null, jobStatus: '排队中' })
    try {
      await this.refreshWorkerStatus()
      if (this.data.cloudMode && this.data.worker.enabled && !this.data.worker.motion_online) {
        wx.showToast({ title: '电脑端未在线，任务会保留', icon: 'none', duration: 2500 })
      }
      let jobId = this.data.jobId
      if (!jobId) {
        const created = await api.post('/media/motion-jobs', { media_id: asset.media_id, exercise_type: this.data.exerciseType })
        jobId = created.job_id
        this.setData({ jobId }); pending.remember('motion', jobId, asset, { exerciseType: this.data.exerciseType })
      }
      pending.remember('motion', jobId, asset, { exerciseType: this.data.exerciseType })
      const a = await pollJob({ path: '/media/motion-jobs', jobId, asset,
        cancelled: () => this._unloaded,
        onStatus: label => { if (!this._unloaded) this.setData({ jobStatus: label }) }
      })
      if (this._unloaded) return
      const labels = EXERCISE_LABELS
      const detectedType = (a.pose && a.pose.exercise_type) || (a.recognition && a.recognition.selected_type) || this.data.exerciseType
      a.detectedExerciseType = detectedType
      a.detectedExerciseName = labels[detectedType] || (detectedType && detectedType !== 'auto' ? detectedType : '未确认')
      if (a.recognition) {
        const method = String(a.recognition.method || '')
        a.recognitionLabel = method.includes('deepseek_vision_fallback') ? 'DeepSeek 兜底识别'
          : method.includes('deepseek_vision_review') ? '本地识别 + DeepSeek 复查'
          : method.includes('deepseek') ? 'DeepSeek 参与识别'
          : '本地规则识别'
        a.recognition.candidates = (a.recognition.candidates || []).slice(0, 3).map(item => ({
          ...item,
          exerciseName: labels[item.exercise_type] || item.exercise_type,
          matchPct: Math.round(Number(item.match_score) || 0)
        }))
        if (a.recognition.review) {
          a.reviewNote = (a.recognition.review.reason || '').slice(0, 120)
        }
      }
      if (a.training_semantics && a.training_semantics.goal_alignment) {
        const score = a.training_semantics.goal_alignment.score
        a.training_semantics.goal_alignment.scoreLabel = score === null || score === undefined ? '--' : Math.round(Number(score))
        a.training_semantics.goal_alignment.missingLabel = [
          ...(a.training_semantics.goal_alignment.missing_body_parts || []),
          ...(a.training_semantics.goal_alignment.missing_goals || [])
        ].join('、')
      }
      const primary = { pushup:['肘角','elbow','elbow_angle'], leg_abduction:['腿部角','leg_abduction','leg_abduction_angle'], arm_abduction:['抬臂角','arm_abduction','arm_abduction_angle'], arm_vw:['肘角','arm_vw','arm_vw_angle'] }[detectedType] || ['膝角','knee','knee_angle']
      a.primaryLabel = primary[0]
      a.primaryMin = a.pose && a.pose.angles ? fmtAngle(a.pose.angles[primary[1]+'_min']) : '--'
      a.primaryMax = a.pose && a.pose.angles ? fmtAngle(a.pose.angles[primary[1]+'_max']) : '--'
      a.primaryMinRaw = a.pose && a.pose.angles ? a.pose.angles[primary[1]+'_min'] : null
      a.primaryMaxRaw = a.pose && a.pose.angles ? a.pose.angles[primary[1]+'_max'] : null
      a.frames = (a.frames || []).map((x, index) => ({
        ...x,
        index,
        fullUrl: x.url || '',
        hasPreview: !!x.url,
        primaryAngle: x[primary[2]] == null ? null : fmtAngle(x[primary[2]]),
        primaryAngleRaw: x[primary[2]] == null ? null : Number(x[primary[2]]),
        motionPct: Math.round((Number(x.confidence) || 0) * 100)
      }))
      a.tips = ((a.pose&&a.pose.errors)||[]).map(item=>String(item.label||'').slice(0,8)).filter(Boolean).slice(0,3)
      if(!a.tips.length)a.tips=['保持稳定','匀速完成']
      a.sourceLabel=a.source==='cloud'?'云端':'本地'
      const period = Number(a.motion && a.motion.period_mean_seconds)
      a.periodLabel = Number.isFinite(period) && period > 0 ? Number(period.toFixed(2)) : '--'
      this.setData({ analysis: a, activeFrame: a.frames[0] || null, jobStatus: '完成' }, () => { this.drawScoreRadar(); this.drawSkeleton() })
      pending.forget('motion')
      await this.loadMotionProfile()
      await this.refreshWorkerStatus()
    } catch (e) {
      if (this._unloaded) return
      this.setData({ jobStatus: e.message || '分析未完成，可稍后重试' })
      wx.showModal({ title: '动作反馈未完成', content: e.message || '请稍后重试', showCancel: false })
    } finally { if (!this._unloaded) this.setData({ analyzing: false }) }
  },
  pickFrame(e) { const i = Number(e.currentTarget.dataset.i); this.setData({ activeFrame: this.data.analysis.frames[i] }, () => this.drawSkeleton()) },

  // Kinetics-400：400 类预训练识别（识别到但无次数/评分）
  async analyzeKinetics() {
    if (this.data.analyzingKinetics) return
    if (this.data.type !== 'video') return wx.showToast({ title: '请选择视频素材', icon: 'none' })
    let asset = this.data.result
    if (!asset) { asset = await this.upload(); if (!asset) return }
    this.setData({ analyzingKinetics: true, kinetics: null, kineticsStatus: '排队中' })
    try {
      await this.refreshWorkerStatus()
      if (this.data.cloudMode && this.data.worker.enabled && !this.data.worker.kinetics_online) {
        wx.showToast({ title: '电脑端 400 类识别未在线，任务会保留', icon: 'none', duration: 2500 })
      }
      const created = await api.post('/media/kinetics-jobs', { media_id: asset.media_id })
      const k = await pollJob({ path: '/media/kinetics-jobs', jobId: created.job_id, asset,
        cancelled: () => this._unloaded,
        onStatus: label => { if (!this._unloaded) this.setData({ kineticsStatus: label }) }
      })
      if (this._unloaded) return
      k.topPct = Math.round(Number(k.top_probability || 0) * 100)
      k.candidates = (k.candidates || []).slice(0, 5).map(item => ({
        ...item,
        name: item.label_zh || item.label,
        pct: Math.round(Number(item.probability || 0) * 100)
      }))
      this.setData({ kinetics: k, kineticsStatus: '完成' })
      await this.refreshWorkerStatus()
    } catch (e) {
      if (this._unloaded) return
      this.setData({ kineticsStatus: e.message || '识别未完成，可稍后重试' })
    } finally { if (!this._unloaded) this.setData({ analyzingKinetics: false }) }
  },

  // Animated Text Disclosure：按真实内容高度过渡，而不是固定时长淡入
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
      setTimeout(() => this.drawSkeleton(), 360)
      return
    }
    // 收起：先量出当前高度并固化成具体值，下一帧再收到 0，才能连续过渡
    this.measureDetails((h) => {
      this.setData({ detailsHeight: h + 'px' }, () => {
        setTimeout(() => this.setData({ detailsHeight: '0px', showDetails: false }), 20)
      })
    })
  },
  // 展开动画结束后落成 auto：内容再长高也不会被裁掉
  onDisclosureEnd(e) {
    if (!e || !e.detail || e.detail.propertyName !== 'height') return
    if (this.data.showDetails && this.data.detailsHeight !== 'auto') this.setData({ detailsHeight: 'auto' })
  },
  toggleGoal() { this.setData({ showGoal: !this.data.showGoal }) },
  drawScoreRadar() {
    const score = this.data.analysis && this.data.analysis.score
    if (!score || !score.available) return
    wx.createSelectorQuery().in(this).select('#scoreRadar').fields({ node: true, size: true }).exec(res => {
      const item = res && res[0]; if (!item || !item.node) return
      const canvas = item.node, ctx = canvas.getContext('2d')
      const dpr = (wx.getWindowInfo ? wx.getWindowInfo().pixelRatio : 1) || 1
      canvas.width = item.width * dpr; canvas.height = item.height * dpr; ctx.scale(dpr, dpr)
      const values = [score.completeness, score.stability, score.rhythm_control, 100 - score.risk_index]
      const labels = ['完成度', '稳定性', '节奏', '安全度']
      const cx = item.width / 2, cy = item.height / 2, radius = Math.min(item.width, item.height) * .31
      const point = (i, ratio) => { const a = -Math.PI / 2 + i * Math.PI / 2; return [cx + Math.cos(a) * radius * ratio, cy + Math.sin(a) * radius * ratio] }
      ctx.clearRect(0, 0, item.width, item.height); ctx.font = '12px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
      for (let ring = 1; ring <= 4; ring++) { ctx.beginPath(); for (let i = 0; i < 4; i++) { const p = point(i, ring / 4); i ? ctx.lineTo(...p) : ctx.moveTo(...p) } ctx.closePath(); ctx.strokeStyle = '#eeeee6'; ctx.stroke() }
      ctx.beginPath(); values.forEach((v, i) => { const p = point(i, Math.max(0, Math.min(100, Number(v))) / 100); i ? ctx.lineTo(...p) : ctx.moveTo(...p) }); ctx.closePath(); ctx.fillStyle = 'rgba(196,226,103,.32)'; ctx.fill(); ctx.strokeStyle = '#506336'; ctx.lineWidth = 2; ctx.stroke()
      labels.forEach((label, i) => { const p = point(i, 1.27); ctx.fillStyle = '#5f665f'; ctx.fillText(label, ...p) })
    })
  },
  drawSkeleton() {
    const points = this.data.activeFrame && this.data.activeFrame.skeleton
    if (!points || !points.length) return
    wx.createSelectorQuery().in(this).select('#skeletonCanvas').fields({ node: true, size: true }).exec(res => {
      const item = res && res[0]; if (!item || !item.node) return
      const canvas = item.node, ctx = canvas.getContext('2d'), dpr = (wx.getWindowInfo ? wx.getWindowInfo().pixelRatio : 1) || 1
      canvas.width = item.width * dpr; canvas.height = item.height * dpr; ctx.scale(dpr, dpr)
      ctx.fillStyle = '#111613'; ctx.fillRect(0, 0, item.width, item.height)
      const visible = points.filter(p => Number(p.visibility) >= .35), map = {}; visible.forEach(p => { map[p.id] = { x: Number(p.x) * item.width, y: Number(p.y) * item.height } })
      const links = [['left_shoulder','right_shoulder'],['left_shoulder','left_elbow'],['left_elbow','left_wrist'],['right_shoulder','right_elbow'],['right_elbow','right_wrist'],['left_shoulder','left_hip'],['right_shoulder','right_hip'],['left_hip','right_hip'],['left_hip','left_knee'],['left_knee','left_ankle'],['right_hip','right_knee'],['right_knee','right_ankle']]
      ctx.strokeStyle = '#c4e267'; ctx.lineWidth = 3; ctx.lineCap = 'round'; links.forEach(([a,b]) => { if (!map[a] || !map[b]) return; ctx.beginPath(); ctx.moveTo(map[a].x,map[a].y); ctx.lineTo(map[b].x,map[b].y); ctx.stroke() })
      Object.values(map).forEach(p => { ctx.beginPath(); ctx.arc(p.x,p.y,5,0,Math.PI*2); ctx.fillStyle='#fff'; ctx.fill(); ctx.strokeStyle='#506336'; ctx.lineWidth=2; ctx.stroke() })
    })
  }
})
