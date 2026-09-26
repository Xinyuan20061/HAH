---
name: healthmate-motion
description: HealthMate 微信小程序（miniprogram/）的前端交互动画规范与工作流。在本项目做任何动画、动效、过渡、@keyframes、transition、骨架屏、进度条动画、手势跟随、淡入滑入、缓动调整时使用。包含渲染器闸门（当前 WebView，禁用 worklet/GSAP）、色板与尺寸 token、现有动画清单、改动流程与验收命令、以及待办缺口。触发关键词：动画、动效、transition、@keyframes、交互动画、手势、缓动、easing、进度条、骨架屏、shimmer、淡入、滑入、抖动、animation、worklet、Skyline。
---

# HealthMate 小程序交互动画

## 0. 渲染器闸门（每次动手前先确认）

本项目跑在 **WebView 渲染器**上，`miniprogram/tests/motionConstraints.test.js` 会自动校验这一点。当前状态：

| 开关 | 位置 | 现值 |
|---|---|---|
| `skylineRenderEnable` | `miniprogram/project.private.config.json` | `false` |
| `compileWorklet` | `miniprogram/project.config.json` | `false` |
| `renderer` | `miniprogram/app.json` | 未声明（= WebView） |

**因此在本项目里：**

- ❌ 不要写 worklet / `SharedValue` / `runOnUI` / `applyAnimatedStyle`，不要用 `wx.worklet`。
- ❌ 不要引入 GSAP、anime.js 之类 Web 动画库，不要用 `document` / `window` / `requestAnimationFrame` / `getBoundingClientRect`。
- ✅ 用 WXSS `@keyframes` + `transition` + class 切换。这是本项目 100% 的动画实现方式。
- ✅ 需要命令式动画时用 `wx.createAnimation`。
- ✅ 需要逐帧自定义时用 Canvas 2D node 的 `requestAnimationFrame`（见 `miniprogram/utils/canvasChart.js` 的做法）。

> 逻辑层与渲染层是双线程且逻辑层没有 DOM，所以任何"选元素 → 改内联样式"的方案都不可行。这也是 GSAP 在小程序里跑不起来的根因。

**已安装但不该现在使用**：`.agents/skills/skyline-*`（微信官方 7 个技能，commit `050bb071`）。它们是**迁移 Skyline 之后**才生效的知识，包含 worklet 动画、共享元素动画、自定义路由转场等。只有当我明确要求迁移 Skyline、或上述三个开关已经打开时，才加载 `skyline-worklet` / `skyline-config`。

**唯一现在就有用的**：`skyline-wxss`（尤其是 `references/animation.md`，官方列出哪些属性支持/不支持 transition 与 animation）。注意它是以 Skyline 为口径编写的，WebView 下不完全等价，所以在本项目里当参考而非绝对真理。

## 1. 设计 token（不要新造）

来源：PACE 参考稿取色 + 2026-09 全站换肤（白底 + 柠檬绿）。**色值只有下面这些，其余一律算错。**

| 用途 | 值 |
|---|---|
| 页面底 | `#f7f7f2` |
| 卡片 / 浮层 | `#ffffff`（无边框，圆角 22rpx，`0 8rpx 26rpx rgba(17,22,19,.035)`） |
| **强调色（柠檬绿）** | `#c4e267` —— **只作填充**，压在其上的文字必须是 `#111613` |
| 强调浅底 / 选中态 | `#e9efd9` |
| 深色特性卡 | `#111613`；渐变高光位用 `#2f3a1e`，**不可用浅色**（否则卡上浅色文字掉到 4:1 以下） |
| 主文字 | `#111613` |
| 次要文字 | `#5f665f`（对白底 5.9:1） |
| 链接 / 品牌绿文字 | `#506336`（对白底 6.6:1） |
| 分隔线 / 边框 | `#eeeee6` |
| 危险 | `#c0392b`；警告 `#9b5b00`（**语义色，换肤时保持不动**） |

**两条硬规则：**

