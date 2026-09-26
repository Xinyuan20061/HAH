const api=require('../../utils/request'); const {ensureLogin}=require('../../utils/auth')
// 松手速度阈值（步/秒）：超过即视为「甩动」，触发惯性补格
const SNAP_VELOCITY = 6
Page({
  data:{loading:true,saving:false,dynamicLoading:false,explainLoading:false,dynamic:null,dynamicExplain:'',goals:{water_target:1800,sleep_target:8,exercise_target:30,steps_target:8000,protein_target:90,calorie_target:2000,weekly_checkin_target:5},summary:{},cards:[],snapKey:'',checkinSnap:false},
  onShow(){this.load()},
  pct(a,b){return Math.min(100,Math.round((Number(a)||0)/(Number(b)||1)*100))},
  async load(){
    this.setData({loading:true})
    try{await ensureLogin(); const [g,s]=await Promise.all([api.get('/health/goals'),api.get('/health/today')]);this.setData({goals:g,summary:s}); this.buildCards()}
    catch(e){wx.showToast({title:e.message||'目标加载失败',icon:'none'})}
    finally{this.setData({loading:false})}
  },
  buildCards(){const g=this.data.goals,s=this.data.summary;this.setData({cards:[
    {k:'water_target',name:'每日饮水',value:g.water_target,unit:'ml',current:s.water_ml||0,pct:this.pct(s.water_ml,g.water_target),min:800,max:4000,step:100,icon:'≈'},
    {k:'sleep_target',name:'睡眠时长',value:g.sleep_target,unit:'h',current:s.sleep_hours||0,pct:this.pct(s.sleep_hours,g.sleep_target),min:5,max:10,step:.5,icon:'◐'},
    {k:'exercise_target',name:'每日运动',value:g.exercise_target,unit:'min',current:s.exercise_min||0,pct:this.pct(s.exercise_min,g.exercise_target),min:10,max:120,step:5,icon:'↗'},
    {k:'steps_target',name:'每日步数',value:g.steps_target,unit:'步',current:s.steps||0,pct:this.pct(s.steps,g.steps_target),min:2000,max:20000,step:500,icon:'⌁'},
    {k:'protein_target',name:'蛋白质',value:g.protein_target,unit:'g',current:s.protein||0,pct:this.pct(s.protein,g.protein_target),min:30,max:200,step:5,icon:'P'},
    {k:'calorie_target',name:'热量参考',value:g.calorie_target,unit:'kcal',current:s.calories||0,pct:this.pct(s.calories,g.calorie_target),min:1200,max:3600,step:50,icon:'C'}
  ]})},
  // ── Velocity-Based Slider Snap ──────────────────────────────────────────
  // 原生 slider 不暴露松手速度，用 bindchanging 的连续采样估算「步/秒」；
  // 松手时若还在快速滑动，就往同方向多走一格，让「甩一下」能跨过一格。
  slideLive(e){
    const i=Number(e.currentTarget.dataset.i),card=this.data.cards[i]||{}
    const step=Number(card.step)||1,v=Number(e.detail.value),now=Date.now(),p=this._slideSample
    if(p&&p.i===i&&now>p.t){const dt=(now-p.t)/1000;if(dt>0)this._slideV=(v-p.v)/step/dt}
    this._slideSample={i,v,t:now}
  },
  consumeVelocity(){const v=Number(this._slideV)||0;this._slideSample=null;this._slideV=0;return v},
  snapValue(v,step,min,max,vel){
    if(!vel||Math.abs(vel)<SNAP_VELOCITY)return{v,snapped:false}
    const next=Number((v+(vel>0?step:-step)).toFixed(4))
    if(Number.isFinite(min)&&next<min)return{v,snapped:false}
    if(Number.isFinite(max)&&next>max)return{v,snapped:false}
    return{v:next,snapped:true}
  },
  markSnap(key){
    // 用布尔字段而不是把字符串写进 class 表达式 —— 后者会被类名审计误判为类名
    this.setData(key==='weekly_checkin_target'?{checkinSnap:true}:{snapKey:key})
    clearTimeout(this._snapTimer)
    this._snapTimer=setTimeout(()=>this.setData({snapKey:'',checkinSnap:false}),380)
  },
  slide(e){
    const i=Number(e.currentTarget.dataset.i),card=this.data.cards[i]
    const step=Number(card.step)||1
    const {v,snapped}=this.snapValue(Number(e.detail.value),step,Number(card.min),Number(card.max),this.consumeVelocity())
    this.setData({[`goals.${card.k}`]:v,[`cards[${i}].value`]:v,[`cards[${i}].pct`]:this.pct(card.current,v)})
    if(snapped)this.markSnap(card.k)
  },
  checkinLive(e){
    const v=Number(e.detail.value),now=Date.now(),p=this._slideSample
    if(p&&now>p.t){const dt=(now-p.t)/1000;if(dt>0)this._slideV=(v-p.v)/dt}
    this._slideSample={i:-1,v,t:now}
  },
  checkinTarget(e){
    const {v,snapped}=this.snapValue(Number(e.detail.value),1,1,7,this.consumeVelocity())
    this.setData({'goals.weekly_checkin_target':v})
    if(snapped)this.markSnap('weekly_checkin_target')
  },
  async save(){if(this.data.saving)return;this.setData({saving:true});try{await api.put('/health/goals',this.data.goals);wx.showToast({title:'健康目标已保存'});this.buildCards()}catch(e){wx.showToast({title:e.message||'保存失败',icon:'none'})}finally{this.setData({saving:false})}},
  async evaluateDynamic(){if(this.data.dynamicLoading)return;this.setData({dynamicLoading:true,dynamicExplain:''});try{const dynamic=await api.postLong('/health/goals/dynamic/evaluate?window_days=14',{});dynamic.items=(dynamic.items||[]).map(x=>Object.assign({},x,{has_rate:x.completion_rate!==null&&x.completion_rate!==undefined,completion_pct:x.completion_rate===null?null:Math.round(x.completion_rate*100),decision_label:x.decision==='increase'?'建议提高':x.decision==='reduce'?'建议降低':x.decision==='insufficient_data'?'继续记录':'保持'}));this.setData({dynamic})}catch(e){wx.showToast({title:e.message||'评估失败',icon:'none'})}finally{this.setData({dynamicLoading:false})}},
  async explainDynamic(){if(this.data.explainLoading)return;this.setData({explainLoading:true});try{const r=await api.postLong('/health/goals/dynamic/explain',{});this.setData({dynamicExplain:r.summary||''})}catch(e){wx.showToast({title:'暂时无法说明',icon:'none'})}finally{this.setData({explainLoading:false})}},
  async applyDynamic(e){const id=Number(e.currentTarget.dataset.id);if(!id)return;try{const r=await api.post(`/health/goals/dynamic/${id}/apply`,{});this.setData({goals:r.goals||this.data.goals});this.buildCards();wx.showToast({title:'已应用新目标'});this.evaluateDynamic()}catch(err){wx.showToast({title:err.message||'应用失败',icon:'none'})}}
})
