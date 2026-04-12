// ============================================================
// 钱袋子 — AI 智能资产配置助手 V4.0
// 交易流水制 + 全资产管理 + 净资产仪表盘
// 实时盈亏 + AI 对话 + 云端持久化 + OCR 记账
// + 市场资讯 + 技术指标 + 宏观日历 + 收入源管理
// ============================================================

// ---- API 配置 ----
const API_BASE = (() => {
  const h = location.hostname;
  if (h === 'localhost' || h === '127.0.0.1' || h.startsWith('192.168.')) return 'http://localhost:8000/api';
  return '/api'; // Railway 部署：同源相对路径
})();
let API_AVAILABLE = false;

function getUserId() {
  let id = localStorage.getItem('moneybag_uid');
  if (!id) { id = 'u_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2,8); localStorage.setItem('moneybag_uid', id); }
  return id;
}

// ---- 基金详情 ----
const FUND_DETAILS = {
  '110020': { fullName:'易方达沪深300ETF联接A', type:'指数基金', company:'易方达基金', scale:'约310亿', fee:'管理费0.15%/年+托管费0.05%/年', founded:'2009-08-26', tracking:'沪深300指数', reason:'费率全市场最低档，规模大流动性好，跟踪误差极小。沪深300覆盖A股市值最大的300家公司。', history:{y1:'+8.2%',y3:'+12.5%',y5:'+42.3%'}, risk:'中高风险(R4)', buyTip:'支付宝搜索110020→买入→选"定投"更佳', tags:['低费率','大规模','宽基指数','A股核心'] },
  '050025': { fullName:'博时标普500ETF联接A', type:'指数基金(QDII)', company:'博时基金', scale:'约85亿', fee:'管理费0.15%/年+托管费0.05%/年', founded:'2012-06-14', tracking:'标普500指数', reason:'追踪美国500强企业，分散地域风险。过去30年标普500年化回报约10%。', history:{y1:'+22.1%',y3:'+38.7%',y5:'+95.2%'}, risk:'中高风险(R4)', buyTip:'支付宝搜索050025→买入', tags:['美股','全球配置','QDII','长期王者'] },
  '217022': { fullName:'招商产业债A', type:'债券基金', company:'招商基金', scale:'约120亿', fee:'管理费0.30%/年+托管费0.10%/年', founded:'2012-12-04', tracking:'无(主动管理)', reason:'纯债基金标杆，历史几乎没亏过年度。组合的"稳定器"。', history:{y1:'+4.1%',y3:'+12.8%',y5:'+22.5%'}, risk:'中低风险(R2)', buyTip:'支付宝搜索217022→买入', tags:['低风险','稳定器','纯债','年年正收益'] },
  '000216': { fullName:'华安黄金ETF联接A', type:'商品基金', company:'华安基金', scale:'约95亿', fee:'管理费0.15%/年+托管费0.05%/年', founded:'2013-08-14', tracking:'黄金现货价格', reason:'买黄金最方便的方式。经典避险资产，近年全球央行狂买。', history:{y1:'+28.5%',y3:'+52.3%',y5:'+78.6%'}, risk:'中风险(R3)', buyTip:'支付宝搜索000216→买入', tags:['避险','抗通胀','黄金','央行增持'] },
  '070018': { fullName:'嘉实多利优选混合A', type:'混合基金', company:'嘉实基金', scale:'约45亿', fee:'管理费0.60%/年+托管费0.15%/年', founded:'2009-12-22', tracking:'无(偏REITs/高股息)', reason:'投资高分红股票和类REITs资产，每季度分红，像收房租。', history:{y1:'+6.8%',y3:'+18.2%',y5:'+35.7%'}, risk:'中风险(R3)', buyTip:'支付宝搜索070018→买入', tags:['分红','类REITs','现金流','收租体验'] },
  '余额宝': { fullName:'余额宝(天弘货币基金)', type:'货币基金', company:'天弘基金', scale:'约7000亿', fee:'管理费0.30%/年+托管费0.08%/年', founded:'2013-06-13', tracking:'无', reason:'国民级货币基金，随时存取，几乎零风险。应急弹药。', history:{y1:'+1.8%',y3:'+5.5%',y5:'+10.2%'}, risk:'低风险(R1)', buyTip:'直接在支付宝余额宝存入', tags:['零风险','随时取','应急','抄底弹药'] },
};

// ---- 问卷 & 配置 ----
const QUESTIONS = [
  { emoji:'💰', question:'你打算拿出多少钱来理财？', options:[{text:'10万以下',score:1},{text:'10-30万',score:2},{text:'30-50万',score:3},{text:'50-100万',score:4},{text:'100万以上',score:5}] },
  { emoji:'⏰', question:'这笔钱你多久不会用到？', options:[{text:'随时可能用',score:1},{text:'1年内不用',score:2},{text:'1-3年',score:3},{text:'3-5年',score:4},{text:'5年以上',score:5}] },
  { emoji:'📉', question:'投了10万，一个月后变成8.5万，你会？', options:[{text:'立刻全卖😱',score:1},{text:'卖掉一半😟',score:2},{text:'不动，观望😐',score:3},{text:'再买一点😏',score:4},{text:'加大力度买💪',score:5}] },
  { emoji:'🎯', question:'你最期望的年收益是？', options:[{text:'3-5%(跑赢存款)',score:1},{text:'5-8%',score:2},{text:'8-15%',score:3},{text:'15-25%',score:4},{text:'25%+(搏一把)',score:5}] },
  { emoji:'📚', question:'你对理财投资的了解程度？', options:[{text:'完全不懂',score:1},{text:'听过基金股票',score:2},{text:'买过余额宝/银行理财',score:3},{text:'买过基金/股票',score:4},{text:'有系统投资经验',score:5}] },
];
const RISK_PROFILES = [
  {min:5,max:8,name:'保守型',emoji:'🐢',color:'#4CAF50',desc:'你追求资产安全，不愿承受大幅波动。',period:'1年以上'},
  {min:9,max:12,name:'稳健型',emoji:'🐰',color:'#2196F3',desc:'你希望有一定收益，但也注重安全。',period:'2年以上'},
  {min:13,max:17,name:'平衡型',emoji:'🦊',color:'#FF9800',desc:'你希望资产稳步增长，能接受一定波动。',period:'3年以上'},
  {min:18,max:21,name:'进取型',emoji:'🦁',color:'#F44336',desc:'你追求较高收益，能承受较大波动。',period:'3-5年'},
  {min:22,max:25,name:'激进型',emoji:'🦅',color:'#9C27B0',desc:'你追求最大化收益，能承受剧烈波动。',period:'5年以上'},
];
const ALLOCATIONS = {
  '保守型':[{name:'沪深300',code:'110020',fullName:'易方达沪深300ETF联接A',pct:10,color:'#3B82F6',returns:{good:.15,mid:.08,bad:-.10}},{name:'标普500',code:'050025',fullName:'博时标普500ETF联接A',pct:5,color:'#10B981',returns:{good:.18,mid:.10,bad:-.12}},{name:'债券',code:'217022',fullName:'招商产业债A',pct:50,color:'#F59E0B',returns:{good:.06,mid:.04,bad:.01}},{name:'黄金',code:'000216',fullName:'华安黄金ETF联接A',pct:15,color:'#F97316',returns:{good:.15,mid:.08,bad:-.05}},{name:'REITs',code:'070018',fullName:'嘉实多利优选A',pct:10,color:'#EF4444',returns:{good:.10,mid:.06,bad:-.05}},{name:'货币(应急)',code:'余额宝',fullName:'余额宝',pct:10,color:'#E5E7EB',returns:{good:.02,mid:.018,bad:.015}}],
  '稳健型':[{name:'沪深300',code:'110020',fullName:'易方达沪深300ETF联接A',pct:20,color:'#3B82F6',returns:{good:.15,mid:.08,bad:-.10}},{name:'标普500',code:'050025',fullName:'博时标普500ETF联接A',pct:10,color:'#10B981',returns:{good:.18,mid:.10,bad:-.12}},{name:'债券',code:'217022',fullName:'招商产业债A',pct:35,color:'#F59E0B',returns:{good:.06,mid:.04,bad:.01}},{name:'黄金',code:'000216',fullName:'华安黄金ETF联接A',pct:15,color:'#F97316',returns:{good:.15,mid:.08,bad:-.05}},{name:'REITs',code:'070018',fullName:'嘉实多利优选A',pct:10,color:'#EF4444',returns:{good:.10,mid:.06,bad:-.05}},{name:'货币(应急)',code:'余额宝',fullName:'余额宝',pct:10,color:'#E5E7EB',returns:{good:.02,mid:.018,bad:.015}}],
  '平衡型':[{name:'沪深300',code:'110020',fullName:'易方达沪深300ETF联接A',pct:30,color:'#3B82F6',returns:{good:.20,mid:.10,bad:-.15}},{name:'标普500',code:'050025',fullName:'博时标普500ETF联接A',pct:20,color:'#10B981',returns:{good:.22,mid:.12,bad:-.15}},{name:'债券',code:'217022',fullName:'招商产业债A',pct:20,color:'#F59E0B',returns:{good:.06,mid:.04,bad:.01}},{name:'黄金',code:'000216',fullName:'华安黄金ETF联接A',pct:15,color:'#F97316',returns:{good:.15,mid:.08,bad:-.05}},{name:'REITs',code:'070018',fullName:'嘉实多利优选A',pct:10,color:'#EF4444',returns:{good:.10,mid:.06,bad:-.05}},{name:'货币(应急)',code:'余额宝',fullName:'余额宝',pct:5,color:'#E5E7EB',returns:{good:.02,mid:.018,bad:.015}}],
  '进取型':[{name:'沪深300',code:'110020',fullName:'易方达沪深300ETF联接A',pct:35,color:'#3B82F6',returns:{good:.25,mid:.12,bad:-.18}},{name:'标普500',code:'050025',fullName:'博时标普500ETF联接A',pct:25,color:'#10B981',returns:{good:.25,mid:.13,bad:-.18}},{name:'债券',code:'217022',fullName:'招商产业债A',pct:10,color:'#F59E0B',returns:{good:.06,mid:.04,bad:.01}},{name:'黄金',code:'000216',fullName:'华安黄金ETF联接A',pct:10,color:'#F97316',returns:{good:.15,mid:.08,bad:-.05}},{name:'REITs',code:'070018',fullName:'嘉实多利优选A',pct:15,color:'#EF4444',returns:{good:.12,mid:.07,bad:-.08}},{name:'货币(应急)',code:'余额宝',fullName:'余额宝',pct:5,color:'#E5E7EB',returns:{good:.02,mid:.018,bad:.015}}],
  '激进型':[{name:'沪深300',code:'110020',fullName:'易方达沪深300ETF联接A',pct:40,color:'#3B82F6',returns:{good:.30,mid:.12,bad:-.22}},{name:'标普500',code:'050025',fullName:'博时标普500ETF联接A',pct:30,color:'#10B981',returns:{good:.30,mid:.15,bad:-.22}},{name:'债券',code:'217022',fullName:'招商产业债A',pct:5,color:'#F59E0B',returns:{good:.06,mid:.04,bad:.01}},{name:'黄金',code:'000216',fullName:'华安黄金ETF联接A',pct:5,color:'#F97316',returns:{good:.15,mid:.08,bad:-.05}},{name:'REITs',code:'070018',fullName:'嘉实多利优选A',pct:15,color:'#EF4444',returns:{good:.15,mid:.08,bad:-.10}},{name:'货币(应急)',code:'余额宝',fullName:'余额宝',pct:5,color:'#E5E7EB',returns:{good:.02,mid:.018,bad:.015}}],
};
const AMOUNT_MAP = [0,80000,200000,400000,750000,1500000];

// ---- 全局状态 ----
let currentPage='landing', currentQuestion=0, answers=[], selectedAmount=0;
let chartInstance=null, projChartInstance=null, liveNavData={}, currentProfile=null, currentAllocs=null;
let chatMessages=[];
const STORAGE_KEY='moneybag_portfolio';
const TXN_KEY='moneybag_transactions';
const ASSETS_KEY='moneybag_assets';
const LEDGER_KEY='moneybag_ledger';
const SOURCES_KEY='moneybag_income_sources';
const EXPENSE_ICONS={'餐饮':'🍜','交通':'🚗','购物':'🛍️','娱乐':'🎮','医疗':'🏥','教育':'📚','房租':'🏠','日用':'🧴','通讯':'📱','其他':'📌'};
const INCOME_ICONS={'工资':'💰','兼职':'🔧','民宿':'🏡','外包':'💻','理财收益':'📈','红包':'🧧','退款':'↩️','出租房':'🏘️','其他收入':'💵'};
const SOURCE_TYPE_ICONS={'民宿':'🏡','出租房':'🏘️','外包':'💻','兼职':'🔧','工资':'💰','理财收益':'📈','电商':'🛒','自媒体':'📱','其他':'💵'};
const LEDGER_ICONS={...EXPENSE_ICONS,...INCOME_ICONS};
let ledgerDirection='expense'; // 当前记账方向

// ---- V4 交易流水 + 资产管理数据层 ----
function loadTxns(){try{return JSON.parse(localStorage.getItem(TXN_KEY))||[]}catch{return[]}}
function saveTxns(d){localStorage.setItem(TXN_KEY,JSON.stringify(d))}
function loadAssets(){try{return JSON.parse(localStorage.getItem(ASSETS_KEY))||[]}catch{return[]}}
function saveAssets(d){localStorage.setItem(ASSETS_KEY,JSON.stringify(d))}

// 从交易流水计算持仓（加权平均成本法）
function calcHoldingsFromTxns(txns){
  const map={};
  txns.filter(t=>t.type==='BUY'||t.type==='SELL').sort((a,b)=>new Date(a.date)-new Date(b.date)).forEach(t=>{
    if(!map[t.code])map[t.code]={code:t.code,name:t.name||t.code,shares:0,totalCost:0,avgPrice:0,category:t.category||''};
    const h=map[t.code];
    if(t.type==='BUY'){
      h.totalCost+=t.shares*t.price;h.shares+=t.shares;h.avgPrice=h.shares>0?h.totalCost/h.shares:0;
      if(t.name)h.name=t.name;if(t.category)h.category=t.category;
    }else if(t.type==='SELL'){
      const sellShares=Math.min(t.shares,h.shares);
      h.totalCost-=sellShares*h.avgPrice;h.shares-=sellShares;
      if(h.shares<=0){h.shares=0;h.totalCost=0;h.avgPrice=0}
    }
  });
  return Object.values(map).filter(h=>h.shares>0);
}

// 计算净资产
function calcNetWorth(){
  const txns=loadTxns();const assets=loadAssets();const ledger=loadLedger();
  const holdings=calcHoldingsFromTxns(txns);
  const fundValue=holdings.reduce((s,h)=>s+h.totalCost,0); // 用成本估算（有实时净值时替换）
  const assetTotal=assets.filter(a=>a.type!=='liability').reduce((s,a)=>s+(a.value||0),0);
  const liabilities=assets.filter(a=>a.type==='liability').reduce((s,a)=>s+(a.value||0),0);
  const ledgerIncome=ledger.filter(e=>e.direction==='income').reduce((s,e)=>s+(e.amount||0),0);
  const ledgerExpense=ledger.filter(e=>e.direction!=='income').reduce((s,e)=>s+(e.amount||0),0);
  return {fundValue,assetTotal,liabilities,ledgerIncome,ledgerExpense,ledgerNet:ledgerIncome-ledgerExpense,netWorth:fundValue+assetTotal-liabilities+ledgerIncome-ledgerExpense,holdings};
}

// V3→V4 数据迁移：如果有旧holdings但没有交易记录，自动生成
function migrateV3toV4(){
  const p=loadPortfolio();const txns=loadTxns();
  if(p.holdings&&p.holdings.length>0&&txns.length===0){
    const newTxns=p.holdings.map(h=>({
      id:'mig_'+Date.now().toString(36)+'_'+Math.random().toString(36).slice(2,6),
      type:'BUY',code:h.code,name:h.name,category:h.category||'',
      shares:h.amount/(liveNavData[h.code]?.nav||1),price:liveNavData[h.code]?.nav||1,
      amount:h.amount,date:h.buyDate||new Date().toISOString(),note:'V3迁移',source:'migration'
    }));
    saveTxns(newTxns);
  }
}

// ---- 收入源管理 ----
function loadSources(){try{return JSON.parse(localStorage.getItem(SOURCES_KEY))||[]}catch{return[]}}
function saveSources(d){localStorage.setItem(SOURCES_KEY,JSON.stringify(d))}
function addSource(name,type,expectedAmt,note){
const ss=loadSources();
ss.push({id:'src_'+Date.now().toString(36),name,type,expectedAmt:parseFloat(expectedAmt)||0,note:note||'',createdAt:new Date().toISOString(),lastRecordAt:null,totalRecorded:0,recordCount:0});
saveSources(ss);
if(API_AVAILABLE)fetch(API_BASE+'/income-sources/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:getUserId(),name,type,expectedAmt:parseFloat(expectedAmt)||0,note:note||''})}).catch(()=>{});
return ss}
function deleteSource(id){const ss=loadSources().filter(s=>s.id!==id);saveSources(ss);
if(API_AVAILABLE)fetch(API_BASE+'/income-sources/'+getUserId()+'/'+id,{method:'DELETE'}).catch(()=>{});
return ss}
function quickRecord(id){
const ss=loadSources();const src=ss.find(s=>s.id===id);if(!src)return;
// 弹出确认/修改金额
const amt=prompt(`${SOURCE_TYPE_ICONS[src.type]||'💵'} ${src.name}\n本月实际收入金额：`,src.expectedAmt);
if(!amt||isNaN(parseFloat(amt))||parseFloat(amt)<=0)return;
const realAmt=parseFloat(amt);
// 写入记账
const es=loadLedger();es.push({date:new Date().toISOString(),amount:realAmt,category:src.type,note:src.name,direction:'income',source:'income_source',sourceId:src.id});saveLedger(es);
// 更新收入源统计
src.lastRecordAt=new Date().toISOString();src.totalRecorded+=realAmt;src.recordCount++;saveSources(ss);
if(API_AVAILABLE)fetch(API_BASE+'/income-sources/record',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:getUserId(),sourceId:src.id,amount:realAmt})}).catch(()=>{});
renderLedger()}

