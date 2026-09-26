// Curved Card Deletion：列表卡片弧形滑出删除。
//
// 轨迹由三件事合成 —— 横向位移、与位移同向的旋转、随位移加深的轻微缩放，
// 所以卡片是沿一条弧线离开，而不是平移出屏。
// 松手超过阈值才判定删除，判定后先播完飞出动画，再调用页面自己的
// confirmRemove(id, done)：用户在确认框里取消时调 done() 把卡片弹回原位。
// 未达阈值、或纵向手势，一律弹回并把滚动交还页面。
const THRESHOLD = 90   // px：横向位移超过即判定删除
const AXIS_SLOP = 8    // px：先判定主轴，避免斜向手势误触
const MAX_ROTATE = 16  // deg：最大旋转角
const EXIT_SCALE = 2.4 // 飞出时的位移倍数
const EXIT_MS = 420    // 飞出动画时长，必须与 .leaving 的 transition 一致

const curveStyle = (dx) => {
  const rotate = Math.max(-MAX_ROTATE, Math.min(MAX_ROTATE, dx * 0.06))
  const scale = 1 - Math.min(Math.abs(dx) / 1600, 0.1)
  return 'transform:translateX(' + Math.round(dx) + 'px) rotate(' + rotate.toFixed(2) + 'deg) scale(' + scale.toFixed(3) + ')'
}

module.exports = function swipeDelete() {
  return {
    swipeDeleteStart(e) {
      const t = (e.touches && e.touches[0]) || {}
      const id = e.currentTarget.dataset.id
      this._swipe = { x: t.clientX, y: t.clientY, dx: 0, axis: null, id }
      this.setData({ dragId: id, dragStyle: '', leavingId: '' })
    },
    swipeDeleteMove(e) {
      const sw = this._swipe
      if (!sw) return
      const t = (e.touches && e.touches[0]) || {}
      const dx = t.clientX - sw.x
      const dy = t.clientY - sw.y
      if (!sw.axis) {
        if (Math.abs(dx) < AXIS_SLOP && Math.abs(dy) < AXIS_SLOP) return
        sw.axis = Math.abs(dx) > Math.abs(dy) ? 'x' : 'y'
      }
      if (sw.axis !== 'x') return // 纵向手势交还页面滚动
      sw.dx = dx
      this.setData({ dragStyle: curveStyle(dx) })
    },
    swipeDeleteEnd() {
      const sw = this._swipe
      this._swipe = null
      if (!sw) return
      if (sw.axis !== 'x' || Math.abs(sw.dx) < THRESHOLD) {
        this.setData({ dragId: '', dragStyle: '' }) // 弹回原位
        return
      }
      const id = sw.id
      this.setData({ dragId: id, leavingId: id, dragStyle: curveStyle(sw.dx * EXIT_SCALE) })
      clearTimeout(this._swipeTimer)
      this._swipeTimer = setTimeout(() => {
        const settle = () => this.setData({ dragId: '', dragStyle: '', leavingId: '' })
        if (typeof this.confirmRemove === 'function') this.confirmRemove(id, settle)
        else settle()
      }, EXIT_MS)
    },
    swipeDeleteCancel() {
      this._swipe = null
      this.setData({ dragId: '', dragStyle: '', leavingId: '' })
    },
  }
}
