<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { applyRunPlan, readCurrentPlan, readRun, updatePlanItem } from '../services/api'
import type { useAuthStore } from '../stores/auth'
import { useAuthStore as useAuth } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuth() as ReturnType<typeof useAuthStore>
const loading = ref(true)
const error = ref('')
const preview = ref<Awaited<ReturnType<typeof readRun>>['plan_preview']>(null)
const reply = ref('')
const currentPlan = ref<Awaited<ReturnType<typeof readCurrentPlan>>['plan']>(null)
const confirmOpen = ref(false)
const applying = ref(false)
const successMessage = ref('')
const updatingItem = ref<number | null>(null)
const runId = computed(() => Number(route.query.runId || 0))
const actorName = computed(() => route.query.agent === 'xiaokang' ? '小康' : route.query.agent === 'steward' ? '小管家' : '小健')
const actorPortrait = computed(() => route.query.agent === 'steward'
  ? '/generated-assets/icons/agent-steward.png'
  : `/generated-assets/characters/${route.query.agent === 'xiaokang' ? 'xiaokang' : 'xiaojian'}-portrait-v1.png`)

const weekdayLabels = ['今天', '明天', '后天', '第 4 天', '第 5 天', '第 6 天', '第 7 天']
const categoryLabels: Record<string, string> = { exercise: '训练', diet: '饮食', sleep: '睡眠', habit: '习惯', recovery: '恢复' }

const previewItems = computed(() => (preview.value?.items || []).map((item, index) => ({
  ...item,
  dayLabel: weekdayLabels[Math.max(0, Math.min(6, Number(item.date_offset) || 0))],
  categoryLabel: categoryLabels[String(item.category || '')] || '健康',
  key: `${index}-${item.title || ''}`,
})))

