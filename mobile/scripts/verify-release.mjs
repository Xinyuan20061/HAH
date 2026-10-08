import { readdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import process from 'node:process'
import { loadEnv } from 'vite'

const env = loadEnv('production', process.cwd(), 'VITE_')
const apiRoot = (env.VITE_API_BASE_URL || '').trim().replace(/\/+$/, '')
const cloudbaseEnvId = (env.VITE_CLOUDBASE_ENV_ID || '').trim()
const failures = []
const releaseApplicationId = (process.env.ANDROID_RELEASE_APPLICATION_ID || '').trim()

if (env.VITE_APP_ENV !== 'production') failures.push('VITE_APP_ENV must be production.')
if (env.VITE_AUTH_MODE !== 'wechat') failures.push('VITE_AUTH_MODE must be wechat.')
if (!/^[A-Za-z0-9_-]{1,64}$/.test(cloudbaseEnvId)
  || /placeholder|replace[-_ ]?with|change[-_ ]?me|your[-_ ]/i.test(cloudbaseEnvId)) {
  failures.push('VITE_CLOUDBASE_ENV_ID must be provided to the Android build environment.')
}
if (!apiRoot.startsWith('https://') || !apiRoot.endsWith('/api/v1')) {
  failures.push('VITE_API_BASE_URL must use HTTPS and end in /api/v1.')
} else {
  const hostname = new URL(apiRoot).hostname.toLowerCase()
  const apiUrl = new URL(apiRoot)
  if (apiUrl.username || apiUrl.password) {
    failures.push('VITE_API_BASE_URL must not contain embedded credentials.')
  }
  if (hostname === 'localhost' || hostname.endsWith('.localhost') || hostname === '127.0.0.1'
    || hostname === '::1' || /^10\./.test(hostname) || /^192\.168\./.test(hostname)
    || /^172\.(1[6-9]|2\d|3[01])\./.test(hostname)) {
    failures.push('VITE_API_BASE_URL cannot point to a local or private-network host.')
  }
  if (/example\.(com|org|net)$/i.test(hostname) || /placeholder|replace[-_ ]?with|change[-_ ]?me/i.test(apiRoot)) {
    failures.push('VITE_API_BASE_URL still contains an example or placeholder value.')
  }
}
const wechatMobileAppId = (process.env.WECHAT_MOBILE_APP_ID || '').trim()
if (!/^[A-Za-z0-9_-]{1,64}$/.test(wechatMobileAppId)
  || /demo|placeholder|replace[-_ ]?with|change[-_ ]?me/i.test(wechatMobileAppId)) {
  failures.push('WECHAT_MOBILE_APP_ID must be provided to the Android build environment.')
}
if (!/^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*){1,}$/.test(releaseApplicationId)
  || releaseApplicationId.endsWith('.dev')
  || /\.example\.|placeholder|replace[-_ ]?with|change[-_ ]?me/i.test(releaseApplicationId)) {
  failures.push('ANDROID_RELEASE_APPLICATION_ID must be the valid package registered for the release app.')
}

const assetsDir = path.join(process.cwd(), 'dist', 'assets')
let bundles = []
try {
  bundles = (await readdir(assetsDir)).filter(name => name.endsWith('.js'))
} catch {
  failures.push('Run the production web build before release verification.')
}
if (bundles.length) {
  const source = (await Promise.all(bundles.map(name => readFile(path.join(assetsDir, name), 'utf8')))).join('\n')
  if (source.includes('/auth/dev-login')) failures.push('A development-login endpoint remains in the production JavaScript bundle.')
  const localDevelopmentServer = /https?:\/\/(?:localhost:\d+|127\.0\.0\.1(?::\d+)?|10\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?|192\.168\.\d{1,3}\.\d{1,3}(?::\d+)?|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}(?::\d+)?|0\.0\.0\.0(?::\d+)?)/i
  if (localDevelopmentServer.test(source)) failures.push('A local development server URL remains in the production JavaScript bundle.')
  if (source.includes('server-only-secret') || source.includes('MOBILE_WECHAT_APP_SECRET')) {
    failures.push('A server-only WeChat credential marker appears in the production bundle.')
  }
}

if (failures.length) {
  for (const failure of failures) process.stderr.write(`Release check failed: ${failure}\n`)
  process.exitCode = 1
} else {
  process.stdout.write('Release web configuration and bundle checks passed. Android package identity and signing are checked by Gradle.\n')
}
