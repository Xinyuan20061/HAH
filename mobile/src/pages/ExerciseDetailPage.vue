<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { readExerciseEffect } from '../services/api'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const name = typeof route.query.name === 'string' ? route.query.name : '训练动作'
const slugs: Record<string, string> = { 深蹲: 'squat', 俯卧撑: 'pushup', 平板支撑: 'plank', 弓步蹲: 'lunge', 哑铃划船: 'row', 死虫式: 'deadbug' }
const effect = ref<Record<string, any> | null>(null)
const error = ref('')
const references: Record<string, any> = {
  深蹲: { focus: '下肢与髋部力量', steps: ['双脚约与肩同宽，脚尖自然向外。', '髋部向后下坐，膝盖与脚尖方向一致。', '在可控范围内下降，再稳定站起。'], cues: ['足底保持稳定', '核心轻收紧', '先保证动作质量'], safety: '膝、髋或腰出现锐痛时停止动作。' },
  俯卧撑: { focus: '胸、肩、手臂和核心', steps: ['双手略宽于肩并稳定支撑。', '头、肩、髋尽量保持一条线。', '控制下降，再呼气推起。'], cues: ['避免塌腰', '肘部自然向后', '需要时改做高位版本'], safety: '肩腕不适时减少幅度或停止。' },
  平板支撑: { focus: '核心稳定', steps: ['前臂支撑，肘部位于肩下方。', '轻收骨盆，保持自然呼吸。', '姿势开始变形前结束一组。'], cues: ['腰背保持稳定', '不要憋气', '稳定优先于时长'], safety: '腰部不适时缩短时间或停止。' },
}
const reference = references[name] || { focus: '一般力量训练', steps: ['先用轻负荷熟悉动作。', '保持核心稳定与关节自然对齐。', '动作变形前结束该组。'], cues: ['慢一点、稳一点', '优先保持可控幅度'], safety: '出现锐痛、眩晕或明显不适时停止训练。' }
onMounted(async () => { const slug = slugs[name]; if (!slug) return; try { effect.value = await readExerciseEffect(auth.accessToken, slug) } catch (cause) { error.value = cause instanceof Error ? cause.message : '' } })
</script>

<template>
  <section class="page exercise-detail"><header class="page-heading"><button class="back-button" aria-label="返回" @click="router.back()">‹</button><div><p class="eyebrow">动作说明</p><h1>{{ name }}</h1></div></header><section class="card-surface intro"><span class="eyebrow">动作重点</span><h2>{{ effect?.title || reference.focus }}</h2><p>{{ effect?.summary || '选择自己能控制的幅度和负荷，动作稳定后再逐步增加。' }}</p></section><section class="card-surface section"><h2>动作步骤</h2><ol><li v-for="(step,index) in effect?.steps || reference.steps" :key="index">{{ step }}</li></ol></section><section class="card-surface section"><h2>动作提示</h2><ul><li v-for="cue in effect?.cues || reference.cues" :key="cue">{{ cue }}</li></ul><p v-if="effect?.muscles?.length" class="muscles">涉及部位：{{ effect.muscles.join('、') }}</p></section><section class="safety"><b>安全提醒</b><p>{{ effect?.safety || reference.safety }}</p><small v-if="error">在线动作资料暂不可用，当前显示基础动作说明。</small></section></section>
</template>

<style scoped>
.exercise-detail{padding:9px 0 24px}.page-heading{display:flex;align-items:center;gap:9px;margin-bottom:13px}.page-heading h1{margin:3px 0 0;font-size:22px}.back-button{width:35px;height:35px;border:0;border-radius:50%;background:#fff;font-size:24px}.intro,.section{padding:14px;margin-bottom:9px}.intro h2{margin:5px 0;font-size:17px}.intro p,.section li,.muscles{color:#62685f;font-size:11px;line-height:1.7}.section h2{margin:0;font-size:13px}.section ol,.section ul{padding-left:19px}.section li{padding:3px 0}.safety{padding:12px;border-radius:15px;color:#7c3329;background:#f8e7e4}.safety b{font-size:11px}.safety p,.safety small{font-size:10px;line-height:1.5}
</style>
