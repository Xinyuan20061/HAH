<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { readTodayCheckIn, saveTodayCheckIn, type DailyCheckIn } from '../services/api'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()
const form = reactive<DailyCheckIn>({ water_ml: 0, sleep_hours: 0, weight_kg: 0, steps: 0, mood: 'normal' })
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const saved = ref(false)
let successTimer: number | undefined

const moods = [
  { key: 'great', label: '很好', icon: '😄' },
  { key: 'normal', label: '平稳', icon: '🙂' },
  { key: 'tired', label: '疲惫', icon: '😮‍💨' },
  { key: 'low', label: '低落', icon: '😔' },
]

async function load() {
  loading.value = true
  error.value = ''
  try {
    Object.assign(form, await readTodayCheckIn(auth.accessToken))
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '今日状态暂时无法读取'
  } finally {
    loading.value = false
  }
}

function addWater(amount: number) {
  form.water_ml = Math.min(10000, Math.max(0, Number(form.water_ml) || 0) + amount)
}

async function save() {
  if (saving.value) return
  error.value = ''
  saved.value = false
  const values: DailyCheckIn = {
    water_ml: Math.round(Number(form.water_ml) || 0),
    sleep_hours: Number(form.sleep_hours) || 0,
    weight_kg: Number(form.weight_kg) || 0,
    steps: Math.round(Number(form.steps) || 0),
    mood: form.mood || 'normal',
  }
  if (values.water_ml < 0 || values.water_ml > 10000 || values.sleep_hours < 0 || values.sleep_hours > 24
    || values.weight_kg < 0 || values.weight_kg > 400 || values.steps < 0 || values.steps > 200000) {
    error.value = '请检查数值范围：饮水 0–10000 ml、睡眠 0–24 小时、体重 0–400 kg、步数 0–200000。'
    return
  }
  saving.value = true
  try {
    await saveTodayCheckIn(auth.accessToken, values)
    Object.assign(form, values)
    saved.value = true
    successTimer = window.setTimeout(() => {
      if (window.history.length > 1) void router.back()
      else void router.replace('/home')
    }, 500)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '今日状态没有保存，请重试'
  } finally {
    saving.value = false
  }
}

onMounted(() => void load())
onBeforeUnmount(() => window.clearTimeout(successTimer))
</script>

<template>
  <section class="page checkin-page">
    <header class="checkin-heading">
      <button class="back-button" aria-label="返回" @click="router.back()">‹</button>
      <div><p class="eyebrow">每日打卡</p><h1>记录今天的状态</h1></div>
    </header>

    <div v-if="loading" class="state-card card-surface"><span class="spinner" /><div><b>正在读取今日记录</b><p>已保存的数据会先显示在这里</p></div></div>
    <div v-else-if="error && !saved" class="load-error card-surface"><b>今日状态暂时无法显示</b><p>{{ error }}</p><button class="button-soft" @click="load">重新加载</button></div>
    <template v-else>
      <section class="checkin-card card-surface">
        <div class="field-heading"><label for="water-input">饮水量</label><b>{{ Number(form.water_ml) || 0 }} ml</b></div>
        <div class="water-actions"><button type="button" @click="addWater(250)">+250 ml</button><button type="button" @click="addWater(500)">+500 ml</button></div>
        <input id="water-input" v-model.number="form.water_ml" type="number" min="0" max="10000" inputmode="numeric" placeholder="今日总饮水量" />
      </section>

      <section class="checkin-card card-surface">
        <div class="metric-grid">
          <label><span>睡眠时长</span><span class="unit-input"><input v-model.number="form.sleep_hours" type="number" min="0" max="24" step="0.1" inputmode="decimal" placeholder="0" /><small>小时</small></span></label>
          <label><span>体重</span><span class="unit-input"><input v-model.number="form.weight_kg" type="number" min="0" max="400" step="0.1" inputmode="decimal" placeholder="0" /><small>kg</small></span></label>
        </div>
        <label class="steps-field"><span>今日步数</span><input v-model.number="form.steps" type="number" min="0" max="200000" step="1" inputmode="numeric" placeholder="输入今日步数" /></label>
      </section>

      <section class="mood-section">
        <h2>今天感觉怎么样？</h2>
        <div class="mood-grid">
          <button v-for="mood in moods" :key="mood.key" type="button" class="mood-choice" :class="{ selected: form.mood === mood.key }" :aria-pressed="form.mood === mood.key" @click="form.mood = mood.key">
            <span>{{ mood.icon }}</span><b>{{ mood.label }}</b>
          </button>
        </div>
      </section>

      <p v-if="error" class="form-error">{{ error }}</p>
      <p v-if="saved" class="saved-note">今日状态已保存，正在返回…</p>
      <button class="save-button" :disabled="saving" @click="save">{{ saving ? '正在保存…' : '保存今日状态' }}</button>
      <p class="privacy-note">只保存你输入的记录；没有填写的数据不会被推测。</p>
    </template>
  </section>
