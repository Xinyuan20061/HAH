<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { App as NativeApp } from '@capacitor/app'
import { Capacitor, type PluginListenerHandle } from '@capacitor/core'
import { SplashScreen } from '@capacitor/splash-screen'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from './stores/auth'
import { useCompanionStore } from './stores/companion'
import { checkBackend, registerUnauthorizedHandler } from './services/api'

const auth = useAuthStore()
const companion = useCompanionStore()
const route = useRoute()
const router = useRouter()
const authMode = import.meta.env.VITE_AUTH_MODE
const appEnvironment = import.meta.env.VITE_APP_ENV
const nickname = ref('HAH 开发者')
const backendStatus = ref<'checking' | 'online' | 'offline'>('checking')
const backendMessage = ref('正在连接后端')
const dashboardOpen = ref(false)
const dashboardMode = ref<'main' | 'quick'>('main')

const dashboardActions = [
  { key: 'records', label: '记录', icon: '/generated-assets/tabbar/records-active.png', path: '/records' },
  { key: 'plan', label: '计划', icon: '/generated-assets/icons/calendar.png', path: '/plan' },
  { key: 'quick', label: '快速开始', icon: '/generated-assets/icons/add.png', mode: 'quick' as const },
  { key: 'profile', label: '我的', icon: '/generated-assets/icons/profile-card.png', path: '/profile' },
  { key: 'settings', label: '设置', icon: '/generated-assets/icons/settings.png', path: '/settings' },
]
const quickActions = [
  { key: 'scan', label: '拍照识餐', icon: '/generated-assets/icons/camera.png', path: '/scan' },
  { key: 'media', label: '动作反馈', icon: '/generated-assets/icons/activity.png', path: '/media' },
  { key: 'insights', label: '健康提醒', icon: '/generated-assets/icons/bell.png', path: '/insights' },
  { key: 'trends', label: '7 日趋势', icon: '/generated-assets/icons/trend.png', path: '/trends' },
  { key: 'goals', label: '健康目标', icon: '/generated-assets/icons/target.png', path: '/goals' },
  { key: 'report', label: '健康周报', icon: '/generated-assets/icons/report.png', path: '/report' },
  { key: 'workout', label: '训练计划', icon: '/generated-assets/icons/workout.png', path: '/workout' },
  { key: 'profile-edit', label: '身体档案', icon: '/generated-assets/icons/profile-card.png', path: '/profile/edit' },
]
let backButtonListener: PluginListenerHandle | null = null
const unregisterUnauthorizedHandler = registerUnauthorizedHandler(async () => {
  if (!auth.accessToken) return
  await auth.signOut()
  auth.error = '登录状态已过期，请重新登录后继续。'
  closeDashboard()
})
const activeTab = computed(() => route.path === '/chat' ? 'chat' : route.path === '/home' ? 'home' : '')
const environmentLabel = computed(() => import.meta.env.VITE_APP_ENV === 'production'
  ? '正式环境'
  : import.meta.env.VITE_APP_ENV === 'cloud-test' ? '云端测试' : '本地开发')

async function refreshBackend() {
  backendStatus.value = 'checking'
  backendMessage.value = '正在连接后端'
  try {
    await checkBackend()
    backendStatus.value = 'online'
    backendMessage.value = '后端已连接'
  } catch (error) {
    backendStatus.value = 'offline'
    const message = error instanceof Error ? error.message : ''
    backendMessage.value = /failed to fetch|networkerror|load failed/i.test(message)
      ? '暂时无法连接服务，请检查网络和服务地址后重试。'
      : message || '暂时无法连接服务，请稍后重试。'
  }
}

async function signIn() {
  await refreshBackend()
  if (backendStatus.value !== 'online') return
  try {
    if (import.meta.env.VITE_AUTH_MODE === 'wechat' && import.meta.env.VITE_APP_ENV !== 'local') {
      await auth.signInWithWechat()
    } else if (import.meta.env.VITE_AUTH_MODE === 'dev' && import.meta.env.VITE_APP_ENV !== 'production') {
      await auth.signInForDevelopment(nickname.value.trim() || 'HAH 开发者')
    } else {
      auth.error = '当前构建未配置有效的登录方式。'
      return
    }
  } catch {
    return
  }
  const agent = route.query.agent
  if (agent === 'steward') await router.replace('/chat?agent=steward')
}

function closeDashboard() {
  dashboardOpen.value = false
  dashboardMode.value = 'main'
}

function openDashboard() {
  if (dashboardOpen.value) {
    closeDashboard()
    return
  }
  dashboardMode.value = 'main'
  dashboardOpen.value = true
}

function handleDashboardAction(action: typeof dashboardActions[number]) {
  if (action.mode === 'quick') {
    dashboardMode.value = 'quick'
    return
  }
  if (action.path) navigateDashboard(action.path)
}

function navigateDashboard(path: string) {
  closeDashboard()
  void router.push(path)
}

function goHome() {
  closeDashboard()
  void router.push('/home')
}

function goChat(agent = 'steward') {
  closeDashboard()
  void router.push({ path: '/chat', query: { agent } })
}

async function handleHardwareBack(canGoBack: boolean) {
  const pageHandled = new Event('hah:hardware-back', { cancelable: true })
  window.dispatchEvent(pageHandled)
  if (pageHandled.defaultPrevented) return
  if (dashboardOpen.value) {
    closeDashboard()
    return
  }
  if (route.path !== '/home') {
    if (canGoBack && window.history.length > 1) await router.back()
    else await router.replace('/home')
    return
  }
  await NativeApp.minimizeApp()
}

