const PLAN_HANDOFF_VERSION = 'healthmate.plan-handoff.v1'

const NAVIGATION_TARGETS = {
  plan_preview: { route: '/pages/plan/index', label: '查看计划草案' },
  capability_setup: { route: '/pages/settings/capabilities/index?focus=plan_outcome', label: '开启计划能力' },
  records: { route: '/pages/records/index', label: '打开记录' },
  workout: { route: '/pages/workout/index', label: '打开训练' },
  health_state: { route: '/pages/state/index', label: '查看状态' }
}

function safeRunId(value) {
  const runId = Number(value)
  return Number.isInteger(runId) && runId > 0 ? runId : null
}

function structuredPlanFallback(result) {
  const runId = safeRunId(result && result.run_id)
  const plan = result && result.plan
  const items = plan && Array.isArray(plan.items) ? plan.items : []
  if (!runId || result.intent !== 'plan' || String(result.safety_level || 'normal') !== 'normal') return null
  const actorId = result.agent && result.agent.id === 'xiaokang' ? 'xiaokang' : 'xiaojian'
  if (!items.length) {
    return {
      action: { target: 'capability_setup', ...NAVIGATION_TARGETS.capability_setup, runId, actorId },
      autoNavigate: false
    }
  }
  return {
    action: { target: 'plan_preview', ...NAVIGATION_TARGETS.plan_preview, runId, actorId },
    autoNavigate: true
  }
}

function normalizeNavigation(result) {
  const fallback = structuredPlanFallback(result)
  const directive = result && result.presentation
  if (!directive || directive.version !== 'healthmate.presentation.v1') return fallback || { action: null, autoNavigate: false }
  const navigation = directive.navigation && typeof directive.navigation === 'object'
    ? directive.navigation
    : directive.handoff && typeof directive.handoff === 'object'
      ? directive.handoff
      : {}
  const target = String(navigation.target || directive.target || '')
  const destination = NAVIGATION_TARGETS[target]
  if (!destination) return fallback || { action: null, autoNavigate: false }
  const params = navigation.params && typeof navigation.params === 'object' ? navigation.params : {}
  const runId = safeRunId(params.run_id)
  const responseRunId = safeRunId(result && result.run_id)
  if (target === 'plan_preview') {
    const write = directive.write || {}
    const validDraft = runId && runId === responseRunId
      && write.status === 'not_applied'
      && write.automatic === false
      && write.confirmation_required === true
    if (!validDraft) return fallback || { action: null, autoNavigate: false }
  }
  return {
    action: {
      target,
      label: destination.label,
      route: destination.route,
      runId,
      actorId: directive.actor === 'xiaokang' ? 'xiaokang' : 'xiaojian'
    },
    autoNavigate: navigation.mode === 'after_animation'
  }
}

function persistPlanHandoff(result, action) {
  if (!action || action.target !== 'plan_preview') return
  const runId = safeRunId(action.runId)
  const plan = result && result.plan
  if (!runId || !plan || !Array.isArray(plan.items) || !plan.items.length) return
  try {
    wx.setStorageSync(`healthmate_plan_preview_handoff:${runId}`, {
      version: PLAN_HANDOFF_VERSION,
      savedAt: Date.now(),
      reply: String(result.reply || ''),
      presentation: { actor: action.actorId === 'xiaokang' ? 'xiaokang' : 'xiaojian' },
      plan_preview: {
        run_id: runId,
        status: 'draft',
        read_only: true,
        title: String(plan.title || '本周健康计划'),
        items: plan.items.slice(0, 10),
        write: { status: 'not_applied', automatic: false, confirmation_required: true }
      }
    })
  } catch (error) {}
}

function safeActionUrl(action) {
  if (!action || !NAVIGATION_TARGETS[action.target]) return ''
  const destination = NAVIGATION_TARGETS[action.target]
  if (action.target !== 'plan_preview') return destination.route
  const runId = safeRunId(action.runId)
  if (!runId) return ''
  const actorId = action.actorId === 'xiaokang' ? 'xiaokang' : 'xiaojian'
  return `${destination.route}?mode=preview&run_id=${runId}&agent_id=${actorId}`
}

module.exports = { NAVIGATION_TARGETS, normalizeNavigation, persistPlanHandoff, safeActionUrl }
