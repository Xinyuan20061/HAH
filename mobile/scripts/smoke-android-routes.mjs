import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import process from 'node:process'

const projectRoot = path.resolve(process.cwd(), '..')
const routeMapPath = path.join(projectRoot, 'docs', 'android', 'route-map.json')
const routeMap = JSON.parse(await readFile(routeMapPath, 'utf8'))
const routes = [...routeMap.routes]
if (!routes.some(route => route.android === '/account-link')) {
  routes.push({ mini: '(Android only)', android: '/account-link' })
}

const evidenceDate = process.env.HAH_ROUTE_SMOKE_DATE || '2026-10-07'
if (!/^\d{4}-\d{2}-\d{2}$/.test(evidenceDate)) {
  throw new Error('HAH_ROUTE_SMOKE_DATE must use YYYY-MM-DD')
}

const outputDir = path.join(
  projectRoot,
  'docs',
  'android',
  'verification',
  evidenceDate,
  'route-smoke',
)
await mkdir(outputDir, { recursive: true })

const targets = await (await fetch('http://127.0.0.1:9222/json/list')).json()
const target = targets.find(item => item.type === 'page' && item.url.startsWith('http://localhost/'))
if (!target) throw new Error('未找到已转发到 localhost:9222 的 Android WebView。')

const socket = new WebSocket(target.webSocketDebuggerUrl)
await new Promise((resolve, reject) => {
  socket.addEventListener('open', resolve, { once: true })
  socket.addEventListener('error', reject, { once: true })
})

let nextId = 1
const pending = new Map()
const runtimeErrors = []
socket.addEventListener('message', event => {
  const message = JSON.parse(String(event.data))
  if (message.method === 'Runtime.exceptionThrown') {
    runtimeErrors.push(message.params.exceptionDetails?.text || 'JavaScript runtime exception')
  }
  if (message.id && pending.has(message.id)) {
    pending.get(message.id)(message)
    pending.delete(message.id)
  }
})

function command(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = nextId++
    const timeout = setTimeout(() => {
      pending.delete(id)
      reject(new Error(`CDP ${method} timed out`))
    }, 15000)
    pending.set(id, message => {
      clearTimeout(timeout)
      if (message.error) reject(new Error(message.error.message))
      else resolve(message)
    })
    socket.send(JSON.stringify({ id, method, params }))
  })
}

async function evaluate(expression) {
  const response = await command('Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true,
  })
  if (response.result.exceptionDetails) {
    throw new Error(response.result.exceptionDetails.text || '页面 JavaScript 求值失败')
  }
  return response.result.result.value
}

const pause = ms => new Promise(resolve => setTimeout(resolve, ms))
await command('Runtime.enable')
await command('Page.enable')

const loginState = await evaluate(`({
  authenticated: !!document.querySelector('.route-surface'),
  buttonEnabled: !!document.querySelector('.login-button:not(:disabled)'),
  title: document.querySelector('.login-page h1')?.innerText || ''
})`)
if (!loginState.authenticated) {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    const state = await evaluate(`({
      authenticated: !!document.querySelector('.route-surface'),
      buttonEnabled: !!document.querySelector('.login-button:not(:disabled)')
    })`)
    if (state.authenticated) break
    if (state.buttonEnabled) {
      await evaluate(`document.querySelector('.login-button:not(:disabled)').click(); true`)
    }
    await pause(500)
  }
}
const authenticated = await evaluate('!!document.querySelector(".route-surface")')
if (!authenticated) throw new Error('开发账号未能登录，未开始路由冒烟检查。')

const report = []
for (let index = 0; index < routes.length; index += 1) {
  const route = routes[index]
  const beforeErrors = runtimeErrors.length
  await evaluate(`(() => {
    history.pushState({}, '', ${JSON.stringify(route.android)});
    window.dispatchEvent(new PopStateEvent('popstate'));
    return true;
  })()`)
  await pause(650)
  const page = await evaluate(`(() => {
    const surface = document.querySelector('.route-surface');
    const heading = surface?.querySelector('h1, h2, [role="heading"]');
    return {
      route: location.pathname,
      rendered: !!surface && !!surface.firstElementChild,
      heading: heading?.innerText?.trim() || '',
      text: (surface?.innerText || '').trim().replace(/\\n{3,}/g, '\\n\\n'),
      documentTitle: document.title
    };
  })()`)
  const fileName = `${String(index + 1).padStart(2, '0')}-${route.android.slice(1).replaceAll('/', '-') || 'home'}.png`
  const screenshot = await command('Page.captureScreenshot', {
    format: 'png',
    captureBeyondViewport: false,
  })
  await writeFile(path.join(outputDir, fileName), Buffer.from(screenshot.result.data, 'base64'))
  report.push({
    mini: route.mini,
    expectedRoute: route.android,
    ...page,
    screenshot: fileName,
    runtimeErrors: runtimeErrors.slice(beforeErrors),
  })
}

await writeFile(path.join(outputDir, 'report.json'), `${JSON.stringify(report, null, 2)}\n`, 'utf8')
await writeFile(
  path.join(outputDir, 'visible-copy.md'),
  `# Android 页面可见文案冒烟记录\n\n${report.map(item => `## ${item.expectedRoute}\n\n${item.text || '（页面未显示文字）'}\n`).join('\n')}`,
  'utf8',
)
socket.close()

const failed = report.filter(item => !item.rendered || item.route !== item.expectedRoute)
process.stdout.write(JSON.stringify({
  pageCount: report.length,
  renderedCount: report.length - failed.length,
  failures: failed.map(item => ({ route: item.expectedRoute, actual: item.route, rendered: item.rendered })),
  runtimeExceptions: runtimeErrors.length,
  outputDir,
}, null, 2))
if (failed.length || runtimeErrors.length) process.exitCode = 1
