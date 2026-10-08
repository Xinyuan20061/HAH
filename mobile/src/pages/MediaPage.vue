<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { waitForAppForeground } from '../services/appLifecycle'
import { confirmMotionLabel, createMotionAnalysis, readMediaPlayback, readMobileUploadOptions, readMotionAnalysis, readMotionCapabilities, readMotionEvidence, readMotionTrace, readTrainingIntent, reanalyzeMotion, saveTrainingIntent, submitMotionFeedback } from '../services/api'
import { motionCorrectionIdempotencyKey } from '../services/motionIdempotency'
import { refreshCloudMedia, uploadCloudMedia } from '../services/cloudMedia'
import { canUseNativeMediaPicker, pickNativeVideo, releaseNativeVideo, type NativeVideoSource } from '../services/nativeMedia'
import { clearUserJob, readUserJob, writeUserJob } from '../services/userScopedJobs.mjs'
import { useAuthStore } from '../stores/auth'

type Asset = { media_id: number; cloud_file_id: string; size?: number }
type Frame = { id: string; timestamp_ms: number; preview_url: string; phase: string; observation: string; explanation: string; next_step: string; advice_kind: string }
const router = useRouter(); const auth = useAuthStore()
const filePicker = ref<HTMLInputElement | null>(null); const cameraPicker = ref<HTMLInputElement | null>(null)
const file = ref<File | null>(null); const localUrl = ref(''); const fileName = ref(''); const asset = ref<Asset | null>(null)
const nativeVideo = ref<NativeVideoSource | null>(null); const uploadProgress = ref<number | null>(null)
const uploadRequestId = ref(crypto.randomUUID())
const analysisId = ref<number | null>(null); const run = ref<Record<string, any> | null>(null); const raw = computed(() => run.value?.result || {})
const frames = ref<Frame[]>([]); const activeFrameId = ref(''); const playableUrl = ref(''); const trace = ref<Record<string, any> | null>(null)
const loading = ref(false); const savingIntent = ref(false); const statusText = ref(''); const error = ref(''); const consent = ref(false)
const exercise = ref('auto'); const exerciseOptions = ref<Array<{ id: string; name_zh: string }>>([
  { id: 'auto', name_zh: '自动识别' }, { id: 'squat', name_zh: '深蹲' }, { id: 'pushup', name_zh: '俯卧撑' }, { id: 'lunge', name_zh: '弓步蹲' }, { id: 'leg_abduction', name_zh: '腿外展' }, { id: 'arm_abduction', name_zh: '直臂侧平举' }, { id: 'arm_vw', name_zh: '手臂 V/W' },
])
const targetOptions = [{ key: 'chest', label: '胸部' }, { key: 'back', label: '背部' }, { key: 'shoulders', label: '肩部' }, { key: 'arms', label: '手臂' }, { key: 'core', label: '核心' }, { key: 'quadriceps', label: '大腿前侧' }, { key: 'glutes', label: '臀部' }, { key: 'hamstrings', label: '大腿后侧' }]
const goalOptions = [{ key: 'strength', label: '力量' }, { key: 'hypertrophy', label: '增肌' }, { key: 'muscular_endurance', label: '肌耐力' }, { key: 'balance', label: '平衡与稳定' }, { key: 'core_stability', label: '核心稳定' }]
const intent = reactive<{ target_body_parts: string[]; goals: string[]; constraints: string[]; preferred_equipment: string[]; notes: string }>({ target_body_parts: [], goals: [], constraints: [], preferred_equipment: [], notes: '' })
const correction = reactive({ canonical_id: '', novel_label_zh: '' })
let stopped = false
const pageLifetime = new AbortController()
let activeUploadAbortController: AbortController | null = null
const activeFrame = computed(() => frames.value.find(item => item.id === activeFrameId.value) || frames.value[0] || null)
const statusLabels: Record<string, string> = { queued: '排队中', decoding: '正在读取视频', local_inference: '正在识别动作', evidence_ready: '动作信息已整理', visual_review: '正在复核画面', feedback_generation: '正在整理动作建议', completed: '分析完成', partial: '已完成（部分步骤不可用）', failed: '分析失败', cancelled: '任务已取消' }
const metricLabels: Record<string, string> = { reps: '次数', repetition_count: '次数', count: '次数', duration_ms: '持续时间', duration: '持续时间', rhythm: '节奏', rhythm_control: '节奏', overall: '动作质量', quality: '动作质量', score: '动作质量', completeness: '完成度', stability: '稳定性', risk_index: '动作偏差提示' }
const metrics = computed(() => (Array.isArray(raw.value.metrics) ? raw.value.metrics : []).filter((item: any) => item?.value !== null && metricLabels[item.id]).map((item: any) => ({ label: metricLabels[item.id], value: item.id === 'duration_ms' ? `${(Number(item.value) / 1000).toFixed(1)} 秒` : `${item.value}${item.unit || ''}` })))
const recognition = computed(() => raw.value.recognition || {})
const summary = computed(() => raw.value.summary || {})
const terminal = (status: string) => ['completed', 'partial', 'failed', 'cancelled'].includes(status)
const metricNote = computed(() => (raw.value.notices || []).find((item: any) => item?.kind === 'metric_unavailable')?.text || '本次没有可可靠展示的数值指标，下面的画面讲解仅供参考。')

