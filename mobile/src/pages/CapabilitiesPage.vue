<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { createPluginInstallation, deletePluginInstallation, previewPluginInstallation, readCapabilityHonesty, readPluginAudit, readPluginCatalog, savePluginInstallation, setPluginInstallationEnabled } from '../services/api'
import { useAuthStore } from '../stores/auth'

type Plugin = Record<string, any>
const router = useRouter()
const auth = useAuthStore()
const plugins = ref<Plugin[]>([])
const honesty = ref<Record<string, any> | null>(null)
const loading = ref(true)
const busy = ref('')
const error = ref('')
const reviewedFoodCount = computed(() => Number(honesty.value?.food?.reviewed_entries || 0))
const totalFoodCount = computed(() => Number(honesty.value?.food?.entries || 0))
const verifiedMotionCount = computed(() => (honesty.value?.motion?.gold_exercises || []).length)
const foodQualityText = computed(() => totalFoodCount.value
  ? `食物资料已核验 ${reviewedFoodCount.value} / ${totalFoodCount.value} 项；记录前请核对识别结果。`
  : '食物资料仍在核验，识别结果请先核对再保存。')
const motionQualityText = computed(() => verifiedMotionCount.value
  ? `${verifiedMotionCount.value} 类动作已完成质量核验。`
  : '动作分析目前提供基础训练参考。')
async function load() {
  loading.value = true; error.value = ''
  const [catalog, status] = await Promise.allSettled([readPluginCatalog(auth.accessToken), readCapabilityHonesty(auth.accessToken)])
  if (catalog.status === 'fulfilled') plugins.value = catalog.value.plugins.map(item => ({ ...item, expanded: false, dirty: false, audit: null, preview: null, showAudit: false }))
  else error.value = catalog.reason instanceof Error ? catalog.reason.message : '健康能力暂时无法读取'
  honesty.value = status.status === 'fulfilled' ? status.value : null
  loading.value = false
}
function setScope(plugin: Plugin, scopeId: string) {
  if (plugin.enabled) return
  const scopes = new Set<string>(plugin.config?.data_scopes || [])
  if (scopes.has(scopeId)) scopes.delete(scopeId); else scopes.add(scopeId)
  plugin.config = { ...plugin.config, data_scopes: [...scopes] }; plugin.dirty = true; plugin.preview = null
}
function setChoice(plugin: Plugin, field: string, value: unknown) {
  if (plugin.enabled) return
  plugin.config = { ...plugin.config, [field]: value }; plugin.dirty = true; plugin.preview = null
}
async function persist(plugin: Plugin) {
  if (!plugin.installation_id) {
    const result = await createPluginInstallation(auth.accessToken, plugin.plugin_id, plugin.config)
    Object.assign(plugin, result.installation, { dirty: false })
  } else if (plugin.dirty) {
    const result = await savePluginInstallation(auth.accessToken, plugin.installation_id, plugin.config_version, plugin.config)
    Object.assign(plugin, result.installation, { dirty: false })
  }
}
async function preview(plugin: Plugin) {
  busy.value = plugin.plugin_id; error.value = ''
  try { await persist(plugin); plugin.preview = await previewPluginInstallation(auth.accessToken, plugin.installation_id, plugin.config_version) }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '预览没有生成' }
  finally { busy.value = '' }
}
async function toggle(plugin: Plugin) {
  if (busy.value) return
  if (plugin.enabled) {
    if (!window.confirm(`关闭“${plugin.title}”？之后不再开始新的读取和建议，未确认的行动申请会失效，历史仍保留。`)) return
  } else {
    if (!window.confirm(`确认开启“${plugin.title}”？\n\n允许运用：${(plugin.data_scopes || []).filter((scope: Plugin) => (plugin.config?.data_scopes || []).includes(scope.id)).map((scope: Plugin) => scope.label).join('、') || '不读取个人数据'}。\n\n${plugin.privacy_summary || ''}\n\n所有写入仍需你确认。可随时关闭。`)) return
  }
  busy.value = plugin.plugin_id; error.value = ''
  try {
    await persist(plugin)
    const result = await setPluginInstallationEnabled(auth.accessToken, plugin.installation_id, plugin.config_version, !plugin.enabled)
    Object.assign(plugin, result.installation, { dirty: false, preview: null })
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '能力状态没有更新' }
  finally { busy.value = '' }
}
async function showAudit(plugin: Plugin) {
  if (!plugin.installation_id) return
  busy.value = plugin.plugin_id; error.value = ''
  try { const result = await readPluginAudit(auth.accessToken, plugin.installation_id); plugin.audit = result.events || []; plugin.showAudit = !plugin.showAudit }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '使用记录暂时无法读取' }
  finally { busy.value = '' }
}
async function remove(plugin: Plugin) {
  if (!plugin.installation_id || !window.confirm(`删除“${plugin.title}”的能力配置？这不会删除健康记录。`)) return
  busy.value = plugin.plugin_id; error.value = ''
  try { await deletePluginInstallation(auth.accessToken, plugin.installation_id, plugin.config_version); await load() }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '能力配置没有删除' }
  finally { busy.value = '' }
}
onMounted(() => void load())
</script>

