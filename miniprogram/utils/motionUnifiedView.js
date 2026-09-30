// 统一动作分析结果契约 → 页面展示模型（规格 4.3 / 4.4）。
// 纯函数，不依赖 wx，便于单测。前端只按此形状对接，不实现后端。
const PIPELINE_VERSION = 'motion-unified-v1'
const STAGE_LABELS = {
  queued: '任务排队中',
  decoding: '正在读取视频',
  local_inference: '正在识别动作',
  evidence_ready: '证据已就绪，等待复核',
  visual_review: '正在云端视觉复核',
  feedback_generation: '正在生成点评',
  completed: '完成',
  partial: '完成（部分降级）',
  abstained: '暂不能确定',
  failed: '分析失败',
  cancelled: '已取消'
}
const TERMINAL_STATUSES = ['completed', 'partial', 'abstained', 'failed', 'cancelled']

function fmtSeconds(ms) {
  const n = Number(ms)
  if (!Number.isFinite(n) || n < 0) return '--'
  return (n / 1000).toFixed(1).replace(/\.0$/, '') + 's'
}
function reviewBadge(s) {
  if (s === 'used') return '云端视觉已复核'
  if (s === 'unavailable') return '云端视觉暂不可用（仅本地结果）'
  if (s === 'degraded') return '云端复核降级'
  return ''
}

// 关键边界：
//  - candidate_score 只进折叠详情并标注“模型候选分值，未经校准”，绝不放进主圆环当分数；
//  - calibrated_confidence 为 null 时不显示“判断把握度”；
//  - score.available 为 false 时显示“已识别类别，暂无可靠数值评分”，但仍保留关键帧与谨慎点评。
function buildViewModel(raw) {
  const r = raw || {}
  const recognition = r.recognition || {}
  const score = r.score || {}
  const summary = r.summary || {}
  const state = recognition.state || 'uncertain'
  return {
    analysisId: r.analysis_id,
    pipelineVersion: r.pipeline_version || PIPELINE_VERSION,
    state,
    stateBadge: state === 'recognized' ? '已识别' : state === 'uncertain' ? '暂不能确定' : '证据不足',
    detectedName: recognition.label_zh || recognition.label_id || '暂未确认动作',
    labelId: recognition.label_id || '',
    reason: recognition.reason || '',
    evidenceLabel: '所用证据',
    sourceList: (recognition.sources || []).join('、'),
    reviewStatus: recognition.review_status || '',
    reviewBadge: reviewBadge(recognition.review_status),
    summaryText: summary.text || '',
    summaryDegraded: !!summary.degraded,
    summaryDegradedNote: summary.degraded ? '云端点评暂不可用，以下为本地规则点评' : '',
    qualityAvailable: !!score.available,
    qualityOverall: score.overall == null ? '--' : Math.round(Number(score.overall)),
    qualityReps: score.reps == null ? '--' : score.reps,
    qualityDims: [
      { label: '完成度', value: score.completeness },
      { label: '稳定性', value: score.stability },
      { label: '节奏', value: score.rhythm_control }
    ].filter(d => d.value != null).map(d => ({ label: d.label, value: Math.round(Number(d.value)) })),
    noScoreNote: '已识别类别，暂无可靠数值评分',
    noScoreReason: score.reason_code === 'NO_VALIDATED_SCORER' ? '该动作暂无经过校准的评分器' : (score.reason_code || ''),
    candidatePct: Number.isFinite(Number(recognition.candidate_score)) ? Math.round(Number(recognition.candidate_score) * 100) : null,
    candidateNote: '模型候选分值，未经校准，不等于真实准确率',
    showConfidence: recognition.calibrated_confidence != null,
    confidencePct: recognition.calibrated_confidence == null ? null : Math.round(Number(recognition.calibrated_confidence) * 100),
    keyframes: (r.keyframes || []).slice(0, 4).map(k => ({
      id: k.id || '',
      tLabel: fmtSeconds(k.t_ms),
      phase: k.phase || '',
      finding: k.finding || '',
      advice: k.advice || '',
      imageUrl: k.image_url || '',
      evidenceTypeLabel: k.evidence_type === 'visual_observation' ? '画面观察' : (k.evidence_type || '')
    })),
    limitations: r.limitations || [],
    traceId: r.trace_id || '',
    abstained: state === 'abstained',
    uncertain: state === 'uncertain'
  }
}

module.exports = { buildViewModel, STAGE_LABELS, TERMINAL_STATUSES, fmtSeconds, PIPELINE_VERSION }
