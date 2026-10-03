const config = require('../config/index')

function isCloud() { return config.getTransport() === 'cloudrun' }
function baseUrl() { return config.getApiBaseUrl() }
function httpBaseUrl() { return config.getHttpApiBaseUrl() }
function headers(extra = {}) {
  const token = wx.getStorageSync('token')
  const base = { 'content-type': 'application/json' }
  if (token) base.Authorization = `Bearer ${token}`
  return Object.assign(base, extra)
}
function detailMessage(detail) {
  if (!detail) return ''
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map(x => x && (x.msg || x.message) ? (x.msg || x.message) : JSON.stringify(x)).join('；')
  if (detail.message) return detail.message
  try { return JSON.stringify(detail) } catch (e) { return String(detail) }
}
/**
 * Normalise every failure shape into one object the pages can branch on.
 *
 * The backend answers business errors with the unified envelope
 * `{ error: { code, message, retryable, request_id, details } }` (spec §5.2);
 * older endpoints may still answer `{ detail: ... }`. Pages switch on `code`
 * (for example DIET_RECORD_VERSION_CONFLICT) instead of matching message text.
 */
function httpError(res, url) {
  const body = (res && res.data) || {}
  const envelope = body.error && typeof body.error === 'object' ? body.error : null
  const status = res && res.statusCode
  let code = envelope ? envelope.code : (body.code || null)
  let message = envelope
    ? envelope.message
    : (body.message || detailMessage(body.detail || body.data) || `接口请求失败（HTTP ${status || 'unknown'}）`)
  let details = envelope ? (envelope.details || {}) : (body.details || {})
  let retryable = envelope ? !!envelope.retryable : status >= 500
  if (!envelope && status === 401) message = '登录状态失效，正在尝试重新登录'
  if (!code && status === 401) code = 'UNAUTHENTICATED'
  return {
    statusCode: status,
    code: typeof code === 'string' ? code : null,
    message: message || '请求失败',
    retryable,
    details,
    detail: body,
    requestId: envelope ? envelope.request_id : body.request_id,
    url
  }
}
function networkError(err, url) {
  const root = baseUrl()
  const hint = isCloud()
    ? '请确认 CLOUD_ENV_ID、云托管服务名和服务版本已正确配置。'
    : '请确认 FastAPI 已启动；真机调试不能使用 127.0.0.1。'
  return { statusCode: 0, message: `无法连接 HealthMate 后端：${root}。${hint}${err && err.errMsg ? `\n${err.errMsg}` : ''}`, errMsg: err && err.errMsg, url }
}

function cloudCall({ path, method = 'GET', data = {}, timeout, header = {}, responseType }) {
  return new Promise((resolve, reject) => {
    if (!wx.cloud || !wx.cloud.callContainer) return reject({ message: '当前基础库不支持 wx.cloud.callContainer' })
    wx.cloud.callContainer({
      config: { env: config.CLOUD_ENV_ID },
      path,
      method,
      data,
      timeout: timeout || config.REQUEST_TIMEOUT,
      responseType,
      header: Object.assign({ 'X-WX-SERVICE': config.CLOUDRUN_SERVICE_NAME }, header),
      success: resolve,
      fail: reject
    })
  })
}

let refreshing = null
function directLogin() {
  if (refreshing) return refreshing
  refreshing = new Promise(async (resolve, reject) => {
    try {
      if (isCloud()) {
        // Public HTTPS ingress is enabled for the laptop AI Worker. Therefore user login does
        // not trust forgeable public headers; use a one-time wx.login code and exchange it server-side.
        return wx.login({
          success: async r => {
            try {
              const res = await cloudCall({ path: '/api/v1/auth/wechat', method: 'POST', data: { code: r.code }, header: { 'content-type': 'application/json' } })
              if (res.statusCode >= 200 && res.statusCode < 300 && res.data && res.data.access_token) {
                wx.setStorageSync('token', res.data.access_token); if (res.data.user && res.data.user.id) wx.setStorageSync('healthmate_user_id', res.data.user.id); return resolve(true)
              }
              reject(httpError(res, '/auth/wechat'))
            } catch (e) { reject(networkError(e, '/auth/wechat')) }
          },
          fail: e => reject({ message: e.errMsg || '微信登录失败' })
        })
      }
      const doPost = (path, data) => wx.request({
        url: httpBaseUrl() + path, method: 'POST', data, timeout: config.REQUEST_TIMEOUT,
        header: { 'content-type': 'application/json' },
        success: res => {
          if (res.statusCode >= 200 && res.statusCode < 300 && res.data && res.data.access_token) { wx.setStorageSync('token', res.data.access_token); if (res.data.user && res.data.user.id) wx.setStorageSync('healthmate_user_id', res.data.user.id); resolve(true) }
          else reject(httpError(res, path))
        }, fail: e => reject(networkError(e, path))
      })
      if (config.DEV_LOGIN) return doPost('/auth/dev-login', { nickname: '' })
      wx.login({ success: r => doPost('/auth/wechat', { code: r.code }), fail: e => reject({ message: e.errMsg || '微信登录失败' }) })
    } catch (e) { reject(networkError(e, '/auth/wechat')) }
  }).finally(() => { refreshing = null })
  return refreshing
}
async function ensureToken() { if (wx.getStorageSync('token')) return true; return directLogin() }

