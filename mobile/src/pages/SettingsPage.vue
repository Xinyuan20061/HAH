<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { readCurrentUser } from '../services/api'
import { useAuthStore } from '../stores/auth'
import { useVoiceStore } from '../stores/voice'

const router = useRouter()
const auth = useAuthStore()
const voicePreference = useVoiceStore()
const loading = ref(true)
const providers = ref<string[]>([])
const error = ref('')
const signingOut = ref(false)
const accountStatus = computed(() => {
  const miniProgramLinked = providers.value.includes('wechat_miniprogram')
  const androidLinked = providers.value.includes('wechat_mobile')
  if (miniProgramLinked && androidLinked) return '微信小程序已关联'
  if (miniProgramLinked) return '微信小程序账号'
  if (androidLinked) return 'Android 微信登录'
  if (providers.value.includes('development')) return '开发测试账号'
  return '当前账号'
})

async function signOut() {
  signingOut.value = true
  error.value = ''
  try {
    await auth.signOut()
    await router.replace('/home')
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '退出登录失败，请重试。'
  } finally {
    signingOut.value = false
  }
}

onMounted(async () => {
  await voicePreference.restore()
  try {
    const user = await readCurrentUser(auth.accessToken)
    providers.value = user.linked_providers || []
    auth.user = user
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '账号状态暂时无法读取'
  } finally {
    loading.value = false
  }
})

const rows = [
  { title: '拍照识餐', caption: '安全上传、识别估算并由你确认保存', path: '/scan', icon: '◉' },
  { title: '动作视频分析', caption: '上传训练视频、复核画面并提交反馈', path: '/media', icon: '▷' },
  { title: '健康记录', caption: '饮食与运动明细', path: '/records', icon: '◒' },
  { title: '健康提醒', caption: '查看数据产生的提醒并反馈', path: '/insights', icon: '✳' },
  { title: '每周报告', caption: '按已记录事实回顾本周', path: '/report', icon: '▤' },
  { title: '状态与下一步', caption: '查看近期状态和建议', path: '/state', icon: '◎' },
  { title: '健康能力', caption: '管理健康数据的使用范围', path: '/settings/capabilities', icon: '◇' },
  { title: '运行与评测', caption: '查看服务表现与评测记录', path: '/evaluation', icon: '▥' },
  { title: '个人策略', caption: '查看并复核个人训练周期', path: '/policy', icon: '↻' },
  { title: 'AI 与语音设置', caption: '设置文字回复和语音播报', path: '/settings/ai', icon: '◉' },
  { title: '个人资料', caption: '昵称、身体资料与健康目标', path: '/profile', icon: '◉' },
  { title: '健康目标', caption: '设置饮水、睡眠、运动与记录目标', path: '/goals', icon: '◎' },
  { title: '七日趋势', caption: '查看已经记录的健康变化', path: '/trends', icon: '↗' },
  { title: '关联微信小程序账号', caption: '同步原账号中的健康记录', path: '/account-link', icon: '↔' },
  { title: '隐私与数据', caption: '查看、导出或删除个人数据', path: '/settings/privacy', icon: '◇' },
]
</script>

<template>
  <section class="page settings-page">
    <header class="settings-heading">
      <button class="back-button" aria-label="返回" @click="router.back()">‹</button>
      <div><p class="eyebrow">偏好与账号</p><h1>设置</h1></div>
    </header>
    <section class="card-surface account-card">
      <div class="avatar">{{ (auth.user?.nickname || '微').slice(0, 1) }}</div>
      <div><p class="eyebrow">当前账号</p><b>{{ auth.user?.nickname || '微信用户' }}</b><small>{{ loading ? '正在读取关联状态…' : accountStatus }}</small></div>
    </section>
    <section class="card-surface voice-preference">
      <div><b>语音回复后自动朗读</b><small>关闭后，语音输入仍可使用；回复可以手动点按朗读。</small></div>
      <label class="switch" aria-label="语音回复后自动朗读">
        <input type="checkbox" :checked="voicePreference.autoplay" @change="voicePreference.setAutoplay(($event.target as HTMLInputElement).checked)" />
        <span>{{ voicePreference.autoplay ? '开启' : '关闭' }}</span>
      </label>
    </section>
    <div v-if="error" class="card-surface error-card"><p>{{ error }}</p><button @click="router.push('/home')">返回首页</button></div>
    <nav class="settings-list" aria-label="设置菜单">
      <button v-for="row in rows" :key="row.path" class="card-surface settings-row" @click="router.push(row.path)">
        <span class="row-icon">{{ row.icon }}</span>
        <span class="row-copy"><b>{{ row.title }}</b><small>{{ row.caption }}</small></span>
        <span class="chevron">›</span>
      </button>
    </nav>
    <button class="sign-out-button" :disabled="signingOut" @click="signOut">{{ signingOut ? '正在退出…' : '退出登录' }}</button>
  </section>
</template>

<style scoped>
.settings-heading { display:flex; align-items:center; gap:10px; padding:8px 0 16px; }.settings-heading h1 { margin:3px 0 0; font-size:21px; }
.back-button { width:34px; height:34px; border:0; border-radius:50%; background:#fff; font-size:24px; }
.account-card { display:flex; align-items:center; gap:12px; padding:15px; }.avatar { display:grid; width:48px; height:48px; place-items:center; border-radius:50%; background:var(--accent-soft); color:var(--brand-ink); font-size:21px; font-weight:800; }.account-card > div:last-child { display:flex; flex-direction:column; gap:4px; }.account-card b { font-size:13px; }.account-card small { color:var(--muted); font-size:9px; }
.voice-preference { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-top:10px; padding:12px 14px; }.voice-preference > div { display:flex; flex-direction:column; gap:4px; }.voice-preference b { font-size:11px; }.voice-preference small { max-width:220px; color:var(--muted); font-size:9px; line-height:1.5; }.switch { display:flex; align-items:center; gap:7px; color:var(--brand-ink); font-size:10px; white-space:nowrap; }.switch input { width:17px; height:17px; accent-color:#506336; }
.settings-list { display:flex; flex-direction:column; gap:8px; margin-top:12px; }.settings-row { display:flex; width:100%; align-items:center; gap:11px; padding:12px; border:0; text-align:left; }.row-icon { display:grid; width:36px; height:36px; flex:none; place-items:center; border-radius:12px; background:var(--accent-soft); color:var(--brand-ink); font-size:17px; }.row-copy { display:flex; flex:1; flex-direction:column; gap:4px; }.row-copy b { font-size:11px; }.row-copy small { color:var(--muted); font-size:9px; }.chevron { color:var(--brand-ink); font-size:21px; }.error-card { margin-top:10px; padding:12px; color:var(--danger); font-size:10px; }.error-card button { border:0; border-radius:9px; padding:7px 10px; background:var(--accent-soft); }
.sign-out-button { display:block; width:100%; margin-top:16px; padding:13px; border:1px solid var(--divider); border-radius:15px; color:#9d3730; background:var(--surface); font-size:11px; font-weight:700; }.sign-out-button:disabled { opacity:.58; }
</style>
