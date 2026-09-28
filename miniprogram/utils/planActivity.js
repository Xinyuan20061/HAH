const DAY_MS = 24 * 60 * 60 * 1000

function parseDay(value) {
  const parts = String(value || '').split('-').map(Number)
  if (parts.length !== 3 || parts.some(part => !Number.isFinite(part))) return null
  return new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]))
}

function dayDistance(start, current) {
  return Math.round((current.getTime() - start.getTime()) / DAY_MS)
}

function statusOf(day) {
  const done = Math.max(0, Number(day.done) || 0)
  const total = Math.max(0, Number(day.total) || 0)
  if (total > 0 && done >= total) return 'complete'
  if (done > 0) return 'partial'
  return 'empty'
}

function emptyActivity() {
  return { cells: [], weeks: [], months: [], columns: 53, summary: { completeDays: 0, partialDays: 0, activeDays: 0 } }
}

function buildPlanActivity(payload = {}) {
  const source = Array.isArray(payload.days) ? payload.days : []
  if (!source.length) return emptyActivity()
  const start = parseDay(payload.start || source[0].date)
  const endKey = payload.end || source[source.length - 1].date
  if (!start) return emptyActivity()

  const leadingDays = start.getUTCDay()
  const cells = []
  const months = []
  const seenMonths = new Set()
  let maxColumn = 1
  let completeDays = 0
  let partialDays = 0

  source.forEach(day => {
    const date = parseDay(day.date)
    if (!date) return
    const position = leadingDays + dayDistance(start, date)
    const column = Math.floor(position / 7) + 1
    const row = date.getUTCDay() + 1
    const status = statusOf(day)
    const done = Math.max(0, Number(day.done) || 0)
    const total = Math.max(0, Number(day.total) || 0)
    const monthKey = `${date.getUTCFullYear()}-${date.getUTCMonth()}`
    maxColumn = Math.max(maxColumn, column)
    completeDays += Number(status === 'complete')
    partialDays += Number(status === 'partial')
    cells.push({
      key: day.date,
      date: day.date,
      column,
      row,
      status,
      tone: status === 'empty' ? 'unfilled' : status,
      today: day.date === endKey,
      ariaLabel: `${date.getUTCMonth() + 1}月${date.getUTCDate()}日，${status === 'complete' ? '计划全部完成' : status === 'partial' ? `完成 ${done} 项，共 ${total} 项` : '没有完成计划'}`
    })
    if (!seenMonths.has(monthKey) && date.getUTCDate() <= 7) {
      seenMonths.add(monthKey)
      months.push({ column, label: `${date.getUTCMonth() + 1}月` })
    }
  })

  const columns = Math.max(53, maxColumn)
  const weeks = Array.from({ length: columns }, (_, columnIndex) => ({
    key: `week-${columnIndex + 1}`,
    days: Array.from({ length: 7 }, (_, rowIndex) => ({
      key: `blank-${columnIndex + 1}-${rowIndex + 1}`,
      spacer: true
    }))
  }))
  cells.forEach(cell => { weeks[cell.column - 1].days[cell.row - 1] = cell })
  const monthLabels = months.map((month, index) => ({
    ...month,
    style: `left:${((month.column - 1) / Math.max(1, columns - 1) * 100).toFixed(2)}%;${index === months.length - 1 && month.column >= columns - 1 ? 'transform:translateX(-100%)' : ''}`
  }))
  const summary = payload.summary || {}
  return {
    cells,
    weeks,
    months: monthLabels,
    columns,
    summary: {
      completeDays: Number(summary.complete_days ?? completeDays),
      partialDays: Number(summary.partial_days ?? partialDays),
      activeDays: Number(summary.active_days ?? (completeDays + partialDays))
    }
  }
}

module.exports = { buildPlanActivity, statusOf }
