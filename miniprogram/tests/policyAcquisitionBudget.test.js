'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

const ROOT = path.join(__dirname, '..')

function loadPage(acquisitionApi) {
  let definition
  const source = fs.readFileSync(path.join(ROOT, 'pages/policy/episode/index.js'), 'utf8')
  const sandbox = {
    Page(value) { definition = value },
    require(name) {
      if (name === '../../../utils/policyAcquisition') return acquisitionApi
      return {}
    },
    wx: { showToast() {} },
    Date,
    console
  }
  vm.runInNewContext(source, sandbox, { filename: 'policy/episode/index.js' })
  return definition
}

test('consent UI exposes only default or lower acquisition budgets and sends the selection', async () => {
  let sent
  const page = loadPage({
    async start(_episodeId, body) {
      sent = body
      return { session_id: 'session-test' }
    }
  })
  const context = {
    data: {
      episode: { episode_id: 'episode-test', version: 4 },
      acquisitionConsent: true,
      acquisitionBusy: false,
      acquisitionBudget: { ...page.data.acquisitionBudget }
    },
    setData(changes) {
      for (const [key, value] of Object.entries(changes)) {
        const [parent, child] = key.split('.')
        if (child) this.data[parent][child] = value
        else this.data[key] = value
      }
    },
    async load() {}
  }
  const choose = (dimension, index) => page.chooseAcquisitionBudget.call(context, {
    currentTarget: { dataset: { dimension } }, detail: { value: String(index) }
  })
  choose('daily', 1)
  choose('episode', 1)
  choose('time', 1)
  await page.startAcquisition.call(context)

  assert.deepStrictEqual(JSON.parse(JSON.stringify(sent.budget)), {
    daily_prompt_limit: 1,
    episode_prompt_limit: 7,
    estimated_daily_seconds: 15
  })
  assert.match(fs.readFileSync(path.join(ROOT, 'pages/policy/episode/index.wxml'), 'utf8'), /下面的预算可以调低/)
})

test('acquisition elapsed clock accumulates visible segments and survives page hiding', () => {
  const store = new Map()
  const clock = require('../utils/policyAcquisition').createElapsedClock({
    get: key => store.get(key),
    set: (key, value) => store.set(key, value),
    remove: key => store.delete(key)
  })
  assert.equal(clock.start('session-1', 'question-1', 1000), 0)
  assert.equal(clock.pause('session-1', 'question-1', 4600), 3600)
  assert.equal(clock.elapsed('session-1', 'question-1', 9000), 3600)
  assert.equal(clock.start('session-1', 'question-1', 10000), 3600)
  assert.equal(clock.elapsed('session-1', 'question-1', 12500), 6100)
  assert.equal(clock.pause('session-1', 'question-1', 12500), 6100)
  clock.clear('session-1', 'question-1')
  assert.equal(clock.elapsed('session-1', 'question-1', 20000), 0)
})

test('question answers attach client elapsed time without changing saved retry bodies', async () => {
  const page = loadPage({
    elapsedClock: { elapsed: () => 2345, clear() {}, start() {}, pause() {} }
  })
  let submitted
  const question = { question_id: 'question-1', kind: 'execution_confirmation' }
  const context = {
    data: {
      acquisition: { session_id: 'session-1', session_version: 3, decision: { question } },
      episode: { version: 7 }, acquisitionBusy: false
    },
    acquisitionElapsedMs: page.acquisitionElapsedMs,
    async submitAcquisitionAnswer(_question, body) { submitted = body }
  }
  await page.answerAcquisition.call(context, { currentTarget: { dataset: { response: 'declined' } } })
  assert.equal(submitted.client_elapsed_ms, 2345)
  assert.equal(submitted.response, 'declined')
  assert.equal(submitted.confirmation, false)
})
