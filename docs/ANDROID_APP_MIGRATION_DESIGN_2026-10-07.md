# HAH Android 应用迁移设计

> 版本：1.0  
> 日期：2026-10-07  
> 状态：设计基线，尚未开始 Android 实现  
> 目标：在保留微信小程序的同时新增可安装的 Android APK，并尽最大可能保持原有视觉、交互、动画和 Agent 行为不变。

## 1. 结论与设计决策

HAH 不应被改造成另一套独立产品，也不应为了 APK 重写 Harness Kernel。推荐方案是：

1. 保留现有 `miniprogram/`，微信小程序继续发布和维护；
2. 保留现有 `backend/`、`ai-worker/`、数据库、Harness Kernel、Router/Worker/Decision Agent 和工具注册；
3. 新增 `mobile/`，使用 **Vue 3 + TypeScript + Vite + Capacitor** 构建 Android 客户端；
4. Android 客户端通过标准 HTTPS 接入现有 FastAPI，不再依赖 `wx.cloud.callContainer`；
5. 本地开发时，APK 可通过局域网连接电脑上的 FastAPI、SQLite、`uploads/` 和本地 AI Worker；
6. 正式发布时，APK 继续连接云端 FastAPI/MySQL，媒体改用受控的 COS/S3 上传通道；
7. 迁移阶段只做等价复刻，不顺便重设计页面。任何视觉升级必须在 APK 达到等价验收后单独立项。

选择 Capacitor 而不是 React Native 的主要原因是：当前微信小程序本身使用 WebView 渲染，WXSS 的布局、CSS 动画、Canvas 和精灵图在 Android WebView 中更容易按原样复刻。React Native 会引入另一套布局和动画语义，视觉漂移和重写量都会更大。

## 2. 目标与非目标

### 2.1 目标

- 输出可直接安装的 Android 测试 APK；
- 最终输出带正式签名的发布 APK；
- 微信小程序与 Android 共用同一用户数据、健康记录和 Agent 能力；
- 保持健身房、养生馆、小管家、仪表盘和悬浮助手的产品关系不变；
- 保持小健、小康的场景、精灵动画、角色状态和路由行为不变；
- 保持流式回答、计划审阅、用户确认后写入等安全边界不变；
- 保持当前 UI 色板、圆角、间距、按钮尺寸和交互动效；
- 支持拍照、相册、录音、音频播放、文件上传和下载；
- 支持本地开发、云端测试和正式发布三种配置；
- Android 改造不得破坏现有微信小程序测试和上线方式。

### 2.2 非目标

- 第一版不把 FastAPI、MySQL、Worker 和大模型全部塞进手机；
- 第一版不做完全离线的 Harness；
- 不在迁移过程中重做 UI、重新配色或更换角色形象；
- 不把 `wx.*` API 兼容层伪装成可长期维护的全量微信运行时；
- 不把开发用 `dev-login` 带入正式 APK；
- 不把 API Key、签名密钥、腾讯云密钥或模型密钥写入 APK；
- 不使用一个只负责跳转微信小程序的“空壳 APK”冒充独立应用。

## 3. 当前项目基线

### 3.1 现有组成

| 模块 | 当前实现 | Android 策略 |
| --- | --- | --- |
| 微信客户端 | 原生 WXML/WXSS/JS，小程序 WebView | 保留，不改为 Android 专属代码 |
| 后端 | FastAPI | 原样复用，增加移动端适配端点 |
| Harness Kernel | Router → Domain Agents → Decision Agent | 原样复用 |
| 工具系统 | 记录、计划、营养、运动、恢复等受控工具 | 原样复用 |
| 数据库 | 开发 SQLite，生产 MySQL | 原样复用 |
| 媒体 | 开发本地存储，生产 CloudBase 引用 | Android 增加 COS/S3 通道 |
| Worker | 本地 AI Worker 轮询云端任务 | 原样复用 |
| 文字模型 | 云端模型 → 本地模型 → 规则回退 | 原样复用 |
| 语音 | 后端统一语音网关 | Android 替换录音和播放前端实现 |

当前 `miniprogram/app.json` 注册 29 条页面路由；前端包含 33 个 WXML 文件、34 个 WXSS 文件和约 8,000 行非测试 JavaScript。迁移工作量主要在展示层和微信 API 适配层，不在 Harness 与健康业务层。

### 3.2 微信专属依赖

必须被隔离的微信能力包括：

- `wx.login`；
- `wx.cloud.callContainer`；
- `wx.cloud.uploadFile/getTempFileURL/deleteFile`；
- `wx.request/wx.uploadFile`；
- `wx.getStorageSync/setStorageSync`；
- `wx.chooseMedia`；
- `wx.getRecorderManager`；
- `wx.createInnerAudioContext`；
- `wx.getFileSystemManager`；
- `wx.navigateTo/redirectTo/switchTab/navigateBack`；
- `wx.showToast/showModal/showActionSheet`；
- 小程序页面生命周期和自定义 TabBar。

