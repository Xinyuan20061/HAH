<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { readActionOutcomes, readHealthState, readNextAction } from '../services/api'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()
const state = ref<Record<string, any> | null>(null)
const decision = ref<Record<string, any> | null>(null)
const outcomes = ref<Record<string, any> | null>(null)
const visibleConstraints = computed(() => (state.value?.constraints || []).filter(
  (item: Record<string, any>) => item.key !== 'motion_trend_insufficient',
))
const loading = ref(true)
const errors = ref<Record<string, string>>({})
const decisionNeedsSetup = ref(false)
const labels: Record<string, string> = {
  sleep_debt_7d: '近 7 日睡眠差额',
  sleep_hours_last: '最近一次睡眠',
  weight_kg_latest: '最近记录体重',
  diet_calories_avg: '平均饮食热量',
  exercise_days: '运动天数',
  exercise_gap_days: '距上次运动',
  diet_record_coverage_7d: '饮食记录覆盖',
  plan_adherence_7d: '计划完成情况',
  data_reliability_score: '记录覆盖度',
  load_recovery_ratio: '运动与睡眠比',
  motion_quality_trend: '动作表现趋势',
}
const evidenceLabels: Record<string, string> = {
  observed: '来自你的记录',
  derived: '根据记录计算',
  user_confirmed: '你已确认',
  model_inferred: '状态分析',
}
const unitLabels: Record<string, string> = {
  kg: 'kg', kcal: '千卡', hour: '小时', hours: '小时', h: '小时',
  day: '天', days: '天', count: '次', 'min/h': '分/小时',
}
async function load() {
  loading.value = true; errors.value = {}
  decisionNeedsSetup.value = false
  const results = await Promise.allSettled([readHealthState(auth.accessToken), readNextAction(auth.accessToken), readActionOutcomes(auth.accessToken)])
  if (results[0].status === 'fulfilled') state.value = results[0].value
  else errors.value.state = '当前状态暂时无法显示，请检查网络后重试。'
  if (results[1].status === 'fulfilled') decision.value = results[1].value
  else {
    const code = (results[1].reason as { code?: string })?.code
    decisionNeedsSetup.value = ['PLUGIN_DISABLED', 'PLUGIN_SCOPE_NOT_GRANTED', 'PLUGIN_UNAVAILABLE'].includes(code || '')
    errors.value.decision = decisionNeedsSetup.value
      ? '开启健康状态能力后，这里会根据你的记录提供建议。'
      : '建议暂时无法显示，请检查网络后重试。'
  }
  if (results[2].status === 'fulfilled') outcomes.value = results[2].value
  else errors.value.outcomes = '行动结果暂时无法显示，请稍后重试。'
  loading.value = false
}
function display(entry: Record<string, any>) {
  const value = entry?.value
  if (value === null || value === undefined) return '数据不足'
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '数据不足'
  if (entry.unit === 'ratio') return `${Math.round(numeric * 100)}%`
  const formatted = Number.isInteger(numeric) ? String(numeric) : numeric.toFixed(1)
  const unit = unitLabels[entry.unit] || ''
  return unit ? `${formatted} ${unit}` : formatted
}
function sourceLabel(entry: Record<string, any>) {
  return evidenceLabels[entry.evidence_type] || '根据已有资料'
}
function limitationText(value: unknown) {
  if (typeof value !== 'string') return ''
  if (/覆盖情况\s*[:：]\s*\{/.test(value)) return '记录覆盖情况见下方统计。'
  if (value.includes('没有任何运动记录')) return '还没有运动记录，记录后可查看运动间隔。'
  if (value.includes('没有睡眠记录')) return '还没有睡眠记录，暂时无法计算。'
  if (value.includes('没有睡眠数据')) return '最近一次打卡未记录睡眠。'
  if (value.includes('没有体重记录')) return '近 7 天没有体重记录。'
  if (value.includes('没有计划任务')) return '还没有本周计划，添加后可查看完成情况。'
  if (value.includes('同一动作 + 同一模型版本')) return '相同动作的记录还不够，暂时无法查看变化。'
  if (value.includes('仅比较同动作')) return '只比较同一动作的记录。'
  if (value.includes('仅用于保守排序')) return '仅供日常观察参考，不用于判断疲劳或损伤。'
  if (value.includes('运动或睡眠任一侧缺失')) return '补充运动和睡眠记录后即可查看。'
  if (value.includes('睡眠均值为 0')) return '睡眠记录不足，暂时无法计算。'
  if (/缺失日|不按 0|不假设为 0/.test(value)) return '按有记录的日期计算，记录不足时暂不显示。'
  return value
}
function constraintText(item: Record<string, any>) {
  const copy: Record<string, string> = {
    insufficient_record_coverage: '记录还不完整，暂时不调整计划。',
    motion_trend_insufficient: '相同动作的记录还不够，暂时无法判断变化。',
    profile_incomplete: '完成健康档案后，建议会更贴合你的情况。',
  }
  return copy[item.key] || item.description || '记录不足，暂时无法判断。'
}
function actionLabel(item: Record<string, any>) {
  const key = String(item.action_key || item.action || '')
  const known: Record<string, string> = {
    'plan.apply': '加入健康计划',
    'goal.adjust': '调整健康目标',
    'experiment.start': '开始个人尝试',
  }
  return known[key] || (key.includes('.') ? '已记录的行动' : key || '行动记录')
}
function conclusionLabel(value: string) {
  const labels: Record<string, string> = {
    insufficient_data: '记录不足，暂时无法判断',
    changed: '观察到变化',
    unchanged: '暂未观察到变化',
  }
  return labels[value] || '等待更多记录'
}
onMounted(() => void load())
</script>

<template>
  <section class="page state-page"><header class="page-heading"><button class="back-button" aria-label="返回" @click="router.back()">‹</button><div><p class="eyebrow">根据你的记录</p><h1>状态与下一步</h1></div><button class="refresh" @click="load">刷新</button></header><div v-if="loading" class="card-surface message">正在整理状态和历史记录…</div>
    <template v-else>
      <section class="card-surface next-action"><p class="eyebrow">下一步</p><template v-if="decision?.next_best_action"><h2>{{ decision.next_best_action.title }}</h2><p>{{ decision.next_best_action.reason }}</p><small v-for="(evidence,index) in decision.next_best_action.evidence || []" :key="index">{{ typeof evidence === 'string' ? evidence : evidence.description || evidence.label || '根据近期记录整理' }}</small></template><template v-else><p>{{ errors.decision || '目前没有新的建议。' }}</p><button v-if="decisionNeedsSetup" class="setup-button" @click="router.push('/settings/capabilities')">设置健康能力</button></template><div v-if="decision?.alternatives?.length" class="alternatives"><b>其他方向</b><p v-for="item in decision.alternatives" :key="item.id">{{ item.title }} · {{ item.reason }}</p></div></section>
      <section class="section"><h2>当前状态</h2><p v-if="errors.state" class="error-text">{{ errors.state }}</p><div v-else class="state-list"><article v-for="(entry,key) in state?.values || {}" :key="key" class="card-surface state-row"><div><b>{{ labels[key] || state?.titles?.[key] || '健康状态' }}</b><small>{{ sourceLabel(entry) }}<template v-if="Number(entry.observed_days) > 0"> · 有记录 {{ entry.observed_days }} 天</template></small><small v-for="limit in entry.limitations || []" :key="limit">{{ limitationText(limit) }}</small></div><strong>{{ display(entry) }}</strong></article></div><div v-if="visibleConstraints.length" class="card-surface constraints"><b>目前的限制</b><p v-for="constraint in visibleConstraints" :key="constraint.key">{{ constraintText(constraint) }}</p></div></section>
      <section class="section"><h2>行动记录</h2><p v-if="errors.outcomes" class="error-text">{{ errors.outcomes }}</p><div v-else-if="!(outcomes?.items || []).length" class="card-surface message">完成计划或记录一次尝试后，这里会显示观察结果。</div><article v-for="item in outcomes?.items || []" :key="item.id || item.observed_at" class="card-surface outcome"><b>{{ actionLabel(item) }}</b><span>{{ conclusionLabel(item.conclusion) }}</span><small>{{ item.result || item.note || '' }} {{ (item.observed_at || '').slice(0,10) }}</small></article></section>
      <button class="link-button" @click="router.push('/insights')">查看健康提醒</button>
    </template>
  </section>
</template>

<style scoped>
.state-page{padding:9px 0 24px}.page-heading{display:flex;align-items:center;gap:9px;margin-bottom:13px}.page-heading>div{flex:1}.page-heading h1{margin:3px 0 0;font-size:22px}.back-button{width:35px;height:35px;border:0;border-radius:50%;background:#fff;font-size:24px}.refresh{padding:7px 10px;border:0;border-radius:11px;background:#e9efd9;color:#506336;font-size:10px}.message{padding:14px;color:#737a70;font-size:11px}.next-action{padding:15px;color:#f7f7f2;background:#26331e}.next-action .eyebrow{color:#c4e267}.next-action h2{margin:6px 0;font-size:17px}.next-action p,.next-action small{color:#dce5cb;font-size:10px;line-height:1.5}.next-action>small{display:block}.alternatives{margin:10px 0;padding:9px;border-radius:12px;background:rgba(255,255,255,.08)}.alternatives b{font-size:10px}.section{margin-top:15px}.section>h2{margin:0 0 8px;font-size:14px}.state-list{display:grid;gap:7px}.state-row{display:flex;justify-content:space-between;gap:8px;padding:11px}.state-row>div{display:flex;flex-direction:column;gap:4px}.state-row b{font-size:11px}.state-row small,.outcome small{color:#737a70;font-size:9px;line-height:1.45}.state-row strong{font-size:11px;white-space:nowrap}.constraints{margin-top:8px;padding:11px}.constraints b{font-size:10px}.constraints p{margin:6px 0;color:#7c4d27;font-size:10px;line-height:1.5}.outcome{display:flex;flex-direction:column;gap:5px;margin-bottom:7px;padding:11px}.outcome b,.outcome span{font-size:10px}.error-text{color:#8f3028;font-size:10px}.link-button{width:100%;height:40px;margin-top:8px;border:0;border-radius:12px;background:#c4e267;font-size:11px}
.setup-button{margin-top:8px;padding:8px 11px;border:0;border-radius:10px;background:#c4e267;color:#26331e;font-size:10px;font-weight:700}
</style>
