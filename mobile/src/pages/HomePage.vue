<script setup lang="ts">
import { App } from '@capacitor/app'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import CharacterStage from '../components/CharacterStage.vue'
import {
  readCommandCenter,
  cancelAgentRun,
  readRun,
  requestAgentResponse,
  synthesizeVoice,
  transcribeVoice,
  type AgentResult,
} from '../services/api'
import {
  cancelVoiceCapture,
  listenForVoiceCaptureInterruption,
  startVoiceCapture,
  stopVoiceCapture,
} from '../services/nativeVoice'
import { useAuthStore } from '../stores/auth'
import { useCompanionStore, type AgentId } from '../stores/companion'
import { useConversationStore } from '../stores/conversation'
import { useVoiceStore } from '../stores/voice'

type AgentActivity = 'idle' | 'listening' | 'thinking' | 'planning' | 'presenting' | 'speaking' | 'success' | 'error'
type ResponseAction = {
  target: string
  label: string
  path: string
  query?: Record<string, string>
  autoNavigate: boolean
}
type HomeResponse = { heard: string; reply: string; action: ResponseAction | null }

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const companion = useCompanionStore()
const conversation = useConversationStore()
const voicePreference = useVoiceStore()
const activity = ref<AgentActivity>('idle')
const streak = ref<Awaited<ReturnType<typeof readCommandCenter>>['streak'] | null>(null)
const healthDate = ref('')
const loading = ref(true)
const loadError = ref('')
const voiceRecording = ref(false)
const voiceWorking = ref(false)
const voiceStatus = ref('按住说话')
const voiceError = ref('')
const response = ref<HomeResponse | null>(null)
const voiceAgentId = computed<Exclude<AgentId, 'steward'>>(() => companion.agentId === 'xiaokang' ? 'xiaokang' : 'xiaojian')
const characterStatus = computed(() => ({
  idle: '等你开口',
  listening: '正在听',
  thinking: '正在理解',
  planning: '正在整理草案',
  presenting: '正在展示',
  speaking: '正在回应',
  success: '已经准备好',
  error: '先暂停一下',
})[activity.value])
const dateLabel = computed(() => {
  const day = healthDate.value ? new Date(`${healthDate.value}T00:00:00`) : new Date()
  if (!Number.isFinite(day.getTime())) return ''
  const weekdays = ['日', '一', '二', '三', '四', '五', '六']
  return `${day.getMonth() + 1}月${day.getDate()}日 · 周${weekdays[day.getDay()]}`
})

let recordingStartedAt = 0
let captureReady = false
let stopRequested = false
let recordingTimer: number | undefined
let statusTimer: number | undefined
let navigationTimer: number | undefined
let responseController: AbortController | null = null
let responseRunId: number | null = null
let appStateListener: { remove: () => Promise<void> } | null = null
let voiceInterruptionListener: { remove: () => Promise<void> } | null = null
let currentAudio: HTMLAudioElement | null = null
let finishCurrentAudio: (() => void) | null = null
let playbackEpoch = 0

const characters = [
  { id: 'xiaojian' as const, name: '小健', portrait: '/generated-assets/characters/xiaojian-portrait-v1.png' },
  { id: 'xiaokang' as const, name: '小康', portrait: '/generated-assets/characters/xiaokang-portrait-v1.png' },
]
const microphoneIcon = '/generated-assets/icons/mic.png'
const speakerIcon = '/generated-assets/icons/speaker.png'

const destinations: Record<string, { path: string; label: string }> = {
  capability_setup: { path: '/settings/capabilities', label: '开启计划能力' },
  records: { path: '/records', label: '打开记录' },
  workout: { path: '/workout', label: '打开训练' },
  health_state: { path: '/state', label: '查看状态' },
}

function clearRecordingTimer() {
  window.clearTimeout(recordingTimer)
  recordingTimer = undefined
}

function clearPendingNavigation() {
  window.clearTimeout(navigationTimer)
  navigationTimer = undefined
}

function clearStatusTimer() {
  window.clearTimeout(statusTimer)
  statusTimer = undefined
}

async function loadCommandCenter() {
  loading.value = true
  loadError.value = ''
  try {
    const center = await readCommandCenter(auth.accessToken)
    streak.value = center.streak
    healthDate.value = center.date
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '健康数据暂时没有加载出来'
  } finally {
    loading.value = false
  }
}

