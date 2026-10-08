'use strict'
// 统一动作分析 V2 结果页（工作包 D）行为契约守卫：
//   · 三态展示 identified / likely / unknown，不出现调试字段与英文引擎 ID
//   · 时间轴全量映射、按时间升序（0.9/2.8/4.8/9.3），不再 .slice(0,4) 锁界面帧数
//   · 主图 aspectFit + 横向时间条 + 三段讲解；点击只本地选中，回看走 video seek
//   · confirm-label 必带真实标签；feedback 用字符串 kind 并关联当前选中帧
// 不依赖 wx：纯函数 buildViewModel / normalizeTimeline 直接跑；事件处理断言接线形状。
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const ROOT = path.join(__dirname, '..')
const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf8')

const js = read('pages/media/index.js')
const wxml = read('pages/media/index.wxml')
const { buildViewModel, normalizeTimeline, mergeEvidencePreviews, buildPreviewMap, PIPELINE_VERSION } = require('../utils/motionUnifiedView')

// 契约 §3 / §8.2 的 V2 结果样例（数字为示例，非对真实视频结论）。
const SAMPLE_V2 = {
  analysis_id: 518,
  status: 'completed',
  pipeline_version: 'motion-unified-v2',
  result_version: 1,
  recognition: {
    state: 'likely',
    canonical_id: 'bicep_curl',
    display_name: '看起来是哑铃弯举',
    reason: '肘关节屈伸清楚，最高点附近画面略有遮挡。',
    source: 'vision'
  },
  capabilities: { recognition: 'available', timeline: 'available', coaching: 'available', repetitions: 'unavailable', quality_score: 'unavailable' },
  summary: { text: '手臂屈伸和哑铃轨迹可以看清。下面按抬起和放下拆解。', primary_next_step: '放下时慢数两拍，并留意上臂是否跟着前后摆动。', source: 'visual_coach' },
  metrics: [],
  timeline: {
    duration_ms: 16000,
    frames: [
      { id: 'f_001', timestamp_ms: 9300, preview_url: 'https://p/9.jpg', phase: '下放阶段', observation: '前臂向下回落', explanation: '留意下放是否仍有控制', next_step: '下放时慢数两拍', advice_kind: 'general_tip' },
      { id: 'f_002', timestamp_ms: 900, preview_url: 'https://p/1.jpg', phase: '准备', observation: '双臂接近身体两侧', explanation: '固定上臂位置', next_step: '上臂贴紧身体两侧', advice_kind: 'capture_tip' },
      { id: 'f_003', timestamp_ms: 4800, preview_url: 'https://p/4.jpg', phase: '抬起阶段', observation: '前臂向上转动', explanation: '留意上臂是否前移', next_step: '上臂停在身体两侧', advice_kind: 'observed_correction' },
      { id: 'f_004', timestamp_ms: 2800, preview_url: 'https://p/2.jpg', phase: '转向阶段', observation: '哑铃接近胸前', explanation: '集中在肘部屈伸', next_step: '减少抬肩带动', advice_kind: 'general_tip' },
      { id: 'f_bad', timestamp_ms: -1, preview_url: '', phase: '非法时间戳应被过滤', observation: 'x', explanation: 'y', next_step: 'z', advice_kind: 'general_tip' }
    ]
  },
  notices: [{ kind: 'metric_unavailable', text: '这类动作本次提供画面讲解，暂不显示数值评分。' }]
}

test('PIPELINE_VERSION 锁定为 motion-unified-v2', () => {
  assert.equal(PIPELINE_VERSION, 'motion-unified-v2')
  assert.match(js, /PIPELINE_VERSION/)
})

test('单入口：wxml 只绑 startAnalysis，无旧双按钮', () => {
  assert.match(wxml, /bindtap="startAnalysis"/)
  assert.doesNotMatch(wxml, /开始查看/)
  assert.doesNotMatch(wxml, /400 类动作识别/)
  assert.doesNotMatch(js, /analyzeKinetics/)
})

test('三态 identified：显示名称，不打待确认/暂未确认', () => {
  const vm = buildViewModel({ recognition: { state: 'identified', display_name: '深蹲', source: 'pose' } })
  assert.equal(vm.state, 'identified')
  assert.equal(vm.detectionName, '深蹲')
  assert.equal(vm.detectionBadge, '')
})

test('三态 likely：显示"看起来是 X" + 一个具体分歧 + 保留帧讲解', () => {
  const vm = buildViewModel(SAMPLE_V2)
  assert.equal(vm.state, 'likely')
  assert.match(vm.detectionName, /^看起来是/)
  assert.equal(vm.detectionBadge, '待确认')
  assert.match(vm.detectionNote, /最高点附近画面略有遮挡/)
  // 保留帧讲解
  assert.ok(vm.timelineFrames.length >= 3)
})

