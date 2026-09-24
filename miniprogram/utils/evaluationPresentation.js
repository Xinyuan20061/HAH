const INTENT_LABELS = {
  plan: '计划规划',
  exercise_knowledge: '运动指导',
  nutrition: '营养建议',
  general: '综合健康',
  safety: '安全守护',
  unknown: '其他'
}

function providerLabel(name) {
  if (name === 'deepseek-user' || name === 'deepseek-system') return '在线回答'
  if (name === 'local') return '本地回答'
  if (name === 'rules-fallback' || name === 'fallback') return '基础建议'
  if (name === 'safety-rule' || name === 'demo-safety') return '安全响应'
  if (name === 'demo') return '演示回答'
  return '其他方式'
}

function rows(distribution, labeler) {
  const grouped = {}
  Object.entries(distribution || {}).forEach(([key, count]) => {
    const label = labeler(key)
    grouped[label] = (grouped[label] || 0) + Number(count || 0)
  })
  return Object.entries(grouped)
    .map(([label, count]) => ({ label, count }))
    .sort((a, b) => b.count - a.count)
}

function formatAgentStats(stats) {
  if (!stats) return null
  const latency = stats.latency || {}
  const feedback = stats.insight_feedback || {}
  const experiments = stats.micro_experiments || {}
  const feedbackLabels = { helpful: '有帮助', inaccurate: '不准确', resolved: '已处理' }
  return {
    totalRuns: Number(stats.total_runs || 0),
    fallbackDisplay: stats.ai_unavailable_rate_pct === null || stats.ai_unavailable_rate_pct === undefined ? '暂无样本' : `${stats.ai_unavailable_rate_pct}%`,
    p50Display: latency.p50_ms === null || latency.p50_ms === undefined ? '暂无样本' : `${latency.p50_ms} ms`,
    p95Display: latency.p95_ms === null || latency.p95_ms === undefined ? '暂无样本' : `${latency.p95_ms} ms`,
    intents: rows(stats.intent_distribution, key => INTENT_LABELS[key] || '其他'),
    providers: rows(stats.provider_distribution, providerLabel),
    feedbackSample: Number(feedback.sample_size || 0),
    helpfulRateDisplay: feedback.helpful_rate_pct === null || feedback.helpful_rate_pct === undefined ? '暂无样本' : `${feedback.helpful_rate_pct}%`,
    resolvedCount: Number(feedback.resolved_count || 0),
    feedbackRows: rows(feedback.distribution, key => feedbackLabels[key] || '其他'),
    feedbackNote: feedback.note || '',
    experimentStarted: Number(experiments.started || 0),
    experimentCompleted: Number(experiments.completed || 0),
    experimentCancelled: Number(experiments.cancelled || 0),
    experimentConclusive: Number(experiments.conclusive_outcomes || 0),
    experimentTargetRateDisplay: experiments.target_met_rate_pct === null || experiments.target_met_rate_pct === undefined ? '暂无样本' : `${experiments.target_met_rate_pct}%`,
    experimentNote: experiments.note || '',
    note: stats.note || ''
  }
}

module.exports = { INTENT_LABELS, providerLabel, formatAgentStats }
