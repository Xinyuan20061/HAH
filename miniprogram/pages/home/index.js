const api = require('../../utils/request')
const { ensureLogin } = require('../../utils/auth')
const { NAVIGATION_TARGETS, openAction } = require('../../utils/agentNavigation')

const COMPANIONS = [
  { id: 'xiaojian', name: '小健', space: '健身房', icon: '/assets/characters/xiaojian-portrait-v1.png' },
  { id: 'xiaokang', name: '小康', space: '养生馆', icon: '/assets/characters/xiaokang-portrait-v1.png' }
]

const CHARACTER_ACTIVITIES = new Set([
  'idle', 'listening', 'thinking', 'planning',
  'speaking', 'presenting', 'success', 'error'
])

const CUE_ACTIVITY = {
  'plan.compose': 'planning',
  'answer.present': 'presenting',
  'workout.guide': 'presenting',
  'safety.pause': 'error'
}

const ACTIVITY_STATUS = {
  idle: '等你开口',
  listening: '正在听',
  thinking: '正在理解',
  planning: '正在整理草案',
  speaking: '正在回应',
  presenting: '正在展示',
  success: '已经准备好',
  error: '先暂停一下'
}
const AUTO_NAVIGATION_FALLBACK_MS = 1600
const PLAN_HANDOFF_VERSION = 'healthmate.plan-handoff.v1'
const PLAN_ROUTE_TEST_PROMPT = '给我制定一个计划'

// The server sends semantic targets, never page URLs. Keeping the mapping here
// makes model/provider text unable to navigate to an arbitrary destination.
function weekFallback() {
  const labels = ['日', '一', '二', '三', '四', '五', '六']
  const result = []
  const today = new Date()
  for (let offset = 6; offset >= 0; offset -= 1) {
    const day = new Date(today)
    day.setDate(today.getDate() - offset)
    result.push({ label: labels[day.getDay()], date: String(day.getDate()), done: false })
  }
  return result
}

function normalizeStreak(streak) {
  const source = streak || {}
  return {
    current: Number(source.current || 0),
    last7: Array.isArray(source.last7) && source.last7.length ? source.last7 : weekFallback()
  }
}

function firstDirective(result) {
  if (!result || typeof result !== 'object') return null
  const presentation = result.presentation
  if (!presentation || typeof presentation !== 'object') return null
  return presentation.version === 'healthmate.presentation.v1' ? presentation : null
}

function safeRunId(value) {
  const runId = Number(value)
  return Number.isInteger(runId) && runId > 0 ? runId : null
}

function structuredPlanFallback(result) {
  const runId = safeRunId(result && result.run_id)
  const plan = result && result.plan
  const items = plan && Array.isArray(plan.items) ? plan.items : []
  if (!runId || result.intent !== 'plan' || String(result.safety_level || 'normal') !== 'normal') return null
  const actorId = result.agent && result.agent.id === 'xiaokang' ? 'xiaokang' : 'xiaojian'
  if (!items.length) {
    return {
      activity: 'presenting',
      action: {
        target: 'capability_setup',
        label: NAVIGATION_TARGETS.capability_setup.label,
        route: NAVIGATION_TARGETS.capability_setup.route,
        runId,
        actorId
      },
      autoNavigate: false
    }
  }
  return {
    activity: 'planning',
    action: {
      target: 'plan_preview',
      label: NAVIGATION_TARGETS.plan_preview.label,
      route: NAVIGATION_TARGETS.plan_preview.route,
      runId,
      actorId
    },
    autoNavigate: true
  }
}