function loadPortfolio(){try{return JSON.parse(localStorage.getItem(STORAGE_KEY))||{holdings:[],history:[],profile:null,amount:0}}catch{return{holdings:[],history:[],profile:null,amount:0}}}
function savePortfolio(d){localStorage.setItem(STORAGE_KEY,JSON.stringify(d));syncCloud(d)}
function loadLedger(){try{return JSON.parse(localStorage.getItem(LEDGER_KEY))||[]}catch{return[]}}
function saveLedger(d){localStorage.setItem(LEDGER_KEY,JSON.stringify(d))}
function recordPurchase(allocs,amount,profileName){const p=loadPortfolio();const now=new Date().toISOString();p.profile=profileName;p.amount=amount;p.holdings=allocs.map(a=>({code:a.code,name:a.fullName,category:a.name,targetPct:a.pct,amount:Math.round(amount*a.pct/100),buyDate:now}));p.history.push({date:now,action:'buy',amount,profile:profileName});savePortfolio(p);
// V4: 同时生成交易流水
const txns=loadTxns();
allocs.forEach(a=>{const buyAmt=Math.round(amount*a.pct/100);const nav=liveNavData[a.code]?.nav||1;
txns.push({id:'txn_'+Date.now().toString(36)+'_'+Math.random().toString(36).slice(2,6),
type:'BUY',code:a.code,name:a.fullName,category:a.name,
shares:buyAmt/nav,price:nav,amount:buyAmt,date:now,note:'测评配置',source:'quiz'})});
saveTxns(txns)}

