<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { readEnergyDashboard } from '../services/api'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()
const loading = ref(true)
const error = ref('')
const dashboard = ref<Record<string, any> | null>(null)

async function load() {
  loading.value = true
  error.value = ''
  try { dashboard.value = await readEnergyDashboard(auth.accessToken) }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '记录概览暂时无法读取' }
  finally { loading.value = false }
}

onMounted(() => void load())
</script>

<template>
  <section class="page records-page">
    <header class="records-heading"><button class="back-button" aria-label="返回" @click="router.back()">‹</button><div><p class="eyebrow">日常记录</p><h1>健康记录</h1></div></header>
    <div v-if="loading" class="card-surface state-card"><span class="spinner"/><b>正在整理已有记录…</b></div>
    <div v-else-if="error" class="card-surface record-error"><b>记录概览暂时无法显示</b><p>{{ error }}</p><button class="button-soft" @click="load">重试</button></div>
    <template v-else>
      <section class="card-surface energy-card">
        <p class="eyebrow">{{ dashboard?.business_date || dashboard?.date || '今天' }}</p>
        <h2>今天的能量记录</h2>
        <div class="energy-grid"><div><b>{{ Math.round(Number(dashboard?.today?.intake) || 0) }}</b><small>已记录摄入 kcal</small></div><div><b>{{ Math.round(Number(dashboard?.today?.exercise) || 0) }}</b><small>已记录运动 kcal</small></div></div>
        <p class="data-note">只统计已经记录的数据；没有记录的餐次和运动不会按 0 推断。</p>
      </section>
      <nav class="record-actions">
        <button class="card-surface record-action" @click="router.push('/records/diet')"><span class="record-icon">◒</span><span><b>饮食记录</b><small>查看、补充或修改餐食</small></span><i>›</i></button>
        <button class="card-surface record-action" @click="router.push('/records/exercise')"><span class="record-icon">↗</span><span><b>运动记录</b><small>记录训练时长与强度</small></span><i>›</i></button>
        <button class="card-surface record-action" @click="router.push('/scan')"><span class="record-icon">▧</span><span><b>拍照识别餐食</b><small>先生成草稿，确认后再保存</small></span><i>›</i></button>
        <button class="card-surface record-action" @click="router.push('/media')"><span class="record-icon">▶</span><span><b>动作视频</b><small>录制并查看训练分析</small></span><i>›</i></button>
      </nav>
    </template>
  </section>
</template>

<style scoped>
.records-page{padding:10px 0 24px}.records-heading{display:flex;align-items:center;gap:10px;margin-bottom:15px}.records-heading h1{margin:3px 0 0;font-size:23px}.back-button{width:36px;height:36px;border:0;border-radius:50%;background:#fff;font-size:25px}.energy-card{padding:16px}.energy-card h2{margin:5px 0 14px;font-size:17px}.energy-grid{display:grid;grid-template-columns:1fr 1fr;gap:9px}.energy-grid>div{display:flex;flex-direction:column;gap:4px;padding:12px;border-radius:15px;background:#f7f7f2}.energy-grid b{font-size:22px}.energy-grid small,.data-note{color:#737a70;font-size:10px}.data-note{margin:12px 1px 0;line-height:1.5}.record-actions{display:grid;gap:9px;margin-top:13px}.record-action{display:flex;align-items:center;gap:11px;min-height:67px;padding:10px 13px;border:0;text-align:left}.record-icon{display:grid;width:39px;height:39px;flex:0 0 39px;place-items:center;border-radius:14px;background:#e9efd9;color:#506336;font-size:20px}.record-action>span:nth-child(2){display:flex;flex:1;flex-direction:column;gap:4px}.record-action b{font-size:13px}.record-action small{color:#737a70;font-size:10px}.record-action i{color:#506336;font-size:22px;font-style:normal}.record-error{padding:16px}.record-error p{color:#8f3028;font-size:12px}.button-soft{min-height:36px;padding:0 12px;border:0;border-radius:12px;background:#e9efd9;color:#506336}
</style>
