<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { AgentId } from '../stores/companion'

const props = defineProps<{ agentId: AgentId; activity: string }>()
const failed = ref(false)
const safeAgent = computed(() => props.agentId === 'xiaokang' ? 'xiaokang' : 'xiaojian')
const scene = computed(() => `/generated-assets/characters/${safeAgent.value === 'xiaokang' ? 'xiaokang-wellness-scene-v2' : 'xiaojian-gym-scene-v3'}.png`)
const sheet = computed(() => `/generated-assets/characters/${safeAgent.value === 'xiaokang' ? 'xiaokang-idle-read-v5' : 'xiaojian-idle-curl-v3'}.png`)
const portrait = computed(() => `/generated-assets/icons/agent-${safeAgent.value}.png`)
const label = computed(() => ({ idle: '等你开口', thinking: '正在整理', planning: '正在准备计划', presenting: '正在展示', success: '已经准备好', error: '需要重新尝试' }[props.activity] || '等你开口'))

watch([() => props.agentId, () => props.activity], () => { failed.value = false })
</script>

<template>
  <div class="stage" :class="[`agent-${safeAgent}`, `activity-${activity}`]" :aria-label="`${safeAgent === 'xiaojian' ? '小健' : '小康'}${label}`">
    <img class="scene" :src="scene" alt="" aria-hidden="true" @error="failed = true" />
    <div class="stage-glaze" />
    <div v-if="failed" class="portrait-fallback"><img :src="portrait" alt="" /></div>
    <div v-else class="figure" :class="safeAgent === 'xiaojian' ? 'idle-curl' : 'idle-read'">
      <img class="sprite-sheet" :src="sheet" alt="" draggable="false" @error="failed = true" />
    </div>
    <div class="stage-floor" />
    <div v-if="activity === 'thinking'" class="thinking-dots" aria-hidden="true"><i /><i /><i /></div>
  </div>
</template>

<style scoped>
.stage { position:relative; width:100%; height:clamp(190px, 70vw, 290px); margin-top:13px; overflow:hidden; border-radius:22px; background:#f7f7f2; }
.scene { position:absolute; inset:0; width:100%; height:100%; object-fit:cover; object-position:center 63%; }
.stage-glaze { position:absolute; inset:0; background:linear-gradient(180deg,rgba(247,247,242,.16),transparent 42%,rgba(17,22,19,.08)); pointer-events:none; }
.figure { position:absolute; left:50%; bottom:7%; width:min(37%, 132px); aspect-ratio:2/3; transform:translateX(-50%); overflow:hidden; filter:drop-shadow(0 4px 4px rgba(17,22,19,.12)); }
.sprite-sheet { display:block; width:800%; max-width:none; height:100%; object-fit:fill; image-rendering:pixelated; }
.idle-curl .sprite-sheet { animation: curlFrames 1.6s steps(1,end) infinite; }
.idle-read .sprite-sheet { animation: readFrames 2.4s steps(1,end) infinite; }
.stage-floor { position:absolute; bottom:5%; left:50%; width:34%; height:6px; border-radius:50%; background:rgba(17,22,19,.2); filter:blur(5px); transform:translateX(-50%); }
.portrait-fallback { position:absolute; inset:30% 40%; display:flex; align-items:center; justify-content:center; }
.portrait-fallback img { width:100%; height:100%; object-fit:contain; }
.thinking-dots { position:absolute; right:19px; top:18px; display:flex; gap:5px; }
.thinking-dots i { width:7px; height:7px; border-radius:50%; background:#506336; animation:thinkDot 1s ease-in-out infinite; }
.thinking-dots i:nth-child(2) { animation-delay:.12s; }.thinking-dots i:nth-child(3) { animation-delay:.24s; }
.activity-thinking .idle-curl, .activity-thinking .idle-read { animation-play-state:paused; }
@keyframes curlFrames { 0%{transform:translateX(0)} 12.5%{transform:translateX(-12.5%)} 25%{transform:translateX(-25%)} 37.5%{transform:translateX(-37.5%)} 50%{transform:translateX(-50%)} 62.5%{transform:translateX(-62.5%)} 75%{transform:translateX(-75%)} 87.5%,100%{transform:translateX(-87.5%)} }
@keyframes readFrames { 0%{transform:translateX(0)} 12.5%{transform:translateX(-12.5%)} 25%{transform:translateX(-25%)} 37.5%{transform:translateX(-37.5%)} 50%{transform:translateX(-50%)} 62.5%{transform:translateX(-62.5%)} 75%{transform:translateX(-75%)} 87.5%,100%{transform:translateX(-87.5%)} }
@keyframes thinkDot { 0%,60%,100%{opacity:.28;transform:translateY(0)} 30%{opacity:1;transform:translateY(-6px)} }
</style>
