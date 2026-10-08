// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  correctFoodAnalysis: vi.fn(),
  createFoodJob: vi.fn(),
  finalizeFoodAnalysis: vi.fn(),
  readFoodAnalysis: vi.fn(),
  readFoodJob: vi.fn(),
  uploadCloudMedia: vi.fn(),
  refreshCloudMedia: vi.fn(),
  captureOrChooseMealPhoto: vi.fn(),
  clearUserJob: vi.fn(),
  readUserJob: vi.fn(),
  writeUserJob: vi.fn(),
  push: vi.fn(),
  back: vi.fn(),
}))

vi.mock('vue-router', () => ({ useRouter: () => ({ push: mocks.push, back: mocks.back }) }))
vi.mock('../../src/services/appLifecycle', () => ({ waitForAppForeground: vi.fn() }))
vi.mock('../../src/services/api', () => ({
  correctFoodAnalysis: mocks.correctFoodAnalysis,
  createFoodJob: mocks.createFoodJob,
  finalizeFoodAnalysis: mocks.finalizeFoodAnalysis,
  readFoodAnalysis: mocks.readFoodAnalysis,
  readFoodJob: mocks.readFoodJob,
}))
vi.mock('../../src/services/cloudMedia', () => ({
  uploadCloudMedia: mocks.uploadCloudMedia,
  refreshCloudMedia: mocks.refreshCloudMedia,
}))
vi.mock('../../src/services/mediaPicker', () => ({ captureOrChooseMealPhoto: mocks.captureOrChooseMealPhoto }))
vi.mock('../../src/services/userScopedJobs.mjs', () => ({
  clearUserJob: mocks.clearUserJob,
  readUserJob: mocks.readUserJob,
  writeUserJob: mocks.writeUserJob,
}))
vi.mock('../../src/stores/auth', () => ({ useAuthStore: () => ({ accessToken: 'test-access-token', user: { id: 9 } }) }))

import ScanPage from '../../src/pages/ScanPage.vue'

const result = {
  analysis_id: 7,
  dish_name: '鸡肉饭',
  portion: '一份',
  cooking_method: '蒸制',
  estimated_weight_g: 320,
  calories: 520,
  protein: 32,
  carbs: 65,
  fat: 14,
  fiber: 4,
  confidence: 0.9,
  portion_basis: '按常见餐盘比例估算',
}

describe('ScanPage correction and save recovery', () => {
  let wrapper

  beforeEach(() => {
    Object.values(mocks).forEach(mock => mock.mockReset())
    mocks.readUserJob.mockReturnValue({ mediaId: 11, fileId: 'cloud://private/meal.jpg', jobId: 42 })
    mocks.readFoodJob.mockResolvedValue({ status: 'completed', result })
    mocks.readFoodAnalysis.mockResolvedValue({ status: 'analyzed', initial: result, corrected: null })
    mocks.correctFoodAnalysis.mockResolvedValue({ ok: true })
    globalThis.window.confirm = vi.fn(() => true)
  })

  afterEach(() => wrapper?.unmount())

  async function openCompletedAnalysis() {
    wrapper = mount(ScanPage)
    await vi.waitFor(() => expect(wrapper.find('.result-card').exists()).toBe(true))
    await wrapper.find('.review-check input').setValue(true)
  }

  async function retrySave() {
    await wrapper.find('.result-fields .wide-primary').trigger('click')
  }

  it('sends edited values when a finalized-save retry follows a correction', async () => {
    let rejectFinalize
    mocks.finalizeFoodAnalysis
      .mockImplementationOnce(() => new Promise((_, reject) => { rejectFinalize = reject }))
      .mockResolvedValueOnce({ ok: true })
    await openCompletedAnalysis()

    const caloriesInput = wrapper.findAll('.form-grid label')
      .find(label => label.text().includes('热量（千卡）'))
      .find('input')
    await retrySave()
    await vi.waitFor(() => expect(mocks.finalizeFoodAnalysis).toHaveBeenCalledOnce())
    expect(wrapper.find('.result-fields').element.disabled).toBe(true)
    rejectFinalize(new Error('保存服务暂时不可用'))
    await vi.waitFor(() => expect(wrapper.text()).toContain('保存服务暂时不可用'))
    expect(wrapper.find('.result-fields').element.disabled).toBe(false)

    await caloriesInput.setValue('620')
    await retrySave()

    await vi.waitFor(() => expect(wrapper.find('.saved-card').exists()).toBe(true))
    expect(mocks.correctFoodAnalysis).toHaveBeenCalledTimes(2)
    expect(mocks.correctFoodAnalysis).toHaveBeenNthCalledWith(
      2,
      'test-access-token',
      7,
      expect.objectContaining({ calories: 620 }),
    )
    expect(mocks.finalizeFoodAnalysis).toHaveBeenCalledTimes(2)
  })

  it('does not add a duplicate correction when retrying unchanged values', async () => {
    mocks.finalizeFoodAnalysis
      .mockRejectedValueOnce(new Error('保存服务暂时不可用'))
      .mockResolvedValueOnce({ ok: true })
    await openCompletedAnalysis()

    await retrySave()
    await vi.waitFor(() => expect(wrapper.text()).toContain('保存服务暂时不可用'))
    await retrySave()

    await vi.waitFor(() => expect(wrapper.find('.saved-card').exists()).toBe(true))
    expect(mocks.correctFoodAnalysis).toHaveBeenCalledOnce()
    expect(mocks.finalizeFoodAnalysis).toHaveBeenCalledTimes(2)
  })

  it('restores the corrected weight and skips resubmitting an unchanged correction', async () => {
    mocks.readFoodAnalysis.mockResolvedValue({
      status: 'corrected',
      initial: result,
      corrected: { ...result, weight_g: 400, calories: 600 },
    })
    mocks.finalizeFoodAnalysis.mockResolvedValue({ ok: true })
    await openCompletedAnalysis()

    const weightInput = wrapper.findAll('.form-grid label')
      .find(label => label.text().includes('估计重量（克）'))
      .find('input')
    expect(weightInput.element.value).toBe('400')
    await retrySave()

    await vi.waitFor(() => expect(wrapper.find('.saved-card').exists()).toBe(true))
    expect(mocks.correctFoodAnalysis).not.toHaveBeenCalled()
    expect(mocks.finalizeFoodAnalysis).toHaveBeenCalledOnce()
  })
})
