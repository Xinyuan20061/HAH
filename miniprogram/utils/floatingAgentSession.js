const MAX_MESSAGES = 18

let state = {
  messages: [],
  sessionId: null
}

function getSession() {
  return {
    messages: state.messages.slice(),
    sessionId: state.sessionId
  }
}

function saveSession(patch = {}) {
  if (Array.isArray(patch.messages)) {
    state.messages = patch.messages.filter(message => message && !message.ephemeral).slice(-MAX_MESSAGES)
  }
  if (Object.prototype.hasOwnProperty.call(patch, 'sessionId')) state.sessionId = patch.sessionId || null
  return getSession()
}

module.exports = { getSession, saveSession }
