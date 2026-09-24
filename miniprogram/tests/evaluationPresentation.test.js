const { test } = require('node:test')
const assert = require('node:assert/strict')
const { providerLabel, formatAgentStats } = require('../utils/evaluationPresentation')

test('provider names are grouped into user-facing processing modes', () => {
  assert.equal(providerLabel('deepseek-system'), '在线回答')
  assert.equal(providerLabel('deepseek-user'), '在线回答')
  assert.equal(providerLabel('local'), '本地回答')
  assert.equal(providerLabel('rules-fallback'), '基础建议')
  assert.equal(providerLabel('safety-rule'), '安全响应')
})

test('agent runtime stats preserve real zeroes and missing samples', () => {
  const view = formatAgentStats({total_runs:0,ai_unavailable_rate_pct:null,latency:{sample_size:0,p50_ms:null,p95_ms:null},intent_distribution:{},provider_distribution:{}})
  assert.equal(view.totalRuns, 0)
  assert.equal(view.fallbackDisplay, '暂无样本')
  assert.equal(view.p50Display, '暂无样本')
  assert.equal(view.helpfulRateDisplay, '暂无样本')
  assert.equal(view.feedbackSample, 0)
})

test('agent runtime distributions aggregate providers without exposing model brands', () => {
  const view = formatAgentStats({total_runs:4,ai_unavailable_rate_pct:25,latency:{p50_ms:310,p95_ms:900},intent_distribution:{plan:2,general:2},provider_distribution:{'deepseek-user':1,'deepseek-system':1,local:1,'rules-fallback':1},insight_feedback:{sample_size:3,helpful_rate_pct:50,resolved_count:1,distribution:{helpful:1,inaccurate:1,resolved:1}},micro_experiments:{started:3,completed:2,cancelled:1,conclusive_outcomes:2,target_met_rate_pct:50}})
  assert.deepEqual(view.providers,[{label:'在线回答',count:2},{label:'本地回答',count:1},{label:'基础建议',count:1}])
  assert.equal(view.fallbackDisplay,'25%')
  assert.equal(view.p50Display,'310 ms')
  assert.equal(view.helpfulRateDisplay,'50%')
  assert.deepEqual(view.feedbackRows,[{label:'有帮助',count:1},{label:'不准确',count:1},{label:'已处理',count:1}])
  assert.equal(view.experimentStarted,3)
  assert.equal(view.experimentCompleted,2)
  assert.equal(view.experimentTargetRateDisplay,'50%')
})
