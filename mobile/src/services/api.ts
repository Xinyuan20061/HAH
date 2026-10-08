import { readNdjsonStream, type NdjsonEvent } from './ndjson'

export interface ApiError extends Error {
  code?: string
  status?: number
  requestId?: string
  retryable?: boolean
  details?: Record<string, unknown>
}

type UnauthorizedHandler = (error: ApiError) => void | Promise<void>
let unauthorizedHandler: UnauthorizedHandler | null = null
let unauthorizedRecovery: { token: string; task: Promise<void> } | null = null
let lastExpiredToken = ''

export function registerUnauthorizedHandler(handler: UnauthorizedHandler): () => void {
  unauthorizedHandler = handler
  return () => {
    if (unauthorizedHandler === handler) unauthorizedHandler = null
  }
}

async function recoverExpiredSession(token: string, error: ApiError): Promise<void> {
  if (!token || !unauthorizedHandler) return
  if (unauthorizedRecovery?.token === token) {
    await unauthorizedRecovery.task
    return
  }
  if (lastExpiredToken === token) return

  lastExpiredToken = token
  const handler = unauthorizedHandler
  const task = Promise.resolve().then(() => handler(error)).then(() => undefined).catch(() => undefined)
  unauthorizedRecovery = { token, task }
  try {
    await task
  } finally {
    if (unauthorizedRecovery?.task === task) unauthorizedRecovery = null
  }
}

export interface AgentResult {
  run_id?: number
  reply?: string
  intent?: string
  safety_level?: string
  agent?: { id?: string; name?: string }
  plan?: { title?: string; items?: Array<{ title?: string; description?: string; category?: string; date_offset?: number }> } | null
  presentation?: {
    version?: string
    actor?: string
    cue?: string
    activity?: string
    navigation?: { target?: string; mode?: string; params?: Record<string, unknown> }
    write?: { status?: string; automatic?: boolean; confirmation_required?: boolean }
  }
  actions?: Array<Record<string, unknown>>
  [key: string]: unknown
}

export type AgentEvent = NdjsonEvent
export type AgentResponseMode = 'ndjson' | 'full'

export function agentResponseMode(): AgentResponseMode {
  return import.meta.env.VITE_AGENT_RESPONSE_MODE === 'full' ? 'full' : 'ndjson'
}

function apiRoot(): string {
  const root = import.meta.env.VITE_API_BASE_URL?.trim().replace(/\/+$/, '')
  if (!root || !root.endsWith('/api/v1')) {
    throw new Error('请把 VITE_API_BASE_URL 配置为后端地址并包含 /api/v1')
  }
  return root
}

const CONNECTION_TIMEOUT_MS = 12_000

async function withConnectionTimeout<T>(request: (signal: AbortSignal) => Promise<T>): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), CONNECTION_TIMEOUT_MS)
  try {
    return await request(controller.signal)
  } catch (error) {
    if (controller.signal.aborted) throw new Error('连接超时，请检查网络后重试。')
    throw error
  } finally {
    clearTimeout(timer)
  }
}

function errorFrom(status: number, payload: unknown): ApiError {
  const body = payload && typeof payload === 'object' ? payload as Record<string, unknown> : {}
  const envelope = body.error && typeof body.error === 'object'
    ? body.error as Record<string, unknown>
    : null
  const detail = body.detail
  const message = String(envelope?.message ?? body.message ?? detail ?? `请求失败（HTTP ${status}）`)
  return Object.assign(new Error(message), {
    code: String(envelope?.code ?? body.code ?? (status === 401 ? 'UNAUTHENTICATED' : '')),
    status,
    requestId: String(envelope?.request_id ?? body.request_id ?? ''),
    retryable: Boolean(envelope?.retryable ?? status >= 500),
    details: (envelope?.details && typeof envelope.details === 'object' ? envelope.details : {}) as Record<string, unknown>,
  })
}

async function decodeResponse<T>(response: Response, accessToken?: string): Promise<T> {
  const body = await response.json().catch(() => ({})) as unknown
  if (!response.ok) {
    const error = errorFrom(response.status, body)
    if (response.status === 401 && accessToken) await recoverExpiredSession(accessToken, error)
    throw error
  }
  return body as T
}

export async function publicRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${apiRoot()}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', 'X-Client-Platform': 'android', ...init.headers },
  })
  return decodeResponse<T>(response)
}

