import type { CapacitorConfig } from '@capacitor/cli'
import process from 'node:process'

// Local Android builds are deliberately explicit. The production/default
// Capacitor sync always generates an HTTPS WebView origin.
const localBuild = process.env.CAPACITOR_MODE === 'debug'
const releaseBuild = process.env.CAPACITOR_MODE === 'production'
const configuredReleaseId = process.env.ANDROID_RELEASE_APPLICATION_ID?.trim()
const baseApplicationId = configuredReleaseId || 'com.hah.healthmate'

const config: CapacitorConfig = {
  appId: releaseBuild ? baseApplicationId : `${baseApplicationId}.dev`,
  appName: releaseBuild ? 'HAH 健康助手' : 'HAH 开发版',
  webDir: 'dist',
  android: {
    allowMixedContent: false,
  },
  server: {
    androidScheme: localBuild ? 'http' : 'https',
  },
  plugins: {
    SplashScreen: {
      launchAutoHide: false,
      backgroundColor: '#f7f7f2',
      showSpinner: false,
    },
    SystemBars: {
      insetsHandling: 'native',
      initialViewportFitValueHint: 'cover',
    },
  },
}

export default config
