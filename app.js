// ============================================================
// 钱袋子 — AI 智能资产配置助手 V3
// 实时盈亏 + AI 对话 + 云端持久化 + OCR 记账
// ============================================================

// ---- API 配置 ----
const API_BASE = (() => {
  const h = location.hostname;
  if (h === 'localhost' || h === '127.0.0.1' || h.startsWith('192.168.')) return 'http://localhost:8000/api';
  return ''; // Railway 部署后替换
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
const LEDGER_KEY='moneybag_ledger';
const LEDGER_ICONS={'餐饮':'🍜','交通':'🚗','购物':'🛍️','娱乐':'🎮','医疗':'🏥','教育':'📚','其他':'📌'};

function loadPortfolio(){try{return JSON.parse(localStorage.getItem(STORAGE_KEY))||{holdings:[],history:[],profile:null,amount:0}}catch{return{holdings:[],history:[],profile:null,amount:0}}}
function savePortfolio(d){localStorage.setItem(STORAGE_KEY,JSON.stringify(d));syncCloud(d)}
function loadLedger(){try{return JSON.parse(localStorage.getItem(LEDGER_KEY))||[]}catch{return[]}}
function saveLedger(d){localStorage.setItem(LEDGER_KEY,JSON.stringify(d))}
function recordPurchase(allocs,amount,profileName){const p=loadPortfolio();const now=new Date().toISOString();p.profile=profileName;p.amount=amount;p.holdings=allocs.map(a=>({code:a.code,name:a.fullName,category:a.name,targetPct:a.pct,amount:Math.round(amount*a.pct/100),buyDate:now}));p.history.push({date:now,action:'buy',amount,profile:profileName});savePortfolio(p)}

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
`;document.head.appendChild(s)}

// ---- 底部导航 ----
function renderNav(){let n=document.getElementById('btmNav');if(!n){n=document.createElement('div');n.id='btmNav';n.className='bottom-nav';document.body.appendChild(n)}
const tabs=[{id:'landing',icon:'🏠',label:'首页'},{id:'portfolio',icon:'📊',label:'持仓'},{id:'chat',icon:'🤖',label:'AI分析'},{id:'ledger',icon:'📝',label:'记账'}];
n.innerHTML=tabs.map(t=>`<div class="nav-item ${currentPage===t.id?'active':''}" onclick="navigateTo('${t.id}')"><div class="nav-icon">${t.icon}</div><div>${t.label}</div></div>`).join('')}

function navigateTo(p){currentPage=p;renderNav();if(p==='landing')renderLanding();else if(p==='portfolio')renderPortfolio();else if(p==='chat')renderChat();else if(p==='ledger')renderLedger()}

// ---- 落地页 ----
function renderLanding(){currentPage='landing';const p=loadPortfolio();$('#app').innerHTML=`<div class="landing stagger"><div class="landing-icon">💰</div><h1>你的钱，该怎么放？</h1><p class="subtitle">回答5个问题，AI帮你出一份<br>专属资产配置方案</p><button class="cta-btn" onclick="startQuiz()">开始测评</button>${p.holdings.length?'<div class="has-portfolio-badge" onclick="navigateTo(\'portfolio\')">📊 查看我的持仓</div>':''}<div class="trust-badges"><span class="trust-badge">不收费</span><span class="trust-badge">不推销</span><span class="trust-badge">不注册</span></div></div>`;renderNav()}

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

// ---- 持仓盈亏页 ----
async function renderPortfolio(){currentPage='portfolio';renderNav();const p=loadPortfolio();
if(!p.holdings.length){$('#app').innerHTML=`<div class="landing" style="min-height:70vh"><div class="landing-icon">📊</div><h1>还没有持仓</h1><p class="subtitle">先完成测评并记录买入吧</p><button class="cta-btn" onclick="startQuiz()">去测评</button></div>`;return}
const tc=p.holdings.reduce((s,h)=>s+h.amount,0);
$('#app').innerHTML=`<div class="portfolio-page fade-up"><div class="pnl-hero"><div class="pnl-label">${p.profile}·总投入</div><div class="pnl-total-value">${fmtFull(tc)}</div><div id="pnlSum"><div style="font-size:13px;color:var(--text2);margin-top:8px">${API_AVAILABLE?'正在计算实时盈亏...':'后端离线，显示买入成本'}</div></div></div><div id="holdList">${p.holdings.map(h=>`<div class="holding-card" onclick="showFundDetail('${h.code}')"><div class="holding-top"><div class="holding-info"><div class="holding-name">${h.name}</div><div class="holding-meta">${h.category}·目标${h.targetPct}%</div></div><div class="holding-amount"><div class="holding-money">${fmtFull(h.amount)}</div></div></div></div>`).join('')}</div><div class="bottom-actions" style="margin-top:20px"><button class="action-btn primary" onclick="startQuiz()">🔄重新测评</button><button class="action-btn secondary" onclick="clearPortfolio()">🗑️清除</button></div></div>`;
if(API_AVAILABLE){const pnl=await fetchPnl();if(pnl){const pe=document.getElementById('pnlSum');if(pe){const c=pnl.totalPnl>=0?'pos':'neg';const sg=pnl.totalPnl>=0?'+':'';pe.innerHTML=`<div class="pnl-change ${c}">${sg}${fmtFull(Math.round(pnl.totalPnl))}(${sg}${pnl.totalPnlPct.toFixed(2)}%)</div><div class="pnl-sub">当前市值${fmtFull(Math.round(pnl.totalMarket))}</div>`}
const le=document.getElementById('holdList');if(le&&pnl.holdings){le.innerHTML=pnl.holdings.map(h=>{const pc=h.pnl>=0?'pos':'neg';const ps=h.pnl>=0?'+':'';return`<div class="holding-card" onclick="showFundDetail('${h.code}')"><div class="holding-top"><div class="holding-info"><div class="holding-name">${h.name}</div><div class="holding-meta">${h.category}${h.navDate?' · '+h.navDate:''}</div></div><div class="holding-amount"><div class="holding-money">${fmtFull(Math.round(h.marketValue))}</div><div class="holding-pct">${ps}${h.pnlPct.toFixed(2)}%</div></div></div><div class="holding-pnl-row"><div class="holding-pnl-item"><div class="holding-pnl-label">成本</div><div class="holding-pnl-val">${fmtFull(h.cost)}</div></div><div class="holding-pnl-item"><div class="holding-pnl-label">盈亏</div><div class="holding-pnl-val ${pc}">${ps}${fmtFull(Math.round(h.pnl))}</div></div><div class="holding-pnl-item"><div class="holding-pnl-label">日涨跌</div><div class="holding-pnl-val ${h.dayChange>=0?'pos':'neg'}">${h.dayChange>=0?'+':''}${h.dayChange}%</div></div></div></div>`}).join('')}}}}

// ---- AI聊天页 ----
function renderChat(){currentPage='chat';renderNav();const sugs=['今天市场怎么样？','我该加仓吗？','黄金还能买吗？','为什么最近跌了？'];
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

// ---- 记账页 ----
function renderLedger(){currentPage='ledger';renderNav();const entries=loadLedger();const total=entries.reduce((s,e)=>s+(e.amount||0),0);
const byCat={};entries.forEach(e=>{const c=e.category||'其他';byCat[c]=(byCat[c]||0)+e.amount});
$('#app').innerHTML=`<div class="ledger-page fade-up"><div class="ledger-header"><div style="font-size:14px;color:var(--text2)">本月支出</div><div class="ledger-total">-${fmtFull(Math.round(total))}</div><div class="ledger-period">${entries.length}笔记录</div></div>
${Object.keys(byCat).length?`<div class="ledger-cats">${Object.entries(byCat).map(([c,a])=>`<div class="ledger-cat"><div class="ledger-cat-icon">${LEDGER_ICONS[c]||'📌'}</div><div class="ledger-cat-name">${c}</div><div class="ledger-cat-amt">¥${Math.round(a)}</div></div>`).join('')}</div>`:''}
<div class="section-title">📸 拍照记账</div>
<div class="upload-area" onclick="document.getElementById('rcptFile').click()"><div class="icon">📷</div><div class="text">拍一张小票，AI自动识别入账</div><input type="file" id="rcptFile" accept="image/*" capture="environment" style="display:none" onchange="handleReceipt(this)"></div><div id="ocrRes"></div>
<div class="section-title">✏️ 手动记一笔</div>
<div class="manual-form"><div class="form-row"><div class="form-label">金额</div><input class="form-input" type="number" id="ldgAmt" placeholder="0.00" inputmode="decimal"></div><div class="form-row"><div class="form-label">分类</div><select class="form-select" id="ldgCat">${Object.keys(LEDGER_ICONS).map(c=>`<option value="${c}">${LEDGER_ICONS[c]} ${c}</option>`).join('')}</select></div><div class="form-row"><div class="form-label">备注</div><input class="form-input" type="text" id="ldgNote" placeholder="买了什么..."></div><button class="form-submit" onclick="addEntry()">记一笔</button></div>
${entries.length?`<div class="section-title">📋 最近记录</div>${entries.slice(-20).reverse().map(e=>`<div class="ledger-entry"><div class="ledger-entry-icon">${LEDGER_ICONS[e.category]||'📌'}</div><div class="ledger-entry-info"><div class="ledger-entry-note">${e.note||e.category}</div><div class="ledger-entry-date">${new Date(e.date).toLocaleString('zh-CN')}</div></div><div class="ledger-entry-amt">-¥${e.amount.toFixed(2)}</div></div>`).join('')}`:'<div style="text-align:center;color:var(--text2);padding:32px">还没有记录，拍张小票试试📸</div>'}
${entries.length?'<div class="bottom-actions" style="margin-top:20px"><button class="action-btn secondary" onclick="clearLedger()">🗑️清除记录</button></div>':''}</div>`}

async function handleReceipt(input){if(!input.files||!input.files[0])return;const file=input.files[0];const re=document.getElementById('ocrRes');if(!re)return;
re.innerHTML='<div style="text-align:center;padding:16px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:3px"></div>AI识别中...</div>';
if(API_AVAILABLE){try{const fd=new FormData();fd.append('file',file);fd.append('userId',getUserId());const r=await fetch(API_BASE+'/receipt/ocr',{method:'POST',body:fd});if(r.ok){const d=await r.json();if(d.amount>0){const es=loadLedger();es.push({date:new Date().toISOString(),amount:d.amount,category:d.category||'其他',note:d.merchant||d.note||'OCR',source:'ocr'});saveLedger(es);re.innerHTML=`<div style="background:rgba(16,185,129,.1);border:1px solid rgba(16,185,129,.3);border-radius:12px;padding:16px;margin-bottom:16px"><div style="font-weight:700;color:var(--green);margin-bottom:8px">✅ 已入账</div><div style="font-size:13px;color:var(--text2)">¥${d.amount.toFixed(2)} · ${d.category||'其他'}${d.merchant?' · '+d.merchant:''}</div></div>`;setTimeout(()=>renderLedger(),2000);return}}}catch(e){console.error(e)}}
re.innerHTML=`<div style="background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.3);border-radius:12px;padding:16px"><div style="color:var(--red)">识别失败</div><div style="font-size:13px;color:var(--text2);margin-top:4px">${API_AVAILABLE?'无法识别，请手动输入':'后端离线，请手动输入'}</div></div>`}

function addEntry(){const a=parseFloat(document.getElementById('ldgAmt')?.value);const c=document.getElementById('ldgCat')?.value||'其他';const n=document.getElementById('ldgNote')?.value||'';if(!a||a<=0){alert('请输入金额');return}
const es=loadLedger();es.push({date:new Date().toISOString(),amount:a,category:c,note:n,source:'manual'});saveLedger(es);
if(API_AVAILABLE)fetch(API_BASE+'/ledger/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:getUserId(),amount:a,category:c,note:n})}).catch(()=>{});
renderLedger()}
function clearLedger(){if(confirm('确定清除所有记账记录？')){localStorage.removeItem(LEDGER_KEY);renderLedger()}}

// ---- 弹窗 ----
function showFundDetail(code){const d=FUND_DETAILS[code];if(!d)return;const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};const nav=liveNavData[code];
o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()"><div class="modal-handle"></div><div class="modal-title">${d.fullName}</div><div class="modal-subtitle">${d.type} · ${d.company} · ${d.risk}</div>
<div class="modal-section"><div class="modal-section-title">💡 为什么推荐</div><div class="modal-reason">${d.reason}</div></div>
<div class="modal-section"><div class="modal-section-title">📊 历史业绩</div><div class="modal-history-grid"><div class="modal-history-item"><div class="modal-history-label">近1年</div><div class="modal-history-value">${d.history.y1}</div></div><div class="modal-history-item"><div class="modal-history-label">近3年</div><div class="modal-history-value">${d.history.y3}</div></div><div class="modal-history-item"><div class="modal-history-label">近5年</div><div class="modal-history-value">${d.history.y5}</div></div></div></div>
<div class="modal-section"><div class="modal-section-title">📋 基本信息</div><div class="modal-stat-grid"><div class="modal-stat"><div class="modal-stat-label">规模</div><div class="modal-stat-value">${d.scale}</div></div><div class="modal-stat"><div class="modal-stat-label">成立</div><div class="modal-stat-value">${d.founded}</div></div><div class="modal-stat"><div class="modal-stat-label">费率</div><div class="modal-stat-value" style="font-size:12px">${d.fee}</div></div><div class="modal-stat"><div class="modal-stat-label">跟踪</div><div class="modal-stat-value" style="font-size:12px">${d.tracking}</div></div></div></div>
${nav?`<div class="modal-section"><div class="modal-section-title">📡 实时</div><div class="modal-stat-grid"><div class="modal-stat"><div class="modal-stat-label">净值</div><div class="modal-stat-value" style="color:var(--green)">${nav.nav}</div></div><div class="modal-stat"><div class="modal-stat-label">涨跌</div><div class="modal-stat-value" style="color:${parseFloat(nav.change)>=0?'var(--green)':'var(--red)'}">${nav.change}%</div></div></div></div>`:''}
<div class="modal-section"><div class="modal-section-title">🛒 怎么买</div><div class="modal-buy-tip">✅ ${d.buyTip}</div></div>
<div class="modal-section"><div class="modal-tags">${d.tags.map(t=>`<span class="modal-tag">${t}</span>`).join('')}</div></div></div>`;document.body.appendChild(o)}

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

// ---- 启动 ----
injectStyles();
checkAPI().then(()=>{fetchNav();syncFromCloud()});
renderLanding();
