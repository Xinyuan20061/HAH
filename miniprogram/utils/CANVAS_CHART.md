# Canvas 交互图表

`canvasChart.js` 是无第三方依赖的微信小程序 Canvas 2D 图表层，用于替代旧版纯 CSS 柱形图。

已支持：

- DPR 高清适配；
- 4 级网格线；
- 折线、面积渐变、数据点；
- 触摸后吸附最近数据点；
- 垂直辅助线；
- Canvas 内 tooltip；
- 指标单位与数值格式化；
- 页面切换指标后无须重新创建 canvas。

当前用于：

- `pages/trends/index`
- `pages/report/index`

如果后续需要缩放、双 Y 轴、数据缩放器、复杂 tooltip，可直接替换为 ECharts for WeChat；页面数据结构已经按 `label/value` 方式解耦。
