import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import process from 'node:process'

export default defineConfig(({ mode }) => {
  if (mode === 'production') {
    const env = loadEnv(mode, process.cwd(), 'VITE_')
    const apiUrl = env.VITE_API_BASE_URL?.trim()
    if (env.VITE_APP_ENV !== 'production' || env.VITE_AUTH_MODE !== 'wechat' || !apiUrl?.startsWith('https://') || !apiUrl.endsWith('/api/v1') || !/^[A-Za-z0-9_-]{1,64}$/.test(env.VITE_CLOUDBASE_ENV_ID || '')) {
      throw new Error('正式构建要求 production 环境、微信登录、CloudBase 环境 ID 和 HTTPS /api/v1 后端地址；请勿把本地配置打入正式包。')
    }
  }

  return {
    plugins: [vue()],
    server: {
      host: '0.0.0.0',
      port: 5173,
      strictPort: true,
    },
    build: {
      target: 'es2022',
      sourcemap: false,
    },
  }
})
