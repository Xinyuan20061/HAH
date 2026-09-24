const api = require('./request')
const cloudMedia = require('./cloudMedia')
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))

async function pollJob({ path, jobId, asset, onStatus, cancelled = () => false, maxMs = 300000, wait = sleep, now = Date.now }) {
  const deadline = now() + maxMs
  let delay = 1000, refreshes = 0
  while (now() < deadline && !cancelled()) {
    const job = await api.get(`${path}/${jobId}`, { allowCache: false })
    const labels = {
      queued: job.error_code === 'cloud_fallback' ? '电脑端未在线，任务已保留' : '任务排队中',
      processing: `正在处理 ${job.progress || 0}%`,
      waiting_source_refresh: '图片地址已过期，请重试刷新',
      done: '完成', failed: '分析失败'
    }
    if (onStatus) onStatus(labels[job.status] || '任务状态未知', job)
    if (job.status === 'done') {
      if (!job.result) throw new Error('任务未返回有效结果')
      return job.result
    }
    const expired = job.status === 'waiting_source_refresh' ||
      (job.status === 'failed' && ['media_url_expired', 'media_url_missing'].includes(job.error_code))
    if (expired) {
      if (!asset.cloud_file_id || refreshes >= 2) throw new Error('媒体下载地址无法自动刷新，请稍后重新查看原任务')
      await cloudMedia.refresh(asset.media_id, asset.cloud_file_id)
      refreshes += 1
      delay = 1000
      continue
    }
    if (job.status === 'failed') {
      if (String(job.error_code || '').startsWith('vlm_')) throw new Error('未能识别这张照片，请重拍或手动记录')
      throw new Error(job.error || '处理失败，请稍后重试')
    }
    if (!['queued', 'processing'].includes(job.status)) throw new Error('不支持的任务状态，请更新服务')
    await wait(Math.min(delay, Math.max(0, deadline - now())))
    delay = Math.min(5000, Math.round(delay * 1.25))
  }
  const error = new Error(cancelled() ? '已停止查看，任务会保留' : '电脑端未在线，任务已保留，可稍后再次查看')
  error.pending = true
  throw error
}

module.exports = { pollJob }
