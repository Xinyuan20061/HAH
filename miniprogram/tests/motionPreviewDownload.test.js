'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')

const api = require('../utils/request')

test('云托管关键帧经 callContainer 下载，内部域名不会传给 image', async () => {
  const jpeg = Uint8Array.from([0xff, 0xd8, 0xff, 0xd9]).buffer
  let call
  let written
  global.wx = {
    env: { USER_DATA_PATH: 'wxfile://usr' },
    getStorageSync: () => 'test-token',
    cloud: { callContainer(options) {
      call = options
      options.success({ statusCode: 200, data: jpeg })
    } },
    getFileSystemManager: () => ({ writeFile(options) {
      written = options
      options.success()
    } })
  }
  const url = 'http://internal-host/api/v1/media/motion-analyses/42/previews/f_001?run_id=42&frame_id=f_001&exp=9999999999&sig=abc'
  const path = await api.downloadMotionPreview(url, 42, 'f_001')
  assert.equal(call.path, url.slice(url.indexOf('/api/v1/')))
  assert.equal(call.responseType, 'arraybuffer')
  assert.equal(call.header.Authorization, 'Bearer test-token')
  assert.equal(written.data, jpeg)
  assert.equal(path, written.filePath)
  assert.match(path, /^wxfile:\/\/usr\/healthmate-motion-42-f_001-/)
})

test('关键帧地址必须匹配当前任务和帧，拒绝转发任意路径', async () => {
  await assert.rejects(api.downloadMotionPreview('https://x/api/v1/media/motion-analyses/99/previews/f_001?sig=abc', 42, 'f_001'))
  await assert.rejects(api.downloadMotionPreview('https://x/api/v1/media/motion-analyses/42/previews/f_001?exp=1', 42, 'f_001'))
})

test('接口返回非 JPEG 时不写入图片文件', async () => {
  let writes = 0
  global.wx = {
    env: { USER_DATA_PATH: 'wxfile://usr' },
    getStorageSync: () => '',
    cloud: { callContainer(options) { options.success({ statusCode: 200, data: Uint8Array.from([123, 125]).buffer }) } },
    getFileSystemManager: () => ({ writeFile() { writes += 1 } })
  }
  await assert.rejects(api.downloadMotionPreview('https://x/api/v1/media/motion-analyses/42/previews/f_001?sig=abc', 42, 'f_001'), /格式无效/)
  assert.equal(writes, 0)
})

test('动作分析页用本地图片路径更新主图和时间轴', async () => {
  let pageDefinition
  global.Page = definition => { pageDefinition = definition }
  require('../pages/media/index.js')
  const originalGet = api.get
  const originalDownload = api.downloadMotionPreview
  const signed = 'http://internal/api/v1/media/motion-analyses/42/previews/f_001?sig=abc'
  api.get = async () => ({ frames: [{ id: 'f_001', preview: { state: 'available', url: signed } }] })
  api.downloadMotionPreview = async () => 'wxfile://usr/frame.jpg'
  const frame = { id: 'f_001', timestamp_ms: 1000, previewUrl: '', previewState: 'unavailable' }
  const page = {
    ...pageDefinition,
    data: { analysisId: 42, cloudMode: true, timelineFrames: [frame], activeFrame: frame },
    setData(change) {
      for (const [key, value] of Object.entries(change)) {
        const match = /^timelineFrames\[(\d+)\]\.(\w+)$/.exec(key)
        if (match) this.data.timelineFrames[Number(match[1])][match[2]] = value
        else this.data[key] = value
      }
    }
  }
  try {
    await page.fillEvidencePreviews()
    assert.equal(page.data.timelineFrames[0].previewUrl, 'wxfile://usr/frame.jpg')
    assert.equal(page.data.activeFrame.previewUrl, 'wxfile://usr/frame.jpg')
    assert.equal(page.data.activeFrame.previewState, 'available')
  } finally {
    api.get = originalGet
    api.downloadMotionPreview = originalDownload
    delete global.Page
  }
})