const CACHE_PREFIX = 'healthmate_cache:'
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
function cacheGet(url, maxAgeMs = 6 * 60 * 60 * 1000) {
  try { const x = wx.getStorageSync(CACHE_PREFIX + (wx.getStorageSync('healthmate_user_id') || 'anonymous') + ':' + url); return x && x.savedAt && Date.now() - x.savedAt <= maxAgeMs ? x.data : null } catch (e) { return null }
}
function cacheSet(url, data) { try { wx.setStorageSync(CACHE_PREFIX + (wx.getStorageSync('healthmate_user_id') || 'anonymous') + ':' + url, { savedAt: Date.now(), data }) } catch (e) {} }

async function request({ url, method = 'GET', data = {}, timeout, retried = false, networkRetry = 0, allowCache = false, headers: extraHeaders = {} }) {
  try {
    let res
    if (isCloud()) {
      res = await cloudCall({ path: '/api/v1' + url, method, data, timeout, header: Object.assign(headers(), extraHeaders) })
    } else {
      res = await new Promise((resolve, reject) => wx.request({
        url: httpBaseUrl() + url, method, data, timeout: timeout || config.REQUEST_TIMEOUT, header: Object.assign(headers(), extraHeaders), success: resolve, fail: reject
      }))
    }
    if (res.statusCode >= 200 && res.statusCode < 300) { if (method === 'GET' && allowCache) cacheSet(url, res.data); return res.data }
    if (res.statusCode === 401 && !retried && !url.startsWith('/auth/')) {
      wx.removeStorageSync('token'); await directLogin(); return request({ url, method, data, timeout, retried: true, networkRetry, allowCache, headers: extraHeaders })
    }
    throw httpError(res, url)
  } catch (e) {
    if (e && e.statusCode) throw e
    const safeRetry = method === 'GET' || ['/media/register-cloud', '/media/motion-jobs', '/vision/food-jobs'].includes(url)
    if (safeRetry && networkRetry < 2) { await sleep(350 * Math.pow(2, networkRetry)); return request({ url, method, data, timeout, retried, networkRetry: networkRetry + 1, allowCache, headers: extraHeaders }) }
    if (method === 'GET' && allowCache) { const cached = cacheGet(url); if (cached !== null) return cached }
    throw networkError(e, url)
  }
}

function upload(url, filePath, name = 'file', retried = false) {
  if (isCloud()) return Promise.reject({ message: '云托管模式请使用 wx.cloud.uploadFile，再注册媒体资产。' })
  return new Promise((resolve, reject) => wx.uploadFile({
    url: httpBaseUrl() + url, filePath, name, timeout: 120000, header: headers(),
    success: async res => {
      let data = {}; try { data = JSON.parse(res.data || '{}') } catch (e) {}
      if (res.statusCode >= 200 && res.statusCode < 300) return resolve(data)
      if (res.statusCode === 401 && !retried) {
        try { wx.removeStorageSync('token'); await directLogin(); return resolve(await upload(url, filePath, name, true)) }
        catch (e) { return reject(e) }
      }
      reject(httpError({ statusCode: res.statusCode, data }, url))
    },
    fail: e => reject(networkError(e, url))
  }))
}

function parseChunk(onMeta, onDelta, onDone, onStage) {
  let textBuffer = '', decoder = typeof TextDecoder !== 'undefined' ? new TextDecoder('utf-8') : null
  const decode = ab => { if (decoder) return decoder.decode(new Uint8Array(ab), { stream: true }); const u = new Uint8Array(ab); let s = ''; for (let i = 0; i < u.length; i++) s += String.fromCharCode(u[i]); try { return decodeURIComponent(escape(s)) } catch (e) { return s } }
  return data => {
    textBuffer += decode(data); const lines = textBuffer.split('\n'); textBuffer = lines.pop() || ''
    lines.forEach(line => {
      if (!line.trim()) return
      try {
        const x = JSON.parse(line)
        // Stage events are real pipeline progress (spec §8.7), not fake deltas.
        if (x.type === 'meta') onMeta && onMeta(x)
        if (x.type === 'stage') onStage && onStage(x)
        if (x.type === 'delta') onDelta && onDelta(x.content || '')
        // The final answer arrives only after the safety review has passed.
        if (x.type === 'answer') onDelta && onDelta(x.reply || '')
        if (x.type === 'done') onDone && onDone(x)
      } catch (e) {}
    })
  }
}

