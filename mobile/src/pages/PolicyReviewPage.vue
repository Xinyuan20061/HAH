<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { confirmUserAction, createPolicyFinishProposal, previewPolicyReview, readPolicyEpisode, readPolicyExplanation } from '../services/api'
import { useAuthStore } from '../stores/auth'

const route = useRoute(); const router = useRouter(); const auth = useAuthStore()
const id = computed(() => typeof route.query.id === 'string' ? route.query.id : '')
const episode = ref<Record<string, any> | null>(null); const explanation = ref<Record<string, any> | null>(null); const preview = ref<Record<string, any> | null>(null)
const loading = ref(true); const busy = ref(false); const error = ref(''); const emptyMessage = ref('')
const statusLabels: Record<string, string> = { active: '进行中', awaiting_review: '等待复查', completed: '已完成', stopped: '已停止' }
const sourceLabels: Record<string, string> = { user_report: '本人自评', checkin: '健康记录', health_checkin: '健康记录', exercise_record: '运动记录', plan_task: '计划记录' }
const statusLabel = computed(() => statusLabels[String(episode.value?.status || '')] || '个人周期')
const canFinish = () => (episode.value?.allowed_actions || []).includes('finish_proposal')
async function load() {
  loading.value = true; error.value = ''; emptyMessage.value = ''; episode.value = null; explanation.value = null; preview.value = null
  if (!id.value) { emptyMessage.value = '先选择一个个人周期，再查看复查记录。'; loading.value = false; return }
  try { const e = await readPolicyEpisode(auth.accessToken, id.value); episode.value = e; const [why, verdict] = await Promise.all([readPolicyExplanation(auth.accessToken, id.value), previewPolicyReview(auth.accessToken, id.value).catch(() => null)]); explanation.value = why; preview.value = verdict }
  catch (cause) {
    const status = (cause as { status?: number })?.status
    if (status === 404) emptyMessage.value = '找不到这条个人周期记录。'
    else error.value = '复查记录暂时无法读取，请稍后重试。'
  }
  finally { loading.value = false }
}
async function finish() {
  const current = episode.value; if (!current || !canFinish() || busy.value) return
  if (!window.confirm('按开始前冻结的门槛复核执行情况、观察覆盖和来源有效性？证据不足时会如实标记为无法判断。')) return
  busy.value = true; error.value = ''
  try { const proposal = await createPolicyFinishProposal(auth.accessToken, id.value, current.version); if (window.confirm(`${proposal.title || '确认生成本周期复查'}\n\n${proposal.summary || '只按原有门槛复核，不会改写协议。'}`)) { await confirmUserAction(auth.accessToken, proposal.proposal_id, proposal.version || 1); await load() } }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '复查请求没有完成' }
  finally { busy.value = false }
}
function label(value: string) { const labels: Record<string,string> = { supports_observed_target:'本周期观察到预设方向的变化', target_not_supported:'本周期未观察到预设方向的变化', ambiguous:'当前记录不足以确定方向', insufficient_exposure:'执行记录不足，暂时无法判断', insufficient_data:'记录不足，暂时无法判断', incomparable:'本周期条件不一致，无法比较', stopped:'周期已停止，没有生成效果结论' }; return labels[value] || '暂时无法判断' }
function sourceLabel(ref: Record<string, any>) {
  const raw = String(ref.source_type || ref.endpoint || '')
  return sourceLabels[raw] || (raw.includes('user') ? '本人记录' : '健康记录')
}
watch(() => route.query.id, () => void load())
onMounted(() => void load())
</script>

