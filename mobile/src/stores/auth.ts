import { defineStore } from 'pinia'
import { resetCloudMediaSession } from '../services/cloudMedia'
import { completeAccountLink, loginMobileWechat, readCurrentUser, unlinkMobileAccount } from '../services/api'
import { clearSecureSession, loadSecureSession, requestNativeWechatCode, saveSecureSession } from '../services/nativeAuth'
import { clearAllUserJobs } from '../services/userScopedJobs.mjs'
import { useConversationStore } from './conversation'

interface PublicUser {
  id: number
  nickname: string
  linked_providers?: string[]
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    accessToken: '' as string,
    user: null as PublicUser | null,
    loading: false,
    restoring: true,
    error: '',
  }),
  getters: {
    authenticated: state => Boolean(state.accessToken),
  },
  actions: {
    async restoreSession() {
      try {
        const saved = await loadSecureSession()
        if (saved) {
          this.accessToken = saved.access_token
          this.user = saved.user
        }
      } catch {
        this.accessToken = ''
        this.user = null
        await clearSecureSession().catch(() => undefined)
      } finally {
        this.restoring = false
      }
    },
    async validateRestoredSession() {
      if (!this.accessToken) return
      try {
        this.user = await readCurrentUser(this.accessToken)
      } catch (error) {
        if (this.accessToken && typeof error === 'object' && error !== null && 'status' in error && error.status === 401) {
          await this.signOut()
        }
      }
    },
    async signInForDevelopment(nickname: string) {
      if (import.meta.env.MODE === 'production' || import.meta.env.VITE_AUTH_MODE !== 'dev' || import.meta.env.VITE_APP_ENV === 'production') {
        throw new Error('当前环境未启用开发登录')
      }
      this.loading = true
      this.error = ''
      try {
        const { loginDev } = await import('../services/devAuth')
        const result = await loginDev(nickname)
        this.accessToken = result.access_token
        this.user = result.user
        await clearSecureSession()
      } catch (error) {
        this.error = error instanceof Error ? error.message : '登录失败'
        throw error
      } finally {
        this.loading = false
      }
    },
    async signInWithWechat() {
      this.loading = true
      this.error = ''
      try {
        const code = await requestNativeWechatCode()
        const result = await loginMobileWechat(code)
        await saveSecureSession(result)
        this.accessToken = result.access_token
        this.user = result.user
      } catch (error) {
        this.error = error instanceof Error ? error.message : '微信登录失败'
        throw error
      } finally {
        this.loading = false
      }
    },
    async linkMiniProgramAccount(linkCode: string) {
      this.loading = true
      this.error = ''
      try {
        const result = await completeAccountLink(this.accessToken, linkCode)
        await resetCloudMediaSession()
        clearAllUserJobs()
        useConversationStore().clear()
        await saveSecureSession(result)
        this.accessToken = result.access_token
        this.user = result.user
      } catch (error) {
        this.error = error instanceof Error ? error.message : '账号关联失败'
        throw error
      } finally {
        this.loading = false
      }
    },
    async unlinkWechatMobile() {
      this.loading = true
      this.error = ''
      try {
        await unlinkMobileAccount(this.accessToken)
        await this.signOut()
      } catch (error) {
        this.error = error instanceof Error ? error.message : '解绑失败'
        throw error
      } finally {
        this.loading = false
      }
    },
    async signOut() {
      await resetCloudMediaSession()
      clearAllUserJobs()
      useConversationStore().clear()
      this.accessToken = ''
      this.user = null
      await clearSecureSession().catch(() => undefined)
    },
  },
})