function streamPost(url, data, { onMeta, onStage, onDelta, onDone, onError } = {}) {
  // A public Cloud Run HTTPS URL enables true wx.request chunk streaming. Without it,
  // use the private callContainer path and degrade gracefully to a single final chunk.
  if (!config.USE_STREAMING || (isCloud() && !config.PUBLIC_API_BASE_URL)) {
    let cancelled = false
    request({ url: url.replace(/\/stream$/, ''), method: 'POST', data, timeout: 120000, allowCache: false })
      .then(r => {
        if (cancelled) return
        onMeta && onMeta({ session_id: r.session_id, safety_level: r.safety_level, run_id: r.run_id })
        // No stage events are available without streaming: report the honest
        // single-shot path instead of animating a fake pipeline.
        onStage && onStage({ type: 'stage', stage: 'single_shot', status: 'completed', label: '已生成回答' })
        onDelta && onDelta(r.reply || '')
        onDone && onDone({ provider: r.provider, result: r })
      })
      .catch(e => { if (!cancelled) onError && onError(e) })
    return { abort(){ cancelled = true } }
  }
  const root = isCloud() ? config.PUBLIC_API_BASE_URL.replace(/\/+$/, '') + '/api/v1' : httpBaseUrl()
  const consume = parseChunk(onMeta, onDelta, onDone, onStage)
  const task = wx.request({ url: root + url, method: 'POST', data, enableChunked: true, responseType: 'arraybuffer', header: headers(), timeout: 60000,
    success: res => { if (res.statusCode < 200 || res.statusCode >= 300) onError && onError(httpError(res, url)) }, fail: e => onError && onError(networkError(e, url)) })
  if (task.onChunkReceived) task.onChunkReceived(r => consume(r.data))
  else { task.abort(); setTimeout(() => onError && onError({ message: '当前微信基础库不支持分块响应', unsupported: true }), 0) }
  return task
}

async function downloadPost(url, data = {}, retried = false) {
  if (isCloud()) {
    const res = await cloudCall({ path: '/api/v1' + url, method: 'POST', data, timeout: 120000, header: headers(), responseType: 'arraybuffer' })
    if (res.statusCode === 401 && !retried) { wx.removeStorageSync('token'); await directLogin(); return downloadPost(url, data, true) }
    if (res.statusCode < 200 || res.statusCode >= 300) throw httpError(res, url)
    const path = `${wx.env.USER_DATA_PATH}/healthmate-export-${Date.now()}.zip`
    await new Promise((resolve, reject) => wx.getFileSystemManager().writeFile({ filePath: path, data: res.data, success: resolve, fail: reject }))
    return path
  }
  return new Promise((resolve, reject) => wx.request({
    url: httpBaseUrl() + url, method: 'POST', data, responseType: 'arraybuffer', timeout: 120000, header: headers(),
    success: async res => {
      if (res.statusCode === 401 && !retried) {
        try { wx.removeStorageSync('token'); await directLogin(); return resolve(await downloadPost(url, data, true)) }
        catch (e) { return reject(e) }
      }
      if (res.statusCode < 200 || res.statusCode >= 300) return reject(httpError(res, url))
      try { const path = `${wx.env.USER_DATA_PATH}/healthmate-export-${Date.now()}.zip`; wx.getFileSystemManager().writeFile({ filePath: path, data: res.data, success: () => resolve(path), fail: reject }) } catch (e) { reject(e) }
    }, fail: e => reject(networkError(e, url))
  }))
}

async function health() {
  try {
    const res = isCloud()
      ? await cloudCall({ path: '/health', method: 'GET', timeout: 5000, header: { 'X-WX-SERVICE': config.CLOUDRUN_SERVICE_NAME } })
      : await new Promise((resolve, reject) => wx.request({ url: httpBaseUrl().replace(/\/api\/v1\/?$/, '') + '/health', method: 'GET', timeout: 5000, success: resolve, fail: reject }))
    if (res.statusCode >= 200 && res.statusCode < 300) return res.data
    throw httpError(res, '/health')
  } catch (e) { if (e.statusCode) throw e; throw networkError(e, '/health') }
}

/** Create a record/job idempotently. The key must be unique per user action. */
function idempotencyKey(scope) {
  const suffix = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`
  return `${scope}-${suffix}`.slice(0, 120)
}

module.exports = {
  get: (url, opts={}) => request({ url, ...opts }),
  post: (url, data, headers) => request({ url, method: 'POST', data, headers }),
  postIdempotent: (url, data, scope) =>
    request({ url, method: 'POST', data, headers: { 'Idempotency-Key': idempotencyKey(scope || 'post') } }),
  postLong: (url, data) => request({ url, method: 'POST', data, timeout: 120000 }),
  patch: (url, data, headers) => request({ url, method: 'PATCH', data, headers }),
  put: (url, data) => request({ url, method: 'PUT', data }),
  del: (url, data={}, headers) => request({ url, method: 'DELETE', data, headers }),
  downloadPost, upload, streamPost, health, ensureToken, idempotencyKey,
  getBaseUrl: baseUrl, isCloud, getTransport: config.getTransport,
  setBaseUrl: config.setApiBaseUrl, clearBaseUrl: config.clearApiBaseUrl
}
