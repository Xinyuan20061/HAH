import { defineStore } from 'pinia'
import { Preferences } from '@capacitor/preferences'

export type AgentId = 'xiaojian' | 'xiaokang' | 'steward'

const companionKey = 'healthmate_agent_id'

export const useCompanionStore = defineStore('companion', {
  state: () => ({
    agentId: 'xiaojian' as AgentId,
    loaded: false,
  }),
  getters: {
    name: state => ({ xiaojian: '小健', xiaokang: '小康', steward: '小管家' })[state.agentId],
    space: state => ({ xiaojian: '健身房', xiaokang: '养生馆', steward: '健康管理' })[state.agentId],
    portrait: state => `/generated-assets/characters/${state.agentId}-portrait-v1.png`,
  },
  actions: {
    async restore() {
      try {
        const saved = await Preferences.get({ key: companionKey })
        if (saved.value === 'xiaojian' || saved.value === 'xiaokang') this.agentId = saved.value
      } catch {
        // The in-memory default remains available in browser preview.
      } finally {
        this.loaded = true
      }
    },
    async choose(agentId: 'xiaojian' | 'xiaokang') {
      this.agentId = agentId
      await Preferences.set({ key: companionKey, value: agentId })
    },
  },
})
