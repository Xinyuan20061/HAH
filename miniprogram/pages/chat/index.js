const api=require('../../utils/request');

const SPECIALIST_NAMES={planner:'计划规划',coach:'运动指导',nutritionist:'营养建议',safety_guardian:'安全守护',safety:'安全守护'};
function presentTrace(trace){
  if(!trace||!trace.specialist)return null;
  const reasons=[];
  ;(trace.adjustment_reasons||[]).forEach(x=>{if(x&&x.label)reasons.push(x.label)});
  ;(trace.coaching_focus||[]).forEach(x=>{if(x&&x.label)reasons.push(x.label)});
  ;(trace.plan_guardrail_changes||[]).forEach(x=>{if(x)reasons.push(x)});
  return {specialistLabel:SPECIALIST_NAMES[trace.specialist]||'综合建议',reasons:[...new Set(reasons)].slice(0,4),hasReasons:reasons.length>0};
}
Page({
  data:{input:'',sending:false,sessionId:null,provider:'准备就绪',streaming:false,messages:[{role:'assistant',content:'你好，我是 HealthMate 健康助手。你可以问记录、饮食、运动，也可以让我制定一份由你确认的本周计划。'}]},
  onInput(e){this.setData({input:e.detail.value})},
  onShow(){const prompt=wx.getStorageSync('healthmate_insight_prompt');if(prompt){wx.removeStorageSync('healthmate_insight_prompt');this.setData({input:prompt})}},
  quick(e){this.setData({input:e.currentTarget.dataset.q});this.send()},
  settings(){wx.navigateTo({url:'/pages/settings/ai/index'})},
  scrollBottom(){this.setData({scrollId:'bottom'})},
  name(p){return {'deepseek-user':'我的 DeepSeek','deepseek-system':'系统 DeepSeek','demo':'演示模式','demo-safety':'安全响应','fallback':'基础回答','rules-fallback':'基础建议','safety-rule':'安全提醒'}[p]||p||'健康助手'},
  shouldAgent(q){return /计划|安排|本周|这周|减脂|增肌|练三天|训练三天|怎么练|深蹲|俯卧撑|伏地挺身|弓步|箭步|胸部|背部|肩部|手臂|核心|股四头肌|臀部|腘绳肌|练胸|练背|每周运动|运动多久|运动指南|膳食|营养|怎么吃|慢病|高血压|糖尿病|squat|pushup|lunge|喝水|饮水|睡眠|睡觉|失眠|腰酸|久坐|坐着|体重|减肥|血脂|血压|跑步|散步|快走|运动|锻炼|饮食|吃饭|早餐|午餐|晚餐|盐|油|糖|脂肪|卡路里|热量|深蹲|大腿|膝盖|拉伸|热身|步数|走多少|吃多少|喝多少|合适|注意|怎么办|可以吗|好不好|怎么减|怎么增/i.test(q)},
  async sendAgent(q,messages){
    try{const r=await api.postLong('/agent/respond',{message:q});const last=messages.length-1;messages[last].content=r.reply||'已完成分析。';messages[last].agentPlan=r.plan||null;messages[last].planAdjustment=r.plan_adjustment||null;messages[last].resources=r.resources||[];messages[last].knowledgeSources=r.knowledge_sources||[];messages[last].exerciseRecommendations=(r.exercise_recommendations&&r.exercise_recommendations.items)||[];messages[last].trace=presentTrace(r.trace);messages[last].runId=r.run_id;messages[last].applied=false;messages[last].safetyLevel=r.safety_level||'normal';this.setData({messages,provider:this.name(r.provider),sending:false,streaming:false});this.scrollBottom()}
    catch(e){messages[messages.length-1].content='Health Agent 暂时不可用，请检查后端或 DeepSeek 配置。';this.setData({messages,sending:false,streaming:false});this.scrollBottom()}
  },
  async applyAgentPlan(e){const runId=Number(e.currentTarget.dataset.run);if(!runId)return;try{const r=await api.post(`/agent/runs/${runId}/apply-plan`,{});const messages=this.data.messages.map(m=>m.runId===runId?Object.assign({},m,{applied:true,actionAuditId:r.action_audit_id||null}):m);this.setData({messages});wx.showToast({title:r.already_applied?'计划已在本周':'已加入本周计划'});setTimeout(()=>wx.switchTab({url:'/pages/plan/index'}),450)}catch(err){wx.showToast({title:err.message||'加入计划失败',icon:'none'})}},
  copyResource(e){const url=e.currentTarget.dataset.url;if(!url)return;wx.setClipboardData({data:url,success:()=>wx.showToast({title:'教学链接已复制'})})},
  copyKnowledge(e){const url=e.currentTarget.dataset.url;if(!url)return;wx.setClipboardData({data:url,success:()=>wx.showToast({title:'来源链接已复制'})})},
  async fallback(q,messages){try{let r=await api.post('/chat',{message:q,session_id:this.data.sessionId});messages[messages.length-1].content=r.reply;this.setData({messages,sessionId:r.session_id,provider:this.name(r.provider),sending:false,streaming:false});this.scrollBottom()}catch(e){messages[messages.length-1].content='服务暂时没有连接成功。请检查后端或 DeepSeek 配置；饮食、运动和健康打卡仍可正常使用。';this.setData({messages,sending:false,streaming:false})}},
  send(){let q=this.data.input.trim();if(!q||this.data.sending)return;let messages=[...this.data.messages,{role:'user',content:q},{role:'assistant',content:''}];this.setData({messages,input:'',sending:true,streaming:!this.shouldAgent(q)});this.scrollBottom();if(this.shouldAgent(q)){this.sendAgent(q,messages);return}let failed=false;api.streamPost('/chat/stream',{message:q,session_id:this.data.sessionId},{onMeta:(x)=>this.setData({sessionId:x.session_id}),onDelta:(chunk)=>{messages[messages.length-1].content+=chunk;this.setData({messages});this.scrollBottom()},onDone:(x)=>{this.setData({provider:this.name(x.provider),sending:false,streaming:false});this.scrollBottom()},onError:(e)=>{if(failed)return;failed=true;if(!messages[messages.length-1].content)this.fallback(q,messages);else{this.setData({sending:false,streaming:false})}}})}
})
