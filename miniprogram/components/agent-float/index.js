const api = require('../../utils/request')
const { getSession, saveSession } = require('../../utils/floatingAgentSession')
const { normalizeNavigation, persistPlanHandoff, safeActionUrl } = require('../../utils/agentNavigation')

const AGENTS = {
  xiaojian: { id: 'xiaojian', name: '小健', icon: '/assets/characters/xiaojian-portrait-v1.png', greeting: '我在。训练、记录和计划都可以直接告诉我。' },
  xiaokang: { id: 'xiaokang', name: '小康', icon: '/assets/characters/xiaokang-portrait-v1.png', greeting: '我在。今天想照顾好哪一件事？' }
}
const TOKEN_TICK_MS = 22

function displayTokens(text) {
  return String(text || '').match(/[\u3400-\u4dbf\u4e00-\u9fff]|[A-Za-z0-9]+(?:[._:/+-][A-Za-z0-9]+)*|\s+|./g) || []
}

function messageId(role) {
  return `${role}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`
}

Component({
  properties: {
    voiceOnly: { type: Boolean, value: false },
    pageOwned: { type: Boolean, value: false }
  },

  data: {
    expanded: false,
    actor: AGENTS.xiaojian,
    messages: [],
    input: '',
    canSend: false,
    sending: false,
    recording: false,
    voiceWorking: false,
    statusText: '随时可以开始',
    scrollTop: 0,
    suppressed: false
  },

  lifetimes: {
    attached() {
      this._detached = false
      const suppressed = this.syncVisibility()
      this.syncActor()
      const session = getSession()
      this.setData({ messages: session.messages || [] })
      if (!suppressed) this.initVoice()
    },
    detached() {
      this._detached = true
      this.clearTimers()
      if (this._streamTask && this._streamTask.abort) this._streamTask.abort()
      if (this._recorder && this.data.recording) this._recorder.stop()
      this.removeVoiceListeners()
    }
  },

  pageLifetimes: {
    show() {
      const suppressed = this.syncVisibility()
      this.syncActor()
      const session = getSession()
      if (!this.data.sending) this.setData({ messages: session.messages || [] })
      if (!suppressed) this.initVoice()
    },
    hide() {
      if (this._recorder && this.data.recording) {
        this._voiceCancelled = true
        this._recorder.stop()
      }
      this.removeVoiceListeners()
      this.setData({ recording: false, voiceWorking: false })
    }
  },

  methods: {
    syncVisibility() {
      if (this.properties.pageOwned) {
        if (this.data.suppressed) this.setData({ suppressed: false })
        return false
      }
      const pages = getCurrentPages()
      const current = pages[pages.length - 1]
      const route = current && current.route || ''
      const suppressed = route === 'pages/home/index' || route === 'pages/chat/index' || route === 'pages/plan/index'
      if (suppressed !== this.data.suppressed) this.setData({ suppressed })
      return suppressed
    },

    syncActor() {
      const saved = wx.getStorageSync('healthmate_agent_id')
      const actor = AGENTS[saved] || AGENTS.xiaojian
      if (actor.id !== this.data.actor.id) this.setData({ actor })
    },

    clearTimers() {
      if (this._tokenTimer) clearTimeout(this._tokenTimer)
      if (this._routeTimer) clearTimeout(this._routeTimer)
      if (this._scrollTimer) clearTimeout(this._scrollTimer)
      this._tokenTimer = null
      this._routeTimer = null
      this._scrollTimer = null
      this._tokenQueue = []
      this._pendingDone = null
    },

    toggleDialog() {
      const expanded = !this.data.expanded
      if (expanded) this.triggerEvent('interactionstart')
      this.setData({ expanded }, () => { if (expanded) this.scrollBottom() })
    },

    closeDialog() { this.setData({ expanded: false }) },

    onInput(e) {
      const input = e.detail.value
      this.setData({ input, canSend: !!String(input || '').trim() })
    },

    composerAction() {
      if (this.data.sending) return this.stopGeneration()
      const text = String(this.data.input || '').trim()
      if (text) this.submit(text, 'text')
    },

    initVoice() {
      if (!wx.getRecorderManager) return
      this._recorder = wx.getRecorderManager()
      if (this._voiceListenersBound) return
      this._onRecorderStart = () => {
        if (this._detached) return
        this.setData({ expanded: true, recording: true, voiceWorking: false, statusText: '正在听' })
      }
      this._onRecorderStop = result => {
        if (this._detached) return
        this.setData({ recording: false })
        if (this._voiceCancelled) {
          this._voiceCancelled = false
          this.setData({ statusText: '随时可以开始' })
          return
        }
        if (!result.tempFilePath || Number(result.duration || 0) < 420) {
          this.setData({ statusText: '再按久一点' })
          return
        }
        this.handleVoice(result.tempFilePath)
      }
      this._onRecorderError = () => {
        if (this._detached) return
        this.setData({ recording: false, voiceWorking: false, statusText: '麦克风不可用' })
        wx.showToast({ title: '请允许使用麦克风', icon: 'none' })
      }
      this._recorder.onStart(this._onRecorderStart)
      this._recorder.onStop(this._onRecorderStop)
      this._recorder.onError(this._onRecorderError)
      this._voiceListenersBound = true
    },

    removeVoiceListeners() {
      if (!this._recorder || !this._voiceListenersBound) return
      if (!this._recorder.offStart || !this._recorder.offStop || !this._recorder.offError) return
      this._recorder.offStart(this._onRecorderStart)
      this._recorder.offStop(this._onRecorderStop)
      this._recorder.offError(this._onRecorderError)
      this._voiceListenersBound = false
    },

    startVoice() {
      if (!this._recorder) {
        wx.showToast({ title: '当前设备暂不支持语音输入', icon: 'none' })
        return
      }
      if (this.data.recording || this.data.voiceWorking || this.data.sending) return
      this.triggerEvent('interactionstart')
      this._voiceCancelled = false
      this.setData({ expanded: true, recording: true, statusText: '正在听' })
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
      this.setData({ voiceWorking: true, statusText: '正在识别' })
      try {
        const audioBase64 = await new Promise((resolve, reject) => {
          wx.getFileSystemManager().readFile({ filePath, encoding: 'base64', success: result => resolve(result.data), fail: reject })
        })
        const transcript = await api.postLong('/harness/voice/transcribe', {
          agent_id: this.data.actor.id,
          audio_base64: audioBase64,
          format: 'mp3',
          request_id: 'float-asr-' + Date.now()
        })
        const text = String(transcript && transcript.text || '').trim()
        if (!text) throw new Error('没有听清，再说一次吧')
        this.setData({ voiceWorking: false })
        this.submit(text, 'voice')
      } catch (error) {
        if (this._detached) return
        this.setData({ voiceWorking: false, statusText: '再试一次' })
        wx.showToast({ title: error.message || '语音输入暂时不可用', icon: 'none' })
      }
    },

    submit(text, channel) {
      if (!text || this.data.sending) return
      const actor = this.data.actor
      const messages = this.data.messages.concat([
        { id: messageId('user'), role: 'user', content: text },
        { id: messageId('assistant'), role: 'assistant', content: '', pending: true, agentName: actor.name, agentIcon: actor.icon, action: null }
      ])
      this._streamStopped = false
      this._tokenQueue = []
      this._pendingDone = null
      this.setData({
        expanded: true,
        messages,
        input: '',
        canSend: false,
        sending: true,
        statusText: '正在理解'
      }, () => this.scrollBottom())
      saveSession({ messages })
      this._streamTask = api.streamPost('/agent/respond/stream', {
        message: text,
        agent_id: actor.id,
        channel: channel === 'voice' ? 'voice' : 'text'
      }, {
        onStage: stage => {
          if (!this._detached && stage && stage.label) this.setData({ statusText: stage.label })
        },
        onDelta: chunk => this.queueTokens(chunk),
        onDone: result => this.completeWhenDrained(result),
        onError: error => this.handleStreamError(error, text, actor.id, channel)
      })
    },

    queueTokens(chunk) {
      if (this._streamStopped || this._detached || !chunk) return
      this._tokenQueue = (this._tokenQueue || []).concat(displayTokens(chunk))
      if (!this._tokenTimer) this.drainTokens()
    },

    drainTokens() {
      if (this._streamStopped || this._detached) return
      const queue = this._tokenQueue || []
      if (!queue.length) {
        this._tokenTimer = null
        if (this._pendingDone) {
          const done = this._pendingDone
          this._pendingDone = null
          this.finishResponse(done)
        }
        return
      }
      const batchSize = queue.length > 80 ? 5 : queue.length > 32 ? 3 : 1
      const index = this.data.messages.length - 1
      const content = String(this.data.messages[index] && this.data.messages[index].content || '') + queue.splice(0, batchSize).join('')
      this.setData({
        [`messages[${index}].content`]: content,
        [`messages[${index}].pending`]: false,
        statusText: '正在回应'
      })
      this.scrollBottom()
      this._tokenTimer = setTimeout(() => this.drainTokens(), TOKEN_TICK_MS)
    },

    completeWhenDrained(result) {
      if (this._detached) return
      this._pendingDone = result || {}
      if (!this._tokenTimer && !(this._tokenQueue || []).length) {
        const done = this._pendingDone
        this._pendingDone = null
        this.finishResponse(done)
      }
    },

    finishResponse(done) {
      if (this._streamStopped || this._detached) return
      const result = done && done.result || {}
      const navigation = normalizeNavigation(result)
      const index = this.data.messages.length - 1
      const messages = this.data.messages.slice()
      const existing = messages[index] || {}
      messages[index] = Object.assign({}, existing, {
        pending: false,
        content: existing.content || String(result.reply || '我已经整理好了。'),
        action: navigation.action
      })
      persistPlanHandoff(result, navigation.action)
      this._streamTask = null
      this.setData({ messages, sending: false, voiceWorking: false, statusText: '随时可以继续' }, () => this.scrollBottom())
      saveSession({ messages, sessionId: result.session_id })
      if (navigation.autoNavigate && navigation.action) {
        this._routeTimer = setTimeout(() => this.openActionValue(navigation.action), 720)
      }
    },

    async handleStreamError(error, text, agentId, channel) {
      if (this._streamStopped || this._detached) return
      const index = this.data.messages.length - 1
      if (this.data.messages[index] && this.data.messages[index].content) {
        this._streamTask = null
        this.setData({ sending: false, statusText: '连接中断，可继续提问' })
        saveSession({ messages: this.data.messages })
        return
      }
      try {
        const result = await api.postLong('/agent/respond', { message: text, agent_id: agentId, channel })
        this.queueTokens(result.reply || '我已经整理好了。')
        this.completeWhenDrained({ provider: result.provider, result })
      } catch (fallbackError) {
        const messages = this.data.messages.slice()
        messages[index] = Object.assign({}, messages[index], { pending: false, content: '暂时没有连接成功，稍后再试一次。' })
        this._streamTask = null
        this.setData({ messages, sending: false, voiceWorking: false, statusText: '暂时无法连接' }, () => this.scrollBottom())
        saveSession({ messages })
      }
    },

    stopGeneration() {
      this._streamStopped = true
      if (this._streamTask && this._streamTask.abort) this._streamTask.abort()
      if (this._tokenTimer) clearTimeout(this._tokenTimer)
      this._streamTask = null
      this._tokenTimer = null
      this._tokenQueue = []
      this._pendingDone = null
      const messages = this.data.messages.slice()
      const index = messages.length - 1
      if (messages[index] && !messages[index].content) messages[index] = Object.assign({}, messages[index], { pending: false, content: '已停止。' })
      this.setData({ messages, sending: false, statusText: '已停止' })
      saveSession({ messages })
    },

    openAction(e) {
      const index = Number(e.currentTarget.dataset.index)
      const message = this.data.messages[index]
      this.openActionValue(message && message.action)
    },

    openActionValue(action) {
      const url = safeActionUrl(action)
      if (!url) return
      if (this._routeTimer) clearTimeout(this._routeTimer)
      this._routeTimer = null
      saveSession({ messages: this.data.messages })
      const pages = getCurrentPages()
      const current = pages[pages.length - 1]
      const path = url.split('?')[0].replace(/^\//, '')
      if (current && current.route === path) {
        wx.redirectTo({ url })
        return
      }
      if (pages.length >= 9) wx.redirectTo({ url })
      else wx.navigateTo({ url })
    },

    scrollBottom() {
      if (this._scrollTimer) return
      this._scrollTimer = setTimeout(() => {
        this._scrollTimer = null
        if (!this._detached) this.setData({ scrollTop: this.data.scrollTop + 100000 })
      }, 48)
    }
  }
})
