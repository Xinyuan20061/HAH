# HAH Android 开发执行规格：继续使用微信云托管

> 面向执行者：Luna  
> 日期：2026-10-07（Asia/Shanghai）  
> 文档版本：1.0  
> 状态：开发执行规格；Android debug APK 已在本机成功构建，语音录制/播报与 COS/S3 签名上传代码已接入。真实微信开放平台、COS、云托管和真机仍待配置验收；实时进度见 [`docs/android/development-status.md`](android/development-status.md)。  
> 核对源码提交：`716b8be4aabcc3eff57586e36d73f16e405186fc`  
> 用户已明确的架构要求：新增 Android 应用，后端继续使用微信云托管。

## 1. 执行目标与使用方法

在保留微信小程序的同时，新增可安装的 Android APK。两个客户端复用现有 FastAPI、MySQL、Harness Kernel、业务工具和 AI Worker。Android 的页面、角色、导航、状态、文案和交互以当前小程序为基线，不在迁移中重做产品。

这份文档把用户提供的 `ANDROID_APP_MIGRATION_DESIGN_2026-10-07.md` 转成具体开发任务，并补充了源码核对中发现的差异。原文是设计参考；本文件中的“拟新增”接口、配置、表和脚本都需要开发，不能视为已存在能力。用户之后的明确指令优先于两份文档。

Luna 开始工作时应：

1. 读取当前任务上下文、实际存在的 `AGENTS.md`、本文件及第 3 节列出的项目资料。
2. 检查工作区与当前提交，保留已有修改；不要用 reset、覆盖或清理命令恢复到本文件记录的提交。
3. 先做 WP0，再按依赖推进 WP1～WP7；有外部资源缺口时继续完成不依赖该资源的工作。
4. 每个工作包交付实际文件、运行结果和未验证项，更新开发台账。
5. 文档交接本身不表示已经授权部署生产、运行生产数据库迁移或发布正式 APK。实施时以用户给执行任务的授权范围为准。

首个可演示目标是：**debug APK 可安装 → 连接隔离测试后端 → 登录 → 小健/小康提问 → 展示经过安全复核的回答 → 跳转计划审阅 → 用户确认 → 写入并查看真实计划。**

完整完成目标还包括图片、视频、录音、识餐、动作任务、全部页面、正式登录、两端账号关联、隐私删除及正式签名交付。完成首个目标不能宣称整个 Android 版已完成。

## 2. 固定架构与边界

```text
微信小程序 miniprogram/ ── wx.cloud.callContainer ─┐
                                                 ├─ 微信云托管 healthmate-api
Android mobile/ ────────── 公网 HTTPS + JWT ──────┘   ├─ FastAPI / Harness / 工具
                                                     ├─ 持久 MySQL
                                                     ├─ 现有任务队列与 Worker 协议
                                                     └─ 媒体资产账本

小程序素材 ── 现有 CloudBase 云存储 ── cloud_ref 资产
Android 素材 ── 服务端签发 COS 上传地址 ── cos 资产（拟新增）
本地调试 ── /media/upload ── local 资产

本地 AI Worker ── 主动 HTTPS 轮询云托管 ── 领取/提交任务
```