这些依赖不能散落到新的页面组件中，必须收口到平台接口。

## 4. 目标架构

```text
                         ┌────────────────────────────┐
                         │ Health Agent Harness       │
                         │ Router / Workers / Decision│
                         │ Tool Registry / Safety     │
                         └──────────────┬─────────────┘
                                        │
                               FastAPI / JWT / NDJSON
                                        │
                 ┌──────────────────────┴──────────────────────┐
                 │                                             │
      ┌──────────▼──────────┐                       ┌──────────▼──────────┐
      │ 微信小程序           │                       │ Android APK          │
      │ miniprogram/        │                       │ mobile/              │
      │ wx.cloud + wx.*     │                       │ HTTPS + Capacitor    │
      └──────────┬──────────┘                       └──────────┬──────────┘
                 │                                             │
        CloudBase 媒体                                  COS/S3 签名上传
                 └──────────────────────┬──────────────────────┘
                                        │
                              MySQL / 媒体资产账本
```

### 4.1 双客户端原则

- 后端返回的业务 JSON 不区分页面技术栈；
- 客户端通过 `X-Client-Platform: wechat|miniprogram|android` 标识来源，仅用于兼容、审计和诊断，不作为身份凭据；
- 所有写操作继续经过 JWT、用户权限、幂等键和 Harness 确认机制；
- 同一个健康记录不能因为来自两个客户端而形成两套表；
- Android 的上线不能要求先停止微信小程序；
- Android 故障可单独回滚，不影响微信小程序和后端数据。

## 5. 建议目录结构

```text
health-assistant/
├─ miniprogram/                 # 原微信小程序，继续作为现有视觉基线
├─ mobile/                      # 新增 Android/Web 客户端
│  ├─ src/
│  │  ├─ app/                   # 启动、路由、全局状态、错误边界
│  │  ├─ pages/                 # 与小程序页面一一对应
│  │  ├─ components/            # 角色、悬浮助手、TabBar、状态组件
│  │  ├─ features/              # 计划、记录、识餐、运动等领域 UI
│  │  ├─ platform/              # Android/Web 平台适配器
│  │  ├─ services/              # 请求、鉴权、流式响应、任务轮询
│  │  ├─ stores/                # 会话、用户、陪伴角色和页面状态
│  │  ├─ styles/                # token、全局样式、动效清单
│  │  └─ generated-assets/      # 从 miniprogram/assets 同步，不手改
│  ├─ android/                  # Capacitor 生成的 Android 工程
│  ├─ tests/                    # 单元、契约、截图、交互测试
│  ├─ scripts/                  # 资源同步、WXSS 单位转换、构建检查
│  └─ package.json
├─ shared/
│  ├─ contracts/                # 与平台无关的请求/响应和状态常量
│  └─ presentation/             # 可安全复用的纯函数
├─ backend/
└─ ai-worker/
```

迁移初期不移动小程序文件。只有某段纯 JavaScript 在两个客户端都通过测试后，才允许抽到 `shared/`；小程序通过薄包装继续引用，避免一次大搬迁造成回归。

## 6. 平台适配层

### 6.1 接口定义

Android 页面不得直接调用 Capacitor 插件。统一使用下列接口：

| 接口 | 责任 | 微信实现 | Android 实现 |
| --- | --- | --- | --- |
| `AuthPort` | 登录、刷新、退出、身份链接 | `wx.login` | 原生微信 OAuth/移动登录 |
| `HttpPort` | JSON、上传、下载、NDJSON | `wx.request` | `fetch`/受控原生 HTTP |
| `StoragePort` | Token、偏好、缓存 | 微信 Storage | Capacitor Preferences |
| `NavigationPort` | push、replace、tab、back | 微信页面栈 | Vue Router |
| `MediaPickerPort` | 拍照、相册、视频 | `wx.chooseMedia` | Camera/Photo Picker |
| `RecorderPort` | 麦克风权限与录音 | RecorderManager | Capacitor 原生插件 |
| `AudioPort` | TTS 文件播放与中断 | InnerAudioContext | HTML Audio/原生播放器 |
| `FilePort` | 临时文件、导出和分享 | FileSystemManager | Capacitor Filesystem/Share |
| `DialogPort` | toast、确认、操作菜单 | `wx.show*` | 应用内同风格组件 |
| `LifecyclePort` | 前后台、恢复、销毁 | Page/App 生命周期 | Capacitor App 生命周期 |
| `ViewportPort` | 安全区、键盘、窗口 | 微信窗口 API | CSS env + Keyboard 插件 |

### 6.2 请求语义必须保持一致

Android 的请求层应复刻 `miniprogram/utils/request.js` 的行为，而不是只写一个裸 `fetch`：

