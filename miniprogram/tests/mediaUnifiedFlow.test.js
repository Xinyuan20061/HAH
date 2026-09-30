'use strict'
// 统一动作分析页（P0-C）的行为契约守卫：
//   · 只有一个“开始分析”入口，不再有“开始查看 / 400 类动作识别”两个按钮
//   · 首次 POST 创建统一任务，重复/重进复用同一任务（Idempotency-Key + 轮询 GET）
//   · 主结果不显示假的 100 分：候选分值折叠并标注“未经校准”；calibrated_confidence=null 不报把握度
//   · 文案用“关键帧拆解”；低光/遮挡/证据不足 → “暂不能确定”；降级如实显示
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const ROOT = path.join(__dirname, '..')
const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf8')

const js = read('pages/media/index.js')
const wxml = read('pages/media/index.wxml')
const { buildViewModel } = require('../utils/motionUnifiedView')

// 规格 4.4 的示例契约
const SAMPLE = {
  analysis_id: 318,
  status: 'completed',
  pipeline_version: 'motion-unified-v1',
  recognition: {
    state: 'recognized',
    label_id: 'front_raises',
    label_zh: '前平举',
    candidate_score: 0.82,
    calibrated_confidence: null,
    evidence: ['frame:0', 'frame:2', 'pose:shoulder_range'],
    sources: ['kinetics400', 'mediapipe', 'deepseek_vision'],
    review_status: 'used',
    reason: '手臂在身体前方向上抬起；画面未覆盖足够周期确认全部细节'
  },
  score: { available: false, reason_code: 'NO_VALIDATED_SCORER' },
  summary: { text: '画面更像前平举。可以先放慢抬起和放下的速度。', source: 'deepseek_grounded', degraded: false },
  keyframes: [
    { id: 'frame:0', t_ms: 1200, phase: '准备', finding: '双臂接近身体两侧', advice: '保持站姿稳定', image_url: 'https://signed/0.jpg', evidence_type: 'visual_observation' }
  ],
  limitations: ['结果仅供一般健身参考'],
  trace_id: 'trace-abc'
}

test('单按钮入口：wxml 只绑 startAnalysis，不再有两个旧按钮', () => {
  assert.match(wxml, /bindtap="startAnalysis"/)
  assert.doesNotMatch(wxml, /开始查看/)
  assert.doesNotMatch(wxml, /400 类动作识别/)
  assert.doesNotMatch(wxml, /analyzeKinetics/)
  assert.doesNotMatch(js, /analyzeKinetics/)
  assert.doesNotMatch(js, /kinetics-jobs/)
  assert.doesNotMatch(js, /motion-jobs'/)
})

test('幂等复用：首次 POST 统一任务并带 Idempotency-Key，之后只轮询 GET', () => {
  assert.match(js, /api\.post\('\/media\/motion-analyses'/)
  assert.match(js, /'Idempotency-Key':\s*idem/)
  assert.match(js, /consent_deepseek_frames:\s*!!this\.data\.consentDeepseek/)
  assert.match(js, /requested_exercise:\s*requested/)
  assert.match(js, /pipeline_version:\s*PIPELINE_VERSION/)
  // 复用：已有 analysisId 时跳过创建
  assert.match(js, /if \(!this\.data\.analysisId\)/)
  // 纯读取轮询，不触发模型调用
  assert.match(js, /api\.get\(`\/media\/motion-analyses\/\$\{analysisId\}`/)
})

test('无假 100 分：候选分值折叠且标注未经校准，不进主分数', () => {
  const vm = buildViewModel(SAMPLE)
  // 候选分值来自 candidate_score，进折叠区
  assert.equal(vm.candidatePct, 82)
  assert.match(vm.candidateNote, /未经校准/)
  // score.available=false → 不显示数值评分，但保留关键帧与点评
  assert.equal(vm.qualityAvailable, false)
  assert.match(vm.noScoreNote, /暂无可靠数值评分/)
  assert.equal(vm.keyframes.length, 1)
  assert.ok(vm.summaryText)
  // calibrated_confidence=null → 不报“判断把握度”
  assert.equal(vm.showConfidence, false)
  // 源码里不得再把 top_probability 乘 100 当主圆环分数
  assert.doesNotMatch(js, /top_probability\s*\*\s*100/)
  assert.doesNotMatch(js, /topPct/)
})

test('六类有评价器时显示动作质量分与次数，但不冒充识别准确率', () => {
  const vm = buildViewModel({
    recognition: { state: 'recognized', label_id: 'squat', label_zh: '深蹲', candidate_score: 0.9, calibrated_confidence: null, review_status: 'used' },
    score: { available: true, overall: 86, reps: 5, completeness: 88, stability: 80, rhythm_control: 84 },
    keyframes: []
  })
  assert.equal(vm.qualityAvailable, true)
  assert.equal(vm.qualityOverall, 86)
  assert.equal(vm.qualityReps, 5)
  assert.equal(vm.qualityDims.length, 3)
})

test('关键帧拆解文案与时间格式化（最多 4 张）', () => {
  assert.match(wxml, /关键帧拆解/)
  const many = Array.from({ length: 6 }, (_, i) => ({ id: 'f' + i, t_ms: i * 1000, finding: 'x', advice: 'y' }))
  const vm = buildViewModel({ recognition: { state: 'recognized' }, score: { available: false }, keyframes: many })
  assert.equal(vm.keyframes.length, 4)
  assert.equal(vm.keyframes[0].tLabel, '0s')
  assert.equal(vm.keyframes[1].tLabel, '1s')
})

test('降级与拒识：DeepSeek 不在线显示降级态；证据不足显示暂不能确定', () => {
  const degraded = buildViewModel({
    recognition: { state: 'recognized', label_id: 'squat', review_status: 'unavailable' },
    score: { available: false },
    summary: { text: '本地规则点评', degraded: true }
  })
  assert.match(degraded.reviewBadge, /暂不可用/)
  assert.equal(degraded.summaryDegraded, true)
  assert.match(degraded.summaryDegradedNote, /本地规则点评/)

  const abstained = buildViewModel({
    recognition: { state: 'abstained', reason: 'usable_frames<3' },
    score: { available: false },
    keyframes: []
  })
  assert.equal(abstained.abstained, true)
  assert.match(abstained.stateBadge, /证据不足/)
  assert.match(wxml, /暂不能确定/)
})

test('交互接线：confirm-label / feedback / trace 接口已接入', () => {
  assert.match(js, /\/confirm-label/)
  assert.match(js, /\/feedback/)
  assert.match(js, /\/trace/)
  assert.match(wxml, /bindtap="confirmLabel"/)
  assert.match(wxml, /bindtap="correctLabel"/)
  assert.match(wxml, /bindtap="sendFeedback"/)
})

test('隐私与本地分析口径：关闭云端协作时页面明确标注', () => {
  assert.match(wxml, /纯色画布骨架图/)
  assert.match(wxml, /仅本地分析/)
  assert.match(wxml, /switch[\s\S]*bindchange="toggleConsent"/)
  // 不承诺医疗级/高准确率
  assert.doesNotMatch(wxml, /医疗级/)
  assert.doesNotMatch(wxml, /高准确率/)
})
