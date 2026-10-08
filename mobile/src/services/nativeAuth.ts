import { Capacitor, registerPlugin } from '@capacitor/core'

interface NativeSessionStorage {
  get(options: { key: string }): Promise<{ value?: string }>
  set(options: { key: string; value: string }): Promise<void>
  remove(options: { key: string }): Promise<void>
}

interface NativeWechatAuth {
  login(): Promise<{ code: string; state: string }>
}

const secureStorage = registerPlugin<NativeSessionStorage>('SecureStorage')
const wechatAuth = registerPlugin<NativeWechatAuth>('WechatAuth')
const SESSION_KEY = 'session.v1'

export interface StoredSession {
  access_token: string
  user: { id: number; nickname: string }
}

export async function requestNativeWechatCode(): Promise<string> {
  if (Capacitor.getPlatform() !== 'android') throw new Error('微信登录仅支持 Android 客户端')
  const result = await wechatAuth.login()
  if (!result.code || !result.state) throw new Error('微信未返回有效授权凭证')
  return result.code
}

export async function loadSecureSession(): Promise<StoredSession | null> {
  if (!Capacitor.isNativePlatform()) return null
  const saved = await secureStorage.get({ key: SESSION_KEY })
  if (!saved.value) return null
  const session = JSON.parse(saved.value) as Partial<StoredSession>
  if (!session.access_token || !session.user || !Number.isInteger(session.user.id)) {
    await clearSecureSession()
    return null
  }
  return session as StoredSession
}

export async function saveSecureSession(session: StoredSession): Promise<void> {
  if (!Capacitor.isNativePlatform()) return
  await secureStorage.set({ key: SESSION_KEY, value: JSON.stringify(session) })
}

export async function clearSecureSession(): Promise<void> {
  if (!Capacitor.isNativePlatform()) return
  await secureStorage.remove({ key: SESSION_KEY })
}
