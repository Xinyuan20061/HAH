// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { reactive } from 'vue'
import { mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  cancelAgentRun: vi.fn(),
  readRun: vi.fn(),
  requestAgentResponse: vi.fn(),
  synthesizeVoice: vi.fn(),
  transcribeVoice: vi.fn(),
  cancelVoiceCapture: vi.fn(),
  listenForVoiceCaptureInterruption: vi.fn(),
  startVoiceCapture: vi.fn(),
  stopVoiceCapture: vi.fn(),
  route: { query: { agent: 'xiaojian' } },
  router: { push: vi.fn(), back: vi.fn() },
  companion: { agentId: 'xiaojian', choose: vi.fn() },
  voicePreference: { autoplay: false, restore: vi.fn() },
  conversation: null,
}))

vi.mock('@capacitor/app', () => ({
  App: { addListener: vi.fn(async () => ({ remove: vi.fn() })) },
}))
vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
  useRouter: () => mocks.router,
}))
vi.mock('../../src/components/CharacterStage.vue', () => ({ default: { template: '<div />' } }))
vi.mock('../../src/services/api', () => ({
  cancelAgentRun: mocks.cancelAgentRun,
  readRun: mocks.readRun,
  requestAgentResponse: mocks.requestAgentResponse,
  synthesizeVoice: mocks.synthesizeVoice,
  transcribeVoice: mocks.transcribeVoice,
}))
vi.mock('../../src/services/nativeVoice', () => ({
  cancelVoiceCapture: mocks.cancelVoiceCapture,
  listenForVoiceCaptureInterruption: mocks.listenForVoiceCaptureInterruption,
  startVoiceCapture: mocks.startVoiceCapture,
  stopVoiceCapture: mocks.stopVoiceCapture,
}))
vi.mock('../../src/stores/auth', () => ({ useAuthStore: () => ({ accessToken: 'test-access-token' }) }))
vi.mock('../../src/stores/companion', () => ({ useCompanionStore: () => mocks.companion }))
vi.mock('../../src/stores/conversation', () => ({ useConversationStore: () => mocks.conversation }))
vi.mock('../../src/stores/voice', () => ({ useVoiceStore: () => mocks.voicePreference }))

import ChatPage from '../../src/pages/ChatPage.vue'

describe('ChatPage request cancellation', () => {
  let wrapper
  let messages
  let nextId

  beforeEach(() => {
    messages = reactive([])
    nextId = 0
    mocks.route.query = { agent: 'xiaojian' }
    mocks.router.push.mockReset()
    mocks.companion.choose.mockReset().mockResolvedValue(undefined)
    mocks.voicePreference.restore.mockReset().mockResolvedValue(undefined)
    mocks.cancelAgentRun.mockReset().mockResolvedValue({ status: 'cancelled' })
    mocks.readRun.mockReset().mockResolvedValue({ status: 'cancelled' })
    mocks.synthesizeVoice.mockReset()
    mocks.transcribeVoice.mockReset()
    mocks.cancelVoiceCapture.mockReset().mockResolvedValue(undefined)
    mocks.listenForVoiceCaptureInterruption.mockReset().mockResolvedValue({ remove: vi.fn() })
    mocks.startVoiceCapture.mockReset().mockResolvedValue(undefined)
    mocks.stopVoiceCapture.mockReset().mockResolvedValue({ audio_base64: '', duration_ms: 0 })
    mocks.conversation = {
      messages,
      addMessage(message) {
        const id = `message-${++nextId}`
        messages.push({ ...message, id })
        return id
      },
      setAnswer(id, result) {
        const entry = messages.find(message => message.id === id)
        if (entry) entry.content = result.reply || ''
      },
      setError(id, content) {
        const entry = messages.find(message => message.id === id)
        if (entry) Object.assign(entry, { content, error: true })
      },
    }
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
  })

  async function startPendingRequest() {
    let requestSignal
    mocks.requestAgentResponse.mockImplementation((_token, _request, _onEvent, signal) => {
      requestSignal = signal
      return new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
      })
    })
    wrapper = mount(ChatPage)
    await wrapper.find('textarea').setValue('帮我整理本周训练')
    await wrapper.find('form').trigger('submit')
    await vi.waitFor(() => expect(mocks.requestAgentResponse).toHaveBeenCalledOnce())
    return {
      onEvent: mocks.requestAgentResponse.mock.calls[0][2],
      signal: requestSignal,
    }
  }

  it('stops display immediately, then cancels and rereads a known server run', async () => {
    const pending = await startPendingRequest()
    pending.onEvent({ type: 'meta', run_id: 73 })

    await wrapper.find('[aria-label="停止接收回答"]').trigger('click')

    expect(pending.signal.aborted).toBe(true)
    await vi.waitFor(() => expect(mocks.cancelAgentRun).toHaveBeenCalledWith('test-access-token', 73))
    await vi.waitFor(() => expect(mocks.readRun).toHaveBeenCalledWith('test-access-token', 73))
    expect(wrapper.text()).toContain('已停止接收回答')
    expect(messages.at(-1)).toMatchObject({ role: 'assistant', error: true })
  })

  it('does not invent a server cancellation when no run id arrived', async () => {
    const pending = await startPendingRequest()

    await wrapper.find('[aria-label="停止接收回答"]').trigger('click')

    expect(pending.signal.aborted).toBe(true)
    await vi.waitFor(() => expect(messages.at(-1)?.error).toBe(true))
    expect(mocks.cancelAgentRun).not.toHaveBeenCalled()
    expect(mocks.readRun).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('请求可能已经在服务端完成')
  })

  it('still checks the run state when the best-effort cancel request fails', async () => {
    const pending = await startPendingRequest()
    pending.onEvent({ type: 'meta', run_id: 74 })
    mocks.cancelAgentRun.mockRejectedValueOnce(new Error('network timeout'))

    await wrapper.find('[aria-label="停止接收回答"]').trigger('click')

    await vi.waitFor(() => expect(mocks.readRun).toHaveBeenCalledWith('test-access-token', 74))
    expect(mocks.cancelAgentRun).toHaveBeenCalledWith('test-access-token', 74)
  })
})
