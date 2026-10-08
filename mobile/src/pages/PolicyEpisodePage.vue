<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { confirmUserAction, createPolicyStopProposal, readPolicyEpisode, readPolicyUnit, reportPolicyExecution, savePolicyObservations } from '../services/api'
import { useAuthStore } from '../stores/auth'

const route = useRoute(); const router = useRouter(); const auth = useAuthStore()
const episodeId = computed(() => typeof route.query.id === 'string' ? route.query.id : '')
const episode = ref<Record<string, any> | null>(null); const unit = ref<Record<string, any> | null>(null)
const loading = ref(true); const busy = ref(''); const error = ref(''); const emptyMessage = ref('')
const observation = reactive({ endpoint: 'baseline' as 'baseline' | 'followup', slot: 0, date: new Date().toISOString().slice(0,10), value: '' })
const reportsByOpportunity = computed(() => new Map((episode.value?.reports || []).map((row: Record<string, any>) => [row.opportunity_id, row])))
const opportunities = computed(() => (episode.value?.opportunities || []).map((row: Record<string, any>) => ({ ...row, report: reportsByOpportunity.value.get(row.id), due: new Date(row.scheduled_at).getTime() <= Date.now() })))
const observations = computed(() => episode.value?.observations || [])
const expectedDays = computed(() => Math.max(1, Number(unit.value?.protocol?.template?.expected_days || 7)))
const metricVersion = computed(() => unit.value?.protocol?.template?.metric_version || 'burden-v1')
const canStop = computed(() => (episode.value?.allowed_actions || []).includes('stop_proposal'))
const canReview = computed(() => (episode.value?.allowed_actions || []).includes('review_preview'))
const statusLabels: Record<string, string> = { active: '进行中', awaiting_review: '等待复查', completed: '已完成', stopped: '已停止' }
const statusLabel = computed(() => statusLabels[String(episode.value?.status || '')] || '个人周期')
async function load() {
  loading.value = true; error.value = ''; emptyMessage.value = ''; episode.value = null; unit.value = null
  if (!episodeId.value) { emptyMessage.value = '先创建一个个人周期，或从个人策略页面选择已有周期。'; loading.value = false; return }
  try { const e = await readPolicyEpisode(auth.accessToken, episodeId.value); episode.value = e; unit.value = await readPolicyUnit(auth.accessToken, e.strategy_unit_id) }
  catch (cause) {
    const status = (cause as { status?: number })?.status
    if (status === 404) emptyMessage.value = '找不到这条个人周期记录。'
    else error.value = '个人周期暂时无法读取，请稍后重试。'
  }
  finally { loading.value = false }
}
function slotUsed(endpoint: string, slot: number) { return observations.value.some((row: Record<string, any>) => row.endpoint === endpoint && Number(row.slot) === slot && row.valid) }
const availableSlots = computed(() => Array.from({ length: expectedDays.value }, (_, index) => index).filter(slot => !slotUsed(observation.endpoint, slot)))
async function report(opportunity: Record<string, any>, execution: 'completed' | 'explicitly_not_completed' | 'unknown') {
  const current = episode.value; if (!current || busy.value || opportunity.report || !opportunity.due) return
  const label = execution === 'completed' ? '这一天完成了' : execution === 'explicitly_not_completed' ? '这一天没有完成' : '这一天无法确定'
  if (!window.confirm(`记录为“${label}”？`)) return
  const requestId = `policy-report-${crypto.randomUUID()}`; busy.value = String(opportunity.id); error.value = ''
  try { await reportPolicyExecution(auth.accessToken, episodeId.value, { episode_version: current.version, report_id: requestId, opportunity_id: opportunity.id, execution, perceived_burden: null, confounder_codes: [] }, requestId); await load() }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '执行记录没有保存' }
  finally { busy.value = '' }
}
async function saveObservation() {
  const current = episode.value; const value = Number(observation.value)
  if (!current || !observation.value || !Number.isFinite(value) || value < 0 || value > 10 || slotUsed(observation.endpoint, observation.slot)) { error.value = '请填写 0–10 分，并选择一个尚未使用的观察槽'; return }
  if (!window.confirm(`确认将 ${observation.date} 的主观训练负担记录为 ${value}/10？本人自报会与系统记录分开标记。`)) return
  const requestId = `policy-observation-${crypto.randomUUID()}`; busy.value = 'observation'; error.value = ''
  const date = new Date(`${observation.date}T12:00:00`).toISOString()
  try { await savePolicyObservations(auth.accessToken, episodeId.value, { episode_version: current.version, self_reports: [{ endpoint: observation.endpoint, slot: observation.slot, source_id: requestId, observed_at: date, metric_version: metricVersion.value, value }] }, requestId); observation.value = ''; await load() }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '观察记录没有保存' }
  finally { busy.value = '' }
}
async function stop() {
  const current = episode.value; if (!current || busy.value || !canStop.value) return
  if (!window.confirm('停止后不会生成效果结论；已经记录的数据仍保留。继续？')) return
  busy.value = 'stop'; error.value = ''
  try { const proposal = await createPolicyStopProposal(auth.accessToken, episodeId.value, current.version); if (window.confirm(`${proposal.title || '确认停止周期'}\n\n${proposal.summary || '这会关闭周期，不生成效果结论。'}`)) { await confirmUserAction(auth.accessToken, proposal.proposal_id, proposal.version || 1); await router.replace('/policy') } }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '停止请求没有完成' }
  finally { busy.value = '' }
}
watch(() => route.query.id, () => void load())
onMounted(() => void load())
</script>

