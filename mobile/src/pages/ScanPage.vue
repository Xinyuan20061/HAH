<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { waitForAppForeground } from '../services/appLifecycle'
import { correctFoodAnalysis, createFoodJob, finalizeFoodAnalysis, readFoodAnalysis, readFoodJob } from '../services/api'
import { refreshCloudMedia, uploadCloudMedia } from '../services/cloudMedia'
import { captureOrChooseMealPhoto } from '../services/mediaPicker'
import { clearUserJob, readUserJob, writeUserJob } from '../services/userScopedJobs.mjs'
import { useAuthStore } from '../stores/auth'

type MediaAsset = { media_id: number; cloud_file_id: string; size?: number; temp_url?: string }
const router = useRouter(); const auth = useAuthStore()
const chooser = ref<HTMLInputElement | null>(null)
const photo = ref<Blob | null>(null); const photoUrl = ref(''); const photoName = ref('')
const uploadRequestId = ref(crypto.randomUUID())
const asset = ref<MediaAsset | null>(null); const jobId = ref<number | null>(null)
const analysis = ref<Record<string, any> | null>(null)
const loading = ref(false); const status = ref(''); const error = ref(''); const saved = ref(false)
const mealType = ref('lunch'); const manualMeal = ref(false); const lastCorrectedSignature = ref('')
const form = reactive({ dish_name: '', portion: '', cooking_method: '', weight_g: 0, calories: 0, protein: 0, carbs: 0, fat: 0, fiber: 0 })
let stopped = false
const pageLifetime = new AbortController()
const confidence = computed(() => Math.round(Math.max(0, Math.min(1, Number(analysis.value?.confidence || 0))) * 100))
const needsCarefulReview = computed(() => confidence.value < 60 || !analysis.value?.portion_basis || !Number(form.calories))

