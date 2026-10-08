<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { App } from '@capacitor/app'
import { useRoute, useRouter } from 'vue-router'
import CharacterStage from '../components/CharacterStage.vue'
import { cancelAgentRun, readRun, requestAgentResponse, synthesizeVoice, transcribeVoice } from '../services/api'
import { cancelVoiceCapture, listenForVoiceCaptureInterruption, startVoiceCapture, stopVoiceCapture } from '../services/nativeVoice'
import { useAuthStore } from '../stores/auth'
import { useCompanionStore, type AgentId } from '../stores/companion'
import { useConversationStore } from '../stores/conversation'
import { useVoiceStore } from '../stores/voice'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const companion = useCompanionStore()
const conversation = useConversationStore()
const voicePreference = useVoiceStore()
const input = ref('')
const sending = ref(false)
const status = ref('告诉我你今天想做什么')
const activity = ref('idle')
const messageArea = ref<HTMLElement | null>(null)
const showQuickPrompts = ref(true)
const errorMessage = ref('')
const pendingRunId = ref<number | null>(null)
const voiceRecording = ref(false)
const voiceWorking = ref(false)
const voiceError = ref('')
const voiceDurationMs = ref(0)
const speakingMessageId = ref('')
let controller: AbortController | null = null
let recordingTimer: ReturnType<typeof setInterval> | null = null
let recordingStartedAt = 0
let currentAudio: HTMLAudioElement | null = null
let finishCurrentAudio: (() => void) | null = null
let appStateListener: { remove: () => Promise<void> } | null = null
let voiceInterruptionListener: { remove: () => Promise<void> } | null = null

const agentId = computed<AgentId>(() => {
  const value = String(route.query.agent || companion.agentId)
  return value === 'xiaojian' || value === 'xiaokang' ? value : 'steward'
})
const agentName = computed(() => ({ xiaojian: '小健', xiaokang: '小康', steward: '小管家' })[agentId.value])
const canSend = computed(() => Boolean(input.value.trim()) && !sending.value)
const visibleMessages = computed(() => conversation.messages.filter(item => item.agentId === agentId.value))
const voiceAvailable = computed(() => agentId.value === 'xiaojian' || agentId.value === 'xiaokang')

const prompts: Record<AgentId, string[]> = {
  xiaojian: ['结合我的目标安排本周训练', '今天适合做什么运动？', '帮我看看最近的运动记录'],
  xiaokang: ['结合睡眠记录给我恢复建议', '今天想轻松活动一下', '帮我安排一周作息计划'],
  steward: ['帮我看看今天的健康记录', '给我整理这周的计划', '有什么需要留意的？'],
}

watch(agentId, value => {
  if (voiceRecording.value) void cancelVoice()
  stopVoicePlayback()
  if (value === 'xiaojian' || value === 'xiaokang') void companion.choose(value)
  showQuickPrompts.value = visibleMessages.value.length === 0
  status.value = '告诉我你今天想做什么'
  activity.value = 'idle'
  errorMessage.value = ''
})

async function scrollToLatest() {
  await nextTick()
  if (messageArea.value) messageArea.value.scrollTop = messageArea.value.scrollHeight
}

