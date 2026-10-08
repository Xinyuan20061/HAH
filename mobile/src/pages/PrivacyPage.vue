<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Capacitor } from '@capacitor/core'
import { Directory, Filesystem } from '@capacitor/filesystem'
import { Share } from '@capacitor/share'
import { useRouter } from 'vue-router'
import { deletePersonalAccount, exportPersonalData, readCloudMedia, readDeletionStatus, readPrivacyExportPreview, readPrivacyPolicy } from '../services/api'
import { useAuthStore } from '../stores/auth'
import { useConversationStore } from '../stores/conversation'

const router = useRouter()
const auth = useAuthStore()
const conversation = useConversationStore()
const loading = ref(true)
const exporting = ref(false)
const deleting = ref(false)
const error = ref('')
const policy = ref<Record<string, any>>({})
const preview = ref<Record<string, any>>({})
const cloudFiles = ref<string[]>([])
const deletion = ref<Record<string, any>>({})
const confirmationText = ref('')
const hasCloudFiles = computed(() => cloudFiles.value.length > 0)
const deletionReady = computed(() => confirmationText.value.trim() === 'DELETE MY DATA')
const exportGroups = [
  { title: '个人资料与偏好', items: [
    ['health_profile', '健康档案'], ['health_goals', '健康目标'], ['training_intent', '训练偏好'],
    ['user_food_priors', '饮食偏好'], ['user_preference_memory', '健康偏好'],
  ] },
  { title: '健康记录与媒体', items: [
    ['checkins', '每日状态'], ['diet_records', '饮食记录'], ['exercise_records', '运动记录'],
    ['timeline', '健康动态'], ['food_analyses', '餐食识别'], ['food_corrections', '餐食修正'],
    ['food_questions', '餐食复核'], ['media_assets', '照片与视频'],
    ['mobile_media_upload_sessions', '媒体处理记录'], ['ai_jobs', '分析任务'],
    ['motion_scores', '动作评分'], ['motion_events', '动作反馈'],
    ['motion_semantic_analyses', '动作分析'], ['motion_analysis_runs', '动作分析任务'],
    ['motion_analysis_feedback', '动作分析反馈'], ['motion_user_feedback', '动作识别修正'],
    ['motion_gold_evaluations', '动作质量评估'], ['legacy_motion_jobs', '历史动作任务'],
  ] },
  { title: '计划与服务记录', items: [
    ['agent_runs', '助手运行记录'], ['agent_micro_experiments', '个人尝试'], ['plans', '健康计划'],
    ['plan_items', '计划事项'], ['plan_task_states', '计划完成情况'], ['weekly_reports', '每周报告'],
    ['goal_adjustments', '目标调整'], ['action_proposals', '待确认事项'], ['action_outcomes', '行动结果'],
    ['action_audits', '操作确认记录'], ['chat_sessions', '对话会话'], ['chat_messages', '对话消息'],
  ] },
  { title: '健康能力与评测', items: [
    ['harness_plugin_installations', '健康能力设置'], ['harness_capability_audits', '能力使用记录'],
    ['evaluation_events', '服务评测记录'], ['evaluation_benchmarks', '评测结果'],
    ['safety_events', '安全处理记录'], ['provider_invocations', '服务调用记录'],
    ['provider_connection_checks', '服务连接检查'], ['voice_usage_daily', '语音使用记录'],
  ] },
  { title: '个人策略与复查', items: [
    ['policy_strategy_units', '个人策略'], ['policy_episodes', '个人周期'],
    ['policy_execution_opportunities', '执行安排'], ['policy_reports', '执行记录'],
    ['policy_observations', '个人观察'], ['policy_adjudications', '复查结论'],
    ['policy_beliefs', '策略记录'], ['policy_decisions', '策略决定'], ['policy_outbox', '待处理事项'],
    ['policy_active_slots', '进行中周期'], ['policy_domain_generations', '策略版本'],
    ['policy_learning_controls', '学习设置'], ['policy_acquisition_sessions', '设置问答'],
    ['policy_acquisition_questions', '设置问题'], ['policy_acquisition_commands', '设置操作'],
    ['policy_acquisition_daily_usage', '设置使用记录'], ['policy_acquisition_events', '设置事件'],
    ['policy_decision_certificates', '复查依据'], ['policy_certificate_dependencies', '依据关联'],
    ['policy_evidence_revisions', '依据修订'], ['policy_acquisition_fences', '安全检查记录'],
  ] },
].map(group => ({
  title: group.title,
  items: group.items.map(([key, label]) => ({ key, label })),
}))
const exportBreakdown = computed(() => {
  const counts = preview.value.counts || {}
  return exportGroups.map(group => {
    const items = group.items.map(item => ({ ...item, count: Math.max(0, Number(counts[item.key] || 0)) }))
    return { ...group, items, total: items.reduce((sum, item) => sum + item.count, 0) }
  })
})
const exportTotals = computed(() => {
  const total = exportBreakdown.value.reduce((sum, group) => sum + group.total, 0)
  const categories = exportBreakdown.value.filter(group => group.total > 0).length
  return { total, categories }
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [policyResult, previewResult, mediaResult, deletionResult] = await Promise.all([
      readPrivacyPolicy(auth.accessToken),
      readPrivacyExportPreview(auth.accessToken),
      readCloudMedia(auth.accessToken),
      readDeletionStatus(auth.accessToken),
    ])
    policy.value = policyResult
    preview.value = previewResult
    cloudFiles.value = mediaResult.file_ids || []
    deletion.value = deletionResult
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '隐私数据暂时无法读取'
  } finally {
    loading.value = false
  }
}