export async function apiRequest<T>(path: string, accessToken: string, init: RequestInit = {}): Promise<T> {
  if (!accessToken) throw new Error('请先登录后再继续')
  const response = await fetch(`${apiRoot()}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${accessToken}`,
      'X-Client-Platform': 'android',
      ...init.headers,
    },
  })
  return decodeResponse<T>(response, accessToken)
}

export async function checkBackend(): Promise<{
  status: string
  service?: string
  readiness?: { status: string; checks?: Array<{ name: string; ok: boolean; required: boolean; detail?: string }> }
}> {
  return withConnectionTimeout(async signal => {
    const root = apiRoot().replace(/\/api\/v1$/, '')
    const live = await decodeResponse<{ status: string; service?: string }>(await fetch(`${root}/health/live`, { signal }))
    const readiness = await decodeResponse<{
      status: string
      checks?: Array<{ name: string; ok: boolean; required: boolean; detail?: string }>
    }>(await fetch(`${root}/health/ready`, { signal }))
    if (readiness.status !== 'ok' || readiness.checks?.some(check => check.required && !check.ok)) {
      throw new Error('后端服务在线，但数据库或迁移尚未就绪')
    }
    return { ...live, readiness }
  })
}

export async function loginMobileWechat(code: string): Promise<{ access_token: string; user: { id: number; nickname: string } }> {
  return publicRequest('/auth/mobile/wechat', {
    method: 'POST',
    body: JSON.stringify({ code }),
  })
}

export async function readCurrentUser(accessToken: string): Promise<{ id: number; nickname: string; linked_providers: string[] }> {
  return withConnectionTimeout(signal => apiRequest('/auth/me', accessToken, { signal }))
}

export async function completeAccountLink(accessToken: string, linkCode: string): Promise<{
  access_token: string
  user: { id: number; nickname: string }
}> {
  return apiRequest('/auth/link/complete', accessToken, {
    method: 'POST',
    body: JSON.stringify({ link_code: linkCode }),
  })
}

export async function unlinkMobileAccount(accessToken: string): Promise<{ ok: boolean; unlinked: boolean }> {
  return apiRequest('/auth/link/unlink', accessToken, { method: 'POST', body: JSON.stringify({}) })
}

