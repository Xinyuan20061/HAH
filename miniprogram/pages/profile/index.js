const api = require('../../utils/request');
const cloudMedia = require('../../utils/cloudMedia');
const config = require('../../config/index');

function initialOf(nickname) {
  const s = String(nickname || '').trim();
  return s ? s[0] : '微';
}

Page({
  data: { loading: true, error: '', user: {}, profile: null, goalText: '', ai: { enabled: false, has_api_key: false, model: 'deepseek-chat' }, avatarSrc: '', initial: 'H', editing: false, nickDraft: '', saving: false },
  onShow() { this.load() },
  async load() {
    const firstLoad = !this.data.user.id;
    if (firstLoad) this.setData({ loading: true, error: '' });
    try {
      await api.ensureToken();
      let [u, p, a] = await Promise.all([api.get('/users/me'), api.get('/users/me/health-profile'), api.get('/users/me/ai-config')]);
      let map = { lose: '减脂', maintain: '保持健康', gain: '增肌' };
      this.setData({ loading: false, error: '', user: u, profile: p, ai: a, goalText: p ? map[p.goal_type] || p.goal_type : '', initial: initialOf(u.nickname) });
      this.applyAvatar(u.avatar_url);
      if (!u.nickname && !this._guided) {
        this._guided = true;
        this.openEdit();
      }
    } catch (e) { this.setData({ loading: false, error: (e && e.message) || '请稍后重试' }); console.warn('profile load failed', e) }
  },
  async applyAvatar(url) {
    let src = '';
    if (url) {
      try {
        src = String(url).startsWith('cloud://') ? await cloudMedia.tempUrl(url) : url;
      } catch (e) { src = '' }
    }
    this.setData({ avatarSrc: src });
  },
  onChooseAvatar(e) {
    const temp = e.detail && e.detail.avatarUrl;
    if (!temp) return;
    this._avatarTemp = temp;
    this.setData({ avatarSrc: temp });
  },
  openEdit() { this.setData({ editing: true, nickDraft: this.data.user.nickname || '' }) },
  closeEdit() { this.setData({ editing: false }) },
  retry() { this.load() },
  onNickInput(e) { this.setData({ nickDraft: e.detail.value }) },
  async saveProfile() {
    const nick = String(this.data.nickDraft || '').trim();
    if (!nick) { wx.showToast({ title: '昵称不能为空', icon: 'none' }); return }
    if (this.data.saving) return;
    this.setData({ saving: true });
    try {
      let avatarUrl = this.data.user.avatar_url || '';
      if (this._avatarTemp) {
        const uid = wx.getStorageSync('healthmate_user_id');
        if (config.cloudReady()) {
          const ext = String(this._avatarTemp).match(/\.([A-Za-z0-9]+)(?:\?.*)?$/) ? '.' + String(this._avatarTemp).match(/\.([A-Za-z0-9]+)(?:\?.*)?$/)[1].toLowerCase() : '.jpg';
          const up = await wx.cloud.uploadFile({ cloudPath: `healthmate/u${uid || 'x'}/avatar/${Date.now()}${ext}`, filePath: this._avatarTemp });
          avatarUrl = up.fileID;
        } else {
          const asset = await api.upload('/media/upload', this._avatarTemp);
          avatarUrl = asset && asset.url ? asset.url : avatarUrl;
        }
      }
      const me = await api.put('/users/me', { nickname: nick, avatar_url: avatarUrl });
      this._avatarTemp = null;
      this.setData({ user: me, initial: initialOf(me.nickname), editing: false, saving: false });
      this.applyAvatar(me.avatar_url);
      wx.showToast({ title: '已保存', icon: 'success' });
    } catch (e) {
      this.setData({ saving: false });
      wx.showToast({ title: (e && e.message) || '保存失败', icon: 'none' });
    }
  },
  edit() { wx.navigateTo({ url: '/pages/profile/edit' }) },
  goals() { wx.navigateTo({ url: '/pages/goals/index' }) },
  aiSettings() { wx.navigateTo({ url: '/pages/settings/ai/index' }) },
  trends() { wx.navigateTo({ url: '/pages/trends/index' }) },
  insights() { wx.navigateTo({ url: '/pages/insights/index' }) },
  evaluation() { wx.navigateTo({ url: '/pages/evaluation/index' }) },
  privacy() { wx.navigateTo({ url: '/pages/settings/privacy/index' }) }
})
