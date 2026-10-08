<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { readHealthProfile, readUserProfile, saveHealthProfile, updateUserNickname, type HealthProfile } from '../services/api'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const nickname = ref('')
const form = reactive<HealthProfile>({
  gender: 'unspecified', age: 20, height_cm: 170, weight_kg: 65,
  goal_type: 'maintain', activity_level: 'moderate', diet_preference: '', allergies: '',
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [user, profile] = await Promise.all([readUserProfile(auth.accessToken), readHealthProfile(auth.accessToken)])
    nickname.value = user.nickname || ''
    if (profile) Object.assign(form, profile)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '资料暂时无法读取'
  } finally {
    loading.value = false
  }
}

function numberField(key: 'age' | 'height_cm' | 'weight_kg', value: string) {
  form[key] = Number(value)
}

async function save() {
  if (saving.value) return
  if (!nickname.value.trim()) {
    error.value = '昵称不能为空。'
    return
  }
  saving.value = true
  error.value = ''
  try {
    await saveHealthProfile(auth.accessToken, { ...form })
    await updateUserNickname(auth.accessToken, nickname.value.trim())
    auth.user = { id: auth.user?.id || 0, nickname: nickname.value.trim() }
    await router.replace('/profile')
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '资料没有保存，请检查填写内容后重试。'
  } finally {
    saving.value = false
  }
}

onMounted(() => void load())
</script>

<template>
  <section class="page profile-edit-page">
    <header class="edit-heading">
      <button class="back-button" aria-label="返回" @click="router.back()">‹</button>
      <div><p class="eyebrow">个人资料</p><h1>编辑个人资料</h1></div>
    </header>
    <div v-if="loading" class="card-surface state-card">正在读取资料…</div>
    <form v-else class="card-surface edit-form" @submit.prevent="save">
      <label>昵称<input v-model="nickname" maxlength="30" autocomplete="nickname" /></label>
      <fieldset><legend>性别（可选）</legend><div class="choice-row"><button v-for="item in [{key:'unspecified',label:'不填写'},{key:'female',label:'女'},{key:'male',label:'男'}]" :key="item.key" type="button" :class="{ selected: form.gender === item.key }" @click="form.gender = item.key">{{ item.label }}</button></div></fieldset>
      <label>年龄<input type="number" min="12" max="100" :value="form.age" @input="numberField('age', ($event.target as HTMLInputElement).value)" /></label>
      <label>身高（cm）<input type="number" min="100" max="230" step="0.1" :value="form.height_cm" @input="numberField('height_cm', ($event.target as HTMLInputElement).value)" /></label>
      <label>体重（kg）<input type="number" min="25" max="300" step="0.1" :value="form.weight_kg" @input="numberField('weight_kg', ($event.target as HTMLInputElement).value)" /></label>
      <fieldset><legend>当前目标</legend><div class="choice-row"><button v-for="item in [{key:'lose',label:'减脂'},{key:'maintain',label:'保持健康'},{key:'gain',label:'增肌'}]" :key="item.key" type="button" :class="{ selected: form.goal_type === item.key }" @click="form.goal_type = item.key">{{ item.label }}</button></div></fieldset>
      <fieldset><legend>日常活动量</legend><div class="choice-row"><button v-for="item in [{key:'low',label:'较少'},{key:'moderate',label:'中等'},{key:'high',label:'较高'}]" :key="item.key" type="button" :class="{ selected: form.activity_level === item.key }" @click="form.activity_level = item.key">{{ item.label }}</button></div></fieldset>
      <label>饮食偏好<input v-model="form.diet_preference" maxlength="255" placeholder="例如：素食" /></label>
      <label>过敏信息<input v-model="form.allergies" maxlength="255" placeholder="没有可留空" /></label>
      <p v-if="error" class="error-copy">{{ error }}</p>
      <button class="save-button" type="submit" :disabled="saving">{{ saving ? '正在保存…' : '保存资料' }}</button>
    </form>
  </section>
</template>

<style scoped>
.edit-heading { display:flex; align-items:center; gap:10px; padding:8px 0 16px; }.edit-heading h1 { margin:3px 0 0; font-size:21px; }
.back-button { width:34px; height:34px; border:0; border-radius:50%; background:#fff; font-size:24px; }
.edit-form { display:flex; flex-direction:column; gap:12px; padding:16px; }.edit-form label { display:flex; flex-direction:column; gap:6px; color:var(--muted); font-size:10px; }.edit-form input { width:100%; height:40px; padding:0 10px; border:1px solid var(--divider); border-radius:11px; outline:none; background:var(--page-bg); color:var(--ink); font-size:12px; }
fieldset { min-width:0; margin:0; padding:0; border:0; }legend { margin-bottom:6px; color:var(--muted); font-size:10px; }.choice-row { display:flex; gap:6px; }.choice-row button { flex:1; min-height:34px; padding:6px; border:0; border-radius:10px; color:var(--muted); background:var(--page-bg); font-size:9px; }.choice-row button.selected { color:var(--ink); background:var(--accent); font-weight:750; }
.save-button { height:44px; margin-top:4px; border:0; border-radius:13px; background:var(--accent); font-weight:750; }.save-button:disabled { opacity:.5; }.error-copy { margin:0; color:var(--danger); font-size:10px; }.state-card { padding:18px; color:var(--muted); font-size:12px; }
</style>
