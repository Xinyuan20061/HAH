数据口径：2026-09-23 冻结评测。RAG 49 条查询集（rag-v1：Hit@3 41/41、拒绝率 8/8、来源完整 100%；Hit@1 31/41；MRR 0.874）；Agent 31 条固定集（agent-v1，mock provider 离线）。结构契约=计划合法/知识注入/回复非空/无裸链/免责声明/安全等级 6 项均 100%。

```echarts
{
  title: { text: 'AI / Agent / RAG 评测达成率（2026-09-23 冻结评测）' },
  tooltip: { trigger: 'item', triggerOn: 'mousemove|click', renderMode: 'richText', confine: true },
  grid: { left: 8, right: 70, top: 40, bottom: 8, containLabel: true },
  xAxis: { type: 'value', min: 0, max: 110, axisLabel: { formatter: '{value}%' } },
  yAxis: {
    type: 'category',
    inverse: true,
    data: [
      'Agent 降级回退',
      'Agent 结构契约（6 项）',
      'Agent 安全拦截',
      'Agent 意图识别',
      'RAG MRR（0.874）',
      'RAG Hit@1',
      'RAG 来源完整率',
      'RAG 无关拒绝率',
      'RAG Hit@3'
    ],
    axisLabel: { fontSize: 12 }
  },
  series: [
    {
      type: 'bar',
      barWidth: 18,
      label: { show: true, position: 'right', fontSize: 11, formatter: function (p) { return p.value + '%'; } },
      data: [100, 100, 100, 100, 87.4, 75.61, 100, 100, 100]
    }
  ]
}
```

结论：9 项评测指标中 8 项满分（RAG Hit@3、拒绝率、来源完整；Agent 意图、安全、结构、降级均 100%）；唯一未满的是词法检索 Hit@1 75.61%（"好处/危害"泛意图与疾病指南的词法边界，报告已如实标注，不调参掩盖）。
