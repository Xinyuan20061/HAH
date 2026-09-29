const QUICK_ITEMS = [
  { key: 'scan', label: '拍照识餐', icon: '/assets/icons/camera.png', route: '/pages/scan/index' },
  { key: 'media', label: '动作反馈', icon: '/assets/icons/activity.png', route: '/pages/media/index' },
  { key: 'insights', label: '健康提醒', icon: '/assets/icons/bell.png', route: '/pages/insights/index' },
  { key: 'trends', label: '7 日趋势', icon: '/assets/icons/trend.png', route: '/pages/trends/index' },
  { key: 'goals', label: '健康目标', icon: '/assets/icons/target.png', route: '/pages/goals/index' },
  { key: 'report', label: '健康周报', icon: '/assets/icons/report.png', route: '/pages/report/index' },
  { key: 'workout', label: '训练计划', icon: '/assets/icons/workout.png', route: '/pages/workout/index' },
  { key: 'profile', label: '身体档案', icon: '/assets/icons/profile-card.png', route: '/pages/profile/edit' }
]

Component({
  data: {
    selected: 0,
    wheelOpen: false,
    quickOpen: false,
    quickItems: QUICK_ITEMS
  },

  methods: {
    switchPrimary(e) {
      const key = e.currentTarget.dataset.key
      const url = key === 'steward' ? '/pages/chat/index' : '/pages/home/index'
      const selected = key === 'steward' ? 2 : 0
      this.closePanels()
      if (this.data.selected === selected) return
      wx.switchTab({ url })
    },

    toggleWheel() {
      this.setData({ wheelOpen: !this.data.wheelOpen, quickOpen: false })
    },

    closePanels() {
      if (this.data.wheelOpen || this.data.quickOpen) this.setData({ wheelOpen: false, quickOpen: false })
    },

    openWheelAction(e) {
      const action = e.currentTarget.dataset.action
      if (action === 'quick') {
        this.setData({ wheelOpen: false, quickOpen: true })
        return
      }
      const routes = {
        records: '/pages/records/index',
        plan: '/pages/plan/index',
        profile: '/pages/profile/index',
        settings: '/pages/settings/index'
      }
      this.openRoute(routes[action])
    },

    openQuickItem(e) {
      this.openRoute(e.currentTarget.dataset.route)
    },

    openRoute(route) {
      if (!route) return
      this.setData({ wheelOpen: false, quickOpen: false })
      setTimeout(() => wx.navigateTo({ url: route }), 120)
    }
  }
})