1. **柠檬绿是浅色**。`#c4e267` 上永远配 `#111613` 文字，绝不用白字/浅色字。
2. **深色特性卡保持近黑**。带大量浅色文字的卡（`.report-hero` / `.context` / `.adaptive` / `.insight-hero` / `.experiment-live` / `.observed-card` / `.scanner` / `.tips` / `.boundary` / `.agent-overview` / `.camera` / `.hero`）应映射到 `#111613`，**卡内浅色文字一律原样保留**，不要逐条改。判断依据：这块自己声明了浅色 `color` 且底色是深绿 —— 那是"有浅色文字的容器"，不是按钮。

状态组件已有现成的，优先复用而不是重画：`.state-card` / `.state-card-error` / `.state-card-success` / `.state-title` / `.state-desc` / `.state-spinner` / `.skeleton-line`。

**尺寸**：页面用 `rpx`（750 基准）；正文 `32rpx`，次要 `22–24rpx`，标题 `46rpx`。

**间距刻度（唯一标准，禁止自创数值）**：`4 / 8 / 12 / 16 / 20 / 24 / 32 / 40 / 48`（rpx，4 的倍数）

| 用途 | 取值 |
|---|---|
| **页面左右留白（全站基准 32rpx）** | `app.wxss` 的 `.page`、`insights-page`、`chat .messages`、`chat .composer`、`home .hero-content`、`home .nav-sheet` 都必须是 `32rpx`，否则同一屏里的内容左右边缘对不齐 |
| **卡片与卡片之间** | **`24rpx`** —— 全站基准，等于 `app.wxss` 里 `.card` 的 `margin-bottom` |
| 卡片内边距 | `32rpx` |
| **卡片内的列表行** | 上下内边距 `24rpx`（行高约 `106rpx`）、左右内边距 `24rpx`；必须给足，否则内容会贴住卡片边框、行与行挤在一起 |
| 网格/并排元素之间 | `16–20rpx` |
| 图标 ↔ 文字 | `12–16rpx` |
| 标签 ↔ 数值 | `8rpx` |
| 区块分隔（section 标题上方） | `32–40rpx` |

规则：

- **`>48rpx` 的值不算组件间距**，是布局尺寸（如 `height: 90vh`、`padding-bottom: 380rpx`、`margin-top: 110rpx`），不受刻度约束。
- **负数与 `0` 放行**：负数多用于居中偏移（如 `margin-left: -10rpx` 让圆点对准刻度）。
- 组件内部元素之间的微调也从刻度取值，不要再写 `5/7/9/13/17rpx` 这类随手数。
- **卡片放进网格容器时，间距要由容器负责**：`.card` 自带 `margin-bottom: 24rpx`，网格里常用 `.small{margin:0}` / `.sum{margin:0}` 抹掉它；此时**容器必须自己补 `margin-bottom: 24rpx`**，否则整块网格会和下一个元素贴死。（`records` 的 `.two`、`trends` 的 `.summary`、`report` 的 `.metrics` 都踩过这个坑。）
- `node scripts/audit_miniprogram_ui.mjs` 会检查四件事：`off-scale-spacing`（脱离刻度）、`card-gap-drift`（`.card` 基准被改动）、`cramped-row`（带分隔线的行缺上下内边距）、`card-gap-suppressed`（卡片间距被抹平且容器没补）。后两条都**跨文件/跨 WXML 合并判断** —— 分隔线常写在 `app.wxss`、padding 写在页面 wxss；`margin:0` 写在 CSS、`card` 类写在 WXML，单看一处永远发现不了。

**弃用的荧光绿**：`#d8ff84` `#d9ff84` `#d7ff84` `#d8ff83` `#b7e960` `#8fc45d` 属于 HealthMate 3.0 之前的高光版本（`chat`/`goals`/`report`/`exercise-detail`/`workout` 等页仍在用）。改到这些页面时不要扩散它们，新动效一律用上表色板。

## 2. 现有动画清单（先查再写，不要重复造）

