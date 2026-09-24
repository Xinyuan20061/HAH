# 用户级 DeepSeek 配置实现说明

## 目标

允许不同用户使用自己的 DeepSeek API Key，而不是所有用户共享项目 `.env` 中的一把 Key，同时避免把 Key 明文返回到微信小程序。

## 数据链路

1. 小程序在 `我的 → DeepSeek 模型设置` 输入 `API Key / Base URL / Model`。
2. `PUT /api/v1/users/me/ai-config` 接收配置。
3. API Key 使用 `backend/app/core/crypto.py` 加密后写入 `user_ai_configs.api_key_encrypted`。
4. 查询配置时仅返回 `has_api_key` 与脱敏 `api_key_hint`，不返回明文。
5. 聊天请求进入 `get_provider(user)`：
   - 用户配置已启用且 Key 有效：使用用户 Provider；
   - 用户配置未启用时，若服务器配置 DeepSeek：使用系统 Provider；
   - 没有可用Key时返回明确不可用（聊天503；规则摘要/计划明确降级）。
6. `POST /api/v1/users/me/ai-config/test` 支持保存前/保存后连通性测试。

## 安全边界

- 优先使用独立的 `CREDENTIALS_ENCRYPTION_KEY` 加密用户 API Key；仅为兼容旧配置才回退到 `SECRET_KEY`。正式环境两者都应使用独立高熵值并妥善保管。
- Base URL 强制 HTTPS，并阻止明显的 localhost、`.local`、私有 IP、loopback 和 link-local 地址。
- 当前实现是比赛与产品 Beta 级安全基线。正式商业环境还应加入：KMS/密钥托管、DNS 重绑定防护、出口网络白名单、审计、限流、Key 轮换和敏感日志扫描。
- 小程序端绝不把保存过的 Key 再取回明文，这是刻意设计，不是功能缺失。

## 为什么模型 ID 允许自由输入

第三方模型的型号会更新。如果把型号写死在前端，后续每次模型调整都需要重新发版。当前页面提供常用快捷值，同时保留自由输入，让用户可以直接复制 DeepSeek 官方控制台/API 文档中的当前模型 ID。

生产必须独立CREDENTIALS_ENCRYPTION_KEY；原密钥丢失时解密明确503，不静默切换或返回明文。BaseURL生产做HTTPS/DNS/公网IP固定验证，HTTP失败统一安全提示；目前没有真实Key请求验收。详见根README和docs/FIX_REPORT.md。
