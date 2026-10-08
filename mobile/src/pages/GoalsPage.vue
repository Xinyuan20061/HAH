<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { applyDynamicGoal, evaluateDynamicGoals, explainDynamicGoals, readCommandCenter, readHealthGoals, readTodaySummary, saveHealthGoals, type HealthGoals } from '../services/api'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()
const defaults: HealthGoals = {
  water_target: 1800, sleep_target: 8, exercise_target: 30,
  steps_target: 8000, protein_target: 90, calorie_target: 2000, weekly_checkin_target: 5,
}
const goals = reactive<HealthGoals>({ ...defaults })
const today = ref<Record<string, any>>({})
const lastSeven = ref<Array<{ done: boolean }>>([])
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const saved = ref(false)
const dynamicLoading = ref(false)
const dynamic = ref<Record<string, any> | null>(null)
const explanation = ref('')
const metricCards = [
  { key: 'water_target', title: '每日饮水', unit: 'ml', min: 800, max: 4000, step: 100, currentKey: 'water_ml' },
  { key: 'sleep_target', title: '睡眠时长', unit: '小时', min: 5, max: 10, step: 0.5, currentKey: 'sleep_hours' },
  { key: 'exercise_target', title: '每日运动', unit: '分钟', min: 10, max: 120, step: 5, currentKey: 'exercise_min' },
  { key: 'steps_target', title: '每日步数', unit: '步', min: 2000, max: 20000, step: 500, currentKey: 'steps' },
  { key: 'protein_target', title: '蛋白质参考', unit: 'g', min: 30, max: 200, step: 5, currentKey: 'protein' },
  { key: 'calorie_target', title: '热量参考', unit: 'kcal', min: 1200, max: 3600, step: 50, currentKey: 'calories' },
]
const checkInPercent = computed(() => Math.min(100, Math.round(lastSeven.value.filter(day => day.done).length / goals.weekly_checkin_target * 100)))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [savedGoals, summary, commandCenter] = await Promise.all([
      readHealthGoals(auth.accessToken),
      readTodaySummary(auth.accessToken),
      readCommandCenter(auth.accessToken),
    ])
    Object.assign(goals, savedGoals)
    today.value = summary
    lastSeven.value = commandCenter.streak.last7
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '目标暂时无法加载'
  } finally {
    loading.value = false
  }
}

function setGoal(key: keyof HealthGoals, value: string | number) {
  goals[key] = Number(value)
  saved.value = false
}

async function save() {
  if (saving.value) return
  saving.value = true
  error.value = ''
  saved.value = false
  try {
    const result = await saveHealthGoals(auth.accessToken, { ...goals })
    Object.assign(goals, result)
    saved.value = true
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '目标没有保存，请重试'
  } finally {
    saving.value = false
  }
}

async function evaluate() {
  dynamicLoading.value = true
  error.value = ''
  explanation.value = ''
  try {
    dynamic.value = await evaluateDynamicGoals(auth.accessToken)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '暂时无法评估目标'
  } finally {
    dynamicLoading.value = false
  }
}

async function explain() {
  dynamicLoading.value = true
  try {
    const result = await explainDynamicGoals(auth.accessToken)
    explanation.value = result.summary || ''
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '暂时无法生成说明'
  } finally {
    dynamicLoading.value = false
  }
}

async function applyAdjustment(item: Record<string, any>) {
  if (!item.id || !window.confirm(`确认将${item.metric_label || item.metric}目标调整为 ${item.recommended_target}？`)) return
  dynamicLoading.value = true
  try {
    const result = await applyDynamicGoal(auth.accessToken, Number(item.id))
    Object.assign(goals, result.goals || {})
    dynamic.value = null
    saved.value = true
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '目标调整没有应用，请刷新后重试'
  } finally {
    dynamicLoading.value = false
  }
}

onMounted(() => void load())
</script>