watch([exercise, consent], () => {
  if (!run.value) return
  analysisId.value = null; run.value = null; frames.value = []; trace.value = null; playableUrl.value = ''
  clearUserJob('hm-motion-job')
  statusText.value = ''
})

function chooseVideo(event: Event) {
  const picked = (event.target as HTMLInputElement).files?.[0]
  if (!picked) return
  if (!picked.type.startsWith('video/')) { error.value = '请选择视频文件'; return }
  if (picked.size > 120 * 1024 * 1024) { error.value = '视频请控制在 120MB 以内'; return }
  if (nativeVideo.value) void releaseNativeVideo(nativeVideo.value.uri)
  nativeVideo.value = null
  if (localUrl.value) URL.revokeObjectURL(localUrl.value)
  file.value = picked; localUrl.value = URL.createObjectURL(picked); fileName.value = picked.name
  uploadRequestId.value = crypto.randomUUID()
  asset.value = null; analysisId.value = null; run.value = null; frames.value = []; trace.value = null; error.value = ''; statusText.value = ''
  clearUserJob('hm-motion-job')
}
async function chooseVideoSource(mode: 'pick' | 'capture') {
  error.value = ''
  if (canUseNativeMediaPicker() && auth.accessToken) {
    try {
      const options = await readMobileUploadOptions(auth.accessToken)
      if (options.storage_backend === 's3') {
        const selected = await pickNativeVideo(mode)
        const limit = options.max_upload_bytes_by_purpose?.motion_analysis ?? options.max_upload_bytes
        if (selected.size_bytes > limit) {
          await releaseNativeVideo(selected.uri)
          throw new Error(`视频超过当前服务器限制（${Math.round(limit / 1024 / 1024)} MB）`)
        }
        if (nativeVideo.value) void releaseNativeVideo(nativeVideo.value.uri)
        if (localUrl.value) URL.revokeObjectURL(localUrl.value)
        nativeVideo.value = selected
        file.value = null
        localUrl.value = ''
        fileName.value = selected.file_name
        uploadRequestId.value = crypto.randomUUID()
        asset.value = null; analysisId.value = null; run.value = null; frames.value = []; trace.value = null
        error.value = ''; statusText.value = ''; uploadProgress.value = null
        clearUserJob('hm-motion-job')
        return
      }
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : '无法打开视频选择器'
      if (message.includes('取消')) return
      error.value = message
      return
    }
  }
  if (mode === 'capture') cameraPicker.value?.click()
  else filePicker.value?.click()
}
function chooseAgain() { void chooseVideoSource('pick') }
function toggleValue(list: string[], key: string) {
  const index = list.indexOf(key)
  if (index < 0) list.push(key); else list.splice(index, 1)
}
function viewFrames(value: Record<string, any>) {
  const timeline = value.timeline?.frames
  const source = Array.isArray(timeline) ? timeline : Array.isArray(value.keyframes) ? value.keyframes : []
  frames.value = source
    .filter((item: any) => Number.isFinite(Number(item.timestamp_ms ?? item.t_ms)))
    .map((item: any, index: number) => ({
      id: String(item.id || `frame_${index}`), timestamp_ms: Number(item.timestamp_ms ?? item.t_ms),
      preview_url: String(item.preview?.url || item.preview_url || item.image_url || ''),
      phase: String(item.phase || ''), observation: String(item.observation || item.finding || ''),
      explanation: String(item.explanation || ''), next_step: String(item.next_step || item.advice || ''),
      advice_kind: String(item.advice_kind || ''),
    })).sort((a: Frame, b: Frame) => a.timestamp_ms - b.timestamp_ms)
  activeFrameId.value = frames.value[0]?.id || ''
}
async function fillEvidence(id: number) {
  if (frames.value.every(frame => frame.preview_url)) return
  try {
    const evidence = await readMotionEvidence(auth.accessToken, id)
    const items = evidence.frames || evidence.evidence || []
    const previews = new Map((items as any[]).map(item => [String(item.frame_id || item.id), String(item.preview_url || item.preview?.url || item.url || '')]))
    frames.value = frames.value.map(frame => ({ ...frame, preview_url: frame.preview_url || previews.get(frame.id) || '' }))
  } catch { /* Explanations remain readable if temporary evidence is unavailable. */ }
}
async function getPlayback(mediaId: number) {
  try {
    const playback = await readMediaPlayback(auth.accessToken, mediaId)
    playableUrl.value = playback.playable_url || ''
  } catch { playableUrl.value = '' }
}
async function loadRun(id: number, currentAsset: Asset, existing = false) {
  const deadline = Date.now() + 300_000
  let delay = 1000; let refreshes = 0
  while (Date.now() < deadline && !stopped) {
    const current = await readMotionAnalysis(auth.accessToken, id)
    run.value = current
    const currentStatus = String(current.status || 'queued')
    statusText.value = statusLabels[currentStatus] || '正在处理'
    if (current.error_code === 'media_url_expired' && refreshes < 2) {
      statusText.value = '正在续期视频访问地址'
      await refreshCloudMedia(auth.accessToken, currentAsset.media_id, currentAsset.cloud_file_id)
      refreshes += 1
      continue
    }
    if (terminal(currentStatus)) break
    await new Promise(resolve => window.setTimeout(resolve, delay))
    await waitForAppForeground(pageLifetime.signal)
    if (stopped) return
    delay = Math.min(5000, Math.round(delay * 1.3))
  }
  if (stopped) return
  const currentStatus = String(run.value?.status || '')
  if (!terminal(currentStatus)) { statusText.value = '任务仍在处理中；你可以稍后回到此页继续查看'; return }
  sessionStorage.removeItem('hm-motion-job')
  if (currentStatus === 'failed') { error.value = run.value?.error || '视频分析没有完成'; return }
  if (currentStatus === 'cancelled') { error.value = '分析任务已取消'; return }
  viewFrames(raw.value)
  await Promise.all([fillEvidence(id), getPlayback(currentAsset.media_id)])
  if (!existing) statusText.value = statusLabels[currentStatus] || currentStatus
}
async function startAnalysis(existing = false, retry = false) {
  if (loading.value) return
  if (!file.value && !nativeVideo.value && !asset.value && !existing) { chooseAgain(); return }
  loading.value = true; stopped = false; error.value = ''; run.value = null; trace.value = null
  try {
    if (!auth.accessToken) throw new Error('请先使用微信登录')
    if (!asset.value) {
      statusText.value = '正在安全上传训练视频'
      const controller = new AbortController()
      activeUploadAbortController = controller
      try {
        asset.value = await uploadCloudMedia(
          auth.accessToken, file.value, fileName.value, 'video', uploadRequestId.value,
          'motion_analysis', nativeVideo.value, percent => { uploadProgress.value = percent }, controller.signal,
        ) as Asset
      } finally {
        if (activeUploadAbortController === controller) activeUploadAbortController = null
      }
      uploadProgress.value = null
      if (stopped) return
    }
    let id = analysisId.value
    if (!id || retry) {
      const mode = consent.value ? 'redacted_frames' : 'off'
      const key = `motion:${asset.value.media_id}:${exercise.value}:${consent.value ? 1 : 0}:motion-unified-v2${retry ? `:retry:${crypto.randomUUID()}` : ''}`
      statusText.value = '正在创建动作分析任务'
      const created = await createMotionAnalysis(auth.accessToken, {
        media_id: asset.value.media_id, requested_exercise: exercise.value, exercise_hint: null,
        cloud_review_mode: mode, consent_version: 'motion-real-frames-v2', response_schema: 'motion-analysis-v2',
      }, key)
      id = Number(created.analysis_id)
      if (!Number.isFinite(id)) throw new Error('服务端没有返回任务编号')
      analysisId.value = id
      writeUserJob('hm-motion-job', auth.user?.id, { mediaId: asset.value.media_id, fileId: asset.value.cloud_file_id, analysisId: id, exercise: exercise.value, consent: consent.value })
    }
    await loadRun(id!, asset.value, existing)
  } catch (cause) {
    if (!stopped) error.value = cause instanceof Error ? cause.message : '视频分析暂时不可用'
  } finally { if (!stopped) loading.value = false }
}
async function saveIntent() {
  savingIntent.value = true; error.value = ''
  try { Object.assign(intent, await saveTrainingIntent(auth.accessToken, intent)); statusText.value = '训练目标已保存' }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '训练目标没有保存' }
  finally { savingIntent.value = false }
}
async function feedback(kind: 'useful' | 'wrong_frame' | 'unhelpful_advice') {
  if (!analysisId.value) return
  try {
    await submitMotionFeedback(auth.accessToken, analysisId.value, {
      kind, ...(kind === 'wrong_frame' && activeFrame.value ? { frame_id: activeFrame.value.id } : {}),
    })
    statusText.value = '感谢反馈，已记录到本次分析'
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '反馈没有保存' }
}
async function correctAndReanalyze() {
  if (!analysisId.value || !asset.value) return
  const canonical = correction.canonical_id
  const novel = correction.novel_label_zh.trim()
  if (!canonical && !novel) { error.value = '请选择一个动作类别，或填写动作描述'; return }
  loading.value = true; error.value = ''
  try {
    const body = canonical ? { canonical_id: canonical } : { novel_label_zh: novel }
    const parent = analysisId.value
    const mode = consent.value ? 'redacted_frames' : 'off'
    const request: Record<string, any> = { cloud_review_mode: mode, reason: 'user_label_correction', correction_confirmed: true }
    if (canonical) request.exercise_hint = canonical
    const confirmKey = await motionCorrectionIdempotencyKey('confirm', asset.value.media_id, parent, { canonical_id: canonical, novel_label_zh: novel }, mode)
    const reanalyzeKey = await motionCorrectionIdempotencyKey('reanalyze', asset.value.media_id, parent, { canonical_id: canonical, novel_label_zh: novel }, mode)
    await confirmMotionLabel(auth.accessToken, parent, body, confirmKey)
    const child = await reanalyzeMotion(auth.accessToken, parent, request, reanalyzeKey)
    analysisId.value = Number(child.analysis_id); run.value = null; frames.value = []; trace.value = null
    writeUserJob('hm-motion-job', auth.user?.id, { mediaId: asset.value.media_id, fileId: asset.value.cloud_file_id, analysisId: analysisId.value, exercise: exercise.value, consent: consent.value })
    await loadRun(analysisId.value, asset.value)
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '动作纠正或重新分析没有完成' }
  finally { loading.value = false }
}
async function showTrace() {
  if (!analysisId.value) return
  if (trace.value) { trace.value = null; return }
  try { trace.value = await readMotionTrace(auth.accessToken, analysisId.value) }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '来源摘要暂时无法读取' }
}
function motionNoticeText(item: unknown) {
  if (typeof item === 'string') return item
  if (!item || typeof item !== 'object') return ''
  const value = item as Record<string, unknown>
  return typeof value.text === 'string' ? value.text : ''
}
async function loadPreferences() {
  try {
    const [saved, capabilities] = await Promise.all([readTrainingIntent(auth.accessToken), readMotionCapabilities(auth.accessToken)])
    Object.assign(intent, saved)
    const actions = (capabilities.actions || []).filter((item: any) => item.id && item.id !== 'auto').map((item: any) => ({ id: item.id, name_zh: item.name_zh || item.display_name || item.id }))
    if (actions.length) exerciseOptions.value = [{ id: 'auto', name_zh: '自动识别' }, ...actions]
    if (exercise.value !== 'auto' && !exerciseOptions.value.some(item => item.id === exercise.value)) exercise.value = 'auto'
  } catch { /* Training preferences are optional for a motion review. */ }
}
onMounted(() => {
  if (!auth.accessToken) return
  void loadPreferences()
  const pointer = readUserJob('hm-motion-job', auth.user?.id)
  if (!pointer) return
  asset.value = { media_id: Number(pointer.mediaId), cloud_file_id: String(pointer.fileId || '') }
  analysisId.value = Number(pointer.analysisId); exercise.value = String(pointer.exercise || 'auto'); consent.value = Boolean(pointer.consent)
  if (!Number.isFinite(asset.value.media_id) || !Number.isFinite(analysisId.value)) {
    clearUserJob('hm-motion-job')
    return
  }
  loading.value = true; stopped = false
  void loadRun(analysisId.value, asset.value, true).catch(cause => { error.value = cause instanceof Error ? cause.message : '读取进行中的任务失败' }).finally(() => { if (!stopped) loading.value = false })
})
onBeforeUnmount(() => {
  stopped = true
  pageLifetime.abort()
  activeUploadAbortController?.abort()
  if (localUrl.value) URL.revokeObjectURL(localUrl.value)
  if (!activeUploadAbortController && nativeVideo.value) void releaseNativeVideo(nativeVideo.value.uri)
})
</script>