<template>
  <section class="page episode-page"><header class="page-heading"><button class="back-button" aria-label="返回" @click="router.back()">‹</button><div><p class="eyebrow">个人周期</p><h1>周期记录</h1></div><button class="refresh" @click="load">刷新</button></header><div v-if="loading" class="card-surface message">正在读取周期记录…</div><div v-else-if="emptyMessage" class="card-surface empty-state"><b>{{ emptyMessage }}</b><p>个人周期和历史记录都可以从个人策略页面查看。</p><button @click="router.replace('/policy')">查看个人策略</button></div><div v-else-if="error && !episode" class="card-surface message">{{ error }} <button @click="load">重试</button></div><template v-else-if="episode">
    <section class="card-surface summary"><div class="summary-head"><div><span class="eyebrow">{{ statusLabel }}</span><h2>{{ unit?.protocol?.title || '个人策略周期' }}</h2></div></div><p>{{ unit?.protocol?.summary || '按约定的安排记录执行情况。' }}</p><div class="dates"><span>开始 {{ new Date(episode.start_at).toLocaleDateString('zh-CN') }}</span><span>结束 {{ new Date(episode.end_at).toLocaleDateString('zh-CN') }}</span></div><p class="note">这里只记录个人观察，不代表健康效果或因果关系。</p></section>
    <section class="section"><h2>执行情况</h2><article v-for="(item,index) in opportunities" :key="item.id" class="card-surface opportunity"><div class="opp-head"><div><b>第 {{ Number(index) + 1 }} 次</b><small>{{ new Date(item.scheduled_at).toLocaleString('zh-CN') }}</small></div><span>{{ item.report?.execution === 'completed' ? '已完成' : item.report?.execution === 'explicitly_not_completed' ? '未完成' : item.report?.execution === 'unknown' ? '无法确定' : '待记录' }}</span></div><div v-if="!item.report && item.due" class="opp-actions"><button :disabled="!!busy" @click="report(item, 'completed')">完成</button><button :disabled="!!busy" @click="report(item, 'explicitly_not_completed')">未完成</button><button :disabled="!!busy" @click="report(item, 'unknown')">不确定</button></div><small v-else-if="!item.report" class="note">到计划时间后可填写。</small></article></section>
    <section class="card-surface observe-card"><h2>自评训练负担</h2><p>0 表示几乎不费力，10 表示非常吃力。这是你填写的感受，不是设备测量。</p><label>记录阶段<select v-model="observation.endpoint" @change="observation.slot = availableSlots[0] ?? 0"><option value="baseline">周期开始前</option><option value="followup">周期开始后</option></select></label><div class="form-row"><label>观察序号<select v-model.number="observation.slot"><option v-for="slot in availableSlots" :key="slot" :value="slot">第 {{ Number(slot) + 1 }} 条</option></select></label><label>记录日期<input v-model="observation.date" type="date" /></label></div><label>负担评分<input v-model="observation.value" type="number" min="0" max="10" step="0.5" inputmode="decimal" placeholder="0–10" /></label><button class="primary" :disabled="busy === 'observation' || !availableSlots.length" @click="saveObservation">{{ busy === 'observation' ? '保存中…' : '保存本人自评' }}</button><p v-if="!availableSlots.length" class="note">该阶段的记录次数已满。</p><div v-if="observations.length" class="observation-list"><b>已记录观察</b><p v-for="(row,index) in observations" :key="`${row.endpoint}:${row.slot}:${index}`">{{ row.endpoint === 'baseline' ? '开始前' : '周期内' }} · 第 {{ Number(row.slot) + 1 }} 条 · {{ row.valid ? `${row.value} / 10` : '来源已变化，请重新补充' }} · {{ row.source_type === 'user_report' ? '本人填写' : '记录核验' }}</p></div></section>
    <p v-if="error" class="error">{{ error }}</p><div class="bottom-actions"><button v-if="canReview" class="primary" @click="router.push({ path: '/policy/review', query: { id: episode.episode_id } })">查看复查证据</button><button v-if="canStop" class="stop" :disabled="!!busy" @click="stop">{{ busy === 'stop' ? '处理中…' : '停止周期' }}</button></div>
  </template></section>