| `@keyframes` | 定义位置 | 用途 |
|---|---|---|
| `stateSpin` | `app.wxss` | `.state-spinner` 状态加载圈 |
| `skeletonMove` | `app.wxss` | `.skeleton-line` 骨架屏流动 |
| `shine` | `pages/home/index.wxss` | 首页骨架卡扫光 |
| `shimmer` | `goals` / `insights` / `report` / `trends` | 各页骨架屏（多份重复实现） |
| `blink` | `pages/chat/index.wxss` | 流式输出光标 |
| `pulse` | `pages/exercise-detail/index.wxss` | 动作指示圆点 |
| `planIn` | `pages/workout/index.wxss` | 生成的训练计划入场 |
| `scanX` / `scanMove` / `scanPulse` / `wave` / `move` | `scan` / `insights` 等 | 扫描线、波形等 |

**时长约定**：状态反馈 `150–250ms`；内容入场 `300–400ms`；循环装饰（骨架屏/呼吸）`1.2–1.8s`。
**缓动**：默认 `ease-out`；需要"回弹感"用 `cubic-bezier(.2,.8,.2,1)`。

## 3. 硬规则

1. **只动 `transform` 和 `opacity`** 用于需要流畅度的地方；`width`/`height`/`background` 会引起重排，仅用于低频的状态变化（如进度条）。
2. **不要对不可动画属性写过渡**：`color`、`font-size`、`font-weight`、`font-family`、`line-height`、`letter-spacing`、`visibility`、`pointer-events` 等（官方列表见 `skyline-wxss/references/animation.md`）。测试会直接拦下。
3. **`animation` 引用的名字必须有对应 `@keyframes`**，否则动画静默失效。测试会拦下。
4. **不要用动画表达"AI 已经改了数据"**。`miniprogram/PRODUCT_UI.md:11` 明确写着"不再以增加动画数量作为视觉升级目标"。动效只服务于**传达状态**，不是装饰。
5. 类名不要跨页复用样式：页面 WXSS **不是共享的**，`class="card action-card"` 在 A 页有样式、在 B 页不会有。要么写进 `app.wxss`，要么在本页定义。

## 3.5 交互反馈（每个可点元素都必须有）

**点按反馈三件套** —— 缺一不可：

```html
<view class="menu-row tappable" hover-class="tap" hover-start-time="0" hover-stay-time="80">
```

| 部件 | 作用 |
|---|---|
| `.tappable`（基础类） | `transition: opacity .16s ease-out` —— **过渡必须写在基础样式上**。写在 `hover-class` 里只会让"按下"有动画、"松手"瞬跳 |
| `hover-class="tap\|tap-chip\|tap-card\|tap-btn"` | 按下变暗（`.6 / .7 / .78 / .82`） |
| `hover-start-time="0" hover-stay-time="80"` | 按下立即响应，松手 80ms 收起 |

- 反馈**只用 `opacity`**，绝不用 `transform` —— 否则会盖掉元素自身的定位 transform。元素自带 transform 时（如居中按钮）必须用自定义 hover 类并把 transform 写全：参见 `.start-btn-hover`。
- **`<text>` 不支持 `hover-class`**：可点的 `<text>` 要改成 `<view>`，并把标签选择器扩成 `.chips text,.chips view`（转换后要确认容器是 flex 或子项有 `display:inline-block`，否则会从"一排胶囊"变成"整行块"）。

**浮层入场**：由 `wx:if` 挂载的面板用 `@keyframes` 入场，挂载即播放，**不用改 JS**：

- `expandDown` —— `.state-panel / .detail-panel / .motion-details / .goal-editor / .edit-grid / .add-panel / .week-panel / .quality-panel`
- `fadeIn` —— `.mask / .onboarding-mask`
- `sheetUp` —— `.edit-panel / .onboarding-card`

⚠️ **常驻元素绝不能加 `animation`**：`.nav-sheet` / `.nav-mask` 是靠 class 过渡切换的常驻元素，动画里的 `transform` 会盖掉它们自身的 `translateY(100%)`，导致开屏闪现。