async function sendMessage(prefill?: string, channel: 'text' | 'voice' = 'text') {
  const message = String(prefill ?? input.value).trim()
  if (!message || sending.value) return
  await voicePreference.restore()
  errorMessage.value = ''
  input.value = ''
  showQuickPrompts.value = false
  sending.value = true
  activity.value = 'thinking'
  status.value = '正在整理并进行安全复核…'
  pendingRunId.value = null
  conversation.addMessage({ role: 'user', agentId: agentId.value, content: message })
  const responseId = conversation.addMessage({ role: 'assistant', agentId: agentId.value, content: '正在整理并进行安全复核…' })
  await scrollToLatest()
  controller = new AbortController()
  try {
    const result = await requestAgentResponse(
      auth.accessToken,
      { message, agent_id: agentId.value, channel },
      event => {
        if (event.type === 'meta') pendingRunId.value = Number(event.run_id) || null
      },
      controller!.signal,
    )
    pendingRunId.value = Number(result.run_id) || pendingRunId.value
    conversation.setAnswer(responseId, result)
    const previewEligible = Boolean(result.run_id && result.intent === 'plan' && String(result.safety_level || 'normal') === 'normal'
      && result.plan && Array.isArray(result.plan.items) && result.plan.items.length
      && result.presentation?.navigation?.target === 'plan_preview'
      && result.presentation?.write?.status === 'not_applied'
      && result.presentation?.write?.automatic === false
      && result.presentation?.write?.confirmation_required === true)
    status.value = previewEligible ? '草案已准备好，等待你审阅' : '建议已完成安全复核'
    activity.value = previewEligible ? 'presenting' : 'idle'
    if (channel === 'voice' && voicePreference.autoplay && result.reply) {
      void speakMessage(responseId, String(result.reply))
    }
  } catch (error) {
    if (controller?.signal.aborted) {
      const detail = pendingRunId.value
        ? '已停止接收回答；正在检查这次请求的服务端状态。'
        : '已停止接收回答。请求可能已经在服务端完成，请查看运行记录后再决定是否重试。'
      conversation.setError(responseId, detail)
      if (pendingRunId.value) {
        const runId = pendingRunId.value
        void cancelAgentRun(auth.accessToken, runId)
          .catch(() => undefined)
          .then(() => readRun(auth.accessToken, runId))
          .catch(() => undefined)
      }
      status.value = '已停止接收回答'
    } else {
      const message = error instanceof Error ? error.message : '暂时无法获取回答'
      conversation.setError(responseId, message)
      errorMessage.value = message
      status.value = '回答暂时未能送达'
    }
    activity.value = 'error'
  } finally {
    sending.value = false
    controller = null
    await scrollToLatest()
  }
}

function clearRecordingTimer() {
  if (recordingTimer) clearInterval(recordingTimer)
  recordingTimer = null
}

async function startVoice() {
  if (!voiceAvailable.value || sending.value || voiceRecording.value || voiceWorking.value) return
  await voicePreference.restore()
  voiceError.value = ''
  try {
    await startVoiceCapture()
    recordingStartedAt = Date.now()
    voiceDurationMs.value = 0
    voiceRecording.value = true
    status.value = '正在聆聽…點按停止後識別'
    recordingTimer = setInterval(() => {
      voiceDurationMs.value = Date.now() - recordingStartedAt
      if (voiceDurationMs.value >= 30_000) void stopVoice()
    }, 200)
  } catch (error) {
    const message = error instanceof Error ? error.message : ''
    if (/MICROPHONE_PERMISSION_DENIED|麦克风权限|permission denied/i.test(message)) {
      voiceError.value = '语音输入会使用麦克风记录你说的话；授权后可以语音输入，也可以继续文字输入。'
      status.value = '可以继续文字输入'
      return
    }
    if (/AUDIO_FOCUS_UNAVAILABLE|其他应用正在使用音频/i.test(message)) {
      voiceError.value = '其他应用正在使用音频，录音暂时无法开始；你可以继续文字输入。'
      status.value = '可以继续文字输入'
      return
    }
    voiceError.value = error instanceof Error ? error.message : '麥克風不可用，仍可使用文字输入'
    status.value = '可以继续文字输入'
  }
}

