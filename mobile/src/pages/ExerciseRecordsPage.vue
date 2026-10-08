<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { createExerciseRecord, deleteExerciseRecord, readExerciseRecords, type ExerciseRecord } from '../services/api'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()
const records = ref<ExerciseRecord[]>([])
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const form = reactive({ name: '', duration_min: '30', calories_burned: '', intensity: 'medium' })
const intensities = [{ key: 'low', label: '轻松' }, { key: 'medium', label: '中等' }, { key: 'high', label: '较高' }]
async function load() { loading.value = true; error.value = ''; try { records.value = await readExerciseRecords(auth.accessToken) } catch (cause) { error.value = cause instanceof Error ? cause.message : '运动记录暂时无法读取' } finally { loading.value = false } }
async function save() {
  if (saving.value) return
  const duration = Number(form.duration_min)
  if (!form.name.trim() || !Number.isInteger(duration) || duration < 1 || duration > 600) { error.value = '请填写运动项目和 1–600 分钟的时长'; return }
  saving.value = true; error.value = ''
  try { await createExerciseRecord(auth.accessToken, { name: form.name.trim(), duration_min: duration, calories_burned: Math.max(0, Number(form.calories_burned) || 0), intensity: form.intensity }); Object.assign(form, { name: '', duration_min: '30', calories_burned: '', intensity: 'medium' }); await load() }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '运动记录没有保存' }
  finally { saving.value = false }
}
async function remove(item: ExerciseRecord) { if (!window.confirm(`删除“${item.name}”这条运动记录？`)) return; try { await deleteExerciseRecord(auth.accessToken, item.id); await load() } catch (cause) { error.value = cause instanceof Error ? cause.message : '删除失败' } }
onMounted(() => void load())
</script>

<template>
  <section class="page exercise-page">
    <header class="page-heading"><button class="back-button" aria-label="返回" @click="router.back()">‹</button><div><p class="eyebrow">每日运动</p><h1>运动记录</h1></div><button class="video-button" @click="router.push('/media')">动作分析</button></header>
    <section class="card-surface editor"><h2>记录一次运动</h2><label class="field">运动项目<input v-model="form.name" maxlength="120" placeholder="如：快走、力量训练" /></label><div class="form-row"><label class="field">时长（分钟）<input v-model="form.duration_min" type="number" min="1" max="600" inputmode="numeric" /></label><label class="field">消耗 kcal（选填）<input v-model="form.calories_burned" type="number" min="0" max="5000" inputmode="decimal" placeholder="不确定可留空" /></label></div><div class="intensity"><span>运动强度</span><div><button v-for="level in intensities" :key="level.key" :class="{ active: form.intensity === level.key }" @click="form.intensity = level.key">{{ level.label }}</button></div></div><p v-if="error" class="form-error">{{ error }}</p><button class="save-button" :disabled="saving" @click="save">{{ saving ? '保存中…' : '保存运动记录' }}</button><p class="disclaimer">热量消耗仅在有依据时记录；不确定时可以留空为 0。</p></section>
    <h2 class="list-title">最近记录</h2><div v-if="loading" class="card-surface message">正在读取记录…</div><div v-else-if="!records.length" class="card-surface message">还没有运动记录。完成一次活动后，可以在这里补记。</div><div v-else class="record-list"><article v-for="item in records" :key="item.id" class="card-surface record"><div><span>{{ new Date(item.recorded_at).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) }}</span><h3>{{ item.name }}</h3><p>{{ item.duration_min }} 分钟 · {{ intensities.find(x => x.key === item.intensity)?.label || item.intensity }}<template v-if="item.calories_burned"> · {{ Math.round(item.calories_burned) }} kcal</template></p></div><button aria-label="删除记录" @click="remove(item)">删除</button></article></div>
  </section>
</template>

<style scoped>
.exercise-page{padding:9px 0 24px}.page-heading{display:flex;align-items:center;gap:9px;margin-bottom:13px}.page-heading>div{flex:1}.page-heading h1{margin:3px 0 0;font-size:22px}.back-button{width:35px;height:35px;border:0;border-radius:50%;background:#fff;font-size:24px}.video-button{min-height:34px;padding:0 10px;border:0;border-radius:12px;color:#506336;background:#e9efd9;font-size:10px}.editor{padding:14px}.editor h2,.list-title{margin:0 0 11px;font-size:16px}.field{display:flex;flex:1;flex-direction:column;gap:6px;margin-bottom:9px;color:#62685f;font-size:10px}.field input{width:100%;height:39px;padding:0 10px;border:1px solid #eeeee6;border-radius:11px;outline:none;background:#f7f7f2;font-size:12px}.form-row{display:flex;gap:9px}.intensity{display:flex;align-items:center;justify-content:space-between;color:#62685f;font-size:10px}.intensity div{display:flex;gap:5px}.intensity button{padding:7px 10px;border:0;border-radius:13px;background:#f2f2eb;color:#62685f;font-size:10px}.intensity button.active{background:#c4e267;color:#26331e}.save-button{width:100%;height:42px;margin-top:12px;border:0;border-radius:13px;background:#c4e267;font-size:12px;font-weight:700}.save-button:disabled{opacity:.5}.form-error{color:#8f3028;font-size:11px}.disclaimer{margin:8px 0 0;color:#737a70;font-size:9px;line-height:1.5}.list-title{margin-top:17px}.message{padding:15px;color:#737a70;font-size:11px;line-height:1.5}.record-list{display:grid;gap:8px}.record{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:12px}.record span{color:#737a70;font-size:10px}.record h3{margin:4px 0;font-size:13px}.record p{margin:0;color:#62685f;font-size:10px}.record>button{padding:7px 9px;border:0;border-radius:9px;background:#f8e7e4;color:#8f3028;font-size:10px}
</style>
