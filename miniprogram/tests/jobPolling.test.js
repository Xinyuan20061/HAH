const { test } = require('node:test')
const assert = require('node:assert/strict')
const api = require('../utils/request')
const originalGet = api.get
const cloud = require('../utils/cloudMedia')
const { pollJob } = require('../utils/jobPolling')

const asset = { media_id: 10, cloud_file_id: 'cloud://test/image.jpg' }

test('waiting source refresh resumes the original job through done', async () => {
  const replies = [{ status: 'waiting_source_refresh' }, { status: 'queued' }, { status: 'processing', progress: 60 }, { status: 'done', result: { calories: 200 } }]
  const paths = [], refreshes = []
  api.get = async path => { paths.push(path); return replies.shift() }
  cloud.refresh = async (...args) => refreshes.push(args)
  const result = await pollJob({ path: '/vision/food-jobs', jobId: 42, asset, wait: async () => {} })
  assert.equal(result.calories, 200)
  assert.deepEqual(new Set(paths), new Set(['/vision/food-jobs/42']))
  assert.deepEqual(refreshes, [[10, asset.cloud_file_id]])
})

test('offline queued polling has deadline and increasing bounded waits', async () => {
  let elapsed = 0
  const delays = []
  api.get = async () => ({ status: 'queued' })
  await assert.rejects(pollJob({ path: '/media/motion-jobs', jobId: 42, asset, maxMs: 15000,
    now: () => elapsed, wait: async ms => { delays.push(ms); elapsed += ms } }), e => e.pending === true)
  assert.equal(elapsed, 15000)
  assert.ok(Math.max(...delays) <= 5000)
  assert.ok(delays[1] > delays[0])
})

test('permanent failures are visible', async () => {
  api.get = async () => ({ status: 'failed', error: 'invalid media' })
  await assert.rejects(pollJob({ path: '/vision/food-jobs', jobId: 42, asset }), /invalid media/)
})

test('source refresh is bounded and page unload stops polling', async () => {
  let refreshes = 0
  api.get = async () => ({ status: 'waiting_source_refresh' })
  cloud.refresh = async () => { refreshes++ }
  await assert.rejects(pollJob({ path: '/vision/food-jobs', jobId: 42, asset }), /无法自动刷新/)
  assert.equal(refreshes, 2)
  await assert.rejects(pollJob({ path: '/vision/food-jobs', jobId: 42, asset, cancelled: () => true }), e => e.pending)
})

test('cloud deletion does not treat absent result as success', async () => {
  const media = require('../utils/cloudMedia')
  global.wx = { cloud: { deleteFile: async () => ({ fileList: [] }) } }
  await assert.rejects(media.deleteFiles(['cloud://test/a']), /未能删除/)
  wx.cloud.deleteFile = async () => ({ fileList: [{ fileID: 'cloud://test/a', status: 0 }] })
  assert.equal(await media.deleteFiles(['cloud://test/a']), 1)
  wx.cloud.deleteFile = async () => ({ fileList: [{ fileID: 'cloud://test/a', status: -1, errMsg: 'FILE_NOT_EXIST' }] })
  assert.equal(await media.deleteFiles(['cloud://test/a']), 1)
})

test('pending pointer is scoped to user and does not store signed URLs', () => {
  const storage = new Map([['healthmate_user_id', 1]])
  global.wx = { getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value), removeStorageSync: key => storage.delete(key) }
  const pending = require('../utils/pendingJobs')
  pending.remember('food', 42, { ...asset, temp_url: 'https://signed.example/?token=private' })
  assert.equal(pending.load('food').jobId, 42)
  assert.equal(pending.load('food').asset.temp_url, undefined)
  storage.set('healthmate_user_id', 2)
  assert.equal(pending.load('food'), null)
  storage.set('healthmate_user_id', 1)
  pending.forget('food')
  assert.equal(pending.load('food'), null)
})

test('expired JWT refreshes with wx.login once and retries the request', async () => {
  const storage = new Map([['token', 'expired-test-token']])
  let logins = 0, reads = 0
  global.wx = {
    getStorageSync: key => storage.get(key),
    setStorageSync: (key, value) => storage.set(key, value),
    removeStorageSync: key => storage.delete(key),
    login: options => { logins++; options.success({ code: 'test-code' }) },
    cloud: { callContainer: options => {
      if (options.path.endsWith('/auth/wechat')) return options.success({ statusCode: 200, data: { access_token: 'fresh-test-token', user: { id: 1 } } })
      reads++
      assert.equal(options.header.Authorization, reads === 1 ? 'Bearer expired-test-token' : 'Bearer fresh-test-token')
      options.success({ statusCode: reads === 1 ? 401 : 200, data: reads === 1 ? {} : { ok: true } })
    } }
  }
  assert.deepEqual(await originalGet('/users/me'), { ok: true })
  assert.equal(logins, 1)
  assert.equal(reads, 2)
})
