// @vitest-environment happy-dom
import { afterEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import CharacterStage from '../../src/components/CharacterStage.vue'

describe('CharacterStage', () => {
  let wrapper

  afterEach(() => wrapper?.unmount())

  it('keeps the selected character and activity available to assistive technology', () => {
    wrapper = mount(CharacterStage, { props: { agentId: 'xiaokang', activity: 'thinking' } })

    expect(wrapper.attributes('aria-label')).toBe('小康正在整理')
    expect(wrapper.classes()).toContain('agent-xiaokang')
    expect(wrapper.find('.thinking-dots').exists()).toBe(true)
  })

  it('falls back to the character portrait when scene assets fail', async () => {
    wrapper = mount(CharacterStage, { props: { agentId: 'xiaojian', activity: 'idle' } })
    await wrapper.find('.scene').trigger('error')

    expect(wrapper.find('.portrait-fallback img').attributes('src')).toBe('/generated-assets/icons/agent-xiaojian.png')
    expect(wrapper.find('.figure').exists()).toBe(false)
  })
})
