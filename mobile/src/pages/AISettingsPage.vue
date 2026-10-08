<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { readAIConfig, readVoiceStatus, resetAIConfig, saveAIConfig, testAIConfig, testVoiceConfig, verifyVoiceOnce } from '../services/api'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()
const busy = ref(false)
const testing = ref('')
const error = ref('')
const message = ref('')
const hasKey = ref(false)
const keyHint = ref('')
const hasVoiceKey = ref(false)
const voiceKeyHint = ref('')
const systemVoiceConfigured = ref(false)
const voiceStatus = ref<Record<string, any> | null>(null)
const form = reactive({ enabled: false, base_url: 'https://api.deepseek.com', model: 'deepseek-chat', api_key: '', voice_enabled: false, voice_base_url: '', voice_stt_model: 'whisper-1', voice_tts_model: 'tts-1', voice_name: 'alloy', voice_api_key: '', voice_provider: 'off' })
async function load() {
  busy.value = true; error.value = ''
  try {
    const [config, status] = await Promise.all([readAIConfig(auth.accessToken), readVoiceStatus(auth.accessToken).catch(() => null)])
    Object.assign(form, { enabled: !!config.enabled, base_url: config.base_url || 'https://api.deepseek.com', model: config.model || 'deepseek-chat', api_key: '', voice_enabled: !!config.voice_enabled, voice_base_url: config.voice_base_url || '', voice_stt_model: config.voice_stt_model || 'whisper-1', voice_tts_model: config.voice_tts_model || 'tts-1', voice_name: config.voice_name || 'alloy', voice_api_key: '', voice_provider: config.voice_provider || 'off' })
    hasKey.value = !!config.has_api_key; keyHint.value = config.api_key_hint || ''; hasVoiceKey.value = !!config.has_voice_api_key; voiceKeyHint.value = config.voice_api_key_hint || ''; systemVoiceConfigured.value = !!config.system_voice_configured; voiceStatus.value = status
  } catch (cause) { error.value = cause instanceof Error ? cause.message : 'AI 配置暂时无法读取' }
  finally { busy.value = false }
}
async function save() {
  if (busy.value) return
  if (form.enabled && !hasKey.value && !form.api_key.trim()) { error.value = '开启个人文字模型前，请填写 API Key'; return }
  if (form.voice_enabled && form.voice_provider !== 'tencent_cloud' && !hasVoiceKey.value && !form.voice_api_key.trim() && !systemVoiceConfigured.value) { error.value = '请配置兼容语音 API，或选择已配置的腾讯云语音服务'; return }
  busy.value = true; error.value = ''; message.value = ''
  try { const result = await saveAIConfig(auth.accessToken, { ...form }); hasKey.value = !!result.has_api_key; keyHint.value = result.api_key_hint || ''; hasVoiceKey.value = !!result.has_voice_api_key; voiceKeyHint.value = result.voice_api_key_hint || ''; systemVoiceConfigured.value = !!result.system_voice_configured; Object.assign(form, { api_key: '', voice_api_key: '', enabled: !!result.enabled, voice_enabled: !!result.voice_enabled }); message.value = '配置已保存；密钥不会保存在手机中。' }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '配置没有保存' }
  finally { busy.value = false }
}
async function testText() {
  testing.value = 'text'; error.value = ''; message.value = ''
  try { const result = await testAIConfig(auth.accessToken, { base_url: form.base_url, model: form.model, api_key: form.api_key }); message.value = `文字模型连接成功：${result.model || form.model}` }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '文字模型连接失败' }
  finally { testing.value = '' }
}
async function testVoice() {
  testing.value = 'voice'; error.value = ''; message.value = ''
  try { await testVoiceConfig(auth.accessToken, { base_url: form.voice_base_url, tts_model: form.voice_tts_model, voice_name: form.voice_name, api_key: form.voice_api_key }); message.value = '语音合成连接测试成功' }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '语音测试失败' }
  finally { testing.value = '' }
}
async function verify(kind: 'asr' | 'tts') {
  if (!window.confirm(`现在发起一次腾讯云语音${kind === 'asr' ? '识别' : '合成'}连通验证？这会真实调用服务并可能计入用量。`)) return
  testing.value = kind; error.value = ''; message.value = ''
  try { const result = await verifyVoiceOnce(auth.accessToken, kind); message.value = result.cached ? '该方向已有验证结果，本次没有重复调用。' : `${kind === 'asr' ? '识别' : '合成'}验证完成。`; voiceStatus.value = await readVoiceStatus(auth.accessToken) }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '语音连通验证没有通过' }
  finally { testing.value = '' }
}
async function reset() {
  if (!window.confirm('删除个人文字与语音模型配置，并恢复系统设置？')) return
  busy.value = true; error.value = ''; message.value = ''
  try { await resetAIConfig(auth.accessToken); form.api_key = ''; form.voice_api_key = ''; message.value = '已恢复系统模式。'; await load() }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '恢复系统设置失败' }
  finally { busy.value = false }
}
onMounted(() => void load())
onBeforeUnmount(() => { form.api_key = ''; form.voice_api_key = '' })
</script>