export async function streamAgentResponse(
  accessToken: string,
  request: { message: string; agent_id: 'xiaojian' | 'xiaokang' | 'steward'; channel: 'text' | 'voice' },
  onEvent: (event: AgentEvent) => void,
  signal: AbortSignal,
): Promise<AgentResult> {
  const response = await fetch(`${apiRoot()}/agent/respond/stream`, {
    method: 'POST',
    signal,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${accessToken}`,
      'X-Client-Platform': 'android',
      Accept: 'application/x-ndjson',
    },
    body: JSON.stringify(request),
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    const error = errorFrom(response.status, payload)
    if (response.status === 401) await recoverExpiredSession(accessToken, error)
    throw error
  }
  if (!response.body) throw new Error('当前 Android WebView 不支持读取流式响应')

  const finalEvent = await readNdjsonStream(response.body, onEvent)
  if (!finalEvent || finalEvent.type !== 'done' || !finalEvent.result || typeof finalEvent.result !== 'object') {
    throw new Error('本次响应未正常结束；请先检查运行状态，避免重复提交')
  }
  return finalEvent.result as AgentResult
}

export async function requestAgentResponse(
  accessToken: string,
  request: { message: string; agent_id: 'xiaojian' | 'xiaokang' | 'steward'; channel: 'text' | 'voice' },
  onEvent: (event: AgentEvent) => void,
  signal: AbortSignal,
): Promise<AgentResult> {
  if (agentResponseMode() === 'full') {
    return apiRequest<AgentResult>('/agent/respond', accessToken, {
      method: 'POST',
      body: JSON.stringify(request),
      signal,
    })
  }
  return streamAgentResponse(accessToken, request, onEvent, signal)
}

export async function readRun(accessToken: string, runId: number) {
  return apiRequest<{
    run_id: number
    status: string
    reply?: string
    presentation?: AgentResult['presentation']
    plan_preview?: {
      run_id: number
      status: 'draft' | 'applied'
      read_only: boolean
      title: string
      items: NonNullable<AgentResult['plan']>['items']
      write: { status: string; automatic: boolean; confirmation_required: boolean }
    } | null
  }>(`/agent/runs/${runId}`, accessToken)
}

export async function cancelAgentRun(accessToken: string, runId: number) {
  return apiRequest<{
    run_id: number
    status: string
    cancelled_stages: number
    note: string
  }>(`/agent/runs/${runId}/cancel`, accessToken, {
    method: 'POST',
    body: JSON.stringify({}),
  })
}

export async function applyRunPlan(accessToken: string, runId: number): Promise<{ already_applied?: boolean }> {
  return apiRequest(`/agent/runs/${runId}/apply-plan`, accessToken, {
    method: 'POST',
    body: JSON.stringify({}),
  })
}

export async function readCurrentPlan(accessToken: string) {
  return apiRequest<{ plan?: { id?: number; title?: string; items?: Array<{ id: number; title: string; description?: string; planned_date?: string; done?: boolean }> } | null }>(
    '/agent/plans/current', accessToken,
  )
}

export async function readCommandCenter(accessToken: string) {
  return apiRequest<{
    date: string
    streak: {
      current: number
      checked_today: boolean
      last7: Array<{ date: string; label: string; done: boolean }>
    }
  }>('/health/command-center', accessToken)
}

export interface HealthGoals {
  water_target: number
  sleep_target: number
  exercise_target: number
  steps_target: number
  protein_target: number
  calorie_target: number
  weekly_checkin_target: number
}

export async function readTodaySummary(accessToken: string) {
  return apiRequest<Record<string, any>>('/health/today', accessToken)
}

export async function readHealthGoals(accessToken: string): Promise<HealthGoals> {
  return apiRequest('/health/goals', accessToken)
}

export async function saveHealthGoals(accessToken: string, goals: HealthGoals) {
  return apiRequest('/health/goals', accessToken, {
    method: 'PUT',
    body: JSON.stringify(goals),
  })
}

export async function readHealthTrends(accessToken: string) {
  return apiRequest<{ days: Array<Record<string, any>> }>('/health/trends/7d', accessToken)
}

export async function evaluateDynamicGoals(accessToken: string) {
  return apiRequest<Record<string, any>>('/health/goals/dynamic/evaluate?window_days=14', accessToken, {
    method: 'POST',
    body: JSON.stringify({}),
  })
}

export async function explainDynamicGoals(accessToken: string) {
  return apiRequest<{ summary: string }>('/health/goals/dynamic/explain', accessToken, {
    method: 'POST',
    body: JSON.stringify({}),
  })
}

export async function applyDynamicGoal(accessToken: string, adjustmentId: number) {
  return apiRequest<Record<string, any>>(`/health/goals/dynamic/${adjustmentId}/apply`, accessToken, {
    method: 'POST',
    body: JSON.stringify({}),
  })
}

export async function readPrivacyPolicy(accessToken: string) {
  return apiRequest<Record<string, any>>('/privacy/policy', accessToken)
}

export async function readUserProfile(accessToken: string) {
  return apiRequest<{ id: number; nickname: string; avatar_url?: string }>('/users/me', accessToken)
}

export interface HealthProfile {
  gender: string
  age: number
  height_cm: number
  weight_kg: number
  goal_type: string
  activity_level: string
  diet_preference: string
  allergies: string
  id?: number
  user_id?: number
}

export async function readHealthProfile(accessToken: string): Promise<HealthProfile | null> {
  return apiRequest('/users/me/health-profile', accessToken)
}

export async function updateUserNickname(accessToken: string, nickname: string) {
  return apiRequest('/users/me', accessToken, {
    method: 'PUT',
    body: JSON.stringify({ nickname }),
  })
}

export async function saveHealthProfile(accessToken: string, profile: HealthProfile) {
  return apiRequest('/users/me/health-profile', accessToken, {
    method: 'PUT',
    body: JSON.stringify(profile),
  })
}

export async function readPrivacyExportPreview(accessToken: string) {
  return apiRequest<Record<string, any>>('/privacy/export/preview', accessToken)
}

export async function readCloudMedia(accessToken: string) {
  return apiRequest<{ file_ids: string[]; count: number }>('/privacy/cloud-media', accessToken)
}

export async function readDeletionStatus(accessToken: string) {
  return apiRequest<Record<string, any>>('/privacy/deletion-status', accessToken)
}

export async function exportPersonalData(accessToken: string): Promise<ArrayBuffer> {
  const response = await fetch(`${apiRoot()}/privacy/export`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${accessToken}`,
      'X-Client-Platform': 'android',
    },
    body: JSON.stringify({ confirmation: 'EXPORT' }),
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    const error = errorFrom(response.status, payload)
    if (response.status === 401) await recoverExpiredSession(accessToken, error)
    throw error
  }
  return response.arrayBuffer()
}

