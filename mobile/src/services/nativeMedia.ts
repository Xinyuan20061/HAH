import { Capacitor, registerPlugin, type PluginListenerHandle } from '@capacitor/core'

export interface NativeVideoSource {
  uri: string
  file_name: string
  content_type: 'video/mp4' | 'video/quicktime'
  size_bytes: number
  thumbnail_data_url?: string
}

interface NativeMediaPlugin {
  pickVideo(): Promise<NativeVideoSource>
  captureVideo(): Promise<NativeVideoSource>
  uploadFile(request: {
    uri: string
    transfer_id: string
    upload_url: string
    headers: Record<string, string>
    content_type: string
    size_bytes: number
  }): Promise<{ status: number }>
  cancelUpload(request: { transfer_id: string }): Promise<void>
  releaseMedia(request: { uri: string }): Promise<void>
  addListener(
    eventName: 'uploadProgress',
    listener: (event: { transfer_id: string; percent: number }) => void,
  ): Promise<PluginListenerHandle>
}

const nativeMedia = registerPlugin<NativeMediaPlugin>('NativeMedia')

export function canUseNativeMediaPicker() {
  return Capacitor.isNativePlatform() && Capacitor.getPlatform() === 'android'
}

export async function pickNativeVideo(mode: 'pick' | 'capture') {
  if (!canUseNativeMediaPicker()) throw new Error('原生视频选择仅支持 Android 应用')
  const selected = mode === 'capture'
    ? await nativeMedia.captureVideo()
    : await nativeMedia.pickVideo()
  if (!selected.uri || !selected.file_name || selected.size_bytes <= 0) {
    throw new Error('无法读取视频文件信息，请重新选择')
  }
  if (!['video/mp4', 'video/quicktime'].includes(selected.content_type)) {
    await releaseNativeVideo(selected.uri)
    throw new Error('当前仅支持 MP4 或 MOV 视频')
  }
  return selected
}

export async function uploadNativeVideo(
  source: NativeVideoSource,
  request: {
    uploadUrl: string
    headers: Record<string, string>
    contentType: string
    sizeBytes: number
  },
  onProgress?: (percent: number) => void,
  signal?: AbortSignal,
) {
  if (!canUseNativeMediaPicker()) throw new Error('原生媒体上传仅支持 Android 应用')
  if (signal?.aborted) throw new DOMException('上传已取消', 'AbortError')
  const transferId = crypto.randomUUID()
  let listener: PluginListenerHandle | null = null
  const onAbort = () => {
    void nativeMedia.cancelUpload({ transfer_id: transferId })
  }
  signal?.addEventListener('abort', onAbort, { once: true })
  try {
    listener = await nativeMedia.addListener('uploadProgress', event => {
      if (event.transfer_id === transferId) onProgress?.(event.percent)
    })
    if (signal?.aborted) {
      onAbort()
      throw new DOMException('上传已取消', 'AbortError')
    }
    const result = await nativeMedia.uploadFile({
      uri: source.uri,
      transfer_id: transferId,
      upload_url: request.uploadUrl,
      headers: request.headers,
      content_type: request.contentType,
      size_bytes: request.sizeBytes,
    })
    if (result.status < 200 || result.status >= 300) {
      throw new Error(`COS/S3 上传失败（HTTP ${result.status}）`)
    }
  } finally {
    signal?.removeEventListener('abort', onAbort)
    await listener?.remove()
  }
}

export async function releaseNativeVideo(uri: string) {
  if (!uri || !canUseNativeMediaPicker()) return
  await nativeMedia.releaseMedia({ uri }).catch(() => undefined)
}
