// ---- AI聊天页 ----
let chatModel='deepseek-v4-flash';
let chatModelList=[];
async function loadModelList(){try{const r=await fetch(API_BASE+'/models',{signal:AbortSignal.timeout(5000)});if(r.ok){const d=await r.json();chatModelList=d.models||[];if(d.default)chatModel=localStorage.getItem('chatModel')||d.default}}catch{chatModelList=[{id:'deepseek-v4-flash',name:'DeepSeek V4',provider:'deepseek'}]}}
function renderChat(){currentPage='chat';renderNav();const sugs=['现在适合入场吗？','什么时候该卖出？','智能定投怎么投？','最近有什么新闻？','政策对我持仓有啥影响？','关税贸易战利好利空啥？','技术指标怎么样？','宏观经济怎么样？','今天市场怎么样？','黄金还能买吗？'];
const modelSelector=chatModelList.length>0?`<select id="modelSelect" onchange="chatModel=this.value;localStorage.setItem('chatModel',this.value)" style="background:var(--bg2);color:var(--text);border:1px solid var(--bg3);border-radius:8px;padding:4px 8px;font-size:11px;margin-left:8px">${chatModelList.map(m=>`<option value="${m.id}" ${m.id===chatModel?'selected':''}>${m.name}</option>`).join('')}</select>`:'';
$('#app').innerHTML=`<div class="chat-page"><div class="chat-header"><h2>🤖 AI理财分析师</h2><p>${API_AVAILABLE?'连接实时数据分析':'后端离线，部分功能受限'}${modelSelector}</p></div><div class="chat-messages" id="chatMsgs"><div class="chat-msg bot">你好！我是钱袋子AI分析师，内置巴菲特、格雷厄姆、林奇、塔勒布四位大师的投资框架 🧠\n\n问我关于市场行情、持仓、买卖建议等问题。\n\n所有建议仅供参考 😊<div class="src-tag">系统</div></div>${chatMessages.map(m=>`<div class="chat-msg ${m.role}">${m.text}${m.src?`<div class="src-tag">${m.src==='ai'?'AI分析':'规则引擎'}</div>`:''}</div>`).join('')}</div><div class="chat-suggestions" id="chatSugs">${sugs.map(s=>`<button class="chat-suggest-btn" onclick="sendChat('${s}')">${s}</button>`).join('')}</div><div class="chat-input-bar"><input class="chat-input" id="chatIn" placeholder="问点什么..." onkeydown="if(event.key==='Enter')sendChat()"><button class="chat-send" onclick="sendChat()">→</button></div></div>`;scrollChat()}