export async function deletePersonalAccount(accessToken: string) {
  return apiRequest<{
    ok: boolean
    deleted: boolean
    verification?: 'server_verified' | 'pending' | 'partial'
    cloud_media_deletion?: 'no_cloud_files' | 'client_reported' | 'server_verified' | 'pending' | 'partial'
    cloud_media_objects_pending?: number
    media_deletion?: { manual_review?: number; pending?: number; failed?: number; server_verified?: number; cloud_media_objects_expected_deleted?: number }
  }>(
    '/privacy/account', accessToken, {
      method: 'DELETE',
      body: JSON.stringify({ confirmation: 'DELETE MY DATA' }),
    },
  )
}

export interface DailyCheckIn {
  water_ml: number
  sleep_hours: number
  weight_kg: number
  steps: number
  mood: 'great' | 'normal' | 'tired' | 'low' | string
}

export async function readTodayCheckIn(accessToken: string): Promise<DailyCheckIn> {
  return apiRequest('/health/checkin/today', accessToken)
}

export async function saveTodayCheckIn(accessToken: string, value: DailyCheckIn): Promise<{ ok: boolean; date: string }> {
  return apiRequest('/health/checkin/today', accessToken, {
    method: 'PUT',
    body: JSON.stringify(value),
  })
}

export async function updatePlanItem(accessToken: string, itemId: number, done: boolean) {
  return apiRequest<{ ok: boolean; id: number; done: boolean }>(`/agent/plans/items/${itemId}`, accessToken, {
    method: 'PUT',
    body: JSON.stringify({ done }),
  })
}

export interface DietRecord {
  id: number
  name: string
  meal_type: 'breakfast' | 'lunch' | 'dinner' | 'snack' | 'other'
  calories: number
  protein: number
  carbs: number
  fat: number
  fiber: number
  portion: string
  cooking_method: string
  weight_g: number
  source: string
  source_label: string
  recorded_at: string
  version: number
}

export async function readDietRecords(accessToken: string, date: string, mealType = '') {
  const query = new URLSearchParams({ date_from: date, date_to: date, limit: '50' })
  if (mealType) query.set('meal_type', mealType)
  return apiRequest<{ items: DietRecord[]; next_cursor?: string | null; has_more: boolean }>(
    `/diet/records?${query.toString()}`, accessToken,
  )
}

export async function createDietRecord(accessToken: string, value: {
  name: string; meal_type: DietRecord['meal_type']; calories: number; protein: number; carbs: number; fat: number
}) {
  return apiRequest<DietRecord>('/diet/records', accessToken, { method: 'POST', body: JSON.stringify(value) })
}

export async function editDietRecord(accessToken: string, id: number, value: {
  version: number; name: string; meal_type: DietRecord['meal_type']; calories: number; protein: number; carbs: number; fat: number
}) {
  return apiRequest<DietRecord>(`/diet/records/${id}`, accessToken, { method: 'PATCH', body: JSON.stringify(value) })
}

export async function deleteDietRecord(accessToken: string, id: number) {
  return apiRequest<{ ok: boolean }>(`/diet/records/${id}`, accessToken, { method: 'DELETE' })
}

export interface ExerciseRecord {
  id: number
  name: string
  duration_min: number
  calories_burned: number
  intensity: string
  recorded_at: string
  version: number
}

export async function readExerciseRecords(accessToken: string) {
  return apiRequest<ExerciseRecord[]>('/exercise/records', accessToken)
}

export async function createExerciseRecord(accessToken: string, value: {
  name: string; duration_min: number; calories_burned: number; intensity: string
}) {
  return apiRequest<ExerciseRecord>('/exercise/records', accessToken, { method: 'POST', body: JSON.stringify(value) })
}

export async function deleteExerciseRecord(accessToken: string, id: number) {
  return apiRequest<{ ok: boolean }>(`/exercise/records/${id}`, accessToken, { method: 'DELETE' })
}

export async function readEnergyDashboard(accessToken: string) {
  return apiRequest<Record<string, any>>('/health/energy-dashboard', accessToken)
}

export async function readInsights(accessToken: string) {
  return apiRequest<Record<string, any>>('/agent/insights', accessToken)
}

export async function sendInsightFeedback(accessToken: string, code: string, verdict: 'helpful' | 'inaccurate' | 'resolved') {
  return apiRequest<{ ok: boolean }>(`/agent/insights/${encodeURIComponent(code)}/feedback`, accessToken, {
    method: 'POST', body: JSON.stringify({ verdict }),
  })
}

