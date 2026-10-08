import { beforeEach, describe, expect, it, vi } from 'vitest'

const { plugin, listener } = vi.hoisted(() => ({
  listener: { remove: vi.fn() },
  plugin: {
    pickVideo: vi.fn(),
    captureVideo: vi.fn(),
    uploadFile: vi.fn(),
    cancelUpload: vi.fn(),
    releaseMedia: vi.fn(),
    addListener: vi.fn(),
  },
}))

vi.mock('@capacitor/core', () => ({
  Capacitor: {
    isNativePlatform: () => true,
    getPlatform: () => 'android',
  },
  registerPlugin: () => plugin,
}))

import { pickNativeVideo, uploadNativeVideo } from '../../src/services/nativeMedia.ts'

const source = {
  uri: 'content://media/external/video/42',
  file_name: 'workout.mp4',
  content_type: 'video/mp4',
  size_bytes: 80 * 1024 * 1024,
}

describe('Android native media transfer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    plugin.addListener.mockResolvedValue(listener)
    plugin.pickVideo.mockResolvedValue(source)
    plugin.captureVideo.mockResolvedValue(source)
    plugin.uploadFile.mockResolvedValue({ status: 200 })
    plugin.cancelUpload.mockResolvedValue()
    plugin.releaseMedia.mockResolvedValue()
  })

  it('uses the native document picker and camera capture routes', async () => {
    await expect(pickNativeVideo('pick')).resolves.toEqual(source)
    await expect(pickNativeVideo('capture')).resolves.toEqual(source)
    expect(plugin.pickVideo).toHaveBeenCalledOnce()
    expect(plugin.captureVideo).toHaveBeenCalledOnce()
  })

  it('streams the selected content URI and forwards native progress', async () => {
    plugin.uploadFile.mockImplementation(async request => {
      const onProgress = plugin.addListener.mock.calls[0][1]
      onProgress({ transfer_id: request.transfer_id, percent: 58 })
      return { status: 200 }
    })
    await uploadNativeVideo(source, {
      uploadUrl: 'https://bucket.cos.ap-shanghai.myqcloud.com/staging/object?signature=redacted',
      headers: { 'Content-Type': 'video/mp4' },
      contentType: 'video/mp4',
      sizeBytes: source.size_bytes,
    }, percent => expect(percent).toBe(58))

    const request = plugin.uploadFile.mock.calls[0][0]
    expect(request.uri).toBe(source.uri)
    expect(request.size_bytes).toBe(source.size_bytes)
    expect(request.upload_url).toContain('https://')
    expect(listener.remove).toHaveBeenCalledOnce()
  })

  it('cancels an active native upload when its owner is disposed', async () => {
    let rejectUpload
    plugin.uploadFile.mockImplementation(() => new Promise((_resolve, reject) => {
      rejectUpload = reject
    }))
    plugin.cancelUpload.mockImplementation(async () => {
      rejectUpload?.(new Error('上传已取消'))
    })
    const controller = new AbortController()
    const upload = uploadNativeVideo(source, {
      uploadUrl: 'https://bucket.cos.ap-shanghai.myqcloud.com/staging/object?signature=redacted',
      headers: { 'Content-Type': 'video/mp4' },
      contentType: 'video/mp4',
      sizeBytes: source.size_bytes,
    }, undefined, controller.signal)

    await vi.waitFor(() => expect(plugin.uploadFile).toHaveBeenCalledOnce())
    controller.abort()
    await expect(upload).rejects.toThrow('上传已取消')
    expect(plugin.cancelUpload).toHaveBeenCalledOnce()
    expect(listener.remove).toHaveBeenCalledOnce()
  })
})
