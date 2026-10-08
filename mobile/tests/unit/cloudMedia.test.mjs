import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  auth: {
    signInWithCustomTicket: vi.fn(),
    signOut: vi.fn(),
  },
  storage: {
    upload: vi.fn(),
    createSignedUrl: vi.fn(),
    remove: vi.fn(),
  },
  app: {
    auth: vi.fn(),
    storage: { from: vi.fn() },
  },
  init: vi.fn(),
  readMobileUploadOptions: vi.fn(),
  requestCloudbaseTicket: vi.fn(),
  registerCloudMedia: vi.fn(),
  refreshCloudMediaSource: vi.fn(),
  readMediaPlayback: vi.fn(),
  createMobileUploadSession: vi.fn(),
  completeMobileUploadSession: vi.fn(),
  abortMobileUploadSession: vi.fn(),
  uploadMediaToDevelopmentBackend: vi.fn(),
}))

vi.mock('@cloudbase/js-sdk', () => ({ default: { init: mocks.init } }))
vi.mock('../../src/services/api.ts', () => ({
  readMobileUploadOptions: mocks.readMobileUploadOptions,
  requestCloudbaseTicket: mocks.requestCloudbaseTicket,
  registerCloudMedia: mocks.registerCloudMedia,
  refreshCloudMediaSource: mocks.refreshCloudMediaSource,
  readMediaPlayback: mocks.readMediaPlayback,
  createMobileUploadSession: mocks.createMobileUploadSession,
  completeMobileUploadSession: mocks.completeMobileUploadSession,
  abortMobileUploadSession: mocks.abortMobileUploadSession,
  uploadMediaToDevelopmentBackend: mocks.uploadMediaToDevelopmentBackend,
}))
vi.mock('../../src/services/nativeMedia.ts', () => ({
  releaseNativeVideo: vi.fn(),
  uploadNativeVideo: vi.fn(),
}))

import { resetCloudMediaSession, uploadCloudMedia } from '../../src/services/cloudMedia.ts'