async function syncCloud(portfolio){if(!API_AVAILABLE)return;try{await fetch(API_BASE+'/user/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:getUserId(),portfolio})})}catch{}}
async function syncFromCloud(){if(!API_AVAILABLE)return;try{const r=await fetch(API_BASE+'/user/'+getUserId());if(r.ok){const d=await r.json();if(d.portfolio?.holdings?.length){const l=loadPortfolio();if(!l.holdings.length)localStorage.setItem(STORAGE_KEY,JSON.stringify(d.portfolio))}}}catch{}}

// ---- 工具 ----
function $(s){return document.querySelector(s)}
function fmtMoney(n){if(Math.abs(n)>=10000)return(n/10000).toFixed(1)+'万';return n.toLocaleString('zh-CN')}
function fmtFull(n){return'¥'+n.toLocaleString('zh-CN')}
function getProfile(s){return RISK_PROFILES.find(p=>s>=p.min&&s<=p.max)||RISK_PROFILES[2]}
function calcReturns(a,amt,sc){let t=0;a.forEach(x=>{t+=(amt*x.pct/100)*x.returns[sc]});return t}

// ---- API ----
async function checkAPI(){try{const r=await fetch(API_BASE+'/health',{signal:AbortSignal.timeout(3000)});API_AVAILABLE=r.ok}catch{API_AVAILABLE=false}}
async function fetchNav(){if(!API_AVAILABLE)return;try{const r=await fetch(API_BASE+'/nav/all');if(r.ok)liveNavData=await r.json()}catch{}}
async function fetchSignals(){if(!API_AVAILABLE)return null;try{const p=loadPortfolio();if(!p.holdings.length)return null;const r=await fetch(API_BASE+'/signals',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});if(r.ok)return await r.json()}catch{}return null}
async function fetchPnl(){if(!API_AVAILABLE)return null;try{const p=loadPortfolio();if(!p.holdings.length)return null;const r=await fetch(API_BASE+'/portfolio/pnl',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});if(r.ok)return await r.json()}catch{}return null}
async function fetchDashboard(){if(!API_AVAILABLE)return null;try{const r=await fetch(API_BASE+'/dashboard',{signal:AbortSignal.timeout(30000)});if(r.ok)return await r.json()}catch(e){console.warn('Dashboard fetch failed:',e)}return null}
async function fetchFundNews(code){if(!API_AVAILABLE)return[];try{const r=await fetch(API_BASE+'/news/'+code);if(r.ok){const d=await r.json();return d.news||[]}}catch{}return[]}
async function fetchPortfolioNews(){if(!API_AVAILABLE)return{};try{const r=await fetch(API_BASE+'/news/portfolio');if(r.ok)return await r.json()}catch{}return{}}

// ---- 样式注入 ----
function injectStyles(){const s=document.createElement('style');s.textContent=`
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0F172A;--bg2:#1E293B;--bg3:#334155;--text:#F1F5F9;--text2:#94A3B8;--accent:#F59E0B;--green:#10B981;--red:#EF4444;--blue:#3B82F6;--radius:16px}
body{font-family:'Noto Sans SC',-apple-system,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;overflow-x:hidden;-webkit-tap-highlight-color:transparent}
#app{max-width:480px;margin:0 auto;padding:20px 20px 100px;min-height:100vh}
@keyframes fadeUp{from{opacity:0;transform:translateY(30px)}to{opacity:1;transform:translateY(0)}}
@keyframes fadeIn{from{opacity:0}to{opacity:1}}
@keyframes scaleIn{from{opacity:0;transform:scale(.9)}to{opacity:1;transform:scale(1)}}
@keyframes slideUp{from{opacity:0;transform:translateY(100%)}to{opacity:1;transform:translateY(0)}}
@keyframes float{0%,100%{transform:translateY(0)}50%{transform:translateY(-8px)}}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes typingDot{0%,60%,100%{opacity:.3}30%{opacity:1}}
.fade-up{animation:fadeUp .6s ease forwards}.stagger>*{opacity:0;animation:fadeUp .5s ease forwards}
.stagger>*:nth-child(1){animation-delay:.1s}.stagger>*:nth-child(2){animation-delay:.2s}.stagger>*:nth-child(3){animation-delay:.3s}.stagger>*:nth-child(4){animation-delay:.4s}.stagger>*:nth-child(5){animation-delay:.5s}.stagger>*:nth-child(6){animation-delay:.6s}
.landing{display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:80vh;text-align:center}
.landing-icon{font-size:72px;margin-bottom:24px;animation:float 3s ease-in-out infinite}
.landing h1{font-size:32px;font-weight:900;margin-bottom:12px;background:linear-gradient(135deg,var(--accent),#F472B6,var(--blue));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.landing .subtitle{font-size:16px;color:var(--text2);margin-bottom:40px;line-height:1.6}
.cta-btn{background:linear-gradient(135deg,var(--accent),#F97316);color:#000;border:none;padding:16px 48px;font-size:18px;font-weight:700;border-radius:50px;cursor:pointer;font-family:inherit;box-shadow:0 4px 20px rgba(245,158,11,.4)}.cta-btn:active{transform:scale(.96)}
.trust-badges{margin-top:32px;display:flex;gap:20px;flex-wrap:wrap;justify-content:center}
.trust-badge{font-size:13px;color:var(--text2);display:flex;align-items:center;gap:4px}.trust-badge::before{content:'✓';color:var(--green);font-weight:700}
.has-portfolio-badge{margin-top:16px;font-size:13px;color:var(--accent);cursor:pointer;padding:8px 16px;border:1px solid rgba(245,158,11,.3);border-radius:8px}
.quiz-header{margin-bottom:32px}.progress-bar-bg{width:100%;height:6px;background:var(--bg3);border-radius:3px;overflow:hidden;margin-bottom:16px}
.progress-bar-fill{height:100%;background:linear-gradient(90deg,var(--accent),#F472B6);border-radius:3px;transition:width .5s ease}.quiz-step{font-size:13px;color:var(--text2)}
.question-emoji{font-size:48px;margin-bottom:16px}.question-text{font-size:22px;font-weight:700;margin-bottom:28px;line-height:1.4}
.options{display:flex;flex-direction:column;gap:12px}
.option-btn{background:var(--bg2);border:2px solid var(--bg3);color:var(--text);padding:16px 20px;font-size:16px;border-radius:12px;cursor:pointer;text-align:left;font-family:inherit;position:relative}
.option-btn:active{transform:scale(.97);background:rgba(245,158,11,.15);border-color:var(--accent)}.option-btn.selected{border-color:var(--accent);background:rgba(245,158,11,.15)}
.amount-section{margin-top:32px;animation:fadeUp .5s ease forwards}.amount-input-wrap{position:relative;margin-bottom:8px}
.amount-input{width:100%;background:var(--bg2);border:2px solid var(--bg3);color:var(--text);padding:16px 60px 16px 36px;font-size:24px;font-weight:700;border-radius:12px;outline:none;font-family:inherit}.amount-input:focus{border-color:var(--accent)}
.amount-prefix{position:absolute;left:16px;top:50%;transform:translateY(-50%);color:var(--text2);font-size:18px}
.amount-suffix{position:absolute;right:16px;top:50%;transform:translateY(-50%);color:var(--text2);font-size:14px}
.amount-quick{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.quick-btn{background:var(--bg3);border:none;color:var(--text2);padding:8px 16px;font-size:13px;border-radius:8px;cursor:pointer;font-family:inherit}.quick-btn:active,.quick-btn.active{background:var(--accent);color:#000}
.generate-btn{width:100%;margin-top:24px;background:linear-gradient(135deg,var(--accent),#F97316);color:#000;border:none;padding:16px;font-size:17px;font-weight:700;border-radius:12px;cursor:pointer;font-family:inherit}.generate-btn:disabled{opacity:.4}
.loading-screen{display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:60vh;text-align:center}
.loading-spinner{width:48px;height:48px;border:4px solid var(--bg3);border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite;margin-bottom:20px}
.result-page{padding-bottom:20px}
.profile-card{background:linear-gradient(135deg,var(--bg2),var(--bg3));border-radius:var(--radius);padding:28px;margin-bottom:24px;text-align:center;position:relative;overflow:hidden;animation:scaleIn .5s ease forwards}
.profile-emoji{font-size:56px;margin-bottom:12px}.profile-name{font-size:26px;font-weight:900;margin-bottom:8px}.profile-desc{font-size:14px;color:var(--text2);line-height:1.6}.profile-period{margin-top:12px;font-size:13px;color:var(--accent)}
.section-title{font-size:18px;font-weight:700;margin:28px 0 16px;display:flex;align-items:center;gap:8px}
.chart-card{background:var(--bg2);border-radius:var(--radius);padding:24px;margin-bottom:24px}.chart-wrap{width:240px;height:240px;margin:0 auto 20px}
.alloc-list{display:flex;flex-direction:column;gap:10px}.alloc-item{display:flex;align-items:center;gap:10px;font-size:14px;cursor:pointer;padding:6px 8px;border-radius:8px}.alloc-item:active{background:rgba(245,158,11,.1)}
.alloc-dot{width:12px;height:12px;border-radius:50%;flex-shrink:0}.alloc-name{flex:1;color:var(--text2)}.alloc-pct{font-weight:700;width:40px;text-align:right}.alloc-money{color:var(--accent);font-weight:500;width:90px;text-align:right}
.shopping-list{background:var(--bg2);border-radius:var(--radius);padding:24px;margin-bottom:24px}
.shop-item{display:flex;align-items:flex-start;gap:12px;padding:14px 0;border-bottom:1px solid var(--bg3);cursor:pointer}.shop-item:last-child{border-bottom:none}
.shop-num{width:28px;height:28px;background:var(--accent);color:#000;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;flex-shrink:0;margin-top:2px}
.shop-detail{flex:1}.shop-fund-name{font-weight:600;font-size:15px;margin-bottom:4px}.shop-code{display:inline-block;background:var(--bg3);padding:2px 8px;border-radius:4px;font-size:13px;color:var(--accent);font-family:monospace;margin-right:6px}
.shop-platform{font-size:12px;color:var(--text2)}.shop-live-nav{font-size:12px;color:var(--green);margin-top:4px}.shop-amount{font-size:17px;font-weight:700;color:var(--accent);white-space:nowrap}
.projection-card{background:var(--bg2);border-radius:var(--radius);padding:24px;margin-bottom:24px}
.scenario-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:20px}.scenario-item{text-align:center;padding:16px 8px;background:var(--bg);border-radius:12px}
.scenario-label{font-size:12px;color:var(--text2);margin-bottom:4px}.scenario-return{font-size:20px;font-weight:900}.scenario-money{font-size:12px;color:var(--text2);margin-top:4px}
.scenario-return.pos{color:var(--green)}.scenario-return.neg{color:var(--red)}.projection-chart-wrap{height:200px;margin-top:16px}
.data-source-card{background:var(--bg2);border-radius:var(--radius);padding:20px;margin-bottom:24px}
.data-source-title{font-size:14px;font-weight:600;margin-bottom:12px;color:var(--accent)}.data-source-item{font-size:12px;color:var(--text2);line-height:1.8;display:flex;gap:8px}.data-source-item::before{content:'📊';flex-shrink:0}
.api-status{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;margin-left:4px}.api-status.on{background:rgba(16,185,129,.15);color:var(--green)}.api-status.off{background:rgba(239,68,68,.15);color:var(--red)}
.rules-card{background:linear-gradient(135deg,#1a1a2e,#16213e);border:1px solid rgba(245,158,11,.2);border-radius:var(--radius);padding:24px;margin-bottom:24px}
.rule-item{display:flex;gap:12px;margin-bottom:16px;align-items:flex-start}.rule-item:last-child{margin-bottom:0}
.rule-num{width:28px;height:28px;background:rgba(245,158,11,.15);color:var(--accent);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:700;flex-shrink:0}
.rule-text{font-size:15px;line-height:1.5}.rule-text strong{color:var(--accent)}
.signals-card{background:linear-gradient(135deg,#0d2818,#1a2e35);border:1px solid rgba(16,185,129,.3);border-radius:var(--radius);padding:24px;margin-bottom:24px}
.signal-item{display:flex;gap:12px;margin-bottom:14px;align-items:flex-start}.signal-item:last-child{margin-bottom:0}.signal-icon{font-size:24px;flex-shrink:0}.signal-text{font-size:14px;line-height:1.5}.signal-text strong{color:var(--green)}
.bottom-actions{display:flex;gap:12px;flex-wrap:wrap}
.action-btn{flex:1;min-width:120px;padding:14px;border-radius:12px;font-size:14px;font-weight:600;cursor:pointer;font-family:inherit;text-align:center}
.action-btn.primary{background:linear-gradient(135deg,var(--accent),#F97316);color:#000;border:none}.action-btn.secondary{background:transparent;color:var(--text2);border:1px solid var(--bg3)}.action-btn.green{background:linear-gradient(135deg,var(--green),#059669);color:#fff;border:none}.action-btn:active{transform:scale(.96)}
.modal-overlay{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.7);z-index:1000;display:flex;align-items:flex-end;justify-content:center;animation:fadeIn .2s ease;backdrop-filter:blur(4px)}
.modal-sheet{background:var(--bg2);width:100%;max-width:480px;max-height:85vh;border-radius:20px 20px 0 0;padding:24px 24px 36px;overflow-y:auto;animation:slideUp .35s ease}
.modal-handle{width:40px;height:4px;background:var(--bg3);border-radius:2px;margin:0 auto 20px}.modal-title{font-size:20px;font-weight:700;margin-bottom:4px}.modal-subtitle{font-size:13px;color:var(--accent);margin-bottom:20px}
.modal-section{margin-bottom:16px}.modal-section-title{font-size:13px;color:var(--text2);margin-bottom:8px;font-weight:600}
.modal-tags{display:flex;gap:8px;flex-wrap:wrap}.modal-tag{background:rgba(245,158,11,.12);color:var(--accent);padding:4px 12px;border-radius:6px;font-size:12px}
.modal-stat-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.modal-stat{background:var(--bg);border-radius:10px;padding:12px;text-align:center}.modal-stat-label{font-size:11px;color:var(--text2);margin-bottom:4px}.modal-stat-value{font-size:16px;font-weight:700}
.modal-history-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px}.modal-history-item{text-align:center;background:var(--bg);border-radius:8px;padding:10px}.modal-history-label{font-size:11px;color:var(--text2)}.modal-history-value{font-size:15px;font-weight:700;color:var(--green)}
.modal-buy-tip{background:rgba(16,185,129,.08);border:1px solid rgba(16,185,129,.2);border-radius:10px;padding:14px;font-size:13px;line-height:1.6}
.modal-reason{background:rgba(59,130,246,.08);border-left:3px solid var(--blue);padding:14px;border-radius:0 10px 10px 0;font-size:14px;line-height:1.7}
.pnl-hero{text-align:center;margin-bottom:24px;padding:24px;background:linear-gradient(135deg,var(--bg2),var(--bg3));border-radius:var(--radius)}
.pnl-total-value{font-size:36px;font-weight:900;color:var(--accent)}.pnl-label{font-size:13px;color:var(--text2);margin-top:4px}.pnl-change{font-size:20px;font-weight:700;margin-top:8px}.pnl-change.pos{color:var(--green)}.pnl-change.neg{color:var(--red)}.pnl-sub{font-size:12px;color:var(--text2);margin-top:4px}
.holding-card{background:var(--bg2);border-radius:12px;padding:16px;margin-bottom:12px;cursor:pointer}.holding-card:active{background:var(--bg3)}
.holding-top{display:flex;align-items:center;gap:12px;margin-bottom:8px}.holding-info{flex:1}.holding-name{font-weight:600;font-size:14px}.holding-meta{font-size:12px;color:var(--text2);margin-top:2px}
.holding-amount{text-align:right}.holding-money{font-size:16px;font-weight:700;color:var(--accent)}.holding-pct{font-size:12px;color:var(--text2)}
.holding-pnl-row{display:flex;justify-content:space-between;padding-top:8px;border-top:1px solid var(--bg3);font-size:12px}.holding-pnl-item{text-align:center;flex:1}.holding-pnl-label{color:var(--text2);margin-bottom:2px}.holding-pnl-val{font-weight:700}.holding-pnl-val.pos{color:var(--green)}.holding-pnl-val.neg{color:var(--red)}
.chat-page{display:flex;flex-direction:column;height:calc(100vh - 120px)}.chat-header{text-align:center;padding:16px 0}.chat-header h2{font-size:20px;font-weight:700}.chat-header p{font-size:13px;color:var(--text2)}
.chat-messages{flex:1;overflow-y:auto;padding:8px 0;display:flex;flex-direction:column;gap:12px}
.chat-msg{max-width:85%;padding:12px 16px;border-radius:16px;font-size:14px;line-height:1.6;word-break:break-word;white-space:pre-wrap}
.chat-msg.user{background:var(--accent);color:#000;align-self:flex-end;border-bottom-right-radius:4px}.chat-msg.bot{background:var(--bg2);color:var(--text);align-self:flex-start;border-bottom-left-radius:4px}
.chat-msg .src-tag{display:inline-block;font-size:10px;color:var(--text2);margin-top:8px;padding:2px 6px;background:var(--bg3);border-radius:4px}
.chat-typing{display:flex;gap:4px;padding:12px 16px}.chat-typing span{width:8px;height:8px;background:var(--text2);border-radius:50%;animation:typingDot 1.4s infinite}.chat-typing span:nth-child(2){animation-delay:.2s}.chat-typing span:nth-child(3){animation-delay:.4s}
.chat-input-bar{display:flex;gap:8px;padding:12px 0}.chat-input{flex:1;background:var(--bg2);border:2px solid var(--bg3);color:var(--text);padding:12px 16px;font-size:15px;border-radius:24px;outline:none;font-family:inherit}.chat-input:focus{border-color:var(--accent)}
.chat-send{background:var(--accent);color:#000;border:none;width:44px;height:44px;border-radius:50%;font-size:18px;cursor:pointer;display:flex;align-items:center;justify-content:center}.chat-send:disabled{opacity:.4}
.chat-suggestions{display:flex;gap:8px;flex-wrap:wrap;padding:8px 0}.chat-suggest-btn{background:var(--bg2);border:1px solid var(--bg3);color:var(--text2);padding:8px 14px;border-radius:20px;font-size:13px;cursor:pointer;font-family:inherit}.chat-suggest-btn:active{background:var(--accent);color:#000}
.ledger-page{padding-bottom:20px}.ledger-header{text-align:center;margin-bottom:24px}.ledger-total{font-size:32px;font-weight:900;color:var(--red)}.ledger-period{font-size:13px;color:var(--text2);margin-top:4px}
.ledger-cats{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-bottom:20px}.ledger-cat{background:var(--bg2);border-radius:10px;padding:12px 8px;text-align:center}.ledger-cat-icon{font-size:20px}.ledger-cat-name{font-size:11px;color:var(--text2);margin:4px 0}.ledger-cat-amt{font-size:14px;font-weight:700}
.ledger-entry{background:var(--bg2);border-radius:10px;padding:14px 16px;margin-bottom:8px;display:flex;align-items:center;gap:12px}.ledger-entry-icon{font-size:24px}.ledger-entry-info{flex:1}.ledger-entry-note{font-size:14px;font-weight:500}.ledger-entry-date{font-size:11px;color:var(--text2)}.ledger-entry-amt{font-size:16px;font-weight:700;color:var(--red)}
.upload-area{background:var(--bg2);border:2px dashed var(--bg3);border-radius:var(--radius);padding:32px;text-align:center;cursor:pointer;margin-bottom:16px}.upload-area:active{border-color:var(--accent)}.upload-area .icon{font-size:48px;margin-bottom:12px}.upload-area .text{font-size:14px;color:var(--text2)}
.manual-form{background:var(--bg2);border-radius:var(--radius);padding:20px;margin-bottom:16px}.form-row{margin-bottom:12px}.form-label{font-size:12px;color:var(--text2);margin-bottom:4px}
.form-input{width:100%;background:var(--bg);border:1px solid var(--bg3);color:var(--text);padding:10px 14px;font-size:15px;border-radius:8px;outline:none;font-family:inherit}.form-input:focus{border-color:var(--accent)}
.form-select{width:100%;background:var(--bg);border:1px solid var(--bg3);color:var(--text);padding:10px 14px;font-size:15px;border-radius:8px;outline:none;font-family:inherit}
.form-submit{width:100%;background:var(--accent);color:#000;border:none;padding:12px;font-size:15px;font-weight:700;border-radius:10px;cursor:pointer;font-family:inherit;margin-top:8px}
.bottom-nav{position:fixed;bottom:0;left:50%;transform:translateX(-50%);width:100%;max-width:480px;background:var(--bg2);border-top:1px solid var(--bg3);display:flex;padding:6px 0;padding-bottom:max(6px,env(safe-area-inset-bottom));z-index:100}
.nav-item{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;padding:6px 0;cursor:pointer;color:var(--text2);font-size:10px}.nav-item.active{color:var(--accent)}.nav-icon{font-size:22px}
.footer-disclaimer{text-align:center;font-size:11px;color:#475569;margin-top:32px;padding:16px;line-height:1.5}
.insight-page{padding-bottom:20px}.insight-header{text-align:center;margin-bottom:24px}.insight-header h2{font-size:22px;font-weight:700}.insight-header p{font-size:13px;color:var(--text2);margin-top:4px}
.dashboard-card{background:var(--bg2);border-radius:var(--radius);padding:20px;margin-bottom:16px}
.dashboard-card-title{font-size:15px;font-weight:700;margin-bottom:12px;display:flex;align-items:center;gap:8px}
.fgi-gauge{text-align:center;padding:16px 0}.fgi-score{font-size:48px;font-weight:900}.fgi-label{font-size:14px;color:var(--text2);margin-top:4px}
.fgi-dims{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-top:12px}.fgi-dim{text-align:center;background:var(--bg);border-radius:8px;padding:8px}.fgi-dim-label{font-size:11px;color:var(--text2)}.fgi-dim-val{font-size:14px;font-weight:700;margin-top:2px}
.tech-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.tech-item{background:var(--bg);border-radius:10px;padding:12px;text-align:center}.tech-label{font-size:11px;color:var(--text2)}.tech-value{font-size:16px;font-weight:700;margin-top:4px}.tech-signal{font-size:11px;margin-top:4px;padding:2px 8px;border-radius:4px;display:inline-block}
.tech-signal.buy{background:rgba(16,185,129,.15);color:var(--green)}.tech-signal.sell{background:rgba(239,68,68,.15);color:var(--red)}.tech-signal.neutral{background:rgba(148,163,184,.15);color:var(--text2)}
.news-item{padding:12px 0;border-bottom:1px solid var(--bg3);display:flex;gap:10px;align-items:flex-start;cursor:pointer;transition:background .2s;border-radius:8px;padding:10px 8px;margin:0 -8px}.news-item:active{background:rgba(255,255,255,.05)}.news-item:last-child{border-bottom:none}.news-icon{font-size:18px;flex-shrink:0;margin-top:2px}.news-content{flex:1}.news-title{font-size:14px;line-height:1.5}.news-meta{font-size:11px;color:var(--text2);margin-top:4px}.news-arrow{color:var(--text2);font-size:12px;flex-shrink:0;align-self:center}
.macro-item{display:flex;gap:12px;padding:12px 0;border-bottom:1px solid var(--bg3);align-items:flex-start;cursor:pointer;transition:background .2s;border-radius:8px;padding:10px 8px;margin:0 -8px}.macro-item:active{background:rgba(255,255,255,.05)}.macro-item:last-child{border-bottom:none}.macro-icon{font-size:24px;flex-shrink:0}.macro-info{flex:1}.macro-name{font-size:14px;font-weight:600}.macro-value{font-size:18px;font-weight:900;color:var(--accent);margin:4px 0}.macro-impact{font-size:12px;color:var(--text2);line-height:1.4}.macro-date{font-size:11px;color:var(--text2)}
.val-bar{height:8px;background:var(--bg3);border-radius:4px;margin:8px 0;position:relative;overflow:visible}.val-bar-fill{height:100%;border-radius:4px;transition:width .5s ease}.val-bar-marker{position:absolute;top:-4px;width:16px;height:16px;border-radius:50%;border:2px solid var(--bg);transform:translateX(-50%)}
.section-tab-bar{display:flex;gap:6px;margin-bottom:16px;overflow-x:auto;padding-bottom:4px}.section-tab{padding:6px 14px;border-radius:20px;font-size:13px;background:var(--bg3);color:var(--text2);border:none;cursor:pointer;font-family:inherit;white-space:nowrap}.section-tab.active{background:var(--accent);color:#000;font-weight:600}
`;document.head.appendChild(s)}

// ---- 底部导航 ----
function renderNav(){let n=document.getElementById('btmNav');if(!n){n=document.createElement('div');n.id='btmNav';n.className='bottom-nav';document.body.appendChild(n)}
const tabs=[{id:'landing',icon:'🏠',label:'首页'},{id:'portfolio',icon:'📊',label:'持仓'},{id:'insight',icon:'📰',label:'资讯'},{id:'chat',icon:'🤖',label:'AI分析'},{id:'assets',icon:'🏦',label:'资产'}];
n.innerHTML=tabs.map(t=>`<div class="nav-item ${currentPage===t.id?'active':''}" onclick="navigateTo('${t.id}')"><div class="nav-icon">${t.icon}</div><div>${t.label}</div></div>`).join('')}

function navigateTo(p){currentPage=p;renderNav();if(p==='landing')renderLanding();else if(p==='portfolio')renderPortfolio();else if(p==='insight')renderInsight();else if(p==='chat')renderChat();else if(p==='ledger')renderLedger();else if(p==='assets')renderAssets()}

// ---- 落地页（净资产仪表盘）----
function renderLanding(){currentPage='landing';const p=loadPortfolio();const txns=loadTxns();const assets=loadAssets();const ledger=loadLedger();
const hasTxns=txns.length>0||p.holdings.length>0||assets.length>0||ledger.length>0;
if(!hasTxns){
$('#app').innerHTML=`<div class="landing stagger"><div class="landing-icon">💰</div><h1>你的钱，该怎么放？</h1><p class="subtitle">回答5个问题，AI帮你出一份<br>专属资产配置方案</p><button class="cta-btn" onclick="startQuiz()">开始测评</button><div class="trust-badges"><span class="trust-badge">不收费</span><span class="trust-badge">不推销</span><span class="trust-badge">不注册</span></div></div>`;renderNav();return}
// 有数据 → 净资产仪表盘
const nw=calcNetWorth();const holdings=nw.holdings;
const monthNow=new Date();const monthStart=new Date(monthNow.getFullYear(),monthNow.getMonth(),1).toISOString();
const monthLedger=ledger.filter(e=>e.date>=monthStart);
const monthInc=monthLedger.filter(e=>e.direction==='income').reduce((s,e)=>s+(e.amount||0),0);
const monthExp=monthLedger.filter(e=>e.direction!=='income').reduce((s,e)=>s+(e.amount||0),0);

$('#app').innerHTML=`<div class="result-page fade-up">
<div class="pnl-hero" style="margin-bottom:16px">
<div class="pnl-label">💰 我的净资产</div>
<div class="pnl-total-value">${fmtFull(Math.round(nw.netWorth))}</div>
<div style="display:flex;gap:16px;justify-content:center;margin-top:12px;font-size:12px">
<div style="text-align:center"><div style="color:var(--text2)">基金持仓</div><div style="font-weight:700;color:var(--accent)">¥${fmtMoney(Math.round(nw.fundValue))}</div></div>
<div style="text-align:center"><div style="color:var(--text2)">其他资产</div><div style="font-weight:700;color:var(--blue)">¥${fmtMoney(Math.round(nw.assetTotal))}</div></div>
<div style="text-align:center"><div style="color:var(--text2)">负债</div><div style="font-weight:700;color:var(--red)">¥${fmtMoney(Math.round(nw.liabilities))}</div></div>
<div style="text-align:center"><div style="color:var(--text2)">现金流</div><div style="font-weight:700;color:${nw.ledgerNet>=0?'var(--green)':'var(--red)'}">¥${fmtMoney(Math.round(nw.ledgerNet))}</div></div>
</div>
</div>

<div style="display:flex;gap:8px;margin-bottom:16px">
<div style="flex:1;background:rgba(16,185,129,.08);border:1px solid rgba(16,185,129,.2);border-radius:12px;padding:12px;text-align:center">
<div style="font-size:11px;color:var(--text2)">本月收入</div>
<div style="font-size:16px;font-weight:700;color:var(--green)">+¥${Math.round(monthInc)}</div>
</div>
<div style="flex:1;background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.2);border-radius:12px;padding:12px;text-align:center">
<div style="font-size:11px;color:var(--text2)">本月支出</div>
<div style="font-size:16px;font-weight:700;color:var(--red)">-¥${Math.round(monthExp)}</div>
</div>
</div>

${holdings.length?`<div class="section-title">📊 基金持仓 (${holdings.length})</div>
${holdings.map(h=>`<div class="holding-card" onclick="showHoldingActions('${h.code}')">
<div class="holding-top"><div class="holding-info"><div class="holding-name">${h.name}</div><div class="holding-meta">${h.category} · ${h.shares.toFixed(2)}份 · 均价¥${h.avgPrice.toFixed(4)}</div></div>
<div class="holding-amount"><div class="holding-money">¥${Math.round(h.totalCost)}</div></div></div></div>`).join('')}`:''}

<div class="bottom-actions" style="margin-top:16px">
<button class="action-btn primary" onclick="showAddTxn()">➕ 记一笔交易</button>
<button class="action-btn secondary" onclick="startQuiz()">🔄 重新测评</button>
</div>
</div>`;renderNav()}

// ---- 问卷 ----
function renderQuiz(){const q=QUESTIONS[currentQuestion];$('#app').innerHTML=`<div class="quiz-header fade-up"><div class="progress-bar-bg"><div class="progress-bar-fill" style="width:${currentQuestion/QUESTIONS.length*100}%"></div></div><div class="quiz-step">第${currentQuestion+1}/${QUESTIONS.length}题</div></div><div class="question-card"><div class="question-emoji">${q.emoji}</div><div class="question-text">${q.question}</div><div class="options stagger">${q.options.map((o,i)=>`<button class="option-btn" onclick="selectAnswer(${i},${o.score})">${o.text}</button>`).join('')}</div></div>`}
function renderAmountInput(){const presets=[100000,200000,300000,500000,1000000];$('#app').innerHTML=`<div class="quiz-header"><div class="progress-bar-bg"><div class="progress-bar-fill" style="width:100%"></div></div><div class="quiz-step">最后一步 ✨</div></div><div class="amount-section"><div class="question-emoji">🎯</div><div class="question-text">你具体想投多少钱？</div><div style="font-size:14px;color:var(--text2);margin-bottom:12px">输入金额，算出每个篮子该放多少</div><div class="amount-input-wrap"><span class="amount-prefix">¥</span><input class="amount-input" type="number" id="amtIn" placeholder="500000" value="${AMOUNT_MAP[answers[0]]||''}" oninput="onAmtChange()" inputmode="numeric"><span class="amount-suffix">元</span></div><div class="amount-quick">${presets.map(a=>`<button class="quick-btn" onclick="setAmt(${a})">${fmtMoney(a)}</button>`).join('')}</div><button class="generate-btn" id="genBtn" onclick="genResult()" ${AMOUNT_MAP[answers[0]]?'':'disabled'}>生成我的配置方案 →</button></div>`;onAmtChange()}

// ---- 结果页 ----
function renderResult(){const ts=answers.reduce((s,a)=>s+a,0);const pf=getProfile(ts);const al=ALLOCATIONS[pf.name];const amt=selectedAmount;currentProfile=pf;currentAllocs=al;
const gR=calcReturns(al,amt,'good'),mR=calcReturns(al,amt,'mid'),bR=calcReturns(al,amt,'bad');
$('#app').innerHTML=`<div class="result-page">
<div class="profile-card"><div class="profile-emoji">${pf.emoji}</div><div class="profile-name" style="color:${pf.color}">你是「${pf.name}」投资者</div><div class="profile-desc">${pf.desc}</div><div class="profile-period">建议投资周期：${pf.period}</div></div>
<div class="section-title">📊 你的${fmtMoney(amt)}这样分</div>
<div class="chart-card"><div class="chart-wrap"><canvas id="allocChart"></canvas></div><div class="alloc-list">${al.map(a=>`<div class="alloc-item" onclick="showFundDetail('${a.code}')"><div class="alloc-dot" style="background:${a.color}"></div><div class="alloc-name">${a.name}</div><div class="alloc-pct">${a.pct}%</div><div class="alloc-money">${fmtFull(Math.round(amt*a.pct/100))}</div></div>`).join('')}</div></div>
<div class="section-title">📋 打开支付宝，照着买</div>
<div class="shopping-list">${al.map((a,i)=>`<div class="shop-item" onclick="showFundDetail('${a.code}')"><div class="shop-num">${i+1}</div><div class="shop-detail"><div class="shop-fund-name">${a.fullName}</div><span class="shop-code">${a.code}</span><span class="shop-platform">${a.code==='余额宝'?'留在余额宝':'支付宝搜索代码'}</span>${liveNavData[a.code]?`<div class="shop-live-nav">📡 净值:${liveNavData[a.code].nav}(${liveNavData[a.code].date})</div>`:''}</div><div class="shop-amount">${fmtFull(Math.round(amt*a.pct/100))}</div></div>`).join('')}</div>
<div class="section-title">📊 数据来源</div>
<div class="data-source-card"><div class="data-source-title">配置依据</div><div class="data-source-item">Markowitz均值-方差模型(1952诺贝尔经济学奖)</div><div class="data-source-item">参考Vanguard Target-Date基金系列配比</div><div class="data-source-item">各类资产过去10-20年历史年化回报</div><div style="margin-top:12px"><div class="data-source-title">实时数据 <span class="api-status ${API_AVAILABLE?'on':'off'}">${API_AVAILABLE?'🟢已连接':'🔴离线'}</span></div></div></div>
<div class="section-title">💰 一年后可能会怎样</div>
<div class="projection-card"><div class="scenario-grid">
<div class="scenario-item"><div>📈</div><div class="scenario-label">乐观</div><div class="scenario-return pos">+${(gR/amt*100).toFixed(1)}%</div><div class="scenario-money">赚${fmtMoney(Math.round(gR))}</div></div>
<div class="scenario-item"><div>📊</div><div class="scenario-label">中性</div><div class="scenario-return pos">+${(mR/amt*100).toFixed(1)}%</div><div class="scenario-money">赚${fmtMoney(Math.round(mR))}</div></div>
<div class="scenario-item"><div>📉</div><div class="scenario-label">悲观</div><div class="scenario-return ${bR>=0?'pos':'neg'}">${bR>=0?'+':''}${(bR/amt*100).toFixed(1)}%</div><div class="scenario-money">${bR>=0?'赚':'亏'}${fmtMoney(Math.abs(Math.round(bR)))}</div></div>
</div><div style="font-size:13px;color:var(--text2);margin-bottom:8px">3年累计预测(中性场景)</div><div class="projection-chart-wrap"><canvas id="projChart"></canvas></div></div>
<div id="signalsSection"></div>
<div class="section-title">⚠️ 三条铁律</div>
<div class="rules-card"><div class="rule-item"><div class="rule-num">1</div><div class="rule-text"><strong>跌了别卖</strong>—越跌越该买</div></div><div class="rule-item"><div class="rule-num">2</div><div class="rule-text"><strong>别看新闻瞎操作</strong>—关掉手机</div></div><div class="rule-item"><div class="rule-num">3</div><div class="rule-text"><strong>至少拿3年</strong>—3年赚钱概率>85%</div></div></div>
<div class="bottom-actions"><button class="action-btn green" onclick="confirmPurchase()">✅ 我已买入</button><button class="action-btn secondary" onclick="restart()">🔄 重新测评</button></div>
<div class="footer-disclaimer">⚠️ 本工具仅供参考学习，不构成投资建议。投资有风险，入市需谨慎。</div></div>`;
renderNav();setTimeout(()=>drawAllocChart(al),100);setTimeout(()=>drawProjChart(amt,mR/amt),200);loadSignals()}

async function loadSignals(){const s=await fetchSignals();const el=document.getElementById('signalsSection');if(!el||!s||!s.length)return;el.innerHTML=`<div class="section-title">🔔 AI买卖信号</div><div class="signals-card">${s.map(x=>`<div class="signal-item"><div class="signal-icon">${x.icon||'📡'}</div><div class="signal-text"><strong>${x.title}</strong><br>${x.message}</div></div>`).join('')}</div>`}

// ---- 持仓盈亏页（V4 交易流水制）----
async function renderPortfolio(){currentPage='portfolio';renderNav();
const txns=loadTxns();const holdings=calcHoldingsFromTxns(txns);
const p=loadPortfolio(); // 兼容旧数据

// 没有任何持仓
if(!holdings.length&&!p.holdings.length){$('#app').innerHTML=`<div class="landing" style="min-height:70vh"><div class="landing-icon">📊</div><h1>还没有持仓</h1><p class="subtitle">完成测评记录买入，或手动添加交易</p><div class="bottom-actions" style="margin-top:24px;justify-content:center"><button class="cta-btn" onclick="startQuiz()">去测评</button></div><div style="text-align:center;margin-top:16px"><button class="action-btn secondary" style="display:inline-block;min-width:auto" onclick="showAddTxn()">➕ 手动添加交易</button></div></div>`;return}

const useV4=holdings.length>0;
const displayHoldings=useV4?holdings:p.holdings.map(h=>({code:h.code,name:h.name,category:h.category,shares:0,totalCost:h.amount,avgPrice:0}));
const tc=displayHoldings.reduce((s,h)=>s+h.totalCost,0);

$('#app').innerHTML=`<div class="portfolio-page fade-up">
<div class="pnl-hero"><div class="pnl-label">${p.profile?p.profile+'·':''}基金持仓</div><div class="pnl-total-value">${fmtFull(Math.round(tc))}</div>
<div id="pnlSum"><div style="font-size:13px;color:var(--text2);margin-top:8px">${API_AVAILABLE?'正在计算实时盈亏...':'后端离线，显示买入成本'}</div></div></div>

<div id="holdList">${displayHoldings.map(h=>`<div class="holding-card" onclick="showHoldingActions('${h.code}')">
<div class="holding-top"><div class="holding-info"><div class="holding-name">${h.name}</div>
<div class="holding-meta">${h.category}${h.shares?' · '+h.shares.toFixed(2)+'份':''}${h.avgPrice?' · 均价¥'+h.avgPrice.toFixed(4):''}</div></div>
<div class="holding-amount"><div class="holding-money">${fmtFull(Math.round(h.totalCost))}</div></div></div></div>`).join('')}</div>

<div style="margin-top:16px;margin-bottom:8px;display:flex;gap:8px;flex-wrap:wrap">
<button class="action-btn primary" onclick="showAddTxn()">➕ 新交易</button>
<button class="action-btn secondary" onclick="showAddCustomFund()">🔍 添加自选</button>
</div>

${txns.length?`<div class="section-title">📋 交易记录 (${txns.length})</div>
<div id="txnList">${txns.slice(-20).reverse().map(t=>{
const isBuy=t.type==='BUY';
return`<div class="ledger-entry"><div class="ledger-entry-icon">${isBuy?'🟢':'🔴'}</div>
<div class="ledger-entry-info"><div class="ledger-entry-note">${t.type} ${t.name||t.code}${t.note?' · '+t.note:''}</div>
<div class="ledger-entry-date">${new Date(t.date).toLocaleString('zh-CN')} · ${t.shares?.toFixed(2)||'-'}份 × ¥${t.price?.toFixed(4)||'-'}</div></div>
<div class="ledger-entry-amt" style="color:${isBuy?'var(--green)':'var(--red)'}">¥${Math.round(t.amount||t.shares*t.price)}</div></div>`}).join('')}</div>`:''}

<div class="bottom-actions" style="margin-top:16px">
<button class="action-btn secondary" onclick="startQuiz()">🔄 重新测评</button>
<button class="action-btn secondary" onclick="if(confirm('清除所有持仓和交易记录？')){localStorage.removeItem(TXN_KEY);localStorage.removeItem(STORAGE_KEY);renderPortfolio()}">🗑️ 清除</button>
</div></div>`;

// 异步更新实时盈亏
if(API_AVAILABLE&&useV4){
try{
const body={holdings:displayHoldings.map(h=>({code:h.code,name:h.name,category:h.category,amount:Math.round(h.totalCost),targetPct:0,buyDate:new Date().toISOString()}))};
const r=await fetch(API_BASE+'/portfolio/pnl',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
if(r.ok){const pnl=await r.json();
const pe=document.getElementById('pnlSum');
if(pe){const c=pnl.totalPnl>=0?'pos':'neg';const sg=pnl.totalPnl>=0?'+':'';
pe.innerHTML=`<div class="pnl-change ${c}">${sg}${fmtFull(Math.round(pnl.totalPnl))}(${sg}${pnl.totalPnlPct.toFixed(2)}%)</div><div class="pnl-sub">当前市值${fmtFull(Math.round(pnl.totalMarket))}</div>`}}}catch{}}}

// 持仓操作弹窗（加仓/卖出/删除）
function showHoldingActions(code){
const txns=loadTxns();const holdings=calcHoldingsFromTxns(txns);
const h=holdings.find(x=>x.code===code);
const detail=FUND_DETAILS[code];
const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()"><div class="modal-handle"></div>
<div class="modal-title">${h?h.name:detail?.fullName||code}</div>
<div class="modal-subtitle">${code}${h?` · ${h.shares.toFixed(2)}份 · 均价¥${h.avgPrice.toFixed(4)}`:''}</div>
${h?`<div class="modal-stat-grid" style="margin-bottom:16px">
<div class="modal-stat"><div class="modal-stat-label">持有份额</div><div class="modal-stat-value">${h.shares.toFixed(2)}</div></div>
<div class="modal-stat"><div class="modal-stat-label">总成本</div><div class="modal-stat-value">¥${Math.round(h.totalCost)}</div></div>
</div>`:''}
<div style="display:flex;flex-direction:column;gap:10px">
<button class="action-btn green" onclick="document.querySelector('.modal-overlay')?.remove();showAddTxnFor('${code}','BUY')">🟢 加仓买入</button>
${h?`<button class="action-btn primary" style="background:linear-gradient(135deg,var(--red),#DC2626);color:#fff" onclick="document.querySelector('.modal-overlay')?.remove();showAddTxnFor('${code}','SELL')">🔴 卖出</button>`:''}
<button class="action-btn secondary" onclick="document.querySelector('.modal-overlay')?.remove();showFundDetail('${code}')">📋 基金详情</button>
${h?`<button class="action-btn secondary" style="color:var(--red)" onclick="if(confirm('删除${h.name}所有交易记录？')){deleteFundTxns('${code}');document.querySelector('.modal-overlay')?.remove();renderPortfolio()}">🗑️ 删除持仓</button>`:''}
</div></div>`;
document.body.appendChild(o)}

// 添加交易弹窗
function showAddTxn(){showAddTxnFor('','BUY')}

function showAddTxnFor(code,type){
const detail=code?FUND_DETAILS[code]:null;
const allCodes=Object.keys(FUND_DETAILS);
const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()"><div class="modal-handle"></div>
<div class="modal-title">${type==='BUY'?'🟢 买入':'🔴 卖出'}基金</div>
<div class="manual-form" style="background:transparent;padding:0;margin-top:16px">
<div class="form-row"><div class="form-label">基金代码</div>
<input class="form-input" type="text" id="txnCode" placeholder="输入基金代码 如 110020" value="${code}" ${code?'readonly':''}></div>
<div class="form-row"><div class="form-label">基金名称</div>
<input class="form-input" type="text" id="txnName" placeholder="基金名称" value="${detail?.fullName||''}"></div>
<div class="form-row"><div class="form-label">买入/卖出金额(¥)</div>
<input class="form-input" type="number" id="txnAmt" placeholder="0" inputmode="decimal"></div>
<div class="form-row"><div class="form-label">净值(每份价格)</div>
<input class="form-input" type="number" id="txnPrice" placeholder="如 1.2345" step="0.0001" inputmode="decimal" value="${liveNavData[code]?.nav||''}"></div>
<div class="form-row"><div class="form-label">份额(自动计算)</div>
<input class="form-input" type="number" id="txnShares" placeholder="金额÷净值" readonly style="opacity:.6"></div>
<div class="form-row"><div class="form-label">备注</div>
<input class="form-input" type="text" id="txnNote" placeholder="可选"></div>
<button class="form-submit" style="background:${type==='BUY'?'var(--green)':'var(--red)'}" onclick="confirmAddTxn('${type}')">确认${type==='BUY'?'买入':'卖出'}</button>
</div></div>`;
document.body.appendChild(o);
// 自动算份额
const amtIn=document.getElementById('txnAmt');const priceIn=document.getElementById('txnPrice');const sharesIn=document.getElementById('txnShares');
const calcShares=()=>{const a=parseFloat(amtIn?.value);const p=parseFloat(priceIn?.value);if(a>0&&p>0)sharesIn.value=(a/p).toFixed(2)};
amtIn?.addEventListener('input',calcShares);priceIn?.addEventListener('input',calcShares);
// 自动获取基金名和净值
if(!code){const codeIn=document.getElementById('txnCode');const nameIn=document.getElementById('txnName');
codeIn?.addEventListener('blur',()=>{const c=codeIn.value.trim();const d=FUND_DETAILS[c];if(d){nameIn.value=d.fullName}
if(liveNavData[c]){priceIn.value=liveNavData[c].nav;calcShares()}})}}

function confirmAddTxn(type){
const code=document.getElementById('txnCode')?.value?.trim();
const name=document.getElementById('txnName')?.value?.trim();
const amt=parseFloat(document.getElementById('txnAmt')?.value);
const price=parseFloat(document.getElementById('txnPrice')?.value);
const shares=parseFloat(document.getElementById('txnShares')?.value);
const note=document.getElementById('txnNote')?.value?.trim()||'';
if(!code){alert('请输入基金代码');return}
if(!amt||amt<=0){alert('请输入金额');return}
if(!price||price<=0){alert('请输入净值');return}
const txns=loadTxns();
txns.push({id:'txn_'+Date.now().toString(36)+'_'+Math.random().toString(36).slice(2,6),
type,code,name:name||code,category:FUND_DETAILS[code]?.type||'',
shares:shares||amt/price,price,amount:amt,date:new Date().toISOString(),note,source:'manual'});
saveTxns(txns);
// 同步到后端
if(API_AVAILABLE)fetch(API_BASE+'/portfolio/transaction',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({userId:getUserId(),transaction:{type,code,name:name||code,shares:shares||amt/price,nav:price,amount:amt,note}})}).catch(()=>{});
document.querySelector('.modal-overlay')?.remove();
renderPortfolio()}

function deleteFundTxns(code){const txns=loadTxns().filter(t=>t.code!==code);saveTxns(txns)}

// 添加自选基金弹窗
function showAddCustomFund(){
const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()"><div class="modal-handle"></div>
<div class="modal-title">🔍 添加自选基金</div>
<div class="modal-subtitle">添加推荐列表之外的基金</div>
<div class="manual-form" style="background:transparent;padding:0;margin-top:16px">
<div class="form-row"><div class="form-label">基金代码</div>
<input class="form-input" type="text" id="customCode" placeholder="输入6位基金代码" inputmode="numeric"></div>
<div class="form-row"><div class="form-label">基金名称</div>
<input class="form-input" type="text" id="customName" placeholder="基金名称"></div>
<div id="searchResult"></div>
<button class="form-submit" onclick="confirmCustomFund()">确认添加并买入</button>
</div></div>`;
document.body.appendChild(o);
// 搜索功能
const codeIn=document.getElementById('customCode');
codeIn?.addEventListener('blur',async()=>{
const c=codeIn.value.trim();if(!c||c.length<3)return;
if(FUND_DETAILS[c]){document.getElementById('customName').value=FUND_DETAILS[c].fullName;return}
if(!API_AVAILABLE)return;
try{const r=await fetch(API_BASE+'/fund/search?q='+encodeURIComponent(c));if(r.ok){const d=await r.json();
if(d.results?.length){const f=d.results[0];document.getElementById('customName').value=f.name||'';
document.getElementById('searchResult').innerHTML=`<div style="padding:8px;font-size:12px;color:var(--green)">✅ 找到：${f.name} (${f.code})</div>`}}}catch{}})}

function confirmCustomFund(){
const code=document.getElementById('customCode')?.value?.trim();
const name=document.getElementById('customName')?.value?.trim();
if(!code){alert('请输入基金代码');return}
if(!name){alert('请输入基金名称');return}
document.querySelector('.modal-overlay')?.remove();
showAddTxnFor(code,'BUY')}

// ---- AI聊天页 ----
function renderChat(){currentPage='chat';renderNav();const sugs=['现在适合入场吗？','什么时候该卖出？','智能定投怎么投？','最近有什么新闻？','技术指标怎么样？','宏观经济怎么样？','今天市场怎么样？','黄金还能买吗？'];
$('#app').innerHTML=`<div class="chat-page"><div class="chat-header"><h2>🤖 AI理财分析师</h2><p>${API_AVAILABLE?'连接实时数据分析':'后端离线，部分功能受限'}</p></div><div class="chat-messages" id="chatMsgs"><div class="chat-msg bot">你好！我是钱袋子AI分析师。问我关于市场行情、持仓、买卖建议等问题。\n\n所有建议仅供参考 😊<div class="src-tag">系统</div></div>${chatMessages.map(m=>`<div class="chat-msg ${m.role}">${m.text}${m.src?`<div class="src-tag">${m.src==='ai'?'AI分析':'规则引擎'}</div>`:''}</div>`).join('')}</div><div class="chat-suggestions" id="chatSugs">${sugs.map(s=>`<button class="chat-suggest-btn" onclick="sendChat('${s}')">${s}</button>`).join('')}</div><div class="chat-input-bar"><input class="chat-input" id="chatIn" placeholder="问点什么..." onkeydown="if(event.key==='Enter')sendChat()"><button class="chat-send" onclick="sendChat()">→</button></div></div>`;scrollChat()}

async function sendChat(text){const inp=document.getElementById('chatIn');const msg=text||(inp?inp.value.trim():'');if(!msg)return;if(inp)inp.value='';
const sg=document.getElementById('chatSugs');if(sg)sg.style.display='none';
chatMessages.push({role:'user',text:msg});appendMsg('user',msg);appendTyping();
if(API_AVAILABLE){try{const p=loadPortfolio();const r=await fetch(API_BASE+'/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:msg,portfolio:p.holdings.length?p:null})});rmTyping();if(r.ok){const d=await r.json();chatMessages.push({role:'bot',text:d.reply,src:d.source});appendMsg('bot',d.reply,d.source);return}}catch{}}
rmTyping();const fb='后端未连接，无法获取实时数据。请确保后端运行中。';chatMessages.push({role:'bot',text:fb,src:'offline'});appendMsg('bot',fb,'offline')}

function appendMsg(r,t,src){const el=document.getElementById('chatMsgs');if(!el)return;const d=document.createElement('div');d.className='chat-msg '+r;d.innerHTML=t+(src?`<div class="src-tag">${src==='ai'?'AI分析':src==='rules'?'规则引擎':'离线'}</div>`:'');el.appendChild(d);scrollChat()}
function appendTyping(){const el=document.getElementById('chatMsgs');if(!el)return;const d=document.createElement('div');d.className='chat-typing';d.id='chatTyp';d.innerHTML='<span></span><span></span><span></span>';el.appendChild(d);scrollChat()}
function rmTyping(){const el=document.getElementById('chatTyp');if(el)el.remove()}
function scrollChat(){const el=document.getElementById('chatMsgs');if(el)setTimeout(()=>el.scrollTop=el.scrollHeight,50)}

// ---- 白话解释弹窗 ----
function showExplain(title,text){
const overlay=document.createElement('div');
overlay.style.cssText='position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.6);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px;animation:fadeIn 0.2s';
overlay.onclick=e=>{if(e.target===overlay)overlay.remove()};
const lines=text.replace(/\\n/g,'\n').split('\n').map(l=>l.trim()?`<p style="margin:4px 0;${l.startsWith('•')||l.startsWith('📊')||l.startsWith('🔍')||l.startsWith('🎯')||l.startsWith('💡')||l.startsWith('⚠️')?'':''}${l.startsWith('📊')||l.startsWith('🔍')||l.startsWith('🎯')||l.startsWith('💡')||l.startsWith('⚠️')?'font-weight:600;margin-top:12px;':''}">${l}</p>`:'').join('');
overlay.innerHTML=`<div style="background:var(--card);border-radius:16px;padding:24px;max-width:380px;width:100%;max-height:80vh;overflow-y:auto;box-shadow:0 20px 60px rgba(0,0,0,0.3)">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px"><h3 style="margin:0;font-size:16px;color:var(--text1)">${title}</h3><button onclick="this.closest('[style*=fixed]').remove()" style="background:none;border:none;font-size:20px;color:var(--text2);cursor:pointer;padding:4px">✕</button></div>
<div style="font-size:13px;line-height:1.8;color:var(--text2)">${lines}</div>
<button onclick="this.closest('[style*=fixed]').remove()" style="width:100%;margin-top:16px;padding:12px;border:none;border-radius:10px;background:var(--accent);color:#fff;font-size:14px;cursor:pointer">我懂了 👍</button>
</div>`;
document.body.appendChild(overlay)}

// ---- 资讯页 ----
let insightTab='overview';
async function renderInsight(){currentPage='insight';renderNav();
$('#app').innerHTML=`<div class="insight-page fade-up"><div class="insight-header"><h2>📰 市场资讯</h2><p>${API_AVAILABLE?'实时数据更新中':'后端离线'}</p></div><div class="section-tab-bar"><button class="section-tab ${insightTab==='overview'?'active':''}" onclick="insightTab='overview';renderInsight()">📊 总览</button><button class="section-tab ${insightTab==='news'?'active':''}" onclick="insightTab='news';renderInsight()">📰 新闻</button><button class="section-tab ${insightTab==='tech'?'active':''}" onclick="insightTab='tech';renderInsight()">📈 技术</button><button class="section-tab ${insightTab==='macro'?'active':''}" onclick="insightTab='macro';renderInsight()">🏛️ 宏观</button></div><div id="insightContent"><div style="text-align:center;padding:40px;color:var(--text2)"><div class="loading-spinner" style="width:32px;height:32px;margin:0 auto 12px;border-width:3px"></div><div id="loadingMsg" style="margin-top:8px">正在加载市场数据...</div><div style="font-size:12px;color:var(--text3,#94a3b8);margin-top:8px">☁️ 免费云服务器，首次加载可能需要 10~30 秒</div></div></div></div>`;
if(!API_AVAILABLE){document.getElementById('insightContent').innerHTML='<div style="text-align:center;padding:40px;color:var(--text2)">后端离线，请启动后端服务获取实时数据</div>';return}
// 加载进度动态提示
const loadStart=Date.now();const loadTimer=setInterval(()=>{const el=document.getElementById('loadingMsg');if(!el){clearInterval(loadTimer);return}const sec=Math.round((Date.now()-loadStart)/1000);if(sec>=5&&sec<15)el.textContent='正在从数据源抓取实时行情...';else if(sec>=15&&sec<25)el.textContent='数据量较大，还在努力加载中...';else if(sec>=25)el.textContent='快好了，感谢耐心等待 🙏'},3000);
const dash=await fetchDashboard();clearInterval(loadTimer);if(!dash){document.getElementById('insightContent').innerHTML='<div style="text-align:center;padding:40px;color:var(--text2)">数据加载失败，请稍后再试<br><button onclick="renderInsight()" style="margin-top:12px;padding:8px 20px;border-radius:8px;border:none;background:var(--accent);color:#fff;cursor:pointer">🔄 重新加载</button></div>';return}
const el=document.getElementById('insightContent');if(!el)return;
if(insightTab==='overview')renderInsightOverview(el,dash);
else if(insightTab==='news')renderInsightNews(el,dash);
else if(insightTab==='tech')renderInsightTech(el,dash);
else if(insightTab==='macro')renderInsightMacro(el,dash)}

function renderInsightOverview(el,d){
const fgi=d.fearGreed||{};const val=d.valuation||{};const tech=d.technical||{};const news=(d.news||[]).slice(0,5);const macro=(d.macro||[]).slice(0,3);
const fgiColor=fgi.score>=60?'var(--green)':fgi.score<=35?'var(--red)':'var(--accent)';
const valColor=val.percentile<30?'var(--green)':val.percentile>70?'var(--red)':'var(--accent)';
const valPct=Math.min(Math.max(val.percentile||50,0),100);
const dims=fgi.dimensions||{};
el.innerHTML=`
<div class="dashboard-card" onclick="showExplain('恐惧贪婪指数','这个指数衡量市场情绪——大家是"怕得要死"还是"贪得无厌"。\\n\\n📊 怎么理解：\\n• 0~25 = 极度恐惧（别人恐惧时贪婪？）\\n• 25~45 = 恐惧\\n• 45~55 = 中性\\n• 55~75 = 贪婪\\n• 75~100 = 极度贪婪（别人贪婪时恐惧？）\\n\\n🎯 当前：${(fgi.score||50).toFixed(0)} - ${fgi.level||'中性'}\\n\\n🔍 怎么用：\\n• 巴菲特说"别人恐惧我贪婪"\\n• 极度恐惧(<25)往往是好的买入时机\\n• 极度贪婪(>75)要小心追高\\n\\n💡 但这只是参考，不是万能的买卖信号。')" style="cursor:pointer"><div class="dashboard-card-title">😱 恐惧贪婪指数 <span style="font-size:11px;color:var(--accent)">点击了解 ›</span></div>
<div class="fgi-gauge"><div class="fgi-score" style="color:${fgiColor}">${(fgi.score||50).toFixed(0)}</div><div class="fgi-label">${fgi.level||'中性'}</div></div>
${Object.keys(dims).length?`<div class="fgi-dims">${Object.values(dims).map(d=>`<div class="fgi-dim"><div class="fgi-dim-label">${d.label}</div><div class="fgi-dim-val">${d.value}</div></div>`).join('')}</div>`:''}
</div>
<div class="dashboard-card" onclick="showExplain('估值水平','估值就是看"这个市场现在贵不贵"。\\n\\n📊 核心指标 PE（市盈率）：\\n• PE = 股价 ÷ 每股收益\\n• PE 低 → 相对便宜\\n• PE 高 → 相对贵\\n\\n🔍 百分位怎么看：\\n• 当前：${val.percentile||50}%\\n• 意思是"历史上只有 ${val.percentile||50}% 的时候比现在便宜"\\n• <30% → 便宜区间，适合加仓\\n• 30~70% → 正常区间\\n• >70% → 偏贵，谨慎追高\\n\\n🎯 PE: ${val.current_pe||'-'}\\n\\n💡 一句话：估值越低，安全边际越高，长期赚钱概率越大。')" style="cursor:pointer"><div class="dashboard-card-title">📊 估值水平 <span style="font-size:11px;color:var(--accent)">点击了解 ›</span></div>
<div style="display:flex;justify-content:space-between;align-items:center"><div><div style="font-size:24px;font-weight:900;color:${valColor}">${val.percentile||50}%</div><div style="font-size:13px;color:var(--text2)">${val.index||'沪深300'}·${val.level||'适中'}</div></div><div style="text-align:right;font-size:12px;color:var(--text2)">${val.metric||''}<br>PE: ${val.current_pe||'-'}</div></div>
<div class="val-bar"><div class="val-bar-fill" style="width:${valPct}%;background:${valColor}"></div><div class="val-bar-marker" style="left:${valPct}%;background:${valColor}"></div></div>
<div style="display:flex;justify-content:space-between;font-size:11px;color:var(--text2)"><span>低估</span><span>适中</span><span>高估</span></div>
</div>
<div class="dashboard-card" onclick="insightTab='tech';renderInsight()" style="cursor:pointer"><div class="dashboard-card-title">📈 技术指标 <span style="font-size:11px;color:var(--accent)">点击查看详情 ›</span></div>
<div class="tech-grid">
<div class="tech-item"><div class="tech-label">RSI(14)</div><div class="tech-value">${tech.rsi||'-'}</div><div class="tech-signal ${tech.rsi>70?'sell':tech.rsi<30?'buy':'neutral'}">${tech.rsi_signal||'—'}</div></div>
<div class="tech-item"><div class="tech-label">MACD</div><div class="tech-value" style="font-size:12px">${tech.macd?.trend||'—'}</div></div>
<div class="tech-item"><div class="tech-label">布林带</div><div class="tech-value" style="font-size:11px">${tech.bollinger?.position||'—'}</div></div>
<div class="tech-item"><div class="tech-label">现价</div><div class="tech-value">${tech.bollinger?.current||'—'}</div></div>
</div></div>
${news.length?`<div class="dashboard-card"><div class="dashboard-card-title">📰 最新资讯</div>${news.map(n=>`<div class="news-item" onclick="${n.url?`window.open('${n.url}','_blank')`:''}"${n.url?'':' style="cursor:default"'}><div class="news-icon">📰</div><div class="news-content"><div class="news-title">${n.title}</div><div class="news-meta">${n.source||''}${n.time?' · '+n.time:''}</div></div>${n.url?'<div class="news-arrow">›</div>':''}</div>`).join('')}</div>`:''}
${macro.length?`<div class="dashboard-card" onclick="insightTab='macro';renderInsight()" style="cursor:pointer"><div class="dashboard-card-title">🏛️ 宏观经济 <span style="font-size:11px;color:var(--accent)">点击查看详情 ›</span></div>${macro.map(e=>`<div class="macro-item"><div class="macro-icon">${e.icon||'📅'}</div><div class="macro-info"><div class="macro-name">${e.name}</div><div class="macro-value">${e.value||'—'}</div><div class="macro-impact">${e.impact||''}</div></div><div class="news-arrow">›</div></div>`).join('')}</div>`:''}
<div style="text-align:center;font-size:11px;color:#475569;margin-top:16px">更新于 ${new Date(d.updatedAt).toLocaleString('zh-CN')}</div>`}

function renderInsightNews(el,d){
const news=d.news||[];
el.innerHTML=`<div class="dashboard-card"><div class="dashboard-card-title">📰 市场新闻</div>${news.length?news.map(n=>`<div class="news-item" onclick="${n.url?`window.open('${n.url}','_blank')`:''}"${n.url?'':' style="cursor:default"'}><div class="news-icon">📰</div><div class="news-content"><div class="news-title">${n.title}</div><div class="news-meta">${n.source||''}${n.time?' · '+n.time:''}</div></div>${n.url?'<div class="news-arrow">›</div>':''}</div>`).join(''):'<div style="text-align:center;padding:20px;color:var(--text2)">暂无新闻</div>'}</div>
<div style="text-align:center;margin-top:12px"><button class="action-btn secondary" style="display:inline-block;min-width:auto;padding:10px 24px" onclick="renderInsight()">🔄 刷新</button></div>`}

function renderInsightTech(el,d){
const tech=d.technical||{};const m=tech.macd||{};const b=tech.bollinger||{};
el.innerHTML=`
<div class="dashboard-card" onclick="showExplain('RSI 相对强弱指标','RSI 就像温度计，测量市场的"热度"。\\n\\n📊 数值含义：\\n• 50 = 中性，多空力量平衡\\n• 超过 70 = 市场过热（大家都在买，可能快到顶了）\\n• 低于 30 = 市场过冷（大家都在卖，可能快到底了）\\n\\n🎯 当前 RSI = ${tech.rsi||50}\\n${tech.rsi>70?'⚠️ 偏高，短期可能回调，不宜追涨':tech.rsi<30?'💡 偏低，可能被超卖，可以关注抄底机会':'✅ 中性区间，市场情绪正常'}\\n\\n💡 小贴士：RSI 不能单独使用，要结合其他指标一起看。')" style="cursor:pointer"><div class="dashboard-card-title">📊 RSI 相对强弱指标 <span style="font-size:11px;color:var(--accent)">点击了解 ›</span></div>
<div style="text-align:center"><div style="font-size:48px;font-weight:900;color:${tech.rsi>70?'var(--red)':tech.rsi<30?'var(--green)':'var(--accent)'}">${tech.rsi||50}</div>
<div style="font-size:14px;color:var(--text2);margin-top:4px">${tech.rsi_signal||'中性'}</div>
<div class="val-bar" style="margin:12px 0"><div class="val-bar-fill" style="width:${tech.rsi||50}%;background:${tech.rsi>70?'var(--red)':tech.rsi<30?'var(--green)':'var(--accent)'}"></div></div>
<div style="display:flex;justify-content:space-between;font-size:11px;color:var(--text2)"><span>超卖 (&lt;30)</span><span>中性</span><span>超买 (&gt;70)</span></div></div></div>
<div class="dashboard-card" onclick="showExplain('MACD 指标','MACD 就像两条"均线赛跑"——快线(DIF)和慢线(DEA)。\\n\\n📊 核心概念：\\n• DIF(快线) 和 DEA(慢线) 是两条趋势线\\n• MACD柱 = 两线差值，代表动能强弱\\n\\n🔍 怎么看：\\n• 金叉（快线上穿慢线）→ 可能要涨\\n• 死叉（快线下穿慢线）→ 可能要跌\\n• 柱子变长 = 趋势在加强\\n• 柱子变短 = 趋势在减弱\\n\\n🎯 当前：${m.trend||'—'}\\nDIF=${m.dif?.toFixed(2)||'—'} DEA=${m.dea?.toFixed(2)||'—'}\\n\\n💡 小贴士：MACD 反应较慢，适合看中长期趋势，不适合抓短线。')" style="cursor:pointer"><div class="dashboard-card-title">📈 MACD 指数平滑移动平均 <span style="font-size:11px;color:var(--accent)">点击了解 ›</span></div>
<div class="tech-grid"><div class="tech-item"><div class="tech-label">趋势</div><div class="tech-value" style="font-size:13px;color:${m.trend?.includes('金叉')||m.trend?.includes('多头')?'var(--green)':'var(--red)'}">${m.trend||'—'}</div></div>
<div class="tech-item"><div class="tech-label">DIF</div><div class="tech-value">${m.dif?.toFixed(2)||'—'}</div></div>
<div class="tech-item"><div class="tech-label">DEA</div><div class="tech-value">${m.dea?.toFixed(2)||'—'}</div></div>
<div class="tech-item"><div class="tech-label">MACD柱</div><div class="tech-value" style="color:${m.macd>0?'var(--green)':'var(--red)'}">${m.macd?.toFixed(2)||'—'}</div></div></div></div>
<div class="dashboard-card" onclick="showExplain('布林带','布林带就像给股价画了一条"通道"，有上中下三条线。\\n\\n📊 三条线：\\n• 上轨(${b.upper||'—'}) = 压力线，价格碰到容易回落\\n• 中轨(${b.middle||'—'}) = 移动平均线，大趋势方向\\n• 下轨(${b.lower||'—'}) = 支撑线，价格碰到容易反弹\\n\\n🔍 怎么看：\\n• 现价在上轨附近 → 偏贵，可能回调\\n• 现价在下轨附近 → 偏便宜，可能反弹\\n• 通道变窄 → 即将有大波动（变盘信号）\\n• 通道变宽 → 波动剧烈，注意风险\\n\\n🎯 当前：现价 ${b.current||'—'}，${b.position||''}\\n\\n💡 小贴士：布林带帮你判断价格"贵不贵"，但不能预测方向。')" style="cursor:pointer"><div class="dashboard-card-title">📐 布林带 <span style="font-size:11px;color:var(--accent)">点击了解 ›</span></div>
<div class="tech-grid"><div class="tech-item"><div class="tech-label">上轨</div><div class="tech-value">${b.upper||'—'}</div></div>
<div class="tech-item"><div class="tech-label">中轨</div><div class="tech-value">${b.middle||'—'}</div></div>
<div class="tech-item"><div class="tech-label">下轨</div><div class="tech-value">${b.lower||'—'}</div></div>
<div class="tech-item"><div class="tech-label">现价</div><div class="tech-value">${b.current||'—'}</div></div></div>
<div style="text-align:center;margin-top:12px;font-size:13px;color:var(--text2)">${b.position||''}</div></div>
<div style="text-align:center;padding:16px;font-size:12px;color:#475569;line-height:1.6">💡 点击任意卡片查看白话解释 · 技术指标是辅助参考，需结合估值和基本面综合判断</div>`}

function renderInsightMacro(el,d){
const macro=d.macro||[];
const explainMap={'CPI 居民消费价格指数':'CPI 就是"物价涨了多少"。\\n\\n📊 怎么理解：\\n• CPI = 0% → 物价没变\\n• CPI > 0% → 东西涨价了（通胀）\\n• CPI < 0% → 东西降价了（通缩）\\n\\n🔍 对你的影响：\\n• CPI 涨太快(>3%) → 央行可能加息 → 存款收益↑，股市债市承压\\n• CPI 下降/为负 → 央行可能降息 → 贷款便宜，利好股市和房产\\n\\n💡 一句话：CPI 涨，你手里的钱在贬值；CPI 跌，你的钱更值钱了。','PMI 采购经理指数':'PMI 就是"企业老板们觉得生意怎么样"。\\n\\n📊 怎么理解：\\n• PMI = 50 是分水岭\\n• PMI > 50 → 多数企业觉得在扩张，经济向好\\n• PMI < 50 → 多数企业觉得在收缩，经济下行\\n\\n🔍 对你的影响：\\n• PMI 连续 > 50 → 经济回暖，股市通常表现较好\\n• PMI 连续 < 50 → 经济承压，投资需谨慎\\n\\n💡 一句话：PMI 是经济的"体温计"，>50 说明经济在"发烧式增长"。','M2 广义货币供应量':'M2 就是"市场上钱的总量"。\\n\\n📊 怎么理解：\\n• M2 增速高 → 央行在"放水"，市场上钱多\\n• M2 增速低 → 央行在"收水"，市场上钱紧\\n\\n🔍 对你的影响：\\n• M2 增速上升 → 钱多了要找去处，利好股市和房产\\n• M2 增速下降 → 资金收紧，资产价格可能承压\\n\\n💡 一句话：M2 就是印钞机的"速度表"，转得越快，资产越容易涨。','PPI 工业生产者出厂价格指数':'PPI 就是"工厂出货价涨了多少"。\\n\\n📊 怎么理解：\\n• PPI > 0% → 工厂涨价了（原材料贵了）\\n• PPI < 0% → 工厂降价了（需求不足）\\n\\n🔍 和 CPI 的关系：\\n• PPI 是"上游"，CPI 是"下游"\\n• PPI 涨 → 几个月后 CPI 也可能涨（成本传导）\\n• PPI 领先 CPI，是通胀的"早期预警"\\n\\n💡 一句话：PPI 涨了，你的生活成本迟早也会涨。'};
el.innerHTML=`<div class="dashboard-card"><div class="dashboard-card-title">🏛️ 宏观经济数据 <span style="font-size:11px;color:var(--accent)">点击查看白话解释</span></div>
${macro.length?macro.map(e=>{const key=Object.keys(explainMap).find(k=>e.name.includes(k.split(' ')[0]))||e.name;const explain=explainMap[key]||('📊 '+e.name+'\\n\\n'+e.impact+'\\n\\n点击查看更多：可在百度搜索「'+e.name+' 最新数据」了解详情。');return`<div class="macro-item" onclick="showExplain('${e.name}','${explain}')" style="cursor:pointer"><div class="macro-icon">${e.icon||'📅'}</div><div class="macro-info"><div class="macro-name">${e.name}</div><div class="macro-value">${e.value||'—'}</div><div class="macro-date">${e.date||''}</div><div class="macro-impact">${e.impact||''}</div></div><div class="news-arrow">›</div></div>`}).join(''):'<div style="text-align:center;padding:20px;color:var(--text2)">暂无数据</div>'}
</div>
<div style="padding:16px;font-size:12px;color:#475569;line-height:1.6">💡 点击任意数据查看白话解释 · 宏观数据影响市场整体方向</div>`}

// ---- 记账页 ----
function renderLedger(){currentPage='ledger';renderNav();const entries=loadLedger();const sources=loadSources();
const totalExpense=entries.filter(e=>e.direction!=='income').reduce((s,e)=>s+(e.amount||0),0);
const totalIncome=entries.filter(e=>e.direction==='income').reduce((s,e)=>s+(e.amount||0),0);
const netFlow=totalIncome-totalExpense;
// 本月统计
const now=new Date();const monthStart=new Date(now.getFullYear(),now.getMonth(),1).toISOString();
const monthEntries=entries.filter(e=>e.date>=monthStart);
const monthIncome=monthEntries.filter(e=>e.direction==='income').reduce((s,e)=>s+(e.amount||0),0);
const monthExpense=monthEntries.filter(e=>e.direction!=='income').reduce((s,e)=>s+(e.amount||0),0);
// 按方向+分类汇总
const expCat={},incCat={};
entries.forEach(e=>{const c=e.category||'其他';const a=e.amount||0;if(e.direction==='income'){incCat[c]=(incCat[c]||0)+a}else{expCat[c]=(expCat[c]||0)+a}});
const curIcons=ledgerDirection==='income'?INCOME_ICONS:EXPENSE_ICONS;
// 收入源本月入账情况
const monthSourceIds=new Set(monthEntries.filter(e=>e.sourceId).map(e=>e.sourceId));

$('#app').innerHTML=`<div class="ledger-page fade-up">
<div class="ledger-header">
<div style="display:flex;gap:16px;justify-content:center;margin-bottom:8px">
<div style="text-align:center"><div style="font-size:12px;color:var(--text2)">总收入</div><div style="font-size:18px;font-weight:700;color:var(--green)">+${fmtFull(Math.round(totalIncome))}</div></div>
<div style="text-align:center"><div style="font-size:12px;color:var(--text2)">总支出</div><div style="font-size:18px;font-weight:700;color:var(--red)">-${fmtFull(Math.round(totalExpense))}</div></div>
<div style="text-align:center"><div style="font-size:12px;color:var(--text2)">结余</div><div style="font-size:18px;font-weight:700;color:${netFlow>=0?'var(--green)':'var(--red)'}">${netFlow>=0?'+':''}${fmtFull(Math.round(netFlow))}</div></div>
</div>
<div style="display:flex;gap:12px;justify-content:center;font-size:12px;color:var(--text2);padding:6px 0;border-top:1px solid var(--border)">
<span>本月收入 <b style="color:var(--green)">+¥${Math.round(monthIncome)}</b></span>
<span>本月支出 <b style="color:var(--red)">-¥${Math.round(monthExpense)}</b></span>
</div>
<div style="font-size:13px;color:var(--text2)">${entries.length}笔记录</div></div>

${sources.length?`<div class="section-title" style="display:flex;align-items:center;justify-content:space-between">📋 我的收入源<button style="background:none;border:none;color:var(--accent);font-size:13px;cursor:pointer" onclick="showAddSource()">+ 添加</button></div>
<div style="display:flex;flex-direction:column;gap:8px;margin-bottom:16px">
${sources.map(s=>{
const icon=SOURCE_TYPE_ICONS[s.type]||'💵';
const recorded=monthSourceIds.has(s.id);
const lastT=s.lastRecordAt?new Date(s.lastRecordAt).toLocaleDateString('zh-CN'):'从未入账';
return`<div style="background:var(--card);border:1px solid var(--border);border-radius:12px;padding:12px;display:flex;align-items:center;gap:10px">
<div style="font-size:24px;width:36px;text-align:center">${icon}</div>
<div style="flex:1;min-width:0">
<div style="font-weight:600;font-size:14px;display:flex;align-items:center;gap:6px">${s.name}${recorded?'<span style="font-size:10px;background:rgba(16,185,129,.15);color:var(--green);padding:1px 6px;border-radius:4px">本月已入账</span>':''}</div>
<div style="font-size:12px;color:var(--text2)">${s.type} · 预期¥${s.expectedAmt}/月 · ${lastT}</div>
${s.recordCount?`<div style="font-size:11px;color:var(--text2)">累计${s.recordCount}次 · ¥${Math.round(s.totalRecorded)}</div>`:''}
</div>
<div style="display:flex;gap:6px;flex-shrink:0">
<button style="background:${recorded?'var(--border)':'var(--green)'};border:none;color:${recorded?'var(--text2)':'#fff'};padding:6px 12px;border-radius:8px;font-size:12px;font-weight:600;cursor:pointer" onclick="quickRecord('${s.id}')">${recorded?'再记一笔':'💰 入账'}</button>
<button style="background:none;border:none;color:var(--text2);font-size:16px;cursor:pointer;padding:4px" onclick="if(confirm('删除收入源「${s.name}」？')){deleteSource('${s.id}');renderLedger()}">🗑️</button>
</div></div>`}).join('')}
</div>`:`<div class="section-title" style="display:flex;align-items:center;justify-content:space-between">📋 我的收入源<button style="background:none;border:none;color:var(--accent);font-size:13px;cursor:pointer" onclick="showAddSource()">+ 添加</button></div>
<div style="background:var(--card);border:1px dashed var(--border);border-radius:12px;padding:20px;text-align:center;margin-bottom:16px;cursor:pointer" onclick="showAddSource()">
<div style="font-size:24px;margin-bottom:4px">🏡💻🔧</div>
<div style="font-size:13px;color:var(--text2)">登记你的收入来源（民宿/出租房/外包/兼职…）<br>登记一次，以后每月一键入账</div>
</div>`}

<div id="addSourceForm" style="display:none"></div>

${Object.keys(expCat).length||Object.keys(incCat).length?`
<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px">
${Object.entries(incCat).map(([c,a])=>`<div class="ledger-cat" style="border-color:rgba(16,185,129,.3);background:rgba(16,185,129,.06)"><div class="ledger-cat-icon">${INCOME_ICONS[c]||'💵'}</div><div class="ledger-cat-name">${c}</div><div class="ledger-cat-amt" style="color:var(--green)">+¥${Math.round(a)}</div></div>`).join('')}
${Object.entries(expCat).map(([c,a])=>`<div class="ledger-cat"><div class="ledger-cat-icon">${EXPENSE_ICONS[c]||'📌'}</div><div class="ledger-cat-name">${c}</div><div class="ledger-cat-amt">¥${Math.round(a)}</div></div>`).join('')}
</div>`:''
}
<div class="section-title">📸 拍照/截图记账</div>
<div class="upload-area" onclick="document.getElementById('rcptFile').click()"><div class="icon">📷</div><div class="text">拍照或上传截图，AI自动识别入账<br><span style="font-size:12px;color:var(--text2)">支持：支付宝/微信账单、银行流水截图、消费小票</span></div><input type="file" id="rcptFile" accept="image/*" style="display:none" onchange="handleReceipt(this)"></div><div id="ocrRes"></div>
<div class="section-title">✏️ 手动记一笔</div>
<div class="manual-form">
<div style="display:flex;gap:0;margin-bottom:12px;border-radius:8px;overflow:hidden;border:1px solid var(--border)">
<button id="btnExpense" style="flex:1;padding:10px;border:none;font-size:14px;font-weight:600;cursor:pointer;transition:all .2s;background:${ledgerDirection==='expense'?'var(--red)':'transparent'};color:${ledgerDirection==='expense'?'#fff':'var(--text2)'}" onclick="switchLedgerDir('expense')">💸 支出</button>
<button id="btnIncome" style="flex:1;padding:10px;border:none;font-size:14px;font-weight:600;cursor:pointer;transition:all .2s;background:${ledgerDirection==='income'?'var(--green)':'transparent'};color:${ledgerDirection==='income'?'#fff':'var(--text2)'}" onclick="switchLedgerDir('income')">💰 收入</button>
</div>
<div class="form-row"><div class="form-label">金额</div><input class="form-input" type="number" id="ldgAmt" placeholder="0.00" inputmode="decimal"></div>
<div class="form-row"><div class="form-label">分类</div><select class="form-select" id="ldgCat">${Object.keys(curIcons).map(c=>`<option value="${c}">${curIcons[c]} ${c}</option>`).join('')}</select></div>
<div class="form-row"><div class="form-label">备注</div><input class="form-input" type="text" id="ldgNote" placeholder="${ledgerDirection==='income'?'收入来源...':'买了什么...'}"></div>
<button class="form-submit" style="background:${ledgerDirection==='income'?'var(--green)':'var(--accent)'}" onclick="addEntry()">${ledgerDirection==='income'?'💰 记一笔收入':'💸 记一笔支出'}</button></div>
${entries.length?`<div class="section-title">📋 最近记录</div>${entries.slice(-30).reverse().map(e=>{
const isInc=e.direction==='income';
const icon=isInc?(INCOME_ICONS[e.category]||'💵'):(EXPENSE_ICONS[e.category]||'📌');
return`<div class="ledger-entry"><div class="ledger-entry-icon">${icon}</div><div class="ledger-entry-info"><div class="ledger-entry-note">${e.note||e.category}${isInc?' <span style="font-size:11px;background:rgba(16,185,129,.15);color:var(--green);padding:1px 6px;border-radius:4px">收入</span>':''}${e.source==='income_source'?' <span style="font-size:11px;background:rgba(99,102,241,.15);color:#818cf8;padding:1px 6px;border-radius:4px">定期</span>':''}</div><div class="ledger-entry-date">${new Date(e.date).toLocaleString('zh-CN')}</div></div><div class="ledger-entry-amt" style="color:${isInc?'var(--green)':'var(--red)'}">${isInc?'+':'-'}¥${e.amount.toFixed(2)}</div></div>`}).join('')}`:'<div style="text-align:center;color:var(--text2);padding:32px">还没有记录，拍张小票试试📸</div>'}
${entries.length?'<div class="bottom-actions" style="margin-top:20px"><button class="action-btn secondary" onclick="clearLedger()">🗑️清除记录</button></div>':''}</div>`}

function showAddSource(){
const f=document.getElementById('addSourceForm');if(!f)return;
const types=Object.keys(SOURCE_TYPE_ICONS);
f.style.display='block';
f.innerHTML=`<div style="background:var(--card);border:1px solid var(--accent);border-radius:12px;padding:16px;margin-bottom:16px">
<div style="font-weight:700;font-size:14px;margin-bottom:12px">➕ 登记新收入源</div>
<div class="form-row"><div class="form-label">名称</div><input class="form-input" type="text" id="srcName" placeholder="如：朝阳区民宿、张三外包、滴滴兼职..."></div>
<div class="form-row"><div class="form-label">类型</div><select class="form-select" id="srcType">${types.map(t=>`<option value="${t}">${SOURCE_TYPE_ICONS[t]} ${t}</option>`).join('')}</select></div>
<div class="form-row"><div class="form-label">预期月收入</div><input class="form-input" type="number" id="srcAmt" placeholder="每月大概赚多少" inputmode="decimal"></div>
<div class="form-row"><div class="form-label">备注</div><input class="form-input" type="text" id="srcNote" placeholder="可选"></div>
<div style="display:flex;gap:8px;margin-top:12px">
<button class="form-submit" style="flex:1;background:var(--green)" onclick="confirmAddSource()">✅ 登记</button>
<button class="form-submit" style="flex:1;background:var(--border);color:var(--text2)" onclick="document.getElementById('addSourceForm').style.display='none'">取消</button>
</div></div>`}

function confirmAddSource(){
const name=document.getElementById('srcName')?.value?.trim();
const type=document.getElementById('srcType')?.value;
const amt=document.getElementById('srcAmt')?.value;
const note=document.getElementById('srcNote')?.value;
if(!name){alert('请输入收入源名称');return}
addSource(name,type,amt,note);
renderLedger()}

function switchLedgerDir(dir){ledgerDirection=dir;renderLedger()}

async function handleReceipt(input){if(!input.files||!input.files[0])return;const file=input.files[0];const re=document.getElementById('ocrRes');if(!re)return;
re.innerHTML='<div style="text-align:center;padding:16px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:3px"></div>AI识别中...</div>';
if(API_AVAILABLE){try{const fd=new FormData();fd.append('file',file);fd.append('userId',getUserId());const r=await fetch(API_BASE+'/receipt/ocr',{method:'POST',body:fd});if(r.ok){const d=await r.json();if(d.amount>0){const es=loadLedger();es.push({date:new Date().toISOString(),amount:d.amount,category:d.category||'其他',note:d.merchant||d.note||'OCR',source:'ocr'});saveLedger(es);re.innerHTML=`<div style="background:rgba(16,185,129,.1);border:1px solid rgba(16,185,129,.3);border-radius:12px;padding:16px;margin-bottom:16px"><div style="font-weight:700;color:var(--green);margin-bottom:8px">✅ 已入账</div><div style="font-size:13px;color:var(--text2)">¥${d.amount.toFixed(2)} · ${d.category||'其他'}${d.merchant?' · '+d.merchant:''}</div></div>`;setTimeout(()=>renderLedger(),2000);return}}}catch(e){console.error(e)}}
re.innerHTML=`<div style="background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.3);border-radius:12px;padding:16px"><div style="color:var(--red)">识别失败</div><div style="font-size:13px;color:var(--text2);margin-top:4px">${API_AVAILABLE?'无法识别，请手动输入':'后端离线，请手动输入'}</div></div>`}

function addEntry(){const a=parseFloat(document.getElementById('ldgAmt')?.value);const c=document.getElementById('ldgCat')?.value||'其他';const n=document.getElementById('ldgNote')?.value||'';if(!a||a<=0){alert('请输入金额');return}
const es=loadLedger();es.push({date:new Date().toISOString(),amount:a,category:c,note:n,direction:ledgerDirection,source:'manual'});saveLedger(es);
if(API_AVAILABLE)fetch(API_BASE+'/ledger/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:getUserId(),amount:a,category:c,note:n,direction:ledgerDirection})}).catch(()=>{});
renderLedger()}
function clearLedger(){if(confirm('确定清除所有记账记录？')){localStorage.removeItem(LEDGER_KEY);renderLedger()}}

function toggleLedgerPanel(){
const panel=document.getElementById('ledgerPanelInAssets');
if(!panel)return;
if(panel.style.display!=='none'){panel.style.display='none';return}
panel.style.display='block';
const entries=loadLedger();const sources=loadSources();
const totalIncome=entries.filter(e=>e.direction==='income').reduce((s,e)=>s+(e.amount||0),0);
const totalExpense=entries.filter(e=>e.direction!=='income').reduce((s,e)=>s+(e.amount||0),0);
const curIcons=ledgerDirection==='income'?INCOME_ICONS:EXPENSE_ICONS;
const now=new Date();const monthStart=new Date(now.getFullYear(),now.getMonth(),1).toISOString();
const monthEntries=entries.filter(e=>e.date>=monthStart);
const monthIncome=monthEntries.filter(e=>e.direction==='income').reduce((s,e)=>s+(e.amount||0),0);
const monthExpense=monthEntries.filter(e=>e.direction!=='income').reduce((s,e)=>s+(e.amount||0),0);
const monthSourceIds=new Set(monthEntries.filter(e=>e.sourceId).map(e=>e.sourceId));

panel.innerHTML=`<div style="background:var(--card);border:1px solid var(--border);border-radius:16px;padding:16px">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
<div style="font-weight:700;font-size:16px">📝 记账</div>
<button style="background:none;border:none;color:var(--text2);font-size:18px;cursor:pointer;padding:4px" onclick="document.getElementById('ledgerPanelInAssets').style.display='none'">✕</button>
</div>
<div style="display:flex;gap:12px;justify-content:center;margin-bottom:12px;font-size:12px;color:var(--text2)">
<span>本月收入 <b style="color:var(--green)">+¥${Math.round(monthIncome)}</b></span>
<span>本月支出 <b style="color:var(--red)">-¥${Math.round(monthExpense)}</b></span>
<span>总结余 <b style="color:${totalIncome-totalExpense>=0?'var(--green)':'var(--red)'}">¥${Math.round(totalIncome-totalExpense)}</b></span>
</div>

${sources.length?`<div style="margin-bottom:12px">
<div style="font-size:13px;font-weight:600;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center">📋 收入源<button style="background:none;border:none;color:var(--accent);font-size:12px;cursor:pointer" onclick="showAddSource()">+ 添加</button></div>
${sources.map(s=>{const icon=SOURCE_TYPE_ICONS[s.type]||'💵';const recorded=monthSourceIds.has(s.id);
return`<div style="display:flex;align-items:center;gap:8px;padding:8px;background:var(--bg2);border-radius:8px;margin-bottom:4px">
<span>${icon}</span><span style="flex:1;font-size:13px">${s.name}</span>
<button style="background:${recorded?'var(--border)':'var(--green)'};border:none;color:${recorded?'var(--text2)':'#fff'};padding:4px 10px;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer" onclick="quickRecord('${s.id}')">${recorded?'再记':'💰 入账'}</button>
<button style="background:none;border:none;color:var(--text2);font-size:14px;cursor:pointer" onclick="if(confirm('删除收入源「${s.name}」？')){deleteSource('${s.id}');toggleLedgerPanel();toggleLedgerPanel()}">🗑️</button>
</div>`}).join('')}
</div>`:
`<div style="text-align:center;padding:8px;margin-bottom:8px;border:1px dashed var(--border);border-radius:8px;cursor:pointer;font-size:12px;color:var(--text2)" onclick="showAddSource()">🏡💻🔧 登记收入源（一键入账）</div>`}

<div style="margin-bottom:12px">
<div style="font-size:13px;font-weight:600;margin-bottom:8px">📸 拍照/截图记账</div>
<div class="upload-area" style="padding:12px" onclick="document.getElementById('rcptFileAsset').click()"><div class="icon" style="font-size:20px">📷</div><div class="text" style="font-size:12px">拍照或上传截图，AI自动识别<br><span style="font-size:11px;color:var(--text2)">支付宝/微信/银行流水</span></div><input type="file" id="rcptFileAsset" accept="image/*" style="display:none" onchange="handleReceipt(this)"></div><div id="ocrRes"></div>
</div>

<div>
<div style="font-size:13px;font-weight:600;margin-bottom:8px">✏️ 手动记一笔</div>
<div style="display:flex;gap:0;margin-bottom:8px;border-radius:8px;overflow:hidden;border:1px solid var(--border)">
<button id="btnExpenseAsset" style="flex:1;padding:8px;border:none;font-size:13px;font-weight:600;cursor:pointer;background:${ledgerDirection==='expense'?'var(--red)':'transparent'};color:${ledgerDirection==='expense'?'#fff':'var(--text2)'}" onclick="ledgerDirection='expense';toggleLedgerPanel();toggleLedgerPanel()">💸 支出</button>
<button id="btnIncomeAsset" style="flex:1;padding:8px;border:none;font-size:13px;font-weight:600;cursor:pointer;background:${ledgerDirection==='income'?'var(--green)':'transparent'};color:${ledgerDirection==='income'?'#fff':'var(--text2)'}" onclick="ledgerDirection='income';toggleLedgerPanel();toggleLedgerPanel()">💰 收入</button>
</div>
<div class="form-row" style="margin-bottom:8px"><input class="form-input" type="number" id="ldgAmtAsset" placeholder="金额" inputmode="decimal" style="padding:10px"></div>
<div style="display:flex;gap:8px;margin-bottom:8px">
<select class="form-select" id="ldgCatAsset" style="flex:1;padding:10px">${Object.keys(curIcons).map(c=>`<option value="${c}">${curIcons[c]} ${c}</option>`).join('')}</select>
<input class="form-input" type="text" id="ldgNoteAsset" placeholder="备注" style="flex:1;padding:10px">
</div>
<button class="form-submit" style="width:100%;background:${ledgerDirection==='income'?'var(--green)':'var(--accent)'}" onclick="addEntryFromAsset()">${ledgerDirection==='income'?'💰 记一笔收入':'💸 记一笔支出'}</button>
</div>

${entries.length?`<div style="margin-top:12px"><div style="font-size:13px;font-weight:600;margin-bottom:8px">📋 最近记录 (${entries.length}笔)</div>
${entries.slice(-10).reverse().map(e=>{const isInc=e.direction==='income';const icon=isInc?(INCOME_ICONS[e.category]||'💵'):(EXPENSE_ICONS[e.category]||'📌');
return`<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--bg3);font-size:13px">
<span>${icon}</span><span style="flex:1">${e.note||e.category}</span>
<span style="font-weight:700;color:${isInc?'var(--green)':'var(--red)'}">${isInc?'+':'-'}¥${e.amount.toFixed(2)}</span>
<span style="font-size:10px;color:var(--text2)">${new Date(e.date).toLocaleDateString('zh-CN')}</span></div>`}).join('')}
${entries.length>10?`<div style="text-align:center;padding:8px;font-size:12px;color:var(--text2)">还有${entries.length-10}笔...</div>`:''}
<button style="width:100%;margin-top:8px;padding:8px;border-radius:8px;border:1px solid var(--border);background:transparent;color:var(--red);font-size:12px;cursor:pointer" onclick="if(confirm('确定清除所有记账记录？')){localStorage.removeItem('${LEDGER_KEY}');renderAssets()}">🗑️ 清除记录</button>
</div>`:''}
</div>`}

function addEntryFromAsset(){
const a=parseFloat(document.getElementById('ldgAmtAsset')?.value);
const c=document.getElementById('ldgCatAsset')?.value||'其他';
const n=document.getElementById('ldgNoteAsset')?.value||'';
if(!a||a<=0){alert('请输入金额');return}
const es=loadLedger();es.push({date:new Date().toISOString(),amount:a,category:c,note:n,direction:ledgerDirection,source:'manual'});saveLedger(es);
if(API_AVAILABLE)fetch(API_BASE+'/ledger/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:getUserId(),amount:a,category:c,note:n,direction:ledgerDirection})}).catch(()=>{});
renderAssets();setTimeout(()=>{toggleLedgerPanel()},100)}

// ---- 弹窗 ----
function showFundDetail(code){const d=FUND_DETAILS[code];if(!d)return;const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};const nav=liveNavData[code];
o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()"><div class="modal-handle"></div><div class="modal-title">${d.fullName}</div><div class="modal-subtitle">${d.type} · ${d.company} · ${d.risk}</div>
<div class="modal-section"><div class="modal-section-title">💡 为什么推荐</div><div class="modal-reason">${d.reason}</div></div>
<div class="modal-section"><div class="modal-section-title">📊 历史业绩</div><div class="modal-history-grid"><div class="modal-history-item"><div class="modal-history-label">近1年</div><div class="modal-history-value">${d.history.y1}</div></div><div class="modal-history-item"><div class="modal-history-label">近3年</div><div class="modal-history-value">${d.history.y3}</div></div><div class="modal-history-item"><div class="modal-history-label">近5年</div><div class="modal-history-value">${d.history.y5}</div></div></div></div>
<div class="modal-section"><div class="modal-section-title">📋 基本信息</div><div class="modal-stat-grid"><div class="modal-stat"><div class="modal-stat-label">规模</div><div class="modal-stat-value">${d.scale}</div></div><div class="modal-stat"><div class="modal-stat-label">成立</div><div class="modal-stat-value">${d.founded}</div></div><div class="modal-stat"><div class="modal-stat-label">费率</div><div class="modal-stat-value" style="font-size:12px">${d.fee}</div></div><div class="modal-stat"><div class="modal-stat-label">跟踪</div><div class="modal-stat-value" style="font-size:12px">${d.tracking}</div></div></div></div>
${nav?`<div class="modal-section"><div class="modal-section-title">📡 实时</div><div class="modal-stat-grid"><div class="modal-stat"><div class="modal-stat-label">净值</div><div class="modal-stat-value" style="color:var(--green)">${nav.nav}</div></div><div class="modal-stat"><div class="modal-stat-label">涨跌</div><div class="modal-stat-value" style="color:${parseFloat(nav.change)>=0?'var(--green)':'var(--red)'}">${nav.change}%</div></div></div></div>`:''}
<div class="modal-section" id="fundNewsSection_${code}"><div class="modal-section-title">📰 相关新闻</div><div style="text-align:center;padding:12px;font-size:12px;color:var(--text2)">加载中...</div></div>
<div class="modal-section"><div class="modal-section-title">🛒 怎么买</div><div class="modal-buy-tip">✅ ${d.buyTip}</div></div>
<div class="modal-section"><div class="modal-tags">${d.tags.map(t=>`<span class="modal-tag">${t}</span>`).join('')}</div></div></div>`;document.body.appendChild(o);
// 异步加载基金新闻
if(API_AVAILABLE&&code!=='余额宝'){fetchFundNews(code).then(news=>{const ne=document.getElementById('fundNewsSection_'+code);if(ne&&news.length){ne.innerHTML='<div class="modal-section-title">📰 相关新闻</div>'+news.map(n=>`<div class="news-item"><div class="news-icon">📰</div><div class="news-content"><div class="news-title" style="font-size:13px">${n.title}</div><div class="news-meta">${n.source||''}${n.time?' · '+n.time:''}</div></div></div>`).join('')}else if(ne){ne.innerHTML='<div class="modal-section-title">📰 相关新闻</div><div style="font-size:12px;color:var(--text2);padding:8px">暂无相关新闻</div>'}})}}