test('三态 unknown：只在无法合理判断时出现，不与"已识别"同屏', () => {
  const vm = buildViewModel({ recognition: { state: 'unknown', reason: '' } })
  assert.equal(vm.state, 'unknown')
  assert.equal(vm.detectionBadge, '暂未确认')
  // 不编造一个"已识别"名称
  assert.doesNotMatch(vm.detectionName, /^深蹲$|^哑铃弯举$/)
})

test('来源中文化：英文引擎 ID 不直出，转为视频画面/动作轨迹/AI 分析', () => {
  const vm = buildViewModel({ recognition: { state: 'identified', display_name: '深蹲', source: 'local_worker' }, summary: { source: 'deepseek_vision' } })
  assert.match(vm.sourceText, /动作轨迹/)
  assert.doesNotMatch(vm.sourceText, /local_worker|deepseek_vision/)
})

test('无调试字段：候选分/置信度/帧号/引擎名/诊断码不进用户视图', () => {
  const vm = buildViewModel({
    recognition: { state: 'likely', display_name: '看起来是哑铃弯举', source: 'deepseek_vision', reason: 'NOT_RECOGNIZED', candidate_score: 0.91, calibrated_confidence: 0.5 },
    trace_id: 'trace-abc123'
  })
  assert.equal(vm.candidatePct, undefined)
  assert.equal(vm.showConfidence, undefined)
  const dumped = JSON.stringify(vm)
  for (const banned of ['candidate_score', 'NOT_RECOGNIZED', 'deepseek_vision', 'local_worker', 'frame:', 'trace-']) {
    assert.ok(!dumped.includes(banned), `用户视图出现调试字段：${banned}`)
  }
  // reason 是诊断码时被过滤，不上屏
  assert.equal(vm.detectionNote, '')
})

test('时间轴按时间升序（0.9/2.8/4.8/9.3），过滤非法时间戳，全量映射不锁 4 帧', () => {
  const vm = buildViewModel(SAMPLE_V2)
  const stamps = vm.timelineFrames.map(f => f.timestamp_ms)
  assert.deepEqual(stamps, [900, 2800, 4800, 9300])
  assert.deepEqual(vm.timelineFrames.map(f => f.timeLabel), ['0.9 秒', '2.8 秒', '4.8 秒', '9.3 秒'])
  // 全量：8 帧也不截断
  const many = Array.from({ length: 8 }, (_, i) => ({ id: 'f' + i, timestamp_ms: i * 1000, observation: 'o', explanation: 'e', next_step: 'n' }))
  const vm2 = buildViewModel({ recognition: { state: 'identified', display_name: 'x' }, timeline: { frames: many } })
  assert.equal(vm2.timelineFrames.length, 8)
})

test('activeFrame 默认取排序后首帧；selectFrame 只本地切换', () => {
  const vm = buildViewModel(SAMPLE_V2)
  assert.equal(vm.activeFrame.timestamp_ms, 900)
  assert.equal(vm.activeFrame.id, 'f_002')
  // WXML 用 data-id 绑定 selectFrame，点击不请求模型
  assert.match(wxml, /data-id="\{\{item\.id\}\}"/)
  assert.match(wxml, /bindtap="selectFrame"/)
})

