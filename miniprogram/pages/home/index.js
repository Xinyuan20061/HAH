const api = require('../../utils/request')
const { ensureLogin } = require('../../utils/auth')

const COMPANIONS = [
  { id: 'xiaojian', name: '小健', role: '训练搭子', greeting: '今天别给自己找借口。', icon: '/assets/icons/agent-xiaojian.png' },
  { id: 'xiaokang', name: '小康', role: '养生搭子', greeting: '先听听身体，再慢慢开始。', icon: '/assets/icons/agent-xiaokang.png' }
]

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

function nextAction(text, result) {
  if (result && result.plan) return { label: '确认并查看计划', route: '/pages/plan/index', kind: 'plan' }
  if (/记录|饮食|早餐|午餐|晚餐|热量|卡路里/.test(text)) return { label: '打开记录', route: '/pages/records/index', kind: 'route' }
  if (/训练|动作|健身|怎么练/.test(text)) return { label: '打开训练', route: '/pages/workout/index', kind: 'route' }
  return null
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
    if (tabBar) tabBar.setData({ selected: 0, wheelOpen: false, quickOpen: false })
    const saved = wx.getStorageSync('healthmate_agent_id')
    const activeCompanion = COMPANIONS.find(item => item.id === saved) || COMPANIONS[0]
    this._voiceAutoplay = wx.getStorageSync('healthmate_voice_autoplay') !== false
    if (activeCompanion.id !== this.data.activeCompanion.id) {
      this.setData({ activeCompanion, response: null, voiceStatus: '按住说话' })
    }
    this.load()
  },

  onUnload() {
    this._unloaded = true
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
    this.setData({ activeCompanion, response: null, voiceStatus: '按住说话' })
  },

  initVoice() {
    if (!wx.getRecorderManager) return
    this._recorder = wx.getRecorderManager()
    this._recorder.onStart(() => this.setData({ recording: true, voiceStatus: '松开发送' }))
    this._recorder.onStop(result => {
      if (this._unloaded) return
      this.setData({ recording: false })
      if (this._voiceCancelled) {
        this._voiceCancelled = false
        this.setData({ voiceStatus: '按住说话' })
        return
      }
      if (!result.tempFilePath || Number(result.duration || 0) < 420) {
        this.setData({ voiceStatus: '再按久一点' })
        return
      }
      this.handleVoice(result.tempFilePath)
    })
    this._recorder.onError(() => {
      this.setData({ recording: false, voiceWorking: false, voiceStatus: '麦克风不可用' })
      wx.showToast({ title: '请允许使用麦克风', icon: 'none' })
    })
  },

  startVoice() {
    if (!this._recorder || this.data.recording || this.data.voiceWorking) return
    this._voiceCancelled = false
    this.setData({ recording: true, response: null, voiceStatus: '正在听' })
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
      const result = await api.postLong('/agent/respond', {
        message: text,
        agent_id: this.data.activeCompanion.id,
        channel: 'voice'
      })
      const reply = String(result && result.reply || '我听到了，我们继续。')
      const response = {
        heard: text,
        reply,
        plan: result && result.plan || null,
        runId: result && result.run_id,
        action: nextAction(text, result),
        applied: false
      }
      if (!this._unloaded) this.setData({ response, voiceStatus: '继续说', voiceWorking: false })
      if (this._voiceAutoplay) this.speak(reply)
    } catch (error) {
      if (!this._unloaded) {
        this.setData({ voiceWorking: false, voiceStatus: '再试一次' })
        wx.showToast({ title: error.message || '语音交互暂时不可用', icon: 'none' })
      }
    }
  },

  async speak(text) {
    if (!text || this._unloaded) return
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
      if (!segments || !segments.length) return
      await this.playSegments(segments)
    } catch (error) {}
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

  goState() {
    wx.navigateTo({ url: '/pages/state/index' })
  },

  async runResponseAction() {
    const response = this.data.response
    const action = response && response.action
    if (!action) return
    if (action.kind === 'plan' && response.runId && !response.applied) {
      try {
        await api.post(`/agent/runs/${response.runId}/apply-plan`, {})
        this.setData({ 'response.applied': true })
        wx.showToast({ title: '已加入本周计划' })
      } catch (error) {
        wx.showToast({ title: error.message || '计划暂时没有保存', icon: 'none' })
        return
      }
    }
    wx.navigateTo({ url: action.route })
  }
})
