const config = require('../config/index')
function key(kind) { return `healthmate_pending_${kind}:${wx.getStorageSync('healthmate_user_id') || 'anonymous'}` }
function remember(kind, jobId, asset, extra = {}) {
  const pointer = { jobId, base: config.getApiBaseUrl(), asset: {
    media_id: asset.media_id, cloud_file_id: asset.cloud_file_id || '', size: asset.size || 0
  }, extra }
  wx.setStorageSync(key(kind), pointer)
}
function load(kind) {
  const pointer = wx.getStorageSync(key(kind))
  return pointer && pointer.base === config.getApiBaseUrl() ? pointer : null
}
function forget(kind) { wx.removeStorageSync(key(kind)) }
module.exports = { remember, load, forget }
