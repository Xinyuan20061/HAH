'use strict'
/**
 * Spec §6 evaluation-page provenance: every externally shown benchmark score
 * must carry dataset, date, model/retriever version and evidence level, and a
 * missing provenance must render as 未标注 instead of being silently promoted
 * to a shipped ability.
 */
const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const root = path.join(__dirname, '..')
const read = file => fs.readFileSync(path.join(root, file), 'utf8')
const js = () => read('pages/evaluation/index.js')
const view = () => read('pages/evaluation/index.wxml')

test('evaluation page carries dataset, date, version and evidence level', () => {
  const script = js()
  assert.match(script, /EVIDENCE_LEVEL_LABEL/)
  assert.match(script, /synthetic_replay:[\s\S]*'合成回放'/, '合成回放证据等级必须有中文标签')
  assert.match(script, /measuredDate:[\s\S]*slice\(0, ?10\)/, '成绩必须带测量日期')
  const view0 = view()
  assert.match(view0, /数据集 \{\{item\.dataset \|\| '未标注'\}\}/)
  assert.match(view0, /\{\{item\.evidenceLabel\}\}/)
  assert.match(view0, /版本 \{\{item\.retriever_version \|\| '未标注'\}\}/)
  assert.match(view0, /\{\{item\.measuredDate\}\}/)
})

test('missing provenance renders as 未标注, never as a shipped ability', () => {
  const script = js()
  assert.match(script, /'':\s*'未标注证据等级'/)
  assert.match(script, /evidenceLabel:\s*EVIDENCE_LEVEL_LABEL\[b\.evidence_level\]\s*\|\|\s*b\.evidence_level\s*\|\|\s*'未标注证据等级'/)
  const view0 = view()
  assert.doesNotMatch(view0, /离线 Benchmark[^<]*上线能力/, '开发中指标不得表述为上线能力')
  assert.match(view0, /还没有标注集评测/, '空态必须如实呈现')
})
