# 当前已知缺口（2026-10-07）

2026-10-08 复查：最新 debug 包已重建、打包并通过签名与包名校验；Android 16 模拟器已重新启动并安装最新包，启动与断网超时提示通过。移动微信 AppID/AppSecret、CloudBase 存储管理凭据、COS 凭据和 release 签名环境均不在开发机环境变量、`backend/.env` 或 `mobile/.env.local` 中（只核对配置项是否存在，未读取值）。Docker Desktop 服务仍为 Stopped。以下真实云端与真机缺口仍然有效。

- 微信登录代码已经接入，用户确认开放平台资料已准备。真实 AppID、服务端凭据、注册包名和签名尚未配置并实测。Release 回调 Activity 已按变体 applicationId 生成，并用隔离占位包名成功编译；还需用正式注册的 applicationId 和签名验收。
- 真实微信云托管、测试数据库、CloudBase 自定义登录、COS 桶、CAM、CORS 和线上 Worker 没有连接。COS 上传当前使用 S3 兼容适配器，协议兼容性必须在实际腾讯 COS 桶验证。
- Android 已接入 CloudBase 历史媒体身份与读取代码路径；服务端现通过官方管理端 API 删除旧文件，按平台回执核验，并对失败文件进行加密引用和退避重试。真实云端权限、CAM 最小授权和文件结果仍待验收。
- Android 16 模拟器已验证安装、开发登录、基础 API/NDJSON、导航、401 失效恢复、系统照片选择、模拟相机拍摄/预览、本地开发上传、饮食任务创建/查询、原生录音开始/停止，以及视频系统相机录制、文档选择、原生 URI 流式上传和会话 complete。视频 PUT 只发往本机临时接收端；隔离后端没有启用识别 Worker，页面显示任务失败，不能据此认定 COS 或动作识别已通过。模拟相机使用系统虚拟场景，不代表实体摄像头；测试端没有腾讯云语音凭据，因此营养保存闭环和语音识别未验收（ASR 请求返回 503）。后台任务轮询暂停/恢复已有单元测试，Android 设备实际切后台恢复仍未验收。录音焦点丢失清理和朗读被暂停处理已实现并构建，真实通话/媒体打断未验收。实体传感器、TTS、真机媒体、弱网、布局/动画和真实账号关联仍待验证。
- 根级 Gradle 聚合 `connectedDebugAndroidTest` 的 Cordova/Kotlin 重复类问题已在 `mobile/android/build.gradle` 统一旧 JDK7/JDK8 兼容工件版本；修复后根级聚合在 Android 16 模拟器通过。App 模块 5 项仪器测试均通过。
- 页面路由与 Vue 页面组件一一映射并由契约测试覆盖；当前没有基于旧小程序真实截图的逐页像素对比，也没有 Android 主流/低端机帧耗时证据。
- 微信开发者工具已安装，但 IDE 命令行服务端口关闭；收集原小程序视觉基准需要负责人在开发者工具“设置 → 安全设置”中自行开启该端口。Android 自身截图仅用于布局审阅，不替代小程序基准。
- Capacitor 安全区改用原生 inset 处理，最新 APK 模拟器冷启动未复现启动期 CSS 注入错误；实体机布局和刘海/手势导航组合仍需验收。
- 正式签名 keystore、正式 applicationId、生产 API、CloudBase 环境及 CloudBase/COS 服务端 Secret Manager 参数未确认配置。当前只有 `com.hah.healthmate.dev` debug APK，没有 release APK。
- 后端完整回归测试记录见 `test-summary.md`；部分 SQLAlchemy/SDK 弃用告警未在本次 Android 迁移中处理。