<template>
  <section class="page media-page">
    <header class="page-heading"><button class="back-button" aria-label="返回" @click="router.back()">‹</button><div><p class="eyebrow">训练复盘</p><h1>动作视频分析</h1></div></header>
    <section class="card-surface intro"><p>用于训练复盘，不作医疗判断。开启云端画面复核前会再次征求同意。</p></section>
    <section class="card-surface video-card">
      <video v-if="localUrl" :src="localUrl" controls playsinline preload="metadata" />
      <video v-else-if="playableUrl" :src="playableUrl" controls playsinline preload="metadata" />
      <img v-else-if="nativeVideo?.thumbnail_data_url" :src="nativeVideo.thumbnail_data_url" alt="所选训练视频预览" />
      <div v-else-if="nativeVideo" class="video-empty"><span>▷</span><b>视频已选好</b><small>开始分析后将安全上传并在完成后播放</small></div>
      <div v-else class="video-empty"><span>▷</span><b>选择或录制一段训练视频</b><small>请让全身和动作过程尽量完整入镜</small></div>
      <div class="video-actions"><button class="primary" :disabled="loading" @click="chooseAgain">选择视频</button><button :disabled="loading" @click="chooseVideoSource('capture')">录制视频</button><input ref="filePicker" type="file" accept="video/mp4,video/quicktime,video/*" hidden @change="chooseVideo" /><input ref="cameraPicker" type="file" accept="video/*" capture="environment" hidden @change="chooseVideo" /></div>
      <p v-if="fileName" class="file-name">{{ fileName }} · {{ (((file?.size || nativeVideo?.size_bytes || 0) / 1024 / 1024)).toFixed(1) }} MB</p>
    </section>
    <section v-if="uploadProgress !== null" class="card-surface upload-progress" aria-live="polite"><div><span>视频安全上传中</span><b>{{ uploadProgress }}%</b></div><progress :value="uploadProgress" max="100" /></section>
    <section class="card-surface settings-card">
      <label class="select-label">动作类型<select v-model="exercise" :disabled="loading"><option v-for="item in exerciseOptions" :key="item.id" :value="item.id">{{ item.name_zh }}</option></select></label>
      <label class="consent"><input v-model="consent" type="checkbox" :disabled="loading" /><span><b>允许云端复核处理后画面</b><small>关闭时仅使用本地分析。开启后，会把裁剪并遮挡身份信息的关键画面发送给云端复核。</small></span></label>
    </section>
    <button v-if="!run || terminal(String(run.status))" class="wide-primary" :disabled="loading || (!file && !nativeVideo && !asset)" @click="startAnalysis(false, run?.status === 'failed')">{{ loading ? statusText || '处理中…' : run?.status === 'failed' ? '重试分析' : '开始分析' }}</button>
    <div v-if="loading || statusText" class="card-surface progress-card"><span class="spinner" :class="{ active: loading }" /><span>{{ statusText || '正在处理' }}</span></div>
    <p v-if="error" class="error-card">{{ error }}</p>

    <section v-if="run && terminal(String(run.status)) && run.status !== 'failed' && run.status !== 'cancelled'" class="card-surface result-card">
      <div class="result-title"><div><span class="eyebrow">{{ run.status === 'partial' ? '部分分析' : '动作复盘' }}</span><h2>{{ recognition.display_name || recognition.label_zh || '暂未确认具体动作' }}</h2></div><span class="state-badge">{{ recognition.state === 'identified' ? '已识别' : recognition.state === 'likely' ? '待确认' : '暂未确认' }}</span></div>
      <p v-if="summary.text" class="summary">{{ summary.text }}</p><p v-if="summary.primary_next_step" class="next-step">下一步：{{ summary.primary_next_step }}</p>
      <p v-if="recognition.reason && !/^[A-Z][A-Z0-9_]+$/.test(recognition.reason)" class="muted">{{ recognition.reason }}</p>
      <div v-if="metrics.length" class="metrics"><div v-for="(item,index) in metrics" :key="index"><b>{{ item.value }}</b><small>{{ item.label }}</small></div></div>
      <p v-else class="muted">{{ metricNote }}</p>
      <div v-if="frames.length" class="timeline"><h3>关键画面与动作提示</h3><button v-for="frame in frames" :key="frame.id" class="frame" :class="{ selected: activeFrame?.id === frame.id }" @click="activeFrameId = frame.id"><img v-if="frame.preview_url" :src="frame.preview_url" alt="动作证据帧" /><span v-else class="frame-empty">{{ (frame.timestamp_ms / 1000).toFixed(1) }} 秒</span><span class="frame-copy"><b>{{ frame.phase || `${(frame.timestamp_ms / 1000).toFixed(1)} 秒` }}</b><small v-if="frame.observation">{{ frame.observation }}</small><small v-if="frame.explanation">{{ frame.explanation }}</small><small v-if="frame.next_step" class="frame-advice">建议：{{ frame.next_step }}</small></span></button></div>
      <div v-if="raw.notices?.length" class="notice-list"><p v-for="(item,index) in raw.notices" :key="index">{{ item.text || item }}</p></div>
      <div class="feedback"><b>这份讲解是否有帮助？</b><div><button @click="feedback('useful')">有帮助</button><button @click="feedback('unhelpful_advice')">不太有帮助</button><button v-if="activeFrame" @click="feedback('wrong_frame')">画面不准确</button></div></div>
      <div class="correct-card"><h3>动作标签不准确？</h3><div class="correct-row"><select v-model="correction.canonical_id"><option value="">选择动作类别</option><option v-for="item in exerciseOptions.filter(row => row.id !== 'auto')" :key="item.id" :value="item.id">{{ item.name_zh }}</option></select><input v-model="correction.novel_label_zh" maxlength="40" placeholder="或填写动作描述" /></div><button class="secondary-action" :disabled="loading || (!correction.canonical_id && !correction.novel_label_zh.trim())" @click="correctAndReanalyze">记录纠正并重新分析</button></div>
      <button class="trace-toggle" @click="showTrace">{{ trace ? '收起分析摘要' : '查看分析摘要' }}</button><section v-if="trace" class="trace-summary"><b>{{ trace.display_name || '动作分析' }}</b><p v-if="trace.summary?.text">{{ trace.summary.text }}</p><p v-if="trace.summary?.primary_next_step">下一步：{{ trace.summary.primary_next_step }}</p><p v-for="(notice,index) in trace.notices || []" :key="index">{{ motionNoticeText(notice) }}</p></section>
    </section>
    <section class="card-surface intent-card"><div><span class="eyebrow">动作分析</span><h2>训练偏好</h2><p>补充关注部位和训练目标，后续建议会参考这些偏好。</p></div><b>关注部位</b><div class="chips"><button v-for="item in targetOptions" :key="item.key" :class="{ chosen: intent.target_body_parts.includes(item.key) }" @click="toggleValue(intent.target_body_parts, item.key)">{{ item.label }}</button></div><b>训练目标</b><div class="chips"><button v-for="item in goalOptions" :key="item.key" :class="{ chosen: intent.goals.includes(item.key) }" @click="toggleValue(intent.goals, item.key)">{{ item.label }}</button></div><label class="select-label">身体限制或器械备注<textarea v-model="intent.notes" maxlength="1000" placeholder="例如膝盖不适、可使用弹力带"></textarea></label><button class="secondary-action" :disabled="savingIntent" @click="saveIntent">{{ savingIntent ? '保存中…' : '保存训练偏好' }}</button></section>
  </section>