校验：`missing-tap-feedback`。

## 4. 改动流程

```powershell
# 1) 动手前：看现状（死 CSS、跨页类、进度条缺过渡、旧强调色、禁用 API）
node scripts/audit_miniprogram_ui.mjs
node scripts/audit_miniprogram_ui.mjs --page=home      # 只看某页

# 2) 改 WXML / WXSS

# 3) 硬约束必须常绿
node --test miniprogram/tests/*.test.js

# 4) 有 HIGH 问题想让 CI 拦住时
node scripts/audit_miniprogram_ui.mjs --strict
```

**验收边界（必须说清楚）**：Node 测试只能保证"没有禁用 API、关键帧不缺、属性可动画、导航栏底色一致"。**动效实际观感无法用 Node 测试验证**，必须在微信开发者工具里真机/模拟器肉眼确认，并检查刷新时是否有跳变。不要仅凭测试通过就宣称动效完成。

## 5. 待办缺口

### 已完成（本轮）

- ✅ **首页改为不可滚动的导航页**：`pages/home/index.json` 设 `disableScroll: true`，`.home-page` 用 `height:100vh + overflow:hidden + flex-direction:column`，宫格 `.nav-grid` 用 `flex:1` 吸收剩余高度。任何机型都不出现滚动条，也不会裁掉内容。
- ✅ **首页内容下沉到各自页面**：今日三件事 → `plan` tab；微实验完整控件 → `insights`；今日指标数值与进度 → `goals`；7 日明细 → `trends`；健康助手 → `chat` tab。首页只保留英雄区 + 统计行 + 首要行动细条 + 8 宫格导航。
- ✅ **首页死状态清理**：随内容一起删掉了 `score/scorePct/exPct/waterPct/sleepPct/dataQuality/summary/todayExperiment/insightSummary/topInsight/aiSystem/latest/slides/onboardingStep` 与 `togglePlan/toPlan/toChat/nextOnboarding/pct`，不再有"算了但从不渲染"的字段。
- ✅ **`scan` 四步流程条已接上**：`index.wxml` 顶部渲染 `识别 → 校正 → 确认 → 入库`，由 `data.step`（1→4）驱动 `on` / `done` 两种状态；`index.wxss` 的 `.flow*` 规则已从死代码转为在用，并加了 `background-color` / `transform` / `box-shadow` 过渡。`save()` 现在会先把 `step` 推到 3，让"确认"在 finalize 请求期间真实可见，不再从 2 直接跳到 4。
- ✅ **`scan` 热量区间条已接上**：`.range-line` / `.range-fill` / `.range-dot` 同样是"样式写了但模板没用"。现在由 `index.js` 的 `buildRangeBar(low, high, point)` 按真实数据算百分比，`left`/`width` 带 `.5s` 过渡；用户校正热量时区间带与圆点会平滑移动。
- ✅ **`chat` 的 `action-flow` 会过渡**：三枚胶囊加 `background-color` + `transform` 过渡，两条连接线接入 `on` 状态（`applied` 后由灰转绿），"用户确认 / 写入计划"从硬切变成推进。
- ✅ **`plan` 勾选有完成反馈**：`.check` 加 `background-color` / `transform` / `box-shadow` 过渡并加 `scale(1.04)` 与光圈；`.task` 的 `transition:.2s`（等价 `all`）收敛为显式 `opacity .25s`；`.restore-btn` 用新增的 `@keyframes restoreIn` 淡入。
- ✅ **4 个跨页幽灵类清零**：`app.wxss` 新增共享 `.notice`（修好 `media` / `plan` 裸奔）；`settings/privacy` 补 `.action-card` 与 `.info-card .info-title`；`records/diet` 移除从 exercise 页复制来的 `.templates`。审计 HIGH 从 4 → 0。
- ✅ **`profile` 头部对比度修复**：`.edit-link`、`.avatar-text`、`.avatar-btn` 边框原本是给深色 hero 用的白色半透明，落在米白底上等于不可见，已改为 `#506336` / `#e9efd9` / `#eeeee6`。
- ✅ **全站换肤为白底 + 柠檬绿（PACE 体系）**：20 个 WXSS 全部改到 §1 的 token。做法不是逐行手改，而是用「表面感知」映射器（`表面=深色特性卡 → #111613`；`其余深绿填充=按钮/胶囊 → #c4e267 + 强制深色文字`；`浅绿 → #e9efd9`），再叠 3 处定点补丁。旧强调色 `#d8ff84` 系与深绿 `#173f35` 系**已全部清零**。
- ✅ **换肤验收有机器证据**（此前只有肉眼）：`node --test` 41/41；审计 HIGH=0；自建 WXML/WXSS → headless Chrome 渲染器 + 注入式 WCAG 对比度审计，16 个页面**逐元素**比对有效背景与文字色，最终 0 违规；另有"残留旧色"校验（同一分类器扫 WXSS/WXML/JS/JSON）0 残留。