- 自动附加 Bearer Token；
- 401 时只刷新一次登录状态并重放安全请求；
- GET 和已明确幂等的任务创建允许有限重试；
- 保持统一错误对象：`code/message/retryable/requestId/details`；
- 只读请求允许短期缓存；
- 流式回答继续处理 `meta → stage → answer → delta → done`；
- 语义上以经过安全复核的 `answer` 为准，`delta` 只用于逐字展示动画；
- 中止生成时同时取消 UI 状态和底层网络请求；
- 所有超时、弱网和后台恢复行为具有可测试的终态。

## 7. 本地与云端部署

### 7.1 本地开发配置

本地开发继续使用现有能力：

```env
ENV=development
DATABASE_URL=sqlite:///./healthmate.db
STORAGE_BACKEND=local
UPLOAD_DIR=uploads
PUBLIC_BASE_URL=http://<电脑局域网IP>:8000
```

运行关系：

```text
Android 调试 APK
  └─ http://192.168.x.x:8000/api/v1
       ├─ FastAPI
       ├─ SQLite
       ├─ uploads/
       └─ 可选本地 AI Worker
```

约束：

- 手机和电脑必须处于可互访的局域网；
- APK 不能配置 `127.0.0.1` 访问电脑；
- Android 的明文 HTTP 仅在 debug Manifest 中放行；
- release Manifest 禁止明文 HTTP，只允许 HTTPS；
- `/auth/dev-login` 只用于非生产环境，不能进入正式包；
- 本地媒体继续走 `/media/upload`，生产环境不得写容器临时磁盘。

### 7.2 正式发布配置

正式 APK 使用现有云端 FastAPI 公网 HTTPS 地址。建议新增部署档案，而不是复用小程序专属校验：

```env
DEPLOYMENT_PROFILE=mobile_cloud
MOBILE_AUTH_PROVIDER=wechat_open_platform
MOBILE_UPLOAD_BACKEND=s3
```

部署档案建议拆分为：

| 档案 | 登录 | 媒体 | 数据库 |
| --- | --- | --- | --- |
| `local_dev` | dev-login | local | SQLite |
| `wechat_cloud` | 小程序 code | cloud_ref | MySQL |
| `mobile_cloud` | 移动端 OAuth | COS/S3 | MySQL |
| `dual_client_cloud` | 两种登录同时存在 | cloud_ref + COS/S3 | MySQL |

现有生产配置校验强制要求微信配置和 `cloud_ref`，因此双客户端上线前需要把校验改成“按部署档案校验”，不能简单删除安全限制。

## 8. 登录与账号统一

### 8.1 测试 APK

- 非生产后端可使用 `/auth/dev-login`；
- 测试包必须明显标记测试环境；
- 测试账号数据不得与生产数据混用；
- 测试包不得内置共享的生产 JWT。

### 8.2 正式 APK

推荐使用微信开放平台原生登录，同时保留未来增加手机号登录的能力。后端新增统一身份层：

```text
user_identities
├─ user_id
├─ provider            # wechat_miniprogram / wechat_mobile / phone
├─ subject             # provider 返回的稳定用户标识
├─ created_at
└─ unique(provider, subject)
```

兼容策略：

- `/auth/wechat` 继续服务小程序，不改变请求与返回；
- 新增 `/auth/mobile/wechat` 服务 Android OAuth code；
- 已有小程序用户可通过受控的账号关联流程连接 Android 身份；
- 不以昵称、手机号明文或设备 ID 猜测两个账号属于同一人；
- 身份关联和解除必须写入安全审计；
- 数据库变更必须走新的 Alembic 迁移，不修改历史迁移。

## 9. 媒体存储设计

### 9.1 为什么 APK 不能继续直接调用 `wx.cloud.uploadFile`

`wx.cloud` 属于微信小程序运行时，独立 Android WebView 中不存在。正式 APK 不能依赖它，也不能把 CloudBase 管理密钥放到客户端。

### 9.2 双媒体通道

| 客户端 | 上传方式 | 后端保存 |
| --- | --- | --- |
| 微信小程序 | CloudBase 文件 ID 注册 | `storage_backend=cloud_ref` |
| Android | 后端签发 COS/S3 PUT URL | `storage_backend=s3` |
| 本地调试 | `/media/upload` | `storage_backend=local` |

后端按每条媒体资产自身的 `storage_backend` 读取、刷新和删除，不使用一个全局后端假设全部历史文件来自同一处。

Android 上传流程：

1. 客户端选择图片或视频；
2. 客户端提交文件名、MIME、大小和哈希申请上传；
3. 后端校验用户、类型、大小、预算和路径；
4. 后端签发短时 PUT URL 和绑定当前用户的 object key；
5. 客户端直传 COS/S3；
6. 客户端回传上传凭证；
7. 后端 HEAD 校验后登记媒体资产；
8. 识餐或运动任务只引用 `asset_id`；
9. 用户删除时由服务端执行对象删除并记录回执。

不得让客户端自由提交任意外部 URL，也不得把“客户端说上传成功”当成平台删除或所有权证明。

## 10. UI 等价复刻规范

### 10.1 单一视觉基线

