const config = require('./config/index')
const { ensureLogin } = require('./utils/auth')

App({
  globalData: {},
  async onLaunch() {
    if (wx.cloud) {
      try {
        wx.cloud.init({
          env: config.cloudReady() ? config.CLOUD_ENV_ID : undefined,
          traceUser: true
        })
      } catch (e) { console.warn('wx.cloud.init failed', e) }
    }
    try { await ensureLogin() } catch (e) { console.warn('login failed', e) }
  }
})
