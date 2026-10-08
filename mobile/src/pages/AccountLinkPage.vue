<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { readCurrentUser } from '../services/api'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const code = ref('')
const error = ref('')
const completed = ref(false)
const linked = computed(() => auth.user?.linked_providers?.includes('wechat_miniprogram') ?? false)
const checking = ref(true)

onMounted(async () => {
  try {
    auth.user = await readCurrentUser(auth.accessToken)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '账号状态暂时无法读取'
  } finally {
    checking.value = false
  }
})

async function completeLink() {
  const normalized = code.value.trim().toUpperCase()
  if (!/^[0-9A-F]{32}$/.test(normalized)) {
    error.value = '请输入微信小程序“设置”中生成的 32 位关联码。'
    return
  }
  error.value = ''
  try {
    await auth.linkMiniProgramAccount(normalized)
    completed.value = true
    window.setTimeout(() => void router.replace('/home'), 700)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '账号关联失败，请确认关联码仍在有效期内。'
  }
}

async function unlink() {
  if (!window.confirm('解绑后，此 Android 会话将退出。小程序账号和健康记录会保留。')) return
  error.value = ''
  try {
    await auth.unlinkWechatMobile()
    await router.replace('/home')
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '解绑失败，请稍后重试。'
  }
}
</script>

<template>
  <section class="page account-link-page">
    <header class="account-link-heading">
      <button class="back-button" aria-label="返回" @click="router.back()">‹</button>
      <div><p class="eyebrow">账号迁移</p><h1>关联微信小程序账号</h1></div>
    </header>

    <section class="card-surface account-link-card">
      <div class="account-link-icon">↔</div>
      <template v-if="checking">
        <h2>正在读取账号状态</h2>
        <p>请稍候。</p>
      </template>
      <template v-else-if="linked">
        <h2>已关联微信小程序账号</h2>
        <p>Android 与微信小程序正在使用同一账号和健康记录。解绑后会退出 Android 登录，小程序账号和记录会保留。</p>
        <button class="button-primary account-link-submit" :disabled="auth.loading" @click="unlink">
          {{ auth.loading ? '正在解绑…' : '解绑 Android 微信登录' }}
        </button>
        <p v-if="error" class="error-copy">{{ error }}</p>
      </template>
      <template v-else>
      <h2>把已有记录带到 Android</h2>
      <p>先在微信小程序的“设置”里生成关联码，再填入这里。关联后，Android 将使用小程序账号及其已有健康记录。</p>
      <label class="field-label" for="account-link-code">一次性关联码</label>
      <input
        id="account-link-code"
        v-model="code"
        inputmode="text"
        autocomplete="off"
        autocapitalize="characters"
        maxlength="32"
        placeholder="32 位字母与数字"
        @input="code = code.replace(/[^0-9a-f]/gi, '').toUpperCase()"
      />
      <button class="button-primary account-link-submit" :disabled="auth.loading || completed" @click="completeLink">
        {{ completed ? '已关联，正在返回…' : auth.loading ? '正在验证…' : '确认关联账号' }}
      </button>
      <p v-if="error" class="error-copy">{{ error }}</p>
      <p class="account-link-note">关联码 10 分钟后失效且只能使用一次。系统不会自动合并 Android 账号中已有的健康数据。</p>
      </template>
    </section>
  </section>
</template>

<style scoped>
.account-link-heading { display:flex; align-items:center; gap:10px; padding:8px 0 18px; }
.account-link-heading h1 { margin:3px 0 0; font-size:21px; }
.back-button { width:34px; height:34px; border:0; border-radius:50%; background:#fff; font-size:24px; }
.account-link-card { padding:22px; }
.account-link-icon { display:grid; width:48px; height:48px; place-items:center; border-radius:16px; background:var(--accent-soft); color:var(--brand-ink); font-size:24px; }
.account-link-card h2 { margin:18px 0 8px; font-size:17px; }
.account-link-card > p:not(.error-copy) { color:var(--muted); font-size:12px; line-height:1.65; }
.account-link-card .field-label { margin-top:20px; }
.account-link-card input { width:100%; height:46px; padding:0 12px; border:1px solid var(--divider); border-radius:14px; outline:none; background:var(--page-bg); font-size:15px; letter-spacing:1.1px; }
.account-link-submit { width:100%; height:46px; margin-top:12px; border:0; border-radius:14px; background:var(--accent); font-size:13px; font-weight:750; }
.account-link-submit:disabled { opacity:.55; }
.account-link-note { margin-top:14px; font-size:10px !important; }
</style>
