const api = require('./request')
const config = require('../config/index')

function ext(path) {
  const m = String(path || '').match(/\.([A-Za-z0-9]+)(?:\?.*)?$/)
  return m ? '.' + m[1].toLowerCase() : '.bin'
}
function randomName() { return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}` }
function statSize(filePath) {
  return new Promise((resolve, reject) => wx.getFileSystemManager().getFileInfo({ filePath, success: r => r.size > 0 ? resolve(r.size) : reject(new Error('素材为空')), fail: () => reject(new Error('无法读取素材大小')) }))
}
async function tempUrlInfo(fileID) {
  const r = await wx.cloud.getTempFileURL({ fileList: [fileID] })
  const x = r.fileList && r.fileList[0]
  if (!x || !x.tempFileURL) throw new Error('无法获取云存储临时下载地址')
  return { url: x.tempFileURL, expiresIn: Math.max(300, Math.min(86400, Number(x.maxAge || 7200))) }
}
async function tempUrl(fileID) { return (await tempUrlInfo(fileID)).url }
async function uploadAndRegister(filePath, mediaType, originalName = '', contentType = '') {
  if (!config.cloudReady()) throw new Error('尚未配置微信云环境 ID')
  const suffix = ext(originalName || filePath)
  const uid = wx.getStorageSync('healthmate_user_id')
  if (!uid) throw new Error('请先登录后上传素材')
  const cloudPath = `healthmate/u${uid}/${mediaType}/${new Date().toISOString().slice(0,10)}/${randomName()}${suffix}`
  const size = await statSize(filePath)
  // Competition demos should keep source videos short; reject obviously oversized files before network upload.
  if (size > 120 * 1024 * 1024) throw new Error('素材过大，请压缩到 120MB 以内后再上传')
  const up = await wx.cloud.uploadFile({ cloudPath, filePath })
  const info = await tempUrlInfo(up.fileID)
  const asset = await api.post('/media/register-cloud', {
    file_id: up.fileID,
    temp_url: info.url,
    media_type: mediaType,
    original_name: originalName || cloudPath.split('/').pop(),
    content_type: contentType || '',
    size_bytes: size,
    expires_in: info.expiresIn
  })
  return Object.assign(asset, { cloud_file_id: up.fileID, temp_url: info.url })
}
async function refresh(mediaId, fileID) {
  const info = await tempUrlInfo(fileID)
  return api.put(`/media/${mediaId}/refresh-source`, { temp_url: info.url, expires_in: info.expiresIn })
}
async function deleteFiles(fileIDs) {
  const ids = Array.from(new Set((fileIDs || []).filter(Boolean)))
  let deleted = 0
  for (let i = 0; i < ids.length; i += 50) {
    const batch = ids.slice(i, i + 50)
    const r = await wx.cloud.deleteFile({ fileList: batch })
    const list = (r && r.fileList) || []
    const failed = batch.filter(id => !list.some(x => x.fileID === id && (x.status === 0 || /not exist|does not exist/i.test(String(x.errMsg || '').replace(/_/g, ' ')))))
    if (failed.length) throw new Error(`有 ${failed.length} 个云文件未能删除，请重试后再删除账户`)
    deleted += batch.length
  }
  return deleted
}
module.exports = { uploadAndRegister, refresh, tempUrl, tempUrlInfo, deleteFiles }
