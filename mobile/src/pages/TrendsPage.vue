<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { readHealthTrends } from '../services/api'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()
const loading = ref(true)
const error = ref('')
const days = ref<Array<Record<string, any>>>([])
const selectedKey = ref('water_ml')
const metrics = [
  { key: 'water_ml', title: '饮水', unit: 'ml', decimals: 0, color: '#8da961' },
  { key: 'sleep_hours', title: '睡眠', unit: '小时', decimals: 1, color: '#7185a8' },
  { key: 'exercise_min', title: '运动', unit: '分钟', decimals: 0, color: '#c17b4c' },
  { key: 'steps', title: '步数', unit: '步', decimals: 0, color: '#82983e' },
]
const selectedMetric = computed(() => metrics.find(metric => metric.key === selectedKey.value) || metrics[0])
const values = computed(() => days.value.map(day => {
  const raw = day[selectedMetric.value.key]
  return raw === null || raw === undefined || raw === '' ? null : Number(raw)
}))
const maximum = computed(() => Math.max(1, ...values.value.filter((value): value is number => value !== null)))
const average = computed(() => {
  const observed = values.value.filter((value): value is number => value !== null && Number.isFinite(value))
  if (!observed.length) return '暂无记录'
  const avg = observed.reduce((sum, value) => sum + value, 0) / observed.length
  return `${avg.toFixed(selectedMetric.value.decimals)} ${selectedMetric.value.unit}`
})
const columns = computed(() => days.value.map((day, index) => {
  const value = values.value[index]
  return {
    key: String(day.date || index),
    label: String(day.label || day.date || `第 ${index + 1} 天`),
    value,
    text: value === null || !Number.isFinite(value) ? '未记录' : `${value.toFixed(selectedMetric.value.decimals)} ${selectedMetric.value.unit}`,
    height: value === null || !Number.isFinite(value) ? 3 : Math.max(6, value / maximum.value * 100),
  }
}))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const response = await readHealthTrends(auth.accessToken)
    days.value = response.days || []
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '趋势数据暂时无法加载'
  } finally {
    loading.value = false
  }
}

onMounted(() => void load())
</script>

<template>
  <section class="page trends-page">
    <header class="trends-heading">
      <button class="back-button" aria-label="返回" @click="router.back()">‹</button>
      <div><p class="eyebrow">最近 7 天</p><h1>七日趋势</h1></div>
    </header>

    <div class="metric-tabs" role="tablist" aria-label="选择健康指标">
      <button v-for="metric in metrics" :key="metric.key" :class="{ selected: selectedKey === metric.key }" @click="selectedKey = metric.key">
        {{ metric.title }}
      </button>
    </div>

    <div v-if="loading" class="card-surface state-card">正在加载近期记录…</div>
    <div v-else-if="error" class="card-surface state-card error-state"><p>{{ error }}</p><button class="retry-button" @click="load">重试</button></div>
    <template v-else>
      <section class="card-surface trend-summary">
        <p class="eyebrow">7 日平均</p>
        <strong>{{ average }}</strong>
        <span>仅统计已有记录；未记录的日期不会按 0 计算。</span>
      </section>
      <section class="card-surface trend-chart" :aria-label="`${selectedMetric.title}七日数据`">
        <div v-for="column in columns" :key="column.key" class="trend-column">
          <span class="trend-value">{{ column.text }}</span>
          <div class="trend-track"><div class="trend-bar" :style="{ height: `${column.height}%`, background: selectedMetric.color }" /></div>
          <span class="trend-day">{{ column.label }}</span>
        </div>
      </section>
      <p v-if="!columns.some(column => column.value !== null)" class="empty-note">还没有可展示的{{ selectedMetric.title }}记录。完成每日打卡后，趋势会显示在这里。</p>
    </template>
  </section>
</template>

<style scoped>
.trends-heading { display:flex; align-items:center; gap:10px; padding:8px 0 16px; }
.trends-heading h1 { margin:3px 0 0; font-size:21px; }
.back-button { width:34px; height:34px; border:0; border-radius:50%; background:#fff; font-size:24px; }
.metric-tabs { display:grid; grid-template-columns:repeat(4,1fr); gap:6px; margin-bottom:12px; }
.metric-tabs button { min-width:0; padding:9px 2px; border:0; border-radius:12px; color:var(--muted); background:#fff; font-size:11px; }
.metric-tabs button.selected { color:var(--ink); background:var(--accent); font-weight:750; }
.state-card { padding:20px; color:var(--muted); font-size:12px; }
.error-state p { margin:0 0 10px; color:var(--danger); }
.retry-button { padding:8px 14px; border:0; border-radius:12px; background:var(--accent-soft); }
.trend-summary { display:flex; flex-direction:column; gap:6px; padding:18px; }
.trend-summary strong { font-size:26px; }
.trend-summary span,.empty-note { color:var(--muted); font-size:11px; line-height:1.6; }
.trend-chart { display:grid; grid-template-columns:repeat(7,minmax(0,1fr)); gap:7px; min-height:210px; margin-top:12px; padding:14px 10px 12px; }
.trend-column { display:flex; flex-direction:column; align-items:center; justify-content:flex-end; gap:8px; min-width:0; }
.trend-value { height:25px; color:var(--muted); font-size:8px; text-align:center; overflow-wrap:anywhere; }
.trend-track { display:flex; width:100%; height:130px; align-items:flex-end; justify-content:center; border-bottom:1px solid var(--divider); background:linear-gradient(to top,rgba(17,22,19,.025),transparent); }
.trend-bar { width:min(22px,70%); min-height:3px; border-radius:8px 8px 2px 2px; }
.trend-day { color:var(--muted); font-size:9px; text-align:center; }
.empty-note { margin:14px 4px; }
</style>
