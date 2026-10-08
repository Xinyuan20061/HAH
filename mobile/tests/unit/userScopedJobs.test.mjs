import { describe, expect, it } from 'vitest'
import { clearAllUserJobs, clearUserJob, readUserJob, writeUserJob } from '../../src/services/userScopedJobs.mjs'

function makeStorage(initial = {}) {
  const values = new Map(Object.entries(initial))
  return {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: key => values.delete(key),
    has: key => values.has(key),
  }
}

describe('user-scoped job pointers', () => {
  it('restores a pending job only for the account that created it', () => {
    const storage = makeStorage()
    writeUserJob('hm-motion-job', 17, { mediaId: 52, analysisId: 91 }, storage)

    expect(readUserJob('hm-motion-job', 17, storage)).toMatchObject({
      owner_user_id: 17,
      mediaId: 52,
      analysisId: 91,
    })
  })

  it('deletes another account’s pointer instead of exposing or resuming it', () => {
    const storage = makeStorage()
    writeUserJob('hm-food-job', 17, { mediaId: 52, jobId: 91 }, storage)

    expect(readUserJob('hm-food-job', 29, storage)).toBeNull()
    expect(storage.has('hm-food-job')).toBe(false)
  })

  it('drops malformed and legacy unscoped pointers', () => {
    const storage = makeStorage({
      'hm-food-job': '{broken',
      'hm-motion-job': JSON.stringify({ mediaId: 52, analysisId: 91 }),
    })

    expect(readUserJob('hm-food-job', 17, storage)).toBeNull()
    expect(readUserJob('hm-motion-job', 17, storage)).toBeNull()
    expect(storage.has('hm-food-job')).toBe(false)
    expect(storage.has('hm-motion-job')).toBe(false)
  })

  it('clears both pending job pointers on logout and one pointer on completion', () => {
    const storage = makeStorage({
      'hm-food-job': '{}',
      'hm-motion-job': '{}',
    })

    clearUserJob('hm-food-job', storage)
    expect(storage.has('hm-food-job')).toBe(false)
    clearAllUserJobs(storage)
    expect(storage.has('hm-motion-job')).toBe(false)
  })
})
