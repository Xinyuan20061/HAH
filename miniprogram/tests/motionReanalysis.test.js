'use strict'
/**
 * WP0 red test — MOTION-03/04/HARNESS-01: the motion re-analysis polling chain
 * and the Agent action confirm card (spec §7.4, §8.5).
 *
 * Confirmed defects:
 *   * after POST /reanalyze the page kept polling the *parent* analysis id, so the
 *     timeline either stalled or mixed parent and child evidence;
 *   * the confirmed exercise label never reached the child request;
 *   * the chat page had no `actions[]` confirm/reject/expire flow.
 */
const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const root = path.join(__dirname, '..')
const read = file => fs.readFileSync(path.join(root, file), 'utf8')

test('reanalysis switches polling to the child analysis id and clears old state', () => {
  const js = read('pages/media/index.js')

  assert.match(js, /reanalyze/)
  assert.match(js, /child\.analysis_id|childAnalysisId|analysis_id/)
  assert.match(js, /parentRunId|parent_run_id|parentAnalysisId/)
  // Old view model / timeline must not survive into the child run.
  assert.match(js, /vm:\s*null/)
  assert.match(js, /timelineFrames:\s*\[\]/)
  assert.match(js, /activeFrame:\s*null/)
  // And the page must actually poll the child.
  assert.match(js, /continueAnalysis\(\s*child/)
})

test('reanalysis sends the confirmed label as an exercise hint', () => {
  const js = read('pages/media/index.js')
  assert.match(js, /exercise_hint/)
  assert.match(js, /reason/)
  assert.match(js, /user_label_correction/)
})

test('reanalysis handles a rejected hint instead of silently retrying', () => {
  const js = read('pages/media/index.js')
  assert.match(js, /UNKNOWN_CATALOG_ID|422|提示|无法/)
})

test('media page offers the previous result without mixing evidence', () => {
  const js = read('pages/media/index.js')
  const wxml = read('pages/media/index.wxml')
  assert.match(js + wxml, /上一次|查看上一/)
})

test('motion preview uploader reads the frozen uploads field with a urls fallback', () => {
  const worker = fs.readFileSync(
    path.join(root, '..', 'ai-worker', 'healthmate_worker', 'processors', 'motion_unified.py'),
    'utf8'
  )
  assert.match(worker, /resp\.get\(\s*["']uploads["']\s*\)/)
  assert.match(worker, /["']urls["']/)
})

test('chat page renders confirm cards from the actions array only', () => {
  const js = read('pages/chat/index.js')
  const wxml = read('pages/chat/index.wxml')

  assert.match(js, /actions/)
  assert.match(js, /\/agent\/actions\//)
  assert.match(js, /confirm/)
  assert.match(js, /reject/)
  // Terminal states must be rendered, not spun forever.
  assert.match(js + wxml, /expired|已过期/)
  assert.match(wxml, /proposal/)
})

test('chat page never guesses an executable action out of the reply text', () => {
  const js = read('pages/chat/index.js')
  assert.doesNotMatch(js, /reply[\s\S]{0,40}match\([^)]*计划[\s\S]{0,40}apply/i)
  assert.match(js, /actions\s*\|\|\s*\[\]/)
})

test('agent stream consumer understands stage events, not fake deltas', () => {
  const js = read('pages/chat/index.js')
  assert.match(js, /stage/)
  // The UI must label the animation honestly.
  const request = read('utils/request.js')
  assert.match(request, /onStage|type === 'stage'/)
})