export async function startInsightExperiment(accessToken: string, insightCode: string, variant: 'gentle' | 'standard') {
  return apiRequest<Record<string, any>>('/agent/experiments', accessToken, {
    method: 'POST', body: JSON.stringify({ insight_code: insightCode, variant }),
  })
}

export async function finishInsightExperiment(accessToken: string, id: number) {
  return apiRequest<Record<string, any>>(`/agent/experiments/${id}/finish`, accessToken, { method: 'POST', body: JSON.stringify({}) })
}

export async function cancelInsightExperiment(accessToken: string, id: number) {
  return apiRequest<Record<string, any>>(`/agent/experiments/${id}/cancel`, accessToken, { method: 'POST', body: JSON.stringify({}) })
}

export async function readWeeklyFacts(accessToken: string) {
  return apiRequest<Record<string, any>>('/insights/weekly-facts', accessToken)
}

export async function createWeeklySummary(accessToken: string) {
  return apiRequest<Record<string, any>>('/insights/weekly-report/ai-summary', accessToken, {
    method: 'POST', body: JSON.stringify({}),
  })
}

export async function readHealthState(accessToken: string) {
  return apiRequest<Record<string, any>>('/health/state?window_days=7', accessToken)
}

export async function readNextAction(accessToken: string) {
  return apiRequest<Record<string, any>>('/agent/decision', accessToken)
}

export async function readActionOutcomes(accessToken: string) {
  return apiRequest<Record<string, any>>('/health/outcomes?days=30', accessToken)
}

export async function readCapabilityHonesty(accessToken: string) {
  return apiRequest<Record<string, any>>('/capabilities/honesty', accessToken)
}

export async function readEvaluationDashboard(accessToken: string) {
  return apiRequest<Record<string, any>>('/evaluation/dashboard?days=30', accessToken)
}

export async function readAgentStatistics(accessToken: string) {
  return apiRequest<Record<string, any>>('/agent/stats?days=30', accessToken)
}

export async function generateWorkoutPlan(accessToken: string, value: {
  goal: string; days: number; minutes: number; level: string; equipment: string
}) {
  return apiRequest<Record<string, any>>('/insights/workout-plan', accessToken, { method: 'POST', body: JSON.stringify(value) })
}

export async function readExerciseEffect(accessToken: string, exerciseType: string) {
  return apiRequest<Record<string, any>>(`/fitness/exercise-effects/${encodeURIComponent(exerciseType)}`, accessToken)
}

export async function readPluginCatalog(accessToken: string) {
  return apiRequest<{ plugins: Array<Record<string, any>> }>('/harness/plugin-catalog', accessToken)
}

function idempotencyHeaders() {
  const key = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `android-${Date.now()}-${Math.random().toString(36).slice(2)}`
  return { 'Idempotency-Key': key }
}

export async function createPluginInstallation(accessToken: string, pluginId: string, config: Record<string, any>) {
  return apiRequest<{ installation: Record<string, any> }>('/harness/installations', accessToken, {
    method: 'POST', headers: idempotencyHeaders(), body: JSON.stringify({ plugin_id: pluginId, config }),
  })
}

export async function savePluginInstallation(accessToken: string, id: number, configVersion: number, config: Record<string, any>) {
  return apiRequest<{ installation: Record<string, any> }>(`/harness/installations/${id}`, accessToken, {
    method: 'PATCH', headers: idempotencyHeaders(), body: JSON.stringify({ config_version: configVersion, config }),
  })
}

export async function setPluginInstallationEnabled(accessToken: string, id: number, configVersion: number, enabled: boolean) {
  const action = enabled ? 'resume' : 'pause'
  return apiRequest<{ installation: Record<string, any> }>(`/harness/installations/${id}/${action}`, accessToken, {
    method: 'POST', headers: idempotencyHeaders(), body: JSON.stringify({ config_version: configVersion }),
  })
}

export async function previewPluginInstallation(accessToken: string, id: number, configVersion: number) {
  return apiRequest<Record<string, any>>(`/harness/installations/${id}/preview`, accessToken, {
    method: 'POST', headers: idempotencyHeaders(), body: JSON.stringify({ config_version: configVersion }),
  })
}

export async function deletePluginInstallation(accessToken: string, id: number, configVersion: number) {
  return apiRequest<Record<string, any>>(`/harness/installations/${id}?config_version=${configVersion}`, accessToken, {
    method: 'DELETE', headers: idempotencyHeaders(),
  })
}

export async function readPluginAudit(accessToken: string, id: number) {
  return apiRequest<Record<string, any>>(`/harness/installations/${id}/audit`, accessToken)
}

