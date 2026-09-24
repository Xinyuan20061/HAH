const api = require('./request')
async function ensureLogin() {
  return api.ensureToken()
}
module.exports = { ensureLogin }