在 Android 等价验收完成前，`miniprogram/` 是视觉与交互的唯一基线。迁移规则为：

- 页面结构按当前 WXML 逐块转换；
- 页面样式按当前 WXSS 转换；
- 页面文案按当前版本复制；
- 角色资源从 `miniprogram/assets/` 自动同步；
- 不在 Android 中另造相似图标；
- 不合并看似重复但状态语义不同的组件；
- 不因 Android 屏幕更大而擅自增加信息密度；
- 发现小程序已有缺陷时先登记，不在迁移提交里顺手修复两端。

### 10.2 设计 token

Android 必须使用当前色板：

| 用途 | 固定值 |
| --- | --- |
| 页面底 | `#f7f7f2` |
| 卡片/浮层 | `#ffffff` |
| 强调色 | `#c4e267` |
| 浅强调底 | `#e9efd9` |
| 深色特性卡 | `#111613` |
| 渐变高光 | `#2f3a1e` |
| 主文字 | `#111613` |
| 次要文字 | `#5f665f` |
| 品牌绿文字 | `#506336` |
| 分隔线 | `#eeeee6` |
| 危险/警告 | `#c0392b` / `#9b5b00` |

卡片保持：无可见边框、`22rpx` 圆角、`0 8rpx 26rpx rgba(17,22,19,.035)` 阴影。柠檬绿上只能使用深色文字。

### 10.3 尺寸和单位

- 继续以 750 设计宽度为基线；
- 构建时把 `rpx` 机械转换为视口单位，不手工逐页改数值；
- 页面左右留白固定对应 `32rpx`；
- 卡片间距对应 `24rpx`；
- 卡片内边距对应 `32rpx`；
- 其他间距只能来自 `4/8/12/16/20/24/32/40/48`；
- 内容宽度跟随手机视口，平板模式先以居中手机宽度容器显示，不擅自改成桌面排版；
- 顶部和底部增加 `env(safe-area-inset-*)`，但安全区补偿不能改变组件内部尺寸；
- 键盘弹出时只调整可视区域和输入区，不缩放整个页面。

### 10.4 WXML 到 Vue 映射

| 小程序 | Android/Vue |
| --- | --- |
| `<view>` | `<div>` |
| `<text>` | `<span>` |
| `<image mode="aspectFill">` | `img { object-fit: cover; }` |
| `<image mode="aspectFit">` | `img { object-fit: contain; }` |
| `wx:if` | `v-if` |
| `wx:for + wx:key` | `v-for + :key` |
| `bindtap` | `@click` |
| `bindtouch*` | Pointer Events |
| `scroll-view` | 受控 overflow 容器 |
| `canvas type="2d"` | HTML Canvas 2D |
| `hover-class` | `.is-pressed` + Pointer Events |

转换后必须保留稳定 key，否则列表更新时会出现动画错位。

### 10.5 圆形控件守卫

项目曾多次出现圆形按钮变成椭圆的问题。Android 端所有圆形控件必须满足：

- 显式设置相同的 `width/min-width/max-width`；
- 显式设置相同的 `height/min-height/max-height`；
- `box-sizing:border-box`；
- `border-radius:50%`；
- 在 flex 容器中禁止被压缩；
- 图标和外圆分别设置尺寸；
- 截图测试断言宽高差不超过 1 CSS px。

当前悬浮助手输入区的麦克风与发送按钮外圆均以 `64rpx` 为基线；麦克风图像为 `32rpx`，发送箭头按视觉等大使用 `36rpx` 轮廓。

## 11. 导航与页面转场

### 11.1 导航结构

底部主导航保持三个产品概念：

- 小健时显示“健身房”；
- 小康时显示“养生馆”；
- 中间为仪表盘；
- 右侧为小管家。

仪表盘展开顺序保持：

1. 记录；
2. 计划；
3. 快速开始；
4. 我的；
5. 设置。

陪伴角色选择持久化。离开健身房/养生馆进入其他页面后，底部图标、文字和悬浮助手仍反映当前角色，除非用户主动切换。

### 11.2 防止切页闪白

Android 使用单页应用路由，不进行整页 WebView 重载。必须满足：

1. 原页面仍在屏幕上时先挂载全局转场遮罩；
2. 遮罩首帧背景直接使用 `#f7f7f2`；
3. 中央 Logo/思考状态进入后才开始页面替换；
4. 目标路由代码和关键资源准备完成后再淡出遮罩；
5. 页面组件使用轻微 `opacity + translateY` 入场；
6. 整个过程不出现原生白色 Activity 背景；
7. 首次启动由 Android Splash 与 WebView 根背景无缝衔接；
8. 已访问页面再次打开也必须走相同顺序，不能先闪旧页面再补动画。

目标时序：

```text
0ms      用户点击
0–80ms   点击反馈，遮罩开始覆盖
80–220ms Logo/思考状态稳定显示
220ms+   目标页面就绪即切换
300–420ms 遮罩淡出，页面轻量入场
```

