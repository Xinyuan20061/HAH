<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { cancelInsightExperiment, finishInsightExperiment, readInsights, sendInsightFeedback, startInsightExperiment } from '../services/api'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()
const payload = ref<Record<string, any> | null>(null)
const loading = ref(true)
const error = ref('')
const needsCapability = ref(false)
const busy = ref('')
const activeExperiment = computed(() => payload.value?.active_experiment || null)
const insights = computed(() => Array.isArray(payload.value?.insights) ? payload.value?.insights : [])
const labels: Record<string, string> = { exercise_stall: '运动节奏', sleep_deficit: '恢复提醒', motion_decline: '动作表现', weight_rise: '趋势提醒', record_gap: '记录提醒' }
const actionRoutes: Record<string, string> = { exercise_stall: '/workout', sleep_deficit: '/checkin', motion_decline: '/media', weight_rise: '/trends', record_gap: '/checkin' }

async function load() {
  loading.value = true; error.value = ''; needsCapability.value = false
  try { payload.value = await readInsights(auth.accessToken) }
  catch (cause) {
    const code = (cause as { code?: string })?.code
    needsCapability.value = ['PLUGIN_DISABLED', 'PLUGIN_SCOPE_NOT_GRANTED', 'PLUGIN_UNAVAILABLE'].includes(code || '')
    error.value = needsCapability.value
      ? '开启健康状态能力后，这里会显示基于你记录的提醒。'
      : '健康提醒暂时无法显示，请检查网络后重试。'
  }
  finally { loading.value = false }
}
async function feedback(item: Record<string, any>, verdict: 'helpful' | 'inaccurate' | 'resolved') {
  busy.value = `feedback:${item.code}`; error.value = ''
  try { await sendInsightFeedback(auth.accessToken, item.code, verdict); await load() }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '反馈没有提交' }
  finally { busy.value = '' }
}
async function begin(item: Record<string, any>, variant: 'gentle' | 'standard') {
  const option = item.experiment_proposal?.variants?.find((row: Record<string, any>) => row.key === variant)
  if (!option || !window.confirm(`启动${option.label || '这个'}微实验？\n${option.days} 天内：${option.action}\n结果只表示相关变化，不证明因果。`)) return
  busy.value = 'experiment'; error.value = ''
  try { await startInsightExperiment(auth.accessToken, item.code, variant); await load() }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '微实验没有启动' }
  finally { busy.value = '' }
}
async function closeExperiment(action: 'finish' | 'cancel') {
  const current = activeExperiment.value
  if (!current?.id) return
  if (action === 'cancel' && !window.confirm('停止当前微实验？已经记录的健康数据不会删除。')) return
  busy.value = 'experiment'; error.value = ''
  try {
    if (action === 'finish') {
      const result = await finishInsightExperiment(auth.accessToken, current.id)
      const summary = result.experiment?.outcome?.summary
      window.alert(summary || '复盘已生成。实验结果不证明因果。')
    } else await cancelInsightExperiment(auth.accessToken, current.id)
    await load()
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '微实验操作没有完成' }
  finally { busy.value = '' }
}
function askAssistant(item: Record<string, any>) { void router.push({ path: '/chat', query: { prompt: `结合我的健康记录，针对“${item.title || '这条提醒'}”给出今天能执行的一步，并说明建议依据。` } }) }
onMounted(() => void load())
</script>

