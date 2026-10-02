const api = require('../../utils/request')
const cloudMedia = require('../../utils/cloudMedia')
const { ensureLogin } = require('../../utils/auth')
const editable = ['dish_name','portion','cooking_method','weight_g','calories','protein','carbs','fat','fiber']
const pending = require('../../utils/pendingJobs')
const { pollJob } = require('../../utils/jobPolling')

// Fourth step is "已保存" so the wording never implies a food-warehouse import.
const MEALS = [
  { key: 'breakfast', label: '早餐' },
  { key: 'lunch', label: '午餐' },
  { key: 'dinner', label: '晚餐' },
  { key: 'snack', label: '加餐' }
]
const MEAL_LABELS = MEALS.map(item => item.label)

/** Business timezone (UTC+8) default; the user can always change it. */
function defaultMealIndex() {
  const hour = new Date(Date.now() + 8 * 3600 * 1000).getUTCHours()
  if (hour >= 5 && hour < 10) return 0
  if (hour >= 10 && hour < 15) return 1
  if (hour >= 17 && hour < 21) return 2
  return 3
}

Page({
  data: {
    image:'', imageName:'', loading:false, result:null, form:null, editing:false,
    dirty:false, saving:false, state:'idle', error:'', step:1, showDetails:false, detailsHeight:'0px',
    rangeBar:{bandLeft:0,bandWidth:0,pointPct:0},
    jobStatus:'', workerOnline:null, cloudMode:api.isCloud(), mediaAsset:null, jobId:null,
    mealLabels: MEAL_LABELS, mealIndex: defaultMealIndex(), mealConfirmed: false,
    needsConfirmation: false, confirmHint: '', savedRecord: null
  },
  buildRangeBar(low, high, point){
    const lo=Math.max(0,Number(low)||0)
    const hi=Math.max(lo,Number(high)||0)
    const p=Math.max(0,Number(point)||0)
    const span=Math.max(hi,p)*1.35||1
    const pct=v=>Math.max(0,Math.min(100,Math.round(v/span*1000)/10))
    return {bandLeft:pct(lo),bandWidth:Math.max(1.5,pct(hi)-pct(lo)),pointPct:pct(p)}
  },
  async onLoad(query){
    this._unloaded=false
    try {
      await ensureLogin()
      if (query && query.manual) {
        // "手动记录" from an unreadable photo goes straight to the ledger instead
        // of inventing nutrition values (spec §6.5).
        wx.redirectTo({ url: '/pages/records/diet' })
        return
      }
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
      const gate=this.correctionGate(r)
      this.setData({result:r,form,state:'success',step:2,jobStatus:'完成',rangeBar:this.buildRangeBar(r.calorie_range_low,r.calorie_range_high,form.calories),needsConfirmation:gate.needed,confirmHint:gate.hint,editing:gate.needed})
    }catch(e){
      if(this._unloaded)return
      this.setData({state:'error',error:e.message||'识别失败'})
      wx.showModal({title:'识别未完成',content:(e.message||'请稍后重试')+'。可点“手动记录”自行填写。',showCancel:false})
    }finally{if(!this._unloaded)this.setData({loading:false})}
  },
  /**
   * Low confidence, a missing scale reference or explicit uncertainty must open
   * the correction step instead of letting the user save an unverified estimate
   * (spec §6.5). No fabricated nutrition value is ever generated here.
   */
  correctionGate(r){
    const reasons=[]
    const confidence=Number(r.confidence)
    if(Number.isFinite(confidence)&&confidence<0.6)reasons.push('模型置信度偏低')
    if(!r.portion_basis||/缺少比例尺|无比例尺|无法判断份量/.test(String(r.portion_basis)))reasons.push('缺少比例尺，份量只能粗略估计')
    if(Array.isArray(r.uncertainty_reasons))reasons.push(...r.uncertainty_reasons.slice(0,2))
    if(!Number(r.calories))reasons.push('没有获得可信的热量估算')
    return {
      needed: reasons.length>0,
      hint: reasons.length ? reasons.join('；')+'。请先校正菜名、份量与热量再保存。' : ''
    }
  },
  onMealChange(e){
    const index=Number(e.detail.value)||0
    this.setData({mealIndex:index,mealConfirmed:true})
  },
  mealKey(){ return MEALS[this.data.mealIndex].key },
  mealLabel(){ return MEALS[this.data.mealIndex].label },
  manualRecord(){ wx.redirectTo({url:'/pages/records/diet'}) },
  scanAnother(){
    pending.forget('food')
    this.setData({image:'',imageName:'',result:null,form:null,editing:false,dirty:false,state:'ready',error:'',step:1,jobStatus:'',mediaAsset:null,jobId:null,savedRecord:null,needsConfirmation:false,confirmHint:'',mealConfirmed:false})
  },
  viewRecord(){
    const record=this.data.savedRecord
    if(!record)return
    wx.redirectTo({url:`/pages/records/diet?meal_type=${record.meal_type}&date=${record.date||''}`})
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
  /**
   * Confirm the corrected draft into a diet record (spec §6.4/§6.5).
   *
   * The meal is sent as an explicit choice; the response carries the saved
   * record snapshot, so the page shows exactly what was written and links to it
   * instead of bouncing to the dashboard after a fixed delay.
   */
  async save(){
    const r=this.data.result
    if(!r||this.data.saving)return
    if(this.data.needsConfirmation&&!this.data.editing){
      wx.showModal({title:'请先确认估算',content:this.data.confirmHint||'请先校正菜名、份量与热量。',showCancel:false})
      this.setData({editing:true})
      return
    }
    this.setData({saving:true,error:'',step:Math.max(this.data.step,3)})
    try{
      if(this.data.dirty){const ok=await this.applyCorrection();if(!ok)return}
      const body={
        meal_type:this.mealKey(),
        confirmed:true
      }
      const saved=await api.postIdempotent(
        `/vision/food-analysis/${r.analysis_id}/finalize`,
        body,
        `food-finalize-${r.analysis_id}`
      )
      pending.forget('food')
      const record=saved&&saved.record?saved.record:null
      this.setData({
        state:'saved',
        step:4,
        saving:false,
        savedRecord: record ? {
          id: record.id,
          name: record.name,
          calories: record.calories,
          meal_type: record.meal_type,
          meal_label: this.mealLabel(),
          date: (record.recorded_at||'').slice(0,10)
        } : {name:this.data.form.dish_name,calories:this.data.form.calories,meal_type:this.mealKey(),meal_label:this.mealLabel(),date:''},
        jobStatus: saved&&saved.already_finalized?'此前已保存':'已保存'
      })
      wx.showToast({title: saved&&saved.already_finalized?'该餐此前已保存':'已保存'})
    }catch(e){
      this.setData({state:'error',error:e.message||'保存失败',saving:false})
      wx.showModal({title:'保存失败',content:e.message||'请稍后再试',showCancel:false})
      return
    }
    this.setData({saving:false})
  },
  retry(){this.analyze()}
})
