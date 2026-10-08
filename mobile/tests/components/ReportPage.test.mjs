// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  readWeeklyFacts: vi.fn(),
  createWeeklySummary: vi.fn(),
  back: vi.fn(),
}))

vi.mock('vue-router', () => ({ useRouter: () => ({ back: mocks.back }) }))
vi.mock('../../src/services/api', () => ({
  readWeeklyFacts: mocks.readWeeklyFacts,
  createWeeklySummary: mocks.createWeeklySummary,
}))
vi.mock('../../src/stores/auth', () => ({ useAuthStore: () => ({ accessToken: 'test-token' }) }))

import ReportPage from '../../src/pages/ReportPage.vue'

describe('ReportPage user-facing copy', () => {
  let wrapper

  beforeEach(() => {
    mocks.readWeeklyFacts.mockReset().mockResolvedValue({
      period: { label: '近 7 天' },
      score: null,
      data_quality: { confidence: 'low', note: '记录还不完整' },
      coverage: {},
      averages: {},
      highlights: [],
    })
    mocks.createWeeklySummary.mockReset()
  })

  afterEach(() => wrapper?.unmount())

  it('translates internal confidence values and keeps the summary plain', async () => {
    wrapper = mount(ReportPage)
    await vi.waitFor(() => expect(wrapper.text()).toContain('记录较少'))

    expect(wrapper.text()).not.toContain('low')
    expect(wrapper.text()).not.toContain('FACT-BASED REVIEW')
    expect(wrapper.text()).toContain('总结根据本页的记录生成，供日常参考。')
  })
})
