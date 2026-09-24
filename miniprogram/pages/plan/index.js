const api=require('../../utils/request');
Page({
  data:{headline:'今天只做三件真正有用的事',items:[],doneCount:0,agentPlan:null,showWeek:false,showAdd:false,addTitle:'',addDesc:'',saving:false},
  onShow(){this.load()},
  async load(){try{const [r,a]=await Promise.all([api.get('/health/plan/today'),api.get('/agent/plans/current')]);this.setData({headline:r.headline,items:r.items||[],doneCount:r.done_count||0,agentPlan:a.plan||null})}catch(e){}},
  toggleAdd(){this.setData({showAdd:!this.data.showAdd,addTitle:'',addDesc:''})},
  onTitle(e){this.setData({addTitle:e.detail.value})},
  onDesc(e){this.setData({addDesc:e.detail.value})},
  async saveCustom(){
    const title=(this.data.addTitle||'').trim()
    if(!title)return wx.showToast({title:'请输入计划内容',icon:'none'})
    if(this.data.saving)return
    this.setData({saving:true})
    try{
      await api.post('/health/plan/today/custom',{title,description:this.data.addDesc||'',task_type:'other'})
      this.setData({showAdd:false,addTitle:'',addDesc:''})
      wx.showToast({title:'已添加到今日计划'})
      this.load()
    }catch(e){wx.showModal({title:'添加失败',content:e.message||'请稍后重试',showCancel:false})}
    finally{this.setData({saving:false})}
  },
  async toggle(e){
    const key=e.currentTarget.dataset.key,done=e.currentTarget.dataset.done==='true'
    try{await api.put(`/health/plan/today/${key}`,{done:!done});wx.showToast({title:done?'已恢复':'已完成，可点恢复',icon:'none'});this.load()}catch(err){wx.showToast({title:'更新失败',icon:'none'})}
  },
  async restore(e){
    const key=e.currentTarget.dataset.key
    try{await api.put(`/health/plan/today/${key}`,{done:false});wx.showToast({title:'已恢复未完成',icon:'none'});this.load()}catch(err){wx.showToast({title:'恢复失败',icon:'none'})}
  },
  async removeCustom(e){
    const key=e.currentTarget.dataset.key
    wx.showModal({title:'删除这条计划？',content:'删除后不可恢复，仅删除手动添加的计划。',confirmText:'删除',confirmColor:'#c0392b',
      success:async res=>{if(!res.confirm)return;try{await api.del(`/health/plan/today/custom/${key}`);wx.showToast({title:'已删除'});this.load()}catch(err){wx.showToast({title:'删除失败',icon:'none'})}}})
  },
  async toggleAgent(e){const id=Number(e.currentTarget.dataset.id),done=e.currentTarget.dataset.done==='true';try{await api.put(`/agent/plans/items/${id}`,{done:!done});this.load()}catch(err){wx.showToast({title:'更新失败',icon:'none'})}},
  toggleWeek(){this.setData({showWeek:!this.data.showWeek})}
})
