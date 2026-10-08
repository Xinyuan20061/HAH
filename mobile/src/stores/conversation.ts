import { defineStore } from 'pinia'
import type { AgentResult } from '../services/api'
import type { AgentId } from './companion'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  agentId: AgentId
  content: string
  runId?: number
  result?: AgentResult
  error?: boolean
}

export const useConversationStore = defineStore('conversation', {
  state: () => ({
    messages: [] as ChatMessage[],
  }),
  actions: {
    clear() {
      this.messages = []
    },
    addMessage(message: Omit<ChatMessage, 'id'>) {
      const id = `${message.role}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`
      this.messages.push({ id, ...message })
      return id
    },
    setAnswer(id: string, result: AgentResult) {
      const message = this.messages.find(item => item.id === id)
      if (!message) return
      message.content = String(result.reply || '')
      message.runId = Number(result.run_id) || undefined
      message.result = result
      message.error = false
    },
    setError(id: string, message: string) {
      const entry = this.messages.find(item => item.id === id)
      if (!entry) return
      entry.content = message
      entry.error = true
    },
  },
})