async function saveExport(bytes: ArrayBuffer) {
  const filename = `healthmate-export-${new Date().toISOString().slice(0, 10)}.zip`
  if (Capacitor.isNativePlatform()) {
    const binary = new Uint8Array(bytes)
    let data = ''
    for (let offset = 0; offset < binary.length; offset += 0x8000) {
      data += String.fromCharCode(...binary.subarray(offset, Math.min(offset + 0x8000, binary.length)))
    }
    const base64 = btoa(data)
    const file = await Filesystem.writeFile({ path: filename, data: base64, directory: Directory.Cache })
    try {
      await Share.share({ title: 'HealthMate 个人数据导出', text: '个人数据导出包', files: [file.uri], dialogTitle: '保存或分享导出文件' })
    } finally {
      await Filesystem.deleteFile({ path: filename, directory: Directory.Cache }).catch(() => undefined)
    }
    return
  }
  const url = URL.createObjectURL(new Blob([bytes], { type: 'application/zip' }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

async function exportData() {
  if (!window.confirm('导出文件包含个人资料、健康记录、计划和处理历史，不包含服务密钥或照片、视频文件。继续导出？')) return
  exporting.value = true
  error.value = ''
  try {
    await saveExport(await exportPersonalData(auth.accessToken))
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '数据导出失败，请重试'
  } finally {
    exporting.value = false
  }
}

async function deleteAccount() {
  if (!deletionReady.value || deleting.value) return
  if (!window.confirm('永久删除账户及其健康数据？此操作不可撤销。')) return
  if (!window.confirm('请再次确认删除。没有可恢复的撤销操作。')) return
  deleting.value = true
  error.value = ''
  try {
    const result = await deletePersonalAccount(auth.accessToken)
    if (result.verification === 'pending') {
      window.alert(`账户数据已删除，${result.cloud_media_objects_pending ?? 0} 个云端媒体文件正在由服务端安全重试。`)
    } else if (result.verification === 'partial') {
      window.alert(`账户数据库记录已删除，但有 ${result.media_deletion?.manual_review ?? 0} 项云媒体无法由服务器验证。请联系支持人员核对删除状态。`)
    } else {
      window.alert('账户与关联数据已删除。')
    }
    await auth.signOut()
    conversation.clear()
    await router.replace('/home')
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '删除未完成，请稍后重试'
    await load()
  } finally {
    deleting.value = false
  }
}

onMounted(() => void load())
</script>

<template>
  <section class="page privacy-page">
    <header class="privacy-heading">
      <button class="back-button" aria-label="返回" @click="router.back()">‹</button>
      <div><p class="eyebrow">账号与数据</p><h1>隐私与数据</h1></div>
    </header>
    <div v-if="loading" class="card-surface state-card">正在读取隐私设置…</div>
    <div v-else-if="error && !Object.keys(preview).length" class="card-surface state-card error-state"><p>{{ error }}</p><button @click="load">重试</button></div>
    <template v-else>
      <section class="card-surface privacy-card">
        <h2>你的数据如何处理</h2>
        <dl>
          <div><dt>健康数据</dt><dd>{{ policy.database || 'HealthMate 服务' }}</dd></div>
          <div><dt>媒体存储</dt><dd>{{ policy.media_storage || '按文件来源管理' }}</dd></div>
          <div><dt>AI 服务</dt><dd>{{ policy.ai_processing || '仅按已启用服务处理' }}</dd></div>
          <div><dt>访问密钥</dt><dd>{{ policy.api_key || '由服务端保护' }}</dd></div>
        </dl>
      </section>

      <section class="card-surface privacy-card export-card">
        <div><p class="eyebrow">数据副本</p><h2>导出个人数据</h2><p>保存一份健康资料、记录、计划和处理历史。照片、视频文件和服务密钥不会放入导出文件。</p></div>
        <p class="export-summary">{{ exportTotals.total ? `当前有 ${exportTotals.total} 条记录，分布在 ${exportTotals.categories} 类资料中。` : '目前还没有健康记录；导出文件仍会包含账户资料。' }}</p>
        <details class="export-details">
          <summary>查看导出内容</summary>
          <section v-for="group in exportBreakdown" :key="group.title" class="export-group">
            <div class="export-group-heading"><b>{{ group.title }}</b><span>{{ group.total }} 条</span></div>
            <div class="export-item" v-for="item in group.items" :key="item.key"><span>{{ item.label }}</span><b>{{ item.count }}</b></div>
          </section>
        </details>
        <button class="button-primary" :disabled="exporting" @click="exportData">{{ exporting ? '正在生成文件…' : '生成并保存 ZIP' }}</button>
      </section>

      <section class="card-surface privacy-card deletion-card">
        <p class="eyebrow">不可撤销</p><h2>删除账户与数据</h2>
        <p>删除后，访问令牌、健康记录、计划和处理历史会从服务端移除。</p>
        <p class="media-state" :class="{ blocked: hasCloudFiles }">{{ hasCloudFiles ? `当前账号关联 ${cloudFiles.length} 个云端文件。删除时会逐一处理并核对结果，未完成项会自动重试。` : '当前账号没有关联的云端媒体。' }}</p>
        <p v-if="deletion.manual_review" class="media-state blocked">有 {{ deletion.manual_review }} 个文件仍需核实处理结果，请联系支持人员。</p>
        <label class="field-label" for="delete-confirmation">输入 DELETE MY DATA 以启用删除</label>
        <input id="delete-confirmation" v-model="confirmationText" autocomplete="off" placeholder="DELETE MY DATA" />
        <button class="delete-button" :disabled="!deletionReady || deleting" @click="deleteAccount">{{ deleting ? '正在删除…' : '永久删除账户' }}</button>
      </section>
      <p v-if="error" class="error-copy">{{ error }}</p>
      <button class="retry-link" @click="load">刷新数据状态</button>
    </template>
  </section>
</template>

<style scoped>
.privacy-heading { display:flex; align-items:center; gap:10px; padding:8px 0 16px; }.privacy-heading h1 { margin:3px 0 0; font-size:21px; }
.back-button { width:34px; height:34px; border:0; border-radius:50%; background:#fff; font-size:24px; }
.privacy-card { margin-bottom:10px; padding:16px; }.privacy-card h2 { margin:3px 0 8px; font-size:14px; }.privacy-card p { color:var(--muted); font-size:10px; line-height:1.6; }
dl { margin:0; }dl > div { padding:9px 0; border-top:1px solid var(--divider); }dt { margin-bottom:4px; color:var(--brand-ink); font-size:10px; font-weight:750; }dd { margin:0; color:var(--muted); font-size:10px; line-height:1.6; }
.export-summary { margin:10px 0 0; padding:9px 10px; border-radius:10px; background:var(--page-bg); color:var(--brand-ink) !important; }.export-details { margin-top:8px; border-top:1px solid var(--divider); }.export-details summary { padding:9px 1px 4px; color:var(--brand-ink); font-size:10px; font-weight:700; cursor:pointer; }.export-group { padding:7px 0 3px; border-bottom:1px solid var(--divider); }.export-group-heading,.export-item { display:flex; justify-content:space-between; gap:8px; padding:4px 0; }.export-group-heading b { font-size:10px; }.export-group-heading span,.export-item { color:var(--muted); font-size:9px; }.export-item b { color:var(--ink); }
.export-card .button-primary { width:100%; height:42px; margin-top:10px; border:0; border-radius:13px; background:var(--accent); font-weight:750; }
.deletion-card { border:1px solid rgba(192,57,43,.16); }.deletion-card h2 { color:var(--danger); }.media-state { padding:9px; border-radius:10px; background:var(--accent-soft); color:var(--brand-ink) !important; }.media-state.blocked { background:#f9e8e5; color:var(--danger) !important; }
.field-label { display:block; margin:12px 0 6px; color:var(--muted); font-size:10px; }.deletion-card input { width:100%; height:40px; padding:0 10px; border:1px solid var(--divider); border-radius:11px; outline:none; font-size:12px; }.delete-button { width:100%; height:42px; margin-top:9px; border:0; border-radius:13px; color:#fff; background:var(--danger); font-size:11px; font-weight:750; }.delete-button:disabled { opacity:.38; }.error-copy { color:var(--danger); font-size:11px; }.retry-link { display:block; margin:4px auto 12px; padding:8px; border:0; color:var(--brand-ink); background:transparent; font-size:10px; }.state-card { padding:18px; color:var(--muted); font-size:12px; }.error-state { color:var(--danger); }.error-state button { padding:8px 12px; border:0; border-radius:10px; background:var(--accent-soft); }
</style>
