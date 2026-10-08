<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { generateWorkoutPlan } from '../services/api'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()
const form = reactive({ goal: '减脂塑形', days: 4, minutes: 35, level: '初级', equipment: '徒手' })
const busy = ref(false)
const error = ref('')
const plan = ref<Record<string, any> | null>(null)
async function generate() { if (busy.value) return; busy.value = true; error.value = ''; plan.value = null; try { plan.value = await generateWorkoutPlan(auth.accessToken, { ...form }) } catch (cause) { error.value = cause instanceof Error ? cause.message : '训练计划暂时无法生成' } finally { busy.value = false } }
</script>

<template>
  <section class="page workout-page">
    <header class="page-heading"><button class="back-button" aria-label="返回" @click="router.back()">‹</button><div><p class="eyebrow">一周安排</p><h1>训练计划</h1></div></header>
    <section class="card-surface editor"><h2>告诉我你的训练安排</h2><label class="field">主要目标<select v-model="form.goal"><option>减脂塑形</option><option>增肌力量</option><option>提升体能</option><option>改善体态</option></select></label><div class="form-row"><label class="field">每周训练日<input v-model.number="form.days" type="number" min="1" max="7" /></label><label class="field">每次分钟<input v-model.number="form.minutes" type="number" min="10" max="120" /></label></div><div class="form-row"><label class="field">训练水平<select v-model="form.level"><option>初级</option><option>中级</option><option>进阶</option></select></label><label class="field">可用器械<select v-model="form.equipment"><option>徒手</option><option>哑铃</option><option>健身房</option></select></label></div><p v-if="error" class="form-error">{{ error }}</p><button class="generate-button" :disabled="busy" @click="generate">{{ busy ? '正在整理计划…' : '生成一周计划' }}</button><p class="note">计划是一般生活健身建议。出现疼痛、术后恢复或正在治疗的情况，请先咨询专业人士。</p></section>
    <section v-if="plan" class="plan-result"><div class="result-heading"><p class="eyebrow">{{ plan.provider || '健康计划' }}</p><h2>{{ plan.title || `${form.goal} · ${form.days} 日计划` }}</h2><p>{{ plan.summary }}</p></div><article v-for="(day,index) in plan.days || []" :key="index" class="card-surface day-card"><div class="day-heading"><b>{{ day.day }}</b><span>{{ day.focus }} · {{ day.duration }} 分钟</span></div><button v-for="(exercise,j) in day.exercises || []" :key="j" class="exercise-row" @click="router.push({ path: '/exercise-detail', query: { name: exercise.name } })"><span><b>{{ exercise.name }}</b><small>{{ exercise.sets }} · {{ exercise.reps }} · 休息 {{ exercise.rest }}</small></span><i>›</i></button></article><section v-if="plan.tips?.length" class="card-surface tips"><b>训练提示</b><p v-for="tip in plan.tips" :key="tip">{{ tip }}</p></section></section>
  </section>
</template>

<style scoped>
.workout-page{padding:9px 0 24px}.page-heading{display:flex;align-items:center;gap:9px;margin-bottom:13px}.page-heading h1{margin:3px 0 0;font-size:22px}.back-button{width:35px;height:35px;border:0;border-radius:50%;background:#fff;font-size:24px}.editor{padding:15px}.editor h2{margin:0 0 13px;font-size:16px}.field{display:flex;flex:1;flex-direction:column;gap:6px;margin-bottom:9px;color:#62685f;font-size:10px}.field input,.field select{width:100%;height:39px;padding:0 10px;border:1px solid #eeeee6;border-radius:11px;outline:none;background:#f7f7f2;font-size:12px}.form-row{display:flex;gap:9px}.generate-button{width:100%;height:43px;border:0;border-radius:13px;background:#c4e267;font-size:12px;font-weight:700}.generate-button:disabled{opacity:.55}.form-error{color:#8f3028;font-size:11px}.note{color:#737a70;font-size:9px;line-height:1.5}.result-heading{padding:15px 4px 7px}.result-heading h2{margin:4px 0;font-size:18px}.result-heading p:last-child{color:#62685f;font-size:11px;line-height:1.5}.day-card,.tips{margin-bottom:8px;padding:12px}.day-heading{display:flex;justify-content:space-between;gap:8px;margin-bottom:6px;font-size:11px}.day-heading span{color:#737a70}.exercise-row{display:flex;width:100%;align-items:center;justify-content:space-between;padding:10px 0;border:0;border-top:1px solid #eeeee6;text-align:left;background:transparent}.exercise-row span{display:flex;flex-direction:column;gap:4px}.exercise-row b{font-size:11px}.exercise-row small,.exercise-row i{color:#737a70;font-size:9px;font-style:normal}.tips b{font-size:11px}.tips p{margin:6px 0;color:#62685f;font-size:10px;line-height:1.5}
</style>