function chooseMealDefault() {
  const hour = Number(new Intl.DateTimeFormat('en-GB', { hour: '2-digit', hour12: false, timeZone: 'Asia/Shanghai' }).format(new Date()))
  mealType.value = hour >= 5 && hour < 10 ? 'breakfast' : hour >= 10 && hour < 15 ? 'lunch' : hour >= 17 && hour < 21 ? 'dinner' : 'snack'
}
function setPreview(blob: Blob, name: string) {
  if (photoUrl.value) URL.revokeObjectURL(photoUrl.value)
  photo.value = blob; photoUrl.value = URL.createObjectURL(blob); photoName.value = name
  uploadRequestId.value = crypto.randomUUID()
  asset.value = null; jobId.value = null; analysis.value = null; saved.value = false; lastCorrectedSignature.value = ''; error.value = ''; status.value = ''
  clearUserJob('hm-food-job')
}
async function chooseCamera() {
  error.value = ''
  try {
    const selected = await captureOrChooseMealPhoto()
    setPreview(selected.blob, selected.fileName)
  } catch (cause) {
    if (String(cause).toLowerCase().includes('cancel')) return
    error.value = cause instanceof Error ? cause.message : '打开相机失败'
  }
}
function chooseFile(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  if (!file.type.startsWith('image/')) { error.value = '请选择餐食照片'; return }
  setPreview(file, file.name)
}
function applyResult(value: Record<string, any>) {
  analysis.value = value
  Object.assign(form, {
    dish_name: String(value.dish_name || ''), portion: String(value.portion || ''), cooking_method: String(value.cooking_method || ''),
    weight_g: Number(value.weight_g ?? value.estimated_weight_g ?? 0), calories: Number(value.calories || 0),
    protein: Number(value.protein || 0), carbs: Number(value.carbs || 0), fat: Number(value.fat || 0), fiber: Number(value.fiber || 0),
  })
}
function buildCorrectionPayload() {
  return {
    dish_name: form.dish_name.trim(), portion: form.portion, cooking_method: form.cooking_method,
    weight_g: Number(form.weight_g), calories: Number(form.calories), protein: Number(form.protein),
    carbs: Number(form.carbs), fat: Number(form.fat), fiber: Number(form.fiber),
  }
}
function correctionSignature(payload: ReturnType<typeof buildCorrectionPayload>) {
  return JSON.stringify(payload)
}
async function runJob(existing = false) {
  if (loading.value) return
  if (!photo.value && !asset.value && !existing) { await chooseCamera(); return }
  stopped = false; loading.value = true; error.value = ''; analysis.value = null
  let refreshes = 0
  try {
    if (!auth.accessToken) throw new Error('请先使用微信登录')
    if (!asset.value) {
      status.value = '正在安全上传餐食照片'
      asset.value = await uploadCloudMedia(auth.accessToken, photo.value!, photoName.value, 'image', uploadRequestId.value, 'food_analysis') as MediaAsset
    }
    if (!jobId.value) {
      status.value = '正在创建餐食识别任务'
      const created = await createFoodJob(auth.accessToken, asset.value.media_id)
      jobId.value = Number(created.job_id)
      writeUserJob('hm-food-job', auth.user?.id, { mediaId: asset.value.media_id, fileId: asset.value.cloud_file_id, jobId: jobId.value })
    }
    const deadline = Date.now() + 300_000
    let current: Record<string, any> = {}
    let delay = 1000
    while (Date.now() < deadline && !stopped) {
      current = await readFoodJob(auth.accessToken, jobId.value!)
      const stage = current.status || 'queued'
      status.value = stage === 'waiting_source_refresh' ? '正在刷新安全下载地址' : `餐食分析中${current.progress ? ` · ${current.progress}%` : ''}`
      if (current.error_code === 'media_url_expired' && refreshes < 2) {
        status.value = '正在续期照片访问地址'
        await refreshCloudMedia(auth.accessToken, asset.value!.media_id, asset.value!.cloud_file_id)
        refreshes += 1
        continue
      }
      if (['done', 'completed', 'succeeded'].includes(stage)) break
      if (['failed', 'cancelled'].includes(stage)) throw new Error(current.error || '识别没有完成，可以手动填写饮食记录')
      await new Promise(resolve => window.setTimeout(resolve, delay))
      await waitForAppForeground(pageLifetime.signal)
      if (stopped) return
      delay = Math.min(5000, Math.round(delay * 1.3))
    }
    if (stopped) return
    if (!['done', 'completed', 'succeeded'].includes(current.status)) throw new Error('任务仍在处理中，请稍后回来查看')
    const summary = current.result || {}
    if (!summary.analysis_id) throw new Error(summary.message || '这张照片暂时无法识别，请手动记录')
    const detail = await readFoodAnalysis(auth.accessToken, Number(summary.analysis_id))
    if (detail.status === 'record_deleted') throw new Error('对应的饮食记录已删除，请重新选择照片')
    if (detail.status === 'finalized') {
      saved.value = true; status.value = '这份餐食已保存'; clearUserJob('hm-food-job'); return
    }
    applyResult({ ...summary, ...(detail.initial || {}), ...(detail.corrected || {}), analysis_id: summary.analysis_id })
    lastCorrectedSignature.value = detail.corrected
      ? correctionSignature(buildCorrectionPayload())
      : ''
    status.value = '请核对估算，再确认保存'
  } catch (cause) {
    if (!stopped) error.value = cause instanceof Error ? cause.message : '餐食识别暂时不可用'
  } finally { if (!stopped) loading.value = false }
}
async function saveMeal() {
  const current = analysis.value
  if (!current || !current.analysis_id || loading.value || !form.dish_name.trim()) return
  if (needsCarefulReview.value && !manualMeal.value && !window.confirm('这次估算存在不确定项。请按实际份量核对菜名和营养数值；确认继续保存？')) return
  if (!window.confirm(`确认将“${form.dish_name.trim()}”作为${mealType.value === 'breakfast' ? '早餐' : mealType.value === 'lunch' ? '午餐' : mealType.value === 'dinner' ? '晚餐' : '加餐'}保存到饮食记录？`)) return
  loading.value = true; error.value = ''; status.value = '正在保存你确认的记录'
  try {
    const payload = buildCorrectionPayload()
    const signature = correctionSignature(payload)
    if (signature !== lastCorrectedSignature.value) {
      await correctFoodAnalysis(auth.accessToken, Number(current.analysis_id), payload)
      lastCorrectedSignature.value = signature
    }
    await finalizeFoodAnalysis(auth.accessToken, Number(current.analysis_id), mealType.value)
    saved.value = true; status.value = '已保存到饮食记录'; clearUserJob('hm-food-job')
  } catch (cause) { error.value = cause instanceof Error ? cause.message : '保存没有完成，请重试' }
  finally { loading.value = false }
}
function openManual() { void router.push('/records/diet') }
function startAnotherMeal() {
  saved.value = false; analysis.value = null; lastCorrectedSignature.value = ''; asset.value = null; jobId.value = null
  photo.value = null; photoName.value = ''; status.value = ''; error.value = ''
  if (photoUrl.value) URL.revokeObjectURL(photoUrl.value)
  photoUrl.value = ''
  void chooseCamera()
}
onMounted(() => {
  chooseMealDefault()
  const pointer = readUserJob('hm-food-job', auth.user?.id)
  if (pointer && auth.accessToken) {
    asset.value = { media_id: Number(pointer.mediaId), cloud_file_id: String(pointer.fileId || '') }
    jobId.value = Number(pointer.jobId)
    if (Number.isFinite(asset.value.media_id) && Number.isFinite(jobId.value)) void runJob(true)
    else clearUserJob('hm-food-job')
  }
})
onBeforeUnmount(() => { stopped = true; pageLifetime.abort(); if (photoUrl.value) URL.revokeObjectURL(photoUrl.value) })
</script>