不得为了凑足固定时长强制等待慢页面；最短可感知时长和数据加载是两个独立状态。超过短转场时间后，继续在目标页面显示骨架或明确加载状态。

## 12. 动画等价规范

本节继承 `.agents/skills/healthmate-motion/SKILL.md`，Android 端不得另建一套风格。

### 12.1 时间与缓动

| 类型 | 时长 | 缓动 |
| --- | --- | --- |
| 点击/状态反馈 | 150–250ms | `ease-out` |
| 内容入场 | 300–400ms | `ease-out` |
| 角色姿势切换 | 当前基线约 200–240ms | `cubic-bezier(.2,.8,.2,1)` |
| 骨架/呼吸循环 | 1.2–1.8s | 当前页面定义 |
| 回弹感 | 按当前组件 | `cubic-bezier(.2,.8,.2,1)` |

高频动画优先只改变 `transform` 和 `opacity`。不得使用持续改变布局尺寸的动画模拟角色运动。

### 12.2 点击反馈

所有可点击元素必须在按下立即降低透明度，松手约 80ms 恢复。点击反馈不能使用通用 `scale`，避免覆盖组件已有的定位 transform。禁用态仍保持固定尺寸，不因是否输入文本而改变发送按钮大小。

### 12.3 角色动画

必须原样迁移以下资源和状态：

- `xiaojian-gym-scene-v3.png`；
- `xiaokang-wellness-scene-v2.png`；
- `xiaojian-idle-curl-v3.png`；
- `xiaokang-idle-read-v5.png`；
- 两位角色的四姿势像素图；
- 两位角色的大头照和 TabBar 图标。

角色状态机保持：

```text
idle ↔ listening
  └─ thinking
      ├─ planning → presenting → success
      ├─ speaking
      └─ error
```

关键要求：

- 小健静息时在健身房垫子上做哑铃动作；
- 小康静息时在养生馆沙发上翻书，书页必须能看到翻到右侧；
- `idle` 与 `listening` 共用接近的身体姿势，打开麦克风不能硬切到完全不同的造型；
- 聆听时只出现轻量语音波形，不让人物整体上下晃动；
- 计划、展示、成功和错误动作播放后回到静息状态；
- 角色和场景切换使用交叉淡化，预加载完成前保留旧画面；
- 图片加载失败时使用现有角色图标回退，不显示破图或空白；
- 背景与角色作为两个可独立预加载的层，但最终构图位置必须与小程序一致；
- 精灵帧使用离散 step 切换，不能产生帧间插值模糊。

### 12.4 流式文本

- 每次 `delta` 追加后做短透明度过渡，不让整个消息块反复重排；
- 光标保持当前 blink 节奏；
- 收到完整 `answer` 时不得先清空再重绘；
- 用户切页后悬浮助手继续持有当前会话状态；
- 停止生成立即停止光标与发送中状态；
- Android 系统开启“减少动画”时取消非必要位移，保留状态变化和可访问性提示。

### 12.5 已有特殊交互

以下交互按现有算法迁移，不用相似动画替代：

- workout 标签选择的 padding 扩张和回弹；
- scan/media 详情高度的连续展开与收起；
- goals 滑杆速度采样和惯性补一格；
- records 饮食/运动卡片的弧线滑动删除；
- plan 勾选完成反馈和恢复按钮淡入；
- chat 确认流程的状态推进；
- scan 四步流程与热量区间条过渡；
- 悬浮助手面板展开、收起、输入、语音与计划审阅。

## 13. 重点组件设计

### 13.1 健身房/养生馆

- 页面顶部角色选项卡、角色名、状态胶囊保持当前边距；
- 场景区高度、圆角和角色落点按当前 750 基准复刻；
- 大圆形语音按钮保持当前位置和尺寸；
- 录音过程中外圈可呼吸，但人物本体不抖动；
- Agent 产生计划后，角色进入 planning/presenting，并由路由进入计划页；
- 目标页的悬浮助手携带同一 `run_id/agent_id`，展示计划审阅文本。

### 13.2 小管家

- 保持完整文本聊天能力；
- 保持逐 token 展示；
- 保持来源、确认动作和安全状态；
- 输入胶囊保持约 90% 文本区、右侧圆形发送按钮；
- 空文本和有文本时发送按钮外形不得变化。

### 13.3 常驻悬浮助手

除健身房、养生馆和小管家外，功能页面继续显示悬浮助手：

- 收起态为当前角色圆形头像；
- 展开态包含会话文本、手动输入和语音输入；
- 语音按钮属于输入框内部，不再产生第二套悬浮语音按钮；
- 计划内容作为对话中的文本项，不另造重复预览页；
- 展开/收起不丢失消息、草稿和 `run_id`；
- 页面返回后会话仍在，但涉及个人健康数据的长期缓存必须受隐私设置控制。

### 13.4 仪表盘

- 初始圆形按钮与两侧标签相同大小和风格；
- 打开后上移、放大，再轮滑出现五个入口；
- 常驻导航使用 class 状态过渡，不使用会覆盖 transform 的挂载动画；
- 快速开始继续触发现有动作，不新增空白页面。

