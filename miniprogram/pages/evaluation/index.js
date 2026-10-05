const api=require('../../utils/request')
const {formatAgentStats}=require('../../utils/evaluationPresentation')
// Spec §6: any externally shown score must carry dataset, date, model/retriever
// version and evidence level; dev-only metrics must never look like shipped ability.
const EVIDENCE_LEVEL_LABEL={
  synthetic_replay:'合成回放','real_device':'真机','local_test':'本地测试',
  '真人试用':'真人试用','':'未标注证据等级'
}
Page({data:{loading:true,error:'',dash:null,metrics:[],benchmarks:[],agent:null},onShow(){this.load()},async load(){this.setData({loading:true,error:''});try{await api.ensureToken();const [d,agentRaw]=await Promise.all([api.get('/evaluation/dashboard?days=30',{allowCache:false}),api.get('/agent/stats?days=30',{allowCache:false}).catch(()=>null)]);const metrics=(d.runtime_metrics||[]).map(x=>({...x,display:x.value===null||x.value===undefined?'暂无样本':`${x.value}${x.unit||''}`}));const benchmarks=(d.benchmarks||[]).map(b=>({...b,evidenceLabel:EVIDENCE_LEVEL_LABEL[b.evidence_level]||b.evidence_level||'未标注证据等级',measuredDate:(b.measured_at||'').slice(0,10)}));this.setData({dash:d,metrics,benchmarks,agent:formatAgentStats(agentRaw)})}catch(e){this.setData({error:e.message||'评测数据加载失败'})}finally{this.setData({loading:false})}},retry(){this.load()}})
