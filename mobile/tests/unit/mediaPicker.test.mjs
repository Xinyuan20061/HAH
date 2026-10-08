import { beforeEach, describe, expect, it, vi } from 'vitest'

const { getPhoto } = vi.hoisted(() => ({ getPhoto: vi.fn() }))

vi.mock('@capacitor/camera', () => ({
  Camera: { getPhoto },
  CameraResultType: { Uri: 'uri' },
  CameraSource: { Prompt: 'prompt' },
}))

import { captureOrChooseMealPhoto } from '../../src/services/mediaPicker.ts'

describe('meal media picker port', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    getPhoto.mockReset()
    getPhoto.mockResolvedValue({
      webPath: 'https://localhost/_capacitor_file_/meal.jpg',
      format: 'jpeg',
    })
  })

  it('wraps the native camera prompt and returns a Blob for the upload service', async () => {
    const blob = new Blob(['meal-photo'], { type: 'image/jpeg' })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, blob: async () => blob }))

    const selected = await captureOrChooseMealPhoto()

    expect(getPhoto).toHaveBeenCalledWith(expect.objectContaining({
      resultType: 'uri',
      source: 'prompt',
      correctOrientation: true,
    }))
    expect(selected.blob).toBe(blob)
    expect(selected.fileName).toMatch(/^餐食-\d+\.jpeg$/)
  })

  it('surfaces URI read failures rather than returning an empty image', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false }))

    await expect(captureOrChooseMealPhoto()).rejects.toThrow('无法读取这张照片')
  })
})