</template>

<style scoped>
.media-page { padding: 8px 0 24px; }
.page-heading { display: flex; align-items: center; gap: 9px; margin-bottom: 12px; }
.page-heading h1 { margin: 3px 0 0; font-size: 22px; }
.back-button { width: 35px; height: 35px; border: 0; border-radius: 50%; background: #fff; font-size: 24px; }
.intro, .settings-card, .intent-card { padding: 13px; margin-bottom: 10px; }
.intro p, .intent-card p, .muted { margin: 0; color: #737a70; font-size: 10px; line-height: 1.55; }
.video-card { padding: 10px; }
.video-card video, .video-card > img { display: block; width: 100%; max-height: 360px; border-radius: 12px; background: #161a16; object-fit: contain; }
.video-empty { display: flex; min-height: 205px; flex-direction: column; align-items: center; justify-content: center; gap: 7px; border-radius: 12px; background: #f3f4ed; color: #68705e; }
.video-empty span { font-size: 30px; }
.video-empty b { font-size: 12px; }
.video-empty small { font-size: 9px; }
.video-actions { display: flex; gap: 8px; margin-top: 9px; }
.video-actions button, .primary { flex: 1; min-height: 38px; border: 0; border-radius: 11px; background: #e9efd9; color: #506336; font-size: 10px; font-weight: 700; }
.primary { background: #c4e267; color: #1c2713; }
.file-name { margin: 7px 2px 0; color: #737a70; font-size: 9px; overflow-wrap: anywhere; }
.upload-progress { margin-top: 10px; padding: 11px 12px; border-radius: 12px; background: #fff; }
.upload-progress > div { display: flex; justify-content: space-between; color: #596052; font-size: 10px; }
.upload-progress progress { width: 100%; height: 7px; margin-top: 8px; accent-color: #6c8a35; }
.select-label { display: flex; flex-direction: column; gap: 5px; color: #596052; font-size: 9px; }
.select-label select, .correct-row select, .correct-row input, .select-label textarea { min-height: 37px; padding: 7px 9px; border: 1px solid #e7e9df; border-radius: 9px; background: #fbfbf8; font-size: 10px; }
.consent { display: flex; align-items: flex-start; gap: 8px; margin-top: 12px; }
.consent input { margin-top: 3px; accent-color: #6c8a35; }
.consent span { display: flex; flex-direction: column; gap: 4px; }
.consent b { font-size: 10px; }
.consent small { color: #737a70; font-size: 9px; line-height: 1.5; }
.wide-primary { width: 100%; min-height: 42px; margin: 0 0 9px; border: 0; border-radius: 12px; background: #c4e267; font-size: 11px; font-weight: 750; }
.wide-primary:disabled { opacity: .5; }
.progress-card { display: flex; align-items: center; gap: 9px; margin-bottom: 9px; padding: 12px; color: #506336; font-size: 10px; }
.spinner { width: 15px; height: 15px; border: 2px solid #dce3ce; border-top-color: #65812c; border-radius: 50%; }
.spinner.active { animation: spin .8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.error-card { padding: 9px; color: #9b382d; font-size: 10px; }
.result-card { margin: 11px 0; padding: 14px; }
.result-title { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.result-title h2, .intent-card h2 { margin: 4px 0; font-size: 16px; }
.state-badge { padding: 5px 7px; border-radius: 8px; background: #edf2e1; color: #506336; font-size: 9px; }
.summary, .next-step { font-size: 11px; line-height: 1.6; }
.next-step { padding: 8px; border-radius: 9px; background: #f2f5ea; color: #506336; }
.metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 7px; margin: 10px 0; }
.metrics > div { display: flex; flex-direction: column; gap: 4px; padding: 9px; border-radius: 10px; background: #f4f5ef; }
.metrics b { font-size: 12px; }
.metrics small { color: #737a70; font-size: 9px; }
.timeline { margin-top: 14px; }
.timeline h3, .correct-card h3 { margin: 0 0 8px; font-size: 12px; }
.frame { display: flex; width: 100%; gap: 9px; margin-bottom: 7px; padding: 7px; border: 1px solid #eceee5; border-radius: 10px; background: #fff; text-align: left; }
.frame.selected { border-color: #9ab14f; background: #fbfcf6; }
.frame img, .frame-empty { width: 77px; height: 60px; flex: none; object-fit: cover; border-radius: 7px; background: #f1f2eb; }
.frame-empty { display: grid; place-items: center; color: #737a70; font-size: 9px; }
.frame-copy { display: flex; flex-direction: column; gap: 3px; }
.frame-copy b { font-size: 10px; }
.frame-copy small { color: #737a70; font-size: 9px; line-height: 1.4; }
.frame-copy .frame-advice { color: #506336; }
.notice-list { padding: 4px 8px; border-radius: 9px; background: #f6f2e6; }
.notice-list p { color: #776b40; font-size: 9px; line-height: 1.4; }
.feedback, .correct-card { margin-top: 13px; padding-top: 10px; border-top: 1px solid #eceee5; }
.feedback > b { font-size: 10px; }
.feedback > div { display: flex; gap: 6px; margin-top: 7px; }
.feedback button, .secondary-action, .trace-toggle { min-height: 34px; padding: 0 8px; border: 0; border-radius: 9px; background: #e9efd9; color: #506336; font-size: 9px; }
.correct-row { display: flex; gap: 6px; }
.correct-row > * { flex: 1; min-width: 0; }
.secondary-action { width: 100%; margin-top: 7px; background: #c4e267; color: #1c2713; font-weight: 700; }
.trace-toggle { width: 100%; margin-top: 10px; }
.trace-summary { margin-top: 8px; padding: 10px; border-radius: 10px; background: #f7f7f2; color: #596052; font-size: 10px; line-height: 1.5; }
.trace-summary b { color: #26331e; }
.trace-summary p { margin: 5px 0 0; }
.intent-card { display: flex; flex-direction: column; gap: 8px; }
.intent-card > b { font-size: 10px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chips button { padding: 7px 9px; border: 1px solid #e7e9df; border-radius: 999px; background: #fbfbf8; color: #596052; font-size: 9px; }
.chips button.chosen { border-color: #b9ce72; background: #eef4dc; color: #506336; }
.intent-card textarea { min-height: 62px; resize: vertical; font: inherit; }
</style>
