<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { readCurrentPolicyEpisode, readPolicyCandidates, readPolicyHistory } from '../services/api'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()
const loading = ref(true)
const error = ref('')
const warning = ref('')
const candidate = ref<Record<string, any> | null>(null)
const current = ref<Record<string, any> | null>(null)
const history = ref<Array<Record<string, any>>>([])
async function load() {
  loading.value = true; error.value = ''; warning.value = ''
  const results = await Promise.allSettled([readPolicyCandidates(auth.accessToken), readCurrentPolicyEpisode(auth.accessToken), readPolicyHistory(auth.accessToken)])
  if (results[0].status === 'fulfilled') candidate.value = results[0].value; else error.value = results[0].reason instanceof Error ? results[0].reason.message : '个人策略暂时无法读取'
  if (results[1].status === 'fulfilled') current.value = results[1].value.episode || null; else warning.value = '当前周期暂时无法读取；请检查个人策略授权和网络。'
  if (results[2].status === 'fulfilled') history.value = results[2].value.items.slice(0, 5); else warning.value = '历史记录暂时无法读取；请检查个人策略授权和网络。'
  loading.value = false
}
function statusLabel(status: string) { return ({ active: '进行中', awaiting_review: '等待复查', reviewed: '已复查', stopped: '已停止' } as Record<string,string>)[status] || '状态待更新' }
function conclusion(row: Record<string, any>) { return row.conclusion_valid ? row.conclusion || '尚无最终结论' : row.conclusion ? '来源已变化，旧结论已撤回' : '尚无最终结论' }
onMounted(() => void load())
</script>

<template>
  <section class="page policy-page"><header class="page-heading"><button class="back-button" aria-label="返回" @click="router.back()">‹</button><div><p class="eyebrow">个人记录</p><h1>个人策略</h1></div><button class="refresh" @click="load">刷新</button></header><section class="card-surface hero"><h2>按计划记录，回看自己的变化</h2><p>先选定训练时长，再记录完成情况和感受。周期总结仅供个人参考。</p></section><div v-if="loading" class="card-surface message">正在整理周期与历史记录…</div><div v-else-if="error" class="card-surface message"><b>暂时无法读取</b><p>{{ error }}</p><button @click="load">重试</button></div><template v-else>
    <p v-if="warning" class="warning">{{ warning }} <button @click="router.push('/settings/capabilities')">查看授权</button></p>
    <section v-if="current" class="card-surface current"><p class="eyebrow">正在进行的 7 天周期</p><div class="row"><b>{{ statusLabel(current.status) }}</b><span>{{ current.version ? `版本 ${current.version}` : '' }}</span></div><p>单次训练时长：{{ current.context_snapshot?.changed_variables?.find((item: Record<string, any>) => item.name === 'session_minutes')?.value || '按已确认协议' }} 分钟</p><p>结束时间：{{ new Date(current.end_at).toLocaleString('zh-CN') }}</p><button class="primary" @click="router.push({ path: '/policy/episode', query: { id: current.episode_id } })">继续记录与查看证据</button></section>
    <section v-else class="card-surface candidate"><p class="eyebrow">可以开始的新周期</p><h2>{{ candidate?.candidates?.[0]?.title || '可持续训练时长' }}</h2><p>{{ candidate?.candidates?.[0]?.description || '选定训练时长，记录执行情况和主观负担。' }}</p><p class="note">周期 7 天。开始前和周期内各记录至少 5 次，方便对照回看。</p><ul><li v-for="reason in candidate?.reasons || []" :key="reason">{{ reason }}</li></ul><button class="primary" :disabled="!candidate?.can_compile || !candidate?.can_propose" @click="router.push('/policy/protocol')">查看并设置</button><button v-if="!candidate?.can_compile || !candidate?.can_propose" class="secondary" @click="router.push('/settings/capabilities')">查看功能设置</button></section>
    <section class="card-surface history"><div class="row"><h2>最近的个人周期</h2><button @click="router.push('/policy/history')">全部历史 ›</button></div><p v-if="!history.length" class="note">完成的周期会保存在这里；记录不足时不会补造结论。</p><button v-for="row in history" :key="row.episode_id" class="history-row" @click="router.push({ path: '/policy/review', query: { id: row.episode_id } })"><span><b>{{ new Date(row.started_at).toLocaleDateString('zh-CN') }} · {{ statusLabel(row.status) }}</b><small>{{ conclusion(row) }}</small></span><i>›</i></button></section>
  </template></section>
</template>

<style scoped>
.policy-page{padding:9px 0 24px}.page-heading{display:flex;align-items:center;gap:9px;margin-bottom:12px}.page-heading>div{flex:1}.page-heading h1{margin:3px 0 0;font-size:22px}.back-button{width:35px;height:35px;border:0;border-radius:50%;background:#fff;font-size:24px}.refresh,.history .row button{padding:7px 10px;border:0;border-radius:10px;background:#e9efd9;color:#506336;font-size:9px}.hero,.current,.candidate,.history{margin-bottom:9px;padding:14px}.hero{color:#f7f7f2;background:#26331e}.hero h2{margin:3px 0 7px;font-size:17px}.hero p{margin:0;color:#dce5cb;font-size:10px;line-height:1.5}.message{padding:14px;color:#737a70;font-size:11px}.message p{color:#8f3028}.message button,.warning button{padding:7px 10px;border:0;border-radius:9px;background:#e9efd9;color:#506336;font-size:10px}.warning{padding:10px;border-radius:12px;color:#7c4d27;background:#fff4dd;font-size:10px}.row{display:flex;align-items:center;justify-content:space-between;gap:6px}.current .row b{font-size:13px}.current .row span{color:#737a70;font-size:9px}.current>p,.candidate>p,.candidate li{color:#62685f;font-size:10px;line-height:1.5}.candidate h2{margin:4px 0;font-size:15px}.note{color:#737a70;font-size:9px;line-height:1.5}.candidate ul{padding-left:16px;color:#7c4d27}.primary,.secondary{width:100%;min-height:39px;margin-top:7px;border:0;border-radius:11px;background:#c4e267;font-size:10px;font-weight:700}.primary:disabled{opacity:.5}.secondary{background:#e9efd9;color:#506336}.history .row h2{margin:0;font-size:13px}.history-row{display:flex;width:100%;justify-content:space-between;gap:7px;padding:10px 0;border:0;border-top:1px solid #eeeee6;text-align:left;background:transparent}.history-row span{display:flex;flex-direction:column;gap:4px}.history-row b{font-size:10px}.history-row small,.history-row i{color:#737a70;font-size:9px;font-style:normal}
</style>
