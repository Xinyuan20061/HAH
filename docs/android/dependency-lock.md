# Android 工程依赖与工具链锁定

记录日期：2026-10-07。前端精确依赖以 `mobile/package-lock.json` 为准；原生依赖以 Gradle Wrapper、AGP 和 SDK 安装目录为准。

| 层 | 锁定版本 | 当前证据 |
| --- | --- | --- |
| Node.js / npm | 24.15.0 / 12.0.2 | 本机命令版本 |
| Vue / Vue Router / Pinia | 3.5.43 / 5.3.1 / 4.0.3 | `npm ls --depth=0` |
| TypeScript / Vite / vue-tsc | 5.9.3 / 8.3.3 / 3.3.12 | `mobile/package-lock.json` |
| Capacitor CLI / Core / Android | 8.5.2 | 已同步并构建 debug APK |
| Capacitor Camera / App / Preferences | 8.2.5 / 8.1.2 / 8.0.1 | 依赖锁定；相机和生命周期仍需真机验收 |
| Capacitor Filesystem / Share / SplashScreen | 8.1.4 / 8.0.3 / 8.0.2 | 已接入数据导出与应用启动 |
| CloudBase Web SDK | 3.10.1 | 已接入自定义身份票据；真实环境待验证 |
| 微信开放平台 Android SDK | 6.8.40 | Maven 依赖已编译；真实微信回调待验证 |
| Android Keystore 插件 | AES/GCM/NoPadding | 自定义插件已编译；真机生命周期待验证 |
| Gradle Wrapper / AGP | 8.14.3 / 8.13.0 | `assembleDebug` 已通过 |
| Java | Android Gradle 构建使用 JDK 21.0.12；系统默认 Java 24.0.2 | `mobile/scripts/java-toolchain.mjs` 自动发现 JDK 21；Gradle 插件编译验证通过 |
| Android SDK / Build Tools | Platform 36；35.0.0、36.0.0 | 本机 SDK 目录已检查 |
| Android min SDK | 24 | `mobile/android/variables.gradle` |
| 单元/组件/契约测试 | Vitest 5.0.3、Vue Test Utils 2.5.1、happy-dom 20.14.5 | 依赖已锁定，测试脚本已执行 |
| 静态检查 | ESLint 10.12.0、eslint-plugin-vue 10.11.1、typescript-eslint 8.71.1 | `npm run lint` 已通过 |

`android/local.properties` 与 `.env.local` 被忽略，不随源码提交。当前 Android SDK API 36 x86_64 模拟器已连接（`emulator-5554`），根级 `connectedDebugAndroidTest` 聚合已通过；真实微信回调、实体媒体设备和真机视觉仍待验收。
