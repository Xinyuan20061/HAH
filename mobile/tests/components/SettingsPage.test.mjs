// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  readCurrentUser: vi.fn(),
  auth: {
    accessToken: 'test-token',
    user: { id: 1, nickname: '测试用户', linked_providers: ['wechat_mobile'] },
    signOut: vi.fn(),
  },
  voice: { autoplay: false, restore: vi.fn(), setAutoplay: vi.fn() },
}))

vi.mock('vue-router', () => ({ useRouter: () => ({ push: mocks.push, replace: mocks.replace, back: vi.fn() }) }))
vi.mock('../../src/services/api', () => ({ readCurrentUser: mocks.readCurrentUser }))
vi.mock('../../src/stores/auth', () => ({ useAuthStore: () => mocks.auth }))
vi.mock('../../src/stores/voice', () => ({ useVoiceStore: () => mocks.voice }))

import SettingsPage from '../../src/pages/SettingsPage.vue'

describe('SettingsPage account actions', () => {
  let wrapper

  beforeEach(() => {
    mocks.push.mockReset()
    mocks.replace.mockReset()
    mocks.auth.signOut.mockReset().mockResolvedValue(undefined)
    mocks.readCurrentUser.mockReset().mockResolvedValue({ id: 1, nickname: '测试用户', linked_providers: ['wechat_mobile'] })
    mocks.voice.restore.mockReset().mockResolvedValue(undefined)
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
  })

  it('opens mini-program account linking and signs the user out', async () => {
    wrapper = mount(SettingsPage)
    await vi.waitFor(() => expect(mocks.readCurrentUser).toHaveBeenCalledOnce())
    expect(wrapper.find('.account-card small').text()).toBe('Android 微信登录')
    expect(wrapper.text()).not.toContain('微信云托管')

    const linkRow = wrapper.findAll('.settings-row').find(row => row.text().includes('关联微信小程序账号'))
    expect(linkRow).toBeTruthy()
    await linkRow.trigger('click')
    expect(mocks.push).toHaveBeenCalledWith('/account-link')

    await wrapper.find('.sign-out-button').trigger('click')
    expect(mocks.auth.signOut).toHaveBeenCalledOnce()
    expect(mocks.replace).toHaveBeenCalledWith('/home')
  })

  it('shows when the mini-program account is linked', async () => {
    mocks.readCurrentUser.mockResolvedValue({
      id: 1,
      nickname: '测试用户',
      linked_providers: ['wechat_miniprogram', 'wechat_mobile'],
    })

    wrapper = mount(SettingsPage)
    await vi.waitFor(() => expect(mocks.readCurrentUser).toHaveBeenCalledOnce())
    expect(wrapper.find('.account-card small').text()).toBe('微信小程序已关联')
  })
})