## 14. 页面迁移顺序

| 阶段 | 页面/组件 | 原因 |
| --- | --- | --- |
| P0 | App Shell、全局样式、TabBar、转场、状态组件 | 所有页面共享，先冻结视觉基础 |
| P0 | home、agent-character | 最复杂角色场景与语音入口 |
| P0 | chat、agent-float | Agent 核心闭环、流式输出与悬浮会话 |
| P0 | plan、checkin | 计划路由、审阅、确认和任务完成 |
| P0 | records、diet、exercise、trends | 记录主旅程、图表和滑动删除 |
| P1 | scan、media | 相机/相册/上传/任务轮询 |
| P1 | workout、exercise-detail | 运动配置、媒体与结果展示 |
| P1 | profile、profile/edit、settings | 账号、偏好、API 和语音配置入口 |
| P1 | goals、state、insights | 健康状态、目标和主动建议 |
| P2 | report、evaluation | 周报和评测展示 |
| P2 | settings/ai、settings/privacy、settings/capabilities | 高级设置、安全与能力中心 |
| P2 | policy/overview、protocol、episode、review、history | 个人策略完整闭环 |

每迁移一个页面，先对齐静态布局，再对齐状态，再接 API，最后接动画；禁止同时重写结构、数据和动效后只做一次验收。

## 15. 状态与生命周期

### 15.1 页面状态

关键数据页继续支持：

- `loading`：说明当前正在做什么；
- `empty`：区分没有数据和记录不足；
- `error`：显示可恢复原因和重试；
- `success`：说明结果写到了哪里；
- `destructive`：不可逆动作二次确认。

Android 不得因为有原生 Toast 就删除页面内可追踪状态。

### 15.2 前后台恢复

- 进入后台时停止录音、释放音频焦点；
- 正在上传的文件保留可恢复任务指针；
- 回到前台后根据任务 ID 查询真实状态，不能根据旧计时器猜测完成；
- Agent 流断开时读取最终运行结果，避免重复创建计划；
- 页面销毁时清理定时器、媒体对象和 AbortController；
- 陪伴角色选择、语音自动播放偏好和未完成任务指针继续持久化。

## 16. 语音能力

### 16.1 输入

- 使用 Android 运行时麦克风权限；
- 权限被拒绝时保留文字输入，不阻塞页面；
- 按住说话、松开发送的交互不变；
- 录音时长、文件大小、采样格式由后端语音网关合同约束；
- 客户端不持有语音服务密钥；
- 切后台、电话占用或音频焦点丢失时安全结束录音。

### 16.2 输出

- TTS 音频经鉴权下载或短时地址播放；
- 新一次播放开始前停止旧音频；
- 页面切换后是否继续播报服从用户设置；
- 音频播放失败只影响播报，不影响文本回答；
- 小健和小康的声音偏好由后端用户配置决定，不写死在 APK。

## 17. 安全、隐私与健康边界

- APK 中不保存服务端 API Key；
- Token 优先进入 Android 加密存储；
- release 只允许 HTTPS；
- 禁止信任可伪造的客户端平台头；
- 上传 URL 短时、绑定用户、文件类型、大小和对象路径；
- 日志不记录完整健康对话、Token、录音内容和签名 URL；
- 数据导出与永久删除必须覆盖 Android 上传的对象；
- Agent 写入计划、目标、记录前继续要求用户确认；
- 医疗高风险问题继续走现有 SafetyGuardian 和边界提示；
- 客户端不能通过离线状态伪造“已写入”或“已删除”；
- Android 权限说明必须解释相机、麦克风和文件用途。

## 18. 视觉与交互验收

### 18.1 基线生成

在迁移开始前冻结：

1. 当前 Git 提交；
2. 小程序 UI 测试结果；
3. UI 审计结果；
4. 关键页面在统一视口下的截图；
5. 角色各状态的录屏；
6. 页面切换、悬浮窗、流式回答、滑动删除和计划路由录屏；
7. 所有图标和角色资源的 SHA-256。

建议基准设备：Android 1080×2400、系统显示缩放默认；同时增加一台较低性能 Android 设备作为流畅度基线。

### 18.2 自动截图门禁

每个关键状态同时保存“小程序基准图”和“Android 实现图”。允许字体抗锯齿差异，但不允许结构漂移：

- 主体元素边界偏差不超过 2 CSS px；
- 页面主色、卡片色和强调色必须完全一致；
- 圆形按钮宽高差不超过 1 CSS px；
- 关键组件整体像素差异目标不超过 2.5%；
- 文本换行行数必须一致；
- 底部导航、输入区、安全区不得遮挡内容；
- loading、empty、error、success 均要单独截图；
- 视觉差异必须人工确认，不能通过无限放宽阈值消除失败。

### 18.3 动画门禁

