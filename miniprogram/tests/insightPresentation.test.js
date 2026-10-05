const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { normalizeInsight, normalizeInsights, normalizeExperiment } = require('../utils/insightPresentation')

test('each proactive signal maps to a concrete next step', () => {
  const expected = {
    exercise_stall: '/pages/workout/index',
    sleep_deficit: '/pages/checkin/index',
    motion_decline: '/pages/media/index',
    weight_rise: '/pages/trends/index',
    record_gap: '/pages/checkin/index'
  }
  for (const [code, route] of Object.entries(expected)) {
    const item = normalizeInsight({ code, title: code, severity: 'medium' })
    assert.equal(item.route, route)
    assert.ok(item.actionLabel)
    assert.match(item.askPrompt, /建议依据/)
  }
})

test('unknown signals degrade to a safe recording action', () => {
  const item = normalizeInsight({ code: 'future_signal', title: '新提醒' })
  assert.equal(item.route, '/pages/checkin/index')
  assert.equal(item.label, '健康提醒')
})

test('recent feedback is restored when the insight page reloads', () => {
  const item = normalizeInsight({ code: 'record_gap', title: '记录提醒', user_feedback: { verdict: 'resolved' } })
  assert.equal(item.feedback, 'resolved')
  assert.equal(item.feedbackLabel, '已标记处理完成')
})

test('malformed insight payload produces an empty list', () => {
  assert.deepEqual(normalizeInsights(null), [])
  assert.deepEqual(normalizeInsights({ insights: 'invalid' }), [])
})

test('micro experiment presentation preserves measured progress and causal boundary', () => {
  const item = normalizeExperiment({
    id: 8,
    status: 'active',
    display_status: 'ready_to_review',
    progress: { value: 2, unit: '天', target_value: 3, target_unit: '天', progress_pct: 67 },
    protocol: { daily_action: '记录睡眠', measurement: '有记录天数', stop_condition: '不适时停止' },
    boundary: '结果不能证明因果。'
  })
  assert.equal(item.statusLabel, '待复盘')
  assert.equal(item.progressValueDisplay, '2天')
  assert.equal(item.progressTargetDisplay, '3天')
  assert.equal(item.progressPct, 67)
  assert.match(item.boundary, /不能证明因果/)
})

test('health insight page exposes evidence, advice and user-controlled actions', () => {
  const page = fs.readFileSync(path.join(__dirname, '..', 'pages', 'insights', 'index.wxml'), 'utf8')
  assert.match(page, /观察到/)
  assert.match(page, /建议先做/)
  assert.match(page, /bindtap="openAction"/)
  assert.match(page, /bindtap="askAssistant"/)
  assert.match(page, /不把缺失数据当成异常/)
  assert.match(page, /dataQuality\.coverage_pct/)
  assert.match(page, /dataQuality\.message/)
  assert.match(page, /data-verdict="helpful"/)
  assert.match(page, /data-verdict="inaccurate"/)
  assert.match(page, /data-verdict="resolved"/)
  assert.match(page, /仅用于质量评测，不自动训练/)
  assert.match(page, /个人微实验/)
  assert.match(page, /startExperiment/)
  assert.match(page, /停止条件/)
  assert.match(page, /不证明因果/)
})

test('contract evidence renders as understandable basis, not raw jargon', () => {
  const item = normalizeInsight({
    code: 'exercise_stall',
    title: '运动断档',
    evidence: '近 7 天仅 1 天有运动记录。',
    evidence_contract: {
      facts: [
        { name: 'exercise_days', value: 1, unit: '天', source: 'confirmed_records' },
        { name: 'consecutive_no_exercise_days', value: 4, unit: '天', source: 'confirmed_records' }
      ],
      data_coverage: { observed_days: 1, expected_days: 7 },
      limitations: ['3 天没有运动记录，无法判断是否实际未运动。'],
      evidence_type: 'record_observation'
    },
    action_timeline: [
      { decision_id: 'dec-abc', variant: 'gentle', status: 'completed', start_date: '2026-09-20', end_date: '2026-09-22', outcome: 'insufficient_data' },
      { decision_id: 'dec-def', variant: 'standard', status: 'active', start_date: '2026-09-24', end_date: '2026-09-28', outcome: null }
    ]
  })
  assert.equal(item.evidenceView.factLines.length, 2)
  assert.equal(item.evidenceView.factLines[0].label, '有运动记录的天数')
  assert.equal(item.evidenceView.factLines[0].value, '1天')
  assert.equal(item.evidenceView.coverageText, '记录覆盖 1 / 7 天')
  assert.ok(item.evidenceView.limitations[0].includes('无法判断'))
  assert.equal(item.evidenceView.evidenceType, 'record_observation')
  assert.equal(item.actionTimeline[0].statusLabel, '已复盘')
  assert.equal(item.actionTimeline[0].outcomeLabel, '记录不足，未下结论')
  assert.equal(item.actionTimeline[1].statusLabel, '进行中')
  assert.equal(item.actionTimeline[1].variantLabel, '标准版')
})

test('timeline and basis blocks are present in the insight page', () => {
  const page = fs.readFileSync(path.join(__dirname, '..', 'pages', 'insights', 'index.wxml'), 'utf8')
  assert.match(page, /依据与数据覆盖/)
  assert.match(page, /我们还不知道/)
  assert.match(page, /历史行动/)
  assert.match(page, /item\.actionTimeline/)
  assert.match(page, /item\.evidenceView\.factLines/)
})

test('chat renders the decision explanation returned by the health agent', () => {
  const script = fs.readFileSync(path.join(__dirname, '..', 'pages', 'chat', 'index.js'), 'utf8')
  const view = fs.readFileSync(path.join(__dirname, '..', 'pages', 'chat', 'index.wxml'), 'utf8')
  assert.match(script, /presentTrace\(r\.trace,\s*r\.decision_explanation\)/)
  assert.match(view, /为什么这样建议/)
  assert.match(view, /msg\.trace\.reasons/)
})