test('seek 事件接线：video id=motionVideo，回看调 createVideoContext+seek', () => {
  assert.match(wxml, /id="motionVideo"/)
  assert.match(wxml, /bindtap="replayFrame"/)
  assert.match(js, /wx\.createVideoContext\('motionVideo'/)
  assert.match(js, /\.seek\(Number\(frame\.timestamp_ms\) \/ 1000\)/)
  // seek 目标 = timestamp_ms/1000 秒（§8.4）
  assert.equal(Number(4800) / 1000, 4.8)
})

test('三段讲解：看到什么 / 为什么要留意 / 下一遍怎么做', () => {
  const vm = buildViewModel(SAMPLE_V2)
  const f = vm.timelineFrames[2] // 4800ms
  assert.ok(f.observation && f.explanation && f.nextStep)
  assert.match(wxml, /看到什么/)
  assert.match(wxml, /为什么要留意/)
  assert.match(wxml, /下一遍怎么做/)
})

test('指标按实际能力显示；缺失用具体原因，不硬编"已识别类别"', () => {
  const vm = buildViewModel({
    recognition: { state: 'identified', display_name: '深蹲' },
    metrics: [{ id: 'reps', value: 5, unit: '次' }],
    notices: [{ kind: 'metric_unavailable', text: '这段只拍到半次，暂不统计次数。' }]
  })
  assert.equal(vm.metricRows.length, 1)
  assert.equal(vm.metricRows[0].label, '次数')
  assert.match(vm.metricNote, /只拍到半次/)
  // 旧的固定文案不再出现
  assert.doesNotMatch(JSON.stringify(vm), /已识别类别，暂无可靠数值评分/)
})

test('confirm-label 必带真实标签，不再提交 user_confirmed 占位', () => {
  assert.doesNotMatch(js, /user_confirmed/)
  assert.match(js, /canonical_id|novel_label_zh/)
  assert.match(js, /请选择或输入正确动作/)
})

test('feedback 用字符串 kind，关联当前选中帧，失败可重试', () => {
  // 不再布尔 label_correction=true
  assert.doesNotMatch(js, /label_correction\s*=\s*true/)
  assert.match(js, /kind === 'wrong_frame'/)
  // frame_id 来自当前选中帧 activeFrame，不再恒取 keyframes[0]
  assert.match(js, /this\.data\.activeFrame && this\.data\.activeFrame\.id/)
})

test('改模式/轮询超时：清 analysisId 起新任务；超时保留进行中不渲染假完成', () => {
  // toggleConsent 清空 analysisId
  assert.match(js, /toggleConsent[\s\S]{0,260}analysisId: null/)
  // 超时保留进行中
  assert.match(js, /仍在分析，可稍后回来/)
  // V2 创建请求形状
  assert.match(js, /cloud_review_mode/)
  assert.match(js, /consent_version:\s*CONSENT_VERSION/)
  assert.match(js, /response_schema:\s*RESPONSE_SCHEMA/)
  // 新端点接线
  assert.match(js, /\/reanalyze/)
  assert.match(js, /\/confirm-label/)
  assert.match(js, /\/feedback/)
  assert.match(js, /\/fitness\/motion-capabilities/)
})

test('主图 aspectFit + 横向 scroll-x 时间条，选中态有边框', () => {
  assert.match(wxml, /mode="aspectFit" class="frame-hero-img"/)
  assert.match(wxml, /scroll-view scroll-x/)
  assert.match(wxml, /\{\{activeFrame\.id===item\.id\?'on':''\}\}/)
})

test('结果帧自带 preview_url 时直接映射为 previewUrl', () => {
  const vm = buildViewModel({
    recognition: { state: 'identified', display_name: '深蹲' },
    timeline: { frames: [
      { id: 'a', timestamp_ms: 1000, preview_url: 'https://signed/a.jpg', observation: 'o', explanation: 'e', next_step: 'n' },
      { id: 'b', timestamp_ms: 3000, observation: 'o2', explanation: 'e2', next_step: 'n2' }
    ] }
  })
  assert.equal(vm.timelineFrames[0].previewUrl, 'https://signed/a.jpg')
  // 未带 preview_url 的帧先留空，等 /evidence 兜底
  assert.equal(vm.timelineFrames[1].previewUrl, '')
})

test('/evidence 是已核验图片的权威来源，会替换旧 URL', () => {
  const vm = buildViewModel({
    recognition: { state: 'identified', display_name: '深蹲' },
    timeline: { frames: [
      { id: 'a', timestamp_ms: 1000, preview_url: 'https://signed/a.jpg', observation: 'o', explanation: 'e', next_step: 'n' },
      { id: 'b', timestamp_ms: 3000, observation: 'o2', explanation: 'e2', next_step: 'n2' }
    ] }
  })
  // mock /evidence 响应（F 包签名 GET，query 带 user_id/exp/sig）
  const evidence = { frames: [
    { frame_id: 'b', preview_url: 'https://signed/b.jpg?user_id=1&exp=999&sig=abc' },
    { frame_id: 'a', preview_url: 'https://signed/should-not-overwrite.jpg' }
  ] }
  const merged = mergeEvidencePreviews(vm.timelineFrames, evidence)
  // 空帧被补齐
  assert.equal(merged[1].previewUrl, 'https://signed/b.jpg?user_id=1&exp=999&sig=abc')
  assert.equal(merged[0].previewUrl, 'https://signed/should-not-overwrite.jpg')
  const unavailable = mergeEvidencePreviews(merged, { frames: [
    { id: 'a', preview: { state: 'unavailable', url: null } }
  ] })
  assert.equal(unavailable[0].previewUrl, '')
  assert.equal(unavailable[0].previewState, 'unavailable')
})

test('buildPreviewMap 兼容 id/frame_id 键与顶层数组', () => {
  const m1 = buildPreviewMap({ frames: [{ frame_id: 'x', preview_url: 'u1' }] })
  assert.equal(m1.x, 'u1')
  const m2 = buildPreviewMap([{ id: 'y', preview_url: 'u2' }])
  assert.equal(m2.y, 'u2')
})

test('/evidence 拉取路径已接线：只读 GET，失败不阻塞，点击仍本地选中', () => {
  assert.match(js, /\/media\/motion-analyses\/\$\{id\}\/evidence/)
  // 云托管图片必须通过私有接口取回，不能直接把内部地址交给 image。
  assert.match(js, /api\.downloadMotionPreview/)
  // 图片加载失败占位
  assert.match(wxml, /binderror="onFrameImgError"/)
  assert.match(js, /onFrameImgError/)
})
