const api = require('./request')

const STATUS_LABELS = {
  active: '进行中',
  awaiting_review: '等待复查',
  reviewed: '已复查',
  stopped: '已停止'
}

const CONCLUSION_LABELS = {
  supports_observed_target: '本周期观察到预设方向的支持',
  target_not_supported: '本周期未观察到预设方向的支持',
  ambiguous: '结果介于判定区间，无法确定',
  insufficient_exposure: '执行暴露不足，无法判断观察结果',
  insufficient_data: '证据不足，无法判断',
  incomparable: '条件不一致，无法比较',
  stopped: '已停止；不作效果结论'
}

const REASON_LABELS = {
  window_not_closed: '观察窗口尚未结束',
  actual_strategy_or_context_mismatch: '实际执行方式或情境与冻结协议不同，不能直接比较',
  adverse_event_requires_runtime_stop: '周期因安全原因停止，不作支持性结论',
  execution_does_not_establish_adequate_exposure: '执行记录尚未达到预设门槛',
  baseline_context_mismatch: '基线情境与协议不匹配',
  confounded: '记录中存在可能影响比较的其他因素',
  insufficient_followup: '周期内有效观察不足',
  insufficient_comparable_pairs: '可配对比较的基线与周期内记录不足',
  baseline_followup_overlap: '基线与周期内使用了重复来源'
}

function statusLabel(status) { return STATUS_LABELS[status] || '状态待更新' }
function conclusionLabel(value) { return CONCLUSION_LABELS[value] || '暂不可判断' }
function reasonLabel(value) { return REASON_LABELS[value] || '当前证据不满足判定条件' }

function formatTime(value) {
  if (!value) return '时间未记录'
  const date = new Date(value)
  if (!isFinite(date.getTime())) return '时间未记录'
  const pad = n => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function confirmProposal(proposal, title, content) {
  return new Promise((resolve, reject) => wx.showModal({
    title: title || proposal.title || '确认这项操作',
    content: content || `${proposal.summary || '这项操作会更新个人策略周期。'}\n\n只有你确认后才会执行。`,
    confirmText: '确认执行',
    cancelText: '暂不执行',
    success: async result => {
      if (!result.confirm) return resolve(null)
      try {
        const response = await api.post(`/agent/actions/${proposal.proposal_id}/confirm`, {
          version: proposal.version || 1,
          confirmation: true
        })
        resolve(response)
      } catch (error) { reject(error) }
    },
    fail: () => resolve(null)
  }))
}

module.exports = { statusLabel, conclusionLabel, reasonLabel, formatTime, confirmProposal }