<template>
  <section class="page capabilities-page"><header class="page-heading"><button class="back-button" aria-label="返回" @click="router.back()">‹</button><div><p class="eyebrow">数据与授权</p><h1>健康能力</h1></div><button class="refresh" @click="load">刷新</button></header><p class="intro">开启前会说明读取哪些数据；写入健康记录仍需你确认，之后也可以随时关闭。</p><div v-if="loading" class="card-surface message">正在读取授权状态…</div><div v-else-if="error && !plugins.length" class="card-surface message">{{ error }} <button @click="load">重试</button></div>
    <div v-else class="plugin-list"><article v-for="plugin in plugins" :key="plugin.plugin_id" class="card-surface plugin-card"><button class="plugin-head" @click="plugin.expanded = !plugin.expanded"><span class="plugin-copy"><b>{{ plugin.title }}</b><small>{{ plugin.summary }}</small></span><span class="state" :class="{ on: plugin.enabled }">{{ plugin.enabled ? '已开启' : plugin.capability_state === 'review_required' ? '需要重新确认' : plugin.installation_id ? '已关闭' : '未开启' }}</span><i>{{ plugin.expanded ? '−' : '+' }}</i></button>
      <div v-if="plugin.expanded" class="plugin-body"><p>{{ plugin.user_value }}</p><p class="privacy-summary">{{ plugin.privacy_summary }}</p><div class="scope-title">选择可使用的数据</div><label v-for="scope in plugin.data_scopes || []" :key="scope.id" class="scope-row"><input type="checkbox" :checked="(plugin.config?.data_scopes || []).includes(scope.id)" :disabled="plugin.enabled" @change="setScope(plugin, scope.id)"/><span>{{ scope.label }}</span></label>
        <label class="choice-field">优先目标<select :value="plugin.config?.goal" :disabled="plugin.enabled" @change="setChoice(plugin, 'goal', ($event.target as HTMLSelectElement).value)"><option v-for="choice in plugin.config_schema?.goals || []" :key="choice.id" :value="choice.id">{{ choice.label }}</option></select></label>
        <label class="choice-field">回答方式<select :value="plugin.config?.output_style" :disabled="plugin.enabled" @change="setChoice(plugin, 'output_style', ($event.target as HTMLSelectElement).value)"><option v-for="choice in plugin.config_schema?.output_styles || []" :key="choice.id" :value="choice.id">{{ choice.label }}</option></select></label>
        <label class="choice-field">提醒频率<select :value="plugin.config?.notification_frequency" :disabled="plugin.enabled" @change="setChoice(plugin, 'notification_frequency', ($event.target as HTMLSelectElement).value)"><option v-for="choice in plugin.config_schema?.notification_frequencies || []" :key="choice.id" :value="choice.id">{{ choice.label }}</option></select></label>
        <label v-if="plugin.safety?.may_propose_action" class="proposal-row"><input type="checkbox" :checked="!!plugin.config?.allow_action_proposals" :disabled="plugin.enabled" @change="setChoice(plugin, 'allow_action_proposals', ($event.target as HTMLInputElement).checked)"/><span>允许提出待我确认的行动申请</span></label>
        <div class="controls"><button :disabled="!!busy || !plugin.installation_id && !plugin.config" @click="preview(plugin)">{{ busy === plugin.plugin_id ? '处理中…' : '预览授权效果' }}</button><button class="primary" :disabled="!!busy || plugin.enabled && plugin.dirty" @click="toggle(plugin)">{{ plugin.enabled ? '关闭能力' : '保存并开启' }}</button></div>
        <p v-if="plugin.dirty" class="dirty-note">设置已更改，开启时需要重新确认。</p>
        <section v-if="plugin.preview" class="preview"><b>功能示例</b><p>{{ plugin.preview.output_example }}</p><small>这是示例内容，不含你的个人记录。</small></section>
        <div class="management"><button :disabled="!!busy || !plugin.installation_id" @click="showAudit(plugin)">{{ plugin.showAudit ? '收起使用记录' : '查看使用记录' }}</button><button v-if="plugin.installation_id" class="delete" :disabled="!!busy" @click="remove(plugin)">删除能力配置</button></div>
        <section v-if="plugin.showAudit" class="audit-list"><p v-if="!plugin.audit?.length">还没有使用记录。</p><article v-for="event in plugin.audit || []" :key="`${event.at}:${event.event}`"><b>{{ event.event }}</b><small>{{ String(event.at || '').replace('T',' ').replace('Z','').slice(0,16) }}</small></article></section>
      </div></article></div>
    <section v-if="honesty" class="card-surface honesty"><h2>能力状态</h2><p>{{ foodQualityText }}</p><p>{{ motionQualityText }}</p></section>
    <p v-if="error && plugins.length" class="inline-error">{{ error }}</p>
  </section>
