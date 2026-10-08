'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')

const api = require('../utils/request')
const pagePath = path.join(__dirname, '../pages/settings/index.js')

function loadPage() {
  let definition
  global.Page = value => { definition = value }
  delete require.cache[require.resolve(pagePath)]
  require(pagePath)
  return definition
}

function pageContext() {
  return {
    data: { busy: false },
    setData(update) { Object.assign(this.data, update) },
  }
}

test('Android account-link entry ignores duplicate taps while the request is pending', async t => {
  const originalEnsureToken = api.ensureToken
  const originalPost = api.post
  const originalWx = global.wx
  const originalPage = global.Page
  t.after(() => {
    api.ensureToken = originalEnsureToken
    api.post = originalPost
    global.wx = originalWx
    global.Page = originalPage
    delete require.cache[require.resolve(pagePath)]
  })

  let releaseToken
  let postCalls = 0
  api.ensureToken = () => new Promise(resolve => { releaseToken = resolve })
  api.post = async () => { postCalls += 1; return { link_code: 'ABCD1234' } }
  global.wx = {
    setClipboardData({ success }) { success() },
    showModal({ complete }) { complete() },
  }

  const page = loadPage()
  const context = pageContext()
  const firstRequest = page.linkAndroid.call(context)
  assert.equal(context.data.busy, true)

  await page.linkAndroid.call(context)
  assert.equal(postCalls, 0, 'the second tap must not start another request')

  releaseToken()
  await firstRequest
  assert.equal(postCalls, 1)
  assert.equal(context.data.busy, false)
})

test('Android account-link code remains available when clipboard access fails', async t => {
  const originalEnsureToken = api.ensureToken
  const originalPost = api.post
  const originalWx = global.wx
  const originalPage = global.Page
  t.after(() => {
    api.ensureToken = originalEnsureToken
    api.post = originalPost
    global.wx = originalWx
    global.Page = originalPage
    delete require.cache[require.resolve(pagePath)]
  })

  let modal
  api.ensureToken = async () => undefined
  api.post = async () => ({ link_code: 'ABCD1234' })
  global.wx = {
    setClipboardData({ fail }) { fail(new Error('clipboard unavailable')) },
    showModal(options) { modal = options; options.complete() },
  }

  const page = loadPage()
  const context = pageContext()
  await page.linkAndroid.call(context)

  assert.equal(modal.title, 'Android 账号关联码已生成')
  assert.match(modal.content, /ABCD1234/)
  assert.match(modal.content, /复制失败，请手动输入上方代码/)
  assert.equal(context.data.busy, false)
})