function persistPlanHandoff(result, action) {
  if (!action || action.target !== 'plan_preview') return
  const runId = safeRunId(action.runId)
  const plan = result && result.plan
  if (!runId || !plan || !Array.isArray(plan.items) || !plan.items.length) return
  try {
    wx.setStorageSync(`healthmate_plan_preview_handoff:${runId}`, {
      version: PLAN_HANDOFF_VERSION,
      savedAt: Date.now(),
      reply: String(result.reply || ''),
      presentation: { actor: action.actorId === 'xiaokang' ? 'xiaokang' : 'xiaojian' },
      plan_preview: {
        run_id: runId,
        status: 'draft',
        read_only: true,
        title: String(plan.title || '本周健康计划'),
        items: plan.items.slice(0, 10),
        write: { status: 'not_applied', automatic: false, confirmation_required: true }
      }
    })
  } catch (error) {}
}

function normalizePresentation(result) {
  const fallback = structuredPlanFallback(result)
  const directive = firstDirective(result)
  if (!directive) return fallback || { activity: 'speaking', action: null, autoNavigate: false }
  const navigation = directive.navigation && typeof directive.navigation === 'object'
    ? directive.navigation
    : directive.handoff && typeof directive.handoff === 'object'
      ? directive.handoff
      : {}
  const target = String(navigation.target || directive.target || '')
  const destination = NAVIGATION_TARGETS[target]
  const cue = String(directive.cue || directive.activity || '')
  const cueActivity = CUE_ACTIVITY[cue] || cue
  const activity = CHARACTER_ACTIVITIES.has(cueActivity) ? cueActivity : 'speaking'
  if (!destination) return fallback || { activity, action: null, autoNavigate: false }

  const params = navigation.params && typeof navigation.params === 'object' ? navigation.params : {}
  const runId = safeRunId(params.run_id)
  const responseRunId = safeRunId(result.run_id)
  // A plan destination without an owned run cannot be a real preview.
  if (target === 'plan_preview') {
    const write = directive.write || {}
    const validDraftBoundary = write.status === 'not_applied'
      && write.automatic === false
      && write.confirmation_required === true
    if (!runId || runId !== responseRunId || !validDraftBoundary) {
      return fallback || { activity, action: null, autoNavigate: false }
    }
  }
  const action = {
    target,
    label: destination.label,
    route: destination.route,
    runId,
    actorId: directive.actor === 'xiaokang' ? 'xiaokang' : 'xiaojian'
  }
  return {
    activity,
    action,
    autoNavigate: navigation.mode === 'after_animation'
  }
}

function safeActionUrl(action) {
  if (!action || !NAVIGATION_TARGETS[action.target]) return ''
  const destination = NAVIGATION_TARGETS[action.target]
  if (action.target === 'plan_preview') {
    const runId = safeRunId(action.runId)
    if (!runId) return ''
    const actorId = action.actorId === 'xiaokang' ? 'xiaokang' : 'xiaojian'
    // Preview is explicitly read-only. The plan screen owns the later manual
    // confirmation; the gym never calls an apply/write endpoint.
    return `${destination.route}?mode=preview&run_id=${runId}&agent_id=${actorId}`
  }
  return destination.route
}