- 页面切换无白屏闪烁；
- 角色静息动画帧序、持续时间和落点一致；
- 打开语音时人物不发生整体跳动；
- 小康翻书完整经过翻页到右侧的帧；
- 已访问页面再次进入时转场仍先覆盖再替换；
- 浮层没有首帧闪现；
- 高频动画只使用合成友好的 transform/opacity；
- 主流设备目标 60fps，低性能设备不得出现连续明显卡顿；
- 自动测试只能守合同，最终观感必须真机肉眼验收并留录屏。

### 18.4 行为门禁

- 三位 Agent 的路由结果与小程序一致；
- 小健/小康选择跨页面保持；
- 计划请求进入计划页并保留悬浮审阅对话；
- 用户确认前不写计划；
- 识餐确认前不写饮食记录；
- 流式断线能恢复最终结果；
- 重复点击不会生成重复任务或重复记录；
- 相机、麦克风拒权后仍可继续使用其他功能；
- 退出登录清理 Token、敏感缓存和未授权媒体引用。

## 19. 测试体系

```text
mobile/tests/
├─ unit/             # 纯函数、状态机、rpx 转换、错误规范化
├─ contract/         # 与 FastAPI OpenAPI/fixture 对齐
├─ component/        # 角色、输入框、悬浮助手、图表
├─ visual/           # Playwright 截图与差异报告
├─ e2e/              # Android Emulator/真机主旅程
└─ performance/      # 启动、切页、动画和内存基线
```

持续集成至少执行：

- 小程序现有 Node 测试；
- 小程序 UI 审计；
- Android 客户端 lint、typecheck、unit、component；
- 后端契约测试；
- Android debug APK 构建；
- 关键页面截图对比；
- `git diff --check`；
- release 分支额外执行签名、权限和网络安全检查。

测试通过不等同于真机完成。相机、录音、后台恢复、弱网、权限变化和动效观感必须在真实 Android 设备上验收。

## 20. 实施阶段

### WP0：冻结基线

交付：

- 页面、接口、动画、资源清单；
- 关键截图和录屏；
- 现有测试真实结果；
- 已知缺陷清单；
- Android 包名、名称和最低版本决策。

门禁：没有冻结基线，不开始页面转换。

### WP1：Android 壳与平台层

交付：

- `mobile/` 工程；
- Capacitor Android 工程；
- 路由、状态、设计 token；
- 本地/云端配置；
- Storage、HTTP、Dialog、Lifecycle 适配器；
- 空白但可安装的 debug APK。

门禁：启动无白屏、根背景与小程序一致、能连接本地 `/health`。

### WP2：Agent 核心闭环

交付：

- 健身房/养生馆；
- 小健/小康角色状态机；
- 小管家；
- 悬浮助手；
- 文字流式回答；
- 计划路由和审阅；
- 本地开发登录。

门禁：从健身房提出计划，到计划页审阅、确认、写入完整跑通。

### WP3：记录与多媒体

交付：

- 记录、趋势、计划、打卡；
- 相机、相册、识餐；
- 运动视频与任务轮询；
- 录音、STT、TTS；
- 本地上传和生产签名上传。

门禁：图片、视频、录音三条真机链路在成功、拒权、弱网和取消场景均有终态。

### WP4：全页面迁移

交付其余设置、能力、隐私、状态、洞察、评测和个人策略页面。

门禁：29 条页面路由均有明确的 Android 对应项或书面不迁移决策，不允许静默遗漏。

### WP5：正式登录与双客户端账号

交付：

- 移动端正式登录；
- 账号关联；
- 身份审计；
- 数据隔离与删除覆盖；
- 必要的 Alembic 迁移。

门禁：生产环境不存在共享 dev-user；两个测试账号不可互读数据。

### WP6：签名与交付

交付：

- `app-debug.apk`；
- `app-release.apk`；
- APK SHA-256；
- 版本号、构建号和源码提交；
- 权限清单；
- 安装说明；
- 真机验收记录；
- 已知限制与回滚说明。

## 21. APK 构建与签名

### 21.1 工具链

实现阶段需要安装并冻结：

- Node.js；
- Android Studio；
- Android SDK；
- 对应的 Build Tools；
- Gradle Wrapper；
- JDK；
- 固定版本的 Capacitor、Vite、Vue 和插件。

当前工作机已有 Node 和 Java，但尚未检测到 Android SDK、ADB 和独立 Gradle。WP1 前必须完成工具链安装和版本记录。

### 21.2 包类型

- Debug APK：开发签名，可直接真机安装，不用于商店；
- Release APK：正式密钥签名，用于正式分发；
- 如上架应用商店，同时生成 AAB，但 AAB 不替代用户要求的 APK。

建议输出目录：

```text
dist/android/<version>/
├─ HAH-<version>-debug.apk
├─ HAH-<version>-release.apk
├─ checksums.sha256
├─ build-manifest.json
├─ permissions.md
└─ release-notes.md
```

正式 keystore、密码和商店凭据只保存在用户控制的安全位置，不提交仓库，也不写入设计文档或构建日志。