export async function readAIConfig(accessToken: string) {
  return apiRequest<Record<string, any>>('/users/me/ai-config', accessToken)
}

export async function saveAIConfig(accessToken: string, value: Record<string, any>) {
  return apiRequest<Record<string, any>>('/users/me/ai-config', accessToken, { method: 'PUT', body: JSON.stringify(value) })
}

export async function testAIConfig(accessToken: string, value: Record<string, any>) {
  return apiRequest<Record<string, any>>('/users/me/ai-config/test', accessToken, { method: 'POST', body: JSON.stringify(value) })
}

export async function readVoiceStatus(accessToken: string) {
  return apiRequest<Record<string, any>>('/harness/voice/status', accessToken)
}

export async function transcribeVoice(accessToken: string, request: {
  agent_id: 'xiaojian' | 'xiaokang'
  audio_base64: string
  format: 'm4a'
  request_id: string
}) {
  return apiRequest<{ text: string; agent_id: string; provider: string; trace_id: string }>(
    '/harness/voice/transcribe', accessToken, { method: 'POST', body: JSON.stringify(request) },
  )
}

export async function synthesizeVoice(accessToken: string, request: {
  agent_id: 'xiaojian' | 'xiaokang'
  text: string
  request_id: string
}) {
  return apiRequest<{
    segments: Array<{ index: number; audio_base64: string; content_type: string }>
    partial: boolean
    provider: string
    trace_id: string
    agent_id: string
  }>('/harness/voice/synthesize', accessToken, { method: 'POST', body: JSON.stringify(request) })
}

export async function verifyVoiceOnce(accessToken: string, check: 'asr' | 'tts') {
  return apiRequest<Record<string, any>>('/harness/voice/verify-once', accessToken, {
    method: 'POST', body: JSON.stringify({ provider: 'tencent_cloud', check, acknowledge_quota: true }),
  })
}

export async function testVoiceConfig(accessToken: string, value: Record<string, any>) {
  return apiRequest<Record<string, any>>('/users/me/ai-config/voice-test', accessToken, { method: 'POST', body: JSON.stringify(value) })
}

export async function resetAIConfig(accessToken: string) {
  return apiRequest<{ ok: boolean }>('/users/me/ai-config', accessToken, { method: 'DELETE' })
}

export async function requestCloudbaseTicket(accessToken: string) {
  return apiRequest<{ ticket: string; expires_in: number; uid: string }>(
    '/auth/cloudbase/ticket', accessToken, { method: 'POST', body: JSON.stringify({}) },
  )
}

export async function registerCloudMedia(accessToken: string, body: {
  file_id: string; temp_url: string; media_type: 'image' | 'video'; original_name: string;
  content_type: string; size_bytes: number; expires_in: number;
  purpose?: 'food_analysis' | 'motion_analysis';
}) {
  return apiRequest<{ ok: boolean; media_id: number; file_name: string; cloud_file_id: string; media_type: string; size: number; storage_backend: string }>(
    '/media/register-cloud', accessToken, { method: 'POST', body: JSON.stringify(body) },
  )
}

export async function refreshCloudMediaSource(accessToken: string, mediaId: number, body: { temp_url: string; expires_in: number }) {
  return apiRequest<Record<string, any>>(`/media/${mediaId}/refresh-source`, accessToken, { method: 'PUT', body: JSON.stringify(body) })
}

export async function createFoodJob(accessToken: string, mediaId: number) {
  return apiRequest<Record<string, any>>('/vision/food-jobs', accessToken, {
    method: 'POST', body: JSON.stringify({ media_id: mediaId }),
  })
}

export async function readMobileUploadOptions(accessToken: string) {
  return apiRequest<{
    storage_backend: 'local' | 'cloud_ref' | 's3'
    max_upload_bytes: number
    max_upload_bytes_by_purpose?: { food_analysis: number; motion_analysis: number }
  }>(
    '/media/mobile-upload/options', accessToken,
  )
}

export async function createMobileUploadSession(accessToken: string, request: {
  request_id: string
  original_name: string
  content_type: string
  media_type: 'image' | 'video'
  size_bytes: number
  purpose: 'food_analysis' | 'motion_analysis'
}) {
  return apiRequest<{
    media_id: number
    storage_backend: 's3'
    upload_url?: string
    method?: 'PUT'
    headers?: Record<string, string>
    expires_in?: number
    max_size_bytes?: number
    already_completed: boolean
  }>('/media/mobile-upload/sessions', accessToken, { method: 'POST', body: JSON.stringify(request) })
}

