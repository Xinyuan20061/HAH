# 语音 SDK stub、错误注入与"连通后停测"检查清单

> 状态：**基架清单，待实现确认**。对应规格第 9 节"语音"行、第 7 节。
> 原则：静态配置检查 → SDK stub → 错误注入 → 端到端模拟响应 → 真机 UI →
> 最后才对 ASR/TTS **各做一次**真实连通验证，成功即停测并写入 `provider_connection_checks`。

## 1. SDK stub 全覆盖矩阵（每个接口 × 每种异常）

被测接口（规格第 5 节）：

- `POST /harness/voice/transcribe`（ASR）
- `POST /harness/voice/synthesize`（TTS，含分句分段）
- `GET /harness/voice/status`
- `POST /harness/voice/verify-once`

| # | 场景 | stub 注入 | 期望行为（本地拒绝/降级，不调云） | 通过 |
| --- | --- | --- | --- | --- |
| V01 | 未配置密钥（SECRET_ID/KEY 为空） | — | status.configured=false；transcribe/synthesize 返回 503 且不发起网络调用 | [ ] |
| V02 | 空音频 / 0 字节 | 空 bytes | 本地拒绝 `unsupported_audio`，不计入 ASR 配额 | [ ] |
| V03 | 格式伪装（扩展名 mp3 实为文本） | 伪造内容 | 解码/格式校验失败，本地拒绝 | [ ] |
| V04 | 超时长（>60s）/ 超字节（>VOICE_MAX_AUDIO_BYTES） | 构造超限音频 | 本地拒绝，不发请求 | [ ] |
| V05 | TTS 单段超字数（>120 Unicode 字符） | 长文本 | 服务端自动分句分段，不得悄悄截断 | [ ] |
| V06 | TTS 第 2 段失败 | stub 让第 2 段抛错 | 前端只播成功段并展示完整文字；不重复合成第 1 段 | [ ] |
| V07 | 腾讯云 SDK 鉴权失败（签名错/密钥错） | stub 返回 AuthFailure | 映射为配置错误；不重试同一无效负载；UI 提示检查密钥 | [ ] |
| V08 | 腾讯云限流/并发超限 | stub 返回 RequestLimitExceeded | 退避一次后失败；记录 failure_count | [ ] |
| V09 | 腾讯云超时 | stub 不返回 | 超时后先查本地状态，不盲目自动重试（上游可能已扣费） | [ ] |
| V10 | 网络中断/弱网 | stub 网络不可达 | 降级提示；ASR/TTS 成功率计入 `voice_usage_daily` | [ ] |
| V11 | 预算耗尽（当月 ASR/TTS 字符超 VOICE_MONTHLY_*_BUDGET） | 计数到上限 | 本地拒绝并提示；额度是本地估算，不冒充腾讯云控制台余量 | [ ] |
| V12 | verify-once 重复调用（同 config_fingerprint + direction 已成功） | 已有成功记录 | 返回缓存记录，不重复真实调用 | [ ] |
| V13 | 密钥/地区/音色变更后 config_fingerprint 变化 | 新指纹 | 允许管理员手动触发一次新验证 | [ ] |
| V14 | OpenAI-compatible 旧 provider 路径 | 旧配置 | 兼容一个版本期；设置页不再自动 voice-test | [ ] |
| V15 | 同步 SDK 阻塞事件循环 | 测量 | ASR/TTS 调用走 threadpool，FastAPI 事件循环不被阻塞 | [ ] |

## 2. 一次真实连通验证后停测（checklist）

- [ ] 上述 V01–V15 全部以录制/脱敏 fixture 通过（不打真实云）。（待实现确认）
- [ ] 管理员在设置页手动点 ASR 一次最小真实识别（一句 ≤60s 示例音频），确认返回文本。
- [ ] 管理员手动点 TTS 一次最小真实合成（一句 ≤120 字示例文本），确认返回 mp3。
- [ ] 成功后 `provider_connection_checks` 写入：config_fingerprint、direction(asr/tts)、
      verified_at、provider_request_id；同指纹唯一约束生效。
- [ ] 设置页显示"上次验证时间"，**不写"永久已连接"**；`VOICE_LIVE_VERIFY_ENABLED=false`。
- [ ] 回归测试从此不再打真实云；DeepSeek 同理只做最小线上确认，之后用脱敏 fixture。
- [ ] 记录本次真实调用的费用与 RequestId，并入成本台账（cost_tracking_template.csv）。

## 3. 真机（小程序/手机）验收

- [ ] 真机可正常录音并上传。
- [ ] 真机可顺序播放 TTS 分段音频（MP3 分片不字节拼接）。
- [ ] 用户拒绝麦克风权限时，小程序给出权限引导，不崩溃。
- [ ] 弱网下：录音上传超时/ASR 超时给出可读降级提示，不静默失败。
- [ ] 越权：A 用户不能读取/触发 B 用户的语音或配置（404/403）。
