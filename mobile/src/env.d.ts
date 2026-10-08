/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_APP_ENV: 'local' | 'cloud-test' | 'production'
  readonly VITE_API_BASE_URL: string
  readonly VITE_AGENT_RESPONSE_MODE: 'ndjson' | 'full'
  readonly VITE_AUTH_MODE: 'dev' | 'wechat'
  readonly VITE_CLOUDBASE_ENV_ID: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