async function stopVoice() {
  if (!voiceRecording.value || voiceWorking.value) return
  clearRecordingTimer()
  voiceDurationMs.value = Date.now() - recordingStartedAt
  voiceRecording.value = false
  voiceWorking.value = true
  voiceError.value = ''
  status.value = '正在识别语音…'
  try {
    const recording = await stopVoiceCapture()
    if (recording.duration_ms < 420 || voiceDurationMs.value < 420) throw new Error('录音太短了，再试一次')
    const result = await transcribeVoice(auth.accessToken, {
      agent_id: agentId.value === 'xiaokang' ? 'xiaokang' : 'xiaojian',
      audio_base64: recording.audio_base64,
      format: recording.format,
      request_id: `android-asr-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
    })
    const transcript = String(result.text || '').trim()
    if (!transcript) throw new Error('没有听清，再说一次吧')
    voiceWorking.value = false
    status.value = '正在整理语音内容…'
    await sendMessage(transcript, 'voice')
  } catch (error) {
    voiceError.value = error instanceof Error ? error.message : '语音输入暂时不可用'
    status.value = '语音输入没有完成，可以继续文字输入'
  } finally {
    voiceWorking.value = false
  }
}

async function cancelVoice(showMessage = false) {
  clearRecordingTimer()
  voiceRecording.value = false
  voiceWorking.value = false
  voiceDurationMs.value = 0
  await cancelVoiceCapture().catch(() => undefined)
  if (showMessage) {
    voiceError.value = '切到后台后录音已结束，请重新开始；文字输入仍可继续。'
    status.value = '录音已结束'
  }
}

function stopVoicePlayback() {
  const finish = finishCurrentAudio
  finishCurrentAudio = null
  if (currentAudio) {
    currentAudio.onpause = null
    currentAudio.pause()
    currentAudio.currentTime = 0
    currentAudio.src = ''
    currentAudio = null
  }
  speakingMessageId.value = ''
  finish?.()
}

function playSegment(audioBase64: string, contentType: string) {
  return new Promise<void>((resolve, reject) => {
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
      reject(new Error('语音播放失败，请检查设备音量后重试'))
    }
    const audio = new Audio(`data:${contentType || 'audio/mpeg'};base64,${audioBase64}`)
    currentAudio = audio
    finishCurrentAudio = finish
    audio.onended = finish
    audio.onerror = fail
    audio.onpause = () => { if (!audio.ended) fail() }
    void audio.play().catch(() => {
      if (settled) return
      settled = true
      finishCurrentAudio = null
      reject(new Error('设备没有允许播放语音，请再点一次朗读'))
    })
  })
}

async function speakMessage(messageId: string, text: string) {
  if (!voiceAvailable.value || !text || text.length > 4000) return
  if (speakingMessageId.value === messageId) {
    stopVoicePlayback()
    return
  }
  stopVoicePlayback()
  speakingMessageId.value = messageId
  voiceError.value = ''
  try {
    const result = await synthesizeVoice(auth.accessToken, {
      agent_id: agentId.value === 'xiaokang' ? 'xiaokang' : 'xiaojian',
      text,
      request_id: `android-tts-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
    })
    for (const segment of result.segments) {
      if (speakingMessageId.value !== messageId) break
      await playSegment(segment.audio_base64, segment.content_type)
    }
    if (result.partial) voiceError.value = '部分语音未能生成，已播放可用内容。'
  } catch (error) {
    voiceError.value = error instanceof Error ? error.message : '语音播报暂时不可用'
  } finally {
    if (speakingMessageId.value === messageId) stopVoicePlayback()
  }
}

async function watchAppState() {
  appStateListener = await App.addListener('appStateChange', state => {
    if (!state.isActive) {
      if (voiceRecording.value) void cancelVoice(true)
      stopVoicePlayback()
    }
  })
  voiceInterruptionListener = await listenForVoiceCaptureInterruption(reason => {
    clearRecordingTimer()
    voiceRecording.value = false
    voiceWorking.value = false
    voiceDurationMs.value = 0
    voiceError.value = reason === 'audio_focus_lost'
      ? '系统音频被其他应用占用，录音已结束；你仍可使用文字输入。'
      : '录音已结束；你仍可使用文字输入。'
    status.value = '录音已结束'
  })
}

function stopReceiving() {
  controller?.abort()
}