let _chatSending=false;
function _setChatLock(locked){
_chatSending=locked;
const inp=document.getElementById('chatIn');const btn=document.querySelector('.chat-send');const sugs=document.querySelectorAll('.chat-suggest-btn');
if(inp){inp.disabled=locked;inp.placeholder=locked?'AI 正在思考中...':'问点什么...'}
if(btn){btn.disabled=locked;btn.style.opacity=locked?'0.4':'1';btn.style.pointerEvents=locked?'none':'auto'}
sugs.forEach(s=>{s.disabled=locked;s.style.opacity=locked?'0.4':'1';s.style.pointerEvents=locked?'none':'auto'})}
async function sendChat(text){if(_chatSending)return;const inp=document.getElementById('chatIn');const msg=text||(inp?inp.value.trim():'');if(!msg)return;_setChatLock(true);if(inp)inp.value='';
const sg=document.getElementById('chatSugs');if(sg)sg.style.display='none';
chatMessages.push({role:'user',text:msg});appendMsg('user',msg);appendTyping();
// 思考进度计时
const isR1=chatModel.includes('reasoner');
let _thinkSec=0;_thinkTimer=setInterval(()=>{_thinkSec++;const el=document.getElementById('chatTyp');if(!el)return;const tips=isR1?['🧠 深度推理模型思考中...','💭 正在多角度分析...','📊 综合大师观点中...','⏳ R1 深度思考需要 15-30 秒，请耐心等待']:['🤖 AI 分析中...','📊 查询实时数据...','💭 综合分析中...'];const tip=tips[Math.min(Math.floor(_thinkSec/5),tips.length-1)];el.innerHTML=`<span></span><span></span><span></span><div style="font-size:11px;color:var(--text2);margin-top:4px">${tip}（${_thinkSec}s）</div>`},1000);
if(API_AVAILABLE){try{const p=loadPortfolio();
const r=await fetch(API_BASE+'/chat/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:msg,model:chatModel,portfolio:p.holdings.length?p:null,userId:getProfileId()})});
rmTyping();
if(r.ok&&r.body){
// SSE 流式逐字渲染
const el=document.getElementById('chatMsgs');if(!el)return;
const botDiv=document.createElement('div');botDiv.className='chat-msg bot';botDiv.innerHTML='<span class="stream-cursor">▊</span>';el.appendChild(botDiv);scrollChat();
let fullText='',source='ai',thinkText='',_r1Thinking=false;
const reader=r.body.getReader();const dec=new TextDecoder();let buf='';
while(true){const{done,value}=await reader.read();if(done)break;
buf+=dec.decode(value,{stream:true});
const lines=buf.split('\n');buf=lines.pop()||'';
for(const line of lines){if(!line.startsWith('data: '))continue;
try{const d=JSON.parse(line.slice(6));if(d.source)source=d.source;
if(d.delta){
// R1: phase=thinking 是思考过程，phase=answering 是正式回答
if(d.phase==='thinking'){
if(!_r1Thinking){_r1Thinking=true;thinkText=''}
thinkText+=d.delta;
botDiv.innerHTML=`<div style="font-size:11px;color:var(--text2);opacity:0.7;border-left:2px solid var(--bg3);padding-left:8px;margin-bottom:8px">🧠 思考中...\n${thinkText}</div><span class="stream-cursor">▊</span>`;scrollChat()
}else{
if(_r1Thinking){_r1Thinking=false;fullText=''}
fullText+=d.delta;
const thinkBlock=thinkText?`<details style="font-size:11px;color:var(--text2);margin-bottom:8px;border:1px solid var(--bg3);border-radius:8px;padding:6px 8px"><summary style="cursor:pointer;opacity:0.7">🧠 查看思考过程</summary><div style="margin-top:4px;white-space:pre-wrap;opacity:0.6">${thinkText}</div></details>`:'';
botDiv.innerHTML=thinkBlock+fullText+'<span class="stream-cursor">▊</span>';scrollChat()
}}
if(d.done){
const thinkBlock=thinkText?`<details style="font-size:11px;color:var(--text2);margin-bottom:8px;border:1px solid var(--bg3);border-radius:8px;padding:6px 8px"><summary style="cursor:pointer;opacity:0.7">🧠 查看思考过程</summary><div style="margin-top:4px;white-space:pre-wrap;opacity:0.6">${thinkText}</div></details>`:'';
botDiv.innerHTML=thinkBlock+fullText+`<div class="src-tag">${source==='ai'?'AI分析':'规则引擎'}</div>`;scrollChat()}}catch{}}}
// 处理剩余 buffer
if(buf.startsWith('data: ')){try{const d=JSON.parse(buf.slice(6));if(d.delta){if(d.phase==='thinking')thinkText+=d.delta;else fullText+=d.delta}if(d.source)source=d.source}catch{}}
botDiv.innerHTML=fullText+`<div class="src-tag">${source==='ai'?'AI分析':'规则引擎'}</div>`;scrollChat();
chatMessages.push({role:'bot',text:fullText,src:source});_saveChatHistory();_setChatLock(false);return}
// 非流式降级（旧接口兼容）
if(r.ok){const d=await r.json();chatMessages.push({role:'bot',text:d.reply,src:d.source});_saveChatHistory();appendMsg('bot',d.reply,d.source);_setChatLock(false);return}
}catch(e){console.warn('Chat stream error:',e)}}
rmTyping();const fb='后端未连接，无法获取实时数据。请确保后端运行中。';chatMessages.push({role:'bot',text:fb,src:'offline'});_saveChatHistory();appendMsg('bot',fb,'offline');_setChatLock(false)}

function appendMsg(r,t,src){const el=document.getElementById('chatMsgs');if(!el)return;const d=document.createElement('div');d.className='chat-msg '+r;d.innerHTML=t+(src?`<div class="src-tag">${src==='ai'?'AI分析':src==='rules'?'规则引擎':'离线'}</div>`:'');el.appendChild(d);scrollChat()}
let _thinkTimer=null;
function appendTyping(){const el=document.getElementById('chatMsgs');if(!el)return;const d=document.createElement('div');d.className='chat-typing';d.id='chatTyp';d.innerHTML='<span></span><span></span><span></span>';el.appendChild(d);scrollChat()}
function rmTyping(){if(_thinkTimer){clearInterval(_thinkTimer);_thinkTimer=null}const el=document.getElementById('chatTyp');if(el)el.remove()}
function scrollChat(){const el=document.getElementById('chatMsgs');if(el)setTimeout(()=>el.scrollTop=el.scrollHeight,50)}

// ---- 白话解释弹窗（数据驱动，避免 onclick 引号冲突）----
const _EXPLAINS={};
function setExplain(key,title,text,extraData){_EXPLAINS[key]={title,text,extraData:extraData||null}}
function showExplain(key){
const entry=_EXPLAINS[key];if(!entry)return;
const {title,text,extraData}=entry;
const overlay=document.createElement('div');
overlay.style.cssText='position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.6);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px;animation:fadeIn 0.2s';
overlay.onclick=e=>{if(e.target===overlay)overlay.remove()};
const lines=text.split('\n').map(l=>l.trim()?`<p style="margin:4px 0;${l.startsWith('📊')||l.startsWith('🔍')||l.startsWith('🎯')||l.startsWith('💡')||l.startsWith('⚠️')?'font-weight:600;margin-top:12px;':''}">${l}</p>`:'').join('');
// AI 点评按钮（只在有 extraData 时显示）
const aiBtn=extraData?`<div id="aiCommentArea_${key}" style="margin-top:12px;border-top:1px solid var(--bg3);padding-top:12px">
<button onclick="loadAiComment('${key}')" style="width:100%;padding:10px;border-radius:10px;border:1px solid rgba(99,102,241,.3);background:transparent;color:#818CF8;font-size:13px;cursor:pointer;font-weight:600">🤖 AI 智能点评</button></div>`:'';
overlay.innerHTML=`<div style="background:var(--card);border-radius:16px;padding:24px;max-width:380px;width:100%;max-height:80vh;overflow-y:auto;box-shadow:0 20px 60px rgba(0,0,0,0.3)">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px"><h3 style="margin:0;font-size:16px;color:var(--text1)">${title}</h3><button onclick="this.closest('[style*=fixed]').remove()" style="background:none;border:none;font-size:20px;color:var(--text2);cursor:pointer;padding:4px">✕</button></div>
<div style="font-size:13px;line-height:1.8;color:var(--text2)">${lines}</div>
${aiBtn}
<button onclick="this.closest('[style*=fixed]').remove()" style="width:100%;margin-top:12px;padding:12px;border:none;border-radius:10px;background:var(--accent);color:#fff;font-size:14px;cursor:pointer">我懂了 👍</button>
</div>`;
document.body.appendChild(overlay)}

async function loadAiComment(key){
const entry=_EXPLAINS[key];if(!entry||!entry.extraData)return;
const {extraData}=entry;
const area=document.getElementById('aiCommentArea_'+key);if(!area)return;
area.innerHTML='<div style="text-align:center;padding:8px;color:var(--text2);font-size:12px"><div class="loading-spinner" style="width:18px;height:18px;margin:0 auto 6px;border-width:2px"></div>AI 分析中...</div>';
try{
let url='';
if(extraData.type==='stock'){
url=API_BASE+'/ai-comment/stock?code='+encodeURIComponent(extraData.code)+'&name='+encodeURIComponent(extraData.name||'')+'&score='+(extraData.score||0)+'&pe='+(extraData.pe||0)+'&roe='+(extraData.roe||0)+'&gross_margin='+(extraData.gross_margin||0);
}else{
const r=extraData.returns||{};
url=API_BASE+'/ai-comment/fund?code='+encodeURIComponent(extraData.code)+'&name='+encodeURIComponent(extraData.name||'')+'&score='+(extraData.score||0)+'&fee='+encodeURIComponent(extraData.fee||'')+(r['3m']!=null?'&r3m='+r['3m']:'')+(r['6m']!=null?'&r6m='+r['6m']:'')+(r['1y']!=null?'&r1y='+r['1y']:'')+(r['3y']!=null?'&r3y='+r['3y']:'');
}
const resp=await fetch(url,{signal:AbortSignal.timeout(20000)});
const d=await resp.json();
area.innerHTML=`<div style="padding:10px;background:rgba(99,102,241,.06);border-radius:10px;border-left:3px solid #6366F1"><div style="font-size:11px;font-weight:700;color:#818CF8;margin-bottom:4px">🤖 AI 点评</div><div style="font-size:13px;line-height:1.8;color:var(--text1)">${d.comment||'暂无点评'}</div></div>`;
}catch(e){area.innerHTML=`<div style="color:var(--text2);font-size:12px;padding:8px">AI点评加载失败: ${e.message}</div>`}}

// 预注册静态解释
setExplain('cpi','CPI 居民消费价格指数','CPI 就是"物价涨了多少"。\n\n📊 怎么理解：\n• CPI = 0% → 物价没变\n• CPI > 0% → 东西涨价了（通胀）\n• CPI < 0% → 东西降价了（通缩）\n\n🔍 对你的影响：\n• CPI 涨太快(>3%) → 央行可能加息 → 存款收益↑，股市债市承压\n• CPI 下降/为负 → 央行可能降息 → 贷款便宜，利好股市和房产\n\n💡 一句话：CPI 涨，你手里的钱在贬值；CPI 跌，你的钱更值钱了。');
setExplain('pmi','PMI 采购经理指数','PMI 就是"企业老板们觉得生意怎么样"。\n\n📊 怎么理解：\n• PMI = 50 是分水岭\n• PMI > 50 → 多数企业觉得在扩张，经济向好\n• PMI < 50 → 多数企业觉得在收缩，经济下行\n\n🔍 对你的影响：\n• PMI 连续 > 50 → 经济回暖，股市通常表现较好\n• PMI 连续 < 50 → 经济承压，投资需谨慎\n\n💡 一句话：PMI 是经济的体温计，>50 说明经济在扩张。');
setExplain('m2','M2 广义货币供应量','M2 就是"市场上钱的总量"。\n\n📊 怎么理解：\n• M2 增速高 → 央行在"放水"，市场上钱多\n• M2 增速低 → 央行在"收水"，市场上钱紧\n\n🔍 对你的影响：\n• M2 增速上升 → 钱多了要找去处，利好股市和房产\n• M2 增速下降 → 资金收紧，资产价格可能承压\n\n💡 一句话：M2 就是印钞机的速度表，转得越快，资产越容易涨。');
setExplain('ppi','PPI 工业生产者出厂价格指数','PPI 就是"工厂出货价涨了多少"。\n\n📊 怎么理解：\n• PPI > 0% → 工厂涨价了（原材料贵了）\n• PPI < 0% → 工厂降价了（需求不足）\n\n🔍 和 CPI 的关系：\n• PPI 是"上游"，CPI 是"下游"\n• PPI 涨 → 几个月后 CPI 也可能涨（成本传导）\n• PPI 领先 CPI，是通胀的"早期预警"\n\n💡 一句话：PPI 涨了，你的生活成本迟早也会涨。');

// ---- 新增：审计报告发现的 12 个缺失术语 ----
setExplain('networth','净资产','净资产就是"你总共值多少钱"。\n\n📊 计算公式：\n净资产 = 基金市值 + 其他资产（房/车/存款）- 负债（房贷/借款）+ 记账现金流（收入-支出）\n\n🔍 举个例子：\n• 基金持仓 ¥30万\n• 银行存款 ¥5万\n• 房贷 -¥100万\n• 房产 ¥200万\n→ 净资产 = 30+5-100+200 = ¥135万\n\n💡 净资产是你真正的"家底"，负债不可怕，关键是资产要大于负债。');
setExplain('hhi','HHI 集中度指数','HHI 就是"你的鸡蛋放了几个篮子"。\n\n📊 怎么理解：\n• HHI < 2500 → 分散良好 ✅（鸡蛋在多个篮子里）\n• HHI 2500-5000 → 适度集中 ⚠️\n• HHI > 5000 → 高度集中 🔴（大部分鸡蛋在一个篮子）\n\n🔍 举个例子：\n• 5只基金各占20% → HHI=2000（分散）\n• 1只基金占80% → HHI=6800（集中！）\n\n💡 投资的第一原则：不要把所有鸡蛋放在一个篮子里。HHI帮你检查这一点。');
setExplain('avg_cost','加权平均成本','加权平均成本就是"你买了好几次，平均每份花了多少钱"。\n\n📊 计算方法：\n总花费 ÷ 总份额 = 每份均价\n\n🔍 举个例子：\n• 第1次买：¥5000，净值1.5，买了3333份\n• 第2次买：¥5000，净值1.2，买了4167份\n• 总花费 ¥10000，总份额 7500份\n→ 均价 = 10000÷7500 = 1.333\n\n💡 定投的妙处就在这里：低价时买的份额多，自动拉低你的平均成本。');
setExplain('pe','PE 市盈率','PE 就是"按现在的赚钱速度，多少年能回本"。\n\n📊 怎么理解：\n• PE = 股价 ÷ 每股利润\n• PE = 10 → 10年回本（便宜）\n• PE = 30 → 30年回本（偏贵）\n• PE = 100 → 100年回本（很贵！）\n\n🔍 A股参考标准：\n• 沪深300 PE < 12 → 低估，值得买\n• 沪深300 PE 12-16 → 合理\n• 沪深300 PE > 16 → 偏贵，谨慎\n\n💡 PE越低越便宜，但也要看公司是不是在赚越来越多的钱。');
setExplain('pb','PB 市净率','PB 就是"花多少钱买1块钱的资产"。\n\n📊 怎么理解：\n• PB = 股价 ÷ 每股净资产\n• PB < 1 → 相当于打折买（可能是白菜价）\n• PB = 1 → 按资产原价买\n• PB > 3 → 溢价买，可能太贵\n\n🔍 不同行业标准不同：\n• 银行股 PB < 0.7 很常见\n• 科技股 PB > 5 也正常\n\n💡 PB 适合评估重资产行业（银行/地产），轻资产公司看PE更合适。');
setExplain('equity_premium','股债性价比','股债性价比就是"现在买股票划算还是买债券划算"。\n\n📊 怎么算：\n股债价差 = 股票盈利收益率(1/PE) - 10年国债利率\n\n🔍 怎么看：\n• 价差 > 4% → 股票极有吸引力（股票便宜）\n• 价差 2-4% → 股票有吸引力\n• 价差 0-2% → 股债差不多\n• 价差 < 0% → 债券更划算\n\n💡 这个指标是机构投资者最爱用的"择时神器"，告诉你钱该放股市还是债市。');
setExplain('dividend_yield','股息率','股息率就是"买了股票每年能分到多少钱"，像收房租一样。\n\n📊 怎么算：\n股息率 = 每年分红 ÷ 股价 × 100%\n\n🔍 参考标准：\n• 股息率 > 3% → 高股息（很香）\n• 股息率 2-3% → 中等\n• 股息率 < 2% → 低股息（靠涨价赚钱）\n\n💡 高股息股票像收房租：每年稳定收钱，股价跌了分红不变，越跌越划算。红利低波ETF就是专门买高股息股票的。');
setExplain('safety_margin','安全边际','安全边际就是"买东西打了几折"。\n\n📊 格雷厄姆的核心理念：\n• 一个东西值100块，你50块买 = 50%安全边际\n• 安全边际越大，亏钱概率越低\n\n🔍 在投资中的应用：\n• 估值百分位 < 20% → 安全边际充足（打了很大的折）\n• 估值百分位 20-40% → 安全边际尚可\n• 估值百分位 > 70% → 没有安全边际（买贵了！）\n\n💡 格雷厄姆说："投资成功的秘诀就是安全边际"——永远不要在贵的时候买。');
setExplain('peg','PEG 比率','PEG 就是"股票贵不贵 vs 公司增长快不快"。\n\n📊 计算：\nPEG = PE(市盈率) ÷ 利润增长率\n\n🔍 彼得·林奇的标准：\n• PEG < 1 → 增长快但不贵（便宜货！）\n• PEG = 1 → 价格和增长匹配（合理）\n• PEG > 2 → 太贵了，增长撑不起这个价\n\n💡 林奇说：PE=30的公司如果利润每年增长30%，PEG=1，其实不贵；PE=10的公司如果不增长，PEG=无穷大，反而贵。');
setExplain('antifragile','反脆弱','反脆弱就是"不仅不怕风险，还能从风险中获益"。\n\n📊 塔勒布的三层分类：\n• 脆弱 → 杯子掉地上就碎了（高杠杆、高集中度）\n• 坚韧 → 铁球掉地上没事（分散配置）\n• 反脆弱 → 越摔越值钱（黑天鹅中获利）\n\n🔍 投资中的反脆弱：\n• 留20%现金 → 大跌时有钱抄底（反脆弱！）\n• 杠铃策略 → 90%极保守 + 10%高风险\n• 定投 → 越跌买越多，均价越低\n\n💡 塔勒布说："风不吹灭蜡烛，却能点旺篝火"——让自己成为篝火。');
setExplain('drawdown','回撤','回撤就是"从最高点跌了多少"。\n\n📊 怎么理解：\n• 你的账户从10万涨到12万，又跌到10.8万\n• 最高12万 → 现在10.8万 = 回撤10%\n\n🔍 风控红线：\n• 回撤 < 10% → 正常波动 ✅\n• 回撤 10-15% → 需要关注 ⚠️\n• 回撤 15-20% → 考虑降仓 🔴\n• 回撤 > 20% → 止损红线！\n\n💡 巴菲特的第一法则："永远不要亏钱"。回撤监控就是帮你守住这条线。');
setExplain('rebalance','再平衡','再平衡就是"调鸡尾酒配方"。\n\n📊 举个例子：\n你的目标配置：股票60% + 债券30% + 现金10%\n过了半年股票涨了很多，变成：股票75% + 债券20% + 现金5%\n\n🔍 再平衡操作：\n• 卖掉一些股票（75%→60%）\n• 买入债券和现金（20%→30%，5%→10%）\n• 恢复到目标比例\n\n💡 再平衡的妙处：自动"高抛低吸"——涨多的卖一点，跌多的买一点。每年做1-2次，长期能多赚2-3%/年。');

// ---- 回测可视化弹窗 ----
async function showBacktest(){
const overlay=document.createElement('div');
overlay.style.cssText='position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.6);z-index:9999;display:flex;align-items:center;justify-content:center;padding:12px;animation:fadeIn 0.2s';
overlay.onclick=e=>{if(e.target===overlay)overlay.remove()};
overlay.innerHTML=`<div style="background:var(--card);border-radius:16px;padding:20px;max-width:420px;width:100%;max-height:85vh;overflow-y:auto;box-shadow:0 20px 60px rgba(0,0,0,0.3)">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px"><h3 style="margin:0;font-size:16px">📊 策略回测（沪深300）</h3><button onclick="this.closest('[style*=fixed]').remove()" style="background:none;border:none;font-size:20px;color:var(--text2);cursor:pointer;padding:4px">✕</button></div>
<div style="text-align:center;padding:30px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>正在回测历史数据（约10秒）...</div>
</div>`;
document.body.appendChild(overlay);
try{
const r=await fetch(API_BASE+'/backtest?years=3&monthly=1000',{signal:AbortSignal.timeout(60000)});
if(!r.ok)throw new Error('backtest failed');
const d=await r.json();
if(d.error){overlay.querySelector('div>div:last-child').innerHTML=`<div style="padding:20px;text-align:center;color:var(--red)">回测失败：${d.error}</div>`;return}
const c=d.comparison||{};const fix=c.fixedDca||{};const smart=c.smartDca||{};
const adv=c.advantage||0;
const content=overlay.querySelector('div>div:last-child');
content.innerHTML=`
<div style="text-align:center;margin-bottom:16px"><div style="font-size:12px;color:var(--text2)">近3年 · 每月定投¥1,000 · 沪深300</div></div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px">
<div style="background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.2);border-radius:12px;padding:14px;text-align:center">
<div style="font-size:11px;color:var(--text2)">📦 固定定投</div>
<div style="font-size:18px;font-weight:900;color:var(--accent);margin:6px 0">${fix.totalReturnPct>0?'+':''}${fix.totalReturnPct||0}%</div>
<div style="font-size:11px;color:var(--text2)">投入 ¥${((fix.invested||0)/1000).toFixed(0)}k → ¥${((fix.finalValue||0)/1000).toFixed(0)}k</div>
<div style="font-size:11px;color:var(--text2)">年化 ${fix.annualizedReturn||0}%</div>
<div style="font-size:11px;color:var(--red)">最大回撤 -${fix.maxDrawdown||0}%</div>
</div>
<div style="background:rgba(16,185,129,.08);border:2px solid rgba(16,185,129,.3);border-radius:12px;padding:14px;text-align:center">
<div style="font-size:11px;color:var(--text2)">🧠 智能定投</div>
<div style="font-size:18px;font-weight:900;color:var(--green);margin:6px 0">${smart.totalReturnPct>0?'+':''}${smart.totalReturnPct||0}%</div>
<div style="font-size:11px;color:var(--text2)">投入 ¥${((smart.invested||0)/1000).toFixed(0)}k → ¥${((smart.finalValue||0)/1000).toFixed(0)}k</div>
<div style="font-size:11px;color:var(--text2)">年化 ${smart.annualizedReturn||0}%</div>
<div style="font-size:11px;color:var(--red)">最大回撤 -${smart.maxDrawdown||0}%</div>
</div></div>
<div style="text-align:center;padding:12px;background:rgba(16,185,129,.06);border-radius:10px;margin-bottom:12px">
<div style="font-size:13px;font-weight:700;color:var(--green)">智能定投多赚 ${adv>0?'+':''}${adv}%</div>
<div style="font-size:11px;color:var(--text2);margin-top:4px">低估多买 + 高估少买 = 更优收益</div></div>
${(()=>{const fm=fix.advancedMetrics||{};const sm=smart.advancedMetrics||{};
if(!sm.winRate&&!sm.calmar)return '';
return `<div style="margin-bottom:12px"><div style="font-size:12px;font-weight:700;margin-bottom:8px;color:var(--text1)">📐 V5.0 高级回测指标</div>
<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;font-size:11px">
<div style="background:var(--card);border-radius:8px;padding:8px;text-align:center"><div style="color:var(--text2)">胜率</div><div style="font-weight:900;color:var(--accent);margin-top:2px">${fm.winRate||0}%</div><div style="font-size:10px;color:var(--green)">${sm.winRate||0}%</div></div>
<div style="background:var(--card);border-radius:8px;padding:8px;text-align:center"><div style="color:var(--text2)">盈亏比</div><div style="font-weight:900;color:var(--accent);margin-top:2px">${fm.profitLossRatio||0}</div><div style="font-size:10px;color:var(--green)">${sm.profitLossRatio||0}</div></div>
<div style="background:var(--card);border-radius:8px;padding:8px;text-align:center"><div style="color:var(--text2)">卡玛比率</div><div style="font-weight:900;color:var(--accent);margin-top:2px">${fm.calmar||0}</div><div style="font-size:10px;color:var(--green)">${sm.calmar||0}</div></div>
<div style="background:var(--card);border-radius:8px;padding:8px;text-align:center"><div style="color:var(--text2)">信息比率</div><div style="font-weight:900;color:var(--accent);margin-top:2px">${fm.ir||0}</div><div style="font-size:10px;color:var(--green)">${sm.ir||0}</div></div>
<div style="background:var(--card);border-radius:8px;padding:8px;text-align:center"><div style="color:var(--text2)">Sortino</div><div style="font-weight:900;color:var(--accent);margin-top:2px">${fm.sortino||0}</div><div style="font-size:10px;color:var(--green)">${sm.sortino||0}</div></div>
<div style="background:var(--card);border-radius:8px;padding:8px;text-align:center"><div style="color:var(--text2)">图例</div><div style="font-size:10px;color:var(--accent);margin-top:4px">📦固定</div><div style="font-size:10px;color:var(--green)">🧠智能</div></div>
</div></div>`;})()}
<div style="font-size:11px;color:var(--text2);text-align:center;line-height:1.8">
📌 回测基于沪深300历史数据，过往收益不代表未来表现<br>
⚠️ 本数据仅供参考，不构成投资建议</div>
<button onclick="this.closest('[style*=fixed]').remove()" style="width:100%;margin-top:12px;padding:12px;border:none;border-radius:10px;background:var(--accent);color:#fff;font-size:14px;cursor:pointer">我懂了 👍</button>
`;
}catch(e){console.warn('Backtest failed:',e);const content=overlay.querySelector('div>div:last-child');if(content)content.innerHTML='<div style="padding:20px;text-align:center;color:var(--red)">回测加载失败，请稍后再试</div>'}}


// --- 07-token-budget.js ---
/* =========================================================================
 * V6 补丁 7/7：Token 预算状态栏（Pro 模式）
 * 方式：劫持 renderNavigation()，在 #profileHeader 右侧追加 Token 用量指示器
 * 数据源：/api/health → llm_usage 对象
 * 显示：🟢ok / 🟡warning(70%) / 🔴critical(90%) + 今日花费 + 调用次数
 * 仅 Pro 模式可见；Simple 模式隐藏
 * ========================================================================= */
;(function(){
  'use strict';

  // 状态颜色映射
  const STATUS_MAP = {
    ok:       { dot: '🟢', color: '#10B981', bg: 'rgba(16,185,129,.1)',  border: 'rgba(16,185,129,.25)' },
    warning:  { dot: '🟡', color: '#F59E0B', bg: 'rgba(245,158,11,.1)',  border: 'rgba(245,158,11,.25)' },
    critical: { dot: '🔴', color: '#EF4444', bg: 'rgba(239,68,68,.1)',   border: 'rgba(239,68,68,.25)' },
    unknown:  { dot: '⚪', color: '#94A3B8', bg: 'rgba(148,163,184,.08)', border: 'rgba(148,163,184,.15)' }
  };

  let _lastBudget = null;   // 缓存上次数据，避免闪烁
  let _pollTimer = null;

  // --- 拉取预算数据（含 keys_status + per-user 花费）---
  async function _fetchBudget(){
    try {
      const d = await _v6Fetch('/health');
      if (d && d.llm_usage) {
        _lastBudget = d.llm_usage;
        // 附带 keys_status
        if (d.keys_status) _lastBudget._keys = d.keys_status;
      }
      // per-user 花费（只在详情弹窗需要，badge 不等它）
      const uid = (typeof currentUserId !== 'undefined' && currentUserId) ? currentUserId : 'LeiJiang';
      try {
        const u = await _v6Fetch('/llm-usage?userId=' + uid);
        if (u && _lastBudget) {
          _lastBudget._userModules = u.modules || {};
          _lastBudget._userCalls = u.daily_count || 0;
          _lastBudget._userLimit = u.daily_limit || 100;
          _lastBudget._userId = uid;
        }
      } catch(e2){ /* per-user 失败不阻塞 */ }
    } catch(e){ /* ignore */ }
    return _lastBudget;
  }

  // --- 渲染预算指示器 ---
  function _renderBudgetBadge(data){
    if (!data) return '';
    const s = STATUS_MAP[data.status] || STATUS_MAP.unknown;
    const cost = (data.today_cost_rmb || 0).toFixed(2);
    const budget = (data.daily_budget_rmb || 3).toFixed(0);
    const calls = data.today_calls || 0;
    const pct = (data.usage_pct || 0).toFixed(0);

    // 进度条宽度
    const barW = Math.min(100, Math.max(2, data.usage_pct || 0));

    return `<div id="v6TokenBudget" onclick="_v6ShowBudgetDetail()" style="
      display:inline-flex;align-items:center;gap:4px;
      font-size:10px;padding:2px 8px;border-radius:6px;
      background:${s.bg};border:1px solid ${s.border};
      color:${s.color};cursor:pointer;white-space:nowrap;
      transition:all .3s ease;position:relative;overflow:hidden;
    " title="Token 预算：¥${cost}/¥${budget} · ${calls}次调用 · ${pct}%">
      <div style="position:absolute;bottom:0;left:0;height:2px;width:${barW}%;background:${s.color};opacity:.4;border-radius:0 0 6px 6px;transition:width .5s ease"></div>
      <span>${s.dot}</span>
      <span style="font-weight:600">¥${cost}</span>
      <span style="opacity:.6">/${budget}</span>
    </div>`;
  }

  // --- 注入/更新到顶栏 ---
  function _injectBadge(data){
    if (!isProMode()) {
      // Simple 模式隐藏
      const existing = document.getElementById('v6TokenBudget');
      if (existing) existing.remove();
      return;
    }

    const hdr = document.getElementById('profileHeader');
    if (!hdr) return;

    const html = _renderBudgetBadge(data);
    const existing = document.getElementById('v6TokenBudget');

    if (existing) {
      // 更新已有元素
      const tmp = document.createElement('span');
      tmp.innerHTML = html;
      existing.replaceWith(tmp.firstElementChild);
    } else {
      // 首次注入：插到右侧按钮组中，Pro/Simple 按钮后面
      const rightSpan = hdr.querySelector('span:last-child');
      if (rightSpan) {
        const wrapper = document.createElement('span');
        wrapper.innerHTML = html;
        rightSpan.appendChild(wrapper.firstElementChild);
      }
    }
  }

  // --- 预算详情弹窗（点击 badge 触发）---
  window._v6ShowBudgetDetail = async function(){
    const data = _lastBudget || await _fetchBudget();
    if (!data) return;

    const s = STATUS_MAP[data.status] || STATUS_MAP.unknown;
    const cost = (data.today_cost_rmb || 0).toFixed(4);
    const budget = (data.daily_budget_rmb || 3).toFixed(2);
    const calls = data.today_calls || 0;
    const pct = (data.usage_pct || 0).toFixed(1);
    const remaining = Math.max(0, (data.daily_budget_rmb || 3) - (data.today_cost_rmb || 0)).toFixed(2);

    // 进度条
    const barPct = Math.min(100, data.usage_pct || 0);

    const o = document.createElement('div');
    o.className = 'modal-overlay';
    o.onclick = e => { if (e.target === o) o.remove(); };
    o.innerHTML = `<div class="modal-sheet" onclick="event.stopPropagation()" style="max-width:360px">
      <div class="modal-handle"></div>
      <div class="modal-title">${s.dot} AI Token 预算</div>
      <div class="modal-subtitle">今日用量详情 · LLMGateway</div>

      <div style="margin:20px 0">
        <!-- 大数字 -->
        <div style="text-align:center;margin-bottom:16px">
          <div style="font-size:32px;font-weight:900;color:${s.color}">¥${cost}</div>
          <div style="font-size:13px;color:var(--text2);margin-top:4px">今日花费 / 日预算 ¥${budget}</div>
        </div>

        <!-- 进度条 -->
        <div style="background:var(--bg3);border-radius:8px;height:8px;overflow:hidden;margin:12px 0">
          <div style="height:100%;width:${barPct}%;background:${s.color};border-radius:8px;transition:width .5s ease"></div>
        </div>
        <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--text2)">
          <span>已用 ${pct}%</span>
          <span>剩余 ¥${remaining}</span>
        </div>

        <!-- 指标网格 -->
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:16px">
          <div style="background:var(--bg3);border-radius:10px;padding:12px;text-align:center">
            <div style="font-size:11px;color:var(--text2)">调用次数</div>
            <div style="font-size:20px;font-weight:800;color:var(--text1);margin-top:2px">${calls}</div>
            <div style="font-size:10px;color:var(--text2)">/ 100 日限</div>
          </div>
          <div style="background:var(--bg3);border-radius:10px;padding:12px;text-align:center">
            <div style="font-size:11px;color:var(--text2)">预算状态</div>
            <div style="font-size:20px;margin-top:2px">${s.dot}</div>
            <div style="font-size:10px;color:${s.color};font-weight:600">${
              data.status === 'ok' ? '正常' : data.status === 'warning' ? '预警 70%' : data.status === 'critical' ? '危险 90%' : '未知'
            }</div>
          </div>
        </div>

        <!-- 个人花费（我:¥xx）-->
        ${data._userId ? '<div style="margin-top:12px;display:grid;grid-template-columns:1fr 1fr;gap:8px">' +
          '<div style="background:rgba(99,102,241,.06);border-radius:10px;padding:10px 12px;text-align:center;border:1px solid rgba(99,102,241,.12)">' +
            '<div style="font-size:10px;color:var(--text2)">' + data._userId + ' 调用</div>' +
            '<div style="font-size:18px;font-weight:800;color:#6366F1;margin-top:2px">' + (data._userCalls || 0) + '</div>' +
            '<div style="font-size:10px;color:var(--text2)">/ ' + (data._userLimit || 100) + ' 日限</div>' +
          '</div>' +
          '<div style="background:rgba(99,102,241,.06);border-radius:10px;padding:10px 12px;text-align:center;border:1px solid rgba(99,102,241,.12)">' +
            '<div style="font-size:10px;color:var(--text2)">模块分布</div>' +
            '<div style="font-size:11px;color:var(--text1);margin-top:4px;line-height:1.5">' +
              (Object.keys(data._userModules || {}).length > 0 ?
                Object.entries(data._userModules).slice(0, 4).map(function(e){ return e[0] + ':' + e[1]; }).join('<br>') :
                '<span style="color:var(--text2)">今日暂无调用</span>') +
            '</div>' +
          '</div>' +
        '</div>' : ''}

        <!-- Key 健康状态 -->
        ${data._keys ? '<div style="margin-top:12px;padding:10px 12px;background:var(--bg3);border-radius:10px">' +
          '<div style="font-size:11px;color:var(--text2);margin-bottom:6px">🔑 API Key 健康</div>' +
          '<div style="display:flex;gap:12px">' +
            Object.entries(data._keys).map(function(e){
              var ok = e[1] === 'ok';
              return '<div style="display:flex;align-items:center;gap:4px">' +
                '<span style="font-size:12px">' + (ok ? '🟢' : '🔴') + '</span>' +
                '<span style="font-size:12px;font-weight:600;color:' + (ok ? '#10B981' : '#EF4444') + '">' + e[0] + '</span>' +
              '</div>';
            }).join('') +
          '</div>' +
        '</div>' : ''}

        <!-- 说明 -->
        <div style="margin-top:12px;padding:10px 12px;background:rgba(99,102,241,.06);border-radius:10px;border:1px solid rgba(99,102,241,.12)">
          <div style="font-size:11px;color:var(--text2);line-height:1.6">
            💡 <b>预算规则</b><br>
            · 日预算 ¥${budget}（月 ¥${((data.daily_budget_rmb || 3) * 30).toFixed(0)}）<br>
            · 70% 预警 → 降低非必要调用<br>
            · 90% 危险 → 仅允许紧急分析<br>
            · 突发限制：5 分钟内最多 10 次
          </div>
        </div>
      </div>

      <button onclick="this.closest('.modal-overlay').remove()" class="action-btn secondary" style="width:100%">关闭</button>
    </div>`;
    document.body.appendChild(o);
  };

  // --- 自动轮询（每 60 秒更新一次）---
  async function _pollUpdate(){
    const data = await _fetchBudget();
    _injectBadge(data);
  }

  // --- 安装：劫持 renderNav（实际渲染顶栏+底栏的函数）---
  function _install(){
    if (typeof renderNav !== 'function') return false;
    _v6Hijack('renderNav', async function(){
      // 延迟等 header DOM 就绪（renderNav 会重写 hdr.innerHTML）
      await new Promise(r => setTimeout(r, 100));
      const data = await _fetchBudget();
      _injectBadge(data);

      // 启动轮询（仅一次）
      if (!_pollTimer) {
        _pollTimer = setInterval(_pollUpdate, 60000);
      }
    });
    return true;
  }

  if (!_install()) {
    const t = setInterval(() => { if (_install()) clearInterval(t); }, 200);
    setTimeout(() => clearInterval(t), 5000);
  }

  // 模式切换时也要更新显示
  if (typeof toggleUIMode === 'function') {
    _v6Hijack('toggleUIMode', async function(){
      await new Promise(r => setTimeout(r, 200));
      _injectBadge(_lastBudget);
    });
  }

  console.log('[V6-7] token-budget badge patch installed');
})();