Page({
  data: {
    loading: true,
    error: '',
    dateLabel: '',
    streak: normalizeStreak(),
    companions: COMPANIONS,
    activeCompanion: COMPANIONS[0],
    recording: false,
    voiceWorking: false,
    voiceStatus: '按住说话',
    characterActivity: 'idle',
    characterStatus: '等你开口',
    response: null
  },

  onLoad() {
    this._unloaded = false
    const now = new Date()
    const week = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][now.getDay()]
    const saved = wx.getStorageSync('healthmate_agent_id')
    this._voiceAutoplay = wx.getStorageSync('healthmate_voice_autoplay') !== false
    const activeCompanion = COMPANIONS.find(item => item.id === saved) || COMPANIONS[0]
    this.setData({ dateLabel: `${now.getMonth() + 1}月${now.getDate()}日 · ${week}`, activeCompanion })
    this.initVoice()
  },

  onShow() {
    const tabBar = typeof this.getTabBar === 'function' && this.getTabBar()
    const saved = wx.getStorageSync('healthmate_agent_id')
    const activeCompanion = COMPANIONS.find(item => item.id === saved) || COMPANIONS[0]
    if (tabBar) {
      tabBar.setData({ selected: 0, wheelOpen: false, quickOpen: false })
      if (typeof tabBar.syncCompanion === 'function') tabBar.syncCompanion(activeCompanion.id)
    }
    this._voiceAutoplay = wx.getStorageSync('healthmate_voice_autoplay') !== false
    if (activeCompanion.id !== this.data.activeCompanion.id) {
      this.setData({ activeCompanion, response: null, voiceStatus: '按住说话' })
    }
    this.load()
  },

  onUnload() {
    this._unloaded = true
    this.clearCharacterTimer()
    this.clearPendingNavigation()
    if (this._audio) this._audio.destroy()
    if (this._recorder && this.data.recording) this._recorder.stop()
  },

  async load() {
    this.setData({ loading: true, error: '' })
    try {
      await ensureLogin()
      const center = await api.get('/health/command-center', { allowCache: true })
      if (!this._unloaded) this.setData({ streak: normalizeStreak(center && center.streak) })
    } catch (error) {
      if (!this._unloaded) this.setData({ error: error.message || '健康数据暂时没有加载出来' })
    } finally {
      if (!this._unloaded) this.setData({ loading: false })
    }
  },

  retry() { this.load() },

  selectCompanion(e) {
    if (this.data.recording || this.data.voiceWorking) return
    const activeCompanion = COMPANIONS.find(item => item.id === e.currentTarget.dataset.id)
    if (!activeCompanion) return
    wx.setStorageSync('healthmate_agent_id', activeCompanion.id)
    const tabBar = typeof this.getTabBar === 'function' && this.getTabBar()
    if (tabBar && typeof tabBar.syncCompanion === 'function') tabBar.syncCompanion(activeCompanion.id)
    this.clearPendingNavigation()
    this.setData({ activeCompanion, response: null, voiceStatus: '按住说话', characterActivity: 'idle', characterStatus: '等你开口' })
  },

  clearCharacterTimer() {
    if (this._characterTimer) clearTimeout(this._characterTimer)
    this._characterTimer = null
  },

  setCharacterActivity(activity, status, settleToIdle) {
    const next = CHARACTER_ACTIVITIES.has(activity) ? activity : 'idle'
    this.clearCharacterTimer()
    this.setData({ characterActivity: next, characterStatus: status || '' })
    if (!settleToIdle) return
    this._characterTimer = setTimeout(() => {
      this._characterTimer = null
      if (!this._unloaded && this.data.characterActivity === next) {
        this.setData({ characterActivity: 'idle', characterStatus: '等你开口' })
      }
    }, settleToIdle)
  },

  initVoice() {
    if (!wx.getRecorderManager) return
    this._recorder = wx.getRecorderManager()
    this._recorder.onStart(() => {
      this.setData({ recording: true, voiceStatus: '松开发送' })
      this.setCharacterActivity('listening', '正在听')
    })
    this._recorder.onStop(result => {
      if (this._unloaded) return
      this.setData({ recording: false })
      if (this._voiceCancelled) {
        this._voiceCancelled = false
        this.setData({ voiceStatus: '按住说话' })
        this.setCharacterActivity('idle', '等你开口')
        return
      }
      if (!result.tempFilePath || Number(result.duration || 0) < 420) {
        this.setData({ voiceStatus: '再按久一点' })
        this.setCharacterActivity('idle', '等你开口')
        return
      }
      this.handleVoice(result.tempFilePath)
    })
    this._recorder.onError(() => {
      this.setData({ recording: false, voiceWorking: false, voiceStatus: '麦克风不可用' })
      this.setCharacterActivity('error', '没有听见', 900)
      wx.showToast({ title: '请允许使用麦克风', icon: 'none' })
    })
  },

  startVoice() {
    if (!this._recorder || this.data.recording || this.data.voiceWorking) return
    this._voiceCancelled = false
    this.setData({ recording: true, response: null, voiceStatus: '正在听' })
    this.clearPendingNavigation()
    this.setCharacterActivity('listening', '正在听')
    this._recorder.start({ duration: 30000, sampleRate: 16000, numberOfChannels: 1, encodeBitRate: 48000, format: 'mp3' })
  },

  stopVoice() {
    if (this._recorder && this.data.recording) this._recorder.stop()
  },

  cancelVoice() {
    this._voiceCancelled = true
    if (this._recorder && this.data.recording) this._recorder.stop()
  },

  async handleVoice(filePath) {
    this.setData({ voiceWorking: true, voiceStatus: '正在理解' })
    this.setCharacterActivity('thinking', '正在理解')
    try {
      const audioBase64 = await new Promise((resolve, reject) => {
        wx.getFileSystemManager().readFile({ filePath, encoding: 'base64', success: result => resolve(result.data), fail: reject })
      })
      const transcript = await api.postLong('/harness/voice/transcribe', {
        agent_id: this.data.activeCompanion.id,
        audio_base64: audioBase64,
        format: 'mp3',
        request_id: 'asr-' + Date.now()
      })
      const text = String(transcript && transcript.text || '').trim()
      if (!text) throw new Error('没有听清，再说一次吧')
      await this.submitAgentMessage(text, {
        channel: 'voice',
        voiceStatus: '继续说',
        autoplay: this._voiceAutoplay
      })
    } catch (error) {
      if (!this._unloaded) {
        this.setData({ voiceWorking: false, voiceStatus: '再试一次' })
        this.setCharacterActivity('error', '需要再试一次', 900)
        wx.showToast({ title: error.message || '语音交互暂时不可用', icon: 'none' })
      }
    }
  },

  async submitAgentMessage(text, options) {
    const settings = options || {}
    const result = await api.postLong('/agent/respond', {
      message: text,
      agent_id: this.data.activeCompanion.id,
      channel: settings.channel === 'text' ? 'text' : 'voice'
    })
    if (this._unloaded) return null

    const reply = String(result && result.reply || '我听到了，我们继续。')
    const presentation = normalizePresentation(result)
    const response = {
      heard: text,
      reply,
      runId: result && result.run_id,
      action: presentation.action
    }
    persistPlanHandoff(result, presentation.action)
    this.queuePendingNavigation(presentation.autoNavigate ? presentation.action : null)
    this.setData({
      response,
      voiceStatus: settings.voiceStatus || '继续说',
      voiceWorking: false
    })
    this.setCharacterActivity(
      presentation.activity,
      ACTIVITY_STATUS[presentation.activity] || ACTIVITY_STATUS.speaking
    )
    const autoplay = settings.autoplay == null ? this._voiceAutoplay : settings.autoplay
    if (autoplay) this.speak(reply)
    return result
  },

  async testPlanRouting() {
    if (this.data.recording || this.data.voiceWorking) return
    this.clearPendingNavigation()
    this.setData({ response: null, voiceWorking: true, voiceStatus: '正在测试计划路由' })
    this.setCharacterActivity('thinking', '正在理解')
    try {
      await this.submitAgentMessage(PLAN_ROUTE_TEST_PROMPT, {
        channel: 'voice',
        voiceStatus: '决策已返回',
        autoplay: false
      })
    } catch (error) {
      if (this._unloaded) return
      this.setData({ voiceWorking: false, voiceStatus: '测试失败' })
      this.setCharacterActivity('error', '需要再试一次', 900)
      wx.showToast({ title: error.message || '计划路由暂时不可用', icon: 'none' })
    }
  },

  async speak(text) {
    if (!text || this._unloaded) return
    const keepsTaskMotion = ['planning', 'presenting', 'success', 'error'].includes(this.data.characterActivity)
    if (!keepsTaskMotion) this.setCharacterActivity('speaking', '正在回应')
    try {
      const result = await api.postLong('/harness/voice/synthesize', {
        agent_id: this.data.activeCompanion.id,
        text,
        request_id: 'tts-' + Date.now()
      })
      // New segmented contract: play MP3 segments in order (never byte-concatenate).
      let segments = Array.isArray(result.segments) && result.segments.length ? result.segments : null
      if (!segments && result.audio_base64) {
        segments = [{ index: 0, audio_base64: result.audio_base64, content_type: result.content_type || 'audio/mpeg' }]
      }
      if (!segments || !segments.length) {
        if (!keepsTaskMotion) this.setCharacterActivity('idle', '等你开口')
        return
      }
      await this.playSegments(segments)
      if (!keepsTaskMotion && !this._unloaded) this.setCharacterActivity('idle', '等你开口')
    } catch (error) {
      if (!keepsTaskMotion && !this._unloaded) this.setCharacterActivity('idle', '等你开口')
    }
  },

  // Write each MP3 segment to its OWN file and chain playback. If a later segment
  // fails, already-played segments are not re-synthesized and not re-played.
  async playSegments(segments) {
    const fsm = wx.getFileSystemManager()
    const stamp = Date.now()
    const files = []
    for (const seg of segments) {
      const fp = `${wx.env.USER_DATA_PATH}/hm-tts-${stamp}-${seg.index}.mp3`
      try {
        await new Promise((resolve, reject) => fsm.writeFile({ filePath: fp, data: seg.audio_base64, encoding: 'base64', success: resolve, fail: reject }))
        files.push(fp)
      } catch (e) { break } // this segment failed -> play the successful ones and stop
    }
    if (!files.length) return
    await this.playQueue(files, 0)
  },

  playQueue(files, i) {
    return new Promise(resolve => {
      if (this._unloaded || i >= files.length) return resolve()
      if (this._audio) this._audio.destroy()
      const audio = wx.createInnerAudioContext()
      this._audio = audio
      audio.src = files[i]
      audio.onEnded(() => { this.playQueue(files, i + 1).then(resolve) })
      audio.onError(() => resolve()) // stop here; earlier segments already played
      audio.play()
    })
  },

  replay() {
    if (this.data.response && this.data.response.reply) this.speak(this.data.response.reply)
  },

  clearPendingNavigation() {
    if (this._autoNavigationTimer) clearTimeout(this._autoNavigationTimer)
    this._autoNavigationTimer = null
    this._pendingNavigation = null
  },

  queuePendingNavigation(action) {
    this.clearPendingNavigation()
    if (!action) return
    this._pendingNavigation = action
    this._autoNavigationTimer = setTimeout(() => {
      this._autoNavigationTimer = null
      this.completePendingNavigation()
    }, AUTO_NAVIGATION_FALLBACK_MS)
  },

  completePendingNavigation() {
    const pending = this._pendingNavigation
    if (!pending || this._unloaded) return
    this.clearPendingNavigation()
    this.setCharacterActivity('idle', '等你开口')
    this.navigateAction(pending)
  },

  onCharacterClipComplete(e) {
    const activity = e.detail && e.detail.activity
    const pending = this._pendingNavigation
    if (pending && activity === this.data.characterActivity) {
      this.completePendingNavigation()
      return
    }
    if (activity === 'planning' || activity === 'presenting' || activity === 'success' || activity === 'error') {
      this.setCharacterActivity('idle', '等你开口')
    }
  },

  goState() {
    wx.navigateTo({ url: '/pages/state/index' })
  },

  navigateAction(action) {
    openAction(action)
  },

  runResponseAction() {
    const response = this.data.response
    const action = response && response.action
    if (!action) return
    this.clearPendingNavigation()
    this.navigateAction(action)
  }
})