function openPlan(runId: number) {
  void router.push({ path: '/plan', query: { runId: String(runId), agent: agentId.value } })
}

function canReviewPlan(result?: Record<string, unknown>) {
  if (!result) return false
  const plan = result.plan as { items?: unknown[] } | null | undefined
  const presentation = result.presentation as { write?: { status?: string; automatic?: boolean; confirmation_required?: boolean } } | undefined
  const safety = String(result.safety_level || 'normal')
  return Boolean(result.run_id && result.intent === 'plan' && safety === 'normal'
    && Array.isArray(plan?.items) && plan.items.length
    && presentation?.write?.status === 'not_applied'
    && presentation.write.automatic === false
    && presentation.write.confirmation_required === true)
}

onMounted(() => {
  void voicePreference.restore()
  showQuickPrompts.value = visibleMessages.value.length === 0
  void scrollToLatest()
  void watchAppState()
})

onBeforeUnmount(() => {
  controller?.abort()
  clearRecordingTimer()
  void cancelVoiceCapture().catch(() => undefined)
  stopVoicePlayback()
  void appStateListener?.remove()
  void voiceInterruptionListener?.remove()
})
</script>

<template>
  <section class="page chat-page">
    <header class="chat-heading">
      <button class="back-button" aria-label="返回" @click="router.push('/home')">‹</button>
      <div class="chat-character"><img :src="`/generated-assets/${agentId === 'steward' ? 'icons/agent-steward.png' : `characters/${agentId}-portrait-v1.png`}`" :alt="agentName" /></div>
      <div class="chat-title"><h1>{{ agentName }}</h1><p>{{ status }}</p></div>
      <span class="chat-safety">安全复核</span>
    </header>

    <div class="chat-body" ref="messageArea" @scroll.passive="showQuickPrompts = false">
      <div v-if="showQuickPrompts" class="welcome-card card-surface">
        <CharacterStage v-if="agentId !== 'steward'" :agent-id="agentId" activity="idle" />
        <div v-else class="steward-mark"><img :src="'/generated-assets/icons/agent-steward.png'" alt="" /></div>
        <span class="eyebrow">{{ agentId === 'steward' ? '健康小管家' : '小健助手' }}</span>
        <h2>{{ agentId === 'xiaokang' ? '今天想照顾好哪一件事？' : agentId === 'xiaojian' ? '训练、记录和计划都可以告诉我。' : '今天想先处理什么？' }}</h2>
        <p>建议会结合已有记录；涉及写入的操作会先交给你确认。</p>
        <div class="suggestion-list">
          <button v-for="prompt in prompts[agentId]" :key="prompt" @click="sendMessage(prompt)">{{ prompt }}<span>›</span></button>
        </div>
      </div>

      <div class="message-list">
        <article v-for="message in visibleMessages" :key="message.id" class="message-row" :class="message.role">
          <img v-if="message.role === 'assistant'" class="message-avatar" :src="`/generated-assets/${message.agentId === 'steward' ? 'icons/agent-steward.png' : `characters/${message.agentId}-portrait-v1.png`}`" alt="" />
          <div class="message-content" :class="{ 'message-error': message.error }">
            <template v-if="message.content">{{ message.content }}</template>
            <template v-else><span class="thinking-indicator"><i /><i /><i /></span></template>
            <div v-if="message.result && canReviewPlan(message.result as unknown as Record<string, unknown>)" class="draft-action">
              <div class="draft-status"><span>只读计划草案</span><b>确认前不会写入</b></div>
              <button @click="openPlan(Number(message.result?.run_id))">审阅这份计划 <span>›</span></button>
            </div>
            <button
              v-if="message.role === 'assistant' && voiceAvailable && !message.error && message.content && message.content.length <= 4000"
              class="voice-read-button"
              type="button"
              :aria-label="speakingMessageId === message.id ? '停止朗读' : '朗读回答'"
              @click="speakMessage(message.id, message.content)"
            >{{ speakingMessageId === message.id ? '停止朗读' : '朗读回答' }}</button>
          </div>
        </article>
      </div>
      <p v-if="errorMessage" class="chat-error">{{ errorMessage }}</p>
    </div>

    <footer class="composer-wrap">
      <div v-if="voiceRecording || voiceWorking || voiceError" class="voice-status-row" role="status">
        <span v-if="voiceRecording">正在聆听 · {{ Math.ceil(voiceDurationMs / 1000) }} 秒 / 30 秒</span>
        <span v-else-if="voiceWorking">正在识别语音…</span>
        <span v-else>{{ voiceError }}</span>
        <button v-if="voiceRecording" type="button" @click="stopVoice">完成</button>
        <button v-else-if="voiceError" type="button" @click="voiceError = ''">知道了</button>
      </div>
      <form class="composer" @submit.prevent="sendMessage()">
        <textarea v-model="input" maxlength="2000" rows="1" :disabled="sending || voiceRecording || voiceWorking" placeholder="告诉我你想做什么…" @keydown.enter.exact.prevent="sendMessage()" />
        <button
          v-if="voiceAvailable && !sending"
          type="button"
          class="composer-circle voice-button"
          :class="{ 'voice-button-active': voiceRecording }"
          :disabled="voiceWorking"
          :aria-label="voiceRecording ? '结束语音输入' : '开始语音输入'"
          @click="voiceRecording ? stopVoice() : startVoice()"
        >{{ voiceRecording ? '■' : '●' }}</button>
        <button v-if="sending" type="button" class="composer-circle stop-button" aria-label="停止接收回答" @click="stopReceiving"><span /></button>
        <button v-else type="submit" class="composer-circle" :disabled="!canSend || voiceRecording || voiceWorking" aria-label="发送"><img :src="'/generated-assets/icons/send.png'" alt="" /></button>
      </form>
      <p class="composer-note">语音会发给已启用的识别服务转成文字；录音结束后会清除手机上的临时文件。</p>
    </footer>
  </section>