微信云托管提供服务运行入口，COS 提供对象存储；新增 COS 不等于更换后端平台。官方服务设置说明支持 APP 通过公网 HTTPS 访问服务，小程序的 `callContainer` 私有链路可以同时保留：[云托管服务设置](https://docs.cloudbase.net/run/deploy/service-setting)。

必须保持：

- `miniprogram/` 独立可发布；Android 不替换、删除或强制迁移微信客户端。
- 新增 `mobile/`，采用 Vue 3、TypeScript、Vite、Vue Router、Pinia、Capacitor。
- APK 内置网页资源，正式包启动不依赖远程开发服务器。
- 后端继续部署于同一微信云托管服务；测试环境与生产环境分离。
- 两端共用业务 `user_id` 和业务表；不新增一套 Android 健康数据库。
- 继续使用现有 Router、领域 Agent、Decision、安全审查、能力授权和用户确认机制。
- Worker 主动访问云端；不要求云托管反向访问个人电脑。
- 不在 APK 写入服务端密钥、Worker Token、数据库口令或正式签名密码。
- `X-Client-Platform: android` 仅用于兼容、审计与诊断，不作为授权依据。
- 不引入 Skyline，不把 `wx.*` 做成全量兼容运行时。

## 3. 当前源码事实与阅读清单

### 3.1 核对后的事实

| 项目 | 当前事实 | 对实施的影响 |
| --- | --- | --- |
| Android 工程 | 核对时没有 `mobile/` | 需要新建客户端及 Android 工程 |
| 页面数 | `miniprogram/app.json` 注册 **28** 条路由 | 原方案的 29 条需纠正；开发时重新生成清单 |
| 用户身份 | `User.openid` 唯一且非空 | 移动身份不能直接复用该字段塞入另一种 openid |
| 开发登录 | `/auth/dev-login` 统一得到 `dev-user` | 不能用它证明两个用户的数据隔离 |
| 正式登录 | `/auth/wechat` 使用小程序 `jscode2session` | Android OAuth code 不能提交给这个接口冒充小程序 code |
| Token | 当前登录返回 access token，未见独立 refresh-token 路由 | 401 重登录和 refresh-token 设计必须区分 |
| 生产校验 | 强制 MySQL、`cloud_ref` 和微信配置 | 需要增加部署档案，不能删除所有校验 |
| 媒体上传 | `/media/upload` 在 production 直接拒绝 | 仅配置 S3/COS 不会自动让安卓生产上传可用 |
| 存储适配 | 已有 Local、CloudReference、旧 S3 适配器 | 旧 S3 不等于已验证的腾讯 COS 实现 |
| 媒体表 | `MediaAsset` 已有 `storage_backend` 等字段 | 利用现有账本，不重复造平行资产表 |
| CloudBase 文件 | 目前主要由小程序获取链接、刷新及删除 | Android 跨端读取旧文件、删除旧文件需要额外服务端能力 |
| Agent 流 | `respond()` 完成后才生成 NDJSON | stage 是既有执行记录的发送，不是当前实时进度 |
| 语音 | STT 接收 base64；TTS 返回 base64 音频分段 | 第一版应适配现有合同，不假定返回音频 URL |
| 原工程验收 | 收口资料明确多个门禁未验证 | 历史通过数字不能充当当前提交的验证证据 |

### 3.2 先读这些文件

| 目的 | 文件 |
| --- | --- |
| 发布边界与历史验证状态 | `docs/FINAL_RELEASE_READINESS_2026-10-03.md`、`docs/audit_baseline.json` |
| 后端架构 | `docs/ARCHITECTURE.md`、`docs/HEALTH_AGENT_HARNESS.md` |
| 云部署与媒体约束 | `docs/WECHAT_CLOUD_RUN_DEPLOY.md`、`backend/.env.example` |
| 产品样式 | `miniprogram/PRODUCT_UI.md`、`miniprogram/app.wxss` |
| 动效 | `.agents/skills/healthmate-motion/SKILL.md`、其 references |
| 请求与身份 | `miniprogram/utils/request.js`、`backend/app/api/v1/auth.py`、`backend/app/api/deps.py` |
| 角色与悬浮会话 | `miniprogram/components/agent-character/`、`miniprogram/components/agent-float/`、`miniprogram/utils/floatingAgentSession.js` |
| 导航与转场 | `miniprogram/custom-tab-bar/`、`miniprogram/components/page-transition/`、`miniprogram/utils/agentNavigation.js` |
| 任务恢复 | `miniprogram/utils/jobPolling.js`、`miniprogram/utils/pendingJobs.js` |
| API 入口 | `backend/app/api/v1/router.py` 及相关路由文件 |
| 模型、存储、预检 | `backend/app/models/models.py`、`backend/app/services/storage.py`、`backend/app/core/config.py`、`backend/scripts/preflight.py` |
| 错误与回归合同 | `backend/app/schemas/errors.py`、`backend/tests/test_api_contract_governance.py` |

源码与旧文档冲突时记录差异，按当前源码建立基线；不能复制技能文件中历史“已完成”描述作为本次测试结论。

## 4. 开发目录与依赖管理

```text
mobile/
  src/
    app/                # App Shell、启动、路由、错误边界
    pages/              # 页面，按第 11 节映射
    components/         # TabBar、角色、浮层、状态卡、转场
    features/           # 领域交互与展示
    platform/
      ports/            # 平台接口
      android/          # Capacitor / Kotlin 实现
      web/              # 浏览器调试与明确的不支持状态
    services/           # API、认证、NDJSON、上传、轮询
    stores/             # auth、companion、assistant、jobs
    styles/             # token、布局、原样迁移的动效
    contracts/          # 服务合同入口
  public/generated-assets/ # 从小程序资产同步；勿手工编辑
  android/              # Capacitor Android 工程、Gradle Wrapper
  scripts/              # 资源同步、单位转换、合同/发布校验
  tests/                # unit、contract、component、visual、e2e
  capacitor.config.ts
  package.json
  package-lock.json
  .env.example
  README.md
shared/                  # 仅在证明可安全复用后抽取纯函数/合同
docs/android/
  development-status.md
  route-map.json
  api-inventory.md
  dependency-lock.md
  baseline/
  verification/
```

先建立 `mobile/` 内的实现，不批量搬动小程序。复制纯函数时注明来源和差异；抽到 `shared/` 前验证 CommonJS/ESM 与小程序打包兼容性。文件实际路径可在保持上述职责的前提下小幅调整，并在 README 说明。

版本选择流程：核对官方 Capacitor Android 与安装环境要求 → 冻结 Capacitor 主版本 → 匹配全部官方插件 → 冻结 Node、JDK、SDK、Gradle、Vue、Vite、测试依赖。写入 `dependency-lock.md`；不使用持续浮动的 latest。

不要按旧迁移文档假定当前机器缺少 SDK；先检测实际环境。存在 Gradle Wrapper 时不必安装独立全局 Gradle。最低 Android 版本必须符合所选 Capacitor 和插件的实际支持范围：[Android 文档](https://capacitorjs.com/docs/android)。

官方插件按需使用 App、Camera、Preferences、Filesystem、Share、Keyboard、Network、SplashScreen、StatusBar。录音、视频选择、微信原生登录、安全存储分别检查插件维护情况、许可、主版本兼容和真机表现；没有可靠插件时实现小型 Kotlin 插件。不要误称官方 Camera 插件已覆盖所有视频拍摄/选择能力。

## 5. 环境与微信云托管配置

### 5.1 本地客户端与后端

客户端拟新增的公开配置，仅包含非秘密：

```env
# mobile/.env.local 示例
VITE_APP_ENV=local
VITE_API_BASE_URL=http://192.168.1.10:8000/api/v1
VITE_AUTH_MODE=dev
```

后端现有本地配置：

```env
ENV=development
DATABASE_URL=sqlite:///./healthmate.db
STORAGE_BACKEND=local
UPLOAD_DIR=uploads
PUBLIC_BASE_URL=http://192.168.1.10:8000
```

注意区分：`PUBLIC_BASE_URL` 是站点根地址；客户端 API base 带一次 `/api/v1`；健康探针在根路径。不要拼出 `/api/v1/api/v1` 或 `/api/v1/health/live`。

真机访问电脑用可互访局域网 IP；标准 Android 模拟器访问宿主可用 `10.0.2.2`，需要实际验证。不能把真机的 `127.0.0.1` 当成电脑。调试明文 HTTP 只在 debug 构建开放；release 禁止明文和远程 `server.url`。

### 5.2 隔离测试云环境

使用单独云托管环境、MySQL 测试库和存储桶；复用相同部署方式，不复用生产数据。测试账号、密钥、Worker 配置与生产隔离。

公网 HTTPS、OPTIONS、Authorization、真实 Capacitor origin、签名 PUT 及播放域名都要联调。origin 取安装包实测值，不假定固定是 `capacitor://localhost`。CORS 不作为身份验证；原生请求也必须 JWT 鉴权。

验证顺序：`/health/live` → `/health/ready` → 登录 → JWT 读写 → NDJSON → COS PUT → 已鉴权播放。记录网关是否缓冲响应、请求超时、上传大小限制与实例冷启动。缺少真实云端地址时完成本地功能和测试，云端项保留“待验证”。

### 5.3 部署档案：代码已实现，生产环境需显式配置

双端云托管启用配置：

```env
DEPLOYMENT_PROFILE=dual_client_cloud
MOBILE_AUTH_PROVIDER=wechat_open_platform
MOBILE_AUTH_ENABLED=true
MOBILE_UPLOAD_ENABLED=true
MOBILE_UPLOAD_BACKEND=s3
```

实现决策：`STORAGE_BACKEND` 继续控制现有小程序/遗留上传路径；新增 `MOBILE_UPLOAD_BACKEND` 独立控制 Android 新上传。生产双端档案配置 `STORAGE_BACKEND=cloud_ref`、`MOBILE_UPLOAD_BACKEND=s3`，每个 `MediaAsset.storage_backend` 仍决定读取、播放、删除和导出适配器。`DEPLOYMENT_PROFILE`、`MOBILE_AUTH_PROVIDER`、`MOBILE_AUTH_ENABLED`、`MOBILE_UPLOAD_ENABLED` 和移动存储后端均加入 Settings、`.env.example`、preflight 摘要、测试和部署模板；路由在开关关闭时返回 503。旧 production 配置未设置档案时按 `wechat_cloud` 兼容，Android 功能默认关闭。当前实现用 S3 兼容接口和地域匹配的 COS HTTPS 端点；Fake S3 和合同测试不等于真实桶联通，COS 兼容性与 CAM 最小权限仍需在目标环境验证。

| 档案 | 环境/数据库 | 登录 | `STORAGE_BACKEND` | `MOBILE_UPLOAD_BACKEND` |
| --- | --- | --- | --- | --- |
| local_dev | development/test，SQLite 或隔离 MySQL | dev-login | local | local |
| wechat_cloud | production，MySQL | 小程序登录 | cloud_ref | cloud_ref |
| mobile_cloud | production，MySQL | 正式移动登录 | cloud_ref | s3（COS） |
| dual_client_cloud | production，MySQL | 两种登录 | cloud_ref | s3（COS） |

旧部署没有提供新档案时兼容原行为，production 默认按 wechat_cloud 校验且 Android 移动功能关闭。生产继续要求强随机密钥、凭据加密密钥、Worker Token、HTTPS、禁止头部身份登录和容器持久存储；按实际启用的功能校验微信及 COS 凭据。

双客户端不能依靠全局 `STORAGE_BACKEND` 切换所有历史资产。保留其旧行为含义，新增移动上传选择；读取、签名、删除依照每条资产的 backend 分派。当前 `s3` 是底层 S3 兼容适配器的值，COS 端点和地域需来自真实存储桶；生产启用 Android 上传时预检要求 `MOBILE_UPLOAD_BACKEND=s3`，真实协议兼容性仍待目标桶验收。

## 6. 登录、统一账号与 Token

### 6.1 首个开发闭环

非生产后端可以用既有 `/auth/dev-login`。debug UI 明确显示测试环境。该接口返回共享 `dev-user`，只用于单账号开发，不测试生产登录、不测试账号隔离，也不与正式数据混用。

需要多用户测试时使用后端测试 fixture 创建独立用户及测试 Token，或建立仅在隔离 test 环境可用的账号机制。不要让 production 因“方便演示”启用 dev-login，也不要向 APK 写入共享生产 JWT。

### 6.2 正式移动登录：拟新增

保持 `POST /api/v1/auth/wechat` 原合同。新增 `POST /api/v1/auth/mobile/wechat`，使用原生微信登录取得的移动应用 OAuth code，由服务端使用移动应用的凭据交换并验证身份。

实施前验证微信开放平台移动应用的注册/审核、AppID、包名、应用签名和 SDK 回调配置。移动 code 与小程序 code 不通用；正式签名变更也可能影响登录配置。缺少外部配置时允许继续开发适配器及 mock 合同，但不能标记真实微信登录通过。

新增统一身份表建议：

```text
user_identities
  id
  user_id -> users.id
  provider                  # wechat_miniprogram / wechat_mobile / 后续 phone
  subject                   # 服务端验证的提供方标识
  issuer                    # AppID 或身份签发范围
  union_subject nullable    # 如服务端确实取得且验证过的 unionid
  created_at
  UNIQUE(provider, issuer, subject)
```

openid 具有应用范围，唯一键要包含签发范围。unionid 的取得条件和绑定关系按实施时微信官方资料核对，不能假设每次都有，也不能接受客户端自报的 unionid 作为身份依据。

当前 `users.openid` 非空且唯一，必须设计兼容迁移：新增身份表、回填原小程序身份，继续让旧登录定位同一 user；移动新用户的旧字段可采用明确的内部兼容标识或通过迁移调整字段约束。内部兼容标识不是微信 openid，不得提交给微信 API、管理员 openid 校验或 CloudBase 权限逻辑。审计所有使用 `user.openid` 的位置再选择方案。

账号关联采用两个身份的可信证明和显式用户确认，可用“已登录小程序生成短时一次性关联码，已登录安卓提交关联码”的流程。关联码绑定目标 user、调用者、用途、有效期和单次消费；避免枚举、重复消费和并发绑定。

如果两个身份已经各有业务数据，不自动合并或直接改全部外键。第一版先拒绝此类自动关联，提示受控处理；不能丢失记录、授权、策略历史或审计。身份解绑不得移除最后一个可用登录方式。

### 6.3 Token 存储与 401

- 普通角色选择、语音偏好等轻量数据使用 Preferences。
- Token 使用 Android Keystore 支持的安全存储；Preferences 使用 Android SharedPreferences，不能当成已加密的 Token 仓库：[官方 Preferences 文档](https://capacitorjs.com/docs/apis/preferences)。
- 保留现有 access-token 合同即可完成第一版；过期时引导重新登录。
- 当前没有 refresh-token 接口，不能写一个前端“刷新”函数就声称支持无感续期。如新增刷新，需要服务端 Token 轮换、撤销、并发与重放设计和测试。
- 401 合并为一次认证恢复；GET 可在认证成功后重试一次，非幂等写操作不盲目重放。
- 403 展示权限问题，409 刷新版本或提案，429 按 retry_after 处理；不要都当成 Token 过期。
- 退出登录清理 Token、健康缓存、关联草稿、用户任务指针和签名链接；按 user_id 隔离普通缓存。

## 7. HTTP、错误、NDJSON 与任务恢复

### 7.1 平台接口

页面和领域组件调用平台接口，不直接使用 `wx.*` 或 Capacitor 插件。

| 接口 | 需要实现的责任 |
| --- | --- |
| AuthPort | 登录、退出、读取凭证、身份关联 |
| HttpPort | JSON 请求、二进制传输、NDJSON、超时、取消 |
| SecureStoragePort / PreferencesPort | 敏感凭证与普通偏好分开 |
| NavigationPort | push、replace、主导航、back |
| MediaPickerPort | 图片拍摄/选择、视频选择、拒权、取消 |
| RecorderPort | 权限、开始、停止、取消、时长和格式 |
| AudioPort | 分段播放、停止、错误、音频焦点 |
| FilePort | 受控临时文件、导出、分享、清理 |
| DialogPort | 与小程序同风格的确认、菜单、提示 |
| LifecyclePort / ViewportPort | 前后台、销毁、安全区、键盘 |

所有异步操作有成功、用户取消、拒权、超时和失败终态；用户取消不是异常 Toast。平台能力不存在时返回明确状态，不能伪造成功。

### 7.2 API 错误和重试

服务端原始错误保持现有格式：

```json
{"error":{"code":"CONFLICT","message":"请刷新后重试","retryable":false,"request_id":"request-id","details":{}}}
```

客户端可转换为 `code/message/retryable/requestId/details/status`，不能要求后端只为安卓把 snake_case 改成 camelCase。校验和解析失败不得吞掉；页面显示可恢复原因并保留 requestId 供诊断。

自动附加 Bearer Token 与 platform header。只对 GET 和服务端已实现幂等的写操作有限重试。使用同一请求的相同幂等键重试；不要每次重试生成新键。当前 AgentRequest 不含客户端请求标识，需检查创建任务的幂等实现，不能只加请求头就宣布重复提交安全。

### 7.3 已存在接口与拟新增接口

下表路径均以 `/api/v1` 为前缀；请求字段及响应类型最终取当前 OpenAPI 和源码，表中仅用于定位。

| 接口 | 状态 | 安卓用途 |
| --- | --- | --- |
| POST /auth/dev-login | 现有，非生产 | 开发账号 |
| POST /auth/wechat | 现有 | 保持小程序使用 |
| POST /auth/mobile/wechat | 已实现代码 | 正式安卓登录；开放平台 OAuth code 由服务端兑换 |
| GET /auth/me | 已实现代码 | 恢复会话及读取关联身份类型 |
| POST /auth/link/start、/auth/link/complete、/auth/link/unlink | 已实现代码 | 双身份关联/解绑；健康数据不做静默合并 |
| POST /agent/respond、/agent/respond/stream | 现有 | 三人格核心助手 |
| GET /agent/runs/{run_id} | 现有 | 读取已知 run 的真实状态 |
| POST /agent/runs/{run_id}/cancel、/retry | 现有 | 控制与重试，按真实服务端语义 |
| GET /agent/actions/{proposal_id} | 现有 | 读取待确认提案 |
| POST /agent/actions/{proposal_id}/confirm、/reject | 现有 | 用户确认/拒绝 |
| GET /agent/plans/current、PUT /agent/plans/items/{item_id} | 现有 | 计划与打卡状态 |
| POST /media/upload | 现有，production 拒绝 | 本地图片/视频上传 |
| POST /media/register-cloud | 现有 | 小程序登记 CloudBase 文件 |
| PUT /media/{media_id}/refresh-source | 现有 | 原小程序刷新链路 |
| GET /media/{media_id}/playback | 现有 | 核对并补齐混合存储播放 |
| GET /media/mobile-upload/options | 已实现 | 返回 Android 上传后端和按用途区分的服务端上限 |
| POST /media/mobile-upload/sessions | 已实现 | 以用户范围 `request_id` 创建或恢复 COS/S3 上传会话 |
| POST /media/mobile-upload/sessions/{media_id}/complete、DELETE /media/mobile-upload/sessions/{media_id} | 已实现 | 验证并提交或取消上传；终态重试会轮换对象键 |
| POST /vision/food-analysis | 现有 | 创建识餐分析 |
| PUT /vision/food-analysis/{analysis_id}/correct | 现有 | 校正草稿 |
| POST /vision/food-analysis/{analysis_id}/finalize | 现有 | 用户确认后入库 |
| POST /media/motion-analyses、GET /media/motion-analyses/{analysis_id} | 现有 | 动作分析主链路 |
| POST /harness/voice/transcribe、/synthesize | 现有 | STT/TTS |
| GET /privacy/export/preview、POST /privacy/export | 现有 | 导出预览及文件 |
| GET /privacy/cloud-media、DELETE /privacy/account | 现有 | 必须扩展双存储删除合同 |
| GET /privacy/deletion-status | 现有 | 核对删除回执语义并扩展 |

不要接标记 deprecated 的 motion-jobs/kinetics-jobs 作为新版默认入口。能力中心、个人策略、健康状态与 AI 配置使用现有 API，保留授权、版本冲突和确认语义。

### 7.4 NDJSON 的真实语义

当前 `/agent/respond/stream` 的实现是：先 `await respond(...)` 得到完整结果，再发送 `meta → stage* → answer → delta* → done`。`answer` 是安全复核后的完整文本，delta 是同一文本的逐字展示。不要写“实时模型 token”或根据 stage 到达时间推断真实执行阶段耗时。

客户端需要：

1. 用流式 TextDecoder 处理中文跨字节块，按换行组装 JSON，处理尾段和空行。
2. 多条事件在同一块、单条跨多块、CRLF、空 delta、未知事件、错误 JSON 都有明确行为。
3. meta 保存 run_id、request_id 等；stage 展示已有执行记录，不造假进度百分比。
4. answer 缓存在 canonicalAnswer，不直接与后续 delta 连续追加，防止整段回答重复两遍。
5. delta 更新 visibleText；done 用最终回复校正缺字/重复，保持同一消息节点。
6. stream 缺少 done 时视为未完整结束；有 run_id 就读取 run；没有 run_id 就提示结果未知，不自动创建第二个 run。
7. AbortController 立即停止网络与展示；有 run_id 时按需调用 cancel，但停止展示不等于撤销已写入数据。
8. 原生 HTTP 插件未证明支持逐块读取时，流式通道保留 fetch；不要全局替换后意外变成整段缓存。
9. 网关不支持流式时提供明确的完整响应模式，只展示真实等待与最终结果。

客户端已通过 `VITE_AGENT_RESPONSE_MODE=ndjson|full` 显式选择流式或完整 JSON 响应。`full` 模式只调用一次 `/agent/respond`；客户端不会因 NDJSON 连接错误自动重发 Agent 请求。

特别注意：当前计算完成前客户端可能拿不到 run_id。WP2 必须确定并记录取消/断线边界。若要承诺“生成中随时取消、即使首个 meta 未到也可恢复”，需新增持久化请求标识和先返回 run_id 的异步创建/查询合同；这属于后端新增能力，不在前端用定时器模拟。可以独立工作包实现，保留旧接口兼容。

## 8. COS 媒体通道与历史 CloudBase 文件

### 8.1 存储实现原则

优先给安卓新增独立私有 COS 桶与明确的 COS adapter。复用既有 MediaAsset 账本，增加上传会话表；不把文件存入云托管持久磁盘，不公开整个桶，不在客户端分发永久云密钥。

旧 S3 adapter 会探测/尝试创建桶并使用 boto3，其行为未经本次 COS 联调验证。生产存储桶应由受控基础设施预创建；适配器初始化不要悄悄建桶或吞异常。

Android COS production 预检要求桶名符合 `BucketName-APPID`，并在使用腾讯标准地域端点时校验 endpoint 地域与 `S3_REGION` 一致；腾讯文档要求 S3 兼容应用选择与桶相同地域的 COS 服务地址。[桶命名规范](https://cloud.tencent.com/document/product/436/13312) · [S3 兼容配置](https://intl.cloud.tencent.com/document/product/436/34688)。COS CORS 按真机 WebView 的 Origin、`PUT`/`GET`/`HEAD` 和实际请求头设置；`OPTIONS` 由 COS 响应预检，不是可配置的业务方法。[CORS 配置](https://cloud.tencent.com/document/product/436/13318)。

COS 支持绑定对象键和有效期的预签名上传；简单预签名 PUT 的失败恢复需要重新上传，不能据此声称支持断点续传：[COS 预签名上传](https://cloud.tencent.com/document/product/436/14114)。

### 8.2 当前上传合同

以下接口已经在当前源码中实现；它们是当前 Android/COS-S3 上传合同，不再使用早期草案中的 `/media/uploads` 和 `upload_id` 命名。云托管部署及真实 COS 联通仍待验收。客户端先读取上传选项：

```http
GET /api/v1/media/mobile-upload/options
Authorization: Bearer <token>
```

响应返回 `storage_backend`、`max_upload_bytes` 和 `max_upload_bytes_by_purpose`。创建或恢复会话：

```http
POST /api/v1/media/mobile-upload/sessions
Authorization: Bearer <token>
```

```json
{
  "request_id": "<稳定的客户端操作编号>",
  "original_name": "meal.jpg",
  "content_type": "image/jpeg",
  "media_type": "image",
  "size_bytes": 180000,
  "purpose": "food_analysis"
}
```

普通新会话响应：

```json
{
  "media_id": 123,
  "storage_backend": "s3",
  "method": "PUT",
  "upload_url": "https://<controlled-cos-host>/<key>?<short-lived-signature>",
  "headers": {"Content-Type": "image/jpeg"},
  "expires_in": 300,
  "max_size_bytes": 4194304,
  "already_completed": false
}
```

同一用户以相同 `request_id` 和相同文件元数据重试会恢复原会话；元数据不一致返回冲突。已完成的请求返回同一 `media_id` 和 `already_completed: true`，无需再次 PUT。每种用途的真实大小限制由服务端配置返回；不能让客户端另设更大的限制。图片识餐、运动视频、语音分别检查现有业务限额，不能都套 `MAX_UPLOAD_MB=200`。

完成和取消接口只使用路径中的 `media_id`，不接客户端自由指定的 bucket/key/user_id/任意下载 URL，也不接受客户端自报对象元数据作为验证依据：

```http
POST /api/v1/media/mobile-upload/sessions/{media_id}/complete
DELETE /api/v1/media/mobile-upload/sessions/{media_id}
```

完成响应返回 `ok`、规范化的 `media_id`、`storage_backend` 和实际文件大小。客户端通过适配层兼容现有业务接口命名，不全局把后端 ID 改叫 `asset_id`。移动上传开关关闭时所有这些 Android 专用接口返回 503。

### 8.3 服务端上传状态机

```text
pending → verifying → ready
   ├─ expired
   ├─ cancelled
   └─ rejected
```

上传会话建议包含 user_id、用途、隔离区 key、提交后的 key、声明大小/类型/hash、状态、有效期、幂等键、media_id、验证结果和清理标记。

实现顺序：

1. JWT 和用途授权；校验类型、文件名、大小、配额；生成服务端随机 key。当前请求使用用户范围的 `request_id` 作为幂等编号，数据库同时记录媒体资产与持久上传会话。
2. 先持久化上传会话，再签发绑定对象/方法/有效期/必要头部的短时 URL。
3. 上传到隔离区；不立即给识餐/Worker 使用。
4. 完成时使用服务端权限 HEAD 检查存在性、实际字节数、类型；必要时受限读取并解码/校验内容。
5. 当前合同不接受客户端 SHA-256；对象存储 metadata 不作为可信内容证明。若业务增加 hash 校验，必须由服务端或受信 Worker 实际计算，不能把 ETag 普遍当作 SHA-256。
6. 短时 PUT URL 可能在到期前被再次使用，因此不能把可覆写的隔离区对象直接当成不可变资产。采用已验证版本读取，或服务端复制到客户端无写权限的提交区，再登记正式 key。
7. 当前实现以用户与 `request_id` 的唯一约束保证重复申请收敛到同一会话；重复 complete 得到同一 `media_id`。验证或复制中断可恢复，不能遗留半登记记录。
8. 只允许 ready 且属于当前用户的 media_id 创建业务任务；业务确认写入独立于媒体上传成功。
9. expired/cancelled/rejected 会话有可重试清理；考虑原签名到期前再次写入的可能，过期后再次对账清理，禁止把一次删除当作永久完成。

预签名 PUT 是否能强制大小取决于具体协议和签名头部实现，不能仅凭申请参数声称已在对象存储层限制大小。登记前验证、下载字节上限和超额对象清理必须保留；若必须在上传时硬限制，选已验证的受控上传合同并说明理由。

### 8.4 播放、Worker、删除

- 按资产自身 backend 选择 adapter；检查用户所有权后签发短时读取地址。
- COS 链接过期由服务端为同一资产重新签名，不创建新任务。
- Worker 的 SSRF、HTTPS、大小和解码校验继续生效；服务端签名不成为绕过校验理由。
- 审计媒体、动作预览、导出、隐私删除、对账服务对全局 get_storage 的依赖，防止双存储串读或误删。
- Android 无法调用 `wx.cloud.getTempFileURL/deleteFile`。旧 CloudBase 私有文件在安卓上可读和可删，需要服务端受控 CloudBase 能力、可信身份映射及真实联调。
- 不简单关闭 CloudBase 创建者规则，不把安卓 JWT 当成 CloudBase 原生身份凭证。
- 历史 CloudBase 无服务端权限时显示“需要在微信端处理”的真实限制；此时不能宣称完整跨端媒体/删除已完成。
- 完整正式版优先补齐服务端签名与删除能力；若迁移旧对象到 COS，使用独立、可追踪、可回滚的迁移任务，不在页面访问时静默搬文件。

### 8.5 永久删除的完成标准

扩展现有隐私服务和 deletion-status，不让安卓构造虚假的 `cloud_files_deleted` 回执。按资产后端删除 COS、CloudBase、动作预览和临时上传对象，覆盖身份表、上传会话、关联码、任务及本地缓存。

删除对象失败保留受控的待处理清单和重试状态；按项目既有删除合同处理数据库数据，不先毁掉唯一对象定位信息。未收到可信服务端删除证据的 CloudBase 文件仍标记 client_reported 或 pending，不能包装成平台确认成功。最终状态区分 pending、部分失败、已验证完成；匿名后续查询如有需要，使用仅可查删除状态的短期回执凭证，不保留普通业务访问权。

## 9. 语音、相机、视频与生命周期

### 9.1 录音及 STT

保留“按住说话、松开发送”，同时支持文字。安卓运行时申请麦克风权限，拒权解释用途并保留文字入口。结束录音、用户取消、切后台、音频焦点丢失等必须释放资源。

第一版接现有 `POST /harness/voice/transcribe`：`agent_id`、`audio_base64`、`format`、可选 `request_id`。插件实际录出的容器与编码必须匹配 format，不改扩展名假装转码。对每种语音 provider 核对格式、采样率、声道、长度和字节限制，并用真机录音验证。

base64 会增加体积，短录音可以保留原合同；长录音或视频不通过 JS 全量 base64 处理。若改成文件上传/asset 引用，新增版本化合同并保留小程序兼容。

### 9.2 TTS

现有 `/harness/voice/synthesize` 返回 `segments[{index,audio_base64,content_type}]`、partial 等信息，部分情况兼容单段字段。安卓先适配这个合同；按顺序写受控临时文件或解码播放，结束后清理。

新播放停止旧播放；播报失败不删除文字回复；partial 结果不提示全部完成。小健/小康能力和用户声音偏好来自后端。小管家是否支持语音按真实 persona 能力展示，不擅自打开。

### 9.3 图片/视频

Android Photo Picker/原生文件 URI 的访问生命周期由 MediaPickerPort 封装；大视频使用原生流式文件传输，避免 JS 全量读文件。录视频、选视频分别测试；旋转、EXIF、压缩、真实 MIME、取消和权限变化都要处理。

第一版单文件 PUT 支持“重新发起失败上传”，不承诺后台断点续传。需要连续后台上传或分块恢复时另做 Android 原生任务与服务端分块合同，不能只保存百分比。

### 9.4 后台与返回

- 切后台停止录音和非必要角色动画，暂停前台轮询。
- 恢复时用任务 ID/run_id 查询服务器，丢弃过期 UI 定时器。
- 任务指针按用户保存，不持久化长期签名 URL 或整段敏感对话。
- Android 返回键优先处理键盘、确认框、展开浮层，再回路由；根页退出遵循统一产品规则。
- 导出文件进入受控缓存并通过 Share 分享，避免申请无必要的全盘存储权限。
- 默认不申请悬浮窗权限：本应用的“悬浮助手”是 WebView 内的组件。

## 10. UI、角色与动效复刻

### 10.1 唯一基线与转换

冻结当前页面截图、状态、资源哈希及关键动作录屏。WXML 转 Vue、WXSS 转 CSS；资源从 `miniprogram/assets/` 自动同步。保持文案、页面块顺序与入口关系。

转换工具使用语法/声明层处理，避免正则替换损坏字符串、URL、keyframes、选择器或注释。替换 view/text/image 标签后同步修正标签选择器；避免普通按钮的浏览器默认边框/内边距改变几何尺寸。微信组件的默认样式不能假定等于 HTML 元素。

750 设计宽度：手机上 `1rpx = 实际内容宽度/750`。可以统一转换为基于 `--rpx` 的 calc。平板居中手机宽度容器时必须基于容器宽度，不能仍用整个屏幕的 vw 放大内部尺寸。只统一单位，不手工改每页数值。

### 10.2 固定 token

| 用途 | 值 |
| --- | --- |
| 页面底 / 卡片 | #f7f7f2 / #ffffff |
| 强调填充 / 浅强调底 | #c4e267 / #e9efd9 |
| 深色卡 / 渐变高光 | #111613 / #2f3a1e |
| 主文字 / 次要文字 | #111613 / #5f665f |
| 品牌绿文字 / 分隔线 | #506336 / #eeeee6 |
| 危险 / 警告 | #c0392b / #9b5b00 |

卡片：无可见边框，圆角 22rpx，阴影 `0 8rpx 26rpx rgba(17,22,19,.035)`。页面左右 32rpx、卡片间距 24rpx、卡片内边距 32rpx，其余间距采用技能规范的刻度。柠檬绿底配深色文字。

安全区增加到布局外层；不同时让 native inset、CSS env 和固定 padding 重复补偿。键盘弹出不缩放整个页面；保证输入框、发送按钮和末条消息可见。

### 10.3 圆形按钮

width/min-width/max-width 一致，height/min-height/max-height 一致，border-box、50% 圆角、flex-shrink:0。外圆与内部图标分别定尺寸。浮助手输入区麦克风/发送外圆按 64rpx 基线，内部图标按源码核对。空输入、非空、加载、禁用状态不能改变按钮外形。

### 10.4 角色与悬浮会话

复用当前两位角色的场景、精灵帧、姿势、大头照和 TabBar 图标。小健健身房静息哑铃、小康养生馆静息翻书，以及翻到右侧的帧序保持一致。精灵图使用离散帧，不在帧间做模糊插值。

状态机按实际组件核对：idle/listening/thinking/planning/presenting/speaking/success/error。录音不能切到完全不同站位；聆听仅用轻量波形；动作终态回静息。图片预加载失败保留旧场景或角色图标回退。

Pinia 的 companion/assistant 状态位于页面外层，切页保留角色、run_id、消息和草稿；进入计划页保留同一审阅对话。home 和 chat 的助手呈现按原页面关系控制，避免全局助手重复叠加。长期健康对话缓存服从隐私设置。

### 10.5 转场与导航

底部视觉保留“健身房或养生馆 / 中央仪表盘 / 小管家”。中央仪表盘是菜单，不是假造的新 tab 页面；顺序记录、计划、快速开始、我的、设置。小程序 app.json 的正式 tab 声明只有 home/chat，安卓应以实际 custom-tab-bar 交互为准。

全局遮罩先覆盖旧页，再准备/替换目标页，目标根布局可见后淡出。首帧背景、Activity、Splash、WebView 根背景保持 #f7f7f2。连续点击、资源失败、已访问页面重进、返回键都应有明确终态；慢数据不让遮罩无限驻留，进入目标页加载状态。

状态反馈 150～250ms，内容入场 300～400ms，缓动按现有组件。高频动效优先 transform/opacity；按下反馈透明度立即变化，松手约 80ms 恢复，避免通用 scale 覆盖定位 transform。常驻菜单用 class 状态过渡，避免挂载动画覆盖收起位移。

迁移现有特殊交互：workout 标签扩张、scan/media 实测高度展开、goals 速度采样补一格、records 弧线滑删、plan 完成反馈、scan 四步、chat 确认状态。弧线滑删保留纵向滚动、主轴锁定、取消恢复和服务端失败恢复。

### 10.6 技能适用范围

小程序改动遵守 `.agents/skills/healthmate-motion/SKILL.md` 的 renderer 闸门和验收要求。新安卓实现复用其 token、时长与产品语义；小程序逻辑层“不能用 document/window”不能照搬为安卓禁用 DOM。安卓 WebView 可以在平台/组件实现中使用 DOM、Pointer Events、requestAnimationFrame；无需为了复刻引入 GSAP 或 Skyline worklet。

系统减少动画开启时减少非必要位移，仍保留状态、确认和可访问性提示；不要用降低角色质量代替性能优化。

## 11. 全部页面迁移矩阵

下表对应核对时 app.json 的 28 条路由。Android 路径是建议映射；启动时重新生成 route-map，并记录 query 参数、入口、返回路径、助手是否出现、API 和验收状态。agent-character/agent-float 是组件，不额外计为页面。

| 小程序路径（pages/ 下） | Android 建议路径 | 工作包 | 核心验收 |
| --- | --- | --- | --- |
| home/index | /home | WP3 | 双场景、角色、语音入口、导航 |
| chat/index | /chat | WP3 | 小管家、流式展示、确认提案 |
| plan/index | /plan | WP3 | 审阅、确认、真实计划、完成/恢复 |
| checkin/index | /checkin | WP4 | 真实打卡与状态刷新 |
| records/index | /records | WP4 | 能量、记录入口、真实统计 |
| records/diet | /records/diet | WP4 | 饮食记录、校正与弧线删除 |
| records/exercise | /records/exercise | WP4 | 运动记录、删除与失败恢复 |
| trends/index | /trends | WP4 | 日期口径、图表、空/不足状态 |
| scan/index | /scan | WP4 | 拍照、四步识餐、确认前不入库 |
| media/index | /media | WP4 | 视频、分析任务、恢复与播放 |
| workout/index | /workout | WP4 | 参数选择、计划、已有交互 |
| exercise-detail/index | /exercise-detail | WP4 | 运动信息、素材、结果展示 |
| profile/index | /profile | WP5 | 账号、角色、统计入口 |
| profile/edit | /profile/edit | WP5 | 校验、保存、健康状态失效刷新 |
| settings/index | /settings | WP5 | 账号、偏好、真实能力状态 |
| settings/ai/index | /settings/ai | WP5 | 原服务端配置合同、密钥不泄漏 |
| settings/privacy/index | /settings/privacy | WP5 | 导出、可信删除与失败状态 |
| settings/capabilities/index | /settings/capabilities | WP5 | 范围授权、暂停/恢复、审计 |
| goals/index | /goals | WP5 | 目标、滑杆算法、真实保存 |
| state/index | /state | WP5 | 安全约束、数据质量与状态来源 |
| insights/index | /insights | WP5 | 主动建议、反馈与边界 |
| report/index | /report | WP5 | 可达入口、周报、真实状态 |
| evaluation/index | /evaluation | WP5 | 评测来源与未验证能力说明 |
| policy/overview/index | /policy | WP5 | 现有单模板候选与开始流程 |
| policy/protocol/index | /policy/protocol | WP5 | 协议冻结、版本与确认 |
| policy/episode/index | /policy/episode | WP5 | 执行、自报负担、幂等 |
| policy/review/index | /policy/review | WP5 | 预览、确认、撤回状态 |
| policy/history/index | /policy/history | WP5 | 历史、停止、记忆重置 |

所有数据页实现 loading、empty、error、success；没有数据与数据不足区分。迁移过程中发现原小程序缺陷先登记。涉及安全、错误展示或已有不通入口的必要修复单独提交，并写明基线差异，不能为了像素相同复制危险行为。

## 12. 工作包、依赖与完成门禁

### WP0：冻结与清点

改动：仅新增开发台账、路由/API/资源/动效清单、依赖环境检测和测试基线。

任务：保存当前 commit、dirty diff 状态、28 条路由、关键截图与录屏、资源 SHA-256、现有测试真实结果；测试无法执行时记录原因。确定测试资源缺口，不把测试历史摘要视为通过。

交付：`docs/android/development-status.md`、`route-map.json`、`api-inventory.md`、`dependency-lock.md`、`baseline/`。

门禁：知道当前项目哪些可验证、哪些只是源码能力；视觉参考可追溯。缺少微信工具/真机时可先做基础平台和联网验证，逐页等价验收仍须有真实基线，不用模拟图冒充小程序截图。

### WP1：Android 工程、平台层和联网

依赖 WP0。新增 mobile 工程、固定依赖、Android Shell、基础路由、设计 token、资源同步、HTTP/偏好/安全存储/对话框/生命周期接口、debug/release 网络配置。

门禁：本地 debug APK 构建成功并实际安装，显示应用首屏，访问正确根路径的 live/ready，能切换本地/测试云配置；release 静态检查无明文、无 dev server。没有设备时只标记构建通过，安装与首屏待验证。

### WP2：认证、请求和生产适配骨架

依赖 WP1。开发登录先跑通；增加部署档案与测试、新身份层及移动登录合同、请求重试规范、NDJSON parser、任务指针；建立 COS adapter 与上传状态机合同。优先识别真实云端、登录与旧 CloudBase 文件权限阻塞。

门禁：隔离测试认证与错误合同通过；dev-login production 拒绝；双身份不混用 openid；重复请求安全边界写清；本地 NDJSON 字节边界/取消/缺少 done 测试通过。外部真实登录可待资源，但不能向后续正式发布借用测试身份。

### WP3：Agent 核心与最小 APK

依赖 WP2。实现 home、agent-character、chat、agent-float、TabBar、转场、plan。连接当前 respond/提案确认/计划读取合同；需要异步 run 创建能力时单独新增而不破坏原接口。

门禁：从小健/小康发起计划到计划页审阅、确认、写入、查看，切页状态保持；确认前不写计划；停止展示立即生效；回答无重复两段；失败可恢复。输出 debug APK、截图和验收记录。

### WP4：记录、多媒体与真机链路

依赖 WP3 及 WP2 媒体合同。实现记录、趋势、打卡、识餐、视频、workout、详情；完成 COS PUT/验证/登记/播放/清理、STT/TTS、任务恢复。

门禁：图片、视频、录音成功/拒权/取消/弱网/后台恢复都有真机证据；识餐确认前无饮食记录；重复 finalize/complete 无重复数据；不能串读他人素材；上传过期不会重新创建业务任务。

### WP5：全部页面与双端业务一致性

依赖 WP4。完成第 11 节其余页面、正式账号关联 UI、能力中心、个人策略、设置和隐私。补齐旧 CloudBase 在安卓的读取/删除及后台混合存储清理；如暂时无法完成，明确记录对应正式门禁未过。

门禁：28 路由都可达或有明确书面不迁移决定；没有空白占位当完成；版本冲突、授权暂停、源记录校正后失效、策略停止等与原合同一致；两端同用户可见同一真实记录。

### WP6：正式环境验证与发布准备

依赖 WP5。验证正式移动登录、签名匹配、两个独立账号隔离、关联与解绑、COS 与旧 CloudBase 权限、导出、删除、备份恢复、断线和网关限制。

门禁：正式包不含 dev-login；不含密钥；已验证的删除状态有平台依据；升级/回滚方案可执行；原小程序回归和后台合同测试通过。缺少正式平台账号、签名或真机时保持未验证，不通过改文案掩盖缺口。

### WP7：签名、安装与交付

依赖 WP6 和用户提供的正式应用标识/签名。输出 debug APK；有正式签名条件且获相应发布授权时输出 release APK。商店需要时增加 AAB，仍交付用户要求的 APK。

门禁：正式构建产物验证签名、包名、版本、HTTPS、权限和安装升级；记录源码提交、SHA-256、设备、测试时间、已知限制。没有 keystore 时不生成“正式发布”假产物，debug 可先交付。

建议提交顺序：baseline → mobile shell → config profile → identity schema/API → transport/NDJSON → home/assistant → plan → COS uploads → records/media/voice → remaining pages → privacy → release checks。依赖交叉时拆小提交，避免一次提交把页面、数据库、安全和云配置全部改完。

## 13. 测试与验证命令

本节是执行清单，**本次编写文档没有运行这些项目测试、构建、迁移或云端请求**。实施时按修改范围执行，保存退出码与关键摘要，不为了数字好看反复跑无关重测。

### 13.1 原项目回归

在仓库根目录：

```powershell
node --test miniprogram/tests/*.test.js
node scripts/audit_miniprogram_ui.mjs --strict
git diff --check
```

在 backend 目录，使用隔离测试配置：

```powershell
python -m pytest
python scripts/preflight.py
python -m alembic heads
```

若改 Worker 合同，在 ai-worker 目录执行其相关测试。全量 Worker 测试需要额外模型/依赖时，记录缺口和相关测试范围。

`preflight.py` 当前只校验配置，不连接数据库。`alembic heads` 只读迁移脚本；不等于实际数据库已经升级。不要在生产环境试跑 upgrade/downgrade。新迁移在隔离 SQLite 与实际部署版本的 MySQL 上验证升级、数据回填、重复升级和恢复策略。

### 13.2 mobile 必须创建的脚本合同

当前 `mobile/package.json` 已提供以下测试与构建脚本：

```powershell
npm ci
npm run lint
npm run typecheck
npm run test:unit
npm run test:contract
npm run test:component
npm run build
npm run cap:sync:local
npm run cap:sync
```

`test:visual` 尚未实现：当前仓库没有冻结的小程序真机基准截图集，现有 Android 路由截图只证明页面可达和渲染，不能证明视觉一致。取得并冻结真实基准后，再加入像素差异检查；不可用 Android 自身截图与自身对比冒充迁移验收。

在 mobile/android 目录：

```powershell
.\gradlew.bat assembleDebug
```

release 构建前执行拟新增 `npm run verify:release`，检查配置、域名、权限、包标识和 dev-login 引用。签名通过受控环境提供，不能在命令行或日志里明文打印密码。

### 13.3 必须覆盖的高价值场景

| 类别 | 场景与预期 |
| --- | --- |
| 身份 | 小程序 code/移动 code 错用被拒；匿名与伪造头无业务权限；两账号不能互读 |
| 关联 | 过期、重复、并发、错误关联码被拒；已有双账号数据不被静默合并 |
| 生产配置 | mobile/dual profile 必要项缺失启动失败；dev-login、本地存储继续禁用 |
| NDJSON | 中文跨块、尾段、重复事件、answer+delta、缺 done、401/429、取消 |
| 请求 | 多个 401 不触发并行登录；非幂等写不重放；同键不同 body 被拒或冲突 |
| 提案 | 确认前不写；版本/授权变化须重新核对；重复确认不产生第二份计划 |
| 上传 | 改 bucket/key/user_id、超额、伪类型、未验证文件、跨用户 complete 全部被拒 |
| 可变对象 | 原 PUT URL 再次写入不能改变已登记资产；取消/过期后再上传能被清理 |
| 删除 | COS、CloudBase、预览、未登记对象分别失败时状态真实，重试可恢复 |
| 语音 | 无权限、插件录音格式不符、切后台、STT 503、TTS partial、音频焦点丢失 |
| UI | 空文本/有文本按钮几何一致；转场失败不白屏；键盘不挡输入；头像跨页保持 |
| 策略/授权 | 版本冲突、暂停授权、源记录更正、停止策略后的状态与后端一致 |

不要只测试“函数调用了一次”这类镜像实现断言；用业务结果和可观察状态验证。文档、样式等低风险修改不另造一套无意义测试。

### 13.4 视觉和真机证据

统一内容宽度、字体设置、测试数据、系统缩放与截图裁切范围。原生系统栏差异单独登记；不能拿浏览器转换图替代真实微信截图。

目标：主要边界偏差≤2 CSS px；圆形宽高差≤1 CSS px；token 色值一致；文本换行一致；关键组件像素差异目标≤2.5%。像素阈值是诊断目标，字体抗锯齿和系统栏差异需合理处理及人工确认，不能无限放宽阈值。

至少覆盖 home 两角色、chat、浮助手展开/收起、plan 待确认/已写入、records、scan 四步、media 任务、settings/privacy、policy 主流程的状态截图。动效录屏覆盖切页无白闪、角色帧序、翻书、语音切换、弧线删除、轮盘与流式展示。

Playwright 证明浏览器布局与交互；模拟器证明安装和部分运行；真实设备证明录音、相机、后台、弱网和动画观感。三类证据分别标注，不能互相替代。至少一台主流 Android 与一台性能较低设备；60fps 是目标，按实际帧耗时报告，不用短视频肉眼流畅代替指标。

## 14. 主要代码改动落点

| 文件/目录 | 要做的变化 |
| --- | --- |
| mobile/ | 新安卓/Web 客户端和测试、平台适配、Android 工程 |
| backend/app/core/config.py | 档案、移动开关、COS 配置与保守校验 |
| backend/.env.example、backend/scripts/preflight.py | 新字段说明、脱敏摘要和预检 |
| backend/app/models/models.py、models/__init__.py | 统一身份、关联/上传会话；复用 MediaAsset |
| backend/migrations/versions/ | 新增迁移；以实际 head 为父，不改历史迁移 |
| backend/app/api/v1/auth.py | 保留旧登录，新增移动登录、受控关联 |
| backend/app/api/v1/router.py | 注册新 API，保持原前缀 |
| backend/app/services/storage.py 及新增 adapter | 混合资产分派、COS、受控 CloudBase 能力 |
| backend/app/api/v1/media.py | 上传会话、完成验证、播放/刷新与旧资产兼容 |
| backend/app/services/motion/media_storage.py | 动作预览与 Worker 素材的混合存储审计 |
| backend/app/services/media_reconciliation.py | 孤立对象、过期会话、删除失败对账 |
| backend/app/api/v1/privacy.py 及对应服务 | 双媒体通道导出/删除与可信回执 |
| backend/app/api/v1/agent.py / schemas/agent.py | 必要时增加请求关联/幂等/异步 run；兼容旧合同 |
| backend/tests/ | 登录、生产校验、上传、隔离、确认、删除与恢复测试 |
| docs/WECHAT_CLOUD_RUN_DEPLOY.md | 双客户端部署步骤、字段、CloudBase/COS 权限 |
| miniprogram/ | 默认保留；必要安全/合同兼容修复独立提交并回归 |

文件名是定位建议，新增 service/schema 文件按仓库现有结构组织。后端逻辑集中到服务层，不把 COS、OAuth 和删除工作流全塞进单个路由函数。

## 15. 安全、兼容与回滚

- 所有业务对象按 JWT user_id 查询；客户端平台、subject、media_id、run_id 不自动证明所有权。
- 请求日志不记录 Token、健康对话原文、录音、签名 URL、API Key 或 OAuth code。
- 私有对象直传仅临时授权；完整 bucket 列表和删除权限留在服务端。
- OAuth code 不写日志，不反复使用；登录/关联接口限制频率。
- 保持原错误格式、旧字段和小程序登录可用；新增响应字段需兼容旧消费者。
- 数据库先做兼容性新增再启用功能；回滚代码不删除已经生成的 cos 资产或新身份。
- 移动开关关闭主要阻止新登录/新上传；已有媒体读取和删除必须保留可处理路径。
- 数据迁移 downgrade 如果会丢新增数据，不承诺“一键回退”；用前向兼容、备份恢复和停发 APK 方案写清边界。
- 正式 appId、keystore 与升级签名保持一致；debug 单独包标识，避免覆盖正式应用。
- 不降低现有 Harness 权限或用户确认要求来打通演示。

## 16. 正式应用参数与可用默认值

| 项目 | 开发阶段 | 正式阶段 |
| --- | --- | --- |
| 显示名 | HAH 测试版 | 用户最终确认 HAH/HealthMate |
| 包名 | 本地唯一 debug 标识，写入依赖记录 | 用户控制的永久标识 |
| Android 最低版本 | 满足冻结的 Capacitor/插件要求 | 结合设备矩阵确认 |
| 登录 | 非生产 dev-login/mock | 开放平台微信；其他登录单独实现 |
| 对象存储 | local 或隔离 COS | 建议 COS，需真实桶/权限/域名 |
| 密钥 | 自动 debug 签名 | 用户保管的正式 keystore |
| 分发 | 本地安装 debug APK | 正式 APK；有商店需求再补 AAB |
| 账号互通 | fixture/合同验证 | 正式版完整目标；缺少则如实列为限制 |

开发默认值可让 Luna 先完成工程和 debug 包；不能用默认正式标识、临时签名或共享测试 Token 冒充 release。资源缺口一次性整理到台账，不因可自行决定的目录、组件或库选择反复中断工作。

## 17. 交付目录与状态台账

```text
dist/android/<version>/
  HAH-<version>-debug.apk
  HAH-<version>-release.apk      # 满足签名/验证条件时才提供
  checksums.sha256
  build-manifest.json
  permissions.md
  installation.md
  release-notes.md
docs/android/verification/<date>/
  test-summary.md
  cloud-connectivity.md
  device-matrix.md
  screenshots/
  recordings/
  known-issues.md
```

`build-manifest.json` 至少记录 commit、是否有未提交代码、versionName/versionCode、包名、debug/release、依赖和工具链版本、公开环境标识、构建时间、APK hash、签名证书摘要。不得包含签名密码、JWT、云凭据或用户健康数据。

工作台账（随开发更新）：

| ID | 工作项 | 实现状态 | 验证状态 | 证据路径 | 缺口/下一步 |
| --- | --- | --- | --- | --- | --- |
| WP1-01 | 可安装 debug APK | 已实现 | 51 项移动端测试、TypeScript/ESLint、Capacitor 同步、Android `assembleDebug` 通过；最新常规配置 debug 包已安装并启动，断网超时提示通过。最新包连接独立 SQLite 临时 API 后 29/29 路由通过且无 JavaScript 异常；原本机 API 的数据库仍需迁移后才能登录。debug 网络配置仅允许 `10.0.2.2` 使用 HTTP，正式主清单保持 HTTPS-only | `mobile/`、`docs/android/verification/2026-10-08/route-smoke/`、`docs/android/verification/2026-10-07/screenshots/android16-latest-debug-2026-10-08.png`、`docs/android/verification/2026-10-07/test-summary.md` | 当前产物为开发包；真机安装与签名 release 仍待验收 |
| WP2-01 | 移动正式登录 | 代码已实现 | 单元/接口测试通过；线上只读 OpenAPI 尚无 Android 登录路由；生产配置预检缺少移动端 AppID/AppSecret | `mobile/src/services/nativeAuth.ts`、`backend/tests/test_mobile_auth.py`、`docs/android/verification/2026-10-07/cloud-readiness.md` | 需在受控云托管环境补齐凭据并部署后，使用开放平台签名包真机联调 |
| WP3-01 | Agent 计划确认闭环 | 进行中 | Android 模拟器连接本机 FastAPI + 隔离 SQLite 完成合成草案读取、确认前零写入、显式确认后 apply、当前状态重读、本周计划读取和任务完成；导航恢复小程序的五个主入口与八个快捷操作，设置页提供账号关联和退出入口。草案由测试 fixture 预置，不代表 Agent/模型生成验收 | `mobile/src/App.vue`、`mobile/src/pages/SettingsPage.vue`、`mobile/tests/components/AppNavigation.test.mjs`、`mobile/tests/components/SettingsPage.test.mjs`、`mobile/src/pages/ChatPage.vue`、`mobile/src/pages/PlanPage.vue`、`mobile/tests/components/PlanPage.test.mjs`、`backend/tests/test_action_protocol_unification.py`、`docs/android/verification/2026-10-07/screenshots/android16-dashboard-menu.png`、`docs/android/verification/2026-10-07/screenshots/android16-dashboard-quick-actions.png`、`docs/android/verification/2026-10-07/screenshots/android16-plan-preview-seeded.png`、`docs/android/verification/2026-10-07/screenshots/android16-plan-confirm-modal.png`、`docs/android/verification/2026-10-07/screenshots/android16-plan-applied.png`、`docs/android/verification/2026-10-07/screenshots/android16-plan-current-item-done.png` | 用户确认和计划读写闭环已验证；真实 Agent 生成及生产云托管/能力授权仍待联调 |
| WP4-01 | COS 图片/视频上传 | 代码已实现 | COS/S3 会话、文件校验和删除测试通过；本机 Android 媒体链路通过；线上 OpenAPI 尚无 Android 会话接口 | `backend/app/api/v1/media.py`、`mobile/src/services/cloudMedia.ts`、`docs/android/verification/2026-10-07/cloud-readiness.md` | 需要补齐云托管配置、部署接口，使用真实 COS 桶、CAM 权限和 Android origin CORS 验收 |
| WP4-02 | 每日健康打卡 | 已实现 | Android 模拟器使用本机 FastAPI + 隔离 SQLite 完成 GET/PUT/GET；保存后重读一致，返回首页后今日状态与连续记录更新 | `mobile/src/pages/CheckInPage.vue`、`docs/android/verification/2026-10-07/screenshots/android16-checkin-saved.png`、`docs/android/verification/2026-10-07/screenshots/android16-home-checkin-refreshed.png` | 生产云托管、真实账号和真实设备尚待验收 |
| WP5-01 | 旧 CloudBase 跨端媒体 | 代码已实现 | CloudBase 票据和媒体目录接口测试通过；真实云端未验证 | `backend/app/api/v1/auth.py`、`mobile/src/services/cloudMedia.ts` | 保留小程序历史文件读取/删除路径，待账号联调 |
| WP6-01 | 双用户与删除验收 | 进行中 | Android 隐私导出已通过 ZIP 校验、分享和缓存清理；随后在合成测试账号完成精确删除短语与双重系统确认，API 返回成功并退出登录。新增独立用户隔离集成测试：两套 JWT 的打卡分离，删除用户甲后其账号/数据消失、令牌失效，用户乙及其记录仍可读取。本机 Android 删除用例没有云媒体；CloudBase 删除重试和账户删除另有后端回归测试 | `mobile/src/pages/PrivacyPage.vue`、`backend/tests/test_user_isolation.py`、`docs/android/verification/2026-10-07/screenshots/android16-privacy-export-share.png`、`docs/android/verification/2026-10-07/screenshots/android16-privacy-delete-confirm.png`、`docs/android/verification/2026-10-07/screenshots/android16-privacy-deleted.png`、`backend/tests/test_mobile_media_upload.py`、`backend/tests/test_reliability.py` | 两个真实微信账号/真机隔离、生产 CloudBase/COS 对象删除回执及真实云端导出/删除尚待验收 |
| WP7-01 | Release 构建门禁 | 已实现 | 使用仅限构建进程的非真实参数构建 production bundle，release 静态检查通过：无开发登录端点、服务端密钥标记或本地/内网开发地址；带凭据的 API URL 被拒绝。随后恢复 debug 构建 | `mobile/scripts/verify-release.mjs`、`docs/android/verification/2026-10-07/test-summary.md` | 实际 production 环境、注册包名、微信 AppID、签名材料和真实 release APK 尚待配置与验收 |

实现状态使用未开始/进行中/已实现；验证状态使用未验证/通过/失败/缺外部资源。每次报告明确本工作包改了什么、实际执行了哪些验证、证据在哪里、哪些仍缺。不要只报告“全部完成”。

## 18. 完整完成定义

以下全部满足才能标记 Android 正式版完成：

- debug 与正式签名 APK 可安装、启动和升级，签名/版本/环境可追溯。
- 微信云托管仍是在线后端，两个客户端复用同一 Harness 与真实数据。
- 28 路由有明确对应实现或经用户接受的不迁移决定；关键页面可达。
- UI、角色、导航、转场与原基线通过截图和真机验收。
- 安全复核后的文本展示、计划确认、识餐确认与授权边界保持一致。
- 正式登录、账号关联、双用户隔离和过期 Token 处理验证通过。
- 图片、视频、录音、STT/TTS、播放、导出和后台恢复有真实设备证据。
- COS 与历史 CloudBase 媒体读取/刷新/删除都有明确且真实的完成状态。
- 重复提交、超时、取消、断线和版本冲突不会伪造成功或重复写入。
- 原小程序回归、后台合同、Android 检查通过；数据库迁移在隔离环境验证。
- release 不含秘密、dev-login、调试远程服务器或明文 HTTP。
- APK、hash、构建清单、安装说明、权限说明、已知限制和回滚说明齐全。

外部条件缺失时交付已经完成的 debug 产物、源码和台账，并明确未过门禁；不把 mock、浏览器截图、历史测试或未执行的真机操作记成真实验证。

## 19. 官方参考与信息有效性

本文件于 2026-10-07 核对以下官方资料，实施时按冻结版本再次确认配置与支持范围：

- [微信云托管公网与服务设置](https://docs.cloudbase.net/run/deploy/service-setting)：APP HTTPS 入口与小程序私有调用共存。
- [Capacitor Android](https://capacitorjs.com/docs/android)：Android 工程、环境与支持范围。
- [Capacitor Preferences](https://capacitorjs.com/docs/apis/preferences)：轻量存储及 Android SharedPreferences 语义。
- [腾讯 COS 预签名上传](https://cloud.tencent.com/document/product/436/14114)：对象键、有效期和简单上传边界。
- [腾讯 COS 临时授权](https://cloud.tencent.com/document/product/436/14048)：受控授权与权限范围。

移动微信登录的审核、签名、SDK 和 UnionID 条件需 Luna 在实施时查阅微信开放平台官方资料并记录链接与配置证据；本次没有验证任何真实开放平台应用配置。

## 20. 可直接交给 Luna 的执行提示

> 请以 `docs/LUNA_ANDROID_WECHAT_CLOUD_DEVELOPMENT_SPEC_2026-10-07.md` 为执行规格，为 HAH 新增 Vue 3 + TypeScript + Vite + Capacitor Android 客户端。后端继续使用微信云托管，保留现有小程序、MySQL、Harness、用户确认和 Worker 协议。先检查当前源码及工作区、完成 WP0 基线，再推进平台层、请求认证和 Agent 计划确认闭环，随后完成 COS 多媒体、语音、全部页面、正式身份和隐私链路。已有能力与拟新增接口必须区分。按源码纠正 28 路由、先处理后发送的 Agent 流、base64 STT/TTS、共享 dev-user 和 CloudBase 跨端媒体限制。开发默认值可用于隔离 debug 构建；真实云端、微信登录、正式签名及真机验收缺资源时继续完成独立工作并记录缺口。每个阶段更新台账，交付实际代码、验证证据和 APK；不要用占位页面、mock 或历史测试冒充完成，也不要在未获相应授权时部署生产或执行生产迁移。