// ---- 图表 ----
function drawAllocChart(al){const c=document.getElementById('allocChart');if(!c)return;if(chartInstance)chartInstance.destroy();chartInstance=new Chart(c,{type:'doughnut',data:{labels:al.map(a=>a.name),datasets:[{data:al.map(a=>a.pct),backgroundColor:al.map(a=>a.color),borderColor:'#1E293B',borderWidth:3}]},options:{responsive:true,maintainAspectRatio:true,cutout:'55%',plugins:{legend:{display:false}}}})}
function drawProjChart(amt,rate){const c=document.getElementById('projChart');if(!c)return;if(projChartInstance)projChartInstance.destroy();const yrs=['现在','1年后','2年后','3年后'];const vals=[amt];for(let i=1;i<=3;i++)vals.push(Math.round(vals[i-1]*(1+rate)));projChartInstance=new Chart(c,{type:'line',data:{labels:yrs,datasets:[{data:vals,borderColor:'#F59E0B',backgroundColor:'rgba(245,158,11,.1)',fill:true,tension:.4,pointBackgroundColor:'#F59E0B',pointRadius:6}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{ticks:{color:'#94A3B8'}},y:{ticks:{color:'#94A3B8',callback:v=>fmtMoney(v)},grid:{color:'rgba(148,163,184,.1)'}}}}})}