</template>

<style scoped>
.chat-page { display:flex; flex-direction:column; overflow:hidden; padding-top:4px; }
.chat-heading { display:flex; align-items:center; gap:10px; padding:7px 2px 13px; border-bottom:1px solid #eeeee6; }
.back-button,.close-button { display:flex; align-items:center; justify-content:center; width:38px; height:38px; flex:0 0 38px; padding:0; border:0; border-radius:50%; color:#111613; background:#e9efd9; font-size:30px; line-height:1; }
.chat-character { width:40px; height:40px; flex:0 0 40px; overflow:hidden; border-radius:14px; background:#e9efd9; }.chat-character img { width:100%; height:100%; object-fit:cover; }
.chat-title { flex:1; min-width:0; }.chat-title h1 { margin:0; font-size:17px; }.chat-title p { margin:3px 0 0; color:#5f665f; font-size:11px; }
.chat-safety { padding:7px 9px; border-radius:14px; color:#506336; background:#e9efd9; font-size:10px; white-space:nowrap; }
.chat-body { flex:1; min-height:0; overflow-y:auto; padding:14px 0 10px; overscroll-behavior:contain; }
.welcome-card { padding:14px; }.welcome-card :deep(.stage) { height:clamp(145px,45vw,205px); margin:0 0 13px; }.welcome-card h2 { margin:7px 0 6px; font-size:20px; line-height:1.45; }.welcome-card p { margin:0; color:#5f665f; font-size:12px; line-height:1.6; }
.steward-mark { display:flex; align-items:center; justify-content:center; width:60px; height:60px; margin-bottom:14px; border-radius:20px; background:#e9efd9; }.steward-mark img { width:40px; height:40px; object-fit:contain; }
.suggestion-list { display:grid; gap:8px; margin-top:14px; }.suggestion-list button { display:flex; justify-content:space-between; align-items:center; min-height:42px; padding:0 12px; border:1px solid #eeeee6; border-radius:14px; color:#111613; background:#fff; text-align:left; font-size:12px; }.suggestion-list button span { color:#506336; font-size:21px; }
.message-list { display:flex; flex-direction:column; gap:14px; margin-top:14px; }.message-row { display:flex; align-items:flex-start; gap:8px; }.message-row.user { justify-content:flex-end; }.message-avatar { width:30px; height:30px; flex:0 0 30px; border-radius:11px; object-fit:cover; background:#e9efd9; }
.message-content { max-width:84%; padding:12px 14px; border-radius:18px; color:#111613; background:#fff; box-shadow:var(--card-shadow); font-size:14px; line-height:1.7; white-space:pre-wrap; overflow-wrap:anywhere; }.message-row.user .message-content { border-bottom-right-radius:6px; background:#e9efd9; box-shadow:none; }.message-row.assistant .message-content { border-top-left-radius:6px; }.message-content.message-error { color:#8f3028; background:#fff6f4; }
.draft-action { margin-top:12px; padding-top:10px; border-top:1px solid #eeeee6; }.draft-status { display:flex; justify-content:space-between; gap:12px; color:#5f665f; font-size:10px; }.draft-status b { color:#506336; font-weight:600; }.draft-action button { display:flex; justify-content:space-between; align-items:center; width:100%; min-height:38px; margin-top:9px; padding:0 12px; border:0; border-radius:13px; background:#c4e267; color:#111613; font-size:12px; font-weight:700; }.draft-action button span { font-size:20px; }
.voice-read-button { margin-top:9px; padding:5px 9px; border:0; border-radius:12px; color:#506336; background:#e9efd9; font-size:10px; }
.thinking-indicator { display:inline-flex; gap:5px; align-items:center; height:17px; }.thinking-indicator i { width:6px; height:6px; border-radius:50%; background:#506336; animation:think 1s ease-in-out infinite; }.thinking-indicator i:nth-child(2) { animation-delay:.12s; }.thinking-indicator i:nth-child(3) { animation-delay:.24s; }
.chat-error { margin:10px 0 0; color:#c0392b; font-size:12px; }
.composer-wrap { padding:9px 0 7px; background:#f7f7f2; }.composer { display:flex; align-items:flex-end; gap:9px; padding:7px 8px 7px 14px; border:1px solid #eeeee6; border-radius:25px; background:#fff; box-shadow:var(--card-shadow); }.composer textarea { flex:1; min-width:0; max-height:100px; resize:none; border:0; outline:0; color:#111613; background:transparent; font:inherit; font-size:14px; line-height:1.45; padding:8px 0; }.composer-circle { display:flex; flex:0 0 42px; align-items:center; justify-content:center; width:42px; min-width:42px; max-width:42px; height:42px; min-height:42px; max-height:42px; padding:0; border:0; border-radius:50%; background:#c4e267; }.composer-circle:disabled { opacity:.4; }.composer-circle img { width:23px; height:23px; object-fit:contain; }.stop-button { background:#e9efd9; }.stop-button span { width:12px; height:12px; border-radius:3px; background:#506336; }.voice-button { color:#506336; background:#e9efd9; font-size:17px; }.voice-button-active { color:#fff; background:#8f3028; }.voice-status-row { display:flex; align-items:center; justify-content:space-between; gap:8px; margin:0 4px 8px; padding:8px 12px; border-radius:14px; color:#506336; background:#e9efd9; font-size:11px; }.voice-status-row button { padding:5px 10px; border:0; border-radius:10px; color:#111613; background:#c4e267; font-size:11px; }.composer-note { margin:7px 7px 0; color:#7b8078; font-size:10px; line-height:1.45; }
@keyframes think { 0%,60%,100%{opacity:.3;transform:translateY(0)} 30%{opacity:1;transform:translateY(-4px)} }
</style>
