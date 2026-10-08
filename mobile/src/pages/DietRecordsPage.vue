<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { createDietRecord, deleteDietRecord, editDietRecord, readDietRecords, type DietRecord } from '../services/api'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const today = new Date(Date.now() + 8 * 60 * 60 * 1000).toISOString().slice(0, 10)
const date = ref(typeof route.query.date === 'string' ? route.query.date : today)
const mealType = ref(typeof route.query.meal_type === 'string' ? route.query.meal_type : '')
const records = ref<DietRecord[]>([])
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const editingId = ref<number | null>(null)
const editingVersion = ref(1)
const form = reactive({ name: '', meal_type: 'breakfast' as DietRecord['meal_type'], calories: '', protein: '', carbs: '', fat: '' })
const totalCalories = computed(() => records.value.reduce((sum, item) => sum + Number(item.calories || 0), 0))
const meals = [{ key: '', label: '全部' }, { key: 'breakfast', label: '早餐' }, { key: 'lunch', label: '午餐' }, { key: 'dinner', label: '晚餐' }, { key: 'snack', label: '加餐' }, { key: 'other', label: '其他' }]
const mealLabel = (key: string) => meals.find(item => item.key === key)?.label || '其他'

async function load() {
  loading.value = true; error.value = ''
  try { records.value = (await readDietRecords(auth.accessToken, date.value, mealType.value)).items }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '饮食记录暂时无法读取' }
  finally { loading.value = false }
}
function clearForm() {
  editingId.value = null; editingVersion.value = 1
  Object.assign(form, { name: '', meal_type: mealType.value as DietRecord['meal_type'] || 'breakfast', calories: '', protein: '', carbs: '', fat: '' })
}
function edit(item: DietRecord) {
  editingId.value = item.id; editingVersion.value = item.version
  Object.assign(form, { name: item.name, meal_type: item.meal_type, calories: String(item.calories), protein: String(item.protein), carbs: String(item.carbs), fat: String(item.fat) })
  document.getElementById('diet-editor')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
async function save() {
  if (saving.value) return
  if (!form.name.trim() || !form.calories || Number(form.calories) < 0) { error.value = '请填写食物名称和有效热量'; return }
  saving.value = true; error.value = ''
  const value = { name: form.name.trim(), meal_type: form.meal_type, calories: Number(form.calories), protein: Number(form.protein) || 0, carbs: Number(form.carbs) || 0, fat: Number(form.fat) || 0 }
  try {
    if (editingId.value) await editDietRecord(auth.accessToken, editingId.value, { ...value, version: editingVersion.value })
    else await createDietRecord(auth.accessToken, value)
    clearForm(); await load()
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '记录没有保存，请重试' }
  finally { saving.value = false }
}
async function remove(item: DietRecord) {
  if (!window.confirm(`删除“${item.name}”这条饮食记录？此操作无法撤销。`)) return
  error.value = ''
  try { await deleteDietRecord(auth.accessToken, item.id); if (editingId.value === item.id) clearForm(); await load() }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '删除失败，请重试' }
}
onMounted(() => void load())
</script>

<template>
  <section class="page diet-page">
    <header class="page-heading"><button class="back-button" aria-label="返回" @click="router.back()">‹</button><div><p class="eyebrow">每日饮食</p><h1>饮食记录</h1></div><button class="scan-button" @click="router.push('/scan')">拍照</button></header>
    <section class="card-surface day-card"><label>记录日期<input v-model="date" type="date" @change="load"></label><div><b>{{ Math.round(totalCalories) }}</b><small>本日筛选结果 kcal</small></div></section>
    <div class="meal-filters"><button v-for="meal in meals" :key="meal.key" :class="{ active: mealType === meal.key }" @click="mealType = meal.key; load()">{{ meal.label }}</button></div>
    <div v-if="loading" class="card-surface loading-card"><span class="spinner"/>正在读取记录…</div>
    <div v-else-if="error && !records.length" class="card-surface error-card"><b>记录没有加载出来</b><p>{{ error }}</p><button @click="load">重试</button></div>
    <div v-else-if="!records.length" class="card-surface empty-card"><b>这一天还没有饮食记录</b><p>可以手动记一餐，或用拍照识别生成待确认草稿。</p></div>
    <div v-else class="diet-list">
      <article v-for="item in records" :key="item.id" class="card-surface diet-record">
        <div class="record-title"><div><span>{{ mealLabel(item.meal_type) }} · {{ new Date(item.recorded_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) }}</span><h2>{{ item.name }}</h2></div><b>{{ Math.round(item.calories) }} kcal</b></div>
        <p class="macros">蛋白质 {{ Math.round(item.protein) }}g · 碳水 {{ Math.round(item.carbs) }}g · 脂肪 {{ Math.round(item.fat) }}g</p>
        <div class="record-foot"><small>{{ item.source_label || '手动记录' }}</small><div><button @click="edit(item)">编辑</button><button class="danger-action" @click="remove(item)">删除</button></div></div>
      </article>
    </div>
    <section id="diet-editor" class="card-surface editor-card">
      <div class="editor-heading"><div><p class="eyebrow">{{ editingId ? '编辑记录' : '添加一餐' }}</p><h2>{{ editingId ? '修改饮食记录' : '手动添加一餐' }}</h2></div><button v-if="editingId" class="cancel-edit" @click="clearForm">取消</button></div>
      <label class="field">食物名称<input v-model="form.name" maxlength="120" placeholder="如：燕麦牛奶" /></label>
      <div class="form-row"><label class="field">餐次<select v-model="form.meal_type"><option v-for="meal in meals.slice(1)" :key="meal.key" :value="meal.key">{{ meal.label }}</option></select></label><label class="field">热量 kcal<input v-model="form.calories" type="number" min="0" max="5000" inputmode="decimal" /></label></div>
      <div class="form-row three"><label class="field">蛋白质 g<input v-model="form.protein" type="number" min="0" inputmode="decimal" /></label><label class="field">碳水 g<input v-model="form.carbs" type="number" min="0" inputmode="decimal" /></label><label class="field">脂肪 g<input v-model="form.fat" type="number" min="0" inputmode="decimal" /></label></div>
      <p v-if="error" class="form-error">{{ error }}</p><button class="save-button" :disabled="saving" @click="save">{{ saving ? '保存中…' : editingId ? '保存修改' : '保存记录' }}</button>
      <p class="disclaimer">手动录入的营养值由你填写；拍照估算结果需先在识别页确认。</p>
    </section>
  </section>