function selectCompanion(agentId: 'xiaojian' | 'xiaokang') {
  if (voiceRecording.value || voiceWorking.value) return
  clearPendingNavigation()
  stopVoicePlayback()
  response.value = null
  voiceError.value = ''
  voiceStatus.value = '按住说话'
  activity.value = 'idle'
  void companion.choose(agentId)
}

function responseAction(result: AgentResult): ResponseAction | null {
  const navigation = result.presentation?.navigation
  const target = String(navigation?.target || '')
  if (target === 'plan_preview') {
    const runId = Number(result.run_id)
    const linkedRunId = Number(navigation?.params?.run_id)
    const write = result.presentation?.write
    const plan = result.plan
    if (!Number.isInteger(runId) || runId < 1 || linkedRunId !== runId
      || String(result.safety_level || 'normal') !== 'normal'
      || !Array.isArray(plan?.items) || !plan.items.length
      || write?.status !== 'not_applied' || write.automatic !== false || write.confirmation_required !== true) {
      return null
    }
    return {
      target,
      label: '查看计划草案',
      path: '/plan',
      query: { runId: String(runId), agent: companion.agentId },
      autoNavigate: navigation?.mode === 'after_animation',
    }
  }
  const destination = destinations[target]
  if (!destination) return null
  return {
    target,
    label: destination.label,
    path: destination.path,
    query: target === 'capability_setup' ? { focus: 'plan_outcome' } : undefined,
    autoNavigate: navigation?.mode === 'after_animation',
  }
}

function structuredPlanAction(result: AgentResult): ResponseAction | null {
  const runId = Number(result.run_id)
  const items = result.plan?.items
  const write = result.presentation?.write
  if (!Number.isInteger(runId) || runId < 1 || result.intent !== 'plan'
    || String(result.safety_level || 'normal') !== 'normal'
    || !Array.isArray(items) || !items.length
    || write?.status !== 'not_applied' || write.automatic !== false || write.confirmation_required !== true) return null
  return {
    target: 'plan_preview',
    label: '查看计划草案',
    path: '/plan',
    query: { runId: String(runId), agent: companion.agentId },
    autoNavigate: true,
  }
}

function runResponseAction(action = response.value?.action) {
  if (!action) return
  clearPendingNavigation()
  void router.push({ path: action.path, query: action.query })
}

function setFailure(error: unknown, fallback: string) {
  voiceError.value = error instanceof Error ? error.message : fallback
  voiceStatus.value = '再试一次'
  activity.value = 'error'
  clearStatusTimer()
  statusTimer = window.setTimeout(() => {
    if (activity.value === 'error') activity.value = 'idle'
  }, 900)
}

async function playSegment(audioBase64: string, contentType: string) {
  await new Promise<void>((resolve, reject) => {
    let settled = false
    const finish = () => {
      if (settled) return
      settled = true
      finishCurrentAudio = null
      resolve()
    }
    const fail = () => {
      if (settled) return
      settled = true
      finishCurrentAudio = null
      reject(new Error('语音播放失败'))
    }
    const audio = new Audio(`data:${contentType || 'audio/mpeg'};base64,${audioBase64}`)
    currentAudio = audio
    finishCurrentAudio = finish
    audio.onended = finish
    audio.onerror = fail
    audio.onpause = () => { if (!audio.ended) fail() }
    void audio.play().catch(fail)
  })
}

function stopVoicePlayback() {
  playbackEpoch += 1
  const finish = finishCurrentAudio
  finishCurrentAudio = null
  if (currentAudio) {
    currentAudio.onpause = null
    currentAudio.pause()
    currentAudio.currentTime = 0
    currentAudio.src = ''
    currentAudio = null
  }
  finish?.()
}