<template>
  <section class="page scan-page">
    <header class="page-heading"><button class="back-button" aria-label="返回" @click="router.back()">‹</button><div><p class="eyebrow">餐食记录</p><h1>拍照记录餐食</h1></div></header>
    <section class="card-surface intro"><p>拍摄或选择一张餐食照片。识别结果仅供参考，核对后再保存。</p></section>
    <section class="card-surface photo-card">
      <img v-if="photoUrl" :src="photoUrl" alt="待识别的餐食照片" />
      <div v-else class="empty-photo"><span>◉</span><b>选择一张餐食照片</b><small>清晰、完整的照片更便于核对</small></div>
      <div class="photo-actions"><button class="primary" :disabled="loading" @click="chooseCamera">打开相机或相册</button><button :disabled="loading" @click="chooser?.click()">选择照片</button><input ref="chooser" type="file" accept="image/jpeg,image/png,image/webp" capture="environment" hidden @change="chooseFile" /></div>
    </section>
    <button v-if="!analysis && !saved" class="wide-primary" :disabled="loading || (!photo && !asset)" @click="runJob()">{{ loading ? '处理中…' : '开始识别' }}</button>
    <div v-if="loading || status" class="card-surface progress-card"><span class="spinner" :class="{ active: loading }" /><span>{{ status || '正在处理' }}</span></div>
    <p v-if="error" class="error-card">{{ error }}</p>
    <button v-if="error && !analysis" class="manual-link" @click="openManual">改为手动记录饮食</button>
    <section v-if="analysis && !saved" class="card-surface result-card">
      <div class="result-heading"><div><span class="eyebrow">估算结果 · 请核对</span><h2>{{ form.dish_name || '餐食估算' }}</h2></div><span class="confidence">参考度 {{ confidence }}%</span></div>
      <p class="caveat">{{ analysis.portion_basis || '图片不包含比例尺，份量和营养值需要按实际情况核对。' }}</p>
      <p v-if="analysis.uncertainty_reasons?.length" class="caveat">{{ analysis.uncertainty_reasons.join('；') }}</p>
      <fieldset class="result-fields" :disabled="loading">
      <div class="form-grid">
        <label>菜名<input v-model="form.dish_name" maxlength="120" /></label><label>份量<input v-model="form.portion" maxlength="120" placeholder="例如一碗、约一掌心" /></label>
        <label>烹调方式<input v-model="form.cooking_method" maxlength="120" placeholder="例如清蒸、煎制" /></label><label>估计重量（克）<input v-model.number="form.weight_g" type="number" min="0" max="5000" /></label>
        <label>热量（千卡）<input v-model.number="form.calories" type="number" min="0" max="5000" /></label><label>蛋白质（克）<input v-model.number="form.protein" type="number" min="0" max="500" /></label>
        <label>碳水（克）<input v-model.number="form.carbs" type="number" min="0" max="1000" /></label><label>脂肪（克）<input v-model.number="form.fat" type="number" min="0" max="500" /></label>
        <label>膳食纤维（克）<input v-model.number="form.fiber" type="number" min="0" max="200" /></label>
      </div>
      <label class="meal-select">餐次<select v-model="mealType"><option value="breakfast">早餐</option><option value="lunch">午餐</option><option value="dinner">晚餐</option><option value="snack">加餐</option></select></label>
      <label class="review-check"><input v-model="manualMeal" type="checkbox" />我已按实际餐食核对上述估算</label>
      <button class="wide-primary" :disabled="loading || !manualMeal || !form.dish_name.trim()" @click="saveMeal">{{ loading ? '保存中…' : '确认并保存饮食记录' }}</button>
      </fieldset>
    </section>
    <section v-if="saved" class="card-surface saved-card"><span>✓</span><h2>{{ status || '已保存' }}</h2><p>这条记录已进入你的饮食明细，可以继续补充其他健康记录。</p><button class="primary" @click="router.push('/records/diet')">查看饮食记录</button><button @click="startAnotherMeal">再记一餐</button></section>
    <button class="manual-link" @click="openManual">不拍照，手动填写</button>
  </section>
