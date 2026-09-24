const api=require('../../utils/request'); const {ensureLogin}=require('../../utils/auth')
Page({
  data:{loading:true,saving:false,dynamicLoading:false,explainLoading:false,dynamic:null,dynamicExplain:'',goals:{water_target:1800,sleep_target:8,exercise_target:30,steps_target:8000,protein_target:90,calorie_target:2000,weekly_checkin_target:5},summary:{},cards:[]},
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
  slide(e){const i=Number(e.currentTarget.dataset.i),card=this.data.cards[i],v=Number(e.detail.value);this.setData({[`goals.${card.k}`]:v,[`cards[${i}].value`]:v,[`cards[${i}].pct`]:this.pct(card.current,v)})},
  checkinTarget(e){this.setData({'goals.weekly_checkin_target':Number(e.detail.value)})},
  async save(){if(this.data.saving)return;this.setData({saving:true});try{await api.put('/health/goals',this.data.goals);wx.showToast({title:'健康目标已保存'});this.buildCards()}catch(e){wx.showToast({title:e.message||'保存失败',icon:'none'})}finally{this.setData({saving:false})}},
  async evaluateDynamic(){if(this.data.dynamicLoading)return;this.setData({dynamicLoading:true,dynamicExplain:''});try{const dynamic=await api.postLong('/health/goals/dynamic/evaluate?window_days=14',{});dynamic.items=(dynamic.items||[]).map(x=>Object.assign({},x,{has_rate:x.completion_rate!==null&&x.completion_rate!==undefined,completion_pct:x.completion_rate===null?null:Math.round(x.completion_rate*100),decision_label:x.decision==='increase'?'建议提高':x.decision==='reduce'?'建议降低':x.decision==='insufficient_data'?'继续记录':'保持'}));this.setData({dynamic})}catch(e){wx.showToast({title:e.message||'评估失败',icon:'none'})}finally{this.setData({dynamicLoading:false})}},
  async explainDynamic(){if(this.data.explainLoading)return;this.setData({explainLoading:true});try{const r=await api.postLong('/health/goals/dynamic/explain',{});this.setData({dynamicExplain:r.summary||''})}catch(e){wx.showToast({title:'暂时无法说明',icon:'none'})}finally{this.setData({explainLoading:false})}},
  async applyDynamic(e){const id=Number(e.currentTarget.dataset.id);if(!id)return;try{const r=await api.post(`/health/goals/dynamic/${id}/apply`,{});this.setData({goals:r.goals||this.data.goals});this.buildCards();wx.showToast({title:'已应用新目标'});this.evaluateDynamic()}catch(err){wx.showToast({title:err.message||'应用失败',icon:'none'})}}
})
