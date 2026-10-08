<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { createWeeklySummary, readWeeklyFacts } from '../services/api'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()
const facts = ref<Record<string, any> | null>(null)
const summary = ref<Record<string, any> | null>(null)
const loading = ref(true)
const summarizing = ref(false)
const error = ref('')
const confidenceLabel = computed(() => ({ high: '记录较完整', medium: '记录一般', low: '记录较少' } as Record<string, string>)[facts.value?.data_quality?.confidence] || '记录情况待确认')
const metricRows = computed(() => {
  const values = facts.value?.averages || {}
  return [
    { key: 'water_ml', label: '平均饮水', unit: 'ml', digits: 0 },
    { key: 'sleep_hours', label: '平均睡眠', unit: '小时', digits: 1 },
    { key: 'steps', label: '平均步数', unit: '步', digits: 0 },
    { key: 'calories', label: '平均摄入', unit: 'kcal', digits: 0 },
    { key: 'protein', label: '平均蛋白质', unit: 'g', digits: 1 },
    { key: 'exercise_min', label: '平均运动', unit: '分钟', digits: 1 },
  ].map(row => ({ ...row, value: values[row.key] }))
})
async function load() { loading.value = true; error.value = ''; try { facts.value = await readWeeklyFacts(auth.accessToken) } catch (cause) { error.value = cause instanceof Error ? cause.message : '每周报告暂时无法读取' } finally { loading.value = false } }
async function generateSummary() { if (summarizing.value) return; summarizing.value = true; error.value = ''; try { summary.value = await createWeeklySummary(auth.accessToken) } catch (cause) { error.value = cause instanceof Error ? cause.message : '总结暂时无法生成' } finally { summarizing.value = false } }
onMounted(() => void load())
</script>

<template>
  <section class="page report-page">
    <header class="page-heading"><button class="back-button" aria-label="返回" @click="router.back()">‹</button><div><p class="eyebrow">每周回顾</p><h1>每周报告</h1></div><button class="refresh" :disabled="loading" @click="load">刷新</button></header>
    <div v-if="loading" class="card-surface message">正在汇总本周已经记录的事实…</div><div v-else-if="error && !facts" class="card-surface message"><b>报告暂时无法显示</b><p>{{ error }}</p><button @click="load">重试</button></div>
    <template v-else-if="facts">
      <section class="card-surface report-hero"><p class="eyebrow">{{ facts.period?.label || '近 7 天' }}</p><h2>{{ facts.score === null ? '继续积累记录' : `本周完成度 ${facts.score}%` }}</h2><p>{{ confidenceLabel }}</p></section>
      <section class="card-surface coverage"><h2>记录覆盖</h2><div class="coverage-grid"><div><b>{{ facts.coverage?.checkin_days ?? 0 }} / 7</b><small>健康打卡日</small></div><div><b>{{ facts.coverage?.diet_days ?? 0 }} / 7</b><small>饮食记录日</small></div><div><b>{{ facts.coverage?.exercise_days ?? 0 }} / 7</b><small>运动记录日</small></div></div></section>
      <section class="card-surface metrics"><h2>有记录日期的平均值</h2><div class="metric-grid"><div v-for="row in metricRows" :key="row.key" class="metric"><span>{{ row.label }}</span><b>{{ row.value === null || row.value === undefined ? '暂无记录' : `${Number(row.value).toFixed(row.digits)} ${row.unit}` }}</b></div></div><small>平均值仅按有记录的日期计算。</small></section>
      <section class="card-surface highlights"><h2>本周观察</h2><p v-for="(line, index) in facts.highlights || []" :key="index">{{ line }}</p></section>
      <section class="card-surface ai-summary"><div class="summary-heading"><div><p class="eyebrow">本周回顾</p><h2>一周小结</h2></div><button :disabled="summarizing" @click="generateSummary">{{ summarizing ? '生成中…' : '生成总结' }}</button></div>
        <template v-if="summary"><h3>{{ summary.title }}</h3><p>{{ summary.summary }}</p><div v-if="summary.wins?.length" class="wins"><b>值得肯定</b><p v-for="win in summary.wins" :key="win">{{ win }}</p></div><p><b>下周关注：</b>{{ summary.focus }}</p><p><b>可以尝试：</b>{{ summary.action }}</p><small>{{ summary.caution }}<template v-if="summary.degraded"> · 当前使用基础总结</template></small></template>
        <p v-else class="summary-hint">总结根据本页的记录生成，供日常参考。</p>
      </section>
      <p v-if="error" class="inline-error">{{ error }}</p><p class="disclaimer">这份报告用于日常健康管理，不提供医疗诊断。{{ facts.policy || '' }}</p>
    </template>
  </section>
</template>

<style scoped>
.report-page{padding:9px 0 24px}.page-heading{display:flex;align-items:center;gap:9px;margin-bottom:13px}.page-heading>div{flex:1}.page-heading h1{margin:3px 0 0;font-size:22px}.back-button{width:35px;height:35px;border:0;border-radius:50%;background:#fff;font-size:24px}.refresh{padding:7px 10px;border:0;border-radius:11px;background:#e9efd9;color:#506336;font-size:10px}.message,.report-hero,.coverage,.metrics,.highlights,.ai-summary{margin-bottom:9px;padding:14px}.message{color:#62685f;font-size:11px;line-height:1.5}.message button{padding:7px 10px;border:0;border-radius:9px;background:#e9efd9;color:#506336}.report-hero{color:#f7f7f2;background:#26331e}.report-hero .eyebrow{color:#c4e267}.report-hero h2{margin:7px 0;font-size:20px}.report-hero p,.report-hero small{margin:4px 0;color:#dce5cb;font-size:10px;line-height:1.5}.coverage h2,.metrics h2,.highlights h2,.ai-summary h2{margin:0 0 11px;font-size:14px}.coverage-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}.coverage-grid>div{display:flex;flex-direction:column;gap:4px;padding:10px 7px;border-radius:12px;background:#f7f7f2}.coverage-grid b{font-size:12px}.coverage-grid small,.metric span{color:#737a70;font-size:9px}.metric-grid{display:grid;grid-template-columns:1fr 1fr;gap:7px}.metric{display:flex;flex-direction:column;gap:5px;padding:9px;border-radius:11px;background:#f7f7f2}.metric b{font-size:11px}.metrics>small{display:block;margin-top:9px;color:#737a70;font-size:9px}.highlights p,.ai-summary p{margin:6px 0;color:#62685f;font-size:11px;line-height:1.5}.summary-heading{display:flex;align-items:center;justify-content:space-between}.summary-heading h2{margin:3px 0}.summary-heading button{padding:8px 10px;border:0;border-radius:11px;background:#c4e267;font-size:10px}.ai-summary h3{font-size:14px}.ai-summary small,.summary-hint{color:#737a70;font-size:9px;line-height:1.5}.wins{padding:8px;border-radius:11px;background:#f7f7f2}.wins b{font-size:10px}.inline-error{color:#8f3028;font-size:10px}.disclaimer{color:#737a70;font-size:9px;line-height:1.5;text-align:center}
</style>