</template>

<style scoped>
.diet-page{padding:9px 0 26px}.page-heading{display:flex;align-items:center;gap:9px;margin-bottom:13px}.page-heading>div{flex:1}.page-heading h1{margin:3px 0 0;font-size:22px}.back-button{width:35px;height:35px;border:0;border-radius:50%;background:#fff;font-size:24px}.scan-button,.cancel-edit{min-height:34px;padding:0 11px;border:0;border-radius:12px;background:#e9efd9;color:#506336;font-size:11px}.day-card{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:12px 14px}.day-card label{display:flex;flex-direction:column;gap:5px;color:#737a70;font-size:10px}.day-card input{width:145px;padding:6px 8px;border:1px solid #eeeee6;border-radius:9px;background:#f7f7f2;font-size:12px}.day-card>div{display:flex;flex-direction:column;align-items:flex-end;gap:3px}.day-card b{font-size:18px}.day-card small,.macros,.record-foot small{color:#737a70;font-size:10px}.meal-filters{display:flex;gap:6px;overflow-x:auto;margin:10px 0}.meal-filters button{flex:0 0 auto;padding:7px 11px;border:0;border-radius:15px;color:#62685f;background:#fff;font-size:10px}.meal-filters button.active{color:#26331e;background:#c4e267}.diet-list{display:grid;gap:8px}.diet-record{padding:13px}.record-title{display:flex;justify-content:space-between;gap:8px}.record-title span{color:#737a70;font-size:10px}.record-title h2{margin:4px 0 0;font-size:14px}.record-title>b{white-space:nowrap;font-size:13px}.macros{margin:9px 0}.record-foot{display:flex;align-items:center;justify-content:space-between}.record-foot div{display:flex;gap:8px}.record-foot button{padding:5px 9px;border:0;border-radius:9px;color:#506336;background:#e9efd9;font-size:10px}.record-foot .danger-action{color:#8f3028;background:#f8e7e4}.editor-card{margin-top:12px;padding:14px}.editor-heading{display:flex;align-items:center;justify-content:space-between;margin-bottom:11px}.editor-heading h2{margin:3px 0 0;font-size:16px}.field{display:flex;flex:1;flex-direction:column;gap:6px;margin-bottom:9px;color:#62685f;font-size:10px}.field input,.field select{width:100%;min-width:0;height:39px;padding:0 10px;border:1px solid #eeeee6;border-radius:11px;outline:none;background:#f7f7f2;font-size:12px}.form-row{display:flex;gap:9px}.form-row.three{gap:7px}.form-error{color:#8f3028;font-size:11px}.save-button{width:100%;height:42px;border:0;border-radius:13px;background:#c4e267;font-size:12px;font-weight:700}.save-button:disabled{opacity:.55}.disclaimer{color:#737a70;font-size:9px;text-align:center}.loading-card,.empty-card,.error-card{padding:16px;margin:9px 0;color:#596056;font-size:12px}.loading-card{display:flex;align-items:center;gap:10px}.empty-card p,.error-card p{margin-bottom:0;color:#737a70;font-size:11px;line-height:1.5}.error-card button{margin-top:9px;padding:7px 10px;border:0;border-radius:9px;background:#e9efd9;color:#506336}.spinner{width:18px;height:18px;border:3px solid #e9efd9;border-top-color:#506336;border-radius:50%;animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}
</style>
