import { describe, expect, it } from 'vitest'
import { motionCorrectionIdempotencyKey } from '../../src/services/motionIdempotency.ts'

describe('motion correction idempotency keys', () => {
  it('reuses a key for the same correction after an ambiguous retry', async () => {
    const correction = { canonical_id: 'pushup', novel_label_zh: '' }
    const first = await motionCorrectionIdempotencyKey('reanalyze', 31, 77, correction, 'off')
    const retry = await motionCorrectionIdempotencyKey('reanalyze', 31, 77, correction, 'off')
    expect(retry).toBe(first)
    expect(first.length).toBeLessThanOrEqual(120)
  })

  it('uses separate keys when the action, parent, consent, or correction changes', async () => {
    const correction = { canonical_id: '', novel_label_zh: '保加利亚分腿蹲' }
    const baseline = await motionCorrectionIdempotencyKey('confirm', 31, 77, correction, 'off')
    const changed = await Promise.all([
      motionCorrectionIdempotencyKey('reanalyze', 31, 77, correction, 'off'),
      motionCorrectionIdempotencyKey('confirm', 31, 78, correction, 'off'),
      motionCorrectionIdempotencyKey('confirm', 31, 77, correction, 'redacted_frames'),
      motionCorrectionIdempotencyKey('confirm', 31, 77, { canonical_id: 'squat' }, 'off'),
    ])
    expect(new Set([baseline, ...changed]).size).toBe(5)
  })
})
