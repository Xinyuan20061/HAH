'use strict'

const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const root = path.join(__dirname, '..')
const read = file => fs.readFileSync(path.join(root, file), 'utf8')

test('health capability center is discoverable, reversible and user-facing', () => {
  const app = JSON.parse(read('app.json'))
  const view = read('pages/settings/capabilities/index.wxml')
  const script = read('pages/settings/capabilities/index.js')

  assert.ok(app.pages.includes('pages/settings/capabilities/index'))
  assert.match(read('pages/settings/index.wxml'), /bindtap="capabilities"/)
  assert.match(read('pages/settings/index.js'), /pages\/settings\/capabilities\/index/)
  assert.match(view, /^<include src="\/components\/page-transition\/first-paint\.wxml"\/>\r?\n<page-transition\/>/)
  assert.match(view, /class="[^\"]*ui-page-content/)
  assert.match(script, /api\.get\('\/harness\/plugins'/)
  assert.match(script, /api\.post\(path, nextEnabled \? \{ data_scope: plugin\.data_needed \|\| \[\] \} : \{\}\)/)
  assert.match(view, /可以随时暂停/)
  assert.match(view, /历史记录不会被删除/)
  assert.match(view, /一般健康参考，不替代医生诊断/)
  assert.doesNotMatch(view, /candidate_score|trace_id|模型名|tool_names/)
})

test('capability page exposes a confirmation boundary before pausing', () => {
  const script = read('pages/settings/capabilities/index.js')
  assert.match(script, /暂停这项能力？/)
  assert.match(script, /已有记录和周期仍可查看、停止或导出/)
  assert.match(script, /nextEnabled \? '已开启' : '已暂停'/)
  assert.match(script, /busy: plugin\.plugin_id/)
})