// ---- 事件 ----
function startQuiz(){currentPage='quiz';currentQuestion=0;answers=[];renderQuiz()}
function selectAnswer(i,score){const bs=document.querySelectorAll('.option-btn');bs[i].classList.add('selected');answers.push(score);setTimeout(()=>{currentQuestion++;currentQuestion<QUESTIONS.length?renderQuiz():renderAmountInput()},300)}
function onAmtChange(){const inp=document.getElementById('amtIn');const btn=document.getElementById('genBtn');if(!inp||!btn)return;const v=parseInt(inp.value);btn.disabled=!(v>0);selectedAmount=v||0}
function setAmt(v){const inp=document.getElementById('amtIn');if(inp){inp.value=v;selectedAmount=v;onAmtChange();document.getElementById('genBtn').disabled=false}}
function genResult(){if(!selectedAmount)return;$('#app').innerHTML='<div class="loading-screen"><div class="loading-spinner"></div><div style="color:var(--text2)">AI正在计算...</div></div>';fetchNav().finally(()=>{setTimeout(()=>{currentPage='result';renderResult()},1200)})}
function confirmPurchase(){if(!currentAllocs||!selectedAmount||!currentProfile)return;recordPurchase(currentAllocs,selectedAmount,currentProfile.name);const b=document.querySelector('.action-btn.green');if(b){b.textContent='✅ 已记录！';b.style.opacity='.6';b.style.pointerEvents='none'}}
function restart(){currentPage='landing';currentQuestion=0;answers=[];selectedAmount=0;renderLanding()}
function clearPortfolio(){if(confirm('确定清除持仓？')){localStorage.removeItem(STORAGE_KEY);restart()}}

