import {
  abortMobileUploadSession,
  completeMobileUploadSession,
  createMobileUploadSession,
  readMediaPlayback,
  readMobileUploadOptions,
  registerCloudMedia,
  refreshCloudMediaSource,
  requestCloudbaseTicket,
  uploadMediaToDevelopmentBackend,
} from './api'
import { releaseNativeVideo, uploadNativeVideo, type NativeVideoSource } from './nativeMedia'

const TEMP_URL_SECONDS = 3600
let cloudApp: any = null
let cloudAuth: any = null
let loginTask: Promise<string> | null = null
let cloudSessionGeneration = 0
const activeCloudbaseUploads = new Set<Promise<void>>()

function beginCloudbaseUpload(): () => void {
  let finish!: () => void
  const operation = new Promise<void>(resolve => { finish = resolve })
  activeCloudbaseUploads.add(operation)
  return () => {
    activeCloudbaseUploads.delete(operation)
    finish()
  }
}

async function getCloudbase() {
  const envId = import.meta.env.VITE_CLOUDBASE_ENV_ID?.trim()
  if (!envId) throw new Error('尚未配置微信云环境，请先完成应用环境设置')
  if (!cloudApp) {
    const sdk = await import('@cloudbase/js-sdk')
    const cloudbase = (sdk as any).default || sdk
    cloudApp = cloudbase.init({ env: envId })
    // Storage authorization is scoped to this process. The server issues a
    // fresh ticket on demand; no CloudBase refresh credential is persisted.
    cloudAuth = cloudApp.auth({ persistence: 'none' })
  }
  return { app: cloudApp, auth: cloudAuth! }
}

async function signIn(accessToken: string): Promise<{ app: any; uid: string; generation: number }> {
  const generation = cloudSessionGeneration
  const { app, auth } = await getCloudbase()
  if (generation !== cloudSessionGeneration) throw new Error('登录身份已变化，请重新尝试媒体操作')
  if (loginTask) {
    const uid = await loginTask
    if (generation !== cloudSessionGeneration) throw new Error('登录身份已变化，请重新尝试媒体操作')
    return { app, uid, generation }
  }
  const task = (async () => {
    const issued = await requestCloudbaseTicket(accessToken)
    if (generation !== cloudSessionGeneration) throw new Error('登录身份已变化，请重新尝试媒体操作')
    const signedIn = await auth.signInWithCustomTicket(() => Promise.resolve(issued.ticket))
    if (generation !== cloudSessionGeneration) {
      await auth.signOut().catch(() => undefined)
      throw new Error('登录身份已变化，请重新尝试媒体操作')
    }
    // SDK 3.x returns ILoginState { user }; it does not wrap this in `data`.
    const signedInUser = signedIn?.user
    const signedInUid = signedInUser?.customUserId || signedInUser?.uid
    if (!signedInUid || signedInUid !== issued.uid) {
      await auth.signOut().catch(() => undefined)
      throw new Error('微信云身份校验失败，请检查 CloudBase 自定义登录配置')
    }
    return issued.uid
  })()
  loginTask = task
  try {
    const uid = await task
    return { app, uid, generation }
  } finally {
    if (loginTask === task) loginTask = null
  }
}

function safeExtension(name: string, contentType: string, mediaType: 'image' | 'video') {
  const fromName = name.match(/\.([a-zA-Z0-9]{1,8})$/)?.[1]?.toLowerCase()
  const extension = fromName || (contentType === 'image/png' ? 'png'
    : contentType === 'image/webp' ? 'webp'
      : contentType === 'video/quicktime' ? 'mov'
        : mediaType === 'video' ? 'mp4' : 'jpg')
  const allowed = mediaType === 'video' ? ['mp4', 'mov'] : ['jpg', 'jpeg', 'png', 'webp']
  if (!allowed.includes(extension)) throw new Error('当前只支持 JPG、PNG、WebP、MP4 或 MOV 文件')
  return extension
}

async function getTempUrl(app: any, fileId: string) {
  const result = await app.storage.from().createSignedUrl(fileId, TEMP_URL_SECONDS)
  if (result.error || !result.data?.signedUrl) throw new Error('微信云存储暂时无法提供文件访问地址')
  return result.data.signedUrl
}

