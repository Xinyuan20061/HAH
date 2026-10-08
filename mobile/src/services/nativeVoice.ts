import { Capacitor, registerPlugin, type PluginListenerHandle } from '@capacitor/core'

interface VoiceCapturePlugin {
  startRecording(): Promise<void>
  stopRecording(): Promise<{
    audio_base64: string
    duration_ms: number
    format: 'm4a'
    mime_type: 'audio/mp4'
  }>
  cancelRecording(): Promise<{ cancelled: boolean }>
  addListener(eventName: 'recordingInterrupted', listener: (event: { reason: string }) => void): Promise<PluginListenerHandle>
}

const voiceCapture = registerPlugin<VoiceCapturePlugin>('VoiceCapture')

export async function startVoiceCapture() {
  if (!Capacitor.isNativePlatform()) throw new Error('语音输入需要在 Android 应用中使用')
  await voiceCapture.startRecording()
}

export async function stopVoiceCapture() {
  if (!Capacitor.isNativePlatform()) throw new Error('当前设备不支持录音')
  return voiceCapture.stopRecording()
}

export async function cancelVoiceCapture() {
  if (Capacitor.isNativePlatform()) await voiceCapture.cancelRecording()
}

export async function listenForVoiceCaptureInterruption(listener: (reason: string) => void) {
  if (!Capacitor.isNativePlatform()) return null
  try {
    return await voiceCapture.addListener('recordingInterrupted', event => listener(event.reason))
  } catch {
    return null
  }
}