### 交互动效（本轮新增的 4 个，都是"状态表达"而非装饰）

1. **Expanding Tag Selection**（`workout` 目标/水平/器械三组标签）
   选中项靠 **padding 增长**把邻近标签推开（低频状态变化，允许重排），再叠 `scale(1.04)` + 柠檬绿柔光。缓动 `cubic-bezier(.34,1.56,.64,1)` 出回弹感。
   ⚠️ 坑：局部 `.chips view` 的 `transition` 会**覆盖** `.tappable` 的基础过渡，必须把 `opacity .16s` 一并声明，否则松手反馈会瞬跳。
2. **Animated Text Disclosure**（`scan` / `media` 的"查看详情"）
   详情内容**常驻挂载**（不再 `wx:if`），外层 `.disclosure` 的 `height` 由 JS 按 `boundingClientRect` 实测的真实内容高度写成具体 px，所以展开/收起都是从**当前高度**连续过渡到目标高度。展开动画结束后落成 `height:auto`，内容再长高也不会被裁掉。
   ⚠️ 坑：`height:auto` 无法参与过渡，所以收起必须"先量出当前高度固化成 px → 下一帧再收到 0"；直连 `setData({detailsHeight:'0px'})` 会瞬跳。守卫见 `tests/disclosure.test.js`。
3. **Velocity-Based Slider Snap**（`goals` 6 个目标滑杆 + 每周打卡）
   原生 `<slider>` 不暴露松手速度，用 `bindchanging` 连续采样估算「步/秒」；松手时若仍 > `SNAP_VELOCITY`(6) 就按惯性**多补一格**（越界则放弃），数值读数用回弹缩放把这个补格显式反馈出来。
   ⚠️ 坑：补格过渡必须写在 `.target / .week-v` **基础类**上；只写 `.snap` 会"放大有动画、回落瞬跳"。
4. **Curved Card Deletion**（`records/diet` + `records/exercise`）
   `utils/swipeDelete.js`：卡片跟手时 `translateX + rotate(dx*0.06) + scale(1-|dx|/1600)` —— 三件事合成才是一条**弧线**，只做位移就退化成直线平移。松手超过 90px 先播完飞出动画，**然后**才弹既有的删除确认框；取消则弹回原位。
   ⚠️ 三个必须守住的点：① 主轴锁定，纵向手势直接返回、把滚动交还页面；② 用 `bindtouchmove` 而不是 `catchtouchmove`（后者会把整页滚动一起吃掉）；③ 跟手期间 `transition:none`，否则有拖尾延迟。

### 仍未做

