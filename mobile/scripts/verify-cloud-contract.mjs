import process from 'node:process'

const originValue = process.env.HEALTHMATE_CLOUD_ORIGIN?.trim()
if (!originValue) {
  throw new Error('Set HEALTHMATE_CLOUD_ORIGIN to the service origin (HTTPS outside localhost).')
}

const origin = new URL(originValue)
const localHosts = new Set(['localhost', '127.0.0.1', '10.0.2.2'])
if (origin.pathname !== '/' || origin.search || origin.hash) {
  throw new Error('HEALTHMATE_CLOUD_ORIGIN must contain only the service origin, without a path.')
}
if (origin.protocol !== 'https:' && !localHosts.has(origin.hostname)) {
  throw new Error('WeChat Cloud Hosting contract checks require HTTPS.')
}

const required = [
  ['post', '/api/v1/auth/mobile/wechat'],
  ['post', '/api/v1/auth/cloudbase/ticket'],
  ['post', '/api/v1/auth/link/start'],
  ['post', '/api/v1/auth/link/complete'],
  ['post', '/api/v1/auth/link/unlink'],
  ['get', '/api/v1/media/mobile-upload/options'],
  ['post', '/api/v1/media/mobile-upload/sessions'],
  ['post', '/api/v1/media/mobile-upload/sessions/{media_id}/complete'],
  ['delete', '/api/v1/media/mobile-upload/sessions/{media_id}'],
  ['get', '/api/v1/privacy/deletion-status'],
  ['delete', '/api/v1/privacy/account'],
]

const response = await fetch(new URL('/openapi.json', origin), {
  headers: { accept: 'application/json' },
  redirect: 'error',
  signal: AbortSignal.timeout(15_000),
})
if (!response.ok) throw new Error(`Cloud OpenAPI request failed with HTTP ${response.status}.`)

const openapi = await response.json()
if (!openapi.paths || typeof openapi.paths !== 'object') {
  throw new Error('Cloud OpenAPI response does not contain a paths object.')
}

const missing = required.filter(([method, path]) => !openapi.paths[path]?.[method])
console.log(`Checked ${required.length} Android cloud contract operations.`)
if (missing.length) {
  for (const [method, path] of missing) console.error(`Missing: ${method.toUpperCase()} ${path}`)
  process.exitCode = 1
} else {
  console.log('All Android contract operations are present at the checked origin.')
}
