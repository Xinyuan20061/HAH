import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@capacitor/app', () => ({ App: { addListener: vi.fn(), getState: vi.fn() } }))
vi.mock('@capacitor/core', () => ({ Capacitor: { isNativePlatform: vi.fn() } }))

import { App } from '@capacitor/app'
import { Capacitor } from '@capacitor/core'
import { waitForAppForeground } from '../../src/services/appLifecycle.ts'

const getState = vi.mocked(App.getState)
const addListener = vi.mocked(App.addListener)
const isNativePlatform = vi.mocked(Capacitor.isNativePlatform)

describe('foreground task gate', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    isNativePlatform.mockReturnValue(true)
    getState.mockResolvedValue({ isActive: true })
  })

  afterEach(() => vi.restoreAllMocks())

  it('does not wait in a browser or while the app is already active', async () => {
    isNativePlatform.mockReturnValue(false)
    await waitForAppForeground()
    expect(getState).not.toHaveBeenCalled()

    isNativePlatform.mockReturnValue(true)
    await waitForAppForeground()
    expect(getState).toHaveBeenCalledOnce()
    expect(addListener).not.toHaveBeenCalled()
  })

  it('waits through background and resumes when the app becomes active', async () => {
    getState.mockResolvedValueOnce({ isActive: false }).mockResolvedValue({ isActive: false })
    let stateListener
    const remove = vi.fn(async () => undefined)
    addListener.mockImplementation(async (_event, listener) => {
      stateListener = listener
      return { remove }
    })

    let resumed = false
    const waiting = waitForAppForeground().then(() => { resumed = true })
    await vi.waitFor(() => expect(getState).toHaveBeenCalledTimes(2))
    expect(resumed).toBe(false)
    stateListener?.({ isActive: true })
    await waiting

    expect(resumed).toBe(true)
    expect(remove).toHaveBeenCalledOnce()
  })

  it('removes the foreground listener when its page is destroyed', async () => {
    getState.mockResolvedValueOnce({ isActive: false }).mockResolvedValue({ isActive: false })
    const remove = vi.fn(async () => undefined)
    addListener.mockResolvedValue({ remove })
    const controller = new AbortController()

    const waiting = waitForAppForeground(controller.signal)
    await vi.waitFor(() => expect(getState).toHaveBeenCalledTimes(2))
    controller.abort()
    await waiting

    expect(remove).toHaveBeenCalledOnce()
  })
})
