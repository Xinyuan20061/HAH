# 云端联通验收（2026-10-07）

代码已实现 FastAPI/微信云托管服务端路径、Android OAuth、CloudBase 票据，以及 COS/S3 兼容存储的短时预签名上传、隔离区校验、服务端复制提交、播放地址和延迟清理。短期 PUT URL 只写随机隔离键；后端核对对象长度、MIME 和 JPEG/PNG/WebP/MP4/MOV 文件头后，才复制到私有正式键。上传会话与清理任务可恢复，账号删除会把隔离对象纳入同一持久账本。CloudBase 历史文件删除通过官方管理端 API 完成，逐文件回执写入台账，失败项使用加密文件引用自动重试。小程序仍可使用 CloudBase 历史媒体路径。

这里的“本机”指保存本项目的 Windows 开发电脑（`D:\学习资料\计算机应用大赛\health-assistant`），与微信云托管控制台里的容器环境分开。本机 `mobile/.env.local` 用于客户端 API 地址和公开 CloudBase 环境 ID；Windows 构建进程还需 `WECHAT_MOBILE_APP_ID` 才能启用原生微信 SDK。云托管侧另需 `MOBILE_WECHAT_APP_ID`/`MOBILE_WECHAT_APP_SECRET`、CloudBase/COS 服务端配置及受限删除凭据，所有秘密都只放云托管 Secret Manager。设置本机文件或进程环境变量不会自动修改云托管环境；反向也一样。

用户已确认开放平台资料准备就绪。仅检查变量是否存在的本机复查显示：`mobile/.env.local` 配有 API 地址，但没有 `VITE_CLOUDBASE_ENV_ID`；`backend/.env` 有 `CLOUDBASE_ENV_ID`，但没有移动 AppID/AppSecret、CloudBase 存储管理凭据、COS 密钥或 Android 功能开关；当前构建进程也没有 `WECHAT_MOBILE_APP_ID`。本次 debug 构建清单实测 `cloudbase_env_id_configured=false`、`wechat_mobile_app_id_configured=false`，且没有正式包名或签名环境变量。以上只说明本机和本次构建的状态，不代表已读取微信云托管控制台环境；未读取或记录任何配置值。API 36 Android 模拟器已安装并连通本机隔离测试后端，但未访问真实云托管环境。

部署 COS 时，桶名需符合 `BucketName-APPID`，标准地域端点与 `S3_REGION` 必须一致；服务启动预检现在会拦截这两类常见配置错误。桶建议保持私有读写。CORS 来源要按 Android WebView 实际报告的 Origin 配置（含协议和非默认端口），允许 `PUT`、`GET`、`HEAD`，并按预检请求放行实际使用的请求头；COS 自动处理 `OPTIONS` 预检，不把它作为允许方法配置。请参照腾讯云官方的[桶命名规范](https://cloud.tencent.com/document/product/436/13312)、[S3 兼容配置](https://intl.cloud.tencent.com/document/product/436/34688)和[COS 跨域访问配置](https://cloud.tencent.com/document/product/436/13318)。

腾讯云 COS 官方资料确认 S3 兼容 API 可用于第三方 S3 SDK；对象复制使用 `CopyObject`，服务端 CAM 至少需要源对象读取和目标对象写入权限：[S3 兼容配置](https://intl.cloud.tencent.com/document/product/436/34688)、[Python SDK 对象复制](https://cloud.tencent.com/document/product/436/65826)。因此上传签名、隔离键和清理边界已有本地测试覆盖，真实桶的 `CopyObject`、CAM、CORS 和跨网络行为仍需在部署环境验收。