export async function uploadCloudMedia(
  accessToken: string,
  file: Blob | null,
  originalName: string,
  mediaType: 'image' | 'video',
  requestId: string,
  purpose: 'food_analysis' | 'motion_analysis',
  nativeSource?: NativeVideoSource | null,
  onProgress?: (percent: number) => void,
  signal?: AbortSignal,
): Promise<Record<string, any>> {
  const sizeBytes = file?.size || nativeSource?.size_bytes || 0
  if (!sizeBytes) throw new Error('素材为空，请重新选择')
  if ((purpose === 'food_analysis' && mediaType !== 'image') || (purpose === 'motion_analysis' && mediaType !== 'video')) {
    throw new Error('媒体类型与当前操作不匹配')
  }
  const nameExtension = originalName.match(/\.([a-zA-Z0-9]{1,8})$/)?.[1]?.toLowerCase()
  const inferredType: Record<string, string> = {
    jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', webp: 'image/webp',
    mp4: 'video/mp4', mov: 'video/quicktime',
  }
  const contentType = file?.type || nativeSource?.content_type || inferredType[nameExtension || ''] || (mediaType === 'video' ? 'video/mp4' : 'image/jpeg')
  const allowedTypes = mediaType === 'video'
    ? ['video/mp4', 'video/quicktime']
    : ['image/jpeg', 'image/png', 'image/webp']
  if (!allowedTypes.includes(contentType)) throw new Error('文件格式不受支持，请选择 JPG、PNG、WebP、MP4 或 MOV')
  const options = await readMobileUploadOptions(accessToken)
  const purposeLimit = options.max_upload_bytes_by_purpose?.[purpose] ?? options.max_upload_bytes
  if (sizeBytes > purposeLimit) throw new Error('文件超过当前用途的服务端上传限制')
  if (options.storage_backend === 'local') {
    if (!file) throw new Error('当前开发存储模式不支持原生视频流式上传，请使用系统文件选择器')
    return uploadMediaToDevelopmentBackend(accessToken, file, originalName)
  }
  if (options.storage_backend === 's3') {
    const session = await createMobileUploadSession(accessToken, {
      request_id: requestId,
      original_name: originalName.slice(0, 255) || `healthmate.${safeExtension(originalName, contentType, mediaType)}`,
      content_type: contentType,
      media_type: mediaType,
      size_bytes: sizeBytes,
      purpose,
    })
    if (session.already_completed) {
      if (nativeSource) await releaseNativeVideo(nativeSource.uri)
      return { media_id: session.media_id, cloud_file_id: '', media_type: mediaType, size: sizeBytes, storage_backend: 's3' }
    }
    try {
      if (nativeSource) {
        await uploadNativeVideo(nativeSource, {
          uploadUrl: String(session.upload_url || ''),
          headers: session.headers || { 'Content-Type': contentType },
          contentType,
          sizeBytes,
        }, onProgress, signal)
      } else {
        if (!file) throw new Error('无法读取所选素材，请重新选择')
        const upload = await fetch(String(session.upload_url || ''), {
          method: session.method || 'PUT',
          headers: session.headers || { 'Content-Type': contentType },
          body: file,
          signal,
        })
        if (!upload.ok) throw new Error(`COS/S3 上传失败（HTTP ${upload.status}），请检查存储桶 CORS 与签名配置`)
      }
      const completed = await completeMobileUploadSession(accessToken, session.media_id)
      if (nativeSource) await releaseNativeVideo(nativeSource.uri)
      onProgress?.(100)
      return { ...completed, cloud_file_id: '', media_type: mediaType, file_name: originalName, storage_backend: 's3' }
    } catch (error) {
      await abortMobileUploadSession(accessToken, session.media_id).catch(() => undefined)
      if (nativeSource && signal?.aborted) await releaseNativeVideo(nativeSource.uri)
      throw error
    }
  }
  if (!file) throw new Error('当前媒体存储模式需要系统文件选择器，请重新选择视频')
  const ext = safeExtension(originalName, contentType, mediaType)
  const { app, uid, generation } = await signIn(accessToken)
  const day = new Date().toISOString().slice(0, 10)
  const name = `${crypto.randomUUID()}.${ext}`
  const cloudPath = `healthmate/${uid}/${mediaType}/${day}/${name}`
  const storage = app.storage.from()
  const finishCloudbaseUpload = beginCloudbaseUpload()
  let fileId = ''
  try {
    if (generation !== cloudSessionGeneration) throw new Error('登录身份已变化，请重新尝试媒体操作')
    const uploaded = await storage.upload(cloudPath, file, { contentType, upsert: false })
    fileId = typeof uploaded.data?.id === 'string' ? uploaded.data.id : ''
    if (uploaded.error || !fileId) throw new Error('微信云存储上传失败，请检查网络和存储权限')
    if (!fileId.startsWith('cloud://')) throw new Error('微信云存储没有返回有效文件编号，请重试')
    if (generation !== cloudSessionGeneration) throw new Error('登录身份已变化，请重新尝试媒体操作')
    const tempUrl = await getTempUrl(app, fileId)
    if (generation !== cloudSessionGeneration) throw new Error('登录身份已变化，请重新尝试媒体操作')
    const asset = await registerCloudMedia(accessToken, {
      file_id: fileId,
      temp_url: tempUrl,
      media_type: mediaType,
      original_name: originalName.slice(0, 255) || name,
      content_type: contentType,
      size_bytes: sizeBytes,
      expires_in: TEMP_URL_SECONDS,
      purpose,
    })
    return { ...asset, cloud_file_id: fileId, temp_url: tempUrl }
  } catch (error) {
    if (fileId) await storage.remove([fileId]).catch(() => undefined)
    throw error
  } finally {
    finishCloudbaseUpload()
  }
}

export async function refreshCloudMedia(accessToken: string, mediaId: number, fileId: string) {
  if (!fileId) return readMediaPlayback(accessToken, mediaId)
  const { app } = await signIn(accessToken)
  const tempUrl = await getTempUrl(app, fileId)
  return refreshCloudMediaSource(accessToken, mediaId, {
    temp_url: tempUrl,
    expires_in: TEMP_URL_SECONDS,
  })
}

export async function resetCloudMediaSession() {
  cloudSessionGeneration += 1
  const auth = cloudAuth
  loginTask = null
  cloudAuth = null
  cloudApp = null
  await Promise.all([...activeCloudbaseUploads])
  if (auth) await auth.signOut().catch(() => undefined)
}
