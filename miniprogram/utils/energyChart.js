function init(page, selector, key, options) {
  wx.createSelectorQuery().in(page).select(selector).fields({ node: true, size: true }).exec(res => {
    const item = res && res[0]
    if (!item || !item.node) return
    const dpr = (wx.getWindowInfo ? wx.getWindowInfo().pixelRatio : wx.getSystemInfoSync().pixelRatio) || 1
    const canvas = item.node
    const ctx = canvas.getContext('2d')
    canvas.width = item.width * dpr
    canvas.height = item.height * dpr
    ctx.scale(dpr, dpr)
    page._energyCharts = page._energyCharts || {}
    page._energyCharts[key] = { canvas, ctx, width: item.width, height: item.height, options: null, centers: [] }
    draw(page, key, options)
  })
}

function roundedRect(ctx, x, y, width, height, radius) {
  const r = Math.max(0, Math.min(radius, width / 2, height / 2))
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.arcTo(x + width, y, x + width, y + height, r)
  ctx.arcTo(x + width, y + height, x, y + height, r)
  ctx.arcTo(x, y + height, x, y, r)
  ctx.arcTo(x, y, x + width, y, r)
  ctx.closePath()
}

function draw(page, key, options, activeIndex = -1) {
  const chart = page._energyCharts && page._energyCharts[key]
  if (!chart) return
  chart.options = options
  const { ctx, width: w, height: h } = chart
  const days = options.days || []
  ctx.clearRect(0, 0, w, h)
  if (!days.length) return

  const pad = { left: 34, right: 12, top: 30, bottom: 38 }
  const positiveHeight = (h - pad.top - pad.bottom) * 0.76
  const negativeHeight = (h - pad.top - pad.bottom) - positiveHeight
  const baseline = pad.top + positiveHeight
  const plotWidth = w - pad.left - pad.right
  const maxPositive = Math.max(1, ...days.map(day => Math.max(Number(day.intake) || 0, Number(day.upper) || 0))) * 1.08
  const maxExercise = Math.max(1, ...days.map(day => Number(day.exercise) || 0)) * 1.18
  const xFor = index => pad.left + plotWidth * (index + 0.5) / days.length
  const yFor = value => baseline - (Number(value) || 0) / maxPositive * positiveHeight
  const barWidth = Math.min(25, plotWidth / days.length * 0.54)
  const colors = { breakfast: '#c4e267', lunch: '#e9efd9', dinner: '#506336', snack: '#5f665f' }

  if (activeIndex >= 0 && days[activeIndex]) {
    const cellWidth = plotWidth / days.length
    roundedRect(ctx, xFor(activeIndex) - cellWidth * 0.42, pad.top - 8, cellWidth * 0.84, h - pad.top - pad.bottom + 14, 12)
    ctx.fillStyle = 'rgba(196,226,103,.13)'
    ctx.fill()
  }

  ctx.font = '10px sans-serif'
  ctx.textAlign = 'right'
  ctx.textBaseline = 'middle'
  ctx.fillStyle = '#5f665f'
  ctx.strokeStyle = '#eeeee6'
  ctx.lineWidth = 1
  for (let i = 0; i <= 3; i++) {
    const value = maxPositive * (3 - i) / 3
    const y = pad.top + positiveHeight * i / 3
    ctx.beginPath()
    ctx.moveTo(pad.left, y)
    ctx.lineTo(w - pad.right, y)
    ctx.stroke()
    ctx.fillText(value >= 1000 ? `${(value / 1000).toFixed(1)}k` : `${Math.round(value)}`, pad.left - 6, y)
  }

  ctx.beginPath()
  days.forEach((day, index) => {
    const x = xFor(index)
    const y = yFor(day.target)
    if (index === 0) ctx.moveTo(x, y)
    else ctx.lineTo(x, y)
  })
  ctx.strokeStyle = '#111613'
  ctx.lineWidth = 1.5
  ctx.setLineDash([5, 4])
  ctx.stroke()
  ctx.setLineDash([])

  const centers = []
  days.forEach((day, index) => {
    const x = xFor(index)
    centers.push(x)
    let cursor = baseline
    ;['breakfast', 'lunch', 'dinner', 'snack'].forEach(meal => {
      const value = Number(day[meal]) || 0
      if (!value) return
      const height = Math.max(1.5, value / maxPositive * positiveHeight)
      ctx.fillStyle = colors[meal]
      ctx.fillRect(x - barWidth / 2, cursor - height, barWidth, height)
      cursor -= height
    })
    if ((Number(day.intake) || 0) > 0) {
      ctx.beginPath()
      ctx.arc(x, cursor, activeIndex === index ? 3.8 : 2.6, 0, Math.PI * 2)
      ctx.fillStyle = activeIndex === index ? '#c4e267' : '#111613'
      ctx.fill()
    }
    const exercise = Number(day.exercise) || 0
    if (exercise > 0) {
      const exerciseHeight = Math.max(3, exercise / maxExercise * Math.max(18, negativeHeight - 8))
      roundedRect(ctx, x - barWidth * 0.27, baseline + 4, barWidth * 0.54, exerciseHeight, barWidth * 0.27)
      ctx.fillStyle = '#111613'
      ctx.fill()
    }
  })
  chart.centers = centers

  ctx.beginPath()
  ctx.moveTo(pad.left, baseline)
  ctx.lineTo(w - pad.right, baseline)
  ctx.strokeStyle = '#b7bcb2'
  ctx.lineWidth = 1
  ctx.stroke()

  ctx.font = '10px sans-serif'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'top'
  ctx.fillStyle = '#5f665f'
  days.forEach((day, index) => ctx.fillText(day.label, xFor(index), h - pad.bottom + 15))
}

function touch(page, key, event) {
  const chart = page._energyCharts && page._energyCharts[key]
  if (!chart || !chart.centers.length) return -1
  const point = (event.touches && event.touches[0]) || (event.changedTouches && event.changedTouches[0])
  if (!point) return -1
  const x = Number(point.x != null ? point.x : point.clientX)
  let active = 0
  let distance = Infinity
  chart.centers.forEach((center, index) => {
    const next = Math.abs(center - x)
    if (next < distance) { distance = next; active = index }
  })
  draw(page, key, chart.options, active)
  return active
}

module.exports = { init, draw, touch }