describe('CloudBase media authentication', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubEnv('VITE_CLOUDBASE_ENV_ID', 'test-env-123')
    mocks.app.auth.mockReturnValue(mocks.auth)
    mocks.app.storage.from.mockReturnValue(mocks.storage)
    mocks.init.mockReturnValue(mocks.app)
    mocks.auth.signInWithCustomTicket.mockImplementation(async getTicket => {
      await getTicket()
      return { user: { uid: 'cloud-user-17', customUserId: 'hm_user_17' } }
    })
    mocks.auth.signOut.mockResolvedValue(undefined)
    mocks.storage.upload.mockResolvedValue({ data: { id: 'cloud://test-env/files/photo.jpg' } })
    mocks.storage.createSignedUrl.mockResolvedValue({
      data: { signedUrl: 'https://storage.example.test/photo.jpg?signature=temporary' },
    })
    mocks.storage.remove.mockResolvedValue({ data: {} })
    mocks.readMobileUploadOptions.mockResolvedValue({
      storage_backend: 'cloud_ref',
      max_upload_bytes: 5 * 1024 * 1024,
      max_upload_bytes_by_purpose: { food_analysis: 5 * 1024 * 1024 },
    })
    mocks.requestCloudbaseTicket.mockResolvedValue({ ticket: 'short-lived-ticket', uid: 'hm_user_17' })
    mocks.registerCloudMedia.mockResolvedValue({ media_id: 37, storage_backend: 'cloud_ref' })
  })

  afterEach(async () => {
    await resetCloudMediaSession()
    vi.unstubAllEnvs()
  })

  it('uses the SDK 3.x login state and registers the upload under its verified custom user', async () => {
    const result = await uploadCloudMedia(
      'healthmate-token',
      new Blob(['photo-bytes'], { type: 'image/jpeg' }),
      'photo.jpg',
      'image',
      'cloud-media-request-1',
      'food_analysis',
    )

    expect(mocks.init).toHaveBeenCalledWith({ env: 'test-env-123' })
    expect(mocks.app.auth).toHaveBeenCalledWith({ persistence: 'none' })
    expect(mocks.auth.signInWithCustomTicket).toHaveBeenCalledOnce()
    expect(mocks.storage.upload).toHaveBeenCalledOnce()
    const [path, file, options] = mocks.storage.upload.mock.calls[0]
    expect(path).toMatch(/^healthmate\/hm_user_17\/image\/\d{4}-\d{2}-\d{2}\/.+\.jpg$/)
    expect(file.type).toBe('image/jpeg')
    expect(options).toMatchObject({ contentType: 'image/jpeg', upsert: false })
    expect(mocks.registerCloudMedia).toHaveBeenCalledWith('healthmate-token', expect.objectContaining({
      file_id: 'cloud://test-env/files/photo.jpg',
      temp_url: 'https://storage.example.test/photo.jpg?signature=temporary',
      media_type: 'image',
      purpose: 'food_analysis',
    }))
    expect(result).toMatchObject({ media_id: 37, storage_backend: 'cloud_ref' })
  })

  it('rejects and signs out if the CloudBase identity differs from the ticket owner', async () => {
    mocks.auth.signInWithCustomTicket.mockResolvedValue({
      user: { uid: 'cloud-user-18', customUserId: 'hm_user_18' },
    })

    await expect(uploadCloudMedia(
      'healthmate-token',
      new Blob(['photo-bytes'], { type: 'image/jpeg' }),
      'photo.jpg',
      'image',
      'cloud-media-request-2',
      'food_analysis',
    )).rejects.toThrow('微信云身份校验失败')

    expect(mocks.auth.signOut).toHaveBeenCalledOnce()
    expect(mocks.storage.upload).not.toHaveBeenCalled()
    expect(mocks.registerCloudMedia).not.toHaveBeenCalled()
  })

  it('discards a ticket response that arrives after logout', async () => {
    let finishTicket
    mocks.requestCloudbaseTicket.mockReturnValue(new Promise(resolve => { finishTicket = resolve }))
    const upload = uploadCloudMedia(
      'healthmate-token',
      new Blob(['photo-bytes'], { type: 'image/jpeg' }),
      'photo.jpg',
      'image',
      'cloud-media-request-3',
      'food_analysis',
    )

    await vi.waitFor(() => expect(mocks.requestCloudbaseTicket).toHaveBeenCalledOnce())
    await resetCloudMediaSession()
    finishTicket({ ticket: 'late-ticket', uid: 'hm_user_17' })

    await expect(upload).rejects.toThrow('登录身份已变化')
    expect(mocks.auth.signInWithCustomTicket).not.toHaveBeenCalled()
    expect(mocks.storage.upload).not.toHaveBeenCalled()
  })

  it('deletes an uploaded object if the account changes before it can be registered', async () => {
    let finishUpload
    mocks.storage.upload.mockReturnValue(new Promise(resolve => { finishUpload = resolve }))
    const upload = uploadCloudMedia(
      'healthmate-token',
      new Blob(['photo-bytes'], { type: 'image/jpeg' }),
      'photo.jpg',
      'image',
      'cloud-media-request-4',
      'food_analysis',
    )

    await vi.waitFor(() => expect(mocks.storage.upload).toHaveBeenCalledOnce())
    let resetFinished = false
    const reset = resetCloudMediaSession().then(() => { resetFinished = true })
    await Promise.resolve()
    expect(resetFinished).toBe(false)
    finishUpload({ data: { id: 'cloud://test-env/files/orphan.jpg' } })

    await expect(upload).rejects.toThrow('登录身份已变化')
    await reset
    expect(mocks.storage.remove).toHaveBeenCalledWith(['cloud://test-env/files/orphan.jpg'])
    expect(mocks.storage.createSignedUrl).not.toHaveBeenCalled()
    expect(mocks.registerCloudMedia).not.toHaveBeenCalled()
    expect(mocks.auth.signOut).toHaveBeenCalledOnce()
    expect(mocks.storage.remove.mock.invocationCallOrder[0]).toBeLessThan(mocks.auth.signOut.mock.invocationCallOrder[0])
  })
})
