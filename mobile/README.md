# HAH Android 客户端

客户端使用 Vue 3、TypeScript、Vite 和 Capacitor，复用项目当前微信云托管 FastAPI。开发规格见 [`docs/LUNA_ANDROID_WECHAT_CLOUD_DEVELOPMENT_SPEC_2026-10-07.md`](../docs/LUNA_ANDROID_WECHAT_CLOUD_DEVELOPMENT_SPEC_2026-10-07.md)；实施台账见 [`docs/android/development-status.md`](../docs/android/development-status.md)。

## 当前进度

小程序的 28 条业务路由及 Android 账号关联页均已迁移，包含健康记录、饮食与运动分析、计划、策略、语音、AI 配置、评测、媒体和隐私管理。原生微信授权、Keystore 会话保护、CloudBase 历史媒体及 COS/S3 预签名上传也已接入。Android 页面和后端源码已完成本地构建、测试及模拟器路由检查；当前线上云托管仍运行旧 API，移动登录、账号关联与 Android 媒体会话尚未部署。只读核对线上合同前，在 PowerShell 设置 `$env:HEALTHMATE_CLOUD_ORIGIN='https://<云托管域名>'`，再运行 `npm run verify:cloud-contract`；该检查只读取公开 OpenAPI，不会登录或写入数据。详见 [`docs/android/development-status.md`](../docs/android/development-status.md) 和 [`docs/android/verification/2026-10-07/cloud-readiness.md`](../docs/android/verification/2026-10-07/cloud-readiness.md)。

原生微信登录使用独立的微信开放平台 Android AppID。构建时通过环境变量 `WECHAT_MOBILE_APP_ID` 提供公开 AppID；AppSecret 只配置在后端环境变量 `MOBILE_WECHAT_APP_SECRET`，绝不写入 APK。正式应用还必须配置已在开放平台登记的应用包名和签名证书。当前开发包名为 `com.hah.healthmate.dev`，不能直接当作正式注册信息。

正式构建先复制 `.env.production.example` 为本机忽略文件 `.env.production`，填写真实 HTTPS API 地址和 CloudBase 环境 ID。构建进程还要提供 `WECHAT_MOBILE_APP_ID`、`ANDROID_RELEASE_APPLICATION_ID`，以及 `ANDROID_RELEASE_STORE_FILE`、`ANDROID_RELEASE_STORE_PASSWORD`、`ANDROID_RELEASE_KEY_ALIAS`、`ANDROID_RELEASE_KEY_PASSWORD`。微信 AppSecret、CloudBase 删除凭据及 COS 密钥只放在微信云托管的服务端环境/密钥管理中，不能放进 APK、前端环境文件或聊天。执行 `npm run android:release` 会校验正式包名、微信 AppID、生产环境和签名材料；正式包显示名称与应用 ID 会随 release 配置生成。不要把签名密码写进仓库或聊天。

开发登录会使用后端单一 `dev-user`，**只适用于隔离开发数据**。开发 JWT 只留在应用内存中，退出或重载后须重新登录，不会测试多账号隔离。Android 正式登录适配器和服务端身份合同已实现；用户已确认开放平台资料准备就绪，但资料尚未配置到本机/云托管环境，包名/签名匹配和微信真机授权也尚未验证。不能通过公开环境头伪造登录。

## 准备环境

- Node.js 20.19 或更新的兼容版本（当前工程以 Node 24 为基线）。
- npm 与安装后生成并提交的 `package-lock.json`。
- 后续 Android 包装构建还需要与冻结 Gradle/Android Gradle Plugin 兼容的 JDK、Android SDK 与 Build Tools。执行前先根据实际工具链确认 JDK 版本，不能假设 Java 24 可用。

## 本地运行

```powershell
Copy-Item .env.example .env.local
npm ci
npm run dev
```

默认 Android 模拟器地址是 `http://10.0.2.2:8000/api/v1`。实体设备编辑 `.env.local`，将主机换为手机可访问的电脑局域网 IP。后端按现有开发方式启动，使用隔离本地数据库。不要把 `127.0.0.1` 填为手机正在访问的电脑地址。

Vite 浏览器预览可以使用 `http://127.0.0.1:5173`，但浏览器预览不能证明 Android 原生权限、Activity、录音或 APK 安装通过。

## 构建

```powershell
npm run build:debug
npm run cap:sync:local
cd android
.\gradlew.bat assembleDebug
```

当 Android 工程尚未生成时，先使用兼容的 Android SDK/JDK 环境执行 `npx cap add android`。推荐的本地 debug 命令是 `npm run android:debug`：它以 Vite development mode 读取 `.env.local`，并只在 Capacitor debug profile 中将 WebView scheme 切到 HTTP。debug cleartext HTTP 若为本机调试需要，只能放在 Android `src/debug/AndroidManifest.xml`；release manifest 必须禁止明文 HTTP。当前 `.env.example` 的测试 API 是 HTTP，不能将这种配置编入 release。

APK 路径通常是 `android/app/build/outputs/apk/debug/app-debug.apk`。调试包的 appId 是 `com.hah.healthmate.dev`，以免覆盖正式应用。正式包的 appId、签名、OAuth 应用标识需单独决策和配置。

## 前后端约定

- `VITE_API_BASE_URL` 必须包含一次 `/api/v1`；`/health/live` 位于其父目录。
- 客户端只在非 production 且 `VITE_AUTH_MODE=dev` 时显示开发登录。
- JWT 不持久化在 localStorage 或 Capacitor Preferences；正式 Android 会话用 AES-GCM 加密后保存在 Keystore 支持的本机存储，开发登录仍仅存于内存。
- Agent 流返回的是安全复核后的答案及其展示事件。界面不会将其描述为模型实时 token。
- 计划草案由用户显式确认后调用现有 `/agent/runs/{run_id}/apply-plan`。
- `generated-assets/` 由 `npm run assets:sync` 从 `miniprogram/assets/` 同步，清单含 SHA-256。