</template>

<style scoped>
.scan-page{padding:8px 0 24px}.page-heading{display:flex;align-items:center;gap:9px;margin-bottom:12px}.page-heading h1{margin:3px 0 0;font-size:22px}.back-button{width:35px;height:35px;border:0;border-radius:50%;background:#fff;font-size:24px}.intro{padding:12px;margin-bottom:10px}.intro p,.caveat{margin:0;color:#737a70;font-size:10px;line-height:1.6}.photo-card{padding:10px}.photo-card>img{display:block;width:100%;max-height:340px;object-fit:contain;border-radius:13px;background:#f1f2eb}.empty-photo{display:flex;min-height:220px;flex-direction:column;align-items:center;justify-content:center;gap:8px;border-radius:13px;background:#f3f4ed;color:#68705e}.empty-photo span{font-size:34px}.empty-photo b{font-size:13px}.empty-photo small{font-size:10px}.photo-actions{display:flex;gap:8px;margin-top:9px}.photo-actions button,.primary{flex:1;min-height:38px;border:0;border-radius:11px;background:#e9efd9;color:#506336;font-size:10px;font-weight:700}.photo-actions .primary,.primary{background:#c4e267;color:#1c2713}.wide-primary{width:100%;min-height:43px;margin-top:10px;border:0;border-radius:13px;background:#c4e267;color:#1c2713;font-size:11px;font-weight:750}.wide-primary:disabled,.photo-actions button:disabled{opacity:.52}.result-fields{min-width:0;margin:0;padding:0;border:0}.progress-card{display:flex;align-items:center;gap:10px;margin-top:9px;padding:12px;color:#506336;font-size:10px}.spinner{width:15px;height:15px;border:2px solid #dce3ce;border-top-color:#65812c;border-radius:50%}.spinner.active{animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}.error-card{margin-top:9px;padding:10px;color:#9b382d;font-size:10px}.result-card{margin-top:11px;padding:14px}.result-heading{display:flex;align-items:center;justify-content:space-between;gap:8px}.result-heading h2{margin:4px 0 0;font-size:16px}.confidence{padding:5px 7px;border-radius:8px;background:#f1f2eb;color:#68705e;font-size:9px}.caveat{margin-top:8px}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px}.form-grid label,.meal-select{display:flex;flex-direction:column;gap:5px;color:#596052;font-size:9px}.form-grid input,.meal-select select{width:100%;min-width:0;height:36px;padding:0 8px;border:1px solid #e7e9df;border-radius:9px;background:#fbfbf8;font-size:10px}.meal-select{margin-top:10px}.review-check{display:flex;align-items:center;gap:7px;margin-top:12px;color:#47513d;font-size:10px}.review-check input{accent-color:#6c8a35}.saved-card{display:flex;align-items:center;flex-direction:column;margin-top:12px;padding:20px;text-align:center}.saved-card>span{display:grid;width:38px;height:38px;place-items:center;border-radius:50%;background:#e9efd9;color:#506336;font-size:22px}.saved-card h2{margin:8px 0 4px;font-size:15px}.saved-card p{color:#737a70;font-size:10px;line-height:1.5}.saved-card button{width:100%;min-height:38px;margin-top:7px;border:0;border-radius:10px;background:#f1f2eb;color:#435035;font-size:10px}.manual-link{display:block;margin:12px auto;padding:6px;border:0;background:none;color:#637548;font-size:10px}
</style>
