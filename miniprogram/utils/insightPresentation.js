const PRESENTATION = {
  exercise_stall: { label: '运动节奏', actionLabel: '轻量开始', route: '/pages/workout/index', tone: 'amber' },
  sleep_deficit: { label: '恢复提醒', actionLabel: '记录今天', route: '/pages/checkin/index', tone: 'violet' },
  motion_decline: { label: '动作表现', actionLabel: '复查动作', route: '/pages/media/index', tone: 'blue' },
  weight_rise: { label: '趋势提醒', actionLabel: '查看趋势', route: '/pages/trends/index', tone: 'rose' },
  record_gap: { label: '记录提醒', actionLabel: '开始记录', route: '/pages/checkin/index', tone: 'green' }
}

const SEVERITY_LABEL = { high: '优先关注', medium: '建议关注', low: '温和提醒' }
const VARIANT_LABELS = { gentle: '温和版', standard: '标准版' }

function normalizeProposalHistory(history) {
  if (!history || !history.total || !history.preferred) return null
  const counts = history.counts || {}
  return {
    total: history.total,
    preferred: history.preferred,
    preferredLabel: VARIANT_LABELS[history.preferred] || '',
    preferredCount: counts[history.preferred] || 0,
    counts: counts,
    policy: history.policy || ''
  }
}

function normalizeInsight(item = {}) {
  const view = PRESENTATION[item.code] || { label: '健康提醒', actionLabel: '去看看', route: '/pages/checkin/index', tone: 'green' }
  const verdict = item.user_feedback && item.user_feedback.verdict
  const feedbackLabels = { helpful: '已标记有帮助', inaccurate: '已反馈不准确', resolved: '已标记处理完成' }
  return Object.assign({}, item, view, {
    severityLabel: SEVERITY_LABEL[item.severity] || '健康提醒',
    askPrompt: `结合我的健康记录，针对“${item.title || '这条提醒'}”给出今天能执行的一步，并说明建议依据。`,
    feedback: verdict || '',
    feedbackLabel: feedbackLabels[verdict] || '',
    experimentProposal: item.experiment_proposal || null,
    proposalHistory: normalizeProposalHistory(item.proposal_history),
    activeExperiment: item.active_experiment ? normalizeExperiment(item.active_experiment) : null
  })
}

function normalizeExperiment(item) {
  if (!item || typeof item !== 'object') return null
  const progress = item.progress || {}
  const protocol = item.protocol || {}
  const value = progress.value === null || progress.value === undefined ? '暂无记录' : `${progress.value}${progress.unit || ''}`
  const target = progress.target_value === null || progress.target_value === undefined ? '等待基线' : `${progress.target_value}${progress.target_unit || ''}`
  const statusLabels = { active: '进行中', ready_to_review: '待复盘', completed: '已完成', cancelled: '已停止' }
  return Object.assign({}, item, {
    statusLabel: statusLabels[item.display_status || item.status] || '进行中',
    progressValueDisplay: value,
    progressTargetDisplay: target,
    progressPct: Math.max(0, Math.min(100, Number(progress.progress_pct || 0))),
    targetMet: Boolean(progress.target_met),
    dailyAction: protocol.daily_action || '',
    measurement: protocol.measurement || '',
    stopCondition: protocol.stop_condition || ''
  })
}

function normalizeInsights(payload) {
  const raw = payload && Array.isArray(payload.insights) ? payload.insights : []
  return raw.map(normalizeInsight)
}

module.exports = { PRESENTATION, normalizeInsight, normalizeInsights, normalizeExperiment }
