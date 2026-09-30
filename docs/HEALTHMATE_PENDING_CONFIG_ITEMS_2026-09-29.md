# 待配置项清单（腾讯云 / 语音 / 视觉）

> 版本：2026-09-30；状态：**部分已完成**。腾讯云语音密钥已配置并通过真实连通验证（ASR+TTS 各一次，2026-09-30）；
> 云端部署与真机评测仍待办。密钥类禁止入库、禁止写进小程序或 `user_ai_configs`。

## 1. 腾讯云密钥（已完成 ✅）

| 配置项 | 填写位置 | 状态 |
| --- | --- | --- |
| `TENCENT_SECRET_ID` | 本地 `backend/.env` 已填；云托管环境变量待部署时注入 | ✅ 2026-09-30 填写，鉴权通过 |
| `TENCENT_SECRET_KEY` | 本地 `backend/.env` 已填；云托管环境变量待部署时注入 | ✅ 与 SecretId 成对，鉴权通过 |
| `TENCENT_REGION` | 同上 | ✅ `ap-shanghai`（实测可用） |
| `TENCENT_ASR_ENGINE` | 同上 | ✅ `16k_zh`（实测可用） |

## 2. TTS 音色（已完成核对 ✅）

| 配置项 | 填写位置 | 状态 |
| --- | --- | --- |
| `TENCENT_TTS_VOICE_TYPE` | 云托管环境变量 / `backend/.env` | ✅ `101001`（基础/精品音色，实测合成成功 2026-09-30）。变更需重新做一次连通验证 |

## 3. 语音开关与预算

| 配置项 | 填写位置 | 默认值/说明 |
| --- | --- | --- |
| `VOICE_PROVIDER` | 云托管环境变量 / `backend/.env` | 生产 `tencent_cloud`；未配置前保持 `off` |
| `VOICE_MAX_AUDIO_BYTES` | 同上 | `2500000`（保守，官方 ASR Base64 后 ≤3MB；以实测文档为准） |
| `VOICE_MONTHLY_ASR_BUDGET` | 同上 | `100` 次/月（本地预算开关，不冒充控制台余量） |
| `VOICE_MONTHLY_TTS_CHARS_BUDGET` | 同上 | `30000` 字符/月 |
| `VOICE_LIVE_VERIFY_ENABLED` | 同上 | ✅ 已置 `true`（2026-09-30，密钥配置 + ASR/TTS 各一次真实连通通过后开启）。后续由 `provider_connection_checks` 唯一约束停测，不自动线上测试 |

## 4. DeepSeek 视觉

| 配置项 | 填写位置 | 说明/待办 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | 云托管环境变量（既有项） | 系统 Key 可留空由用户自带；视觉协作需系统级可用 |
| `DEEPSEEK_VISION_MODEL` | 同上 | **发布时锁定实际型号**（如 `deepseek-flash`，支持图像输入）与回包契约；未锁定前视觉复核走降级标记 |

## 5. 配置验证顺序（与语音停测策略一致）

1. ✅ 已填 `TENCENT_SECRET_ID/KEY`、region（ap-shanghai）、engine（16k_zh）、voice_type（101001）。
2. ✅ SDK stub 与错误注入已完成（`benchmark/motion-unified-v1/voice_verify_checklist.md`，后端 218 测试全绿）。
3. ✅ 2026-09-30 已完成 ASR/TTS 各一次最小真实连通（TTS 合成"连接测试"→ ASR 转录回"连接测试。"）；`provider_connection_checks` 落库需在云端部署 0024 后由线上 verify-once 写入。
4. 之后回归一律用脱敏 fixture；`VOICE_LIVE_VERIFY_ENABLED=true` 但唯一约束保证不重复真调用。
5. 任何密钥/region/voice_type 变更 → config_fingerprint 变化 → 才允许重新手动验证一次。

## 6. 明确不要做的事

- 不要把 SecretId/SecretKey 写进小程序、`user_ai_configs`、Git 提交。
- 不要重复做真实连通调用做回归（免费额度有限；验证已通过）。
- 密钥变更前先核对控制台再改 `TENCENT_TTS_VOICE_TYPE`。
- 不要把本地预算数字说成"腾讯云账户真实余量"。

## 7. 仍待办（部署相关）

- 云端云托管环境变量注入 `TENCENT_SECRET_ID/KEY/REGION/ASR_ENGINE/TTS_VOICE_TYPE` 与 `VOICE_*` 配置（按部署手册）。
- 云端部署迁移 0024 后，线上触发一次 `verify-once`（asr+tts）写入 `provider_connection_checks`，设置页即可显示"上次验证时间"。
