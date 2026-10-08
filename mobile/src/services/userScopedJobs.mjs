const JOB_KEYS = ['hm-food-job', 'hm-motion-job']

function getStorage(storage) {
  if (storage) return storage
  try {
    return globalThis.sessionStorage ?? null
  } catch {
    return null
  }
}

function validUserId(userId) {
  return Number.isSafeInteger(userId) && userId > 0
}

export function readUserJob(key, userId, storage) {
  const target = getStorage(storage)
  if (!target) return null
  if (!validUserId(userId)) {
    target.removeItem(key)
    return null
  }
  const raw = target.getItem(key)
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw)
    if (!parsed || parsed.owner_user_id !== userId || typeof parsed !== 'object') {
      target.removeItem(key)
      return null
    }
    return parsed
  } catch {
    target.removeItem(key)
    return null
  }
}

export function writeUserJob(key, userId, value, storage) {
  const target = getStorage(storage)
  if (!target) return
  if (!validUserId(userId)) {
    target.removeItem(key)
    return
  }
  target.setItem(key, JSON.stringify({ ...value, owner_user_id: userId }))
}

export function clearUserJob(key, storage) {
  getStorage(storage)?.removeItem(key)
}

export function clearAllUserJobs(storage) {
  const target = getStorage(storage)
  if (!target) return
  for (const key of JOB_KEYS) target.removeItem(key)
}
