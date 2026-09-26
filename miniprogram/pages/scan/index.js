const api = require('../../utils/request')
const cloudMedia = require('../../utils/cloudMedia')
const { ensureLogin } = require('../../utils/auth')
const editable = ['dish_name','portion','cooking_method','weight_g','calories','protein','carbs','fat','fiber']
const pending = require('../../utils/pendingJobs')
const { pollJob } = require('../../utils/jobPolling')

Page({
  data: {
    image:'', imageName:'', loading:false, result:null, form:null, editing:false,
    dirty:false, saving:false, state:'idle', error:'', step:1, showDetails:false, detailsHeight:'0px',
    rangeBar:{bandLeft:0,bandWidth:0,pointPct:0},
    jobStatus:'', workerOnline:null, cloudMode:api.isCloud(), mediaAsset:null, jobId:null
  },
  buildRangeBar(low, high, point){
    const lo=Math.max(0,Number(low)||0)
    const hi=Math.max(lo,Number(high)||0)
    const p=Math.max(0,Number(point)||0)
    const span=Math.max(hi,p)*1.35||1
    const pct=v=>Math.max(0,Math.min(100,Math.round(v/span*1000)/10))
    return {bandLeft:pct(lo),bandWidth:Math.max(1.5,pct(hi)-pct(lo)),pointPct:pct(p)}
  },
  async onLoad(){
    this._unloaded=false
    try {
      await ensureLogin()
      const pointer=pending.load('food')
      if(pointer)this.setData({jobId:pointer.jobId,mediaAsset:pointer.asset,state:'ready',jobStatus:'有云端任务，点击识别查看结果'})
      await this.refreshWorker()
    }catch(e){this.setData({error:e.message||'请先登录'})}
  },
  onUnload(){ this._unloaded=true },
  async refreshWorker(){ try{ const w=await api.get('/system/ai-worker',{allowCache:false}); this.setData({workerOnline:!!w.food_online}) }catch(e){} },
  async choose(){
    if(this.data.loading)return
    try{
      const r=await wx.chooseMedia({count:1,mediaType:['image'],sourceType:['camera','album'],sizeType:['compressed']})
      if(r.tempFiles?.length){
        pending.forget('food')
        const f=r.tempFiles[0]
        this.setData({image:f.tempFilePath,imageName:(f.tempFilePath||'').split('/').pop(),result:null,form:null,editing:false,dirty:false,state:'ready',error:'',step:1,jobStatus:'',mediaAsset:null, jobId:null})
      }
    }catch(e){}
  },
  async analyze(){
    if(this.data.loading)return
    if(!this.data.image&&!this.data.mediaAsset){ await this.choose(); if(!this.data.image)return }
    this.setData({loading:true,state:'loading',error:'',step:1,jobStatus:'准备视觉任务'})
    try{
      await ensureLogin()
      let r
      {
        await this.refreshWorker()
        if(this.data.workerOnline===false) wx.showToast({title:'本地视觉 Worker 离线，任务会在云端等待',icon:'none',duration:2500})
        let asset=this.data.mediaAsset
        if(!asset){
          this.setData({jobStatus:'上传至微信云存储'})
          asset=api.isCloud()?await cloudMedia.uploadAndRegister(this.data.image,'image',this.data.imageName):await api.upload('/media/upload',this.data.image)
          this.setData({mediaAsset:asset})
        }
        let jobId=this.data.jobId
        if(!jobId){
          const created=await api.post('/vision/food-jobs',{media_id:asset.media_id})
          jobId=created.job_id; this.setData({jobId})
        }
        pending.remember('food',jobId,asset)
        r=await pollJob({path:'/vision/food-jobs',jobId,asset,cancelled:()=>this._unloaded,
          onStatus:label=>{if(!this._unloaded)this.setData({jobStatus:label})}})
      }
      if(this._unloaded)return
      if(r.analysis_id){
        const latest=await api.get(`/vision/food-analysis/${r.analysis_id}`,{allowCache:false})
        if(latest.status==='record_deleted')throw new Error('对应饮食记录已删除，请选择新的照片或手动记录')
        r={...r,...latest.initial,...latest.corrected}
        if(latest.corrected&&latest.corrected.weight_g!==undefined)r.estimated_weight_g=latest.corrected.weight_g
        if(latest.status==='finalized'){
          pending.forget('food');this.setData({state:'saved',step:4,jobStatus:'已确认保存'});return
        }
      }
      if(this._unloaded)return
      r.sourceLabel=r.source==='cloud'?'云端':'本地'
      r.visible_items=Array.isArray(r.visible_items)?r.visible_items:[]
      r.uncertainty_reasons=Array.isArray(r.uncertainty_reasons)?r.uncertainty_reasons:[]
      r.calorie_range_low=r.calorie_range_low!==null&&r.calorie_range_low!==undefined&&Number.isFinite(Number(r.calorie_range_low))?Number(r.calorie_range_low):Math.round((Number(r.calories)||0)*.85)
      r.calorie_range_high=r.calorie_range_high!==null&&r.calorie_range_high!==undefined&&Number.isFinite(Number(r.calorie_range_high))?Number(r.calorie_range_high):Math.round((Number(r.calories)||0)*1.15)
      r.portion_basis=r.portion_basis||'依据图片可见份量估算，缺少比例尺时需人工确认。'
      const rawItems=Array.isArray(r.items)&&r.items.length?r.items:[{name:r.dish_name||'整份餐食',portion:r.portion||'',portion_basis:r.portion_basis,weight_g:Number(r.estimated_weight_g)||0,calories:Number(r.calories)||0,calorie_range_low:r.calorie_range_low,calorie_range_high:r.calorie_range_high,protein:Number(r.protein)||0,carbs:Number(r.carbs)||0,fat:Number(r.fat)||0,fiber:Number(r.fiber)||0,confidence:Number(r.confidence)||0,evidence:'整体估算，建议按实际份量确认。',in_image:false}]
      r.items=rawItems.map(item=>({...item,calorie_range_low:Number(item.calorie_range_low)||Math.round((Number(item.calories)||0)*.8),calorie_range_high:Number(item.calorie_range_high)||Math.round((Number(item.calories)||0)*1.2)}))
      const form={dish_name:r.dish_name||'',portion:r.portion||'',cooking_method:r.cooking_method||'',weight_g:Number(r.estimated_weight_g)||0,calories:Number(r.calories)||0,protein:Number(r.protein)||0,carbs:Number(r.carbs)||0,fat:Number(r.fat)||0,fiber:Number(r.fiber)||0,items:r.items.map(item=>({...item}))}
      this.setData({result:r,form,state:'success',step:2,jobStatus:'完成',rangeBar:this.buildRangeBar(r.calorie_range_low,r.calorie_range_high,form.calories)})
    }catch(e){
      if(this._unloaded)return
      this.setData({state:'error',error:e.message||'识别失败'})
      wx.showModal({title:'识别未完成',content:e.message||'请稍后重试或手动记录',showCancel:false})
    }finally{if(!this._unloaded)this.setData({loading:false})}
  },
  toggleEdit(){this.setData({editing:!this.data.editing})},

  // Animated Text Disclosure：按真实内容高度过渡，而不是固定时长淡入
  measureDetails(cb) {
    wx.createSelectorQuery().in(this).select('#detailsBody').boundingClientRect((r) => {
      cb(r && r.height ? Math.ceil(r.height) : 0)
    }).exec()
  },
  toggleDetails() {
    const open = !this.data.showDetails
    if (open) {
      this.setData({ showDetails: true })
      this.measureDetails((h) => this.setData({ detailsHeight: h + 'px' }))
      
      return
    }
    // 收起：先量出当前高度并固化成具体值，下一帧再收到 0，才能连续过渡
    this.measureDetails((h) => {
      this.setData({ detailsHeight: h + 'px' }, () => {
        setTimeout(() => this.setData({ detailsHeight: '0px', showDetails: false }), 20)
      })
    })
  },
  // 展开动画结束后落成 auto：内容再长高也不会被裁掉
  onDisclosureEnd(e) {
    if (!e || !e.detail || e.detail.propertyName !== 'height') return
    if (this.data.showDetails && this.data.detailsHeight !== 'auto') this.setData({ detailsHeight: 'auto' })
  },
  onField(e){const field=e.currentTarget.dataset.field;if(!editable.includes(field))return;let value=e.detail.value;if(field!=='dish_name'&&field!=='portion'&&field!=='cooking_method')value=Number(value)||0;this.setData({[`form.${field}`]:value,dirty:true})},
  onItemField(e){
    const index=Number(e.currentTarget.dataset.index),field=e.currentTarget.dataset.field
    if(!Number.isInteger(index)||!['name','portion','weight_g','calories','protein','carbs','fat','fiber'].includes(field))return
    const items=(this.data.form.items||[]).map(item=>({...item}))
    if(!items[index])return
    items[index][field]=['name','portion'].includes(field)?e.detail.value:(Number(e.detail.value)||0)
    if(field==='calories'){
      items[index].calorie_range_low=Math.round(items[index].calories*.8*10)/10
      items[index].calorie_range_high=Math.round(items[index].calories*1.2*10)/10
    }
    const sum=key=>Math.round(items.reduce((total,item)=>total+(Number(item[key])||0),0)*10)/10
    this.setData({'form.items':items,'form.weight_g':sum('weight_g'),'form.calories':sum('calories'),'form.protein':sum('protein'),'form.carbs':sum('carbs'),'form.fat':sum('fat'),'form.fiber':sum('fiber'),'result.calorie_range_low':sum('calorie_range_low'),'result.calorie_range_high':sum('calorie_range_high'),rangeBar:this.buildRangeBar(sum('calorie_range_low'),sum('calorie_range_high'),sum('calories')),dirty:true})
  },
  async applyCorrection(){if(!this.data.result||!this.data.form||!this.data.dirty)return true;try{const r=await api.put(`/vision/food-analysis/${this.data.result.analysis_id}/correct`,this.data.form);this.setData({dirty:false,editing:false,step:3});wx.showToast({title:`已校正 ${r.changed_fields.length} 项`});return true}catch(e){wx.showModal({title:'校正失败',content:e.message||'请稍后重试',showCancel:false});return false}},
  async save(){const r=this.data.result;if(!r||this.data.saving)return;this.setData({saving:true,error:'',step:Math.max(this.data.step,3)});try{if(this.data.dirty){const ok=await this.applyCorrection();if(!ok)return}await api.post(`/vision/food-analysis/${r.analysis_id}/finalize`,{meal_type:'other',confirmed:true});pending.forget('food');this.setData({state:'saved',step:4});wx.showToast({title:'已确认并保存'});setTimeout(()=>wx.navigateTo({url:'/pages/records/diet'}),700)}catch(e){this.setData({state:'error',error:e.message||'保存失败'});wx.showModal({title:'保存失败',content:e.message||'请稍后再试',showCancel:false})}finally{this.setData({saving:false})}},
  retry(){this.analyze()}
})
