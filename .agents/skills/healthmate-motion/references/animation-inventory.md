# 动画与状态样式索引

> 由 `node scripts/audit_miniprogram_ui.mjs` 与代码扫描得出。改动前先查这里，避免重复实现。

## 1. `@keyframes` 全量清单

| keyframes | 定义位置 | 绑定选择器 | 参数 | 是否在用 |
|---|---|---|---|---|
| `stateSpin` | `app.wxss` | `.state-spinner` | `.8s linear infinite` | ✅ 全局状态圈 |
| `skeletonMove` | `app.wxss` | `.skeleton-line` | `1.4s ease infinite` | ✅ 全局骨架行 |
| `shine` | `pages/home/index.wxss` | `.skeleton::after` | `1.35s infinite` | ✅ 首页骨架卡扫光 |
| `move` | `pages/insights/index.wxss` | `.skeleton-card` | `1.4s ease infinite` | ✅ |
| `shimmer` | `pages/goals/index.wxss` | `.shimmer` | `1.35s infinite` | ✅ |
| `shimmer` | `pages/report/index.wxss` | `.shimmer` | `1.3s infinite` | ✅ **与 goals 同名不同参** |
| `shimmer` | `pages/trends/index.wxss` | `.shimmer` | `1.3s infinite` | ✅ **同上** |
| `blink` | `pages/chat/index.wxss` | `.cursor` | `1s infinite` | ✅ 流式光标 |
| `pulse` | `pages/exercise-detail/index.wxss` | `.motion-dot view` | `1.7s infinite` | ✅ |
| `planIn` | `pages/workout/index.wxss` | `.plan` | `.38s ease-out` | ✅ 计划入场 |
| `restoreIn` | `pages/plan/index.wxss` | `.restore-btn` | `.22s ease-out` | ✅ "恢复"按钮淡入 |
| `scanX` | `pages/media/index.wxss` | `.scan-line` | `1.6s ease-in-out infinite` | ❌ 选择器已不在 WXML 中 |
| `wave` | `pages/media/index.wxss` | `.wave view` | `1.2s infinite` | ❌ 同上 |
| `scanMove` | `pages/scan/index.wxss` | `.scan-beam.active` | `1.6s cubic-bezier(.45,.05,.55,.95) infinite` | ❌ `.scan-beam` 不在 WXML 中 |
| `scanPulse` | `pages/scan/index.wxss` | `.scan-dot` | `1s infinite` | ✅ |

**已知重复**：`shimmer` 在 3 个页面各自定义、参数不一致；`insights` 又用 `move` 做了同一件事。若要统一，抽到 `app.wxss` 定义一次，页面只保留各自的 `.shimmer` 宽高。

**已知死动画**：`scanX`、`wave`、`scanMove` 绑定的选择器已从 WXML 中移除，属于遗留。`records/diet` 的 `.templates`、`settings/privacy` 的 `.action-card` 同类。

## 2. 过渡（`transition`）现状

| 位置 | 写法 | 说明 |
|---|---|---|
| `pages/goals/index.wxss` `.progress view` | `transition: width .35s` | ✅ 进度条平滑增长 |
| `pages/home/index.wxss` `.dot-core` | `transition: background-color/box-shadow/transform .3s` | ✅ 7 日打卡圆点 |
| `pages/scan/index.wxss` `.range-line .range-fill` | `transition: left/width .5s …` | ✅ 热量区间带 |
| `pages/scan/index.wxss` `.range-line .range-dot` | `transition: left .5s …` | ✅ 估算点平滑移动 |
| `pages/scan/index.wxss` `.flow-item text` / `.flow-line` | `transition: background-color/transform/box-shadow .28–.3s` | ✅ 四步流程推进 |
| `pages/chat/index.wxss` `.action-flow text` / `.action-line` | `transition: background-color/transform/box-shadow .32s` | ✅ 计划三步流转 |
| `pages/plan/index.wxss` `.task` | `transition: opacity .25s ease-out` | ✅ 勾选后淡出 |
| `pages/plan/index.wxss` `.check` | `transition: background-color/transform/box-shadow .25s` | ✅ 勾选完成反馈 |
| `pages/workout/index.wxss` `.exercise` | `transition: background .2s` | ✅ `background` 可动画 |
| `pages/profile/index.wxss` `.edit-link` | `transition: background-color .2s ease-out` | ✅ |

**已知重复**：`shimmer` 在 3 个页面各自定义、参数不一致；`insights` 又用 `move` 做了同一件事。若要统一，抽到 `app.wxss` 定义一次，页面只保留各自的 `.shimmer` 宽高。

**已知死动画**：`scanX`、`wave`、`scanMove` 绑定的选择器已从 WXML 中移除，属于遗留。（`.flow*` 与 `.range-line*` 已不再是死代码。）

## 3. 全局状态样式（`app.wxss`，优先复用）

| 类 | 用途 |
|---|---|
| `.state-card` | 通用状态容器（loading/提示） |
| `.state-card-error` | 错误态配色 |
| `.state-card-success` | 成功态配色 |
| `.state-title` / `.state-desc` | 状态标题/说明 |
| `.state-spinner` | 旋转加载圈（`stateSpin`） |
| `.skeleton-line` | 骨架行（`skeletonMove`） |
| `.empty` | 空态文案 |
| `.notice` | 页面底部安全/免责说明（`media` / `plan` 使用；`profile` / `scan` 有自己的覆盖版） |
| `.safe-area` | `env(safe-area-inset-bottom)` 占位 |

页面级状态展示（`home` / `insights` / `scan` / `evaluation` / `settings/privacy`）已按 `loading / empty / error+retry / success` 四态实现。`checkin`、`records/*`、`plan`、`trends`、`report` 目前是静默吞错，新增状态展示时优先套用上面这组类。

## 4. 校验命令

```powershell
node scripts/audit_miniprogram_ui.mjs          # 含死动画 / 死关键帧 / 缺过渡报告
node --test miniprogram/tests/*.test.js        # 硬约束
```
