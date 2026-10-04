const ACTIVITIES = new Set([
  'idle', 'listening', 'thinking', 'planning',
  'speaking', 'presenting', 'success', 'error'
])

const POSE_BY_ACTIVITY = {
  idle: 0,
  listening: 1,
  thinking: 0,
  planning: 2,
  speaking: 1,
  presenting: 2,
  success: 3,
  error: 0
}

const CLIP_DURATION = {
  planning: 1080,
  presenting: 820,
  success: 680,
  error: 680
}

const CHARACTER_NAME = {
  xiaojian: '小健',
  xiaokang: '小康'
}

const POSE_TRANSITION_MS = 240

function safeAgent(agentId) {
  return agentId === 'xiaokang' ? 'xiaokang' : 'xiaojian'
}

function safeActivity(activity) {
  return ACTIVITIES.has(activity) ? activity : 'idle'
}

Component({
  properties: {
    agentId: { type: String, value: 'xiaojian' },
    activity: { type: String, value: 'idle' }
  },

  data: {
    safeAgentId: 'xiaojian',
    previousAgentId: 'xiaojian',
    safeActivity: 'idle',
    poseIndex: 0,
    previousPoseIndex: 0,
    swapToken: 'a',
    transitioning: false,
    imageFailed: false,
    showVoiceWave: false,
    ariaLabel: '小健正在等待'
  },

  observers: {
    'agentId, activity'(agentId, activity) {
      this.applyClip(agentId, activity)
    }
  },

  lifetimes: {
    attached() {
      this.applyClip(this.properties.agentId, this.properties.activity)
    },
    detached() {
      this.clearClipTimer()
      this.clearTransitionTimer()
    }
  },

  methods: {
    clearClipTimer() {
      if (this._clipTimer) clearTimeout(this._clipTimer)
      this._clipTimer = null
    },

    clearTransitionTimer() {
      if (this._transitionTimer) clearTimeout(this._transitionTimer)
      this._transitionTimer = null
    },

    applyClip(agentId, activity) {
      if (typeof this.setData !== 'function') return
      this.clearClipTimer()
      const nextAgent = safeAgent(agentId)
      const nextActivity = safeActivity(activity)
      const nextPose = POSE_BY_ACTIVITY[nextActivity]
      const poseChanged = nextAgent !== this.data.safeAgentId || nextPose !== this.data.poseIndex
      const common = {
        safeAgentId: nextAgent,
        safeActivity: nextActivity,
        poseIndex: nextPose,
        imageFailed: false,
        showVoiceWave: nextActivity === 'listening' || nextActivity === 'speaking',
        ariaLabel: `${CHARACTER_NAME[nextAgent]}${this.activityLabel(nextActivity)}`
      }

      if (poseChanged) {
        this.clearTransitionTimer()
        this.setData(Object.assign(common, {
          previousAgentId: this.data.safeAgentId,
          previousPoseIndex: this.data.poseIndex,
          swapToken: this.data.swapToken === 'a' ? 'b' : 'a',
          transitioning: true
        }))
        this._transitionTimer = setTimeout(() => {
          this._transitionTimer = null
          this.setData({ transitioning: false })
        }, POSE_TRANSITION_MS)
      } else {
        this.setData(common)
      }

      const duration = CLIP_DURATION[nextActivity]
      if (!duration) return
      this._clipTimer = setTimeout(() => {
        this._clipTimer = null
        this.triggerEvent('clipcomplete', {
          agentId: nextAgent,
          activity: nextActivity
        })
      }, duration)
    },

    activityLabel(activity) {
      return {
        idle: '正在等待',
        listening: '正在倾听',
        thinking: '正在思考',
        planning: '正在整理计划',
        speaking: '正在回应',
        presenting: '正在展示结果',
        success: '已经准备好',
        error: '需要重新尝试'
      }[activity] || '正在等待'
    },

    onFrameError() {
      this.clearTransitionTimer()
      this.setData({ imageFailed: true, transitioning: false })
    }
  }
})