export async function completeMobileUploadSession(accessToken: string, mediaId: number) {
  return apiRequest<{ ok: boolean; media_id: number; storage_backend: string; size: number }>(
    `/media/mobile-upload/sessions/${mediaId}/complete`, accessToken, { method: 'POST', body: '{}' },
  )
}

export async function abortMobileUploadSession(accessToken: string, mediaId: number) {
  return apiRequest<{ ok: boolean; deleted: boolean; cleanup_pending?: boolean; cleanup_after?: string }>(
    `/media/mobile-upload/sessions/${mediaId}`, accessToken, { method: 'DELETE' },
  )
}

export async function uploadMediaToDevelopmentBackend(accessToken: string, file: Blob, originalName: string) {
  const form = new FormData()
  form.append('file', file, originalName)
  const response = await fetch(`${apiRoot()}/media/upload`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${accessToken}`, 'X-Client-Platform': 'android' },
    body: form,
  })
  return decodeResponse<Record<string, any>>(response, accessToken)
}

export async function readFoodJob(accessToken: string, jobId: number) {
  return apiRequest<Record<string, any>>(`/vision/food-jobs/${jobId}`, accessToken)
}

export async function readFoodAnalysis(accessToken: string, analysisId: number) {
  return apiRequest<Record<string, any>>(`/vision/food-analysis/${analysisId}`, accessToken)
}

export async function correctFoodAnalysis(accessToken: string, analysisId: number, body: Record<string, any>) {
  return apiRequest<Record<string, any>>(`/vision/food-analysis/${analysisId}/correct`, accessToken, {
    method: 'PUT', body: JSON.stringify(body),
  })
}

export async function finalizeFoodAnalysis(accessToken: string, analysisId: number, mealType: string) {
  return apiRequest<Record<string, any>>(`/vision/food-analysis/${analysisId}/finalize`, accessToken, {
    method: 'POST', headers: { 'Idempotency-Key': `food-finalize-${analysisId}` },
    body: JSON.stringify({ meal_type: mealType, confirmed: true }),
  })
}

export async function createMotionAnalysis(accessToken: string, body: Record<string, any>, idempotencyKey: string) {
  return apiRequest<Record<string, any>>('/media/motion-analyses', accessToken, {
    method: 'POST', headers: { 'Idempotency-Key': idempotencyKey }, body: JSON.stringify(body),
  })
}

export async function readMotionAnalysis(accessToken: string, analysisId: number) {
  return apiRequest<Record<string, any>>(`/media/motion-analyses/${analysisId}`, accessToken)
}

export async function submitMotionFeedback(accessToken: string, analysisId: number, body: Record<string, any>) {
  return apiRequest<Record<string, any>>(`/media/motion-analyses/${analysisId}/feedback`, accessToken, {
    method: 'POST', body: JSON.stringify(body),
  })
}

export async function readMotionEvidence(accessToken: string, analysisId: number) {
  return apiRequest<Record<string, any>>(`/media/motion-analyses/${analysisId}/evidence`, accessToken)
}

export async function readMotionTrace(accessToken: string, analysisId: number) {
  return apiRequest<Record<string, any>>(`/media/motion-analyses/${analysisId}/trace`, accessToken)
}

export async function reanalyzeMotion(accessToken: string, analysisId: number, body: Record<string, any>, idempotencyKey: string) {
  return apiRequest<Record<string, any>>(`/media/motion-analyses/${analysisId}/reanalyze`, accessToken, {
    method: 'POST', headers: { 'Idempotency-Key': idempotencyKey }, body: JSON.stringify(body),
  })
}

export async function confirmMotionLabel(accessToken: string, analysisId: number, body: Record<string, any>, idempotencyKey?: string) {
  return apiRequest<Record<string, any>>(`/media/motion-analyses/${analysisId}/confirm-label`, accessToken, {
    method: 'POST', ...(idempotencyKey ? { headers: { 'Idempotency-Key': idempotencyKey } } : {}), body: JSON.stringify(body),
  })
}

export async function readTrainingIntent(accessToken: string) {
  return apiRequest<Record<string, any>>('/fitness/training-intent', accessToken)
}

export async function saveTrainingIntent(accessToken: string, body: Record<string, any>) {
  return apiRequest<Record<string, any>>('/fitness/training-intent', accessToken, {
    method: 'PUT', body: JSON.stringify(body),
  })
}

export async function readMotionCapabilities(accessToken: string) {
  return apiRequest<Record<string, any>>('/fitness/motion-capabilities', accessToken)
}

export async function readMediaPlayback(accessToken: string, mediaId: number) {
  return apiRequest<Record<string, any>>(`/media/${mediaId}/playback`, accessToken)
}

export async function readPolicyCandidates(accessToken: string) {
  return apiRequest<Record<string, any>>('/policy/candidates', accessToken)
}

export async function readCurrentPolicyEpisode(accessToken: string) {
  return apiRequest<{ episode: Record<string, any> | null }>('/policy/episodes/current', accessToken)
}

export async function readPolicyHistory(accessToken: string, cursor?: string | null) {
  const query = new URLSearchParams({ limit: '20' })
  if (cursor) query.set('cursor', cursor)
  return apiRequest<{ items: Array<Record<string, any>>; next_cursor?: string | null }>(`/policy/history?${query}`, accessToken)
}

export async function readPolicyTemplates(accessToken: string) {
  return apiRequest<Record<string, any>>('/policy/templates', accessToken)
}

export async function compilePolicy(accessToken: string, minutes: number, requestId: string) {
  return apiRequest<Record<string, any>>('/policy/compile', accessToken, {
    method: 'POST', headers: { 'Idempotency-Key': requestId },
    body: JSON.stringify({ template_id: 'session_duration', template_version: '1.0.0', parameters: { variant: `session_${minutes}m`, time_budget: 'unknown', recovery: 'unknown', schedule: 'unknown' }, goal_key: 'make_plan_sustainable' }),
  })
}

export async function createPolicyEpisodeProposal(accessToken: string, unitId: string) {
  return apiRequest<Record<string, any>>(`/policy/units/${encodeURIComponent(unitId)}/proposal`, accessToken, { method: 'POST', body: JSON.stringify({}) })
}

export async function confirmUserAction(accessToken: string, proposalId: string, version = 1) {
  return apiRequest<Record<string, any>>(`/agent/actions/${encodeURIComponent(proposalId)}/confirm`, accessToken, {
    method: 'POST', body: JSON.stringify({ version, confirmation: true }),
  })
}

export async function readPolicyEpisode(accessToken: string, id: string) {
  return apiRequest<Record<string, any>>(`/policy/episodes/${encodeURIComponent(id)}`, accessToken)
}

export async function readPolicyUnit(accessToken: string, id: string) {
  return apiRequest<Record<string, any>>(`/policy/units/${encodeURIComponent(id)}`, accessToken)
}

export async function reportPolicyExecution(accessToken: string, episodeId: string, body: Record<string, any>, requestId: string) {
  return apiRequest<Record<string, any>>(`/policy/episodes/${encodeURIComponent(episodeId)}/reports`, accessToken, {
    method: 'POST', headers: { 'Idempotency-Key': requestId }, body: JSON.stringify(body),
  })
}

export async function savePolicyObservations(accessToken: string, episodeId: string, body: Record<string, any>, requestId: string) {
  return apiRequest<Record<string, any>>(`/policy/episodes/${encodeURIComponent(episodeId)}/observations`, accessToken, {
    method: 'POST', headers: { 'Idempotency-Key': requestId }, body: JSON.stringify(body),
  })
}

export async function readPolicyExplanation(accessToken: string, episodeId: string) {
  return apiRequest<Record<string, any>>(`/policy/episodes/${encodeURIComponent(episodeId)}/explanation`, accessToken)
}

export async function previewPolicyReview(accessToken: string, episodeId: string) {
  return apiRequest<Record<string, any>>(`/policy/episodes/${encodeURIComponent(episodeId)}/review-preview`, accessToken, { method: 'POST', body: JSON.stringify({}) })
}

export async function createPolicyFinishProposal(accessToken: string, episodeId: string, version: number) {
  return apiRequest<Record<string, any>>(`/policy/episodes/${encodeURIComponent(episodeId)}/finish-proposal`, accessToken, { method: 'POST', body: JSON.stringify({ episode_version: version }) })
}

export async function createPolicyStopProposal(accessToken: string, episodeId: string, version: number) {
  return apiRequest<Record<string, any>>(`/policy/episodes/${encodeURIComponent(episodeId)}/stop-proposal`, accessToken, { method: 'POST', body: JSON.stringify({ episode_version: version, reason_code: 'user_requested' }) })
}

export async function createPolicyMemoryResetProposal(accessToken: string) {
  return apiRequest<Record<string, any>>('/policy/memory/reset-proposal', accessToken, { method: 'POST', body: JSON.stringify({ scope: 'all', strategy_id: null, version: 1 }) })
}