<template>
  <section class="page insights-page">
    <header class="page-heading"><button class="back-button" aria-label="返回" @click="router.back()">‹</button><div><p class="eyebrow">近期记录</p><h1>健康提醒</h1></div><button class="refresh" :disabled="loading" @click="load">刷新</button></header>
      <section v-if="payload?.data_quality" class="card-surface intro"><span class="live-dot"/><div><b>值得留意的变化</b><p v-if="payload.data_quality.message || payload.data_quality.label">{{ payload.data_quality.message || payload.data_quality.label }}</p><small>记录覆盖 {{ payload.data_quality.recorded_days ?? 0 }} / {{ payload.data_quality.expected_days ?? 7 }} 天</small></div></section>
    <div v-if="loading" class="card-surface message">正在查看近期记录…</div><div v-else-if="error" class="card-surface error-card"><b>{{ needsCapability ? '开启健康提醒' : '提醒暂时无法显示' }}</b><p>{{ error }}</p><button v-if="needsCapability" @click="router.push('/settings/capabilities')">设置健康能力</button><button v-else @click="load">重试</button></div>
    <template v-else>
      <section v-if="activeExperiment" class="card-surface experiment">
        <div class="card-top"><div><span class="eyebrow">个人微实验</span><h2>{{ activeExperiment.title }}</h2></div><span class="status">{{ activeExperiment.display_status || activeExperiment.status }}</span></div>
        <p>{{ activeExperiment.hypothesis }}</p><div class="experiment-action"><small>今天可以做</small><b>{{ activeExperiment.protocol?.daily_action || activeExperiment.daily_action || '按实验说明持续记录' }}</b></div>
        <div class="progress-label"><span>当前进度</span><span>{{ activeExperiment.days_remaining ?? '—' }} 天剩余</span></div><div class="track"><i :style="{ width: `${Math.min(100, Number(activeExperiment.progress?.progress_pct || 0))}%` }"/></div>
        <div class="button-row"><button v-if="activeExperiment.can_finish" :disabled="!!busy" @click="closeExperiment('finish')">生成复盘</button><button class="stop" :disabled="!!busy" @click="closeExperiment('cancel')">停止实验</button></div><small>结果只描述实验期相关变化，不证明因果。</small>
      </section>
      <section v-if="!insights.length" class="card-surface empty"><b>暂时没有需要特别留意的变化</b><p>继续记录，之后更容易看出趋势。</p><button @click="router.push('/checkin')">记录今天</button></section>
      <div v-else class="insight-list">
        <article v-for="item in insights" :key="item.code" class="card-surface insight-card">
          <div class="card-top"><span class="topic">{{ labels[item.code] || '健康提醒' }}</span><span class="severity">{{ item.severity === 'high' ? '优先关注' : item.severity === 'medium' ? '建议关注' : '温和提醒' }}</span></div>
          <h2>{{ item.title }}</h2><section class="fact"><small>观察到</small><p>{{ item.evidence || item.description || '当前记录不足以形成更多说明。' }}</p></section>
          <section v-if="item.evidence_contract" class="basis"><b>依据与限制</b><div v-for="fact in item.evidence_contract.facts || []" :key="fact.name" class="basis-row"><span>{{ fact.name }}</span><span>{{ fact.value ?? '暂无记录' }}{{ fact.unit || '' }}</span></div><small>记录覆盖 {{ item.evidence_contract.data_coverage?.observed_days ?? 0 }} / {{ item.evidence_contract.data_coverage?.expected_days ?? 7 }} 天</small><p v-for="limit in item.evidence_contract.limitations || []" :key="limit">{{ limit }}</p></section>
          <section class="advice"><small>建议先做</small><p>{{ item.advice || '继续记录，暂时不需要额外干预。' }}</p></section>
          <div class="button-row"><button @click="router.push(actionRoutes[item.code] || '/checkin')">{{ item.code === 'exercise_stall' ? '查看运动建议' : item.code === 'motion_decline' ? '查看动作分析' : '记录今天' }}</button><button class="secondary" @click="askAssistant(item)">问健康助手</button></div>
          <section v-if="item.experiment_proposal && !activeExperiment" class="proposal"><b>{{ item.experiment_proposal.title }}</b><p>{{ item.experiment_proposal.hypothesis }}</p><div class="variant-row"><button v-for="variant in item.experiment_proposal.variants || []" :key="variant.key" :disabled="!!busy" @click="begin(item, variant.key)">{{ variant.label }} · {{ variant.days }} 天</button></div><small>{{ item.experiment_proposal.boundary }}</small></section>
          <section class="feedback"><template v-if="!item.user_feedback?.verdict"><span>这条提醒对你有帮助吗？</span><div><button v-for="choice in [{key:'helpful',label:'有帮助'},{key:'inaccurate',label:'不准确'},{key:'resolved',label:'已处理'}]" :key="choice.key" :disabled="!!busy" @click="feedback(item, choice.key as 'helpful' | 'inaccurate' | 'resolved')">{{ choice.label }}</button></div></template><p v-else>已收到反馈，谢谢你告诉我们。</p></section>
        </article>
      </div>
      <p v-if="error" class="inline-error">{{ error }}</p><section class="boundary">{{ payload?.policy || '提醒只用于日常健康管理参考，不用于诊断。' }}</section>
    </template>
  </section>
