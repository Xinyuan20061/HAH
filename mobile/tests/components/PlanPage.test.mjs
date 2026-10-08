// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  applyRunPlan: vi.fn(),
  readCurrentPlan: vi.fn(),
  readRun: vi.fn(),
  replace: vi.fn(),
  route: { query: { runId: '42', agent: 'xiaojian' } },
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
  useRouter: () => ({ back: vi.fn(), replace: mocks.replace, push: vi.fn() }),
}))

vi.mock('../../src/services/api', () => ({
  applyRunPlan: mocks.applyRunPlan,
  readCurrentPlan: mocks.readCurrentPlan,
  readRun: mocks.readRun,
  updatePlanItem: vi.fn(),
}))

vi.mock('../../src/stores/auth', () => ({
  useAuthStore: () => ({ accessToken: 'test-access-token' }),
}))

import PlanPage from '../../src/pages/PlanPage.vue'

const preview = status => ({
  run_id: 42,
  status,
  read_only: status === 'draft',
  title: '测试训练计划',
  items: [{ date_offset: 0, category: 'exercise', title: '基础训练', description: '轻松完成一组训练' }],
  write: { status: status === 'draft' ? 'not_applied' : 'applied', automatic: false, confirmation_required: status === 'draft' },
})

const run = status => ({
  run_id: 42,
  status: 'completed',
  reply: '先看看这份草案，确认后才会加入计划。',
  plan_preview: preview(status),
})

describe('PlanPage confirmation boundary', () => {
  let wrapper

  beforeEach(() => {
    mocks.applyRunPlan.mockReset()
    mocks.readCurrentPlan.mockReset()
    mocks.readRun.mockReset()
    mocks.replace.mockReset()
    mocks.readRun.mockResolvedValue(run('draft'))
    mocks.applyRunPlan.mockResolvedValue({ already_applied: false })
  })

  afterEach(() => wrapper?.unmount())

  it('keeps a draft read-only until the user confirms, then reloads the applied result', async () => {
    wrapper = mount(PlanPage)
    await vi.waitFor(() => expect(wrapper.text()).toContain('尚未写入'))

    await wrapper.find('.confirm-button').trigger('click')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    expect(mocks.applyRunPlan).not.toHaveBeenCalled()

    await wrapper.find('.confirm-dialog .button-soft').trigger('click')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(mocks.applyRunPlan).not.toHaveBeenCalled()

    mocks.readRun.mockResolvedValueOnce(run('applied'))
    await wrapper.find('.confirm-button').trigger('click')
    await wrapper.find('.confirm-dialog .button-primary').trigger('click')
    await vi.waitFor(() => expect(mocks.applyRunPlan).toHaveBeenCalledOnce())
    await vi.waitFor(() => expect(wrapper.text()).toContain('已写入你的计划'))

    expect(mocks.applyRunPlan).toHaveBeenCalledWith('test-access-token', 42)
    expect(mocks.readRun).toHaveBeenCalledTimes(2)
  })

  it('shows a recoverable error when the confirmed write fails', async () => {
    mocks.applyRunPlan.mockRejectedValueOnce(new Error('计划能力未开启'))
    wrapper = mount(PlanPage)
    await vi.waitFor(() => expect(wrapper.text()).toContain('尚未写入'))

    await wrapper.find('.confirm-button').trigger('click')
    await wrapper.find('.confirm-dialog .button-primary').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('计划能力未开启'))

    expect(mocks.applyRunPlan).toHaveBeenCalledOnce()
    expect(wrapper.find('.confirm-dialog').exists()).toBe(false)
    await wrapper.find('.error-state .button-soft').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('尚未写入'))
    expect(mocks.applyRunPlan).toHaveBeenCalledOnce()
    expect(mocks.readRun).toHaveBeenCalledTimes(2)
  })
})