async function loadPage() {
  loading.value = true
  error.value = ''
  successMessage.value = ''
  try {
    if (runId.value > 0) {
      const result = await readRun(auth.accessToken, runId.value)
      preview.value = result.plan_preview
      reply.value = result.reply || ''
      if (!result.plan_preview) throw new Error('这份草案已失效或没有待确认的计划，请回到助手重新生成。')
    } else {
      preview.value = null
      const result = await readCurrentPlan(auth.accessToken)
      currentPlan.value = result.plan || null
      if (!result.plan) successMessage.value = '还没有本周计划。和小管家聊聊你的安排，确认后计划会显示在这里。'
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '计划暂时无法加载'
  } finally {
    loading.value = false
  }
}

async function updateItem(itemId: number, done: boolean) {
  if (updatingItem.value) return
  updatingItem.value = itemId
  error.value = ''
  try {
    await updatePlanItem(auth.accessToken, itemId, done)
    await loadPage()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '计划状态没有保存，请重试'
  } finally {
    updatingItem.value = null
  }
}

async function confirmPlan() {
  if (!preview.value || preview.value.status !== 'draft' || applying.value || !runId.value) return
  applying.value = true
  error.value = ''
  try {
    await applyRunPlan(auth.accessToken, runId.value)
    await loadPage()
    successMessage.value = '计划已加入。你可以逐项标记完成，记录仍可调整。'
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '计划没有写入，请刷新后查看状态'
  } finally {
    applying.value = false
    confirmOpen.value = false
  }
}

function handleHardwareBack(event: Event) {
  if (!confirmOpen.value) return
  confirmOpen.value = false
  event.preventDefault()
}

watch(() => route.query.runId, () => void loadPage())
onMounted(() => {
  window.addEventListener('hah:hardware-back', handleHardwareBack)
  void loadPage()
})
onBeforeUnmount(() => window.removeEventListener('hah:hardware-back', handleHardwareBack))
</script>

<template>
  <section class="page plan-page">
    <header class="plan-heading"><button class="back-button" aria-label="返回" @click="router.back()">‹</button><div><p class="eyebrow">本周安排</p><h1>{{ preview ? '计划草案' : '本周计划' }}</h1></div><span v-if="preview" class="read-only-chip">只读预览</span></header>

    <div v-if="loading" class="state-card card-surface"><span class="spinner" /><div><b>正在加载计划…</b></div></div>
    <div v-else-if="error" class="state-card error-state"><div><b>计划暂时无法显示</b><p>{{ error }}</p><button class="button-soft" @click="loadPage">重新加载</button></div></div>
    <template v-else-if="preview">
      <div class="review-intro card-surface"><div class="review-mark"><img :src="actorPortrait" alt="" /></div><div><span class="eyebrow">{{ actorName }}整理的草案</span><h2>{{ preview.title || '本周健康计划' }}</h2></div></div>
      <div v-if="reply" class="reply-card card-surface"><p>{{ reply }}</p></div>
      <div class="boundary-note"><span class="boundary-check">✓</span><span><b>{{ preview.status === 'applied' ? '已写入你的计划' : '尚未写入' }}</b><small>{{ preview.status === 'applied' ? '以下内容来自当前计划记录。' : '这是只读草案；只有确认后才会写入。' }}</small></span></div>
      <article v-for="item in previewItems" :key="item.key" class="plan-item card-surface"><div class="item-day">{{ item.dayLabel }}<span>{{ item.categoryLabel }}</span></div><div class="item-copy"><h3>{{ item.title }}</h3><p v-if="item.description">{{ item.description }}</p></div></article>
      <button v-if="preview.status === 'draft'" class="button-primary confirm-button" @click="confirmOpen = true">审阅完成，确认加入</button>
      <button v-else class="button-soft confirm-button" @click="router.replace('/plan')">查看本周计划</button>
    </template>
    <template v-else-if="currentPlan">
      <div class="review-intro card-surface"><div class="review-mark"><img :src="'/generated-assets/icons/agent-steward.png'" alt="" /></div><div><span class="eyebrow">你的健康安排</span><h2>{{ currentPlan.title || '本周计划' }}</h2></div></div>
      <article v-for="(item, index) in currentPlan.items || []" :key="item.id" class="plan-item current-item card-surface"><button class="task-check" :class="{ done: item.done }" :aria-label="item.done ? '标为未完成' : '标记完成'" :disabled="updatingItem === item.id" @click="updateItem(item.id, !item.done)">{{ item.done ? '✓' : '' }}</button><div class="item-copy"><span class="item-date">{{ item.planned_date || `任务 ${index + 1}` }}</span><h3 :class="{ completed: item.done }">{{ item.title }}</h3><p v-if="item.description">{{ item.description }}</p></div></article>
    </template>
    <div v-else class="empty-state card-surface"><div class="empty-mark">✳</div><span class="eyebrow">从适合你的节奏开始</span><h2>先聊聊你想怎么安排</h2><p>{{ successMessage }}</p><button class="button-primary" @click="router.push({ path:'/chat', query:{ agent:'steward' } })">请助手帮我整理</button></div>
    <div v-if="successMessage && currentPlan" class="inline-success">{{ successMessage }}</div>

    <div v-if="confirmOpen" class="confirm-backdrop" @click.self="confirmOpen = false"><section class="confirm-dialog card-surface" role="dialog" aria-modal="true" aria-labelledby="confirm-title"><div class="confirm-symbol">✓</div><p class="eyebrow">请确认</p><h2 id="confirm-title">加入这份计划？</h2><p>确认后，这份草案会写入你的本周计划。你之后仍可以调整每项安排。</p><div class="confirm-actions"><button class="button-soft" :disabled="applying" @click="confirmOpen = false">再看看</button><button class="button-primary" :disabled="applying" @click="confirmPlan">{{ applying ? '正在确认…' : '确认并加入' }}</button></div></section></div>
  </section>
</template>

<style scoped>
.plan-page { padding-top:5px; }.plan-heading { display:flex; align-items:center; gap:11px; margin:3px 0 17px; }.plan-heading h1 { margin:3px 0 0; font-size:26px; }.plan-heading > div { flex:1; }.back-button { display:flex; align-items:center; justify-content:center; width:38px; height:38px; padding:0; border:0; border-radius:50%; color:#111613; background:#e9efd9; font-size:29px; }.read-only-chip { padding:7px 9px; border-radius:13px; color:#506336; background:#e9efd9; font-size:10px; }
.review-intro { display:flex; align-items:center; gap:12px; padding:15px; }.review-mark { display:flex; align-items:center; justify-content:center; width:47px; height:47px; flex:0 0 47px; overflow:hidden; border-radius:16px; background:#e9efd9; }.review-mark img { width:100%; height:100%; object-fit:cover; }.review-intro h2 { margin:4px 0 0; font-size:17px; }.reply-card { margin-top:10px; padding:14px; color:#111613; font-size:13px; line-height:1.7; white-space:pre-wrap; }.reply-card p { margin:0; }.boundary-note { display:flex; gap:9px; align-items:center; margin:15px 4px 12px; }.boundary-check { display:flex; align-items:center; justify-content:center; width:22px; height:22px; flex:0 0 22px; border-radius:50%; color:#111613; background:#c4e267; font-size:13px; }.boundary-note > span:last-child { display:flex; flex-direction:column; gap:2px; }.boundary-note b { font-size:12px; }.boundary-note small { color:#5f665f; font-size:10px; }
.plan-item { display:flex; gap:12px; margin-bottom:10px; padding:14px; }.item-day { display:flex; flex:0 0 56px; flex-direction:column; gap:6px; color:#111613; font-size:13px; font-weight:700; }.item-day span { color:#506336; font-size:10px; font-weight:500; }.item-copy { min-width:0; }.item-copy h3 { margin:0; font-size:14px; line-height:1.5; }.item-copy p { margin:4px 0 0; color:#5f665f; font-size:11px; line-height:1.55; }.confirm-button { width:100%; margin:6px 0 12px; }.button-primary,.button-soft { display:flex; align-items:center; justify-content:center; min-height:46px; padding:0 16px; border:0; border-radius:16px; color:#111613; font-size:13px; font-weight:700; }.button-primary { background:#c4e267; }.button-soft { background:#e9efd9; }.button-primary:disabled,.button-soft:disabled { opacity:.5; }
.current-item { align-items:flex-start; }.task-check { display:flex; align-items:center; justify-content:center; width:25px; height:25px; flex:0 0 25px; padding:0; border:1px solid #cdd9ab; border-radius:50%; color:#111613; background:white; }.task-check.done { border-color:#c4e267; background:#c4e267; }.item-date { display:block; margin-bottom:4px; color:#506336; font-size:10px; }.completed { color:#5f665f; text-decoration:line-through; }.inline-success { margin:5px 0 13px; color:#506336; font-size:12px; text-align:center; }
.empty-state { display:flex; flex-direction:column; align-items:center; padding:31px 22px; text-align:center; }.empty-mark { display:flex; align-items:center; justify-content:center; width:53px; height:53px; margin-bottom:16px; border-radius:18px; color:#506336; background:#e9efd9; font-size:27px; }.empty-state h2 { margin:8px 0 5px; font-size:19px; }.empty-state p { margin:0 0 17px; color:#5f665f; font-size:12px; line-height:1.6; }.empty-state .button-primary { width:100%; }.state-card { display:flex; align-items:center; gap:13px; padding:16px; }.state-card b { font-size:13px; }.state-card p { margin:4px 0 0; color:#5f665f; font-size:11px; }.error-state { color:#c0392b; background:#fff6f4; }.error-state .button-soft { margin-top:10px; min-height:38px; }.spinner { width:21px; height:21px; flex:0 0 21px; border:3px solid #e9efd9; border-top-color:#506336; border-radius:50%; animation:spin .8s linear infinite; }
.confirm-backdrop { position:fixed; z-index:30; inset:0; display:flex; align-items:center; justify-content:center; padding:25px; background:rgba(17,22,19,.34); }.confirm-dialog { width:min(100%,380px); padding:23px; text-align:center; }.confirm-symbol { display:flex; align-items:center; justify-content:center; width:44px; height:44px; margin:0 auto 14px; border-radius:50%; background:#c4e267; font-size:20px; }.confirm-dialog h2 { margin:7px 0; font-size:21px; }.confirm-dialog > p:not(.eyebrow) { margin:0; color:#5f665f; font-size:13px; line-height:1.65; }.confirm-actions { display:grid; grid-template-columns:1fr 1.3fr; gap:9px; margin-top:19px; }
@keyframes spin { to { transform:rotate(360deg); } }
</style>