</template>

<style scoped>
.insights-page{padding:9px 0 24px}.page-heading{display:flex;align-items:center;gap:9px;margin-bottom:13px}.page-heading>div{flex:1}.page-heading h1{margin:3px 0 0;font-size:22px}.back-button{width:35px;height:35px;border:0;border-radius:50%;background:#fff;font-size:24px}.refresh{padding:7px 10px;border:0;border-radius:11px;background:#e9efd9;color:#506336;font-size:10px}.intro{display:flex;gap:10px;padding:13px}.live-dot{width:8px;height:8px;flex:0 0 8px;margin-top:4px;border-radius:50%;background:#506336}.intro b{font-size:12px}.intro p{margin:5px 0;color:#62685f;font-size:10px;line-height:1.5}.intro small{color:#737a70;font-size:9px}.message,.empty,.error-card{padding:15px;margin:10px 0;font-size:12px}.empty p,.error-card p{color:#737a70;font-size:11px;line-height:1.5}.empty button,.error-card button{padding:8px 11px;border:0;border-radius:10px;background:#c4e267;color:#26331e}.insight-list{display:grid;gap:10px;margin-top:10px}.insight-card,.experiment{padding:14px}.card-top{display:flex;justify-content:space-between;align-items:center;gap:8px}.topic,.severity,.status{padding:5px 8px;border-radius:10px;background:#e9efd9;color:#506336;font-size:9px}.severity{background:#f3f1e7;color:#6d5a22}.insight-card h2,.experiment h2{margin:9px 0;font-size:16px}.fact,.advice{padding:10px;border-radius:13px;background:#f7f7f2}.fact small,.advice small{color:#506336;font-size:9px;font-weight:700}.fact p,.advice p{margin:5px 0 0;font-size:11px;line-height:1.55}.basis{margin:10px 0}.basis>b{font-size:10px}.basis-row{display:flex;justify-content:space-between;gap:6px;margin-top:6px;color:#62685f;font-size:10px}.basis>small,.basis>p{display:block;margin:7px 0 0;color:#737a70;font-size:9px}.button-row{display:flex;gap:7px;margin:10px 0}.button-row button,.variant-row button{flex:1;min-height:37px;padding:7px;border:0;border-radius:11px;background:#c4e267;color:#26331e;font-size:10px}.button-row .secondary,.button-row .stop{background:#e9efd9;color:#506336}.proposal{margin-top:12px;padding-top:11px;border-top:1px solid #eeeee6}.proposal>b{font-size:11px}.proposal p,.proposal small,.experiment>p,.experiment>small{color:#62685f;font-size:10px;line-height:1.5}.variant-row{display:flex;gap:7px}.feedback{margin-top:10px;padding-top:9px;border-top:1px solid #eeeee6}.feedback>span,.feedback p{color:#62685f;font-size:10px}.feedback>div{display:flex;gap:6px;margin-top:7px}.feedback button{flex:1;padding:7px 4px;border:0;border-radius:9px;background:#f2f2eb;color:#506336;font-size:9px}.experiment{margin-top:10px}.experiment-action{display:flex;flex-direction:column;gap:5px;padding:10px;border-radius:12px;background:#f7f7f2;font-size:11px}.experiment-action small,.progress-label{color:#737a70;font-size:9px}.progress-label{display:flex;justify-content:space-between;margin:10px 0 5px}.track{height:6px;border-radius:9px;background:#e9efd9}.track i{display:block;height:100%;border-radius:9px;background:#9fb845}.inline-error{color:#8f3028;font-size:10px}.boundary{margin:12px 3px;color:#737a70;font-size:9px;line-height:1.55}
</style>