<template>
  <section class="page review-page"><header class="page-heading"><button class="back-button" aria-label="返回" @click="router.back()">‹</button><div><p class="eyebrow">周期回顾</p><h1>复查与记录</h1></div><button class="refresh" @click="load">刷新</button></header><div v-if="loading" class="card-surface message">正在读取复查记录…</div><div v-else-if="emptyMessage" class="card-surface empty-state"><b>{{ emptyMessage }}</b><p>可以在个人策略页面查看正在进行的周期和历史记录。</p><button @click="router.replace('/policy')">查看个人策略</button></div><div v-else-if="error && !episode" class="card-surface message">{{ error }} <button @click="load">重试</button></div><template v-else-if="episode && explanation">
    <section class="card-surface hero"><p class="eyebrow">{{ statusLabel }}</p><h2>{{ explanation.protocol?.template?.title || '个人策略周期' }}</h2><p>周期状态：{{ statusLabel }}</p><p>开始前：{{ explanation.context_snapshot?.baseline_context_key || '未记录' }}</p><p>周期内：{{ explanation.context_snapshot?.followup_context_key || '未记录' }}</p></section>
    <section class="card-surface section"><h2>当前复查预览</h2><template v-if="preview"><p><b>结论：</b>{{ label(preview.conclusion) }}</p><p>执行记录：{{ preview.execution_label }}</p><p>观察方向：{{ preview.support_label }}</p><p>资料覆盖：{{ preview.availability_label }}</p><p v-for="reason in preview.reasons || []" :key="reason">{{ reason }}</p><small>预览不会关闭周期或写入最终结论。</small></template><p v-else>当前暂时无法生成预览。记录不足时不会补造结论。</p><div v-if="explanation.conclusion" class="final-conclusion"><b>已保存的结论：{{ label(explanation.conclusion.label) }}</b><span v-if="!explanation.conclusion.valid || explanation.conclusion.stale">来源已变化，旧结论已撤回，需要重新核验证据。</span><p v-for="reason in explanation.conclusion.reasons || []" :key="reason">{{ reason }}</p></div></section>
    <section class="card-surface section"><h2>参考记录</h2><article v-for="(ref,index) in explanation.evidence_refs || []" :key="index" class="evidence-row"><b>{{ sourceLabel(ref) }} · 第 {{ Number(ref.slot ?? 0) + 1 }} 条</b><small>{{ ref.valid === false ? '来源已变化' : ref.value === null || ref.value === undefined ? '未填写数值' : `${ref.value} ${ref.unit || ''}` }} · {{ ref.observed_at || '' }}</small></article><p v-for="limit in explanation.limitations || []" :key="limit" class="note">{{ limit }}</p></section>
    <p v-if="error" class="error">{{ error }}</p><button v-if="canFinish()" class="finish" :disabled="busy" @click="finish">{{ busy ? '处理中…' : '确认生成复查结论' }}</button><button class="history-link" @click="router.push('/policy/history')">查看历史周期</button>
  </template></section>
</template>

<style scoped>
.review-page{padding:9px 0 24px}.page-heading{display:flex;align-items:center;gap:9px;margin-bottom:13px}.page-heading>div{flex:1}.page-heading h1{margin:3px 0 0;font-size:22px}.back-button{width:35px;height:35px;border:0;border-radius:50%;background:#fff;font-size:24px}.refresh{padding:7px 10px;border:0;border-radius:10px;background:#e9efd9;color:#506336;font-size:9px}.message{padding:14px;color:#737a70;font-size:11px}.hero,.section{margin-bottom:9px;padding:14px}.hero{color:#f7f7f2;background:#26331e}.hero .eyebrow{color:#c4e267}.hero h2{font-size:16px}.hero p{color:#dce5cb;font-size:9px;line-height:1.5}.section h2{margin:0 0 10px;font-size:13px}.section p{color:#62685f;font-size:10px;line-height:1.5}.section small,.note{color:#737a70;font-size:9px;line-height:1.5}.final-conclusion{margin-top:10px;padding:10px;border-radius:12px;background:#eef3e4}.final-conclusion b,.final-conclusion span{display:block;font-size:10px}.final-conclusion span{margin-top:5px;color:#8f3028}.evidence-row{display:flex;flex-direction:column;gap:4px;padding:8px 0;border-top:1px solid #eeeee6}.evidence-row b{font-size:9px}.evidence-row small{font-size:9px}.finish,.history-link{width:100%;min-height:40px;margin-top:7px;border:0;border-radius:11px;background:#c4e267;font-size:10px;font-weight:700}.history-link{background:#e9efd9;color:#506336}.error{color:#8f3028;font-size:10px}
.empty-state{padding:16px}.empty-state b{font-size:12px}.empty-state p{color:#737a70;font-size:10px;line-height:1.5}.empty-state button{padding:8px 11px;border:0;border-radius:10px;background:#c4e267;color:#26331e;font-size:10px;font-weight:700}
</style>
