import { defineStore } from 'pinia'
import { Preferences } from '@capacitor/preferences'

const autoplayKey = 'healthmate_voice_autoplay'

export const useVoiceStore = defineStore('voice-preferences', {
  state: () => ({ autoplay: true, loaded: false }),
  actions: {
    async restore() {
      if (this.loaded) return
      try {
        const saved = await Preferences.get({ key: autoplayKey })
        if (saved.value !== null) this.autoplay = saved.value !== 'false'
      } catch {
        this.autoplay = true
      } finally {
        this.loaded = true
      }
    },
    async setAutoplay(value: boolean) {
      this.autoplay = value
      await Preferences.set({ key: autoplayKey, value: String(value) })
    },
  },
})
