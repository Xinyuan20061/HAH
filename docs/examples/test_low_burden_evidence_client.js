const assert = require('node:assert/strict')
const { createClient } = require('./low_burden_evidence_client')

async function main() {
  const calls = []
  let counter = 0
  const api = {
    idempotencyKey(scope) { return `${scope}-${++counter}` },
    async post(path, body, headers) { calls.push({ path, body, headers }); return { ok: true } },
    async get(path, options) { calls.push({ path, options }); return { ok: true } }
  }
  const client = createClient(api)
  const original = { expected_session_version: 2, expected_episode_version: 6, response: 'declined' }
  const operation = client.createAnswer('session', 'question', original)
  original.response = 'unknown'
  await client.submit(operation)
  await client.submit(operation)
  assert.equal(counter, 1)
  assert.equal(calls[0].headers['Idempotency-Key'], calls[1].headers['Idempotency-Key'])
  assert.deepEqual(calls[0].body, calls[1].body)
  assert.equal(calls[0].body.response, 'declined')
  assert.equal(calls[0].path, '/policy/acquisition/sessions/session/questions/question/answer')
  const next = client.createNext('session', { expected_session_version: 3, expected_episode_version: 6 })
  assert.notEqual(next.key, operation.key)
  await client.getSession('session')
  assert.equal(calls[2].options.allowCache, false)
  await client.getCertificate('cert')
  assert.equal(calls[3].options.allowCache, false)
  process.stdout.write('Client reference checks passed: stable retry key/body, new operation key, fresh reads.\n')
}

main().catch(error => { process.stderr.write(`${error.stack}\n`); process.exitCode = 1 })
