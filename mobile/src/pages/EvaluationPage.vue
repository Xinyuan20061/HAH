<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { readAgentStatistics, readEvaluationDashboard } from '../services/api'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()
const dashboard = ref<Record<string, any> | null>(null)
const agent = ref<Record<string, any> | null>(null)
const loading = ref(true)
const error = ref('')
const metricLabels: Record<string, string> = {
  food_analysis_success: '识餐成功率', food_correction_rate: '用户校正率', food_finalize_rate: '识餐保存率',
  food_latency_p50: '识餐响应中位数', agent_latency_p50: '健康助手响应中位数', agent_latency_p95: '较慢回复耗时',
  weekly_latency_p50: '周报生成中位数', motion_success: '视频分析完成率', motion_latency_p50: '视频分析用时中位数',
  pose_valid_rate: '动作画面可识别率', plan_completion: '计划完成率', action_execution: '确认后完成的行动',
  action_blocked: '安全保护触发次数', safety_intercepts: '风险提示次数', experiments_started: '个人尝试次数',
  experiments_active: '正在进行的尝试', experiments_completed: '完成的尝试', experiments_cancelled: '主动停止的尝试',
  experiments_completion_rate: '尝试完成率',
}
async function load() { loading.value = true; error.value = ''; const results = await Promise.allSettled([readEvaluationDashboard(auth.accessToken), readAgentStatistics(auth.accessToken)]); if (results[0].status === 'fulfilled') dashboard.value = results[0].value; else error.value = '运行数据暂时无法读取，请稍后重试。'; agent.value = results[1].status === 'fulfilled' ? results[1].value : null; loading.value = false }
function metricValue(item: Record<string, any>) { const unit = item.unit === 'ms' ? ' 毫秒' : item.unit || ''; return item.value === null || item.value === undefined ? '暂无样本' : `${item.value}${unit}` }
onMounted(() => void load())
</script>

<template>
  <section class="page evaluation-page"><header class="page-heading"><button class="back-button" aria-label="返回" @click="router.back()">‹</button><div><p class="eyebrow">服务表现</p><h1>运行与评测</h1></div><button class="refresh" @click="load">刷新</button></header><div v-if="loading" class="card-surface message">正在汇总运行数据…</div><div v-else-if="error && !dashboard" class="card-surface message">{{ error }} <button @click="load">重试</button></div><template v-else>
    <h2 class="section-title">近 30 日运行指标</h2><div class="metric-grid"><article v-for="item in dashboard?.runtime_metrics || []" :key="item.key" class="card-surface metric"><span>{{ metricLabels[item.key] || item.label || item.key }}</span><b>{{ metricValue(item) }}</b><small>样本 {{ item.sample_size ?? 0 }} 条</small></article></div>
    <section v-if="agent" class="card-surface agent-card"><h2>健康助手运行情况</h2><div class="agent-stats"><div><b>{{ agent.total_runs ?? agent.totalRuns ?? 0 }}</b><small>建议次数</small></div><div><b>{{ agent.p50_latency_ms ?? agent.p50_ms ?? '—' }}</b><small>响应中位数（毫秒）</small></div><div><b>{{ agent.fallback_rate ?? '—' }}</b><small>备用服务使用率</small></div></div><p>{{ agent.note || '数据反映近期服务运行情况。' }}</p></section>
    <h2 class="section-title">评测结果</h2><div v-if="!(dashboard?.benchmarks || []).length" class="card-surface message">暂无可查看的评测结果。</div><article v-for="item in dashboard?.benchmarks || []" :key="item.id" class="card-surface benchmark"><div><b>{{ item.metric_name }}</b><strong>{{ item.value }}{{ item.unit }}</strong></div><p>{{ item.task_type }} · {{ item.sample_size }} 条样本 · {{ item.evidence_level || '证据等级未标注' }}</p><small>数据集 {{ item.dataset || '未标注' }} · 版本 {{ item.retriever_version || '未标注' }} · {{ (item.measured_at || '').slice(0,10) }}</small><small v-if="item.notes">{{ item.notes }}</small></article>
    <section class="card-surface notes"><b>评测说明</b><p v-for="note in dashboard?.notes || []" :key="note">{{ note }}</p></section><p class="disclaimer">评测结果反映已收集的样本，可用于了解服务表现。</p></template></section>
</template>

<style scoped>
.evaluation-page{padding:9px 0 24px}.page-heading{display:flex;align-items:center;gap:9px;margin-bottom:13px}.page-heading>div{flex:1}.page-heading h1{margin:3px 0 0;font-size:22px}.back-button{width:35px;height:35px;border:0;border-radius:50%;background:#fff;font-size:24px}.refresh{padding:7px 10px;border:0;border-radius:11px;background:#e9efd9;color:#506336;font-size:10px}.section-title{margin:14px 1px 8px;font-size:13px}.message{padding:14px;color:#737a70;font-size:11px;line-height:1.5}.message button{padding:6px 9px;border:0;border-radius:8px;background:#e9efd9}.metric-grid{display:grid;grid-template-columns:1fr 1fr;gap:7px}.metric{display:flex;flex-direction:column;gap:6px;padding:11px}.metric span,.metric small,.benchmark p,.benchmark small,.agent-card p,.disclaimer{color:#737a70;font-size:9px;line-height:1.5}.metric b{font-size:14px}.agent-card,.notes{margin-top:12px;padding:13px}.agent-card h2{margin:0 0 10px;font-size:13px}.agent-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.agent-stats>div{display:flex;flex-direction:column;gap:4px;padding:9px;border-radius:11px;background:#f7f7f2}.agent-stats b{font-size:13px}.agent-stats small{color:#737a70;font-size:9px}.benchmark{margin-bottom:7px;padding:12px}.benchmark>div{display:flex;justify-content:space-between;gap:8px}.benchmark b,.benchmark strong{font-size:11px}.benchmark p{margin:6px 0}.benchmark small{display:block;margin-top:4px}.notes b{font-size:10px}.notes p{color:#62685f;font-size:10px;line-height:1.5}.disclaimer{text-align:center}
</style>