</template>

<style scoped>
.checkin-page { padding:12px 0 20px; }.checkin-heading { display:flex; align-items:center; gap:11px; margin:0 0 17px; }.checkin-heading h1 { margin:3px 0 0; font-size:23px; }.back-button { display:flex; align-items:center; justify-content:center; width:38px; height:38px; flex:0 0 38px; padding:0; border:0; border-radius:50%; background:#e9efd9; font-size:29px; }
.checkin-card { margin-bottom:11px; padding:15px; }.field-heading { display:flex; justify-content:space-between; align-items:center; color:#5f665f; font-size:12px; }.field-heading b { color:#111613; font-size:13px; }.water-actions { display:flex; gap:8px; margin:12px 0 10px; }.water-actions button { min-height:33px; padding:0 12px; border:0; border-radius:18px; color:#506336; background:#e9efd9; font-size:11px; }.checkin-card input { width:100%; min-width:0; height:42px; padding:0 12px; border:1px solid #eeeee6; border-radius:13px; outline:none; color:#111613; background:#f7f7f2; font:inherit; font-size:13px; }.checkin-card input:focus { border-color:#a8c85b; }
.metric-grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }.metric-grid label,.steps-field { display:flex; flex-direction:column; gap:7px; color:#5f665f; font-size:11px; }.unit-input { position:relative; display:flex; align-items:center; }.unit-input input { padding-right:43px; }.unit-input small { position:absolute; right:11px; color:#506336; font-size:10px; }.steps-field { margin-top:15px; }
.mood-section h2 { margin:16px 2px 9px; font-size:14px; }.mood-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:7px; }.mood-choice { display:flex; min-width:0; flex-direction:column; align-items:center; gap:7px; padding:11px 4px; border:1px solid transparent; border-radius:16px; color:#5f665f; background:#fff; box-shadow:var(--card-shadow); }.mood-choice span { font-size:21px; }.mood-choice b { font-size:10px; font-weight:500; }.mood-choice.selected { border-color:#c4e267; color:#111613; background:#e9efd9; }.mood-choice.selected b { font-weight:700; }
.form-error,.load-error { color:#8f3028; font-size:12px; line-height:1.55; }.form-error { margin:10px 2px; }.saved-note { margin:11px 2px; color:#506336; font-size:11px; text-align:center; }.save-button { width:100%; min-height:47px; margin-top:16px; border:0; border-radius:15px; color:#111613; background:#c4e267; font-size:13px; font-weight:700; }.save-button:disabled { opacity:.55; }.privacy-note { margin:8px 4px 0; color:#7b8078; font-size:10px; text-align:center; }.load-error { padding:16px; }.load-error p { margin:6px 0 10px; }.load-error .button-soft { min-height:37px; padding:0 12px; border:0; border-radius:12px; color:#111613; background:#e9efd9; font-size:11px; }.state-card { display:flex; align-items:center; gap:13px; padding:16px; }.state-card b { font-size:13px; }.state-card p { margin:4px 0 0; color:#5f665f; font-size:11px; }.spinner { width:21px; height:21px; flex:0 0 21px; border:3px solid #e9efd9; border-top-color:#506336; border-radius:50%; animation:spin .8s linear infinite; }
@keyframes spin { to { transform:rotate(360deg); } }
</style>
