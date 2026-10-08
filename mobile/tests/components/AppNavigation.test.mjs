// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  route: { path: '/home', query: {} },
  router: { push: vi.fn(), replace: vi.fn(), back: vi.fn() },
  auth: {
    accessToken: 'test-token',
    authenticated: true,
    restoring: false,
    error: '',
    restoreSession: vi.fn(),
    validateRestoredSession: vi.fn(),
    signOut: vi.fn(),
  },
  companion: { agentId: 'xiaojian', space: '健身房', restore: vi.fn() },
  checkBackend: vi.fn(),
}))

vi.mock('@capacitor/app', () => ({ App: { addListener: vi.fn(async () => ({ remove: vi.fn() })), minimizeApp: vi.fn() } }))
vi.mock('@capacitor/core', () => ({ Capacitor: { getPlatform: () => 'web' } }))
vi.mock('@capacitor/splash-screen', () => ({ SplashScreen: { hide: vi.fn(async () => undefined) } }))
vi.mock('vue-router', () => ({ useRoute: () => mocks.route, useRouter: () => mocks.router }))
vi.mock('../../src/services/api', () => ({
  checkBackend: mocks.checkBackend,
  registerUnauthorizedHandler: () => vi.fn(),
}))
vi.mock('../../src/stores/auth', () => ({ useAuthStore: () => mocks.auth }))
vi.mock('../../src/stores/companion', () => ({ useCompanionStore: () => mocks.companion }))

import App from '../../src/App.vue'

describe('main dashboard navigation', () => {
  let wrapper

  beforeEach(() => {
    mocks.route.path = '/home'
    mocks.route.query = {}
    mocks.router.push.mockReset()
    mocks.router.replace.mockReset()
    mocks.auth.authenticated = true
    mocks.auth.accessToken = 'test-token'
    mocks.auth.restoreSession.mockReset().mockResolvedValue(undefined)
    mocks.auth.validateRestoredSession.mockReset().mockResolvedValue(undefined)
    mocks.companion.restore.mockReset().mockResolvedValue(undefined)
    mocks.checkBackend.mockReset().mockResolvedValue(undefined)
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
  })

  it('shows the five mini-program dashboard actions and all eight quick actions', async () => {
    wrapper = mount(App, { global: { stubs: { RouterView: true } } })
    await wrapper.find('.dashboard-slot').trigger('click')

    expect(wrapper.findAll('.wheel-item')).toHaveLength(5)
    expect(wrapper.text()).toContain('记录')
    expect(wrapper.text()).toContain('计划')
    expect(wrapper.text()).toContain('快速开始')
    expect(wrapper.text()).toContain('我的')
    expect(wrapper.text()).toContain('设置')

    await wrapper.find('.wheel-quick').trigger('click')
    expect(wrapper.findAll('.quick-item')).toHaveLength(8)
    expect(wrapper.text()).toContain('拍照识餐')
    expect(wrapper.text()).toContain('动作反馈')
    expect(wrapper.text()).toContain('健康提醒')
    expect(wrapper.text()).toContain('7 日趋势')
    expect(wrapper.text()).toContain('健康目标')
    expect(wrapper.text()).toContain('健康周报')
    expect(wrapper.text()).toContain('训练计划')
    expect(wrapper.text()).toContain('身体档案')

    await wrapper.findAll('.quick-item')[0].trigger('click')
    expect(mocks.router.push).toHaveBeenCalledWith('/scan')
    expect(wrapper.find('.quick-sheet').exists()).toBe(false)
  })

  it('routes every dashboard and quick action to its matching page', async () => {
    wrapper = mount(App, { global: { stubs: { RouterView: true } } })
    const mainRoutes = [
      ['wheel-records', '/records'],
      ['wheel-plan', '/plan'],
      ['wheel-profile', '/profile'],
      ['wheel-settings', '/settings'],
    ]
    for (const [selector, path] of mainRoutes) {
      await wrapper.find('.dashboard-slot').trigger('click')
      await wrapper.find(`.${selector}`).trigger('click')
      expect(mocks.router.push).toHaveBeenLastCalledWith(path)
    }

    const quickRoutes = ['/scan', '/media', '/insights', '/trends', '/goals', '/report', '/workout', '/profile/edit']
    for (const [index, path] of quickRoutes.entries()) {
      await wrapper.find('.dashboard-slot').trigger('click')
      await wrapper.find('.wheel-quick').trigger('click')
      await wrapper.findAll('.quick-item')[index].trigger('click')
      expect(mocks.router.push).toHaveBeenLastCalledWith(path)
    }
  })
})
