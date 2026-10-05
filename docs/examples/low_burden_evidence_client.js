/* Reference factory to port into miniprogram/utils/policyAcquisition.js.
 * Inject the existing request module. Keep a pending operation's key and body
 * unchanged on network retries; createOperation must not be called on retry.
 */

function createClient(api) {
  function id(value) { return encodeURIComponent(String(value)) }
  function createOperation(path, body, scope) {
    return {
      path,
      body: JSON.parse(JSON.stringify(body)),
      key: api.idempotencyKey(scope)
    }
  }
  function submit(operation) {
    return api.post(operation.path, operation.body, {
      'Idempotency-Key': operation.key
    })
  }
  return {
    getSession(sessionId) {
      return api.get(`/policy/acquisition/sessions/${id(sessionId)}`, { allowCache: false })
    },
    getCertificate(certificateId) {
      return api.get(`/policy/acquisition/certificates/${id(certificateId)}`, { allowCache: false })
    },
    createStart(episodeId, body) {
      return createOperation(`/policy/episodes/${id(episodeId)}/acquisition/sessions`, body, 'acq-start')
    },
    createNext(sessionId, body) {
      return createOperation(`/policy/acquisition/sessions/${id(sessionId)}/next`, body, 'acq-next')
    },
    createAnswer(sessionId, questionId, body) {
      return createOperation(`/policy/acquisition/sessions/${id(sessionId)}/questions/${id(questionId)}/answer`, body, 'acq-answer')
    },
    createRepair(sessionId, body) {
      return createOperation(`/policy/acquisition/sessions/${id(sessionId)}/repair`, body, 'acq-repair')
    },
    submit
  }
}

module.exports = { createClient }
