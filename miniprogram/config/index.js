const DEFAULT_API_BASE_URL = 'http://127.0.0.1:8000/api/v1'
const API_BASE_STORAGE_KEY = 'healthmate_api_base_url'

// Competition deployment: fill these two values after creating the WeChat Cloud Run service.
// When CLOUD_ENV_ID is configured, normal API requests automatically use wx.cloud.callContainer.
const CLOUD_ENV_ID = 'cloud1-d7gv5f8xpf862595e'
const CLOUDRUN_SERVICE_NAME = 'healthmate-api'

// Optional public HTTPS domain of the same Cloud Run service. It enables chunked display-token
// responses after server-side safety validation and is also used by the local GPU worker.
// Leave blank to let chat gracefully fall back to a complete callContainer response.
const PUBLIC_API_BASE_URL = 'https://healthmate-api-317482-12-1491045313.sh.run.tcloudbase.com/api/v1'

function normalizeBaseUrl(value) {
  return String(value || '').trim().replace(/\/+$/, '')
}
function cloudReady() {
  return !!CLOUD_ENV_ID && !CLOUD_ENV_ID.startsWith('YOUR_') && !!CLOUDRUN_SERVICE_NAME
}
function getTransport() { return cloudReady() ? 'cloudrun' : 'http' }
function getApiBaseUrl() {
  if (cloudReady()) return `cloudrun://${CLOUD_ENV_ID}/${CLOUDRUN_SERVICE_NAME}/api/v1`
  try {
    const saved = normalizeBaseUrl(wx.getStorageSync(API_BASE_STORAGE_KEY))
    return saved || DEFAULT_API_BASE_URL
  } catch (e) { return DEFAULT_API_BASE_URL }
}
function getHttpApiBaseUrl() {
  try {
    const saved = normalizeBaseUrl(wx.getStorageSync(API_BASE_STORAGE_KEY))
    return saved || DEFAULT_API_BASE_URL
  } catch (e) { return DEFAULT_API_BASE_URL }
}
function setApiBaseUrl(value) {
  const next = normalizeBaseUrl(value)
  if (!next) throw new Error('后端地址不能为空')
  wx.setStorageSync(API_BASE_STORAGE_KEY, next)
  return next
}
function clearApiBaseUrl() { wx.removeStorageSync(API_BASE_STORAGE_KEY) }

module.exports = {
  API_BASE_URL: DEFAULT_API_BASE_URL,
  DEFAULT_API_BASE_URL,
  PUBLIC_API_BASE_URL,
  API_BASE_STORAGE_KEY,
  CLOUD_ENV_ID,
  CLOUDRUN_SERVICE_NAME,
  cloudReady,
  getTransport,
  getApiBaseUrl,
  getHttpApiBaseUrl,
  setApiBaseUrl,
  clearApiBaseUrl,
  DEV_LOGIN: false,
  USE_STREAMING: true,
  REQUEST_TIMEOUT: 20000,
  USE_CLOUD_STORAGE: true
}