// ---- 资产管理页 ----
const ASSET_TYPES=[
{id:'cash',icon:'💵',label:'现金/存款',color:'var(--green)'},
{id:'property',icon:'🏠',label:'房产',color:'var(--accent)'},
{id:'car',icon:'🚗',label:'车辆',color:'var(--blue)'},
{id:'insurance',icon:'🛡️',label:'保险',color:'#8B5CF6'},
{id:'other',icon:'📦',label:'其他资产',color:'var(--text2)'},
{id:'liability',icon:'💳',label:'负债/贷款',color:'var(--red)'}];

function renderAssets(){currentPage='assets';renderNav();
const assets=loadAssets();
const ledger=loadLedger();
const assetTotal=assets.filter(a=>a.type!=='liability').reduce((s,a)=>s+(a.value||0),0);
const liabTotal=assets.filter(a=>a.type==='liability').reduce((s,a)=>s+(a.value||0),0);
const ledgerIncome=ledger.filter(e=>e.direction==='income').reduce((s,e)=>s+(e.amount||0),0);
const ledgerExpense=ledger.filter(e=>e.direction!=='income').reduce((s,e)=>s+(e.amount||0),0);
const ledgerNet=ledgerIncome-ledgerExpense;
const net=assetTotal-liabTotal+ledgerNet;

$('#app').innerHTML=`<div class="result-page fade-up">
<div class="pnl-hero" style="margin-bottom:16px">
<div class="pnl-label">🏦 资产管理</div>
<div style="display:flex;gap:12px;justify-content:center;margin-top:8px;flex-wrap:wrap">
<div style="text-align:center"><div style="font-size:12px;color:var(--text2)">总资产</div><div style="font-size:20px;font-weight:900;color:var(--green)">¥${fmtMoney(Math.round(assetTotal))}</div></div>
<div style="text-align:center"><div style="font-size:12px;color:var(--text2)">总负债</div><div style="font-size:20px;font-weight:900;color:var(--red)">¥${fmtMoney(Math.round(liabTotal))}</div></div>
<div style="text-align:center"><div style="font-size:12px;color:var(--text2)">记账现金流</div><div style="font-size:20px;font-weight:900;color:${ledgerNet>=0?'var(--green)':'var(--red)'}">${ledgerNet>=0?'+':''}¥${fmtMoney(Math.round(ledgerNet))}</div></div>
<div style="text-align:center"><div style="font-size:12px;color:var(--text2)">净值</div><div style="font-size:20px;font-weight:900;color:${net>=0?'var(--accent)':'var(--red)'}">¥${fmtMoney(Math.round(net))}</div></div>
</div></div>

<div class="section-title" style="display:flex;justify-content:space-between;align-items:center">📋 我的资产<button style="background:none;border:none;color:var(--accent);font-size:13px;cursor:pointer" onclick="showAddAsset()">+ 添加</button></div>

${assets.length?assets.map(a=>{
const t=ASSET_TYPES.find(x=>x.id===a.type)||ASSET_TYPES[4];
const isLiab=a.type==='liability';
return`<div class="holding-card" style="border-left:3px solid ${t.color}">
<div class="holding-top"><div class="holding-info">
<div class="holding-name">${t.icon} ${a.name}</div>
<div class="holding-meta">${t.label}${a.note?' · '+a.note:''}</div></div>
<div class="holding-amount" style="display:flex;align-items:center;gap:8px">
<div class="holding-money" style="color:${isLiab?'var(--red)':'var(--accent)'}">${isLiab?'-':''}¥${fmtMoney(Math.round(a.value||0))}</div>
<button style="background:none;border:none;color:var(--text2);font-size:14px;cursor:pointer;padding:4px" onclick="event.stopPropagation();showEditAsset('${a.id}')">✏️</button>
<button style="background:none;border:none;color:var(--text2);font-size:14px;cursor:pointer;padding:4px" onclick="event.stopPropagation();if(confirm('删除「${a.name}」？')){deleteAsset('${a.id}')}">🗑️</button>
</div></div></div>`}).join(''):`<div style="text-align:center;padding:32px;color:var(--text2)">
<div style="font-size:48px;margin-bottom:12px">🏦</div>
<div>还没有资产记录</div>
<div style="font-size:12px;margin-top:8px">添加现金、房产、车辆、保险等</div></div>`}

<div style="display:flex;gap:10px;margin-top:16px">
<button style="flex:1;padding:14px;border-radius:12px;border:none;background:var(--accent);color:#000;font-size:15px;font-weight:700;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:6px" onclick="toggleLedgerPanel()">📝 记账</button>
<button style="flex:1;padding:14px;border-radius:12px;border:none;background:var(--card);color:var(--text);font-size:15px;font-weight:600;cursor:pointer;border:1px solid var(--border);display:flex;align-items:center;justify-content:center;gap:6px" onclick="showAddAsset()">➕ 添加资产</button>
</div>

<div id="ledgerPanelInAssets" style="display:none;margin-top:16px"></div>

<div style="text-align:center;margin-top:16px;font-size:12px;color:var(--text2);line-height:1.8">
💡 这里管理基金以外的资产<br>
基金持仓在「📊 持仓」页管理 · 记账收支自动计入净资产<br>
所有数据汇总到首页净资产仪表盘</div></div>`}

