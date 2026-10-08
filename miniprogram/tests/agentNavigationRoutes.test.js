const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const {
  NAVIGATION_TARGETS, normalizeNavigation, safeActionUrl, openAction
} = require('../utils/agentNavigation')

const root = path.resolve(__dirname, '..')
const app = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'))

test('every agent navigation target resolves to a registered mini-program page', () => {
  const pages = new Set(app.pages.map(item => `/${item}`))
  for (const [target, destination] of Object.entries(NAVIGATION_TARGETS)) {
    assert.ok(pages.has(destination.route.split('?')[0]), `${target} has an unregistered route`)
  }
})

test('explicit non-plan navigation uses only local allow-listed destinations', () => {
  const result = {
    intent: 'navigation',
    run_id: 31,
    presentation: {
      version: 'healthmate.presentation.v1',
      actor: 'xiaokang',
      navigation: { target: 'trends', mode: 'after_animation', params: {} }
    }
  }
  const navigation = normalizeNavigation(result)
  assert.equal(navigation.action.target, 'trends')
  assert.equal(navigation.autoNavigate, true)
  assert.equal(safeActionUrl({ ...navigation.action, route: 'https://bad.invalid/' }), '/pages/trends/index')
  result.presentation.navigation.target = 'https://bad.invalid/'
  assert.equal(normalizeNavigation(result).action, null)
})

test('agent opens tab destinations with switchTab and other pages with navigateTo', () => {
  const calls = []
  const previous = global.wx
  global.wx = {
    switchTab: value => calls.push(['tab', value.url]),
    navigateTo: value => calls.push(['page', value.url]),
    showToast: () => {}
  }
  try {
    openAction({ target: 'home' })
    openAction({ target: 'steward' })
    openAction({ target: 'settings' })
    assert.deepEqual(calls, [
      ['tab', '/pages/home/index'],
      ['tab', '/pages/chat/index'],
      ['page', '/pages/settings/index']
    ])
  } finally {
    global.wx = previous
  }
})

test('gym, floating companion and steward all consume the shared route list', () => {
  const home = fs.readFileSync(path.join(root, 'pages/home/index.js'), 'utf8')
  const floating = fs.readFileSync(path.join(root, 'components/agent-float/index.js'), 'utf8')
  const chat = fs.readFileSync(path.join(root, 'pages/chat/index.js'), 'utf8')
  assert.match(home, /NAVIGATION_TARGETS, openAction.*agentNavigation/)
  assert.match(floating, /TAB_ROUTES/)
  assert.match(chat, /normalizeNavigation, persistPlanHandoff, safeActionUrl, TAB_ROUTES/)
  assert.match(chat, /navigationAction/)
})