async function speakReply(text = response.value?.reply || '') {
  if (!text || text.length > 4000) return
  stopVoicePlayback()
  const epoch = playbackEpoch
  const keepsTaskActivity = ['planning', 'presenting', 'success', 'error'].includes(activity.value)
  if (!keepsTaskActivity) activity.value = 'speaking'
  try {
    const result = await synthesizeVoice(auth.accessToken, {
      agent_id: voiceAgentId.value,
      text,
      request_id: `android-home-tts-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
    })
    for (const segment of result.segments) {
      if (epoch !== playbackEpoch) break
      await playSegment(segment.audio_base64, segment.content_type)
    }
  } catch {
    // The text reply remains available when speech synthesis is unavailable.
  } finally {
    stopVoicePlayback()
    if (!keepsTaskActivity && activity.value === 'speaking') activity.value = 'idle'
  }
}

function presentationActivity(result: AgentResult): AgentActivity {
  const cue = String(result.presentation?.cue || result.presentation?.activity || '')
  const cueMap: Record<string, AgentActivity> = {
    'plan.compose': 'planning',
    'answer.present': 'presenting',
    'workout.guide': 'presenting',
    'safety.pause': 'error',
    planning: 'planning',
    presenting: 'presenting',
    success: 'success',
    error: 'error',
  }
  return cueMap[cue] || 'speaking'
}

async function submitVoiceMessage(text: string) {
  activity.value = 'thinking'
  voiceStatus.value = '正在整理建议'
  responseRunId = null
  responseController = new AbortController()
  try {
    const result = await requestAgentResponse(
      auth.accessToken,
      { message: text, agent_id: voiceAgentId.value, channel: 'voice' },
      event => {
        if (event.type === 'meta') {
          responseRunId = Number(event.run_id) || null
          activity.value = 'thinking'
        }
      },
      responseController.signal,
    )
    const reply = String(result.reply || '我听到了，我们继续。')
    const action = responseAction(result) || structuredPlanAction(result)
    conversation.addMessage({ role: 'user', agentId: voiceAgentId.value, content: text })
    conversation.addMessage({
      role: 'assistant', agentId: voiceAgentId.value, content: reply,
      runId: Number(result.run_id) || undefined, result,
    })
    response.value = { heard: text, reply, action }
    voiceStatus.value = '继续说'
    activity.value = presentationActivity(result)
    if (action?.autoNavigate) {
      clearPendingNavigation()
      navigationTimer = window.setTimeout(() => runResponseAction(action), 1600)
    }
    if (voicePreference.autoplay && reply) void speakReply(reply)
  } catch (error) {
    if (responseController.signal.aborted) {
      voiceError.value = '已停止接收回答；请求结果可能仍在服务端，请查看运行记录后再重试。'
      voiceStatus.value = '回答暂时未能送达'
      if (responseRunId) {
        const runId = responseRunId
        void cancelAgentRun(auth.accessToken, runId)
          .catch(() => undefined)
          .then(() => readRun(auth.accessToken, runId))
          .catch(() => undefined)
      }
    } else {
      setFailure(error, '语音回复暂时不可用')
    }
  } finally {
    responseController = null
    responseRunId = null
  }
}

async function handleTranscript(audioBase64: string, durationMs: number) {
  if (durationMs < 420) throw new Error('再按久一点')
  voiceWorking.value = true
  voiceStatus.value = '正在识别语音'
  activity.value = 'thinking'
  const transcript = await transcribeVoice(auth.accessToken, {
    agent_id: voiceAgentId.value,
    audio_base64: audioBase64,
    format: 'm4a',
    request_id: `android-home-asr-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
  })
  const text = String(transcript.text || '').trim()
  if (!text) throw new Error('没有听清，再说一次吧')
  await submitVoiceMessage(text)
}

async function startVoice(event?: PointerEvent | KeyboardEvent) {
  if (voiceRecording.value || voiceWorking.value || !auth.accessToken) return
  if (event && 'pointerId' in event && event.currentTarget instanceof HTMLElement) {
    try { event.currentTarget.setPointerCapture(event.pointerId) } catch { /* capture is optional */ }
  }
  stopRequested = false
  captureReady = false
  clearPendingNavigation()
  clearStatusTimer()
  stopVoicePlayback()
  response.value = null
  voiceError.value = ''
  voiceRecording.value = true
  voiceStatus.value = '正在听'
  activity.value = 'listening'
  try {
    await startVoiceCapture()
    if (stopRequested || !voiceRecording.value) {
      await cancelVoiceCapture().catch(() => undefined)
      voiceStatus.value = '按住说话'
      activity.value = 'idle'
      return
    }
    recordingStartedAt = Date.now()
    captureReady = true
    voiceStatus.value = '松开发送'
    recordingTimer = window.setTimeout(() => { void stopVoice() }, 30_000)
  } catch (error) {
    voiceRecording.value = false
    captureReady = false
    setFailure(error, '麦克风暂时不可用')
  }
}

async function stopVoice() {
  if (!voiceRecording.value) return
  stopRequested = true
  voiceRecording.value = false
  clearRecordingTimer()
  if (!captureReady) return
  captureReady = false
  voiceWorking.value = true
  voiceStatus.value = '正在处理录音'
  const duration = Date.now() - recordingStartedAt
  try {
    const recording = await stopVoiceCapture()
    await handleTranscript(recording.audio_base64, Math.min(duration, recording.duration_ms))
  } catch (error) {
    setFailure(error, '语音输入暂时不可用')
  } finally {
    voiceWorking.value = false
    stopRequested = false
  }
}

async function cancelVoice(showMessage = false) {
  stopRequested = true
  clearRecordingTimer()
  const wasRecording = voiceRecording.value
  voiceRecording.value = false
  captureReady = false
  if (wasRecording) await cancelVoiceCapture().catch(() => undefined)
  if (showMessage) {
    voiceError.value = '录音已结束；文字对话仍可继续。'
    voiceStatus.value = '录音已结束'
  } else if (wasRecording) {
    voiceStatus.value = '按住说话'
    activity.value = 'idle'
  }
}

async function watchAppState() {
  try {
    appStateListener = await App.addListener('appStateChange', state => {
      if (!state.isActive) {
        if (voiceRecording.value) void cancelVoice(true)
        responseController?.abort()
        stopVoicePlayback()
      }
    })
  } catch { /* Browser preview does not expose the native lifecycle plugin. */ }
  voiceInterruptionListener = await listenForVoiceCaptureInterruption(reason => {
    clearRecordingTimer()
    voiceRecording.value = false
    voiceWorking.value = false
    captureReady = false
    voiceError.value = reason === 'audio_focus_lost'
      ? '系统音频被其他应用占用，录音已结束；你仍可继续使用。'
      : '录音已结束；你仍可继续使用。'
    voiceStatus.value = '录音已结束'
    activity.value = 'error'
  })
}

onMounted(async () => {
  if (route.query.agent === 'steward') {
    void router.replace({ path: '/chat', query: { agent: 'steward' } })
    return
  }
  await Promise.all([companion.restore(), voicePreference.restore()])
  void loadCommandCenter()
  void watchAppState()
})

onBeforeUnmount(() => {
  clearRecordingTimer()
  clearPendingNavigation()
  clearStatusTimer()
  responseController?.abort()
  if (voiceRecording.value) void cancelVoiceCapture().catch(() => undefined)
  stopVoicePlayback()
  void appStateListener?.remove()
  void voiceInterruptionListener?.remove()
})
</script>

<template>
  <section class="page home-page">
    <div v-if="loading" class="home-loading" aria-label="正在加载">
      <div class="skeleton skeleton-head" />
      <div class="skeleton skeleton-stage" />
      <div class="skeleton skeleton-voice" />
    </div>
    <section v-else-if="loadError" class="home-error card-surface" role="alert">
      <h1>暂时没有加载出来</h1>
      <p>{{ loadError }}</p>
      <button class="retry-button" @click="loadCommandCenter">重新连接</button>
    </section>
    <template v-else>
      <header class="gym-head">
        <div>
          <p class="date-label">{{ dateLabel }}</p>
          <h1 class="gym-title">{{ companion.space }}</h1>
        </div>
        <div class="streak-mark"><b>{{ streak?.current || 0 }}</b><span>天</span></div>
      </header>

      <div class="week-strip" role="list" aria-label="最近七天记录">
        <div v-for="(day, index) in streak?.last7 || []" :key="day.date || index" class="week-day" :class="{ done: day.done }" role="listitem">
          <span class="week-core">{{ day.done ? '✓' : '' }}</span>
          <span>{{ day.label }}</span>
        </div>
      </div>

      <div class="companion-switch" role="group" aria-label="选择健康伙伴">
        <button
          v-for="character in characters"
          :key="character.id"
          class="companion-choice"
          :class="{ selected: companion.agentId === character.id }"
          :aria-pressed="companion.agentId === character.id"
          :disabled="voiceRecording || voiceWorking"
          @click="selectCompanion(character.id)"
        >
          <span class="choice-portrait"><img :src="character.portrait" :alt="character.name" /></span>
          <span class="choice-name">{{ character.name }}</span>
        </button>
      </div>

      <section class="companion-stage card-surface" :class="`space-${companion.agentId}`">
        <header class="stage-head">
          <h2>{{ companion.name }}</h2>
          <span class="activity-pill"><i />{{ characterStatus }}</span>
        </header>
        <CharacterStage class="stage-character" :agent-id="companion.agentId" :activity="activity" />
      </section>

      <div class="voice-zone">
        <span class="voice-ring" :class="{ recording: voiceRecording }" aria-hidden="true" />
        <button
          class="voice-button"
          :class="{ recording: voiceRecording, working: voiceWorking }"
          :disabled="voiceWorking"
          aria-label="按住与健康伙伴说话"
          @pointerdown.prevent="startVoice"
          @pointerup.prevent="stopVoice"
          @pointercancel.prevent="cancelVoice()"
          @keydown.space.prevent="startVoice"
          @keyup.space.prevent="stopVoice"
          @keydown.enter.prevent="startVoice"
          @keyup.enter.prevent="stopVoice"
          @contextmenu.prevent
        >
          <img :src="microphoneIcon" alt="" />
        </button>
        <p class="voice-status" aria-live="polite">{{ voiceStatus }}</p>
        <p v-if="voiceError" class="voice-error" role="status">{{ voiceError }}</p>
      </div>

      <section v-if="response" class="voice-response card-surface" aria-live="polite">
        <p class="heard-text">“{{ response.heard }}”</p>
        <div class="reply-row">
          <p class="reply-text">{{ response.reply }}</p>
          <button class="replay-button" aria-label="朗读回复" @click="speakReply()">
            <img :src="speakerIcon" alt="" />
          </button>
        </div>
        <button v-if="response.action" class="response-action" @click="runResponseAction()">{{ response.action.label }}</button>
        <p v-if="response.action?.target === 'plan_preview'" class="draft-boundary">草案确认后才会加入计划</p>
      </section>

      <button class="state-entry card-surface" @click="router.push('/state')">
        <span><b>状态与下一步</b><small>建议、依据、上次做完的变化</small></span>
        <span class="state-arrow" aria-hidden="true">→</span>
      </button>
    </template>
  </section>
</template>

<style scoped>
.home-page { padding-top:12px; padding-bottom:24px; }
.gym-head { display:flex; align-items:flex-start; justify-content:space-between; }
.date-label { margin:0; color:var(--muted); font-size:11px; }
.gym-title { margin:3px 0 0; font-size:24px; font-weight:760; letter-spacing:-.5px; line-height:1.2; }
.streak-mark { display:flex; align-items:baseline; gap:4px; color:var(--brand-ink); font-size:11px; }
.streak-mark b { color:var(--ink); font-family:Georgia,"Times New Roman",serif; font-size:33px; line-height:1; }
.week-strip { display:flex; align-items:center; justify-content:space-between; margin-top:12px; padding:8px 10px; border-radius:11px; background:#111613; }
.week-day { display:flex; flex-direction:column; align-items:center; gap:4px; color:rgba(255,255,255,.68); font-size:9px; }
.week-core { display:flex; align-items:center; justify-content:center; width:13px; height:13px; border:1px solid rgba(255,255,255,.46); border-radius:50%; color:#111613; background:rgba(255,255,255,.1); font-size:8px; }
.week-day.done { color:#fff; font-weight:700; }.week-day.done .week-core { border-color:var(--accent); background:var(--accent); }
.companion-switch { display:flex; gap:8px; margin-top:12px; }
.companion-choice { display:flex; flex:1; min-width:0; align-items:center; gap:8px; padding:5px 8px; border-radius:11px; background:#fff; box-shadow:var(--card-shadow); transition:opacity .16s ease-out,transform .24s cubic-bezier(.2,.8,.2,1); }
.companion-choice.selected { background:var(--accent-soft); }
.companion-choice:disabled { opacity:.55; }
.choice-portrait { display:block; flex:0 0 36px; width:36px; height:36px; overflow:hidden; border-radius:9px; background:var(--page-bg); transition:transform .24s cubic-bezier(.2,.8,.2,1); }
.choice-portrait img { width:100%; height:100%; object-fit:cover; }
.companion-choice.selected .choice-portrait { transform:translateY(-2px) scale(1.04); }
.choice-name { font-size:13px; font-weight:720; }
.companion-stage { min-height:280px; margin-top:12px; padding:12px; overflow:hidden; border-radius:16px; }
.stage-head { position:relative; z-index:2; display:flex; align-items:flex-start; justify-content:space-between; gap:8px; }
.stage-head h2 { margin:0; font-size:18px; font-weight:760; line-height:1.2; }
.activity-pill { display:flex; flex:none; align-items:center; gap:5px; padding:4px 7px; border-radius:11px; color:var(--brand-ink); background:var(--accent-soft); font-size:9px; font-weight:700; white-space:nowrap; }
.activity-pill i { width:5px; height:5px; border-radius:50%; background:currentColor; }
.stage-character { display:block; margin-top:8px; }
.voice-zone { position:relative; z-index:3; display:flex; flex-direction:column; align-items:center; margin-top:-28px; }
.voice-ring { position:absolute; top:-6px; width:100px; height:100px; border:1px solid var(--accent-soft); border-radius:50%; opacity:.75; pointer-events:none; }
.voice-ring.recording { animation:voiceRing 1.2s ease-out infinite; }
.voice-button { position:relative; z-index:1; display:flex; flex:0 0 88px; align-items:center; justify-content:center; width:88px; min-width:88px; max-width:88px; height:88px; min-height:88px; max-height:88px; border-radius:50%; background:var(--accent); box-shadow:0 8px 20px rgba(17,22,19,.13); touch-action:none; user-select:none; -webkit-user-select:none; transition:opacity .16s ease-out,transform .2s cubic-bezier(.2,.8,.2,1); }
.voice-button img { width:36px; height:36px; object-fit:contain; }
.voice-button.recording { transform:scale(.96); }
.voice-button.working { opacity:.5; }
.voice-button:active { opacity:.76; transform:scale(.94); }
.voice-status { margin:8px 0 0; color:var(--brand-ink); font-size:11px; font-weight:700; }
.voice-error { max-width:100%; margin:6px 0 0; color:var(--danger); font-size:10px; line-height:1.4; text-align:center; }
.voice-response { margin-top:12px; padding:12px; border-radius:12px; animation:responseIn .34s cubic-bezier(.2,.8,.2,1); }
.heard-text { margin:0; color:var(--muted); font-size:10px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.reply-row { display:flex; align-items:flex-start; gap:8px; margin-top:5px; }
.reply-text { flex:1; min-width:0; margin:0; font-size:13px; line-height:1.6; white-space:pre-wrap; overflow-wrap:anywhere; }
.replay-button { display:flex; flex:0 0 30px; align-items:center; justify-content:center; width:30px; min-width:30px; max-width:30px; height:30px; min-height:30px; max-height:30px; border-radius:50%; background:var(--accent-soft); }
.replay-button img { width:17px; height:17px; object-fit:contain; }
.response-action { width:100%; margin-top:10px; padding:9px 10px; border-radius:10px; color:var(--ink); background:var(--accent); font-size:11px; font-weight:720; text-align:center; }
.draft-boundary { margin:7px 0 0; color:var(--muted); font-size:9px; text-align:center; }
.state-entry { display:flex; width:100%; align-items:center; justify-content:space-between; gap:12px; margin-top:12px; padding:12px; border:1px solid var(--divider); border-radius:12px; text-align:left; }
.state-entry > span:first-child { display:flex; min-width:0; flex-direction:column; gap:4px; }
.state-entry b { font-size:14px; font-weight:700; }.state-entry small { color:var(--muted); font-size:10px; }
.state-arrow { flex:none; color:var(--brand-ink); font-size:20px; }
.home-loading { padding-top:12px; }.skeleton { position:relative; overflow:hidden; border-radius:12px; background:var(--divider); }
.skeleton::after { position:absolute; inset:0; content:''; transform:translateX(-100%); background:linear-gradient(90deg,transparent,rgba(255,255,255,.55),transparent); animation:shine 1.35s infinite; }
.skeleton-head { height:92px; }.skeleton-stage { height:280px; margin-top:12px; }.skeleton-voice { width:88px; height:88px; margin:16px auto 0; border-radius:50%; }
.home-error { margin-top:76px; padding:24px 16px; text-align:center; }.home-error h1 { margin:0; font-size:18px; }.home-error p { margin:8px 0 0; color:var(--muted); font-size:12px; line-height:1.5; }.retry-button { margin-top:16px; padding:9px 18px; border-radius:10px; color:var(--ink); background:var(--accent); font-size:12px; font-weight:700; }
@keyframes voiceRing { 0% { opacity:.72; transform:scale(.88); } 100% { opacity:0; transform:scale(1.18); } }
@keyframes responseIn { from { opacity:0; transform:translateY(6px); } to { opacity:1; transform:translateY(0); } }
@keyframes shine { to { transform:translateX(100%); } }
@media (max-height:720px) { .stage-character :deep(.stage) { height:220px; } .companion-stage { min-height:250px; } .voice-button { flex-basis:76px; width:76px; min-width:76px; max-width:76px; height:76px; min-height:76px; max-height:76px; } .voice-ring { width:88px; height:88px; } }
</style>