</template>

<style scoped>
.capabilities-page{padding:9px 0 24px}.page-heading{display:flex;align-items:center;gap:9px;margin-bottom:10px}.page-heading>div{flex:1}.page-heading h1{margin:3px 0 0;font-size:22px}.back-button{width:35px;height:35px;border:0;border-radius:50%;background:#fff;font-size:24px}.refresh{padding:7px 10px;border:0;border-radius:11px;background:#e9efd9;color:#506336;font-size:10px}.intro{margin:0 3px 10px;color:#62685f;font-size:10px;line-height:1.55}.message{padding:14px;color:#737a70;font-size:11px}.plugin-list{display:grid;gap:8px}.plugin-card{overflow:hidden}.plugin-head{display:flex;width:100%;align-items:center;gap:8px;padding:13px;border:0;text-align:left;background:transparent}.plugin-copy{display:flex;flex:1;flex-direction:column;gap:5px}.plugin-copy b{font-size:12px}.plugin-copy small{color:#737a70;font-size:9px;line-height:1.4}.plugin-head i{font-size:18px;font-style:normal}.state{padding:5px 7px;border-radius:10px;background:#f2f2eb;color:#737a70;font-size:8px;white-space:nowrap}.state.on{background:#e9efd9;color:#506336}.plugin-body{padding:0 13px 13px;border-top:1px solid #f0f0e9}.plugin-body>p{color:#62685f;font-size:10px;line-height:1.5}.privacy-summary{padding:8px;border-radius:10px;background:#f7f7f2}.scope-title{margin:12px 0 6px;font-size:10px;font-weight:700}.scope-row,.proposal-row{display:flex;align-items:center;gap:8px;min-height:32px;color:#62685f;font-size:10px}.scope-row input,.proposal-row input{accent-color:#647a36}.choice-field{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:6px;color:#62685f;font-size:10px}.choice-field select{max-width:58%;min-width:145px;height:34px;padding:0 8px;border:1px solid #eeeee6;border-radius:9px;background:#f7f7f2;font-size:10px}.controls,.management{display:flex;gap:7px;margin-top:11px}.controls button,.management button{flex:1;min-height:37px;padding:6px;border:0;border-radius:10px;background:#e9efd9;color:#506336;font-size:10px}.controls .primary{background:#c4e267;color:#26331e}.controls button:disabled,.management button:disabled{opacity:.5}.dirty-note,.inline-error{color:#8f3028;font-size:9px;line-height:1.5}.preview{margin-top:10px;padding:10px;border-radius:12px;background:#eef3e4}.preview b{font-size:10px}.preview p,.preview small{color:#62685f;font-size:9px;line-height:1.5}.management .delete{color:#8f3028;background:#f8e7e4}.audit-list{margin-top:9px;padding:8px;border-radius:11px;background:#f7f7f2}.audit-list article{display:flex;justify-content:space-between;gap:5px;padding:6px 0;border-bottom:1px solid #e9e9e1;font-size:9px}.audit-list small,.audit-list p{color:#737a70;font-size:9px}.honesty{margin-top:13px;padding:13px}.honesty h2{font-size:12px}.honesty p,.honesty small{color:#62685f;font-size:9px;line-height:1.5}.inline-error{margin-top:9px}
</style>
