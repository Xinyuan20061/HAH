const HOLD_MS = 320
const FADE_MS = 180

Component({
  data: {
    // Keep the veil node mounted permanently. Its transparent idle state is
    // cheaper and, crucially, cannot miss the first frame of a cached page.
    leaving: true,
    paused: true
  },

  lifetimes: {
    attached() {
      // The initial route is covered by the compile-time WXML include. Waiting
      // for a custom component to attach is exactly what caused the one-frame
      // flash, so this component only handles later cached-page re-entries.
      this.hasShownOnce = false
    },
    detached() {
      this.clearTimers()
    }
  },

  pageLifetimes: {
    show() {
      if (!this.hasShownOnce) {
        this.hasShownOnce = true
        return
      }
      this.play()
    },
    hide() {
      this.clearTimers()
      // Arm the veil while the page is hidden. When a cached tab/page is shown
      // again, its very first composited frame is already covered; mounting it
      // from show() would leave one uncovered frame on slower devices.
      this.setData({ leaving: false, paused: true })
    }
  },

  methods: {
    clearTimers() {
      if (this.leaveTimer) clearTimeout(this.leaveTimer)
      if (this.settleTimer) clearTimeout(this.settleTimer)
      this.leaveTimer = null
      this.settleTimer = null
    },

    play() {
      this.clearTimers()
      this.setData({ leaving: false, paused: false })
      this.leaveTimer = setTimeout(() => {
        this.setData({ leaving: true })
      }, HOLD_MS)
      this.settleTimer = setTimeout(() => {
        this.setData({ paused: true })
      }, HOLD_MS + FADE_MS)
    }
  }
})