onMounted(async () => {
  await auth.restoreSession()
  await auth.validateRestoredSession()
  void companion.restore()
  void refreshBackend()
  if (Capacitor.getPlatform() === 'android') {
    backButtonListener = await NativeApp.addListener('backButton', ({ canGoBack }) => {
      void handleHardwareBack(canGoBack)
    })
  }
  void nextTick(() => requestAnimationFrame(() => {
    void SplashScreen.hide().catch(() => undefined)
  }))
})

onBeforeUnmount(() => {
  unregisterUnauthorizedHandler()
  void backButtonListener?.remove()
})
</script>

<template>
  <div class="app-frame">
    <main v-if="!auth.authenticated" class="login-page">
      <div class="brand-lockup">
        <img :src="'/generated-assets/icons/spark.png'" alt="" class="brand-icon" />
        <span>HealthMate</span>
      </div>
      <section class="login-card card-surface">
        <p class="eyebrow">健康管理</p>
        <h1>把健康管理，<br />变成每天做得到的小事。</h1>
        <p class="muted login-copy">管理健康记录、饮食和训练计划，按自己的节奏开始。</p>
        <div v-if="appEnvironment !== 'production'" class="environment-badge"><span class="status-dot" :class="backendStatus" />{{ environmentLabel }}</div>
        <template v-if="authMode === 'dev' && appEnvironment !== 'production'">
          <label class="field-label" for="dev-nickname">测试昵称</label>
          <input id="dev-nickname" v-model="nickname" autocomplete="nickname" maxlength="64" placeholder="输入一个昵称" />
        </template>
        <button class="button-primary login-button" :disabled="auth.loading || auth.restoring || backendStatus === 'checking'" @click="signIn">
          {{ auth.restoring ? '正在恢复会话…' : auth.loading ? '正在登录…' : backendStatus === 'checking' ? '正在连接…' : authMode === 'wechat' ? '微信登录' : '进入开发版' }}
        </button>
        <p v-if="auth.error || backendStatus === 'offline'" class="error-copy">{{ auth.error || backendMessage }}</p>
        <p class="privacy-copy">{{ authMode === 'wechat' ? '使用微信授权登录，无需在此输入微信密码。' : '这是非生产开发登录；访问令牌只保存在本次运行内存中。' }}</p>
      </section>
      <div class="login-foot muted">每天一点进步，慢慢看见变化。</div>
    </main>

    <template v-else>
      <div v-if="appEnvironment !== 'production'" class="top-status">
        <span class="status-indicator"><span class="status-dot" :class="backendStatus" />{{ backendStatus === 'online' ? '服务已连接' : backendStatus === 'checking' ? '连接中' : '连接中断' }}</span>
        <button class="environment-chip" @click="refreshBackend">{{ environmentLabel }} · 重试</button>
      </div>
      <div class="route-surface"><RouterView /></div>
      <nav class="bottom-nav" aria-label="主导航">
        <button class="nav-item" :class="{ selected: activeTab === 'home' }" @click="goHome">
          <img :src="`/generated-assets/tabbar/${companion.agentId === 'xiaokang' ? 'wellness-space' : 'gym'}${activeTab === 'home' ? '-active' : ''}.png`" alt="" />
          <span>{{ companion.space }}</span>
        </button>
        <button class="dashboard-slot" :class="{ open: dashboardOpen }" :aria-expanded="dashboardOpen" :aria-label="dashboardOpen ? '关闭仪表盘菜单' : '打开仪表盘菜单'" @click="openDashboard">
          <span class="dashboard-orb"><img :src="`/generated-assets/tabbar/dashboard${dashboardOpen ? '-active' : ''}.png`" alt="" /></span>
          <span class="dashboard-label">仪表盘</span>
        </button>
        <button class="nav-item" :class="{ selected: activeTab === 'chat' }" @click="goChat()">
          <img :src="`/generated-assets/tabbar/steward${activeTab === 'chat' ? '-active' : ''}.png`" alt="" />
          <span>小管家</span>
        </button>
      </nav>

      <div v-if="dashboardOpen" class="dashboard-backdrop" @click.self="closeDashboard" />
      <nav v-if="dashboardOpen && dashboardMode === 'main'" class="dashboard-wheel" :class="{ open: dashboardOpen }" aria-label="仪表盘菜单">
        <button
          v-for="(action, index) in dashboardActions"
          :key="action.key"
          class="wheel-item"
          :class="[`wheel-${action.key}`, `wheel-delay-${index}`]"
          :aria-label="action.label"
          @click="handleDashboardAction(action)"
        >
          <span class="wheel-orb" :class="{ lime: action.key === 'quick' }"><img :src="action.icon" alt="" /></span>
          <span class="wheel-label">{{ action.label }}</span>
        </button>
      </nav>
      <section v-if="dashboardOpen && dashboardMode === 'quick'" class="quick-sheet card-surface" aria-label="快速开始">
        <div class="sheet-heading">
          <button class="quick-back" aria-label="返回仪表盘" @click="dashboardMode = 'main'">‹</button>
          <div><p class="eyebrow">快速开始</p><h2>现在想做什么？</h2></div>
          <button class="close-button" aria-label="关闭" @click="closeDashboard">×</button>
        </div>
        <div class="quick-grid">
          <button v-for="action in quickActions" :key="action.key" class="quick-item" @click="navigateDashboard(action.path)">
            <span class="quick-orb"><img :src="action.icon" alt="" /></span>
            <span>{{ action.label }}</span>
          </button>
        </div>
      </section>
    </template>
  </div>
</template>