function showAddAsset(){
const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()"><div class="modal-handle"></div>
<div class="modal-title">➕ 添加资产</div>
<div class="manual-form" style="background:transparent;padding:0;margin-top:16px">
<div class="form-row"><div class="form-label">类型</div>
<select class="form-select" id="assetType">${ASSET_TYPES.map(t=>`<option value="${t.id}">${t.icon} ${t.label}</option>`).join('')}</select></div>
<div class="form-row"><div class="form-label">名称</div>
<input class="form-input" type="text" id="assetName" placeholder="如：建行存款、朝阳区房产、花呗"></div>
<div class="form-row"><div class="form-label">金额(¥)</div>
<input class="form-input" type="number" id="assetValue" placeholder="0" inputmode="decimal"></div>
<div class="form-row"><div class="form-label">备注</div>
<input class="form-input" type="text" id="assetNote" placeholder="可选"></div>
<button class="form-submit" onclick="confirmAddAsset()">✅ 添加</button>
</div></div>`;
document.body.appendChild(o)}

function confirmAddAsset(){
const type=document.getElementById('assetType')?.value;
const name=document.getElementById('assetName')?.value?.trim();
const value=parseFloat(document.getElementById('assetValue')?.value);
const note=document.getElementById('assetNote')?.value?.trim()||'';
if(!name){alert('请输入名称');return}
if(!value||value<=0){alert('请输入金额');return}
const assets=loadAssets();
assets.push({id:'ast_'+Date.now().toString(36)+'_'+Math.random().toString(36).slice(2,6),type,name,value,note,createdAt:new Date().toISOString(),updatedAt:new Date().toISOString()});
saveAssets(assets);
if(API_AVAILABLE)fetch(API_BASE+'/assets',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:getUserId(),asset:{type,name,value,note}})}).catch(()=>{});
document.querySelector('.modal-overlay')?.remove();renderAssets()}

function showEditAsset(id){
const assets=loadAssets();const a=assets.find(x=>x.id===id);if(!a)return;
const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()"><div class="modal-handle"></div>
<div class="modal-title">✏️ 编辑资产</div>
<div class="manual-form" style="background:transparent;padding:0;margin-top:16px">
<div class="form-row"><div class="form-label">名称</div>
<input class="form-input" type="text" id="editAssetName" value="${a.name}"></div>
<div class="form-row"><div class="form-label">金额(¥)</div>
<input class="form-input" type="number" id="editAssetValue" value="${a.value}" inputmode="decimal"></div>
<div class="form-row"><div class="form-label">备注</div>
<input class="form-input" type="text" id="editAssetNote" value="${a.note||''}"></div>
<button class="form-submit" onclick="confirmEditAsset('${id}')">✅ 保存</button>
</div></div>`;
document.body.appendChild(o)}

function confirmEditAsset(id){
const assets=loadAssets();const a=assets.find(x=>x.id===id);if(!a)return;
a.name=document.getElementById('editAssetName')?.value?.trim()||a.name;
a.value=parseFloat(document.getElementById('editAssetValue')?.value)||a.value;
a.note=document.getElementById('editAssetNote')?.value?.trim()||'';
a.updatedAt=new Date().toISOString();
saveAssets(assets);
if(API_AVAILABLE)fetch(API_BASE+'/assets',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:getUserId(),asset:{id:a.id,type:a.type,name:a.name,value:a.value,note:a.note}})}).catch(()=>{});
document.querySelector('.modal-overlay')?.remove();renderAssets()}

function deleteAsset(id){const assets=loadAssets().filter(a=>a.id!==id);saveAssets(assets);
if(API_AVAILABLE)fetch(API_BASE+'/assets/'+id+'?userId='+getUserId(),{method:'DELETE'}).catch(()=>{});
renderAssets()}

// ---- 启动 ----
injectStyles();
migrateV3toV4();
checkAPI().then(()=>{fetchNav();syncFromCloud()});
renderLanding();
