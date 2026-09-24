function init(page, selector, key, options){
  const q=wx.createSelectorQuery().in(page)
  q.select(selector).fields({node:true,size:true}).exec(res=>{
    const item=res&&res[0]; if(!item||!item.node)return
    const dpr=(wx.getWindowInfo?wx.getWindowInfo().pixelRatio:wx.getSystemInfoSync().pixelRatio)||1
    const canvas=item.node,ctx=canvas.getContext('2d')
    canvas.width=item.width*dpr;canvas.height=item.height*dpr;ctx.scale(dpr,dpr)
    page._charts=page._charts||{};page._charts[key]={canvas,ctx,width:item.width,height:item.height,options:null,points:[]}
    draw(page,key,options)
  })
}
function roundedRect(ctx,x,y,w,h,r){ctx.beginPath();ctx.moveTo(x+r,y);ctx.arcTo(x+w,y,x+w,y+h,r);ctx.arcTo(x+w,y+h,x,y+h,r);ctx.arcTo(x,y+h,x,y,r);ctx.arcTo(x,y,x+w,y,r);ctx.closePath()}
function isMissing(v){return v===null||v===undefined||v===''||Number.isNaN(Number(v))}
function draw(page,key,options,activeIndex=-1){
  const c=page._charts&&page._charts[key];if(!c)return
  c.options=options;const {ctx,width:w,height:h}=c;ctx.clearRect(0,0,w,h)
  const data=options.data||[],pad={l:40,r:18,t:22,b:38};const cw=w-pad.l-pad.r,ch=h-pad.t-pad.b
  const vals=data.filter(x=>!isMissing(x.value)).map(x=>Number(x.value));let max=Math.max(...vals,Number(options.max)||0,1);let min=Number(options.min)||0
  if(max===min)max=min+1; max*=1.08
  ctx.font='11px sans-serif';ctx.textAlign='right';ctx.textBaseline='middle';ctx.fillStyle='#94a09b';ctx.strokeStyle='#e8eeeb';ctx.lineWidth=1
  for(let i=0;i<=4;i++){const y=pad.t+ch*i/4;ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(w-pad.r,y);ctx.stroke();const v=max-(max-min)*i/4;ctx.fillText(formatTick(v,options),pad.l-8,y)}
  const points=data.map((d,i)=>{const missing=isMissing(d.value),value=missing?null:Number(d.value);return {x:pad.l+(data.length<=1?cw/2:cw*i/(data.length-1)),y:missing?null:pad.t+ch-(value-min)/(max-min)*ch,label:d.label,value,index:i,missing}})
  c.points=points
  let segment=[]
  function paintSegment(seg){if(!seg.length)return;if(seg.length>1){const g=ctx.createLinearGradient(0,pad.t,0,pad.t+ch);g.addColorStop(0,'rgba(44,112,89,.18)');g.addColorStop(1,'rgba(44,112,89,0)');ctx.beginPath();ctx.moveTo(seg[0].x,pad.t+ch);seg.forEach(p=>ctx.lineTo(p.x,p.y));ctx.lineTo(seg[seg.length-1].x,pad.t+ch);ctx.closePath();ctx.fillStyle=g;ctx.fill()}ctx.beginPath();seg.forEach((p,i)=>i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));ctx.strokeStyle='#2c7059';ctx.lineWidth=2.5;ctx.lineJoin='round';ctx.lineCap='round';ctx.stroke()}
  points.forEach((p,i)=>{if(p.missing){paintSegment(segment);segment=[]}else segment.push(p);if(i===points.length-1)paintSegment(segment)})
  ctx.textAlign='center';ctx.textBaseline='top';ctx.font='11px sans-serif';ctx.fillStyle='#84918b';points.forEach(p=>ctx.fillText(p.label,p.x,pad.t+ch+12))
  points.forEach((p,i)=>{if(p.missing){ctx.beginPath();ctx.arc(p.x,pad.t+ch,2.6,0,Math.PI*2);ctx.fillStyle='#c8d1cd';ctx.fill();return}ctx.beginPath();ctx.arc(p.x,p.y,i===activeIndex?5:3.5,0,Math.PI*2);ctx.fillStyle=i===activeIndex?'#d6ff7f':'#2c7059';ctx.fill();ctx.lineWidth=2;ctx.strokeStyle='#fff';ctx.stroke()})
  if(activeIndex>=0&&points[activeIndex]&&!points[activeIndex].missing){
    const p=points[activeIndex];ctx.setLineDash([4,4]);ctx.strokeStyle='#87a49a';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(p.x,pad.t);ctx.lineTo(p.x,pad.t+ch);ctx.stroke();ctx.setLineDash([])
    const txt=`${p.label}  ${formatValue(p.value,options)}`;ctx.font='12px sans-serif';const tw=ctx.measureText(txt).width+24;let tx=Math.max(6,Math.min(w-tw-6,p.x-tw/2)),ty=Math.max(4,p.y-42)
    roundedRect(ctx,tx,ty,tw,30,10);ctx.fillStyle='#173f34';ctx.fill();ctx.fillStyle='#fff';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(txt,tx+tw/2,ty+15)
  }
}
function formatTick(v,o){if(o&&o.tickFormatter)return o.tickFormatter(v);return v>=1000?Math.round(v/1000)+'k':Math.round(v)}
function formatValue(v,o){const suffix=o&&o.suffix||'';const decimals=o&&o.decimals||0;return Number(v).toFixed(decimals)+suffix}
function touch(page,key,e){const c=page._charts&&page._charts[key];if(!c||!c.points.length)return -1;const t=e.touches&&e.touches[0]||e.changedTouches&&e.changedTouches[0];if(!t)return -1;const x=Number(t.x!=null?t.x:t.clientX);let best=-1,dist=Infinity;c.points.forEach((p,i)=>{if(p.missing)return;const d=Math.abs(p.x-x);if(d<dist){dist=d;best=i}});if(best>=0)draw(page,key,c.options,best);return best}
module.exports={init,draw,touch}
