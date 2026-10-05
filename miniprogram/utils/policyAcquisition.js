const api = require('./request')

function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(',')}}`
  }
  return JSON.stringify(value)
}

function clone(value) { return JSON.parse(JSON.stringify(value)) }

function createElapsedClock(storage, maxElapsedMs = 600000) {
  if (!storage || typeof storage.get !== 'function' || typeof storage.set !== 'function' ||
      typeof storage.remove !== 'function') throw new Error('计时器缺少存储依赖')
  const limit = Number.isInteger(maxElapsedMs) && maxElapsedMs >= 0 ? maxElapsedMs : 600000
  const keyFor = (sessionId, itemId) =>
    `healthmate_acquisition_clock:${String(sessionId)}:${String(itemId)}`
  const read = key => {
    const value = storage.get(key)
    return value && Number.isFinite(value.active_ms) && value.active_ms >= 0
      ? { active_ms: Math.min(limit, Math.floor(value.active_ms)),
        started_at: Number.isFinite(value.started_at) ? value.started_at : null }
      : { active_ms: 0, started_at: null }
  }
  const elapsed = (state, now) => Math.min(limit, state.active_ms +
    (state.started_at === null ? 0 : Math.max(0, now - state.started_at)))

  return {
    start(sessionId, itemId, now = Date.now()) {
      const key = keyFor(sessionId, itemId)
      const state = read(key)
      if (state.started_at === null && state.active_ms < limit) state.started_at = now
      storage.set(key, state)
      return elapsed(state, now)
    },
    pause(sessionId, itemId, now = Date.now()) {
      const key = keyFor(sessionId, itemId)
      const state = read(key)
      if (state.started_at !== null) {
        state.active_ms = elapsed(state, now)
        state.started_at = null
        storage.set(key, state)
      }
      return state.active_ms
    },
    elapsed(sessionId, itemId, now = Date.now()) {
      return elapsed(read(keyFor(sessionId, itemId)), now)
    },
    clear(sessionId, itemId) { storage.remove(keyFor(sessionId, itemId)) }
  }
}

function createPolicyAcquisitionClient({ get, post, idempotencyKey, storage }) {
  if (!get || !post || !idempotencyKey || !storage) throw new Error('取证请求客户端缺少依赖')
  const keyFor = scope => `healthmate_acquisition_command:${scope}`

  function pending(scope) {
    const saved = storage.get(keyFor(scope))
    return saved ? clone(saved) : null
  }

  function clear(scope) { storage.remove(keyFor(scope)) }

  async function command(scope, url, body) {
    const storageKey = keyFor(scope)
    let saved = storage.get(storageKey)
    const safeBody = clone(body || {})
    if (saved && stableJson(saved.body) !== stableJson(safeBody)) {
      const error = new Error('上次操作结果未确认。为避免重复写入，请先使用保存的原答复重试。')
      error.code = 'ACQUISITION_RETRY_BODY_LOCKED'
      error.pendingBody = clone(saved.body)
      throw error
    }
    if (!saved) {
      saved = { key: idempotencyKey('acquisition'), body: safeBody, created_at: Date.now() }
      storage.set(storageKey, saved)
    }
    try {
      const result = await post(url, clone(saved.body), { 'Idempotency-Key': saved.key })
      clear(scope)
      return result
    } catch (error) {
      const status = Number(error && error.statusCode)
      if (status && status < 500 && status !== 408 && status !== 429) clear(scope)
      throw error
    }
  }

  return {
    getSession(sessionId) { return get(`/policy/acquisition/sessions/${sessionId}`, { allowCache: false }) },
    getHistory(episodeId) { return get(`/policy/episodes/${episodeId}/acquisition/history`, { allowCache: false }) },
    getRepairTargets(episodeId) { return get(`/policy/episodes/${episodeId}/observation-repair-targets`, { allowCache: false }) },
    getRereviewContext(episodeId) { return get(`/policy/episodes/${episodeId}/rereview-context`, { allowCache: false }) },
    getPending: pending,
    clearPending: clear,
    start(episodeId, body) {
      return command(`start:${episodeId}:${body.expected_episode_version}`,
        `/policy/episodes/${episodeId}/acquisition/sessions`, body)
    },
    next(sessionId, body) {
      return command(`next:${sessionId}:${body.expected_session_version}:${body.expected_episode_version}`,
        `/policy/acquisition/sessions/${sessionId}/next`, body)
    },
    answer(sessionId, questionId, body) {
      return command(`answer:${sessionId}:${questionId}:${body.expected_session_version}:${body.expected_episode_version}`,
        `/policy/acquisition/sessions/${sessionId}/questions/${questionId}/answer`, body)
    },
    pause(sessionId, body) {
      return command(`pause:${sessionId}:${body.expected_session_version}`,
        `/policy/acquisition/sessions/${sessionId}/pause`, body)
    },
    resume(sessionId, body) {
      return command(`resume:${sessionId}:${body.expected_session_version}:${body.expected_episode_version}`,
        `/policy/acquisition/sessions/${sessionId}/resume`, body)
    },
    repair(sessionId, body) {
      return command(`repair:${sessionId}`,
        `/policy/acquisition/sessions/${sessionId}/repair`, body)
    },
    repairObservation(episodeId, body) {
      return command(`observation-repair:${episodeId}:${body.observation_ref_id}:${body.expected_episode_version}:${body.expected_observation_revision}`,
        `/policy/episodes/${episodeId}/observation-repairs`, body)
    },
    proposeRereview(episodeId, body) {
      return command(`rereview-proposal:${episodeId}:${body.expected_episode_version}:${body.expected_adjudication_revision}`,
        `/policy/episodes/${episodeId}/rereview-proposal`, body)
    },
    previewRereview(episodeId, body) {
      return post(`/policy/episodes/${episodeId}/rereview-preview`, body)
    }
  }
}

const defaultStorage = {
  get(key) { return wx.getStorageSync(key) || null },
  set(key, value) { wx.setStorageSync(key, value) },
  remove(key) { wx.removeStorageSync(key) }
}

const elapsedClock = createElapsedClock(defaultStorage)

const client = createPolicyAcquisitionClient({
  get: (url, options) => api.get(url, options),
  post: (url, body, headers) => api.post(url, body, headers),
  idempotencyKey: scope => api.idempotencyKey(scope),
  storage: defaultStorage
})

module.exports = Object.assign({ createPolicyAcquisitionClient, createElapsedClock, elapsedClock, stableJson }, client)
