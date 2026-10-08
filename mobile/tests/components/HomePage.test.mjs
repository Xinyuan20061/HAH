// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  cancelAgentRun: vi.fn(),
  readRun: vi.fn(),
  readCommandCenter: vi.fn(),
  requestAgentResponse: vi.fn(),
  transcribeVoice: vi.fn(),
  synthesizeVoice: vi.fn(),
  startVoiceCapture: vi.fn(),
  stopVoiceCapture: vi.fn(),
  cancelVoiceCapture: vi.fn(),
  listenForVoiceCaptureInterruption: vi.fn(),
  choose: vi.fn(),
  addMessage: vi.fn(),
  push: vi.fn(),
  replace: vi.fn(),
  route: { query: {} },
  appStateChange: null,
  companion: {
    agentId: 'xiaojian',
    name: '小健',
    space: '健身房',
    restore: vi.fn(),
    choose: vi.fn(),
  },
}))

vi.mock('@capacitor/app', () => ({
  App: { addListener: vi.fn(async (event, listener) => {
    if (event === 'appStateChange') mocks.appStateChange = listener
    return { remove: vi.fn() }
  }) },
}))
vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
  useRouter: () => ({ push: mocks.push, replace: mocks.replace }),
}))
vi.mock('../../src/services/api', () => ({
  cancelAgentRun: mocks.cancelAgentRun,
  readRun: mocks.readRun,
  readCommandCenter: mocks.readCommandCenter,
  requestAgentResponse: mocks.requestAgentResponse,
  transcribeVoice: mocks.transcribeVoice,
  synthesizeVoice: mocks.synthesizeVoice,
}))
vi.mock('../../src/services/nativeVoice', () => ({
  cancelVoiceCapture: mocks.cancelVoiceCapture,
  listenForVoiceCaptureInterruption: mocks.listenForVoiceCaptureInterruption,
  startVoiceCapture: mocks.startVoiceCapture,
  stopVoiceCapture: mocks.stopVoiceCapture,
}))
vi.mock('../../src/stores/auth', () => ({ useAuthStore: () => ({ accessToken: 'test-token' }) }))
vi.mock('../../src/stores/companion', () => ({ useCompanionStore: () => mocks.companion }))
vi.mock('../../src/stores/conversation', () => ({ useConversationStore: () => ({ addMessage: mocks.addMessage }) }))
vi.mock('../../src/stores/voice', () => ({ useVoiceStore: () => ({ autoplay: false, restore: vi.fn() }) }))

import HomePage from '../../src/pages/HomePage.vue'