## 22. 性能预算

| 项目 | 目标 |
| --- | --- |
| 冷启动到稳定首屏 | 在基准机上记录并持续监控，不以 Splash 掩盖无限加载 |
| 页面切换 | 遮罩首帧立即覆盖，无白屏 |
| 高频动画 | 优先保持 60fps |
| 角色资源 | 进入页面前预加载，失败有回退 |
| 首包 | 页面按路由拆包，角色核心资源优先 |
| 长列表 | 需要时虚拟化，不因迁移改变卡片样式 |
| 图片 | 使用适配尺寸和压缩，不把原图全部常驻内存 |
| 音频 | 播放完成及时释放，避免多实例叠加 |
| 后台 | 停止非必要动画、录音和轮询 |

性能优化不得通过删除现有动效、降低角色帧数或更换低质量资源来“达标”。如低端设备无法承载，应先优化预加载、合成层、图片尺寸和无效重绘。

## 23. 风险与处理

| 风险 | 影响 | 处理 |
| --- | --- | --- |
| 字体渲染差异 | 换行和高度漂移 | 固定字体栈、行高和截图容差，逐页校准 |
| rpx 转换误差 | 间距和圆形失真 | 机械转换、750 基准、几何测试 |
| Android 键盘 | 输入区被遮挡 | 可视视口 + Keyboard 事件 + 安全区 |
| WebView 版本差异 | 流式或动画行为不同 | 固定最低 Android 版本、真机矩阵、完整响应回退 |
| 微信登录不可用 | 无法进入业务 | debug 登录与正式移动 OAuth 分离 |
| CloudBase API 缺失 | 上传失败 | COS/S3 签名上传，不在 APK 放密钥 |
| 双客户端媒体删除 | 隐私删除不完整 | 按资产后端分派删除并记录回执 |
| 页面迁移时顺手改版 | 无法判断回归 | 等价迁移与新设计分支严格分开 |
| 动画自动测试不足 | 测试绿但观感差 | 真机录屏与人工验收作为发布门禁 |
| API 合同漂移 | 两端表现不同 | 共享 schema、fixture 和契约测试 |
| release 密钥泄漏 | 无法可信发布 | 密钥不入库，构建环境注入 |

## 24. 回滚策略

- `miniprogram/` 保持独立，Android 回滚不影响微信发布；
- 新移动端接口只做兼容性新增，不破坏已有返回字段；
- 新媒体后端按资产行分派，旧 CloudBase 文件保持可读；
- 正式登录启用前使用功能开关；
- 数据库迁移必须支持明确 downgrade 或前向兼容回滚；
- 发布 APK 保留上一稳定版本及其 SHA-256；
- 若移动端出现严重问题，停止分发 APK 即可，不关闭现有小程序和 Harness。

## 25. 需要在实施前确认的产品项

以下项目不影响本设计成立，但会影响正式包：

1. 应用显示名使用“HAH”还是“HealthMate”；
2. Android 包名；
3. 最低 Android 版本；
4. 正式登录使用微信开放平台还是手机号；
5. 正式媒体使用腾讯 COS 还是其他 S3 兼容服务；
6. 正式签名密钥由谁保管；
7. 是否计划进入应用商店；
8. 微信小程序账号与 Android 账号是否必须在第一版互通。

未确认前可使用不影响架构的开发默认值构建 debug APK，但不得以开发默认值生成正式发布包。

## 26. 完成定义

只有同时满足下列条件，才可以宣称“Android 版已完成”：

- Android APK 可安装、启动和升级；
- 核心页面与小程序通过截图和真机视觉验收；
- 角色、页面转场、流式回答和交互动画通过录屏验收；
- Agent 路由、计划确认、记录写入和安全边界与小程序一致；
- 拍照、视频、语音、上传、下载和后台恢复通过真机测试；
- debug 与 release 配置隔离；
- release 只使用 HTTPS，密钥不入包；
- 双账号隔离、数据导出和永久删除覆盖 Android 数据；
- 小程序原有测试继续通过；
- 后端和 Android 新增测试通过；
- 输出 APK、SHA-256、构建清单、安装说明和已知限制；
- 没有把未执行的真机、云端或生产验证写成已完成事实。

## 27. 参考

- `miniprogram/PRODUCT_UI.md`：当前产品 UI 原则；
- `.agents/skills/healthmate-motion/SKILL.md`：当前动画、色板和交互规范；
- `docs/ARCHITECTURE.md`：当前 Harness 与部署架构；
- `docs/HEALTH_AGENT_HARNESS.md`：Router/Workers/Decision 与工具边界；
- `backend/.env.example`：本地和云端配置基线；
- `docs/WECHAT_CLOUD_RUN_DEPLOY.md`：微信云部署与媒体约束；
- Capacitor 官方文档：<https://capacitorjs.com/docs>；
- Capacitor Android 文档：<https://capacitorjs.com/docs/android>。

