import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiRequest, checkBackend, readCurrentUser, registerUnauthorizedHandler } from '../../src/services/api.ts'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

function stalledFetch(_url, { signal } = {}) {
  return new Promise((_, reject) => {
    signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
  })
}

describe('startup connection timeout', () => {
  it('ends a stalled backend health check with a retryable message', async () => {
    vi.useFakeTimers()
    vi.stubEnv('VITE_API_BASE_URL', 'https://api.example.test/api/v1')
    const fetchMock = vi.fn(stalledFetch)
    vi.stubGlobal('fetch', fetchMock)

    const failure = expect(checkBackend()).rejects.toThrow('连接超时，请检查网络后重试。')
    await vi.advanceTimersByTimeAsync(12_000)
    await failure
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true)
  })

  it('ends a stalled restored-session check so app startup can continue', async () => {
    vi.useFakeTimers()
    vi.stubEnv('VITE_API_BASE_URL', 'https://api.example.test/api/v1')
    const fetchMock = vi.fn(stalledFetch)
    vi.stubGlobal('fetch', fetchMock)

    const failure = expect(readCurrentUser('saved-token')).rejects.toThrow('连接超时，请检查网络后重试。')
    await vi.advanceTimersByTimeAsync(12_000)
    await failure
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true)
  })
})

describe('authenticated API recovery', () => {
  it('coalesces parallel 401 recovery and never replays a protected write', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://api.example.test/api/v1')
    const unauthorized = () => new Response(JSON.stringify({
      error: { code: 'UNAUTHENTICATED', message: '登录已过期', request_id: 'req-401' },
    }), { status: 401, headers: { 'Content-Type': 'application/json' } })
    const fetchMock = vi.fn().mockImplementation(async () => unauthorized())
    vi.stubGlobal('fetch', fetchMock)

    let finishRecovery
    const recoveryGate = new Promise(resolve => { finishRecovery = resolve })
    const recover = vi.fn(() => recoveryGate)
    const unregister = registerUnauthorizedHandler(recover)
    const token = `expired-token-${crypto.randomUUID()}`

    try {
      const read = apiRequest('/health/today', token).catch(error => error)
      const write = apiRequest('/health/goals', token, { method: 'PUT', body: '{}' }).catch(error => error)

      await vi.waitFor(() => expect(recover).toHaveBeenCalledTimes(1))
      finishRecovery()
      const [readError, writeError] = await Promise.all([read, write])

      expect(readError).toMatchObject({ status: 401, requestId: 'req-401' })
      expect(writeError).toMatchObject({ status: 401, requestId: 'req-401' })
      expect(fetchMock).toHaveBeenCalledTimes(2)
      expect(fetchMock.mock.calls.map(([url, init]) => [url, init.method ?? 'GET'])).toEqual([
        ['https://api.example.test/api/v1/health/today', 'GET'],
        ['https://api.example.test/api/v1/health/goals', 'PUT'],
      ])
      expect(recover).toHaveBeenCalledTimes(1)
    } finally {
      unregister()
    }
  })
})