describe('HomePage', () => {
  let wrapper

  beforeEach(() => {
    vi.useRealTimers()
    mocks.route.query = {}
    mocks.appStateChange = null
    mocks.cancelAgentRun.mockReset().mockResolvedValue({ status: 'cancelled' })
    mocks.readRun.mockReset().mockResolvedValue({ status: 'cancelled' })
    mocks.readCommandCenter.mockReset().mockResolvedValue({
      date: '2026-10-07',
      streak: {
        current: 4,
        checked_today: true,
        last7: [
          { date: '2026-10-01', label: '四', done: true },
          { date: '2026-10-02', label: '五', done: true },
          { date: '2026-10-03', label: '六', done: false },
          { date: '2026-10-04', label: '日', done: true },
          { date: '2026-10-05', label: '一', done: true },
          { date: '2026-10-06', label: '二', done: false },
          { date: '2026-10-07', label: '三', done: true },
        ],
      },
    })
    mocks.requestAgentResponse.mockReset().mockResolvedValue({
      run_id: 91,
      intent: 'answer',
      reply: '今天先轻松活动十分钟。',
      presentation: { cue: 'answer.present' },
    })
    mocks.transcribeVoice.mockReset().mockResolvedValue({ text: '今天怎么安排运动' })
    mocks.synthesizeVoice.mockReset().mockResolvedValue({ segments: [] })
    mocks.startVoiceCapture.mockReset().mockResolvedValue(undefined)
    mocks.stopVoiceCapture.mockReset().mockResolvedValue({ audio_base64: 'YXVkaW8=', duration_ms: 500 })
    mocks.cancelVoiceCapture.mockReset().mockResolvedValue(undefined)
    mocks.listenForVoiceCaptureInterruption.mockReset().mockResolvedValue({ remove: vi.fn() })
    mocks.companion.agentId = 'xiaojian'
    mocks.companion.name = '小健'
    mocks.companion.space = '健身房'
    mocks.companion.restore.mockReset().mockResolvedValue(undefined)
    mocks.companion.choose.mockReset().mockResolvedValue(undefined)
    mocks.addMessage.mockReset()
    mocks.push.mockReset()
    mocks.replace.mockReset()
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
    vi.useRealTimers()
  })

  it('renders the original command center structure and both companions', async () => {
    wrapper = mount(HomePage)
    await vi.waitFor(() => expect(wrapper.text()).toContain('健身房'))

    expect(wrapper.text()).toContain('10月7日 · 周三')
    expect(wrapper.text()).toContain('4')
    expect(wrapper.findAll('.week-day')).toHaveLength(7)
    expect(wrapper.findAll('.companion-choice')).toHaveLength(2)
    expect(wrapper.find('.voice-button').attributes('aria-label')).toContain('按住')
    expect(wrapper.text()).toContain('状态与下一步')
    expect(wrapper.text()).not.toContain('测试计划路由')
  })

  it('switches the selected companion through the persisted companion store', async () => {
    wrapper = mount(HomePage)
    await vi.waitFor(() => expect(wrapper.findAll('.companion-choice')).toHaveLength(2))
    await wrapper.findAll('.companion-choice')[1].trigger('click')

    expect(mocks.companion.choose).toHaveBeenCalledWith('xiaokang')
  })

  it('records, transcribes and sends a held voice message into the conversation', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-07T10:00:00.000Z'))
    wrapper = mount(HomePage)
    await vi.waitFor(() => expect(wrapper.find('.voice-button').exists()).toBe(true))

    await wrapper.find('.voice-button').trigger('pointerdown')
    await vi.waitFor(() => expect(wrapper.text()).toContain('松开发送'))
    vi.setSystemTime(new Date('2026-10-07T10:00:00.600Z'))
    await wrapper.find('.voice-button').trigger('pointerup')
    await vi.waitFor(() => expect(wrapper.text()).toContain('今天先轻松活动十分钟。'))

    expect(mocks.stopVoiceCapture).toHaveBeenCalledOnce()
    expect(mocks.transcribeVoice).toHaveBeenCalledOnce()
    expect(mocks.requestAgentResponse).toHaveBeenCalledWith(
      'test-token',
      { message: '今天怎么安排运动', agent_id: 'xiaojian', channel: 'voice' },
      expect.any(Function),
      expect.any(AbortSignal),
    )
    expect(mocks.addMessage).toHaveBeenCalledTimes(2)
    expect(wrapper.find('.voice-response').exists()).toBe(true)
  })

  it('cancels a known home voice run when the app moves to the background', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-08T10:00:00.000Z'))
    mocks.requestAgentResponse.mockImplementation((_token, _request, onEvent, signal) => {
      onEvent({ type: 'meta', run_id: 92 })
      return new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
      })
    })
    wrapper = mount(HomePage)
    await vi.waitFor(() => expect(wrapper.find('.voice-button').exists()).toBe(true))
    await vi.waitFor(() => expect(mocks.appStateChange).toBeTypeOf('function'))

    await wrapper.find('.voice-button').trigger('pointerdown')
    await vi.waitFor(() => expect(wrapper.text()).toContain('松开发送'))
    vi.setSystemTime(new Date('2026-10-08T10:00:00.600Z'))
    await wrapper.find('.voice-button').trigger('pointerup')
    await vi.waitFor(() => expect(mocks.requestAgentResponse).toHaveBeenCalledOnce())
    const signal = mocks.requestAgentResponse.mock.calls[0][3]

    await mocks.appStateChange({ isActive: false })

    expect(signal.aborted).toBe(true)
    await vi.waitFor(() => expect(mocks.cancelAgentRun).toHaveBeenCalledWith('test-token', 92))
    await vi.waitFor(() => expect(mocks.readRun).toHaveBeenCalledWith('test-token', 92))
    expect(wrapper.text()).toContain('已停止接收回答')
  })

  it('does not offer a plan action when the response says its write was already applied', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-07T10:00:00.000Z'))
    mocks.requestAgentResponse.mockResolvedValueOnce({
      run_id: 91,
      intent: 'plan',
      safety_level: 'normal',
      reply: '这份草案已经处理过。',
      plan: { items: [{ date_offset: 0, category: 'exercise', title: '散步', description: '轻松散步' }] },
      presentation: {
        navigation: { target: 'plan_preview', mode: 'after_animation', params: { run_id: 91 } },
        write: { status: 'applied', automatic: false, confirmation_required: true },
      },
    })
    wrapper = mount(HomePage)
    await vi.waitFor(() => expect(wrapper.find('.voice-button').exists()).toBe(true))

    await wrapper.find('.voice-button').trigger('pointerdown')
    await vi.waitFor(() => expect(wrapper.text()).toContain('松开发送'))
    vi.setSystemTime(new Date('2026-10-07T10:00:00.600Z'))
    await wrapper.find('.voice-button').trigger('pointerup')
    await vi.waitFor(() => expect(wrapper.text()).toContain('这份草案已经处理过。'))

    expect(wrapper.find('.response-action').exists()).toBe(false)
    expect(mocks.push).not.toHaveBeenCalled()
  })
})
