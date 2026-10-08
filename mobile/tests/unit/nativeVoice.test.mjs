import { beforeEach, describe, expect, it, vi } from 'vitest'

const { plugin, listenerHandle, runtime } = vi.hoisted(() => ({
  listenerHandle: { remove: vi.fn(async () => undefined) },
  plugin: { startRecording: vi.fn(), stopRecording: vi.fn(), cancelRecording: vi.fn(), addListener: vi.fn() },
  runtime: { native: true },
}))

vi.mock('@capacitor/core', () => ({
  Capacitor: { isNativePlatform: () => runtime.native },
  registerPlugin: () => plugin,
}))

import { listenForVoiceCaptureInterruption } from '../../src/services/nativeVoice.ts'

describe('native voice interruption events', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    runtime.native = true
    plugin.addListener.mockResolvedValue(listenerHandle)
  })

  it('forwards Android audio focus interruptions and returns a removable listener', async () => {
    const onInterrupt = vi.fn()
    const registration = await listenForVoiceCaptureInterruption(onInterrupt)
    const nativeListener = plugin.addListener.mock.calls[0][1]
    nativeListener({ reason: 'audio_focus_lost' })

    expect(plugin.addListener).toHaveBeenCalledWith('recordingInterrupted', expect.any(Function))
    expect(onInterrupt).toHaveBeenCalledWith('audio_focus_lost')
    expect(registration).toBe(listenerHandle)
  })

  it('does not register native listeners in a browser', async () => {
    runtime.native = false
    await expect(listenForVoiceCaptureInterruption(vi.fn())).resolves.toBeNull()
    expect(plugin.addListener).not.toHaveBeenCalled()
  })
})