<template>
  <section class="page ai-settings"><header class="page-heading"><button class="back-button" aria-label="返回" @click="router.back()">‹</button><div><p class="eyebrow">个性化服务</p><h1>AI 与语音设置</h1></div></header>
    <div v-if="busy && !form.base_url" class="card-surface message">正在读取当前配置…</div>
    <section class="card-surface config-card"><div class="section-head"><div><p class="eyebrow">文字回复</p><h2>文字模型</h2></div><label class="switch"><input v-model="form.enabled" type="checkbox"/><span>{{ form.enabled ? '启用' : '停用' }}</span></label></div><label class="field">服务地址<input v-model.trim="form.base_url" type="url" autocomplete="url" placeholder="https://api.deepseek.com" /></label><label class="field">模型名称<input v-model.trim="form.model" maxlength="120" placeholder="deepseek-chat" /></label><label class="field">服务密钥<input v-model="form.api_key" type="password" autocomplete="new-password" maxlength="500" placeholder="仅本次输入，不会保存在手机" /></label><small v-if="hasKey" class="key-hint">服务端已保存密钥：{{ keyHint }}。留空则保留现有密钥。</small><div class="button-row"><button :disabled="testing === 'text'" @click="testText">{{ testing === 'text' ? '测试中…' : '测试连接' }}</button><button class="primary" :disabled="busy" @click="save">{{ busy ? '保存中…' : '保存配置' }}</button></div></section>
    <section class="card-surface config-card"><div class="section-head"><div><p class="eyebrow">语音设置</p><h2>语音服务</h2></div><label class="switch"><input v-model="form.voice_enabled" type="checkbox"/><span>{{ form.voice_enabled ? '启用' : '停用' }}</span></label></div><label class="field">服务类型<select v-model="form.voice_provider"><option value="off">关闭</option><option value="openai_compatible">OpenAI 兼容服务</option><option value="tencent_cloud">系统腾讯云服务</option></select></label><template v-if="form.voice_provider === 'openai_compatible'"><label class="field">语音服务地址<input v-model.trim="form.voice_base_url" type="url" placeholder="https://…" /></label><label class="field">语音识别模型<input v-model.trim="form.voice_stt_model" /></label><label class="field">语音合成模型<input v-model.trim="form.voice_tts_model" /></label><label class="field">音色<input v-model.trim="form.voice_name" /></label><label class="field">语音 API Key<input v-model="form.voice_api_key" type="password" autocomplete="new-password" maxlength="500" placeholder="仅本次输入，不会保存在手机" /></label><small v-if="hasVoiceKey" class="key-hint">服务端已保存语音密钥：{{ voiceKeyHint }}。留空则保留现有密钥。</small><button class="soft-button" :disabled="testing === 'voice'" @click="testVoice">{{ testing === 'voice' ? '测试中…' : '测试语音合成' }}</button></template><template v-else-if="form.voice_provider === 'tencent_cloud'"><p class="info">腾讯云密钥由服务端管理，手机端不接触密钥。当前系统服务：{{ systemVoiceConfigured ? '已配置' : '尚未配置' }}</p><div class="button-row"><button :disabled="!!testing" @click="verify('asr')">{{ testing === 'asr' ? '验证中…' : '验证识别' }}</button><button :disabled="!!testing" @click="verify('tts')">{{ testing === 'tts' ? '验证中…' : '验证合成' }}</button></div><small v-if="voiceStatus">状态：{{ voiceStatus.configured ? '已配置' : voiceStatus.status || '待检查' }}</small></template><button class="primary save-voice" :disabled="busy" @click="save">{{ busy ? '保存中…' : '保存语音配置' }}</button></section>
    <p v-if="error" class="error-text">{{ error }}</p><p v-if="message" class="success-text">{{ message }}</p><p class="security-note">服务密钥通过 HTTPS 发送，并由云托管服务加密保存。密钥只在当前页面使用；请勿在共享设备上输入。</p><button class="reset-button" :disabled="busy" @click="reset">恢复系统模式并删除个人配置</button>
  </section>
</template>

<style scoped>
.ai-settings{padding:9px 0 25px}.page-heading{display:flex;align-items:center;gap:9px;margin-bottom:13px}.page-heading h1{margin:3px 0 0;font-size:22px}.back-button{width:35px;height:35px;border:0;border-radius:50%;background:#fff;font-size:24px}.config-card{margin-bottom:10px;padding:14px}.section-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:11px}.section-head h2{margin:3px 0;font-size:16px}.switch{display:flex;align-items:center;gap:6px;color:#506336;font-size:10px}.switch input{accent-color:#506336}.field{display:flex;flex-direction:column;gap:6px;margin-bottom:9px;color:#62685f;font-size:10px}.field input,.field select{height:39px;padding:0 10px;border:1px solid #eeeee6;border-radius:11px;outline:none;background:#f7f7f2;font-size:11px}.key-hint,.security-note,.info{color:#737a70;font-size:9px;line-height:1.5}.button-row{display:flex;gap:7px;margin-top:10px}.button-row button,.soft-button,.primary{flex:1;min-height:38px;padding:7px;border:0;border-radius:11px;background:#e9efd9;color:#506336;font-size:10px}.button-row .primary,.primary{background:#c4e267;color:#26331e}.save-voice{width:100%;margin-top:11px}.error-text,.success-text{font-size:10px;line-height:1.5}.error-text{color:#8f3028}.success-text{color:#506336}.security-note{text-align:center}.reset-button{width:100%;padding:9px;border:0;border-radius:10px;background:#f8e7e4;color:#8f3028;font-size:10px}.message{padding:12px;color:#737a70;font-size:10px}
</style>
