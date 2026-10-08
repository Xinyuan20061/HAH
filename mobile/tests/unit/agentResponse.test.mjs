import { afterEach, describe, expect, it, vi } from 'vitest'
import { cancelAgentRun, requestAgentResponse } from '../../src/services/api.ts'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

const request = { message: '帮我整理计划', agent_id: 'xiaojian', channel: 'text' }

describe('Agent response transport', () => {
  it('cancels a known run through the authenticated run lifecycle endpoint', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://api.example.test/api/v1')
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      run_id: 43,
      status: 'cancelled',
      cancelled_stages: 1,
      note: '已停止未开始的后续阶段。',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(cancelAgentRun('access-token', 43)).resolves.toMatchObject({
      run_id: 43,
      status: 'cancelled',
    })

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0][0]).toBe('https://api.example.test/api/v1/agent/runs/43/cancel')
    expect(fetchMock.mock.calls[0][1].method).toBe('POST')
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe('Bearer access-token')
  })

  it('uses one full-response request when the gateway cannot stream NDJSON', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://api.example.test/api/v1')
    vi.stubEnv('VITE_AGENT_RESPONSE_MODE', 'full')
    const result = { run_id: 41, reply: '先从轻量训练开始。' }
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(result), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)
    const onEvent = vi.fn()

    await expect(requestAgentResponse('access-token', request, onEvent, new AbortController().signal))
      .resolves.toEqual(result)

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0][0]).toBe('https://api.example.test/api/v1/agent/respond')
    expect(fetchMock.mock.calls[0][1].method).toBe('POST')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(request)
    expect(onEvent).not.toHaveBeenCalled()
  })

  it('keeps NDJSON streaming as the default transport and returns its final result', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://api.example.test/api/v1')
    vi.stubEnv('VITE_AGENT_RESPONSE_MODE', 'ndjson')
    const result = { run_id: 42, reply: '收到。' }
    const events = [
      { type: 'meta', run_id: 42 },
      { type: 'done', result },
    ].map(event => JSON.stringify(event)).join('\n') + '\n'
    const fetchMock = vi.fn(async () => new Response(events, {
      status: 200,
      headers: { 'Content-Type': 'application/x-ndjson' },
    }))
    vi.stubGlobal('fetch', fetchMock)
    const onEvent = vi.fn()

    await expect(requestAgentResponse('access-token', request, onEvent, new AbortController().signal))
      .resolves.toEqual(result)

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0][0]).toBe('https://api.example.test/api/v1/agent/respond/stream')
    expect(fetchMock.mock.calls[0][1].headers.Accept).toBe('application/x-ndjson')
    expect(onEvent.mock.calls.map(([event]) => event.type)).toEqual(['meta', 'done'])
  })
})
