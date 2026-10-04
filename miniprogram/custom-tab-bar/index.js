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

const COMPANION_TABS = {
  xiaojian: {
    id: 'xiaojian',
    label: '健身房',
    icon: '/assets/tabbar/gym.png',
    activeIcon: '/assets/tabbar/gym-active.png'
  },
  xiaokang: {
    id: 'xiaokang',
    label: '养生馆',
    icon: '/assets/tabbar/wellness-space.png',
    activeIcon: '/assets/tabbar/wellness-space-active.png'
  }
}

Component({
  data: {
    selected: 0,
    wheelOpen: false,
    quickOpen: false,
    quickItems: QUICK_ITEMS,
    companionId: 'xiaojian',
    companionLabel: '健身房',
    companionIcon: COMPANION_TABS.xiaojian.icon,
    companionActiveIcon: COMPANION_TABS.xiaojian.activeIcon,
    companionSwapToken: 'a'
  },

  lifetimes: {
    attached() { this.syncCompanion() }
  },

  pageLifetimes: {
    show() { this.syncCompanion() }
  },

  methods: {
    syncCompanion(agentId) {
      const saved = agentId || wx.getStorageSync('healthmate_agent_id')
      const next = COMPANION_TABS[saved] || COMPANION_TABS.xiaojian
      const changed = next.id !== this.data.companionId
      this.setData({
        companionId: next.id,
        companionLabel: next.label,
        companionIcon: next.icon,
        companionActiveIcon: next.activeIcon,
        companionSwapToken: changed ? (this.data.companionSwapToken === 'a' ? 'b' : 'a') : this.data.companionSwapToken
      })
    },

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