1. **`scan` 流程条与区间条只在详情展开区可见**：`.range-line` 位于详情面板内。若希望"证据优先"在折叠态就能看见，需要把它提到 `.meal-result` 顶部。
2. **死 CSS 仍然可观**：`media` 64%、`plan` 44%、`scan` 33%、`home` 31% 的页面样式类未被 WXML 使用（多为"工程语言版本"的遗留）。
3. **归一化骨架屏动画**：`shimmer` 在 `goals` / `report` / `trends` 各定义一次且参数不一致，`insights` 又用 `move` 做同一件事。可抽到 `app.wxss` 只留一次。
4. **`checkin` / `records/*` / `plan` / `trends` / `report` 仍静默吞加载错误**，不符合 `PRODUCT_UI.md` 的四态要求。
5. **`pages/report` 依然没有入口**（只有 `app.json` 声明），健康周报打不开。
6. **字号未纳入校验**：对比度审计已覆盖颜色，但"最小字号 / 行高"仍靠人工。

### 换肤工具链（改色时复用，别重写）

放在 `.tmp_ui_ref/`（临时目录，需要时可重跑）：

| 脚本 | 作用 |
|---|---|
| `reskin_core.mjs` | 表面感知映射核心；`DARK` 表按文件列出 `surf`（深色卡）/ `ctx`（深色上下文）/ `lime`（柠檬绿容器）/ `light`（同名类出现在浅色卡的例外） |
| `reskin.mjs` | 驱动 + 守卫（声明数 / 颜色记号 / rpx / 中文 / 花括号 必须不变）。**默认 dry-run，`--apply` 才写盘** |
| `patches.mjs` | 换肤定点修正（app.json、WXML 硬编码色、Canvas 图表色、既有对比度缺陷），可重放 |
| `motion.mjs` | 交互动效改动（4 个交互），可重放 |
| `restore.mjs` | 从 `miniprogram.bak/` 完整还原，并清掉备份里不存在的文件（`tests/`、`assets/` 除外） |
| `replay.mjs` | **一键重放全流程 + 全部校验**：`node .tmp_ui_ref/replay.mjs`（加 `--skip-verify` 只改不验） |
| `verify_residual.mjs` | 用同一分类器扫 WXSS/WXML/JS/JSON，任何非 token、非语义色残留即报错 |
| `render.mjs` / `shoot.mjs` | WXML+WXSS → HTML → headless Chrome。`--force` 强制渲染所有 `wx:if`/`wx:for` 分支 |
| `contrast.mjs` | 注入脚本遍历 DOM 算 WCAG 对比度（含渐变取平均色），`--dump-dom` 取回结果 |
| `mutate.mjs` | 变异检验：故意注入缺陷，确认测试真的会失败 |

**重放顺序**：直接跑 `replay.mjs` 即可（内部就是 restore → reskin → 首页 → patches → motion → 三项校验）。**映射与动效改动都不可重入，必须先 `restore`。**

**渲染器要点**：WXSS 里的 `view` / `text` / `image` 是 WXML 标签选择器；渲染成 HTML 后标签名会变，所以渲染器给这些元素补 `wx-<tag>` 标记类并改写选择器 —— 否则 `.chips view` 这类规则会静默失效，视觉与对比度校验都会失真。


## 6. 常见坑

- **顶部导航栏底色**：`app.json` 的 `window.navigationBarBackgroundColor` 必须与 `app.wxss` 里 `page` 的背景色一致，否则下拉回弹露色差（已有测试守住）。
- **Canvas 覆盖层**：在 `<canvas>` / `<video>` / `<map>` 之上叠内容需要考虑原生组件层级，必要时用 `<cover-view>`；`<canvas type="2d">` 已是同层渲染，规则较宽松。
- **iOS 日期**：`new Date('2026-01-01')` 在 iOS 上可能解析失败，先 `replace(/-/g, '/')`。
- **页面栈上限 10**：`wx.navigateTo` 不能无限叠加，深层入口用 `redirectTo`。
- **`wx:for` 必须带 `wx:key`**，否则列表更新时节点复用会引起动画错位。

## 7. 参考

- `references/animation-inventory.md`：动画与状态样式的完整位置索引。
- `.agents/skills/skyline-worklet/`：迁移 Skyline 后的交互动画能力（**现在不要用**）。
- `.agents/skills/skyline-wxss/references/animation.md`：官方属性支持表。
- `miniprogram/PRODUCT_UI.md`：产品视觉原则（动效克制的依据）。