<template>
  <section class="page goals-page">
    <header class="goals-heading">
      <button class="back-button" aria-label="返回" @click="router.back()">‹</button>
      <div><p class="eyebrow">我的目标</p><h1>健康目标</h1></div>
    </header>
    <div v-if="loading" class="card-surface state-card">正在读取目标与今日记录…</div>
    <div v-else-if="error && !Object.keys(today).length" class="card-surface state-card error-state"><p>{{ error }}</p><button @click="load">重试</button></div>
    <template v-else>
      <p class="intro-copy">按适合自己的节奏设置目标。目标是参考值，不是医疗建议。</p>
      <section class="goal-card-list">
        <article v-for="card in metricCards" :key="card.key" class="card-surface goal-card">
          <div class="goal-card-top">
            <div><h2>{{ card.title }}</h2><p>今日记录：{{ Number(today[card.currentKey]) > 0 ? `${today[card.currentKey]} ${card.unit}` : '暂无记录' }}</p></div>
            <strong>{{ goals[card.key as keyof HealthGoals] }}<small>{{ card.unit }}</small></strong>
          </div>
          <input
            type="range"
            :min="card.min"
            :max="card.max"
            :step="card.step"
            :value="goals[card.key as keyof HealthGoals]"
            :aria-label="card.title"
            @input="setGoal(card.key as keyof HealthGoals, ($event.target as HTMLInputElement).value)"
          />
          <div class="range-labels"><span>{{ card.min }}</span><span>{{ card.max }} {{ card.unit }}</span></div>
        </article>
      </section>

      <section class="card-surface weekly-target">
        <div><h2>每周打卡</h2><p>根据本周的打卡记录统计进度。</p></div>
        <div class="weekly-control"><input type="range" min="1" max="7" step="1" :value="goals.weekly_checkin_target" aria-label="每周打卡目标" @input="setGoal('weekly_checkin_target', ($event.target as HTMLInputElement).value)"/><b>{{ goals.weekly_checkin_target }} 天</b></div>
        <div class="weekly-progress"><span :style="{ width: `${checkInPercent}%` }" /></div>
      </section>

      <p v-if="error" class="error-copy">{{ error }}</p>
      <p v-if="saved" class="saved-copy">目标已保存。</p>
      <button class="button-primary save-goals" :disabled="saving" @click="save">{{ saving ? '正在保存…' : '保存目标' }}</button>

      <section class="dynamic-section">
        <div class="dynamic-heading"><div><p class="eyebrow">可选建议</p><h2>目标调整建议</h2></div><button :disabled="dynamicLoading" @click="evaluate">{{ dynamicLoading ? '查看中…' : '查看建议' }}</button></div>
        <p class="dynamic-note">建议会先检查记录覆盖度；数据不足时保持原目标。应用调整前会再次征求你的确认。</p>
        <div v-if="dynamic" class="dynamic-card card-surface">
          <p>{{ dynamic.summary || '评估依据已读取。' }}</p>
          <article v-for="item in dynamic.items || []" :key="item.id || item.metric" class="adjustment-row">
            <div><b>{{ item.metric_label || item.metric }}</b><small>{{ item.reason || item.decision }}</small></div>
            <div class="adjustment-value">{{ item.recommended_target ?? item.current_target ?? '—' }}<small>{{ item.unit || '' }}</small></div>
            <button v-if="['increase','reduce'].includes(item.decision) && item.id" :disabled="dynamicLoading" @click="applyAdjustment(item)">确认应用</button>
          </article>
          <button class="explain-button" :disabled="dynamicLoading" @click="explain">{{ dynamicLoading ? '正在说明…' : '解释这些建议' }}</button>
          <p v-if="explanation" class="explanation-copy">{{ explanation }}</p>
        </div>
      </section>
    </template>
  </section>
</template>

<style scoped>
.goals-heading { display:flex; align-items:center; gap:10px; padding:8px 0 12px; }
.goals-heading h1 { margin:3px 0 0; font-size:21px; }
.back-button { width:34px; height:34px; border:0; border-radius:50%; background:#fff; font-size:24px; }
.intro-copy,.dynamic-note { margin:0 2px 12px; color:var(--muted); font-size:11px; line-height:1.6; }
.goal-card-list { display:flex; flex-direction:column; gap:9px; }
.goal-card,.weekly-target { padding:14px; }
.goal-card-top,.weekly-target { display:flex; align-items:center; justify-content:space-between; gap:12px; }
.goal-card h2,.weekly-target h2 { margin:0; font-size:13px; }
.goal-card p,.weekly-target p { margin:4px 0 0; color:var(--muted); font-size:10px; }
.goal-card strong { white-space:nowrap; font-size:18px; }
.goal-card strong small,.adjustment-value small { margin-left:3px; color:var(--muted); font-size:9px; font-weight:500; }
input[type="range"] { width:100%; margin:14px 0 0; accent-color:#82983e; }
.range-labels { display:flex; justify-content:space-between; color:var(--muted); font-size:9px; }
.weekly-target { display:block; margin-top:9px; }
.weekly-control { display:flex; align-items:center; gap:10px; }.weekly-control input { flex:1; }.weekly-control b { white-space:nowrap; font-size:12px; }
.weekly-progress { height:5px; margin-top:9px; overflow:hidden; border-radius:99px; background:var(--divider); }.weekly-progress span { display:block; height:100%; border-radius:inherit; background:var(--accent); }
.save-goals { width:100%; height:45px; margin:12px 0 22px; border:0; border-radius:14px; background:var(--accent); font-weight:750; }
.saved-copy { margin:10px 0 0; color:var(--brand-ink); font-size:11px; }
.error-copy { color:var(--danger); font-size:11px; }
.dynamic-heading { display:flex; align-items:center; justify-content:space-between; margin-bottom:8px; }.dynamic-heading h2 { margin:3px 0 0; font-size:15px; }.dynamic-heading button,.adjustment-row button,.explain-button { padding:8px 12px; border:0; border-radius:11px; background:var(--accent-soft); font-size:10px; font-weight:700; }
.dynamic-card { padding:14px; }.dynamic-card > p { color:var(--muted); font-size:11px; line-height:1.6; }.adjustment-row { display:flex; align-items:center; gap:9px; padding:10px 0; border-top:1px solid var(--divider); }.adjustment-row > div:first-child { display:flex; flex:1; flex-direction:column; gap:4px; }.adjustment-row b { font-size:11px; }.adjustment-row small { color:var(--muted); font-size:9px; }.adjustment-value { white-space:nowrap; font-size:12px; }.explanation-copy { color:var(--brand-ink); font-size:11px; line-height:1.6; }.state-card { padding:18px; color:var(--muted); font-size:12px; }.error-state { color:var(--danger); }.error-state button { padding:8px 12px; border:0; border-radius:10px; background:var(--accent-soft); }
</style>
