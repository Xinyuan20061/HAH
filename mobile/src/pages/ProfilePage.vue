<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { readHealthProfile, readUserProfile, type HealthProfile } from '../services/api'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()
const loading = ref(true)
const error = ref('')
const user = ref<{ id: number; nickname: string; avatar_url?: string } | null>(null)
const profile = ref<HealthProfile | null>(null)
const goalLabels: Record<string, string> = { lose: '减脂', maintain: '保持健康', gain: '增肌' }

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [userResult, profileResult] = await Promise.all([
      readUserProfile(auth.accessToken),
      readHealthProfile(auth.accessToken),
    ])
    user.value = userResult
    profile.value = profileResult
    auth.user = { id: userResult.id, nickname: userResult.nickname }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '个人资料暂时无法读取'
  } finally {
    loading.value = false
  }
}

onMounted(() => void load())
</script>

<template>
  <section class="page profile-page">
    <header class="profile-heading">
      <button class="back-button" aria-label="返回" @click="router.back()">‹</button>
      <div><p class="eyebrow">个人资料</p><h1>个人健康空间</h1></div>
    </header>
    <div v-if="loading" class="card-surface state-card">正在读取个人资料…</div>
    <div v-else-if="error" class="card-surface state-card error-state"><p>{{ error }}</p><button @click="load">重试</button></div>
    <template v-else>
      <section class="card-surface identity-card">
        <div class="avatar">{{ (user?.nickname || '微').slice(0, 1) }}</div>
        <div class="identity-copy"><p class="eyebrow">当前账号</p><h2>{{ user?.nickname || '微信用户' }}</h2><span>健康记录只对当前登录账号开放</span></div>
        <button class="edit-button" aria-label="编辑资料" @click="router.push('/profile/edit')">编辑</button>
      </section>
      <section v-if="profile" class="card-surface facts-card">
        <div><strong>{{ profile.age }}</strong><span>年龄</span></div>
        <div><strong>{{ profile.height_cm }}<small> cm</small></strong><span>身高</span></div>
        <div><strong>{{ profile.weight_kg }}<small> kg</small></strong><span>体重</span></div>
      </section>
      <section class="card-surface profile-detail">
        <p class="eyebrow">当前目标</p>
        <h2>{{ profile ? goalLabels[profile.goal_type] || profile.goal_type : '完善健康档案' }}</h2>
        <p>{{ profile ? `活动水平：${profile.activity_level} · 饮食偏好：${profile.diet_preference || '未填写'}` : '填写身体资料后，健康服务可以更贴合你的目标。' }}</p>
        <button class="detail-action" @click="router.push('/profile/edit')">{{ profile ? '查看或编辑身体资料' : '完善身体资料' }}　›</button>
      </section>
      <section class="profile-links">
        <button class="card-surface profile-link" @click="router.push('/goals')"><span>健康目标</span><b>›</b></button>
        <button class="card-surface profile-link" @click="router.push('/trends')"><span>七日趋势</span><b>›</b></button>
        <button class="card-surface profile-link" @click="router.push('/settings/privacy')"><span>隐私与数据</span><b>›</b></button>
      </section>
      <p class="medical-note">健康建议不替代医生诊断。</p>
    </template>
  </section>
</template>

<style scoped>
.profile-heading { display:flex; align-items:center; gap:10px; padding:8px 0 16px; }.profile-heading h1 { margin:3px 0 0; font-size:21px; }
.back-button { width:34px; height:34px; border:0; border-radius:50%; background:#fff; font-size:24px; }
.identity-card { display:flex; align-items:center; gap:12px; padding:17px; }.avatar { display:grid; width:56px; height:56px; flex:none; place-items:center; border-radius:50%; background:var(--accent-soft); color:var(--brand-ink); font-size:24px; font-weight:800; }.identity-copy { flex:1; min-width:0; }.identity-copy h2 { margin:3px 0; overflow:hidden; font-size:17px; text-overflow:ellipsis; white-space:nowrap; }.identity-copy span { color:var(--muted); font-size:9px; }.edit-button,.detail-action { padding:8px 10px; border:0; border-radius:10px; background:var(--accent-soft); color:var(--brand-ink); font-size:10px; font-weight:700; }
.facts-card { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-top:10px; padding:15px; text-align:center; }.facts-card > div { display:flex; flex-direction:column; gap:3px; }.facts-card strong { font-size:18px; }.facts-card strong small { color:var(--muted); font-size:9px; font-weight:500; }.facts-card span { color:var(--muted); font-size:9px; }
.profile-detail { margin-top:10px; padding:16px; }.profile-detail h2 { margin:5px 0; font-size:16px; }.profile-detail > p:not(.eyebrow) { color:var(--muted); font-size:10px; line-height:1.6; }.detail-action { margin-top:4px; }
.profile-links { display:flex; flex-direction:column; gap:8px; margin-top:10px; }.profile-link { display:flex; justify-content:space-between; padding:14px; border:0; text-align:left; font-size:11px; }.profile-link b { color:var(--brand-ink); font-size:17px; }.medical-note { color:var(--muted); font-size:9px; text-align:center; }.state-card { padding:18px; color:var(--muted); font-size:12px; }.error-state { color:var(--danger); }.error-state button { padding:8px 12px; border:0; border-radius:10px; background:var(--accent-soft); }
</style>