</template>

<style scoped>
.episode-page{padding:9px 0 24px}.page-heading{display:flex;align-items:center;gap:9px;margin-bottom:13px}.page-heading>div{flex:1}.page-heading h1{margin:3px 0 0;font-size:22px}.back-button{width:35px;height:35px;border:0;border-radius:50%;background:#fff;font-size:24px}.refresh{padding:7px 10px;border:0;border-radius:10px;background:#e9efd9;color:#506336;font-size:9px}.message{padding:14px;color:#737a70;font-size:11px}.summary,.observe-card{padding:14px}.summary-head{display:flex;justify-content:space-between;gap:7px}.summary-head h2{margin:4px 0;font-size:16px}.summary-head>span,.summary p,.note{color:#737a70;font-size:9px}.summary p{line-height:1.5}.dates{display:flex;justify-content:space-between;color:#62685f;font-size:9px}.section{margin-top:14px}.section h2,.observe-card h2{margin:0 0 8px;font-size:14px}.opportunity{margin-bottom:7px;padding:11px}.opp-head{display:flex;justify-content:space-between;align-items:center}.opp-head>div{display:flex;flex-direction:column;gap:4px}.opp-head b,.opp-head span{font-size:10px}.opp-head small{color:#737a70;font-size:9px}.opp-actions{display:flex;gap:5px;margin-top:9px}.opp-actions button{flex:1;padding:7px 3px;border:0;border-radius:9px;background:#e9efd9;color:#506336;font-size:9px}.observe-card{margin-top:12px}.observe-card>p{color:#737a70;font-size:9px;line-height:1.5}.observe-card label{display:flex;flex-direction:column;gap:5px;margin-top:8px;color:#62685f;font-size:9px}.observe-card input,.observe-card select{height:36px;padding:0 8px;border:1px solid #eeeee6;border-radius:9px;background:#f7f7f2;font-size:10px}.form-row{display:flex;gap:8px}.form-row label{flex:1}.primary,.stop{width:100%;min-height:39px;margin-top:9px;border:0;border-radius:11px;background:#c4e267;font-size:10px;font-weight:700}.stop{background:#f8e7e4;color:#8f3028}.observation-list{margin-top:11px;padding-top:9px;border-top:1px solid #eeeee6}.observation-list b{font-size:10px}.observation-list p{color:#62685f;font-size:9px;line-height:1.45}.error{color:#8f3028;font-size:10px}.bottom-actions{display:flex;gap:8px}.bottom-actions>*{flex:1}.bottom-actions .stop{padding:0 8px}
.empty-state{padding:16px}.empty-state b{font-size:12px}.empty-state p{color:#737a70;font-size:10px;line-height:1.5}.empty-state button{padding:8px 11px;border:0;border-radius:10px;background:#c4e267;color:#26331e;font-size:10px;font-weight:700}
</style>
