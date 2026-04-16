// ============================================================
// 钱袋子 — AI 智能资产配置助手 V4.0
// 交易流水制 + 全资产管理 + 净资产仪表盘
// 实时盈亏 + AI 对话 + 云端持久化 + OCR 记账
// + 市场资讯 + 技术指标 + 宏观日历 + 收入源管理
// ============================================================

// ---- API 配置 ----
// 优先相对路径（同源请求，无 Mixed Content 问题）
// 用户直接访问腾讯云 http://150.158.47.189:8000 或 Railway https://...railway.app 都走 /api
const API_BASE = (() => {
  const h = location.hostname;
  if (h === 'localhost' || h === '127.0.0.1' || h.startsWith('192.168.')) return 'http://localhost:8000/api';
  return '/api'; // 同源，自动匹配当前协议和域名
})();
let API_AVAILABLE = false;

function getUserId() {
  // 统一使用 Profile ID（所有 API 调用都应该用 getProfileId()）
  return getProfileId();
}

// ---- 基金详情 ----
const FUND_DETAILS = {
  '110020': { fullName:'易方达沪深300ETF联接A', type:'指数基金', company:'易方达基金', scale:'约310亿', fee:'管理费0.15%/年+托管费0.05%/年', founded:'2009-08-26', tracking:'沪深300指数', reason:'费率全市场最低档，规模大流动性好，跟踪误差极小。沪深300覆盖A股市值最大的300家公司。', history:{y1:'+8.2%',y3:'+12.5%',y5:'+42.3%'}, risk:'中高风险(R4)', buyTip:'场外：支付宝/天天基金搜110020（申购费1折约0.12%）\n场内更省：券商APP买510300（沪深300ETF），仅收交易佣金（万2左右）\n💡 定投推荐场外，大额单笔推荐场内ETF', etfCode:'510300', tags:['低费率','大规模','宽基指数','A股核心'] },
  '050025': { fullName:'博时标普500ETF联接A', type:'指数基金(QDII)', company:'博时基金', scale:'约85亿', fee:'管理费0.15%/年+托管费0.05%/年', founded:'2012-06-14', tracking:'标普500指数', reason:'追踪美国500强企业，分散地域风险。过去30年标普500年化回报约10%。', history:{y1:'+22.1%',y3:'+38.7%',y5:'+95.2%'}, risk:'中高风险(R4)', buyTip:'场外：支付宝/天天基金搜050025（申购费1折约0.12%）\n⚠️ QDII基金限购，部分渠道每日限额100元\n💡 多个渠道同时买可突破限额', tags:['美股','全球配置','QDII','长期王者'] },
  '217022': { fullName:'招商产业债A', type:'债券基金', company:'招商基金', scale:'约120亿', fee:'管理费0.30%/年+托管费0.10%/年', founded:'2012-12-04', tracking:'无(主动管理)', reason:'纯债基金标杆，历史几乎没亏过年度。组合的"稳定器"。', history:{y1:'+4.1%',y3:'+12.8%',y5:'+22.5%'}, risk:'中低风险(R2)', buyTip:'场外：支付宝/天天基金搜217022（申购费1折约0.08%）\n也可通过招商基金官网直销（费率可能更低）\n💡 债基费率差异小，选操作方便的平台即可', tags:['低风险','稳定器','纯债','年年正收益'] },
  '000216': { fullName:'华安黄金ETF联接A', type:'商品基金', company:'华安基金', scale:'约95亿', fee:'管理费0.15%/年+托管费0.05%/年', founded:'2013-08-14', tracking:'黄金现货价格', reason:'买黄金最方便的方式。经典避险资产，近年全球央行狂买。', history:{y1:'+28.5%',y3:'+52.3%',y5:'+78.6%'}, risk:'中风险(R3)', buyTip:'场外：支付宝/天天基金搜000216（申购费1折约0.12%）\n场内更省：券商APP买518880（华安黄金ETF），仅收佣金\n💡 场内实时交易更灵活，场外定投更省心', etfCode:'518880', tags:['避险','抗通胀','黄金','央行增持'] },
  '008114': { fullName:'天弘中证红利低波动100ETF联接A', type:'指数基金', company:'天弘基金', scale:'约19亿', fee:'管理费0.50%/年+托管费0.10%/年', founded:'2019-12-10', tracking:'中证红利低波动100指数', reason:'高股息+低波动双因子选股，每季度分红，像收房租。2022-2024连续三年同类排名优秀。', history:{y1:'+8.0%',y3:'+24.4%',y5:'+73.3%'}, risk:'中高风险(R4)', buyTip:'场外：支付宝/天天基金搜008114（申购费1折约0.15%）\n场内替代：券商APP买515100（红利低波ETF），仅收佣金\n💡 定投推荐场外联接基金，省心自动扣款', etfCode:'515100', tags:['红利','低波动','高股息','季度分红'] },
  '余额宝': { fullName:'余额宝(天弘货币基金)', type:'货币基金', company:'天弘基金', scale:'约7000亿', fee:'管理费0.30%/年+托管费0.08%/年', founded:'2013-06-13', tracking:'无', reason:'国民级货币基金，随时存取，几乎零风险。应急弹药。', history:{y1:'+1.8%',y3:'+5.5%',y5:'+10.2%'}, risk:'低风险(R1)', buyTip:'支付宝余额宝直接存入（零申购费）\n微信零钱通也是同类产品\n券商的场内货基（如511880）收益可能略高\n💡 货币基金各渠道费率相同，选最方便的', tags:['零风险','随时取','应急','抄底弹药'] },
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
  '保守型':[{name:'沪深300',code:'110020',fullName:'易方达沪深300ETF联接A',pct:10,color:'#3B82F6',returns:{good:.15,mid:.08,bad:-.10}},{name:'标普500',code:'050025',fullName:'博时标普500ETF联接A',pct:5,color:'#10B981',returns:{good:.18,mid:.10,bad:-.12}},{name:'债券',code:'217022',fullName:'招商产业债A',pct:50,color:'#F59E0B',returns:{good:.06,mid:.04,bad:.01}},{name:'黄金',code:'000216',fullName:'华安黄金ETF联接A',pct:15,color:'#F97316',returns:{good:.15,mid:.08,bad:-.05}},{name:'红利低波',code:'008114',fullName:'天弘红利低波100联接A',pct:10,color:'#EF4444',returns:{good:.12,mid:.07,bad:-.05}},{name:'货币(应急)',code:'余额宝',fullName:'余额宝',pct:10,color:'#E5E7EB',returns:{good:.02,mid:.018,bad:.015}}],
  '稳健型':[{name:'沪深300',code:'110020',fullName:'易方达沪深300ETF联接A',pct:20,color:'#3B82F6',returns:{good:.15,mid:.08,bad:-.10}},{name:'标普500',code:'050025',fullName:'博时标普500ETF联接A',pct:10,color:'#10B981',returns:{good:.18,mid:.10,bad:-.12}},{name:'债券',code:'217022',fullName:'招商产业债A',pct:35,color:'#F59E0B',returns:{good:.06,mid:.04,bad:.01}},{name:'黄金',code:'000216',fullName:'华安黄金ETF联接A',pct:15,color:'#F97316',returns:{good:.15,mid:.08,bad:-.05}},{name:'红利低波',code:'008114',fullName:'天弘红利低波100联接A',pct:10,color:'#EF4444',returns:{good:.12,mid:.07,bad:-.05}},{name:'货币(应急)',code:'余额宝',fullName:'余额宝',pct:10,color:'#E5E7EB',returns:{good:.02,mid:.018,bad:.015}}],
  '平衡型':[{name:'沪深300',code:'110020',fullName:'易方达沪深300ETF联接A',pct:30,color:'#3B82F6',returns:{good:.20,mid:.10,bad:-.15}},{name:'标普500',code:'050025',fullName:'博时标普500ETF联接A',pct:20,color:'#10B981',returns:{good:.22,mid:.12,bad:-.15}},{name:'债券',code:'217022',fullName:'招商产业债A',pct:20,color:'#F59E0B',returns:{good:.06,mid:.04,bad:.01}},{name:'黄金',code:'000216',fullName:'华安黄金ETF联接A',pct:15,color:'#F97316',returns:{good:.15,mid:.08,bad:-.05}},{name:'红利低波',code:'008114',fullName:'天弘红利低波100联接A',pct:10,color:'#EF4444',returns:{good:.12,mid:.07,bad:-.05}},{name:'货币(应急)',code:'余额宝',fullName:'余额宝',pct:5,color:'#E5E7EB',returns:{good:.02,mid:.018,bad:.015}}],
  '进取型':[{name:'沪深300',code:'110020',fullName:'易方达沪深300ETF联接A',pct:35,color:'#3B82F6',returns:{good:.25,mid:.12,bad:-.18}},{name:'标普500',code:'050025',fullName:'博时标普500ETF联接A',pct:25,color:'#10B981',returns:{good:.25,mid:.13,bad:-.18}},{name:'债券',code:'217022',fullName:'招商产业债A',pct:10,color:'#F59E0B',returns:{good:.06,mid:.04,bad:.01}},{name:'黄金',code:'000216',fullName:'华安黄金ETF联接A',pct:10,color:'#F97316',returns:{good:.15,mid:.08,bad:-.05}},{name:'红利低波',code:'008114',fullName:'天弘红利低波100联接A',pct:15,color:'#EF4444',returns:{good:.15,mid:.08,bad:-.08}},{name:'货币(应急)',code:'余额宝',fullName:'余额宝',pct:5,color:'#E5E7EB',returns:{good:.02,mid:.018,bad:.015}}],
  '激进型':[{name:'沪深300',code:'110020',fullName:'易方达沪深300ETF联接A',pct:40,color:'#3B82F6',returns:{good:.30,mid:.12,bad:-.22}},{name:'标普500',code:'050025',fullName:'博时标普500ETF联接A',pct:30,color:'#10B981',returns:{good:.30,mid:.15,bad:-.22}},{name:'债券',code:'217022',fullName:'招商产业债A',pct:5,color:'#F59E0B',returns:{good:.06,mid:.04,bad:.01}},{name:'黄金',code:'000216',fullName:'华安黄金ETF联接A',pct:5,color:'#F97316',returns:{good:.15,mid:.08,bad:-.05}},{name:'红利低波',code:'008114',fullName:'天弘红利低波100联接A',pct:15,color:'#EF4444',returns:{good:.18,mid:.10,bad:-.10}},{name:'货币(应急)',code:'余额宝',fullName:'余额宝',pct:5,color:'#E5E7EB',returns:{good:.02,mid:.018,bad:.015}}],
};
const AMOUNT_MAP = [0,80000,200000,400000,750000,1500000];

// ---- 全局状态 ----
let currentPage='landing', currentQuestion=0, answers=[], selectedAmount=0;
let chartInstance=null, projChartInstance=null, liveNavData={}, currentProfile=null, currentAllocs=null;
let chatMessages=[];
// W8: 双模式UI — 小白(老婆) vs 专业(你)
// LeiJiang 首次自动专业模式，其他人默认简洁，手动切换后尊重选择
function _getDefaultUIMode(){
const pid=(localStorage.getItem('moneybag_profile_id')||'').toLowerCase();
const name=(localStorage.getItem('moneybag_profile_name')||'').toLowerCase();
const saved=localStorage.getItem('moneybag_ui_mode');
// 管理员首次：如果之前没有主动切换过，自动设为 pro
if((pid==='leijiang'||name==='leijiang')&&!localStorage.getItem('moneybag_ui_mode_set_by_user'))return 'pro';
if(saved)return saved;
return 'simple'}
let _uiMode=_getDefaultUIMode();
function toggleUIMode(){
  const oldMode=_uiMode;
  _uiMode=_uiMode==='simple'?'pro':'simple';
  localStorage.setItem('moneybag_ui_mode',_uiMode);
  localStorage.setItem('moneybag_ui_mode_set_by_user','1');
  // Phase 0: 同步到后端偏好 API（失败不阻塞，降级用 localStorage）
  fetch(`${API_BASE}/user/preference?userId=${encodeURIComponent(getProfileId())}`,{
    method:'PUT',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({display_mode:_uiMode})
  }).catch(()=>{});
  location.reload()
}
function isProMode(){return _uiMode==='pro'}

// Phase 0 (3.8): 亮/暗/系统 主题切换
const _THEME_CYCLE=['system','dark','light'];
let _currentTheme=localStorage.getItem('moneybag_theme')||'system';
function applyTheme(theme){
  _currentTheme=theme;
  localStorage.setItem('moneybag_theme',theme);
  if(theme==='light'){document.documentElement.setAttribute('data-theme','light')}
  else if(theme==='dark'){document.documentElement.setAttribute('data-theme','dark')}
  else{document.documentElement.removeAttribute('data-theme')} // system
  const btn=document.getElementById('themeBtn');if(btn)btn.textContent=getThemeIcon();
}
function getThemeIcon(){return _currentTheme==='light'?'☀️':_currentTheme==='dark'?'🌙':'🖥️'}
function cycleTheme(){
  const idx=(_THEME_CYCLE.indexOf(_currentTheme)+1)%_THEME_CYCLE.length;
  applyTheme(_THEME_CYCLE[idx]);
}
applyTheme(_currentTheme); // 启动时应用

// Phase 0: 通用三态渲染（Loading / Error / Empty / Data）
function renderCard(title, state, content=''){
  if(state==='loading') return `<div class="dashboard-card"><div class="dashboard-card-title">${title}</div><div style="padding:16px;text-align:center;color:var(--text2)"><div class="loading-spinner" style="width:20px;height:20px;margin:0 auto 8px;border-width:2px"></div>加载中...</div></div>`;
  if(state==='error') return `<div class="dashboard-card" style="border-left:3px solid var(--red)"><div class="dashboard-card-title">${title}</div><div style="padding:12px;text-align:center;color:var(--text2)">加载失败<br><button onclick="location.reload()" style="margin-top:8px;padding:4px 12px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:12px">🔄 重试</button></div></div>`;
  if(state==='empty') return `<div class="dashboard-card"><div class="dashboard-card-title">${title}</div><div style="padding:12px;text-align:center;color:var(--text2);font-size:12px">暂无数据</div></div>`;
  return `<div class="dashboard-card"><div class="dashboard-card-title">${title}</div>${content}</div>`;
}

const _BASE_STORAGE_KEY='moneybag_portfolio';
const _BASE_TXN_KEY='moneybag_transactions';
const _BASE_ASSETS_KEY='moneybag_assets';
const _BASE_LEDGER_KEY='moneybag_ledger';
const _BASE_SOURCES_KEY='moneybag_income_sources';
// 多用户隔离：key 加 userId 后缀（铁律 #19）
function _uk(base){const uid=getProfileId();return uid?`${base}_${uid}`:base}
Object.defineProperty(window,'STORAGE_KEY',{get(){return _uk(_BASE_STORAGE_KEY)}});
Object.defineProperty(window,'TXN_KEY',{get(){return _uk(_BASE_TXN_KEY)}});
Object.defineProperty(window,'ASSETS_KEY',{get(){return _uk(_BASE_ASSETS_KEY)}});
Object.defineProperty(window,'LEDGER_KEY',{get(){return _uk(_BASE_LEDGER_KEY)}});
Object.defineProperty(window,'SOURCES_KEY',{get(){return _uk(_BASE_SOURCES_KEY)}});

// ---- 多用户 Profile 系统 ----
let _profileId = localStorage.getItem('moneybag_profile_id') || '';
let _profileName = localStorage.getItem('moneybag_profile_name') || '';
function getProfileId(){ return _profileId || 'default' }
function getProfileParam(){ return `userId=${encodeURIComponent(getProfileId())}` }

async function ensureProfile(){
  if(_profileId) return; // 已有身份
  return new Promise(resolve => {
    const overlay=document.createElement('div');
    overlay.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;display:flex;align-items:center;justify-content:center';
    overlay.innerHTML=`<div style="background:var(--bg2,#1e293b);border-radius:16px;padding:32px;max-width:320px;width:90%;text-align:center">
      <div style="font-size:48px;margin-bottom:16px">👋</div>
      <h2 style="color:var(--text,#f1f5f9);margin-bottom:8px">欢迎使用钱袋子</h2>
      <p style="color:var(--text2,#94a3b8);font-size:14px;margin-bottom:20px">输入名字和邀请码开始使用</p>
      <input id="profileNameIn" type="text" placeholder="你的名字" maxlength="20"
        style="width:100%;box-sizing:border-box;padding:12px;border-radius:10px;border:1px solid var(--bg3,#334155);background:var(--bg,#0f172a);color:var(--text,#f1f5f9);font-size:16px;text-align:center;margin-bottom:12px">
      <input id="profileCodeIn" type="text" placeholder="邀请码（联系管理员获取）" maxlength="10"
        style="width:100%;box-sizing:border-box;padding:12px;border-radius:10px;border:1px solid var(--bg3,#334155);background:var(--bg,#0f172a);color:var(--text,#f1f5f9);font-size:16px;text-align:center;margin-bottom:16px;text-transform:uppercase">
      <div id="profileError" style="color:#EF4444;font-size:13px;margin-bottom:12px;display:none"></div>
      <button id="profileConfirmBtn" disabled onclick="confirmProfile()"
        style="width:100%;padding:12px;border-radius:10px;border:none;background:var(--accent,#F59E0B);color:#000;font-size:16px;font-weight:700;cursor:pointer;opacity:.5">确认</button>
    </div>`;
    document.body.appendChild(overlay);
    const inp=document.getElementById('profileNameIn');
    const codeInp=document.getElementById('profileCodeIn');
    const btn=document.getElementById('profileConfirmBtn');
    // URL 参数自动填充（邀请链接带 ?name=xxx&code=xxx）
    const urlP=new URLSearchParams(window.location.search);
    if(urlP.get('name'))inp.value=urlP.get('name');
    if(urlP.get('code'))codeInp.value=urlP.get('code');
    inp.focus();
    const checkReady=()=>{const v=inp.value.trim()&&codeInp.value.trim();btn.disabled=!v;btn.style.opacity=v?'1':'.5'};
    inp.oninput=checkReady;codeInp.oninput=checkReady;
    checkReady();  // 检查一次（自动填充后可能已就绪）
    codeInp.onkeydown=(e)=>{if(e.key==='Enter'&&inp.value.trim()&&codeInp.value.trim())confirmProfile()};
    window._profileOverlay=overlay;
    window._profileResolve=resolve;
  });
}
async function confirmProfile(){
  const name=document.getElementById('profileNameIn').value.trim();
  const code=document.getElementById('profileCodeIn').value.trim();
  const errEl=document.getElementById('profileError');
  if(!name||!code)return;
  try{
    const r=await fetch(`${API_BASE}/profiles`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,inviteCode:code})});
    const d=await r.json();
    if(r.ok&&d.ok){
      _profileId=d.profile.id;_profileName=d.profile.name;
      localStorage.setItem('moneybag_profile_id',_profileId);
      localStorage.setItem('moneybag_profile_name',_profileName);
      if(window._profileOverlay)window._profileOverlay.remove();
      if(window._profileResolve)window._profileResolve();
    }else{
      if(errEl){errEl.style.display='block';errEl.textContent=d.detail||'注册失败，请检查邀请码'}
    }
  }catch(e){
    if(errEl){errEl.style.display='block';errEl.textContent='网络错误，请重试'}
  }
}
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

// 计算净资产（本地 fallback — 后端统一 API 不可用时使用）
function calcNetWorth(){
  const txns=loadTxns();const assets=loadAssets();const ledger=loadLedger();
  const holdings=calcHoldingsFromTxns(txns);
  const fundValue=holdings.reduce((s,h)=>s+h.totalCost,0);
  const assetTotal=assets.filter(a=>a.type!=='liability').reduce((s,a)=>s+(a.value||0),0);
  const liabilities=assets.filter(a=>a.type==='liability').reduce((s,a)=>s+(a.value||0),0);
  const ledgerIncome=ledger.filter(e=>e.direction==='income').reduce((s,e)=>s+(e.amount||0),0);
  const ledgerExpense=ledger.filter(e=>e.direction!=='income').reduce((s,e)=>s+(e.amount||0),0);
  return {fundValue,assetTotal,liabilities,ledgerIncome,ledgerExpense,ledgerNet:ledgerIncome-ledgerExpense,netWorth:fundValue+assetTotal-liabilities+ledgerIncome-ledgerExpense,holdings};
}

// 从后端获取统一净资产（包含股票+基金+手动资产+负债）
async function fetchUnifiedNetworth(){
  if(!API_AVAILABLE)return null;
  const uid=getProfileId();if(!uid)return null;
  try{
    const r=await fetch(`${API_BASE}/unified-networth?userId=${uid}`,{signal:AbortSignal.timeout(10000)});
    if(!r.ok)return null;return await r.json();
  }catch(e){console.warn('unified-networth:',e);return null}
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
function recordPurchase(allocs,amount,profileName,preference){const p=loadPortfolio();const now=new Date().toISOString();p.profile=profileName;p.amount=amount;p.holdings=allocs.map(a=>({code:a.code,name:a.fullName,category:a.name,targetPct:a.pct,amount:Math.round(amount*a.pct/100),buyDate:now}));p.history.push({date:now,action:'quiz_buy',amount,profile:profileName,preference:preference||'fund',allocations:allocs.map(a=>({code:a.code,name:a.name||a.fullName,pct:a.pct,amount:Math.round(amount*a.pct/100)}))});savePortfolio(p);
// V4: 同时生成交易流水
const txns=loadTxns();
allocs.forEach(a=>{const buyAmt=Math.round(amount*a.pct/100);const nav=liveNavData[a.code]?.nav||1;
txns.push({id:'txn_'+Date.now().toString(36)+'_'+Math.random().toString(36).slice(2,6),
type:'BUY',code:a.code,name:a.fullName,category:a.name,
shares:buyAmt/nav,price:nav,amount:buyAmt,date:now,note:'测评配置',source:'quiz'})});
saveTxns(txns)}

async function syncCloud(portfolio){if(!API_AVAILABLE)return;try{await fetch(API_BASE+'/user/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:getUserId(),portfolio})})}catch{}}
async function syncFromCloud(){if(!API_AVAILABLE)return;try{const r=await fetch(API_BASE+'/user/'+getUserId());if(r.ok){const d=await r.json();if(d.portfolio?.transactions?.length){const l=loadPortfolio();if(!l.transactions?.length){const p=Object.assign(loadPortfolio(),{transactions:d.portfolio.transactions,assets:d.portfolio.assets||[]});savePortfolio(p)}}}}catch{}}

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
async function fetchFundDynamic(code){if(!API_AVAILABLE)return null;try{const r=await fetch(API_BASE+'/fund/info/'+code,{signal:AbortSignal.timeout(15000)});if(r.ok)return await r.json()}catch{}return null}
async function fetchPolicyNews(){if(!API_AVAILABLE)return[];try{const r=await fetch(API_BASE+'/news/policy',{signal:AbortSignal.timeout(15000)});if(r.ok){const d=await r.json();return d.news||[]}}catch{}return[]}
async function runDataAudit(){const btn=document.getElementById('auditBtn');if(btn)btn.textContent='🔄 检查中...';try{const r=await fetch(API_BASE+'/health/data-audit',{signal:AbortSignal.timeout(60000)});if(!r.ok)throw new Error('audit failed');const d=await r.json();const statusIcon={'ok':'✅','warn':'⚠️','error':'❌'};const statusColor={'ok':'#10B981','warn':'#F59E0B','error':'#EF4444'};const overallIcon={'healthy':'✅','degraded':'⚠️','unhealthy':'❌'};const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()"><div class="modal-handle"></div><div class="modal-title">${overallIcon[d.overall]||'🔍'} 数据健康体检报告</div><div class="modal-subtitle">${d.summary} · ${new Date(d.timestamp).toLocaleString('zh-CN')}</div><div style="padding:12px 0">${d.checks.map(c=>`<div style="display:flex;align-items:center;padding:8px 0;border-bottom:1px solid rgba(148,163,184,.1)"><span style="font-size:16px;width:28px">${statusIcon[c.status]}</span><span style="flex:1;font-size:13px;color:var(--text1)">${c.name}</span><span style="font-size:12px;color:${statusColor[c.status]};text-align:right;max-width:50%">${c.msg}</span></div>`).join('')}</div><div style="font-size:11px;color:var(--text2);padding-top:8px;border-top:1px solid rgba(148,163,184,.1)">💡 此体检自动检查：宏观数据新鲜度、估值准确性、基金净值及时性、新闻相关性、API响应速度</div></div>`;document.body.appendChild(o);if(btn)btn.textContent='🔍 数据体检'}catch(e){if(btn)btn.textContent='❌ 检查失败';setTimeout(()=>{if(btn)btn.textContent='🔍 数据体检'},3000)}}

// ---- 样式已迁移至 styles.css ----

// ---- 底部导航 ----
function renderNav(){let n=document.getElementById('btmNav');if(!n){n=document.createElement('div');n.id='btmNav';n.className='bottom-nav';document.body.appendChild(n)}
const tabs=[{id:'landing',icon:'🏠',label:'首页'},{id:'stocks',icon:'📈',label:'持仓'},{id:'insight',icon:'📰',label:'资讯'},{id:'chat',icon:'🤖',label:'AI分析'},{id:'assets',icon:'🏦',label:'资产'}];
n.innerHTML=tabs.map(t=>`<div class="nav-item ${currentPage===t.id?'active':''}" onclick="navigateTo('${t.id}')"><div class="nav-icon">${t.icon}</div><div>${t.label}</div></div>`).join('');
// 顶部用户名条
let hdr=document.getElementById('profileHeader');if(!hdr){hdr=document.createElement('div');hdr.id='profileHeader';hdr.style.cssText='position:fixed;top:0;left:0;right:0;z-index:100;padding:6px 16px;font-size:12px;color:var(--text2,#94a3b8);background:var(--bg,#0f172a);display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--bg3,#334155)';document.body.appendChild(hdr);document.body.style.paddingTop='32px'}
hdr.innerHTML=`<span onclick="showProfileSettings()" style="cursor:pointer">👋 ${_profileName||'未登录'} ⚙️</span><span style="display:flex;align-items:center;gap:8px"><button onclick="cycleTheme()" style="font-size:10px;padding:2px 8px;border-radius:4px;border:1px solid var(--bg3);background:transparent;color:var(--text2);cursor:pointer" id="themeBtn">${getThemeIcon()}</button><button onclick="toggleUIMode()" style="font-size:10px;padding:2px 8px;border-radius:4px;border:1px solid var(--bg3);background:${isProMode()?'rgba(99,102,241,.2)':'rgba(16,185,129,.2)'};color:${isProMode()?'#818CF8':'#10B981'};cursor:pointer">${isProMode()?'🔬 专业':'🌱 简洁'}</button><span style="font-size:10px;color:var(--text3,#64748b)">${getProfileId().slice(0,8)}</span></span>`}

function showProfileSettings(){
const pid=getProfileId();const wxId=localStorage.getItem('moneybag_wxwork_uid')||'';
const statusText=wxId?`<div style="background:rgba(16,185,129,.1);border:1px solid rgba(16,185,129,.3);border-radius:8px;padding:8px 12px;margin-bottom:12px;font-size:12px;color:#10B981">✅ 已绑定企微：<b>${wxId}</b>　盯盘信号会推送到你的微信</div>`:`<div style="background:rgba(245,158,11,.1);border:1px solid rgba(245,158,11,.3);border-radius:8px;padding:8px 12px;margin-bottom:12px;font-size:12px;color:#F59E0B">⚠️ 未绑定企微　绑定后才能收到盯盘推送</div>`;
const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()"><div class="modal-handle"></div>
<div class="modal-title" style="display:flex;justify-content:space-between;align-items:center">⚙️ 个人设置 <button onclick="clearLocalCache()" style="font-size:11px;padding:4px 10px;border-radius:6px;border:1px solid rgba(239,68,68,.3);background:transparent;color:var(--red);cursor:pointer">🗑️ 清缓存</button></div>
<div class="modal-subtitle">Profile: ${_profileName} (${pid.slice(0,8)})</div>
<div class="manual-form" style="background:transparent;padding:0;margin-top:16px">
${statusText}
<div class="form-row"><div class="form-label">企业微信账号 (用于个人推送)</div>
<input class="form-input" type="text" id="wxworkUidInput" placeholder="如 LeiJiang" value="${wxId}">
<div style="font-size:11px;color:var(--text2);margin-top:4px">填写你的企微账号（在企微通讯录→个人信息→账号）</div></div>
<button class="form-submit" id="wxSaveBtn" onclick="saveProfileSettings()">💾 保存</button>
</div></div>`;document.body.appendChild(o)}

function clearLocalCache(){
if(!confirm('确定清除本地缓存？\\n（不会删除服务器数据，只清浏览器缓存）'))return;
const keys=Object.keys(localStorage).filter(k=>k.startsWith('moneybag')||k===STORAGE_KEY||k===LEDGER_KEY);
keys.forEach(k=>localStorage.removeItem(k));
alert('已清除 '+keys.length+' 项本地数据，即将刷新');
location.reload()}

async function saveProfileSettings(){
const wxId=document.getElementById('wxworkUidInput')?.value?.trim()||'';
const pid=getProfileId();
const btn=document.getElementById('wxSaveBtn');
if(btn){btn.textContent='保存中...';btn.disabled=true}
if(API_AVAILABLE){try{
  const r=await fetch(API_BASE+'/profiles/'+encodeURIComponent(pid),{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({wxworkUserId:wxId})});
  const d=await r.json();
  if(!r.ok){alert('❌ '+( d.detail||'保存失败'));if(btn){btn.textContent='💾 保存';btn.disabled=false};return}
}catch(e){alert('❌ 网络错误');if(btn){btn.textContent='💾 保存';btn.disabled=false};return}}
localStorage.setItem('moneybag_wxwork_uid',wxId);
document.querySelector('.modal-overlay')?.remove();
if(wxId){alert('✅ 绑定成功！盯盘异动将推送给: '+wxId)}else{alert('已清除企微绑定')}}

function navigateTo(p){currentPage=p;renderNav();if(p==='landing')renderLanding();else if(p==='portfolio')renderPortfolio();else if(p==='stocks')renderStocks();else if(p==='insight')renderInsight();else if(p==='chat')renderChat();else if(p==='ledger')renderLedger();else if(p==='assets')renderAssets()}

// ---- 落地页（智能决策中心）----
function renderLanding(){currentPage='landing';const p=loadPortfolio();const txns=loadTxns();const assets=loadAssets();const ledger=loadLedger();
// 已登录用户直接进首页，不走问卷（v3.0：资产通过持仓Tab和资产Tab录入，不需要问卷）
const hasProfile=!!getProfileId()&&getProfileId()!=='default';
const hasServerHoldings=localStorage.getItem(_uk('moneybag_has_holdings'))==='1';
const hasLocalData=txns.length>0||p.transactions?.length>0||assets.length>0||ledger.length>0||hasServerHoldings;
if(!hasProfile&&!hasLocalData){
$('#app').innerHTML=`<div class="landing stagger"><div class="landing-icon">💰</div><h1>你的钱，该怎么放？</h1><p class="subtitle">回答5个问题，AI帮你出一份<br>专属资产配置方案</p><button class="cta-btn" onclick="startQuiz()">开始测评</button><div class="trust-badges"><span class="trust-badge">不收费</span><span class="trust-badge">不推销</span><span class="trust-badge">不注册</span></div></div>`;renderNav();return}

// Phase 0: 有用户但空仓 → 市场概览模式（机会导向）
const hasHoldings=txns.length>0||p.transactions?.length>0||hasServerHoldings;
if(hasProfile&&!hasHoldings){
$('#app').innerHTML=`<div class="result-page fade-up">
<div class="pnl-hero" style="margin-bottom:16px">
<div class="pnl-label">💰 家庭净资产</div>
<div style="font-size:10px;color:var(--text2);margin-top:2px">空仓观望中 👀</div>
<div class="pnl-total-value" id="heroNetWorth">${fmtFull(Math.round(nw.netWorth))}</div>
<div id="heroBreakdown" style="display:flex;gap:12px;justify-content:center;margin-top:12px;font-size:12px;flex-wrap:wrap">
<div style="text-align:center"><div style="color:var(--text2)">💵 现金</div><div style="font-weight:700;color:var(--green)">¥${fmtMoney(Math.round(nw.assetTotal))}</div></div>
</div>
</div>

<div class="dashboard-card" style="border-left:3px solid #F59E0B;margin-bottom:12px">
<div class="dashboard-card-title">📊 市场概览</div>
<div style="font-size:13px;color:var(--text1);line-height:1.8">空仓期间，AI 管家持续监控市场，寻找入场时机。</div>
<div id="emptyPortfolioSignals" style="margin-top:8px;font-size:12px;color:var(--text2)">加载市场信号中...</div>
</div>

<div id="stewardBriefingCard" class="dashboard-card" style="border-left:3px solid #6366F1;display:none">
<div class="dashboard-card-title">🤖 管家一句话</div>
<div id="stewardBriefingText" style="font-size:13px;line-height:1.8;color:var(--text1)">加载中...</div>
</div>

<div id="dailyFocusSection"></div>

<div class="bottom-actions" style="margin-top:16px">
<button class="action-btn primary" onclick="navigateTo('stocks')">📈 开始建仓<div style="font-size:10px;font-weight:400;opacity:0.7;margin-top:2px">添加第一笔投资</div></button>
<button class="action-btn secondary" onclick="navigateTo('chat')">💬 问问管家</button>
<button class="action-btn secondary" onclick="startQuiz()">🔄 重新测评</button>
</div>
</div>`;renderNav();loadDailyFocus();loadStewardBriefing();loadUnifiedHero();
// 加载空仓市场信号
if(API_AVAILABLE){fetch(API_BASE+'/daily-signal?'+getProfileParam(),{signal:AbortSignal.timeout(10000)}).then(r=>r.json()).then(d=>{
const el=document.getElementById('emptyPortfolioSignals');if(el){
const sig=d.signal||d.overall_signal||'观望';const emoji=sig.includes('买')||sig.includes('good')?'🟢':sig.includes('卖')||sig.includes('bad')?'🔴':'🟡';
el.innerHTML=`${emoji} 今日信号：${sig}<br>💡 ${d.suggestion||d.one_line||'持续关注市场变化'}`}
}).catch(()=>{})}
return}
// 有数据 → 智能决策中心
const nw=calcNetWorth();
const monthNow=new Date();const monthStart=new Date(monthNow.getFullYear(),monthNow.getMonth(),1).toISOString();
const monthLedger=ledger.filter(e=>e.date>=monthStart);
const monthInc=monthLedger.filter(e=>e.direction==='income').reduce((s,e)=>s+(e.amount||0),0);
const monthExp=monthLedger.filter(e=>e.direction!=='income').reduce((s,e)=>s+(e.amount||0),0);

$('#app').innerHTML=`<div class="result-page fade-up">
<div class="pnl-hero" style="margin-bottom:16px">
<div class="pnl-label">💰 我的净资产 <span onclick="showExplain('networth')" style="font-size:14px;cursor:pointer;opacity:0.6">ℹ️</span></div>
<div style="font-size:10px;color:var(--text2);margin-top:2px">含投资+现金+房产+车辆+保险 - 负债</div>
<div class="pnl-total-value" id="heroNetWorth">${fmtFull(Math.round(nw.netWorth))}</div>
<div id="heroBreakdown" style="display:flex;gap:12px;justify-content:center;margin-top:12px;font-size:12px;flex-wrap:wrap">
<div style="text-align:center"><div style="color:var(--text2)">📈 投资</div><div style="font-weight:700;color:var(--accent)">¥${fmtMoney(Math.round(nw.fundValue))}</div></div>
<div style="text-align:center"><div style="color:var(--text2)">💵 现金</div><div style="font-weight:700;color:var(--green)">¥${fmtMoney(Math.round(nw.assetTotal))}</div></div>
<div style="text-align:center"><div style="color:var(--text2)">💳 负债</div><div style="font-weight:700;color:var(--red)">-¥${fmtMoney(Math.round(nw.liabilities))}</div></div>
</div>
<div id="heroHealth" style="margin-top:8px;font-size:12px;color:var(--text2)"></div>
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

<div id="dailyFocusSection"></div>

<div id="timingSection" style="display:none"></div>

<div id="stewardBriefingCard" class="dashboard-card" style="border-left:3px solid #6366F1;display:none">
<div class="dashboard-card-title">🤖 管家一句话</div>
<div id="stewardBriefingText" style="font-size:13px;line-height:1.8;color:var(--text1)">加载中...</div>
</div>

<div id="signalsSection" ${isProMode()?'':'style="display:none"'}></div>

<div id="riskAlertSection"></div>

<div id="allocationAdviceSection" ${isProMode()?'':'style="display:none"'}></div>

<div class="bottom-actions" style="margin-top:16px">
<button class="action-btn primary" onclick="showAllocateAssets()">💰 配置资产<div style="font-size:10px;font-weight:400;opacity:0.7;margin-top:2px">新存款到账？一键按方案分配</div></button>
<button class="action-btn secondary" onclick="showAllocHistory()">📋 配比历史</button>
<button class="action-btn secondary" onclick="showAddTxn()">➕ 记交易</button>
<button class="action-btn secondary" onclick="startQuiz()">🔄 重新测评</button>
</div>
</div>`;renderNav();loadSignals();loadDailyFocus();loadTimingSection();loadHomeRiskAlert();loadHomeAllocationAdvice();loadUnifiedHero();loadStewardBriefing()}

// ---- 首页：管家简报 ----
async function loadStewardBriefing(){
const card=document.getElementById('stewardBriefingCard');const txt=document.getElementById('stewardBriefingText');
if(!card||!txt||!API_AVAILABLE)return;
try{const r=await fetch(API_BASE+'/steward/briefing?userId='+getProfileId(),{signal:AbortSignal.timeout(15000)});
if(r.ok){const d=await r.json();card.style.display='block';
txt.innerHTML=`<div style="font-size:14px;font-weight:700;margin-bottom:4px">${d.one_line||'暂无'}</div>
<div style="font-size:12px;color:var(--text2)">${d.regime_description?'📊 '+d.regime_description:''} ${d.risk_level&&d.risk_level!=='normal'?'⚠️'+d.risk_level:''}</div>
${d.top_signal?`<div style="margin-top:6px;padding:6px 10px;background:rgba(99,102,241,.08);border-radius:8px;font-size:12px">🎯 ${d.top_signal}</div>`:''}
<button onclick="showLatestReview()" style="margin-top:8px;padding:6px 12px;border-radius:8px;border:1px solid rgba(99,102,241,.3);background:transparent;color:#818CF8;font-size:11px;cursor:pointer">📋 查看收盘复盘</button>`}}catch(e){console.warn('briefing:',e)}}

// ---- 收盘复盘查看 ----
async function showLatestReview(){
const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()"><div class="modal-handle"></div><div class="modal-title">📋 收盘复盘</div><div id="reviewContent" style="padding:12px 0"><div style="text-align:center;color:var(--text2)">加载中...</div></div></div>`;
document.body.appendChild(o);
try{const r=await fetch(API_BASE+'/steward/review?userId='+getProfileId(),{signal:AbortSignal.timeout(15000)});
if(r.ok){const d=await r.json();const el=document.getElementById('reviewContent');if(!el)return;
const concl=d.conclusion||d.summary||'暂无复盘数据';
let html=`<div style="font-size:14px;font-weight:700;margin-bottom:12px">${concl}</div>`;
if(d.regime_description)html+=`<div style="font-size:12px;color:var(--text2);margin-bottom:8px">📊 Regime: ${d.regime_description||d.regime}</div>`;
if(d.modules_called?.length)html+=`<div style="font-size:12px;color:var(--text2);margin-bottom:8px">📦 分析模块: ${d.modules_called.join(', ')} (${d.modules_called.length}个)</div>`;
if(d.direction)html+=`<div style="font-size:13px;margin-bottom:8px;padding:8px;background:var(--bg2);border-radius:8px">方向: <b>${d.direction}</b> | 置信度: <b>${d.confidence||50}%</b> | 门控: ${d.gate_decision||'?'}</div>`;
const diagFile=d.diagnosis||'';
if(diagFile)html+=`<div style="margin-bottom:8px;padding:10px;background:rgba(99,102,241,.06);border-radius:10px;font-size:13px;line-height:1.8;border-left:3px solid #6366F1"><div style="font-weight:700;margin-bottom:4px">🤖 R1 深度诊断</div>${diagFile}</div>`;
if(d.risk_level&&d.risk_level!=='normal')html+=`<div style="font-size:12px;color:var(--red)">⚠️ 风控: ${d.risk_level}</div>`;
html+=`<div style="font-size:11px;color:var(--text3);margin-top:12px;text-align:center">${d.elapsed?d.elapsed+'s · ':''}LLM ${d.llm_calls||0}次 · ${d.timestamp||'N/A'}</div>`;
el.innerHTML=html}}catch(e){const el=document.getElementById('reviewContent');if(el)el.innerHTML=`<div style="color:var(--text2)">加载失败: ${e.message}</div>`}}

// ---- 首页：统一净资产 Hero 更新 ----
async function loadUnifiedHero(){
const d=await fetchUnifiedNetworth();if(!d||!d.netWorth)return;
const el=document.getElementById('heroNetWorth');if(el)el.textContent=fmtFull(Math.round(d.netWorth));
const bd=document.getElementById('heroBreakdown');
if(bd){const b=d.breakdown||{};
bd.innerHTML=`
<div style="text-align:center"><div style="color:var(--text2)">📈 投资</div><div style="font-weight:700;color:var(--accent)">¥${fmtMoney(Math.round((b.investment||{}).total||0))}</div></div>
<div style="text-align:center"><div style="color:var(--text2)">💵 现金</div><div style="font-weight:700;color:var(--green)">¥${fmtMoney(Math.round((b.cash||{}).total||0))}</div></div>
<div style="text-align:center"><div style="color:var(--text2)">🏠 房产</div><div style="font-weight:700;color:#F59E0B">¥${fmtMoney(Math.round((b.property||{}).total||0))}</div></div>
<div style="text-align:center"><div style="color:var(--text2)">💳 负债</div><div style="font-weight:700;color:var(--red)">-¥${fmtMoney(Math.round((b.liability||{}).total||0))}</div></div>`}
const hel=document.getElementById('heroHealth');
if(hel&&d.healthGrade)hel.innerHTML=`${d.healthGrade} · ${d.healthScore}分${d.healthIssues?.length?` · <span style="color:var(--red)">${d.healthIssues[0]}</span>`:''}`
// Phase 0: 加载家庭资产汇总
if(API_AVAILABLE){fetch(`${API_BASE}/household/summary`,{signal:AbortSignal.timeout(10000)}).then(r=>r.json()).then(h=>{
  if(h.members&&h.members.length>1){
    const famEl=document.getElementById('heroBreakdown');
    if(famEl){const famHtml=h.members.map(m=>`<div style="text-align:center"><div style="color:var(--text2)">${m.nickname}</div><div style="font-weight:700;color:var(--accent)">¥${fmtMoney(Math.round(m.value))}</div>${m.change?`<div style="font-size:10px;color:${m.change>=0?'var(--green)':'var(--red)'}">${m.change>=0?'+':''}¥${Math.round(m.change)}</div>`:''}</div>`).join('');
    famEl.insertAdjacentHTML('beforeend',`<div style="width:100%;border-top:1px solid rgba(148,163,184,.1);margin-top:8px;padding-top:8px;display:flex;gap:12px;justify-content:center">${famHtml}</div>`)}
  }
}).catch(()=>{})}
}

// ---- 资产变更后异步刷新净资产（供添加/编辑/删除资产后调用）----
async function _refreshNetWorthAfterAssetChange(){
// 等待 500ms 让后端处理完保存（API 是 fire-and-forget）
await new Promise(r=>setTimeout(r,500));
// 刷新后端净资产（后端缓存已在 API 层失效）
const d=await fetchUnifiedNetworth();
// 更新首页 hero（如果 DOM 存在）
if(d&&d.netWorth){
const el=document.getElementById('heroNetWorth');if(el)el.textContent=fmtFull(Math.round(d.netWorth));
}
// 更新资产页的净资产显示
const assetNW=document.getElementById('assetPageNW');
if(assetNW&&d&&d.netWorth)assetNW.textContent=fmtFull(Math.round(d.netWorth));
}

// ---- 首页：今日关注（DeepSeek 个性化）----
async function loadDailyFocus(){
const el=document.getElementById('dailyFocusSection');if(!el||!API_AVAILABLE)return;
try{const r=await fetch(`${API_BASE}/daily-focus`,{signal:AbortSignal.timeout(15000)});
if(!r.ok)return;const d=await r.json();const tips=d.tips||[];
if(tips.length)el.innerHTML=`<div style="background:rgba(99,102,241,.06);border:1px solid rgba(99,102,241,.15);border-radius:12px;padding:12px 14px;margin-bottom:12px"><div style="font-size:13px;font-weight:700;margin-bottom:8px">🎯 今日关注 <span style="font-size:10px;color:var(--text2);font-weight:400">${d.source==='ai'?'AI':'默认'}</span></div>${tips.map(t=>`<div style="font-size:12px;line-height:1.8">${t}</div>`).join('')}</div>`
}catch(e){console.warn('dailyFocus:',e)}}

// Phase 0 (3.2): 入场时机 + 智能定投
async function loadTimingSection(){
const el=document.getElementById('timingSection');if(!el||!API_AVAILABLE)return;
try{
  const r=await fetch(`${API_BASE}/timing`,{signal:AbortSignal.timeout(10000)});
  if(!r.ok)return;const d=await r.json();
  const score=d.timingScore||50;
  const color=score<30?'var(--green)':score<50?'#F59E0B':score<70?'#F97316':'var(--red)';
  let dcaHtml='';
  // Pro 模式加载智能定投
  if(isProMode()){
    try{
      const dr=await fetch(`${API_BASE}/smart-dca`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({holdings:[]}),signal:AbortSignal.timeout(10000)});
      if(dr.ok){const dd=await dr.json();dcaHtml=`<div style="margin-top:10px;padding:10px;border-radius:8px;background:rgba(99,102,241,.06);font-size:12px"><span style="font-weight:600">🧠 智能定投：</span>${dd.advice||''} <span style="color:var(--accent)">倍率 ${dd.multiplier||1.0}x</span></div>`}
    }catch(e){}
  }
  el.style.display='block';
  el.innerHTML=`<div style="background:rgba(148,163,184,.04);border:1px solid rgba(148,163,184,.1);border-radius:12px;padding:12px 14px;margin-bottom:12px">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
      <span style="font-size:13px;font-weight:700">⏱️ 入场时机</span>
      <span style="font-size:20px;font-weight:700;color:${color}">${score}分</span>
    </div>
    <div style="font-size:14px;font-weight:600;margin-bottom:4px">${d.verdict||''}</div>
    <div style="font-size:12px;color:var(--text2);line-height:1.6">${d.detail||''}</div>
    ${isProMode()?`<div style="margin-top:8px;font-size:11px;color:var(--text3)">估值百分位: ${d.valuation?.percentile||'-'}% · 恐贪指数: ${d.fearGreed?.score||'-'}</div>`:''}
    ${dcaHtml}
  </div>`;
}catch(e){console.warn('timing:',e)}}

// Phase 0 (3.4): 盯盘预警轮询 + visibilitychange 智能控制
let _alertPolling=null;
function startAlertPolling(){
  if(_alertPolling||!API_AVAILABLE)return;
  _alertPolling=setInterval(async()=>{
    try{
      const r=await fetch(`${API_BASE}/watchlist/alerts?userId=${encodeURIComponent(getProfileId())}`,{signal:AbortSignal.timeout(10000)});
      if(!r.ok)return;const d=await r.json();
      if(d.alerts&&d.alerts.length>0){
        // 更新首页预警 badge
        const badge=document.getElementById('alertBadge');
        if(badge){badge.style.display='block';badge.textContent=d.alerts.length}
        // 高危预警弹 toast
        d.alerts.filter(a=>a.level==='danger').forEach(a=>{
          showToast(`⚠️ ${a.message}`,'danger');
        });
      }
    }catch(e){}
  },15000);
}
function stopAlertPolling(){if(_alertPolling){clearInterval(_alertPolling);_alertPolling=null}}

// 页面可见性控制（三方 AI 审查共识）
// 手机切后台/锁屏时暂停轮询，切回来时立即刷新
document.addEventListener('visibilitychange',()=>{
  if(document.hidden){stopAlertPolling()}
  else{
    startAlertPolling();
    // 切回来时立即刷新一次
    if(API_AVAILABLE){fetch(`${API_BASE}/watchlist/alerts?userId=${encodeURIComponent(getProfileId())}`,{signal:AbortSignal.timeout(10000)}).then(r=>r.json()).then(d=>{
      if(d.alerts?.length){const badge=document.getElementById('alertBadge');if(badge){badge.style.display='block';badge.textContent=d.alerts.length}}
    }).catch(()=>{})}
  }
});

// 交易时段自动启动（09:25-15:05 工作日）
function checkTradingHours(){
  const now=new Date();const h=now.getHours();const m=now.getMinutes();const day=now.getDay();
  if(day>=1&&day<=5&&((h===9&&m>=25)||h>=10)&&(h<15||(h===15&&m<=5))){startAlertPolling()}
  else{stopAlertPolling()}
}
// 每 5 分钟检查是否进入/离开交易时段
setInterval(checkTradingHours,300000);
checkTradingHours(); // 首次检查

// ---- 首页：风控预警摘要 ----
async function loadHomeRiskAlert(){
const el=document.getElementById('riskAlertSection');if(!el||!API_AVAILABLE)return;
try{
const vp=await fetch(API_BASE+'/dashboard',{signal:AbortSignal.timeout(15000)}).then(r=>r.ok?r.json():null);
if(!vp)return;
const r=await fetch(API_BASE+'/risk-actions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({valuation_percentile:vp.valuation?.percentile||50,fear_greed:vp.fear_greed?.score||50}),signal:AbortSignal.timeout(10000)});
if(!r.ok)return;const data=await r.json();
const actions=(data.actions||[]).filter(a=>a.level==='danger'||a.level==='warning');
if(!actions.length){el.innerHTML='<div style="background:rgba(16,185,129,.06);border:1px solid rgba(16,185,129,.15);border-radius:12px;padding:10px 14px;margin-bottom:12px;font-size:12px;color:var(--green)">✅ 风控状态良好，暂无预警</div>';return}
el.innerHTML=`<div style="margin-bottom:12px">${actions.map(a=>{
const isD=a.level==='danger';
return`<div style="background:${isD?'rgba(239,68,68,.08)':'rgba(245,158,11,.08)'};border:1px solid ${isD?'rgba(239,68,68,.2)':'rgba(245,158,11,.2)'};border-radius:12px;padding:10px 14px;margin-bottom:6px;font-size:13px;color:${isD?'var(--red)':'#F59E0B'}">${isD?'🔴':'⚠️'} ${a.action}</div>`}).join('')}</div>`;
}catch(e){console.warn('Risk alert:',e)}}

// ---- 首页：资产配置建议 ----
async function loadHomeAllocationAdvice(){
const el=document.getElementById('allocationAdviceSection');if(!el||!API_AVAILABLE)return;
try{
const r=await fetch(API_BASE+'/allocation-advice',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:getUserId()}),signal:AbortSignal.timeout(10000)});
if(!r.ok)return;const data=await r.json();
if(!data.target)return;
const t=data.target||{};const c=data.current||{};const dev=data.deviation||{};
const advArr=Array.isArray(data.advice)?data.advice:[];
const summaryText=data.summary||'';
el.innerHTML=`<div style="background:var(--bg2);border-radius:var(--radius);padding:16px;margin-bottom:12px">
<div style="font-size:14px;font-weight:700;margin-bottom:10px">🎯 资产配置建议 <span style="font-size:11px;color:var(--text2);font-weight:400">${data.valuation_zone||''}</span></div>
${['stock','bond','cash'].map(k=>{
const label=k==='stock'?'股票类':k==='bond'?'债券类':'现金类';
const cur=Math.round(c[k]||0);const tgt=Math.round(t[k]||0);const d=Math.round(dev[k]||0);
const dColor=Math.abs(d)>15?'var(--red)':Math.abs(d)>5?'#F59E0B':'var(--green)';
return`<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
<div style="width:56px;font-size:12px;color:var(--text2)">${label}</div>
<div style="flex:1;height:6px;background:var(--bg3);border-radius:3px;overflow:hidden"><div style="height:100%;width:${Math.min(cur,100)}%;background:var(--accent);border-radius:3px"></div></div>
<div style="width:80px;font-size:11px;text-align:right">${cur}% <span style="color:var(--text2)">→</span> ${tgt}% <span style="color:${dColor};font-weight:600">${d>0?'+':''}${d}%</span></div>
</div>`}).join('')}
${summaryText?`<div style="font-size:12px;color:var(--text2);margin-top:6px;padding-top:8px;border-top:1px solid var(--bg3)">💡 ${summaryText}</div>`:''}
${advArr.length?advArr.map(a=>{const bg=a.direction==='reduce'?'rgba(239,68,68,.08)':'rgba(34,197,94,.08)';return`<div style="background:${bg};border-radius:8px;padding:8px 10px;margin-top:6px;font-size:12px">${a.message}</div>`}).join(''):''}
</div>`;
}catch(e){console.warn('Allocation advice:',e)}}

// ---- 配置资产弹窗（v3.0 动态推荐） ----
let _allocDynamicFunds = null; // 缓存动态推荐结果
let _allocPreference = 'fund'; // fund|stock|mix

function showAllocateAssets(){
const p=loadPortfolio();const profile=p.profile||'稳健型';
_allocPreference=p.preference||'fund';
const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()"><div class="modal-handle"></div>
<div class="modal-title">💰 配置新资产</div>
<div class="modal-subtitle">新存款/工资到账？AI 动态推荐最优配置</div>
<div class="manual-form" style="background:transparent;padding:0;margin-top:16px">
<div class="form-row"><div class="form-label">投入金额</div>
<div class="amount-input-wrap"><span class="amount-prefix">¥</span><input type="number" id="allocAmt" class="amount-input" placeholder="10000" style="padding-left:36px;padding-right:16px;font-size:20px"></div>
<div style="display:flex;gap:6px;margin-top:8px;flex-wrap:wrap">${[1000,3000,5000,10000,20000].map(v=>`<button onclick="document.getElementById('allocAmt').value=${v};updateAllocPreview()" style="background:var(--bg3);border:none;color:var(--text2);padding:6px 12px;border-radius:6px;font-size:12px;cursor:pointer">¥${fmtMoney(v)}</button>`).join('')}</div></div>
<div class="form-row"><div class="form-label">配置偏好</div>
<div style="display:flex;gap:6px">
<button id="prefFund" onclick="_allocPreference='fund';_allocDynamicFunds=null;_updatePrefBtns();loadDynamicAlloc()" style="flex:1;padding:8px;border-radius:8px;border:1px solid var(--bg3);font-size:12px;cursor:pointer">💰 纯基金</button>
<button id="prefStock" onclick="_allocPreference='stock';_allocDynamicFunds=null;_updatePrefBtns();loadDynamicAlloc()" style="flex:1;padding:8px;border-radius:8px;border:1px solid var(--bg3);font-size:12px;cursor:pointer">📈 纯股票</button>
<button id="prefMix" onclick="_allocPreference='mix';_allocDynamicFunds=null;_updatePrefBtns();loadDynamicAlloc()" style="flex:1;padding:8px;border-radius:8px;border:1px solid var(--bg3);font-size:12px;cursor:pointer">🔀 混合</button>
</div></div>
<div id="allocPreview" style="margin-top:16px"><div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:20px;height:20px;margin:0 auto 8px;border-width:2px"></div>AI 正在推荐最优配置...</div></div>
<button class="form-submit" onclick="executeAllocate()" id="allocBtn" disabled>✅ 我知道了，去录入持仓</button>
<div id="allocAdjustments" style="margin-top:8px;font-size:11px;color:var(--text2)"></div>
<div style="font-size:11px;color:var(--text2);text-align:center;margin-top:8px">按「${profile}」方案 · AI 动态推荐</div>
</div></div>`;
document.body.appendChild(o);
_updatePrefBtns();
document.getElementById('allocAmt').addEventListener('input',updateAllocPreview);
loadDynamicAlloc()}

function _updatePrefBtns(){
['fund','stock','mix'].forEach(k=>{
const btn=document.getElementById('pref'+k.charAt(0).toUpperCase()+k.slice(1));
if(btn){btn.style.background=_allocPreference===k?'var(--accent)':'transparent';btn.style.color=_allocPreference===k?'#fff':'var(--text2)'}});}

async function loadDynamicAlloc(){
const el=document.getElementById('allocPreview');
if(el)el.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:20px;height:20px;margin:0 auto 8px;border-width:2px"></div>AI 正在推荐最优配置...</div>';
if(!API_AVAILABLE){if(el)el.innerHTML='<div style="text-align:center;padding:12px;color:var(--text2)">后端离线，使用默认配置</div>';_allocDynamicFunds=null;updateAllocPreview();return}
const p=loadPortfolio();const profile=p.profile||'稳健型';
try{const r=await fetch(API_BASE+'/recommend-alloc?profile='+encodeURIComponent(profile)+'&with_ai=true&preference='+_allocPreference,{signal:AbortSignal.timeout(30000)});
if(r.ok){const d=await r.json();
if(d.allocations&&d.allocations.length){_allocDynamicFunds=d.allocations;
const adjEl=document.getElementById('allocAdjustments');
if(adjEl&&d.adjustments)adjEl.innerHTML=d.adjustments.map(a=>`<div style="padding:2px 0">${a}</div>`).join('');
updateAllocPreview();return}}}catch(e){console.warn('dynamic alloc:',e)}
_allocDynamicFunds=null;updateAllocPreview()}

function updateAllocPreview(){
const amt=parseFloat(document.getElementById('allocAmt')?.value)||0;
const el=document.getElementById('allocPreview');const btn=document.getElementById('allocBtn');
if(!el)return;
if(!amt||amt<=0){el.innerHTML='';if(btn)btn.disabled=true;return}
if(btn)btn.disabled=false;
const al=_allocDynamicFunds||(ALLOCATIONS[loadPortfolio().profile||'稳健型']||ALLOCATIONS['稳健型']);
const isDynamic=!!_allocDynamicFunds;
el.innerHTML=(isDynamic?`<div style="font-size:11px;color:var(--green);margin-bottom:8px;padding:4px 8px;background:rgba(16,185,129,.08);border-radius:6px">🤖 AI 动态推荐（${_allocPreference==='fund'?'纯基金':_allocPreference==='stock'?'纯股票':'混合'}）</div>`:'')+
al.map(a=>{
const fundAmt=Math.round(amt*a.pct/100);
const reason=a.aiReason?`<div style="font-size:10px;color:var(--accent);margin-top:2px">💡 ${a.aiReason}</div>`:'';
return`<div style="display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--bg3)">
<div style="width:10px;height:10px;border-radius:50%;background:${a.color};flex-shrink:0"></div>
<div style="flex:1"><div style="font-size:13px;font-weight:600">${a.fullName||a.name}</div><div style="font-size:11px;color:var(--text2)">${a.code} · ${a.pct}%${a.category?' · '+a.category:''}</div>${reason}</div>
<div style="font-size:15px;font-weight:700;color:var(--accent)">¥${fmtMoney(fundAmt)}</div></div>`}).join('')}

function executeAllocate(){
const amt=parseFloat(document.getElementById('allocAmt')?.value)||0;
if(!amt||amt<=0)return;
// v3.0: 不写入假数据，只保存偏好+跳转
const p=loadPortfolio();
p.preference=_allocPreference;
savePortfolio(p);
// 保存风险偏好到 agent_memory
if(API_AVAILABLE){
fetch(API_BASE+'/agent/preferences',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({userId:getProfileId(),risk_profile:p.profile||'稳健型',preference:_allocPreference,last_alloc_amount:amt})}).catch(()=>{})}
document.querySelector('.modal-overlay')?.remove();
alert('✅ 配置方案已保存！\n\n请到「持仓」页添加你实际买入的基金/股票。\n配置建议仅供参考，实际买入以你的操作为准。');
navigateTo('stocks')}

// ---- 配比历史弹窗 ----
let _compareSelection=[];
function showAllocHistory(){
const p=loadPortfolio();const hist=(p.history||[]).filter(h=>h.allocations&&h.allocations.length>0);
if(!hist.length){
const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()"><div class="modal-handle"></div>
<div class="modal-title">📋 配比历史</div>
<div style="text-align:center;padding:40px;color:var(--text2)"><div style="font-size:48px;margin-bottom:12px">📋</div>
<div style="font-size:14px">还没有配比记录</div>
<div style="font-size:12px;margin-top:8px">完成测评或使用「配置资产」后，这里会显示你的历次配比方案</div></div></div>`;
document.body.appendChild(o);return}
_compareSelection=[];
const sorted=hist.slice().reverse();
const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
const actionLabel={'allocate':'💰 配置资产','quiz_buy':'📝 测评配置','buy':'📝 测评配置'};
const prefLabel={'fund':'🏦 基金','stock':'📊 股票','mixed':'🔄 混合'};
let listHtml=sorted.map((h,idx)=>{
const d=new Date(h.date);
const dateStr=`${d.getMonth()+1}/${d.getDate()} ${d.getHours().toString().padStart(2,'0')}:${d.getMinutes().toString().padStart(2,'0')}`;
const totalIdx=hist.length-idx;
return`<div class="alloc-history-item" id="allocHistItem${idx}" style="background:var(--card);border-radius:12px;padding:14px;margin-bottom:10px;border:2px solid transparent;cursor:pointer;transition:border-color .2s" onclick="toggleAllocCompare(${idx})">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
<div><span style="font-size:14px;font-weight:700">#${totalIdx}</span> <span style="font-size:12px;color:var(--text2)">${dateStr}</span></div>
<div style="display:flex;gap:6px;align-items:center">
${h.preference?`<span style="font-size:10px;padding:2px 8px;background:rgba(139,92,246,.1);border-radius:4px;color:#A78BFA">${prefLabel[h.preference]||h.preference}</span>`:''}
<span style="font-size:10px;padding:2px 8px;background:rgba(245,158,11,.1);border-radius:4px;color:#F59E0B">${h.profile||'稳健型'}</span>
<span style="font-size:10px;padding:2px 8px;background:var(--bg3);border-radius:4px;color:var(--text2)">${actionLabel[h.action]||h.action}</span>
</div></div>
<div style="font-size:18px;font-weight:800;color:var(--accent);margin-bottom:8px">¥${fmtMoney(h.amount)}</div>
<div style="display:flex;flex-wrap:wrap;gap:4px">${h.allocations.map(a=>`<span style="font-size:11px;padding:3px 8px;background:var(--bg3);border-radius:6px;color:var(--text1)">${a.name} ${a.pct}% ¥${fmtMoney(a.amount)}</span>`).join('')}</div>
</div>`}).join('');
o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()" style="max-height:85vh;overflow-y:auto"><div class="modal-handle"></div>
<div class="modal-title">📋 配比历史 <span style="font-size:12px;color:var(--text2);font-weight:400">${hist.length}次</span></div>
<div style="font-size:12px;color:var(--text2);margin-bottom:12px">点击选中两条记录可并排对比差异</div>
<div id="allocCompareBar" style="display:none;position:sticky;top:0;background:var(--accent);color:#000;padding:10px 14px;border-radius:10px;margin-bottom:12px;font-size:13px;font-weight:700;text-align:center;cursor:pointer;z-index:10" onclick="showAllocCompare()">🔍 对比选中的 2 条记录</div>
${listHtml}</div>`;
document.body.appendChild(o)}

function toggleAllocCompare(idx){
const item=document.getElementById('allocHistItem'+idx);
const pos=_compareSelection.indexOf(idx);
if(pos>=0){_compareSelection.splice(pos,1);if(item)item.style.borderColor='transparent'}
else{if(_compareSelection.length>=2){const old=_compareSelection.shift();const oldEl=document.getElementById('allocHistItem'+old);if(oldEl)oldEl.style.borderColor='transparent'}
_compareSelection.push(idx);if(item)item.style.borderColor='var(--accent)'}
const bar=document.getElementById('allocCompareBar');
if(bar)bar.style.display=_compareSelection.length===2?'block':'none'}

function showAllocCompare(){
const p=loadPortfolio();const hist=(p.history||[]).filter(h=>h.allocations&&h.allocations.length>0).slice().reverse();
if(_compareSelection.length!==2)return;
const [a,b]=[hist[_compareSelection[0]],hist[_compareSelection[1]]];
if(!a||!b)return;
const fmtDate=d=>{const dt=new Date(d);return`${dt.getMonth()+1}/${dt.getDate()} ${dt.getHours().toString().padStart(2,'0')}:${dt.getMinutes().toString().padStart(2,'0')}`};
const prefLabel={'fund':'🏦 基金','stock':'📊 股票','mixed':'🔄 混合'};
// 合并所有基金/股票 code
const allCodes=[...new Set([...a.allocations.map(x=>x.code),...b.allocations.map(x=>x.code)])];
let diffHtml=allCodes.map(code=>{
const aItem=a.allocations.find(x=>x.code===code);
const bItem=b.allocations.find(x=>x.code===code);
const name=(aItem||bItem).name;
const aPct=aItem?aItem.pct:0;const bPct=bItem?bItem.pct:0;
const aAmt=aItem?aItem.amount:0;const bAmt=bItem?bItem.amount:0;
const pctDiff=bPct-aPct;const amtDiff=bAmt-aAmt;
const diffColor=pctDiff>0?'var(--green)':pctDiff<0?'var(--red)':'var(--text2)';
return`<div style="display:flex;align-items:center;padding:8px 0;border-bottom:1px solid var(--bg3);gap:8px">
<div style="flex:1;font-size:13px;font-weight:600">${name}</div>
<div style="width:70px;text-align:center;font-size:12px">${aPct}%<br><span style="font-size:11px;color:var(--text2)">¥${fmtMoney(aAmt)}</span></div>
<div style="width:50px;text-align:center;font-size:12px;font-weight:700;color:${diffColor}">${pctDiff>0?'+':''}${pctDiff}%</div>
<div style="width:70px;text-align:center;font-size:12px">${bPct}%<br><span style="font-size:11px;color:var(--text2)">¥${fmtMoney(bAmt)}</span></div>
</div>`}).join('');
const amtDiffTotal=b.amount-a.amount;const amtDiffColor=amtDiffTotal>0?'var(--green)':amtDiffTotal<0?'var(--red)':'var(--text2)';
document.querySelector('.modal-overlay')?.remove();
const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()" style="max-height:85vh;overflow-y:auto"><div class="modal-handle"></div>
<div class="modal-title">🔍 配比对比</div>
<div style="display:flex;gap:12px;margin-bottom:16px">
<div style="flex:1;background:rgba(59,130,246,.08);border:1px solid rgba(59,130,246,.2);border-radius:10px;padding:10px;text-align:center">
<div style="font-size:11px;color:var(--text2)">旧配比 · ${fmtDate(a.date)}</div>
<div style="font-size:16px;font-weight:800;color:var(--accent)">¥${fmtMoney(a.amount)}</div>
<div style="font-size:10px;color:var(--text2)">${a.profile} ${prefLabel[a.preference]||''}</div></div>
<div style="flex:1;background:rgba(16,185,129,.08);border:1px solid rgba(16,185,129,.2);border-radius:10px;padding:10px;text-align:center">
<div style="font-size:11px;color:var(--text2)">新配比 · ${fmtDate(b.date)}</div>
<div style="font-size:16px;font-weight:800;color:var(--green)">¥${fmtMoney(b.amount)}</div>
<div style="font-size:10px;color:var(--text2)">${b.profile} ${prefLabel[b.preference]||''}</div></div>
</div>
<div style="text-align:center;margin-bottom:12px;font-size:13px">金额变化: <span style="color:${amtDiffColor};font-weight:700">${amtDiffTotal>0?'+':''}¥${fmtMoney(amtDiffTotal)}</span></div>
<div style="display:flex;padding:6px 0;border-bottom:2px solid var(--bg3);margin-bottom:4px;font-size:11px;color:var(--text2);font-weight:600">
<div style="flex:1">基金/股票</div><div style="width:70px;text-align:center">旧</div><div style="width:50px;text-align:center">差异</div><div style="width:70px;text-align:center">新</div></div>
${diffHtml}
<button class="form-submit" style="margin-top:16px" onclick="document.querySelector('.modal-overlay')?.remove();showAllocHistory()">← 返回历史</button>
</div>`;
document.body.appendChild(o)}

// ---- 问卷 ----
function renderQuiz(){const q=QUESTIONS[currentQuestion];$('#app').innerHTML=`<div class="quiz-header fade-up"><div class="progress-bar-bg"><div class="progress-bar-fill" style="width:${currentQuestion/QUESTIONS.length*100}%"></div></div><div class="quiz-step">第${currentQuestion+1}/${QUESTIONS.length}题</div></div><div class="question-card"><div class="question-emoji">${q.emoji}</div><div class="question-text">${q.question}</div><div class="options stagger">${q.options.map((o,i)=>`<button class="option-btn" onclick="selectAnswer(${i},${o.score})">${o.text}</button>`).join('')}</div></div>`}
let _selectedPreference='fund';
function renderAmountInput(){const presets=[100000,200000,300000,500000,1000000];$('#app').innerHTML=`<div class="quiz-header"><div class="progress-bar-bg"><div class="progress-bar-fill" style="width:100%"></div></div><div class="quiz-step">最后一步 ✨</div></div><div class="amount-section"><div class="question-emoji">🎯</div><div class="question-text">你具体想投多少钱？</div><div style="font-size:14px;color:var(--text2);margin-bottom:12px">输入金额，算出每个篮子该放多少</div><div class="amount-input-wrap"><span class="amount-prefix">¥</span><input class="amount-input" type="number" id="amtIn" placeholder="500000" value="${AMOUNT_MAP[answers[0]]||''}" oninput="onAmtChange()" inputmode="numeric"><span class="amount-suffix">元</span></div><div class="amount-quick">${presets.map(a=>`<button class="quick-btn" onclick="setAmt(${a})">${fmtMoney(a)}</button>`).join('')}</div>
<div style="margin:20px 0 16px"><div style="font-size:15px;font-weight:700;margin-bottom:10px">🎨 投资偏好</div>
<div style="display:flex;gap:8px" id="prefBtns">
<button class="pref-btn active" data-pref="fund" onclick="selectPreference('fund')" style="flex:1;padding:14px 8px;border-radius:12px;border:2px solid var(--accent);background:rgba(245,158,11,.1);color:var(--text);font-size:13px;cursor:pointer;text-align:center"><div style="font-size:24px;margin-bottom:4px">🏦</div><div style="font-weight:700">纯基金</div><div style="font-size:10px;color:var(--text2);margin-top:2px">6只经典基金<br>稳健长持</div></button>
<button class="pref-btn" data-pref="stock" onclick="selectPreference('stock')" style="flex:1;padding:14px 8px;border-radius:12px;border:2px solid transparent;background:var(--card);color:var(--text);font-size:13px;cursor:pointer;text-align:center"><div style="font-size:24px;margin-bottom:4px">📊</div><div style="font-weight:700">纯股票</div><div style="font-size:10px;color:var(--text2);margin-top:2px">AI选股TOP6<br>高收益高波动</div></button>
<button class="pref-btn" data-pref="mixed" onclick="selectPreference('mixed')" style="flex:1;padding:14px 8px;border-radius:12px;border:2px solid transparent;background:var(--card);color:var(--text);font-size:13px;cursor:pointer;text-align:center"><div style="font-size:24px;margin-bottom:4px">🔄</div><div style="font-weight:700">混合</div><div style="font-size:10px;color:var(--text2);margin-top:2px">基金+股票<br>攻守兼备</div></button>
</div></div>
<button class="generate-btn" id="genBtn" onclick="genResult()" ${AMOUNT_MAP[answers[0]]?'':'disabled'}>生成我的配置方案 →</button></div>`;onAmtChange()}

function selectPreference(pref){
_selectedPreference=pref;
document.querySelectorAll('#prefBtns button').forEach(b=>{
  const isPref=b.dataset.pref===pref;
  b.style.borderColor=isPref?'var(--accent)':'transparent';
  b.style.background=isPref?'rgba(245,158,11,.1)':'var(--card)';
})}

// ---- 结果页 ----
let _recAdjustments=[];let _recAiComments={};let _recMarketData={};
async function renderResult(){const ts=answers.reduce((s,a)=>s+a,0);const pf=getProfile(ts);let al=ALLOCATIONS[pf.name];const amt=selectedAmount;currentProfile=pf;
const pref=_selectedPreference||'fund';
// 尝试从后端获取动态配置 + 配置理由（替代硬编码）
_recAdjustments=[];_recAiComments={};_recMarketData={};
if(API_AVAILABLE){try{const r=await fetch(API_BASE+'/recommend-alloc?profile='+encodeURIComponent(pf.name)+'&with_ai=true&preference='+pref,{signal:AbortSignal.timeout(30000)});if(r.ok){const d=await r.json();if(d.allocations&&d.allocations.length)al=d.allocations;_recAdjustments=d.adjustments||[];_recAiComments=d.aiComments||{};_recMarketData=d.marketData||{}}}catch(e){console.warn('recommend-alloc fallback:',e)}}
currentAllocs=al;
// 保存偏好到 portfolio
const pp=loadPortfolio();pp.preference=pref;savePortfolio(pp);
const prefLabels={'fund':'🏦 纯基金','stock':'📊 纯股票','mixed':'🔄 混合'};
const gR=calcReturns(al,amt,'good'),mR=calcReturns(al,amt,'mid'),bR=calcReturns(al,amt,'bad');
const adjHtml=_recAdjustments.length?`<div style="background:rgba(139,92,246,.08);border:1px solid rgba(139,92,246,.2);border-radius:12px;padding:12px;margin:8px 0"><div style="font-size:13px;font-weight:700;color:#8B5CF6;margin-bottom:8px">🤖 AI 配置逻辑</div><div style="font-size:12px;line-height:1.8;color:var(--text1)">${_recAdjustments.map(a=>`<div>${a}</div>`).join('')}</div>${_recMarketData.valuationPct?`<div style="font-size:11px;color:var(--text2);margin-top:6px">数据来源：沪深300估值 ${_recMarketData.valuationPct}% · 恐贪指数 ${_recMarketData.fearGreed}</div>`:''}</div>`:'';
$('#app').innerHTML=`<div class="result-page">
<div class="profile-card"><div class="profile-emoji">${pf.emoji}</div><div class="profile-name" style="color:${pf.color}">你是「${pf.name}」投资者</div><div class="profile-desc">${pf.desc}</div><div class="profile-period">建议投资周期：${pf.period} · 偏好：${prefLabels[pref]||pref}</div></div>
${adjHtml}
<div class="section-title">📊 你的${fmtMoney(amt)}这样分</div>
<div class="chart-card"><div class="chart-wrap"><canvas id="allocChart"></canvas></div><div class="alloc-list">${al.map(a=>`<div class="alloc-item" onclick="showFundDetail('${a.code}')"><div class="alloc-dot" style="background:${a.color}"></div><div class="alloc-name">${a.name}</div><div class="alloc-pct">${a.pct}%</div><div class="alloc-money">${fmtFull(Math.round(amt*a.pct/100))}</div></div>`).join('')}</div></div>
<div class="section-title">📋 照着买</div>
<div class="shopping-list">${al.map((a,i)=>{const fd=FUND_DETAILS[a.code];const etfHint=fd&&fd.etfCode?` · 场内ETF:${fd.etfCode}`:'';const aiC=_recAiComments[a.code];const aiR=a.aiReason;const isStock=a.assetType==='stock'||(a.code&&/^(sh|sz)\d{6}$/.test(a.code));const typeTag=isStock?'<span style="display:inline-block;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:600;background:rgba(239,68,68,.15);color:#EF4444;margin-left:6px">📊 个股</span>':a.code==='余额宝'?'<span style="display:inline-block;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:600;background:rgba(16,185,129,.15);color:#10B981;margin-left:6px">💵 货基</span>':'<span style="display:inline-block;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:600;background:rgba(59,130,246,.15);color:#3B82F6;margin-left:6px">🏦 基金</span>';const platformText=a.code==='余额宝'?'留在余额宝/零钱通':isStock?'⚠️ 仅券商APP可买（如东方财富/同花顺/雪球）':'支付宝·天天基金·券商均可买';return`<div class="shop-item" onclick="showFundDetail('${a.code}')"><div class="shop-num">${i+1}</div><div class="shop-detail"><div class="shop-fund-name">${a.fullName||a.name}${typeTag}</div><span class="shop-code">${a.code}${etfHint}</span><span class="shop-platform"${isStock?' style="color:#EF4444"':''}>${platformText}</span>${aiR&&!aiC?`<div style="margin-top:6px;padding:6px 10px;background:rgba(59,130,246,.08);border-radius:8px;font-size:12px;color:#60A5FA;line-height:1.5">🤖 ${aiR}</div>`:''}${aiC?`<div style="margin-top:6px;padding:6px 10px;background:rgba(139,92,246,.08);border-radius:8px;font-size:12px;color:#A78BFA;line-height:1.5">🤖 ${aiC}</div>`:''}${liveNavData[a.code]?`<div class="shop-live-nav">📡 净值:${liveNavData[a.code].nav}(${liveNavData[a.code].date})</div>`:''}</div><div class="shop-amount">${fmtFull(Math.round(amt*a.pct/100))}</div></div>`}).join('')}</div>
<div style="background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.2);border-radius:12px;padding:12px;margin:8px 0;font-size:12px;color:var(--text2)"><div style="font-weight:600;color:#F59E0B;margin-bottom:6px">💰 各渠道费率对比</div><div style="line-height:1.8">🏆 <b>券商场内买ETF</b>：最便宜，仅收佣金（万2左右），适合大额<br>🥈 <b>支付宝/天天基金/理财通</b>：申购费1折（约0.1-0.15%），适合定投<br>🥉 <b>基金公司官网直销</b>：4-6折，仅限自家产品<br>❌ <b>银行柜台</b>：原价~8折，不推荐<br><span style="color:#F59E0B">👉 点击基金名查看各基金的详细购买渠道建议</span></div></div>
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
<div class="bottom-actions"><button class="action-btn green" onclick="confirmPurchase()">✅ 我知道了，去录入持仓</button><button class="action-btn secondary" onclick="restart()">🔄 重新测评</button></div>
<div class="footer-disclaimer">⚠️ 本工具仅供参考学习，不构成投资建议。投资有风险，入市需谨慎。</div></div>`;
renderNav();setTimeout(()=>drawAllocChart(al),100);setTimeout(()=>drawProjChart(amt,mR/amt),200);loadSignals()}

async function loadSignals(){
const el=document.getElementById('signalsSection');if(!el)return;
if(!API_AVAILABLE){return}
el.innerHTML=`<div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>正在分析市场信号...</div>`;
try{
const r=await fetch(API_BASE+'/daily-signal',{signal:AbortSignal.timeout(30000)});
if(!r.ok)throw new Error('signal fetch failed');
const d=await r.json();

// 综合信号大卡片
const bgMap={STRONG_BUY:'rgba(16,185,129,.12)',BUY:'rgba(16,185,129,.08)',HOLD:'rgba(245,158,11,.08)',SELL:'rgba(239,68,68,.08)',STRONG_SELL:'rgba(239,68,68,.12)'};
const borderMap={STRONG_BUY:'rgba(16,185,129,.3)',BUY:'rgba(16,185,129,.2)',HOLD:'rgba(245,158,11,.2)',SELL:'rgba(239,68,68,.2)',STRONG_SELL:'rgba(239,68,68,.3)'};
const labelMap={STRONG_BUY:'强烈买入 🟢',BUY:'建议买入 🟢',HOLD:'持有观望 🟡',SELL:'建议减仓 🟠',STRONG_SELL:'强烈减仓 🔴'};

let html=`<div class="section-title">🤖 今日量化信号 <span style="font-size:11px;color:var(--accent);font-weight:400">V4.5 · 12维多因子</span></div>`;
setExplain('signal','量化信号解读','钱袋子 V4.5 多因子信号系统融合了12个维度的数据（借鉴幻方量化）：\n\n📊 技术面(25%)：RSI(8%) + MACD(10%) + 布林带(7%)\n📈 基本面(30%)：估值(18%) + 股息率(5%) + 股债性价比(7%)\n💰 资金面(20%)：北向资金(10%) + 融资融券(5%) + SHIBOR(5%)\n😊 情绪面(15%)：恐惧贪婪(8%) + LLM新闻情绪(7%)\n🏛️ 宏观面(10%)：PMI+M2\n\n每个维度打分(-100~+100)，加权平均后得出综合信号。\n\n当前综合得分：'+(d.score||0)+'\n置信度：'+Math.round(d.confidence||0)+'%\n\n'+((d.details||[]).map(x=>(x.category||'')+' | '+x.name+'('+x.weight+')：'+x.detail).join('\n'))+'\n\n⚠️ 量化信号仅供参考，不构成投资建议。');
html+=`<div style="background:${bgMap[d.overall]||bgMap.HOLD};border:1px solid ${borderMap[d.overall]||borderMap.HOLD};border-radius:16px;padding:16px;margin-bottom:12px;cursor:pointer" onclick="showExplain('signal')">
<div style="display:flex;justify-content:space-between;align-items:center">
<div><div style="font-size:20px;font-weight:900">${labelMap[d.overall]||'持有观望'}</div><div style="font-size:12px;color:var(--text2);margin-top:4px">${d.date||''} · 综合得分 ${d.score||0} · 置信度 ${Math.round(d.confidence||0)}%</div></div>
<div style="font-size:11px;color:var(--accent)">点击看详情 ›</div></div>
<div style="font-size:13px;margin-top:8px;line-height:1.6">${d.summary||''}</div></div>`;

// V4.5 因子分组展示
const catIcons={'技术面':'📊','基本面':'📈','资金面':'💰','情绪面':'😊','宏观面':'🏛️'};
const catColors={'技术面':'#3B82F6','基本面':'#10B981','资金面':'#F59E0B','情绪面':'#EC4899','宏观面':'#8B5CF6'};
if(d.factorGroups){
html+=`<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:6px;margin-bottom:12px">`;
Object.entries(d.factorGroups).forEach(([cat,grp])=>{
const catScore=grp.totalWeight>0?Math.round(grp.weightedScore/grp.totalWeight):0;
const c=catColors[cat]||'var(--text2)';
const barW=Math.min(Math.abs(catScore),100);
const barDir=catScore>=0?'right':'left';
html+=`<div style="background:var(--card);border-radius:10px;padding:8px 10px">
<div style="font-size:11px;font-weight:700;color:${c}">${catIcons[cat]||''} ${cat} <span style="font-weight:400;opacity:.7">${(grp.totalWeight*100).toFixed(0)}%</span></div>
<div style="font-size:16px;font-weight:900;color:${catScore>10?'var(--green)':catScore<-10?'var(--red)':'var(--text1)'};margin-top:2px">${catScore>0?'+':''}${catScore}</div>
<div style="height:3px;background:var(--bg);border-radius:2px;margin-top:4px;overflow:hidden"><div style="height:100%;width:${barW}%;background:${catScore>=0?'var(--green)':'var(--red)'};border-radius:2px;float:${barDir}"></div></div>
<div style="font-size:10px;color:var(--text2);margin-top:3px">${grp.factors.map(f=>'<span style="color:'+(f.score>10?'var(--green)':f.score<-10?'var(--red)':'var(--text2)')+'">'+f.name+(f.score>0?'+':'')+f.score+'</span>').join(' ')}</div></div>`;
});
html+=`</div>`}

// V4.5 新闻情绪卡片
if(d.sentiment&&d.sentiment.available){
const s=d.sentiment;
const sentColor=s.score>20?'var(--green)':s.score<-20?'var(--red)':'var(--accent)';
setExplain('sentiment','LLM新闻情绪分析','🤖 用AI（DeepSeek/GPT）对最新新闻进行情绪打分：\n\n情绪得分：'+s.score+' ('+(s.level||'中性')+')\n分析来源：'+(s.source==='llm'?'AI模型':'关键词匹配')+'\n'+(s.reason||'')+'\n\n📰 分析的新闻标题：\n'+(s.headlines||[]).map((h,i)=>(i+1)+'. '+h).join('\n')+'\n\n💡 新闻情绪是短期市场方向的参考，不要因为单日情绪做决策。');
html+=`<div style="background:var(--card);border-radius:12px;padding:12px;margin-bottom:12px;cursor:pointer" onclick="showExplain('sentiment')">
<div style="display:flex;justify-content:space-between;align-items:center">
<div><div style="font-size:13px;font-weight:700">🤖 新闻情绪 <span style="font-size:11px;font-weight:400;color:var(--text2)">${s.source==='llm'?'AI分析':'关键词'}</span></div>
<div style="font-size:11px;color:var(--text2);margin-top:2px">${s.reason||s.level||'中性'}</div></div>
<div style="text-align:right"><div style="font-size:18px;font-weight:900;color:${sentColor}">${s.score>0?'+':''}${s.score}</div>
<div style="font-size:10px;color:var(--text2)">${s.level||'中性'}</div></div></div></div>`}

// 大师策略
if(d.masterStrategies&&d.masterStrategies.length){
html+=`<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px">`;
d.masterStrategies.forEach((ms,i)=>{
const msKey='master_'+i;
setExplain(msKey,ms.icon+' '+ms.master+'的投资策略','💡 核心理念：'+ms.philosophy+'\n\n'+ms.message+'\n\n⚠️ 大师策略基于当前市场数据自动生成分析，仅供参考。');
const msColor=ms.signal==='STRONG_BUY'||ms.signal==='BUY'||ms.signal==='HOLD_BUY'?'var(--green)':ms.signal==='SELL'||ms.signal==='STRONG_SELL'?'var(--red)':'var(--accent)';
const msLabel=ms.signal==='STRONG_BUY'?'强烈买入':ms.signal==='BUY'?'建议买入':ms.signal==='HOLD_BUY'?'可以建仓':ms.signal==='SELL'?'建议减仓':ms.signal==='STRONG_SELL'?'强烈减仓':'持有观望';
html+=`<div style="background:var(--card);border-radius:12px;padding:12px;cursor:pointer" onclick="showExplain('${msKey}')">
<div style="font-size:14px;font-weight:700">${ms.icon} ${ms.master}</div>
<div style="font-size:12px;color:${msColor};margin-top:4px;font-weight:600">${msLabel}</div>
<div style="font-size:11px;color:var(--text2);margin-top:4px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden">${ms.message||ms.philosophy}</div></div>`;
});
html+=`</div>`}

// 智能定投建议
if(d.smartDca){
const sc=d.smartDca;
setExplain('smartdca','智能定投策略','🧠 智能定投 vs 固定定投：\n\n固定定投：每月投相同金额。\n智能定投：根据估值动态调整——低估多买、高估少买。\n\n当前估值百分位：'+sc.valuationPct+'%\n本月倍率：'+sc.multiplier+'x\n'+sc.advice+'\n\n📊 倍率对照表：\n• 估值 <20% → 1.5x（极度低估，多买）\n• 20-30% → 1.3x（低估，适当多买）\n• 30-50% → 1.1x（偏低，略多）\n• 50-70% → 1.0x（正常）\n• 70-85% → 0.7x（偏高，少买）\n• >85% → 0.3x（高估，大幅减少）\n\n历史回测：智能定投比固定定投长期多赚约15-20%。\n\n⚠️ 仅供参考，不构成投资建议。');
html+=`<div style="background:var(--card);border-radius:12px;padding:12px;margin-bottom:12px;cursor:pointer" onclick="showExplain('smartdca')">
<div style="display:flex;justify-content:space-between;align-items:center">
<div><div style="font-size:13px;font-weight:700">🧠 智能定投建议</div><div style="font-size:11px;color:var(--text2);margin-top:2px">${sc.advice}</div></div>
<div style="text-align:right"><div style="font-size:16px;font-weight:900;color:var(--accent)">${sc.multiplier}x</div><div style="font-size:11px;color:var(--text2)">本月倍率</div></div></div></div>`}

// 回测按钮
html+=`<div style="text-align:center"><button onclick="showBacktest()" style="background:transparent;border:1px solid var(--accent);color:var(--accent);padding:8px 20px;border-radius:20px;font-size:12px;cursor:pointer">📊 查看历史回测数据</button></div>`;

el.innerHTML=html;
}catch(e){console.warn('Signal load failed:',e);el.innerHTML=''}
}

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

<div id="riskActionsSection"></div>
<div id="riskMetricsSection"><div style="text-align:center;padding:12px;font-size:12px;color:var(--text2)">${API_AVAILABLE?'正在加载风控体检...':''}</div></div>

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
pe.innerHTML=`<div class="pnl-change ${c}">${sg}${fmtFull(Math.round(pnl.totalPnl))}(${sg}${pnl.totalPnlPct.toFixed(2)}%)</div><div class="pnl-sub">当前市值${fmtFull(Math.round(pnl.totalMarket))}</div>`}}}catch{}}
// 异步加载风控指标
if(API_AVAILABLE){loadRiskMetrics();loadRiskActions()}}

async function loadRiskMetrics(){
try{const r=await fetch(API_BASE+'/risk-metrics',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:getUserId()}),signal:AbortSignal.timeout(15000)});
if(!r.ok)return;const rm=await r.json();
const el=document.getElementById('riskMetricsSection');if(!el)return;
const conc=rm.concentration||{};const dd=rm.drawdown||{};const corr=rm.correlation||{};const alerts=rm.alerts||[];
const concColor=conc.level==='高度集中'?'var(--red)':conc.level==='适度集中'?'var(--accent)':'var(--green)';
const ddColor=dd.level==='严重回撤'?'var(--red)':dd.level==='中度回撤'?'var(--accent)':'var(--green)';
const corrColor=corr.avg>0.6?'var(--red)':corr.avg>0.4?'var(--accent)':'var(--green)';
setExplain('risk_hhi','持仓集中度(HHI)','HHI(赫芬达尔指数) = 每只基金占比的平方和 × 10000\n\n📊 当前HHI：'+conc.hhi+'\n📊 最大单品占比：'+conc.max_single+'%\n📊 评级：'+conc.level+'\n\n🔍 怎么看：\n• HHI < 3000 → 分散良好 ✅\n• 3000-5000 → 适度集中 ⚠️\n• > 5000 → 高度集中 🔴\n\n💡 "不要把鸡蛋放在一个篮子里"——分散投资是最基本的风控。');
setExplain('risk_dd','回撤监控','回撤 = 从最高点跌了多少。\n\n📊 当前回撤：'+dd.current+'%\n📊 评级：'+dd.level+'\n\n🔍 怎么看：\n• < 10% → 正常波动\n• 10-20% → 需要注意，检查基本面\n• > 20% → 严重回撤，要认真审视持仓\n\n⚠️ 最大回撤是投资中最重要的风险指标之一。\n💡 控制回撤的关键是分散配置+止盈纪律。');
setExplain('risk_corr','相关性分析','相关性 = 持仓基金之间的涨跌联动程度。\n\n📊 平均相关性：'+corr.avg+'\n📊 分析：'+corr.detail+'\n\n🔍 怎么看：\n• < 0.3 → 低相关，对冲效果好 ✅\n• 0.3-0.6 → 中等相关\n• > 0.6 → 高相关，涨跌同步 ⚠️\n\n💡 股+债+黄金 是经典低相关组合。\n全买股票型基金 = 高相关 = 风险集中。');
let alertHtml='';
if(alerts.length){alertHtml=alerts.map(a=>{
const ic=a.severity==='danger'?'🔴':a.severity==='warning'?'⚠️':'💡';
const bg=a.severity==='danger'?'rgba(239,68,68,.1)':a.severity==='warning'?'rgba(245,158,11,.1)':'rgba(59,130,246,.08)';
return`<div style="background:${bg};border-radius:8px;padding:8px 10px;margin-bottom:6px;font-size:12px">${ic} ${a.message}</div>`}).join('')}
el.innerHTML=`<div class="section-title" style="margin-top:20px">🛡️ 风控体检 <span style="font-size:11px;color:var(--accent);font-weight:400">借鉴幻方CVaR</span></div>
${alertHtml}
<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px">
<div style="background:var(--card);border-radius:10px;padding:10px;cursor:pointer" onclick="showExplain('risk_hhi')">
<div style="font-size:11px;color:var(--text2)">集中度 HHI</div>
<div style="font-size:18px;font-weight:900;color:${concColor};margin-top:2px">${conc.hhi}</div>
<div style="font-size:10px;color:${concColor}">${conc.level}</div></div>
<div style="background:var(--card);border-radius:10px;padding:10px;cursor:pointer" onclick="showExplain('risk_dd')">
<div style="font-size:11px;color:var(--text2)">当前回撤</div>
<div style="font-size:18px;font-weight:900;color:${ddColor};margin-top:2px">${dd.current}%</div>
<div style="font-size:10px;color:${ddColor}">${dd.level}</div></div>
<div style="background:var(--card);border-radius:10px;padding:10px;cursor:pointer" onclick="showExplain('risk_corr')">
<div style="font-size:11px;color:var(--text2)">相关性</div>
<div style="font-size:18px;font-weight:900;color:${corrColor};margin-top:2px">${corr.avg}</div>
<div style="font-size:10px;color:${corrColor}">${corr.detail.slice(0,8)}</div></div></div>`}catch(e){console.warn('Risk metrics load failed:',e)}}

// 风控硬阈值执行建议（借鉴豆包方案+幻方量化）
async function loadRiskActions(){
try{const r=await fetch(API_BASE+'/risk-actions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:getUserId()}),signal:AbortSignal.timeout(15000)});
if(!r.ok)return;const data=await r.json();
const el=document.getElementById('riskActionsSection');if(!el)return;
const actions=data.actions||[];const summary=data.summary||'';const level=data.risk_level||'safe';
if(!actions.length){el.innerHTML=`<div style="margin-top:16px;padding:12px 14px;border-radius:12px;background:rgba(34,197,94,.08);border:1px solid rgba(34,197,94,.2)">
<div style="font-size:13px;font-weight:700;color:var(--green)">🟢 风控指令</div>
<div style="font-size:12px;color:var(--green);margin-top:4px">${summary}</div></div>`;return}
const borderColor=level==='danger'?'rgba(239,68,68,.3)':level==='warning'?'rgba(245,158,11,.3)':'rgba(34,197,94,.2)';
const bgColor=level==='danger'?'rgba(239,68,68,.06)':level==='warning'?'rgba(245,158,11,.06)':'rgba(34,197,94,.06)';
const headerColor=level==='danger'?'var(--red)':level==='warning'?'var(--accent)':'var(--green)';
const actionsHtml=actions.map(a=>{
const bg=a.level==='danger'?'rgba(239,68,68,.1)':a.level==='warning'?'rgba(245,158,11,.1)':'rgba(59,130,246,.08)';
const border=a.level==='danger'?'rgba(239,68,68,.2)':a.level==='warning'?'rgba(245,158,11,.2)':'rgba(59,130,246,.15)';
return`<div style="background:${bg};border:1px solid ${border};border-radius:8px;padding:10px 12px;margin-top:6px">
<div style="font-size:13px;font-weight:600;line-height:1.5">${a.action}</div>
<div style="font-size:11px;color:var(--text2);margin-top:3px">📋 ${a.rule}｜${a.detail}</div></div>`}).join('');
el.innerHTML=`<div style="margin-top:16px;padding:14px;border-radius:12px;background:${bgColor};border:1px solid ${borderColor}">
<div style="display:flex;align-items:center;justify-content:space-between">
<div style="font-size:14px;font-weight:800;color:${headerColor}">⚡ 风控执行指令</div>
<div style="font-size:11px;color:${headerColor};font-weight:600">${summary}</div></div>
${actionsHtml}</div>`}catch(e){console.warn('Risk actions load failed:',e)}}

// 大类资产配置建议（总览页）
async function loadAllocationAdvice(){
try{const r=await fetch(API_BASE+'/allocation-advice',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:getUserId()}),signal:AbortSignal.timeout(15000)});
if(!r.ok)return;const data=await r.json();
const el=document.getElementById('allocationSection');if(!el)return;
const t=data.target||{};const c=data.current||{};const dev=data.deviation||{};
const advice=data.advice||[];const zone=data.valuation_zone||'适中';const valPct=data.valuation_pct||50;
const zoneColor=zone==='低估'?'var(--green)':zone==='高估'?'var(--red)':'var(--accent)';
// 配置饼图（简化CSS饼图）
const stockC=c.stock||0;const bondC=c.bond||0;const cashC=c.cash||0;
const stockT=t.stock||65;const bondT=t.bond||25;const cashT=t.cash||10;
// 生成偏离度指示
function devBar(label,icon,cur,tgt,devVal){
const color=Math.abs(devVal)>8?(devVal>0?'var(--red)':'var(--accent)'):'var(--green)';
const sign=devVal>0?'+':'';
return`<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid rgba(148,163,184,.06)">
<div style="font-size:16px">${icon}</div>
<div style="flex:1">
<div style="display:flex;justify-content:space-between;font-size:12px"><span style="color:var(--text2)">${label}</span><span style="font-weight:700">${cur.toFixed(0)}% <span style="color:var(--text2);font-weight:400">/ 目标${tgt}%</span></span></div>
<div style="height:4px;background:rgba(148,163,184,.1);border-radius:2px;margin-top:4px;overflow:hidden">
<div style="height:100%;width:${Math.min(cur/Math.max(tgt,1)*100,150)}%;background:${color};border-radius:2px;transition:width .3s"></div></div>
</div>
<div style="font-size:12px;font-weight:700;color:${color};min-width:45px;text-align:right">${sign}${devVal}%</div></div>`}
const adviceHtml=advice.length?advice.map(a=>{
const bg=a.direction==='reduce'?'rgba(239,68,68,.08)':'rgba(34,197,94,.08)';
const border=a.direction==='reduce'?'rgba(239,68,68,.15)':'rgba(34,197,94,.15)';
return`<div style="background:${bg};border:1px solid ${border};border-radius:8px;padding:8px 10px;margin-top:6px;font-size:12px;line-height:1.5">${a.message}</div>`}).join(''):'<div style="font-size:12px;color:var(--green);margin-top:6px">✅ 各资产类别偏离度在合理范围内</div>';
el.innerHTML=`<div class="dashboard-card-title">🎯 资产配置建议 <span style="font-size:11px;color:${zoneColor};font-weight:600">估值${zone}(${valPct}%)</span></div>
<div style="font-size:12px;color:var(--text2);margin-bottom:10px">${data.summary||''}</div>
${devBar('股票类','📊',stockC,stockT,dev.stock||0)}
${devBar('债券类','🏦',bondC,bondT,dev.bond||0)}
${devBar('现金类','💵',cashC,cashT,dev.cash||0)}
<div style="margin-top:8px;font-size:11px;color:var(--text2);padding:6px 8px;background:rgba(148,163,184,.04);border-radius:6px">📐 目标比例根据估值水平动态调整：低估→股票${ALLOCATION_PROFILES?.low?.stock*100||75}% / 高估→股票${ALLOCATION_PROFILES?.high?.stock*100||45}%</div>
${adviceHtml}`}catch(e){console.warn('Allocation advice load failed:',e);const el=document.getElementById('allocationSection');if(el)el.innerHTML=''}}
const ALLOCATION_PROFILES={low:{stock:0.75,bond:0.15,cash:0.10},mid:{stock:0.65,bond:0.25,cash:0.10},high:{stock:0.45,bond:0.35,cash:0.20}};

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
${h?`<button class="action-btn secondary" style="color:var(--red)" onclick="if(confirm('删除所有交易记录？')){document.querySelector('.modal-overlay')?.remove();deleteFundTxns('${code}')}">🗑️ 删除持仓</button>`:''}
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

function deleteFundTxns(code){const txns=loadTxns().filter(t=>t.code!==code);saveTxns(txns);
if(API_AVAILABLE)fetch(API_BASE+'/portfolio/transaction/delete',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({userId:getUserId(),code})}).catch(()=>{});
renderPortfolio()}

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
let chatModel='deepseek-chat';
let chatModelList=[];
async function loadModelList(){try{const r=await fetch(API_BASE+'/models',{signal:AbortSignal.timeout(5000)});if(r.ok){const d=await r.json();chatModelList=d.models||[];if(d.default)chatModel=localStorage.getItem('chatModel')||d.default}}catch{chatModelList=[{id:'deepseek-chat',name:'DeepSeek V3',provider:'deepseek'}]}}
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
return `<div style="margin-bottom:12px"><div style="font-size:12px;font-weight:700;margin-bottom:8px;color:var(--text1)">📐 V4.5 高级回测指标</div>
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

// ---- 资讯页 ----
let insightTab='overview';
function _insightTabs(){
const all=[
['overview','📊 总览'],['deepimpact','🔍 深度分析'],['riskassess','🛡️ 风控评估'],['fundpick','🔍 选基'],['stockpick','🧠 选股'],['news','📰 新闻'],['policy','🏛️ 政策'],['tech','📈 技术'],['macro','📊 宏观'],['global','🌐 全球'],['signals','📡 信号'],['scorecard','📊 成绩单'],['doctor','🏥 体检'],['steward','🤖 管家'],['factorictest','🔬 因子检验'],['montecarlo','🎲 蒙特卡洛'],['aipredict','🤖 AI预测'],['geneticfactor','🧬 遗传因子'],['optimizer','⚡ 组合优化'],['altdata','📡 另类数据'],['rlposition','🎮 RL仓位'],['llmfactor','🧠 LLM因子'],['weekly','📋 周报']];
const simple=['overview','news','policy','doctor','steward'];
return isProMode()?all:all.filter(t=>simple.includes(t[0]))}
async function renderInsight(){currentPage='insight';renderNav();const tabs=_insightTabs();
$('#app').innerHTML=`<div class="insight-page fade-up"><div class="insight-header"><h2>📰 市场资讯</h2><p>${API_AVAILABLE?'实时数据更新中':'后端离线'} <button onclick="runDataAudit()" style="background:rgba(245,158,11,.15);border:1px solid rgba(245,158,11,.3);border-radius:6px;padding:2px 8px;font-size:11px;color:#F59E0B;cursor:pointer;margin-left:4px" id="auditBtn">🔍 数据体检</button></p></div><div class="section-tab-bar" id="insightTabBar">${tabs.map(t=>`<button class="section-tab ${insightTab===t[0]?'active':''}" data-tab="${t[0]}" onclick="insightTab='${t[0]}';renderInsight()">${t[1]}</button>`).join('')}</div><div id="insightContent"><div style="text-align:center;padding:40px;color:var(--text2)"><div class="loading-spinner" style="width:32px;height:32px;margin:0 auto 12px;border-width:3px"></div><div id="loadingMsg" style="margin-top:8px">正在加载市场数据...</div><div style="font-size:12px;color:var(--text3,#94a3b8);margin-top:8px">☁️ 免费云服务器，首次加载可能需要 10~30 秒</div></div></div></div>`;
// Tab栏自动滚动到选中位置
setTimeout(()=>{const bar=document.getElementById('insightTabBar');const active=bar&&bar.querySelector('.section-tab.active');if(active&&bar){active.scrollIntoView({behavior:'smooth',inline:'center',block:'nearest'})}},50);
if(!API_AVAILABLE){document.getElementById('insightContent').innerHTML='<div style="text-align:center;padding:40px;color:var(--text2)">后端离线，请启动后端服务获取实时数据</div>';return}
// 独立 tab 不需要 dashboard 数据，秒开
if(insightTab==='fundpick'){const el=document.getElementById('insightContent');if(el)renderFundPick(el);return}
// Phase 0: 新闻深度分析 + 风控评估
if(insightTab==='deepimpact'){const el=document.getElementById('insightContent');if(el)renderDeepImpact(el);return}
if(insightTab==='riskassess'){const el=document.getElementById('insightContent');if(el)renderRiskAssess(el);return}
if(insightTab==='stockpick'){const el=document.getElementById('insightContent');if(el)renderStockPick(el);return}
if(insightTab==='factorictest'){const el=document.getElementById('insightContent');if(el)renderFactorIC(el);return}
if(insightTab==='montecarlo'){const el=document.getElementById('insightContent');if(el)renderMonteCarlo(el);return}
if(insightTab==='aipredict'){const el=document.getElementById('insightContent');if(el)renderAIPredict(el);return}
if(insightTab==='geneticfactor'){const el=document.getElementById('insightContent');if(el)renderGeneticFactor(el);return}
if(insightTab==='optimizer'){const el=document.getElementById('insightContent');if(el)renderOptimizer(el);return}
if(insightTab==='altdata'){const el=document.getElementById('insightContent');if(el)renderAltData(el);return}
if(insightTab==='rlposition'){const el=document.getElementById('insightContent');if(el)renderRLPosition(el);return}
if(insightTab==='llmfactor'){const el=document.getElementById('insightContent');if(el)renderLLMFactor(el);return}
if(insightTab==='signals'){const el=document.getElementById('insightContent');if(el)renderSignalScout(el);return}
if(insightTab==='scorecard'){const el=document.getElementById('insightContent');if(el)renderScorecard(el);return}
if(insightTab==='doctor'){const el=document.getElementById('insightContent');if(el)renderDoctor(el);return}
if(insightTab==='steward'){const el=document.getElementById('insightContent');if(el)renderSteward(el);return}
if(insightTab==='weekly'){const el=document.getElementById('insightContent');if(el)renderWeeklyReport(el);return}
if(insightTab==='policy'){const el=document.getElementById('insightContent');if(el)renderInsightPolicy(el);return}
if(insightTab==='news'){const el=document.getElementById('insightContent');if(el){el.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>加载新闻中...</div>';try{const r=await fetch(API_BASE+'/news',{signal:AbortSignal.timeout(15000)});if(r.ok){const d=await r.json();renderInsightNews(el,{news:d.news||[]});}else{el.innerHTML='<div style="text-align:center;padding:40px;color:var(--text2)">新闻加载失败<br><button onclick="renderInsight()" style="margin-top:8px;padding:6px 16px;border-radius:8px;border:none;background:var(--accent);color:#fff;cursor:pointer">🔄 重试</button></div>';}}catch(e){el.innerHTML='<div style="text-align:center;padding:40px;color:var(--text2)">新闻加载超时<br><button onclick="renderInsight()" style="margin-top:8px;padding:6px 16px;border-radius:8px;border:none;background:var(--accent);color:#fff;cursor:pointer">🔄 重试</button></div>';}}return}
// 需要 dashboard 的 tab: overview / tech / macro
const loadStart=Date.now();const loadTimer=setInterval(()=>{const el=document.getElementById('loadingMsg');if(!el){clearInterval(loadTimer);return}const sec=Math.round((Date.now()-loadStart)/1000);if(sec>=5&&sec<15)el.textContent='正在从数据源抓取实时行情...';else if(sec>=15&&sec<25)el.textContent='数据量较大，还在努力加载中...';else if(sec>=25)el.textContent='快好了，感谢耐心等待 🙏'},3000);
const dash=await fetchDashboard();clearInterval(loadTimer);if(!dash){document.getElementById('insightContent').innerHTML='<div style="text-align:center;padding:40px;color:var(--text2)">数据加载失败，请稍后再试<br><button onclick="renderInsight()" style="margin-top:12px;padding:8px 20px;border-radius:8px;border:none;background:var(--accent);color:#fff;cursor:pointer">🔄 重新加载</button></div>';return}
const el=document.getElementById('insightContent');if(!el)return;
if(insightTab==='overview')renderInsightOverview(el,dash);
else if(insightTab==='tech')renderInsightTech(el,dash);
else if(insightTab==='macro')renderInsightMacro(el,dash);
else if(insightTab==='global')renderInsightGlobal(el)}

function renderV45FactorCards(d){
const nb=d.northbound||{};const mg=d.margin||{};const tr=d.treasury||{};const sh=d.shibor||{};const dv=d.dividend||{};
const cards=[];
if(nb.available){
const nbColor=nb.net_flow_5d>30?'var(--green)':nb.net_flow_5d<-30?'var(--red)':'var(--accent)';
setExplain('northbound','北向资金','北向资金 = 外资通过沪股通/深股通买入A股的钱，被称为"聪明钱"。\n\n📊 今日净流入：'+(nb.net_flow_today||0)+'亿\n📊 5日累计：'+(nb.net_flow_5d||0)+'亿\n📊 20日累计：'+(nb.net_flow_20d||0)+'亿\n📊 趋势：'+nb.trend+'\n\n🔍 怎么看：\n• 连续大幅流入（>100亿/5日）→ 外资看好A股\n• 连续大幅流出（<-100亿/5日）→ 外资避险\n• 短期波动不用太在意，看5日/20日趋势更有意义\n\n💡 北向资金占A股成交额约5-8%，影响力不小。');
cards.push(`<div style="background:var(--card);border-radius:12px;padding:12px;cursor:pointer" onclick="showExplain('northbound')">
<div style="font-size:12px;font-weight:700">💰 北向资金</div>
<div style="font-size:18px;font-weight:900;color:${nbColor};margin-top:4px">${nb.net_flow_today>0?'+':''}${nb.net_flow_today}亿</div>
<div style="font-size:11px;color:var(--text2);margin-top:2px">5日 ${nb.net_flow_5d>0?'+':''}${nb.net_flow_5d}亿 · ${nb.trend}</div></div>`)}
if(mg.available){
const mgColor=mg.trend.includes('上升')?'var(--red)':mg.trend.includes('下降')?'var(--green)':'var(--accent)';
setExplain('margin_factor','融资融券','融资融券 = 借钱/借股票来炒股，反映市场的杠杆情绪。\n\n📊 融资余额：'+mg.margin_balance+'亿\n📊 5日变化：'+(mg.margin_change_5d>0?'+':'')+mg.margin_change_5d+'%\n📊 趋势：'+mg.trend+'\n\n🔍 怎么看：\n• 融资余额快速上升（>3%/5日）→ 散户加杠杆，市场过热\n• 融资余额快速下降（<-3%/5日）→ 去杠杆，可能恐慌\n• 温和变化属于正常市场波动\n\n⚠️ 杠杆是双刃剑：放大收益也放大亏损。');
cards.push(`<div style="background:var(--card);border-radius:12px;padding:12px;cursor:pointer" onclick="showExplain('margin_factor')">
<div style="font-size:12px;font-weight:700">📊 融资融券</div>
<div style="font-size:18px;font-weight:900;color:${mgColor};margin-top:4px">${mg.margin_balance}亿</div>
<div style="font-size:11px;color:var(--text2);margin-top:2px">5日 ${mg.margin_change_5d>0?'+':''}${mg.margin_change_5d}% · ${mg.trend}</div></div>`)}
if(sh.available){
const shColor=sh.trend.includes('收紧')?'var(--red)':sh.trend.includes('宽松')?'var(--green)':'var(--accent)';
setExplain('shibor_factor','SHIBOR 利率','SHIBOR = 上海银行间同业拆放利率，反映银行之间借钱的成本，是流动性"温度计"。\n\n📊 隔夜利率：'+sh.overnight+'%\n📊 趋势：'+sh.trend+'\n\n🔍 怎么看：\n• 利率下降/宽松 → 市场钱多，利好股市\n• 利率上升/收紧 → 市场缺钱，股市承压\n• 隔夜利率通常在1-2%之间波动\n\n💡 央行货币政策最先反映在 SHIBOR 上。');
cards.push(`<div style="background:var(--card);border-radius:12px;padding:12px;cursor:pointer" onclick="showExplain('shibor_factor')">
<div style="font-size:12px;font-weight:700">🏦 SHIBOR</div>
<div style="font-size:18px;font-weight:900;color:${shColor};margin-top:4px">${sh.overnight}%</div>
<div style="font-size:11px;color:var(--text2);margin-top:2px">隔夜 · ${sh.trend}</div></div>`)}
if(dv.available){
const dvColor=dv.level.includes('高')?'var(--green)':dv.level.includes('低')?'var(--red)':'var(--accent)';
setExplain('dividend_factor','股息率','股息率 = 每年分红 ÷ 股价，衡量"持有这个指数每年能拿多少分红"。\n\n📊 当前股息率：'+dv.dividend_yield+'%\n📊 水平：'+dv.level+(dv.percentile!=null?'\n📊 百分位：'+dv.percentile+'%':'')+'\n\n🔍 怎么看：\n• 股息率高（百分位>70%）→ 价值凸显，适合长期持有\n• 股息率低（百分位<40%）→ 成长偏好期，市场追涨\n• 高股息策略在熊市特别有保护作用\n\n💡 巴菲特说："如果你不打算持有一只股票10年，那就不要持有10分钟。"');
cards.push(`<div style="background:var(--card);border-radius:12px;padding:12px;cursor:pointer" onclick="showExplain('dividend_factor')">
<div style="font-size:12px;font-weight:700">📈 股息率</div>
<div style="font-size:18px;font-weight:900;color:${dvColor};margin-top:4px">${dv.dividend_yield}%</div>
<div style="font-size:11px;color:var(--text2);margin-top:2px">${dv.level}${dv.percentile!=null?' · P'+dv.percentile+'%':''}</div></div>`)}
if(tr.available){
const trColor=tr.equity_premium.includes('极有')?'var(--green)':tr.equity_premium.includes('债券更')?'var(--red)':'var(--accent)';
setExplain('treasury_factor','国债收益率 / 股债性价比','10年期国债收益率是"无风险回报率"——不承担任何风险就能拿到的收益。\n\n📊 10Y国债：'+tr.yield_10y+'%\n📊 变动：'+(tr.yield_change>0?'+':'')+tr.yield_change+'%\n📊 股债性价比：'+tr.equity_premium+'\n\n🔍 怎么看：\n• 股票盈利收益率(1/PE) 远 > 国债 → 股市更有吸引力\n• 股票盈利收益率 ≈ 国债 → 股债差不多\n• 国债收益率 > 股票 → 买债更划算\n\n💡 巴菲特经常用这个指标判断"股票是否值得买"。');
cards.push(`<div style="background:var(--card);border-radius:12px;padding:12px;cursor:pointer" onclick="showExplain('treasury_factor')">
<div style="font-size:12px;font-weight:700">🔗 国债/股债比</div>
<div style="font-size:18px;font-weight:900;color:${trColor};margin-top:4px">${tr.yield_10y}%</div>
<div style="font-size:11px;color:var(--text2);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${tr.equity_premium||'数据计算中'}</div></div>`)}
if(!cards.length)return'';
return`<div class="dashboard-card"><div class="dashboard-card-title">🔬 V4.5 多因子数据 <span style="font-size:11px;color:var(--accent);font-weight:400">借鉴幻方量化</span></div>
<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:8px">${cards.join('')}</div></div>`}

function renderInsightOverview(el,d){
const fgi=d.fearGreed||{};const val=d.valuation||{};const tech=d.technical||{};const news=(d.news||[]).slice(0,5);const macro=(d.macro||[]).slice(0,3);
const fgiColor=fgi.score>=60?'var(--green)':fgi.score<=35?'var(--red)':'var(--accent)';
const valColor=val.percentile<30?'var(--green)':val.percentile>70?'var(--red)':'var(--accent)';
const valPct=Math.min(Math.max(val.percentile||50,0),100);
const dims=fgi.dimensions||{};
setExplain('fgi','恐惧贪婪指数','这个指数衡量市场情绪——大家是"怕得要死"还是"贪得无厌"。\n\n📊 怎么理解：\n• 0~25 = 极度恐惧（别人恐惧时贪婪？）\n• 25~45 = 恐惧\n• 45~55 = 中性\n• 55~75 = 贪婪\n• 75~100 = 极度贪婪（别人贪婪时恐惧？）\n\n🎯 当前：'+(fgi.score||50).toFixed(0)+' - '+(fgi.level||'中性')+'\n\n🔍 怎么用：\n• 巴菲特说"别人恐惧我贪婪"\n• 极度恐惧(<25)往往是好的买入时机\n• 极度贪婪(>75)要小心追高\n\n💡 但这只是参考，不是万能的买卖信号。');
setExplain('valuation','估值水平','估值就是看"这个市场现在贵不贵"。\n\n📊 核心指标 PE（市盈率）：\n• PE = 股价 ÷ 每股收益\n• PE 低 → 相对便宜\n• PE 高 → 相对贵\n\n🔍 百分位怎么看：\n• 当前：'+(val.percentile||50)+'%\n• 意思是"历史上只有 '+(val.percentile||50)+'% 的时候比现在便宜"\n• <30% → 便宜区间，适合加仓\n• 30~70% → 正常区间\n• >70% → 偏贵，谨慎追高\n\n🎯 PE: '+(val.current_pe||'-')+'\n\n💡 一句话：估值越低，安全边际越高，长期赚钱概率越大。');
el.innerHTML=`
<div class="dashboard-card" onclick="showExplain('fgi')" style="cursor:pointer"><div class="dashboard-card-title">😱 恐惧贪婪指数 <span style="font-size:11px;color:var(--accent)">点击了解 ›</span></div>
<div class="fgi-gauge"><div class="fgi-score" style="color:${fgiColor}">${(fgi.score||50).toFixed(0)}</div><div class="fgi-label">${fgi.level||'中性'}</div></div>
${Object.keys(dims).length?`<div class="fgi-dims">${Object.values(dims).map(d=>`<div class="fgi-dim"><div class="fgi-dim-label">${d.label}</div><div class="fgi-dim-val">${d.value}</div></div>`).join('')}</div>`:''}
</div>
<div class="dashboard-card" onclick="showExplain('valuation')" style="cursor:pointer"><div class="dashboard-card-title">📊 估值水平 <span style="font-size:11px;color:var(--accent)">点击了解 ›</span></div>
<div style="display:flex;justify-content:space-between;align-items:center"><div><div style="font-size:24px;font-weight:900;color:${valColor}">${val.percentile||50}%</div><div style="font-size:13px;color:var(--text2)">${val.index||'沪深300'}·${val.level||'适中'}</div></div><div style="text-align:right;font-size:12px;color:var(--text2)">PE: ${val.current_pe||'-'}${val.date?'<br>'+val.date:''}</div></div>
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
${renderV45FactorCards(d)}
<div id="allocationSection" class="dashboard-card"><div style="text-align:center;padding:12px;font-size:12px;color:var(--text2)">正在计算资产配置建议...</div></div>
<div id="impactSection" class="dashboard-card"><div class="dashboard-card-title">🔗 事件影响分析</div><div style="text-align:center;padding:16px;font-size:12px;color:var(--text2)">分析新闻对持仓的影响中...</div></div>
<div style="text-align:center;font-size:11px;color:#475569;margin-top:16px">更新于 ${new Date(d.updatedAt).toLocaleString('zh-CN')}</div>`;
// 异步加载资产配置建议
loadAllocationAdvice();
// 异步加载事件影响分析
fetch(API_BASE+'/news/impact',{signal:AbortSignal.timeout(30000)}).then(r=>r.json()).then(data=>{const sec=document.getElementById('impactSection');if(!sec||!data.impacts||!data.impacts.length){if(sec)sec.innerHTML='<div class="dashboard-card-title">🔗 事件影响分析</div><div style="padding:12px;font-size:12px;color:var(--text2)">暂无显著影响事件</div>';return}sec.innerHTML='<div class="dashboard-card-title">🔗 事件对你持仓的影响</div>'+data.impacts.map(imp=>{const bull=imp.bullish.length?'<span style="color:var(--green);font-size:11px">📈'+imp.bullish.join(',')+'</span>':'';const bear=imp.bearish.length?'<span style="color:var(--red);font-size:11px">📉'+imp.bearish.join(',')+'</span>':'';return'<div style="padding:8px 0;border-bottom:1px solid rgba(148,163,184,.08)"><div style="display:flex;align-items:center;gap:6px"><span style="background:rgba(245,158,11,.12);color:#F59E0B;font-size:11px;padding:2px 6px;border-radius:4px">'+imp.tag+'</span>'+bull+' '+bear+'</div><div style="font-size:12px;color:var(--text2);margin-top:4px">'+imp.impact+'</div></div>'}).join('')+'<div style="font-size:11px;color:#475569;margin-top:8px;padding-top:8px;border-top:1px solid rgba(148,163,184,.08)">分析了 '+data.total_news_analyzed+' 条新闻 · 基于关键词匹配</div>'}).catch(()=>{})}

function renderInsightNews(el,d){
const news=d.news||[];
// 情绪/影响标签匹配（增强版7类）
const tagMap=[{kw:['降息','降准','宽松','LPR','利好','上涨','增持','加仓','反弹'],tag:'🟢 利好',color:'var(--green)'},{kw:['加息','收紧','缩表','利空','下跌','减持','暴跌','回调'],tag:'🔴 利空',color:'var(--red)'},{kw:['关税','制裁','贸易战','中美'],tag:'⚠️ 贸易',color:'#F59E0B'},{kw:['战争','冲突','地缘','中东','俄乌'],tag:'🛡️ 地缘',color:'#F59E0B'},{kw:['半导体','芯片','AI','科技','人工智能'],tag:'🚀 科技',color:'var(--blue)'},{kw:['房地产','楼市','房价','限购'],tag:'🏠 地产',color:'#A78BFA'},{kw:['央行','货币','MLF','逆回购'],tag:'🏦 央行',color:'#06B6D4'}];
function getTag(title){for(const t of tagMap){if(t.kw.some(k=>title.includes(k)))return t}return null}
el.innerHTML=`<div class="dashboard-card"><div class="dashboard-card-title">📰 市场新闻（${news.length}条）</div>${news.length?news.map(n=>{const t=getTag(n.title);const tagHtml=t?`<span style="font-size:10px;padding:1px 6px;border-radius:3px;background:rgba(255,255,255,.06);color:${t.color};margin-left:4px;white-space:nowrap">${t.tag}</span>`:'';return`<div class="news-item" onclick="${n.url?`window.open('${n.url}','_blank')`:''}"${n.url?'':' style="cursor:default"'}><div class="news-icon">📰</div><div class="news-content"><div class="news-title">${n.title}${tagHtml}</div><div class="news-meta">${n.source||''}${n.time?' · '+n.time:''}</div></div>${n.url?'<div class="news-arrow">›</div>':''}</div>`}).join(''):'<div style="text-align:center;padding:20px;color:var(--text2)">暂无新闻</div>'}</div>
<div style="text-align:center;margin-top:12px"><button class="action-btn secondary" style="display:inline-block;min-width:auto;padding:10px 24px" onclick="renderInsight()">🔄 刷新</button></div>`}

async function renderInsightPolicy(el){
el.innerHTML='<div style="text-align:center;padding:40px;color:var(--text2)"><div class="loading-spinner" style="width:32px;height:32px;margin:0 auto 12px;border-width:3px"></div>正在加载政策新闻...</div>';
// 并行加载：传统政策新闻 + 分主题政策新闻 + AI影响分析（allSettled 防止一个超时拖垮全部）
const results = await Promise.allSettled([
  fetchPolicyNews(),
  API_AVAILABLE ? fetch(API_BASE+'/policy/all-topics',{signal:AbortSignal.timeout(45000)}).then(r=>r.ok?r.json():{}).catch(()=>({})) : Promise.resolve({}),
  API_AVAILABLE ? fetch(API_BASE+'/policy/impact',{signal:AbortSignal.timeout(30000)}).then(r=>r.ok?r.json():{}).catch(()=>({})) : Promise.resolve({})
]);
const news = results[0].status==='fulfilled' ? results[0].value : [];
const topicsResp = results[1].status==='fulfilled' ? results[1].value : {};
const impactResp = results[2].status==='fulfilled' ? results[2].value : {};
const topics = topicsResp.topics || topicsResp || {};
if(!news.length&&!Object.keys(topics).length){el.innerHTML='<div style="text-align:center;padding:40px;color:var(--text2)">暂无政策新闻</div>';return}
const policyNews=news.filter(n=>n.category==='policy');
const intlNews=news.filter(n=>n.category==='international');
// 主题分类
const topicMap=[['房地产','🏠','realestate'],['科技','🚀','tech'],['公积金','🏦','gongjijin'],['经济','📊','economy'],['房改','🏗️','fanggai']];
let topicHtml='';
topicMap.forEach(([label,icon,key])=>{
const items=Array.isArray(topics[key])?topics[key]:(topics[key]?.news||[]);
if(items.length){topicHtml+=`<div class="dashboard-card"><div class="dashboard-card-title">${icon} ${label}政策（${items.length}条）</div>${items.slice(0,5).map(n=>`<div class="news-item" onclick="${n.url?`window.open('${n.url}','_blank')`:''}"><div class="news-icon">${icon}</div><div class="news-content"><div class="news-title">${n.title}</div><div class="news-meta">${n.source||''}${n.time?' · '+n.time:''}</div></div>${n.url?'<div class="news-arrow">›</div>':''}</div>`).join('')}</div>`}});
// AI 影响分析卡
let impactHtml='';
if(impactResp.analysis&&impactResp.source==='ai'){
impactHtml=`<div class="dashboard-card" style="border:1px solid rgba(245,158,11,.2)"><div class="dashboard-card-title">🤖 AI 政策影响分析</div><div style="font-size:13px;color:var(--text1);line-height:1.7;white-space:pre-wrap;padding:8px 0">${impactResp.analysis}</div><div style="font-size:11px;color:#475569;margin-top:8px">分析了 ${impactResp.newsCount||0} 条政策新闻 · DeepSeek</div></div>`}
el.innerHTML=`
${impactHtml}
${topicHtml}
${policyNews.length?`<div class="dashboard-card"><div class="dashboard-card-title">🇨🇳 国内政策</div>${policyNews.map(n=>`<div class="news-item" onclick="${n.url?`window.open('${n.url}','_blank')`:''}"${n.url?'':' style="cursor:default"'}><div class="news-icon">📜</div><div class="news-content"><div class="news-title">${n.title}</div><div class="news-meta">${n.source||''}${n.time?' · '+n.time:''}</div></div>${n.url?'<div class="news-arrow">›</div>':''}</div>`).join('')}</div>`:''}
${intlNews.length?`<div class="dashboard-card"><div class="dashboard-card-title">🌍 国际动态</div>${intlNews.map(n=>`<div class="news-item" onclick="${n.url?`window.open('${n.url}','_blank')`:''}"${n.url?'':' style="cursor:default"'}><div class="news-icon">🌐</div><div class="news-content"><div class="news-title">${n.title}</div><div class="news-meta">${n.source||''}${n.time?' · '+n.time:''}</div></div>${n.url?'<div class="news-arrow">›</div>':''}</div>`).join('')}</div>`:''}
${!policyNews.length&&!intlNews.length&&!topicHtml?news.map(n=>`<div class="news-item"><div class="news-icon">📰</div><div class="news-content"><div class="news-title">${n.title}</div></div></div>`).join(''):''}
<div style="text-align:center;margin-top:12px"><button class="action-btn secondary" style="display:inline-block;min-width:auto;padding:10px 24px" onclick="insightTab='policy';renderInsight()">🔄 刷新</button></div>
<div style="text-align:center;font-size:11px;color:#475569;margin-top:8px">关键词: 政策/央行/关税/中美/美联储/地缘/半导体等</div>`}

function renderInsightTech(el,d){
const tech=d.technical||{};const m=tech.macd||{};const b=tech.bollinger||{};
setExplain('rsi','RSI 相对强弱指标','RSI 就像温度计，测量市场的"热度"。\n\n📊 数值含义：\n• 50 = 中性，多空力量平衡\n• 超过 70 = 市场过热（大家都在买，可能快到顶了）\n• 低于 30 = 市场过冷（大家都在卖，可能快到底了）\n\n🎯 当前 RSI = '+(tech.rsi||50)+'\n'+(tech.rsi>70?'⚠️ 偏高，短期可能回调，不宜追涨':tech.rsi<30?'💡 偏低，可能被超卖，可以关注抄底机会':'✅ 中性区间，市场情绪正常')+'\n\n💡 小贴士：RSI 不能单独使用，要结合其他指标一起看。');
setExplain('macd','MACD 指标','MACD 就像两条"均线赛跑"——快线(DIF)和慢线(DEA)。\n\n📊 核心概念：\n• DIF(快线) 和 DEA(慢线) 是两条趋势线\n• MACD柱 = 两线差值，代表动能强弱\n\n🔍 怎么看：\n• 金叉（快线上穿慢线）→ 可能要涨\n• 死叉（快线下穿慢线）→ 可能要跌\n• 柱子变长 = 趋势在加强\n• 柱子变短 = 趋势在减弱\n\n🎯 当前：'+(m.trend||'—')+'\nDIF='+(m.dif?.toFixed(2)||'—')+' DEA='+(m.dea?.toFixed(2)||'—')+'\n\n💡 小贴士：MACD 反应较慢，适合看中长期趋势，不适合抓短线。');
setExplain('bollinger','布林带','布林带就像给股价画了一条"通道"，有上中下三条线。\n\n📊 三条线：\n• 上轨('+(b.upper||'—')+') = 压力线，价格碰到容易回落\n• 中轨('+(b.middle||'—')+') = 移动平均线，大趋势方向\n• 下轨('+(b.lower||'—')+') = 支撑线，价格碰到容易反弹\n\n🔍 怎么看：\n• 现价在上轨附近 → 偏贵，可能回调\n• 现价在下轨附近 → 偏便宜，可能反弹\n• 通道变窄 → 即将有大波动（变盘信号）\n• 通道变宽 → 波动剧烈，注意风险\n\n🎯 当前：现价 '+(b.current||'—')+'，'+(b.position||'')+'\n\n💡 小贴士：布林带帮你判断价格"贵不贵"，但不能预测方向。');
el.innerHTML=`
<div class="dashboard-card" onclick="showExplain('rsi')" style="cursor:pointer"><div class="dashboard-card-title">📊 RSI 相对强弱指标 <span style="font-size:11px;color:var(--accent)">点击了解 ›</span></div>
<div style="text-align:center"><div style="font-size:48px;font-weight:900;color:${tech.rsi>70?'var(--red)':tech.rsi<30?'var(--green)':'var(--accent)'}">${tech.rsi||50}</div>
<div style="font-size:14px;color:var(--text2);margin-top:4px">${tech.rsi_signal||'中性'}</div>
<div class="val-bar" style="margin:12px 0"><div class="val-bar-fill" style="width:${tech.rsi||50}%;background:${tech.rsi>70?'var(--red)':tech.rsi<30?'var(--green)':'var(--accent)'}"></div></div>
<div style="display:flex;justify-content:space-between;font-size:11px;color:var(--text2)"><span>超卖 (&lt;30)</span><span>中性</span><span>超买 (&gt;70)</span></div></div></div>
<div class="dashboard-card" onclick="showExplain('macd')" style="cursor:pointer"><div class="dashboard-card-title">📈 MACD 指数平滑移动平均 <span style="font-size:11px;color:var(--accent)">点击了解 ›</span></div>
<div class="tech-grid"><div class="tech-item"><div class="tech-label">趋势</div><div class="tech-value" style="font-size:13px;color:${m.trend?.includes('金叉')||m.trend?.includes('多头')?'var(--green)':'var(--red)'}">${m.trend||'—'}</div></div>
<div class="tech-item"><div class="tech-label">DIF</div><div class="tech-value">${m.dif?.toFixed(2)||'—'}</div></div>
<div class="tech-item"><div class="tech-label">DEA</div><div class="tech-value">${m.dea?.toFixed(2)||'—'}</div></div>
<div class="tech-item"><div class="tech-label">MACD柱</div><div class="tech-value" style="color:${m.macd>0?'var(--green)':'var(--red)'}">${m.macd?.toFixed(2)||'—'}</div></div></div></div>
<div class="dashboard-card" onclick="showExplain('bollinger')" style="cursor:pointer"><div class="dashboard-card-title">📐 布林带 <span style="font-size:11px;color:var(--accent)">点击了解 ›</span></div>
<div class="tech-grid"><div class="tech-item"><div class="tech-label">上轨</div><div class="tech-value">${b.upper||'—'}</div></div>
<div class="tech-item"><div class="tech-label">中轨</div><div class="tech-value">${b.middle||'—'}</div></div>
<div class="tech-item"><div class="tech-label">下轨</div><div class="tech-value">${b.lower||'—'}</div></div>
<div class="tech-item"><div class="tech-label">现价</div><div class="tech-value">${b.current||'—'}</div></div></div>
<div style="text-align:center;margin-top:12px;font-size:13px;color:var(--text2)">${b.position||''}</div></div>
<div style="text-align:center;padding:16px;font-size:12px;color:#475569;line-height:1.6">💡 点击任意卡片查看白话解释 · 技术指标是辅助参考，需结合估值和基本面综合判断</div>`}

function renderInsightMacro(el,d){
const macro=d.macro||[];
const macroKeyMap={'CPI':'cpi','PMI':'pmi','M2':'m2','PPI':'ppi'};
el.innerHTML=`<div class="dashboard-card"><div class="dashboard-card-title">🏛️ 宏观经济数据 <span style="font-size:11px;color:var(--accent)">点击查看白话解释</span></div>
${macro.length?macro.map((e,i)=>{const mkey=Object.keys(macroKeyMap).find(k=>e.name.includes(k));const explainKey=mkey?macroKeyMap[mkey]:'macro_'+i;if(!mkey){setExplain(explainKey,e.name,'📊 '+e.name+'\n\n'+e.impact+'\n\n点击查看更多：可在百度搜索「'+e.name+' 最新数据」了解详情。')}return`<div class="macro-item" onclick="showExplain('${explainKey}')" style="cursor:pointer"><div class="macro-icon">${e.icon||'📅'}</div><div class="macro-info"><div class="macro-name">${e.name}</div><div class="macro-value">${e.value||'—'}</div><div class="macro-date">${e.date||''}</div><div class="macro-impact">${e.impact||''}</div></div><div class="news-arrow">›</div></div>`}).join(''):'<div style="text-align:center;padding:20px;color:var(--text2)">暂无数据</div>'}
</div>
<div style="padding:16px;font-size:12px;color:#475569;line-height:1.6">💡 点击任意数据查看白话解释 · 宏观数据影响市场整体方向</div>`}

// 全球市场数据页
async function renderInsightGlobal(el){
el.innerHTML='<div style="text-align:center;padding:40px;color:var(--text2)"><div class="loading-spinner" style="width:32px;height:32px;margin:0 auto 12px;border-width:3px"></div>正在加载全球市场数据...</div>';
try{
const [snap,impact]=await Promise.all([
fetch(API_BASE+'/global/snapshot').then(r=>r.json()),
fetch(API_BASE+'/global/impact').then(r=>r.json()).catch(()=>({analysis:'暂不可用',source:'none'}))
]);
const us=snap.us_indices||{};const fx=snap.forex||{};const fed=snap.fed_rate||{};const gpe=snap.global_pe||{};
let h='<div class="dashboard-card"><div class="dashboard-card-title">🌐 全球市场实时</div>';
// 美股三大指数
const idxArr=[['dji','道琼斯'],['spx','标普500'],['ixic','纳斯达克']];
h+=idxArr.map(([k,n])=>{const d=us[k];if(!d)return'';const c=d.change_pct>0?'var(--green)':d.change_pct<0?'var(--red)':'var(--text2)';return`<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid var(--bg3)"><div style="font-size:13px;font-weight:600">${d.change_pct>0?'📈':'📉'} ${n}</div><div style="text-align:right"><div style="font-size:14px;font-weight:700">${d.close.toLocaleString()}</div><div style="font-size:12px;color:${c};font-weight:600">${d.change_pct>0?'+':''}${d.change_pct}%</div></div></div>`}).join('');
// 外汇
if(fx.usdcny)h+=`<div style="display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--bg3)"><div style="font-size:13px">💱 美元/人民币</div><div style="font-size:14px;font-weight:700">${fx.usdcny.rate.toFixed(4)}</div></div>`;
// 美联储
if(fed.available)h+=`<div style="display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--bg3)"><div style="font-size:13px">🏛️ 美联储利率</div><div style="text-align:right"><div style="font-size:14px;font-weight:700">${fed.current_rate}%</div><div style="font-size:11px;color:var(--text2)">${fed.trend==='hiking'?'⬆️加息周期':fed.trend==='cutting'?'⬇️降息周期':'按兵不动'}</div></div></div>`;
// PE 对比
if(gpe.available&&gpe.us_pe&&gpe.cn_pe)h+=`<div style="display:flex;justify-content:space-between;padding:10px 0"><div style="font-size:13px">📊 PE 估值对比</div><div style="text-align:right"><div style="font-size:12px">🇺🇸 ${gpe.us_pe} vs 🇨🇳 ${gpe.cn_pe}</div><div style="font-size:11px;color:var(--text2)">${gpe.assessment||''}</div></div></div>`;
h+='</div>';
// DeepSeek 影响分析
if(impact.analysis){h+=`<div class="dashboard-card"><div class="dashboard-card-title">🤖 AI 全球→A股影响分析 <span style="font-size:10px;color:var(--text2)">${impact.source==='ai'?'DeepSeek':'数据'}</span></div><div style="font-size:13px;line-height:1.8;white-space:pre-wrap">${impact.analysis}</div></div>`}
el.innerHTML=h;
}catch(e){el.innerHTML=`<div style="text-align:center;padding:40px;color:var(--text2)">全球数据加载失败: ${e.message}</div>`}}

// 基金智能筛选页
let fundPickType='all';let fundPickSort='score';
function _fundTagsHTML(f){const r=f.returns;const tags=[];if(r['1y']!=null&&r['3m']!=null&&r['6m']!=null&&r['1y']>0&&r['3m']>0&&r['6m']>0)tags.push('📈稳定上涨');if(r['1y']!=null&&r['1y']>15)tags.push('\u{1F525}高收益');if(f.fee&&parseFloat(f.fee)<0.5)tags.push('💰低费率');if(r['3y']!=null&&r['3y']>30)tags.push('⭐长期优秀');let h='';if(tags.length)h+='<div style="display:flex;gap:4px;flex-wrap:wrap;margin-bottom:4px">'+tags.map(t=>'<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:rgba(16,185,129,.1);color:#6EE7B7">'+t+'</span>').join('')+'</div>';if(f.aiComment)h+='<div style="font-size:12px;color:#E0E7FF;padding:6px 10px;background:rgba(99,102,241,.08);border-radius:8px;line-height:1.5">\u{1F916} '+f.aiComment+'</div>';return h?'<div style="padding:4px 12px 8px 32px">'+h+'</div>':''}
function _fundPickBtnsHTML(){
return `<div id="fundPickTypeBar" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">
${[['all','全部'],['stock','股票型'],['bond','债券型'],['index','指数型'],['qdii','QDII']].map(([k,l])=>`<button class="section-tab ${fundPickType===k?'active':''}" onclick="fundPickType='${k}';_updateFundPickBtns();renderFundPickResult()" style="font-size:12px;padding:5px 10px">${l}</button>`).join('')}
</div>
<div id="fundPickSortBar" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px">
${[['score','📊 综合评分'],['1y','📈 近1年'],['3y','📈 近3年'],['ytd','📈 今年来']].map(([k,l])=>`<button class="section-tab ${fundPickSort===k?'active':''}" onclick="fundPickSort='${k}';_updateFundPickBtns();renderFundPickResult()" style="font-size:11px;padding:4px 8px">${l}</button>`).join('')}
</div>`}
function _updateFundPickBtns(){
const tb=document.getElementById('fundPickTypeBar');const sb=document.getElementById('fundPickSortBar');
if(tb)tb.innerHTML=[['all','全部'],['stock','股票型'],['bond','债券型'],['index','指数型'],['qdii','QDII']].map(([k,l])=>`<button class="section-tab ${fundPickType===k?'active':''}" onclick="fundPickType='${k}';_updateFundPickBtns();renderFundPickResult()" style="font-size:12px;padding:5px 10px">${l}</button>`).join('');
if(sb)sb.innerHTML=[['score','📊 综合评分'],['1y','📈 近1年'],['3y','📈 近3年'],['ytd','📈 今年来']].map(([k,l])=>`<button class="section-tab ${fundPickSort===k?'active':''}" onclick="fundPickSort='${k}';_updateFundPickBtns();renderFundPickResult()" style="font-size:11px;padding:4px 8px">${l}</button>`).join('')}

// Phase 0: 新闻深度影响分析（Pro 模式）
async function renderDeepImpact(el){
el.innerHTML=renderCard('🔍 新闻深度影响分析','loading');
try{
  const r=await fetch(`${API_BASE}/news/deep-impact`,{signal:AbortSignal.timeout(30000)});
  if(!r.ok)throw new Error(`HTTP ${r.status}`);
  const d=await r.json();
  const impacts=d.impacts||[];
  if(!impacts.length){el.innerHTML=renderCard('🔍 新闻深度影响分析','empty');return}
  el.innerHTML=`<div class="dashboard-card">
    <div class="dashboard-card-title">🔍 新闻深度影响分析（${impacts.length}条）</div>
    ${impacts.map(imp=>{
      const bull=imp.bullish?.length?`<span style="color:var(--green);font-size:11px">📈 ${imp.bullish.join(', ')}</span>`:'';
      const bear=imp.bearish?.length?`<span style="color:var(--red);font-size:11px">📉 ${imp.bearish.join(', ')}</span>`:'';
      return `<div style="padding:10px 0;border-bottom:1px solid rgba(148,163,184,.08)">
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
          <span style="background:rgba(245,158,11,.12);color:#F59E0B;font-size:11px;padding:2px 6px;border-radius:4px">${imp.tag||'事件'}</span>
          ${bull} ${bear}
        </div>
        <div style="font-size:13px;color:var(--text1);margin-top:6px;line-height:1.6">${imp.impact||imp.analysis||''}</div>
      </div>`}).join('')}
  </div>`;
}catch(e){el.innerHTML=renderCard('🔍 新闻深度影响分析','error')}}

// Phase 0: 新闻风控评估（Pro 模式）
async function renderRiskAssess(el){
el.innerHTML=renderCard('🛡️ 新闻风控评估','loading');
try{
  const r=await fetch(`${API_BASE}/news/risk-assess`,{signal:AbortSignal.timeout(30000)});
  if(!r.ok)throw new Error(`HTTP ${r.status}`);
  const d=await r.json();
  const level=d.risk_level||d.level||'unknown';
  const emoji=level==='low'?'🟢':level==='medium'?'🟡':'🔴';
  el.innerHTML=`<div class="dashboard-card" style="border-left:3px solid ${level==='high'?'var(--red)':level==='medium'?'#F59E0B':'var(--green)'}">
    <div class="dashboard-card-title">🛡️ 新闻风控评估</div>
    <div style="font-size:20px;font-weight:700;margin:8px 0">${emoji} 风险等级：${level==='low'?'低':level==='medium'?'中':level==='high'?'高':level}</div>
    <div style="font-size:13px;color:var(--text1);line-height:1.8">${d.summary||d.analysis||'暂无分析'}</div>
    ${d.headlines?.length?`<div style="margin-top:12px;font-size:12px;color:var(--text2)"><b>分析了 ${d.headlines.length} 条新闻</b></div>`:''}
    ${d.risk_factors?.length?`<div style="margin-top:8px">${d.risk_factors.map(f=>`<div style="padding:4px 0;font-size:12px;color:var(--text2)">⚠️ ${f}</div>`).join('')}</div>`:''}
  </div>`;
}catch(e){el.innerHTML=renderCard('🛡️ 新闻风控评估','error')}}

async function renderFundPick(el){
el.innerHTML=`<div class="dashboard-card" style="overflow:hidden">
<div class="dashboard-card-title">🔍 基金智能筛选</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:12px">多维度打分：近1年(30%)+近3年(20%)+近6月(15%)+近3月(10%)+稳定性(15%)+费率(10%)</div>
${_fundPickBtnsHTML()}
<div id="fundPickList"><div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>正在筛选基金...</div></div>
</div>`;
renderFundPickResult()}

async function renderFundPickResult(){
const listEl=document.getElementById('fundPickList');
if(!listEl)return;
listEl.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>正在筛选基金...</div>';
try{
const r=await fetch(API_BASE+'/fund-screen?fund_type='+fundPickType+'&sort_by='+fundPickSort+'&top_n=20',{signal:AbortSignal.timeout(30000)});
if(!r.ok)throw new Error('fetch failed');
const data=await r.json();
const funds=data.funds||[];
if(!funds.length){listEl.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)">暂无符合条件的基金</div>';return}
listEl.innerHTML=`<div style="font-size:11px;color:var(--text2);margin-bottom:8px">共筛选 ${data.total} 只基金，显示 TOP ${funds.length}</div>
${funds.map((f,i)=>{
const scoreColor=f.score>15?'var(--green)':f.score>5?'var(--accent)':'var(--red)';
const r1y=f.returns['1y'];const r3y=f.returns['3y'];const rytd=f.returns.ytd;
const r1yColor=r1y>0?'var(--green)':'var(--red)';
return`<div style="display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid rgba(148,163,184,.06);cursor:pointer" onclick="showExplain('fund_${f.code}')">
<div style="font-size:12px;color:var(--text2);min-width:20px;text-align:center;font-weight:700">${i+1}</div>
<div style="flex:1;min-width:0">
<div style="font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${f.name}</div>
<div style="font-size:11px;color:var(--text2);margin-top:2px">${f.code} · 费率${f.fee||'-'}</div></div>
<div style="text-align:right;min-width:70px">
<div style="font-size:14px;font-weight:800;color:${r1yColor}">${r1y!=null?(r1y>0?'+':'')+r1y+'%':'—'}</div>
<div style="font-size:10px;color:var(--text2)">近1年</div></div>
<div style="min-width:40px;text-align:right">
<div style="font-size:12px;font-weight:700;color:${scoreColor}">${f.score}</div>
<div style="font-size:10px;color:var(--text2)">评分</div></div></div>${_fundTagsHTML(f)}`}).join('')}
<div style="text-align:center;margin-top:12px"><button class="action-btn secondary" style="display:inline-block;min-width:auto;padding:10px 24px" onclick="renderFundPickResult()">🔄 刷新</button></div>`;
// 注册每只基金的白话弹窗
funds.forEach(f=>{
const r=f.returns;
setExplain('fund_'+f.code,f.name+' ('+f.code+')',
'📊 综合评分：'+f.score+'\n\n📈 收益表现：\n• 近3月：'+(r['3m']!=null?r['3m']+'%':'—')+'\n• 近6月：'+(r['6m']!=null?r['6m']+'%':'—')+'\n• 近1年：'+(r['1y']!=null?r['1y']+'%':'—')+'\n• 近3年：'+(r['3y']!=null?r['3y']+'%':'—')+'\n• 今年来：'+(r.ytd!=null?r.ytd+'%':'—')+'\n\n💰 费率：'+(f.fee||'—')+'\n\n💡 评分方法：近1年35%+近3年25%+近6月20%+近3月10%+费率加减分。仅供参考，不构成投资建议。',
{type:'fund',code:f.code,name:f.name,score:f.score,fee:f.fee||'',returns:r})
})}catch(e){console.warn('Fund pick failed:',e);listEl.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)">📡 数据源加载中，请稍后重试<br><span style="font-size:11px;opacity:0.6">（首次加载可能需要 10-30 秒）</span><br><button onclick="renderFundPickResult()" style="margin-top:8px;padding:6px 16px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:12px">🔄 重试</button></div>'}}

// AI 多因子选股页
function _stockTagsHTML(s){const sc=s.scores||{};const tags=[];if(sc.value>=70)tags.push('💰低估值');if(sc.momentum>=70)tags.push('📈强动量');if(sc.liquidity>=70)tags.push('🏦高流动');if(sc.risk>=80)tags.push('🛡️低风险');if(sc.quality>=75)tags.push('⭐高质量');if(sc.growth>=70)tags.push('🚀高成长');if(s.roe&&s.roe>20)tags.push('💎ROE>20%');if(s.gross_margin&&s.gross_margin>50)tags.push('🏆高毛利');let h='';if(tags.length)h+='<div style="display:flex;gap:4px;flex-wrap:wrap;margin-bottom:4px">'+tags.map(t=>'<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:rgba(99,102,241,.1);color:#818CF8">'+t+'</span>').join('')+'</div>';if(s.aiComment)h+='<div style="font-size:12px;color:#E0E7FF;padding:6px 10px;background:rgba(99,102,241,.08);border-radius:8px;line-height:1.5">\u{1F916} '+s.aiComment+'</div>';return h?'<div style="padding:4px 0 8px 34px;border-bottom:1px solid rgba(148,163,184,.04)">'+h+'</div>':''}
async function renderStockPick(el){
el.innerHTML=`<div class="dashboard-card" style="overflow:hidden">
<div class="dashboard-card-title">🧠 AI 多因子选股</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:8px">30因子7维打分 V3：AI 动态权重 + LLM 舆情 + 因子生成器加分</div>
<div style="font-size:11px;color:var(--accent);margin-bottom:12px;padding:6px 8px;background:rgba(245,158,11,.06);border-radius:6px">⚠️ 含真实财务数据（ROE/毛利率/净利率/现金流/负债率），DeepSeek 根据市场环境动态调权重。仅供参考，不构成投资建议。</div>
<div id="stockScreenMeta" style="display:none;font-size:11px;color:var(--accent);margin-bottom:8px;padding:6px 8px;background:rgba(59,130,246,.06);border-radius:6px"></div>
<div id="stockPickList"><div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>正在从 5000+ A股中筛选（AI 动态调权中）...</div></div>
</div>`;
try{
const r=await fetch(API_BASE+'/stock-screen?top_n=50',{signal:AbortSignal.timeout(60000)});
if(!r.ok)throw new Error('fetch failed');
const data=await r.json();
const stocks=data.stocks||[];
const listEl=document.getElementById('stockPickList');if(!listEl)return;
// 展示 V3 动态权重元信息
const metaEl=document.getElementById('stockScreenMeta');
if(metaEl&&(data.regime||data.weights)){
const regime=data.regime||'';
const weights=data.weights||{};
const wText=Object.entries(weights).map(([k,v])=>`${k}:${v}%`).join(' · ');
metaEl.innerHTML=`🧠 市场判断: <b>${regime}</b> | 动态权重: ${wText}`;
metaEl.style.display='block';
}
if(!stocks.length){listEl.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)">'+(data.error||'暂无数据')+'</div>';return}
listEl.innerHTML=`<div style="font-size:11px;color:var(--text2);margin-bottom:8px">从 ${data.total} 只股票中筛选 TOP ${stocks.length}</div>
<div style="display:grid;grid-template-columns:30px 1fr 70px 50px;gap:4px;font-size:11px;color:var(--text2);font-weight:600;padding:6px 0;border-bottom:1px solid rgba(148,163,184,.1)">
<div>#</div><div>股票</div><div style="text-align:right">涨跌</div><div style="text-align:right">评分</div></div>
${stocks.map((s,i)=>{
const chgColor=s.change_pct>0?'var(--green)':s.change_pct<0?'var(--red)':'var(--text2)';
const scoreColor=s.score>65?'var(--green)':s.score>50?'var(--accent)':'var(--red)';
return`<div style="display:grid;grid-template-columns:30px 1fr 70px 50px;gap:4px;padding:8px 0;border-bottom:1px solid rgba(148,163,184,.04);align-items:center;cursor:pointer" onclick="showExplain('stock_${s.code}')">
<div style="font-size:11px;color:var(--text2);font-weight:700">${i+1}</div>
<div><div style="font-size:13px;font-weight:600">${s.name}</div>
<div style="font-size:10px;color:var(--text2)">${s.code} · PE ${s.pe!=null?s.pe:'暂无'} · ${s.market_cap?s.market_cap+'亿':'-'}${s.roe?' · ROE'+s.roe+'%':''}${s.gross_margin?' · 毛利'+s.gross_margin+'%':''}</div></div>
<div style="text-align:right;font-size:13px;font-weight:700;color:${chgColor}">${s.change_pct!=null?(s.change_pct>0?'+':'')+s.change_pct+'%':'—'}</div>
<div style="text-align:right;font-size:13px;font-weight:800;color:${scoreColor}">${s.score}</div></div>${_stockTagsHTML(s)}`}).join('')}
<div style="text-align:center;margin-top:12px"><button class="action-btn secondary" style="display:inline-block;min-width:auto;padding:10px 24px" onclick="insightTab='stockpick';renderInsight()">🔄 刷新</button></div>
<div style="font-size:11px;color:#475569;margin-top:8px;line-height:1.5">${data.method||''}<br>${data.note||''}</div>`;
stocks.forEach(s=>{
const sc=s.scores||{};
setExplain('stock_'+s.code,s.name+' ('+s.code+')',
'💰 价格：¥'+s.price+' · 涨跌：'+(s.change_pct!=null?s.change_pct+'%':'—')+'\n📊 PE：'+(s.pe||'—')+' · PB：'+(s.pb||'—')+' · 换手率：'+(s.turnover||'—')+'%\n📈 市值：'+(s.market_cap?s.market_cap+'亿':'—')+'\n\n📋 财务指标：\n• ROE：'+(s.roe||'—')+'%\n• 毛利率：'+(s.gross_margin||'—')+'%\n• 净利率：'+(s.net_margin||'—')+'%\n• 负债率：'+(s.debt_ratio||'—')+'%\n• 营收增速：'+(s.revenue_growth||'—')+'%\n• EPS：'+(s.eps||'—')+'\n\n🎯 综合评分：'+s.score+'/100\n\n7维30因子详情：\n• 价值(20%)：'+sc.value+' (PE/PB/股息率/ROE-PB/EPS/低PE高ROE)\n• 成长(15%)：'+sc.growth+' (营收增速/ROE/EPS/60日动量/PEG)\n• 质量(18%)：'+sc.quality+' (ROE/毛利率/净利率/负债率/现金流/市值)\n• 动量(15%)：'+sc.momentum+' (5日/20日/60日/今日)\n• 风险(12%)：'+sc.risk+' (振幅/负债率/现金流/PE极端)\n• 流动性(10%)：'+sc.liquidity+' (换手率/市值/成交额)\n• 舆情(10%)：'+sc.sentiment+' (待接入)\n\n⚠️ 仅供参考，不构成投资建议。',
{type:'stock',code:s.code,name:s.name,score:s.score,pe:s.pe||0,roe:s.roe||0,gross_margin:s.gross_margin||0})
})}catch(e){console.warn('Stock pick failed:',e);
const listEl=document.getElementById('stockPickList');
if(listEl)listEl.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)">📡 选股数据加载中<br><span style="font-size:11px;opacity:0.6">需分析5000+只A股，首次约30秒</span><br><span style="font-size:11px;opacity:0.5">非交易时段数据源可能不稳定</span><br><button onclick="insightTab=\'stockpick\';renderInsight()" style="margin-top:8px;padding:6px 16px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:12px">🔄 重试</button></div>'}}

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
<div class="modal-section"><div class="modal-section-title">📊 历史业绩 <span id="fundHistSrc_${code}" style="font-size:11px;color:var(--text2)"></span></div><div class="modal-history-grid" id="fundHistGrid_${code}"><div class="modal-history-item"><div class="modal-history-label">近1年</div><div class="modal-history-value">${d.history.y1}</div></div><div class="modal-history-item"><div class="modal-history-label">近3年</div><div class="modal-history-value">${d.history.y3}</div></div><div class="modal-history-item"><div class="modal-history-label">近5年</div><div class="modal-history-value">${d.history.y5}</div></div></div></div>
<div class="modal-section"><div class="modal-section-title">📋 基本信息</div><div class="modal-stat-grid"><div class="modal-stat"><div class="modal-stat-label">规模</div><div class="modal-stat-value" id="fundScale_${code}">${d.scale}</div></div><div class="modal-stat"><div class="modal-stat-label">成立</div><div class="modal-stat-value">${d.founded}</div></div><div class="modal-stat"><div class="modal-stat-label">费率</div><div class="modal-stat-value" style="font-size:12px" id="fundFee_${code}">${d.fee}</div></div><div class="modal-stat"><div class="modal-stat-label">跟踪</div><div class="modal-stat-value" style="font-size:12px">${d.tracking}</div></div></div></div>
${nav?`<div class="modal-section"><div class="modal-section-title">📡 实时</div><div class="modal-stat-grid"><div class="modal-stat"><div class="modal-stat-label">净值</div><div class="modal-stat-value" style="color:var(--green)">${nav.nav}</div></div><div class="modal-stat"><div class="modal-stat-label">涨跌</div><div class="modal-stat-value" style="color:${parseFloat(nav.change)>=0?'var(--green)':'var(--red)'}">${nav.change}%</div></div></div></div>`:''}
<div class="modal-section" id="fundNewsSection_${code}"><div class="modal-section-title">📰 相关新闻</div><div style="text-align:center;padding:12px;font-size:12px;color:var(--text2)">加载中...</div></div>
<div class="modal-section"><div class="modal-section-title">🛒 怎么买</div><div class="modal-buy-tip" style="white-space:pre-line;line-height:1.8">${d.buyTip}</div></div>
<div class="modal-section"><div class="modal-tags">${d.tags.map(t=>`<span class="modal-tag">${t}</span>`).join('')}</div></div></div>`;document.body.appendChild(o);
// 异步加载基金新闻 + 动态业绩数据
if(API_AVAILABLE&&code!=='余额宝'){fetchFundNews(code).then(news=>{const ne=document.getElementById('fundNewsSection_'+code);if(ne&&news.length){ne.innerHTML='<div class="modal-section-title">📰 相关新闻</div>'+news.map(n=>`<div class="news-item"><div class="news-icon">📰</div><div class="news-content"><div class="news-title" style="font-size:13px">${n.title}</div><div class="news-meta">${n.source||''}${n.time?' · '+n.time:''}</div></div></div>`).join('')}else if(ne){ne.innerHTML='<div class="modal-section-title">📰 相关新闻</div><div style="font-size:12px;color:var(--text2);padding:8px">暂无相关新闻</div>'}});
fetchFundDynamic(code).then(info=>{if(!info||info.error)return;const grid=document.getElementById('fundHistGrid_'+code);const src=document.getElementById('fundHistSrc_'+code);const scaleEl=document.getElementById('fundScale_'+code);const feeEl=document.getElementById('fundFee_'+code);
const fmt=v=>v!=null?(v>=0?'+':'')+v.toFixed(2)+'%':'\u2014';
if(grid&&info.returns){const r=info.returns;grid.innerHTML='<div class="modal-history-item"><div class="modal-history-label">\u8fd11\u5468</div><div class="modal-history-value">'+fmt(r['1w'])+'</div></div><div class="modal-history-item"><div class="modal-history-label">\u8fd11\u6708</div><div class="modal-history-value">'+fmt(r['1m'])+'</div></div><div class="modal-history-item"><div class="modal-history-label">\u8fd13\u6708</div><div class="modal-history-value">'+fmt(r['3m'])+'</div></div><div class="modal-history-item"><div class="modal-history-label">\u8fd16\u6708</div><div class="modal-history-value">'+fmt(r['6m'])+'</div></div><div class="modal-history-item"><div class="modal-history-label">\u8fd11\u5e74</div><div class="modal-history-value">'+fmt(r['1y'])+'</div></div><div class="modal-history-item"><div class="modal-history-label">\u8fd13\u5e74</div><div class="modal-history-value">'+fmt(r['3y'])+'</div></div>'}
if(src){src.textContent='\ud83d\udce1 \u5b9e\u65f6\u6570\u636e\u00b7'+info.source}
if(scaleEl&&info.nav){scaleEl.textContent='\u51c0\u503c '+info.nav}
if(feeEl&&info.fee){feeEl.textContent=info.fee}})}}

// ---- 图表 ----
function drawAllocChart(al){const c=document.getElementById('allocChart');if(!c)return;if(chartInstance)chartInstance.destroy();chartInstance=new Chart(c,{type:'doughnut',data:{labels:al.map(a=>a.name),datasets:[{data:al.map(a=>a.pct),backgroundColor:al.map(a=>a.color),borderColor:'#1E293B',borderWidth:3}]},options:{responsive:true,maintainAspectRatio:true,cutout:'55%',plugins:{legend:{display:false}}}})}
function drawProjChart(amt,rate){const c=document.getElementById('projChart');if(!c)return;if(projChartInstance)projChartInstance.destroy();const yrs=['现在','1年后','2年后','3年后'];const vals=[amt];for(let i=1;i<=3;i++)vals.push(Math.round(vals[i-1]*(1+rate)));projChartInstance=new Chart(c,{type:'line',data:{labels:yrs,datasets:[{data:vals,borderColor:'#F59E0B',backgroundColor:'rgba(245,158,11,.1)',fill:true,tension:.4,pointBackgroundColor:'#F59E0B',pointRadius:6}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{ticks:{color:'#94A3B8'}},y:{ticks:{color:'#94A3B8',callback:v=>fmtMoney(v)},grid:{color:'rgba(148,163,184,.1)'}}}}})}

// ---- 事件 ----
function startQuiz(){currentPage='quiz';currentQuestion=0;answers=[];renderQuiz()}
function selectAnswer(i,score){const bs=document.querySelectorAll('.option-btn');bs[i].classList.add('selected');answers.push(score);setTimeout(()=>{currentQuestion++;currentQuestion<QUESTIONS.length?renderQuiz():renderAmountInput()},300)}
function onAmtChange(){const inp=document.getElementById('amtIn');const btn=document.getElementById('genBtn');if(!inp||!btn)return;const v=parseInt(inp.value);btn.disabled=!(v>0);selectedAmount=v||0}
function setAmt(v){const inp=document.getElementById('amtIn');if(inp){inp.value=v;selectedAmount=v;onAmtChange();document.getElementById('genBtn').disabled=false}}
function genResult(){if(!selectedAmount)return;$('#app').innerHTML='<div class="loading-screen"><div class="loading-spinner"></div><div style="color:var(--text2)">AI正在计算...</div></div>';fetchNav().finally(()=>{setTimeout(()=>{currentPage='result';renderResult()},1200)})}
function confirmPurchase(){
// v3.0: 只保存风险偏好和建议配置，不写入假持仓数据
if(!currentProfile)return;
localStorage.setItem(_uk('moneybag_risk_profile'), currentProfile.name);
localStorage.setItem(_uk('moneybag_suggested_alloc'), JSON.stringify({
  profile: currentProfile.name,
  amount: selectedAmount||0,
  allocations: (currentAllocs||[]).map(a=>({code:a.code,name:a.fullName||a.name,pct:a.pct})),
  preference: _selectedPreference||'fund',
  date: new Date().toISOString()
}));
// 同步风险偏好到服务器
if(API_AVAILABLE){fetch(API_BASE+'/agent/preferences',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:getProfileId(),risk_profile:currentProfile.name})}).catch(()=>{})}
// 跳转首页
alert('✅ 已保存你的风险偏好: '+currentProfile.name+'\\n\\n去\"持仓\"页添加真实持仓\\n去\"资产\"页录入现金/房产等');
currentPage='landing';renderLanding()}
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
// 先渲染骨架 UI（加载中…），然后异步拉取服务端数据
$('#app').innerHTML=`<div class="result-page fade-up">
<div class="pnl-hero" style="margin-bottom:16px">
<div class="pnl-label">🏦 统一净资产 <span style="font-size:11px;color:var(--text2)">(投资+资产-负债)</span></div>
<div class="pnl-total-value" id="assetPageNW">加载中…</div>
<div id="assetPageHealth" style="font-size:12px;color:var(--text2);margin-top:4px"></div>
<div id="assetPageRing" style="display:flex;justify-content:center;margin-top:12px"></div>
<div id="assetPageBuckets" style="display:flex;gap:8px;justify-content:center;margin-top:8px;font-size:11px;flex-wrap:wrap"></div>
</div>

<div id="aiAssetAdvice" style="display:none"></div>
<div id="cashAdviceCard" style="display:none"></div>

<div class="section-title" style="display:flex;justify-content:space-between;align-items:center">📋 我的资产<button style="background:none;border:none;color:var(--accent);font-size:13px;cursor:pointer" onclick="showAddAsset()">+ 添加</button></div>
<div id="assetListContainer"><div style="text-align:center;padding:24px;color:var(--text2);font-size:13px">加载中…</div></div>

<div style="display:flex;gap:10px;margin-top:16px">
<button style="flex:1;padding:14px;border-radius:12px;border:none;background:var(--accent);color:#000;font-size:15px;font-weight:700;cursor:pointer;display:flex;align-items:center;justify-content:center;gap:6px" onclick="toggleLedgerPanel()">📝 记账</button>
<button style="flex:1;padding:14px;border-radius:12px;border:none;background:var(--card);color:var(--text);font-size:15px;font-weight:600;cursor:pointer;border:1px solid var(--border);display:flex;align-items:center;justify-content:center;gap:6px" onclick="showAddAsset()">➕ 添加资产</button>
</div>

<div id="ledgerPanelInAssets" style="display:none;margin-top:16px"></div>

<div style="text-align:center;margin-top:16px;font-size:12px;color:var(--text2);line-height:1.8">
💡 投资持仓在「📊 持仓」页管理<br>
所有数据自动汇总到统一净资产</div></div>`;
// 异步加载全部数据
loadAssetPageFull();
}
// 资产页：异步加载全部数据（服务端资产列表 + 统一净资产 + AI 建议）
async function loadAssetPageFull(){
// 1. 同时加载统一净资产 + 服务端资产列表
const [nwData, serverAssets] = await Promise.all([
  fetchUnifiedNetworth(),
  (async()=>{if(!API_AVAILABLE)return null;try{const r=await fetch(`${API_BASE}/assets?userId=${getProfileId()}`,{signal:AbortSignal.timeout(10000)});if(r.ok){const d=await r.json();return d.assets||[]}return null}catch{return null}})()
]);

// 2. 合并资产列表（服务端优先，localStorage 降级）
let assets = serverAssets || loadAssets();
// 如果服务端有数据，同步到 localStorage；如果没有，把 localStorage 上传到服务端
if(serverAssets && serverAssets.length>0){
  saveAssets(serverAssets);
} else if(!serverAssets && loadAssets().length>0 && API_AVAILABLE){
  // localStorage 有但服务端没有 → 上传同步
  const localAssets = loadAssets();
  localAssets.forEach(a=>{fetch(`${API_BASE}/assets`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:getProfileId(),asset:a})}).catch(()=>{})});
}

// 3. 渲染资产列表
const listEl = document.getElementById('assetListContainer');
if(listEl){
  if(assets.length){
    listEl.innerHTML = assets.map(a=>{
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
</div></div></div>`}).join('');
  } else {
    listEl.innerHTML=`<div style="text-align:center;padding:32px;color:var(--text2)">
<div style="font-size:48px;margin-bottom:12px">🏦</div>
<div>还没有资产记录</div>
<div style="font-size:12px;margin-top:8px">添加现金、房产、车辆、保险等</div></div>`;
  }
}

// 4. 更新统一净资产（后端数据）
if(nwData){
  const el=document.getElementById('assetPageNW');if(el)el.textContent=`¥${fmtMoney(Math.round(nwData.netWorth))}`;
  const hel=document.getElementById('assetPageHealth');
  if(hel)hel.innerHTML=`${nwData.healthGrade||''} · ${nwData.healthScore||0}分${(nwData.healthIssues||[]).length?` · <span style="color:var(--red);font-size:11px">${nwData.healthIssues[0]}</span>`:''}`;
  // SVG 环形图
  const ring=document.getElementById('assetPageRing');
  if(ring&&nwData.allocation){const al=nwData.allocation;const segs=[
    {pct:al.investment||0,color:'#F59E0B',label:'投资'},{pct:al.cash||0,color:'#10B981',label:'现金'},
    {pct:al.property||0,color:'#3B82F6',label:'房产'},{pct:(al.car||0)+(al.insurance||0)+(al.other||0),color:'#6B7280',label:'其他'}
  ].filter(s=>s.pct>0);
  let offset=0;const r=50,cx=60,cy=60,C=2*Math.PI*r;
  let paths='';segs.forEach(s=>{const len=s.pct/100*C;paths+=`<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${s.color}" stroke-width="12" stroke-dasharray="${len} ${C-len}" stroke-dashoffset="${-offset}" transform="rotate(-90 ${cx} ${cy})"/>`;offset+=len});
  ring.innerHTML=`<svg width="120" height="120" viewBox="0 0 120 120">${paths}<text x="${cx}" y="${cy-4}" text-anchor="middle" fill="var(--text)" font-size="11" font-weight="700">¥${nwData.netWorth>10000?(nwData.netWorth/10000).toFixed(1)+'万':Math.round(nwData.netWorth)}</text><text x="${cx}" y="${cy+10}" text-anchor="middle" fill="var(--text2)" font-size="9">净资产</text></svg>`}
  // 分桶标签
  const bk=document.getElementById('assetPageBuckets');
  if(bk&&nwData.breakdown){const b=nwData.breakdown;const items=[
    {icon:'📈',label:'投资',val:(b.investment||{}).total||0,color:'#F59E0B'},
    {icon:'💵',label:'现金',val:(b.cash||{}).total||0,color:'#10B981'},
    {icon:'🏠',label:'房产',val:(b.property||{}).total||0,color:'#3B82F6'},
    {icon:'💳',label:'负债',val:(b.liability||{}).total||0,color:'#EF4444'}
  ].filter(i=>i.val>0);
  bk.innerHTML=items.map(i=>`<span style="background:${i.color}15;color:${i.color};padding:2px 8px;border-radius:6px;border:1px solid ${i.color}30">${i.icon} ${i.label} ¥${fmtMoney(Math.round(i.val))}</span>`).join('')}
} else {
  // 后端不可用 → 本地计算
  const assetTotal=assets.filter(a=>a.type!=='liability').reduce((s,a)=>s+(a.value||0),0);
  const liabTotal=assets.filter(a=>a.type==='liability').reduce((s,a)=>s+(a.value||0),0);
  const el=document.getElementById('assetPageNW');if(el)el.textContent=`¥${fmtMoney(Math.round(assetTotal-liabTotal))}`;
  const hel=document.getElementById('assetPageHealth');if(hel)hel.textContent='⚠️ 离线模式（不含投资持仓）';
}

// 5. AI 存款建议（有现金时自动触发）
const cashAmt=assets.filter(a=>a.type==='cash').reduce((s,a)=>s+(a.value||a.balance||0),0);
if(API_AVAILABLE && cashAmt>0){
  const advEl=document.getElementById('cashAdviceCard');if(advEl){advEl.style.display='block';
  advEl.innerHTML='<div class="dashboard-card" style="border-left:3px solid var(--accent);padding:12px"><div style="font-size:13px;font-weight:700;margin-bottom:8px">💡 AI 存款建议</div><div style="font-size:12px;color:var(--text2)">DeepSeek 分析中...</div></div>';
  const ledger=loadLedger();const ledgerExpense=ledger.filter(e=>e.direction!=='income').reduce((s,e)=>s+(e.amount||0),0);
  fetch(`${API_BASE}/assets/advice`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cashAmount:cashAmt,monthlyExpense:ledgerExpense/Math.max(1,Math.ceil(ledger.length/30))*30,riskProfile:localStorage.getItem('moneybag_profile')||'稳健型'})}).then(r=>r.json()).then(d=>{
    advEl.innerHTML=`<div class="dashboard-card" style="border-left:3px solid var(--accent);padding:12px"><div style="font-size:13px;font-weight:700;margin-bottom:8px">💡 AI 存款建议 <span style="font-size:10px;color:var(--text2);font-weight:400">${d.source==='ai'?'🤖 DeepSeek':'📐 规则'}</span></div><div style="font-size:12px;line-height:1.6">${d.advice}</div><div style="font-size:11px;color:var(--text2);margin-top:8px">应急储备 ¥${fmtMoney(d.emergencyFund||0)} | 闲置 ¥${fmtMoney(d.idleCash||0)} | 年损失 ¥${fmtMoney(d.annualLoss||0)}</div></div>`;
  }).catch(()=>{advEl.style.display='none'})}}

// 6. AI 资产诊断（有资产时触发）
if(API_AVAILABLE && assets.length>=2){
  const aiEl=document.getElementById('aiAssetAdvice');if(aiEl){aiEl.style.display='block';
  aiEl.innerHTML='<div class="dashboard-card" style="border-left:3px solid #8B5CF6;padding:12px;margin-bottom:12px"><div style="font-size:13px;font-weight:700;margin-bottom:8px">🤖 AI 资产诊断</div><div style="font-size:12px;color:var(--text2)">DeepSeek 全量分析中...</div></div>';
  fetch(`${API_BASE}/ds/asset-diagnosis`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:getProfileId()}),signal:AbortSignal.timeout(30000)}).then(r=>r.ok?r.json():null).then(d=>{
    if(d&&d.advice){aiEl.innerHTML=`<div class="dashboard-card" style="border-left:3px solid #8B5CF6;padding:12px;margin-bottom:12px"><div style="font-size:13px;font-weight:700;margin-bottom:8px">🤖 AI 资产诊断 <span style="font-size:10px;color:var(--text2);font-weight:400">🤖 DeepSeek</span></div><div style="font-size:12px;line-height:1.8">${d.advice}</div></div>`}else{aiEl.style.display='none'}
  }).catch(()=>{aiEl.style.display='none'})}}
}

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
document.querySelector('.modal-overlay')?.remove();renderAssets();
// 资产变更后异步刷新首页净资产（后端缓存已在API层失效）
_refreshNetWorthAfterAssetChange()}

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
document.querySelector('.modal-overlay')?.remove();renderAssets();
_refreshNetWorthAfterAssetChange()}

function deleteAsset(id){const assets=loadAssets().filter(a=>a.id!==id);saveAssets(assets);
if(API_AVAILABLE)fetch(API_BASE+'/assets/'+id+'?userId='+getUserId(),{method:'DELETE'}).catch(()=>{});
renderAssets();
_refreshNetWorthAfterAssetChange()}

// ---- 📈 持仓盯盘页（股票+基金统一） ----
let _stockScanData=null;let _fundScanData=null;let _holdingsSubTab='stock';let _overviewData=null;
async function renderStocks(){currentPage='stocks';renderNav();
$('#app').innerHTML=`<div class="insight-page fade-up"><div id="overviewHero"><div style="text-align:center;padding:20px"><div class="loading-spinner"></div></div></div><div style="display:flex;gap:8px;margin-bottom:16px"><button id="subTabStock" class="action-btn ${_holdingsSubTab==='stock'?'primary':'secondary'}" onclick="_holdingsSubTab='stock';renderStocksContent()" style="flex:1">📊 股票</button><button id="subTabFund" class="action-btn ${_holdingsSubTab==='fund'?'primary':'secondary'}" onclick="_holdingsSubTab='fund';renderFundsContent()" style="flex:1">💰 基金</button></div><div id="holdingsContent"><div style="text-align:center;padding:40px"><div class="loading-spinner"></div><div style="color:var(--text2);margin-top:12px">加载持仓数据...</div></div></div></div>`;
if(!API_AVAILABLE){document.getElementById('holdingsContent').innerHTML='<div style="text-align:center;padding:40px;color:var(--text2)">后端离线</div>';return}
// 加载总览 + 子页面并行
loadOverviewHero();
if(_holdingsSubTab==='fund')renderFundsContent();else renderStocksContent()}

async function loadOverviewHero(){
try{const ov=await fetch(API_BASE+'/portfolio/overview?'+getProfileParam()).then(r=>r.json());_overviewData=ov;
const el=document.getElementById('overviewHero');if(!el)return;
const pnlC=ov.totalPnl>=0?'var(--green)':'var(--red)';
const hC=ov.healthScore>=80?'var(--green)':ov.healthScore>=60?'#F59E0B':'var(--red)';
// 环形图 SVG（股/债/现 三段）
const eq=ov.allocation?.equity||0;const bd=ov.allocation?.bond||0;const ca=ov.allocation?.cash||0;
const r=36;const c=2*Math.PI*r;
const eqLen=c*eq/100;const bdLen=c*bd/100;const caLen=c*(ca||100-eq-bd)/100;
const eqOff=0;const bdOff=-(eqLen);const caOff=-(eqLen+bdLen);
const ringSvg=ov.totalMarketValue>0?`<svg width="90" height="90" viewBox="0 0 90 90" style="transform:rotate(-90deg)">
<circle cx="45" cy="45" r="${r}" fill="none" stroke="var(--bg3)" stroke-width="10"/>
<circle cx="45" cy="45" r="${r}" fill="none" stroke="var(--accent)" stroke-width="10" stroke-dasharray="${eqLen} ${c-eqLen}" stroke-dashoffset="${eqOff}"/>
<circle cx="45" cy="45" r="${r}" fill="none" stroke="#60A5FA" stroke-width="10" stroke-dasharray="${bdLen} ${c-bdLen}" stroke-dashoffset="${bdOff}"/>
<circle cx="45" cy="45" r="${r}" fill="none" stroke="#A78BFA" stroke-width="10" stroke-dasharray="${caLen} ${c-caLen}" stroke-dashoffset="${caOff}"/>
</svg>`:'';
const legendHtml=ov.totalMarketValue>0?`<div style="display:flex;gap:12px;justify-content:center;margin-top:8px;font-size:11px;color:var(--text2)">
<span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--accent);margin-right:3px"></span>股票 ${eq}%</span>
<span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#60A5FA;margin-right:3px"></span>债券 ${bd}%</span>
<span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#A78BFA;margin-right:3px"></span>现金 ${ca}%</span>
</div>`:'';
const devHtml=ov.totalMarketValue>0&&ov.deviation?Object.entries({equity:'股票',bond:'债券',cash:'现金'}).map(([k,label])=>{
const d=ov.deviation[k]||0;const dc=Math.abs(d)>15?'var(--red)':Math.abs(d)>5?'#F59E0B':'var(--green)';
return`<span style="font-size:11px;color:${dc}">${label}${d>0?'+':''}${d}%</span>`}).join(' · '):'';
el.innerHTML=`<div class="pnl-hero" style="position:relative">
<div style="display:flex;align-items:center;gap:16px;justify-content:center">
<div>${ringSvg}</div>
<div><div class="pnl-label">总持仓资产 <span style="font-size:10px;color:var(--text2);font-weight:400">仅股票+基金</span></div>
<div class="pnl-total-value">¥${ov.totalMarketValue>0?ov.totalMarketValue.toLocaleString():'0'}</div>
${ov.totalCost>0?`<div class="pnl-change ${ov.totalPnl>=0?'pos':'neg'}" style="color:${pnlC}">盈亏 ${ov.totalPnl>=0?'+':''}¥${ov.totalPnl.toFixed(0)} (${ov.totalPnlPct>=0?'+':''}${ov.totalPnlPct.toFixed(1)}%)</div>`:''}</div></div>
${legendHtml}
${devHtml?`<div style="text-align:center;margin-top:4px">偏离: ${devHtml}</div>`:''}
<div style="display:flex;justify-content:center;gap:16px;margin-top:10px;font-size:12px">
<span>📊 股票 ${ov.stockCount}只</span><span>💰 基金 ${ov.fundCount}只</span>
<span style="color:${hC};font-weight:600">${ov.healthGrade} ${ov.healthScore}分</span>
</div>
${ov.healthIssues&&ov.healthIssues.length?`<div style="margin-top:8px;padding:8px 12px;background:rgba(245,158,11,.08);border-radius:8px;font-size:11px;color:#F59E0B">${ov.healthIssues.join(' · ')}</div>`:''}
</div>`;
}catch(e){console.warn('Overview load error:',e)}}

async function renderStocksContent(){
_holdingsSubTab='stock';
document.getElementById('subTabStock')?.classList.replace('secondary','primary');
document.getElementById('subTabFund')?.classList.replace('primary','secondary');
const el=document.getElementById('holdingsContent');
el.innerHTML='<div style="text-align:center;padding:40px"><div class="loading-spinner"></div><div style="color:var(--text2);margin-top:12px">加载股票持仓...</div></div>';
try{const[hRes,scanRes]=await Promise.all([fetch(API_BASE+'/stock-holdings?'+getProfileParam()).then(r=>r.json()),fetch(API_BASE+'/stock-holdings/scan?'+getProfileParam()).then(r=>r.json())]);
_stockScanData=scanRes;const holdings=scanRes.holdings||[];const signals=scanRes.signals||[];const discipline=scanRes.discipline||[];
const el=document.getElementById('holdingsContent');if(!el)return;
// 异动信号汇总
let signalHtml='';if(signals.length>0){const dangerS=signals.filter(s=>s.level==='danger'||s.level==='warning');const opS=signals.filter(s=>s.level==='opportunity');
signalHtml=`<div class="dashboard-card" style="border-left:3px solid ${dangerS.length?'var(--red)':'var(--green)'}"><div class="dashboard-card-title">⚡ 盯盘信号 (${signals.length})</div>${signals.map(s=>{const c=s.level==='danger'?'var(--red)':s.level==='warning'?'#F59E0B':s.level==='opportunity'?'var(--green)':'var(--text2)';return`<div style="padding:6px 0;font-size:13px;border-bottom:1px solid var(--bg3);color:${c}">${s.msg}</div>`}).join('')}</div>`}
// 纪律检查面板
let disciplineHtml='';if(discipline.length>0){
disciplineHtml=`<div class="dashboard-card" style="border-left:3px solid #F59E0B;margin-top:8px"><div class="dashboard-card-title">📏 纪律检查 (${discipline.length})</div>${discipline.map(d=>{const c=d.level==='warning'?'#F59E0B':d.level==='danger'?'var(--red)':'var(--text2)';return`<div style="padding:6px 0;font-size:13px;border-bottom:1px solid var(--bg3);color:${c}">${d.msg}</div>`}).join('')}</div>`}
// 持仓列表
let listHtml='';if(holdings.length===0){listHtml=`<div style="text-align:center;padding:40px;color:var(--text2)"><div style="font-size:48px;margin-bottom:16px">📈</div><div style="font-size:16px;margin-bottom:8px">还没有持仓股票</div><div style="font-size:13px">点击下方按钮添加你的第一只股票</div></div>`}else{
listHtml=holdings.map(h=>{const pctC=h.changePct>=0?'var(--green)':'var(--red)';const pnlC=(h.pnlPct||0)>=0?'var(--green)':'var(--red)';const weightTag=h.weight?` · 仓位${h.weight}%`:'';const industryTag=h.industry&&h.industry!=='未知'?` · ${h.industry}`:'';
return`<div class="holding-card" onclick="showStockDetail('${h.code}')"><div class="holding-top"><div class="holding-info"><div class="holding-name">${h.name||h.code}</div><div class="holding-meta">${h.code}${industryTag}${weightTag}</div></div><div class="holding-amount"><div class="holding-money" style="color:${pctC}">${h.price?'¥'+h.price.toFixed(2):'--'}</div><div class="holding-pct" style="color:${pctC}">${h.changePct!=null?(h.changePct>=0?'+':'')+h.changePct.toFixed(2)+'%':'--'}</div></div></div>${h.costPrice&&h.shares?`<div class="holding-pnl-row"><div class="holding-pnl-item"><div class="holding-pnl-label">持仓市值</div><div class="holding-pnl-val">¥${(h.marketValue||0).toLocaleString()}</div></div><div class="holding-pnl-item"><div class="holding-pnl-label">盈亏</div><div class="holding-pnl-val ${(h.pnlPct||0)>=0?'pos':'neg'}" style="color:${pnlC}">${h.pnl!=null?((h.pnl>=0?'+':'')+h.pnl.toFixed(0)):''} ${h.pnlPct!=null?'('+((h.pnlPct>=0?'+':'')+h.pnlPct.toFixed(1))+'%)':''}</div></div><div class="holding-pnl-item"><div class="holding-pnl-label">成本价</div><div class="holding-pnl-val">¥${h.costPrice}</div></div></div>`:''}</div>`}).join('')}
// 汇总
let totalMV=holdings.reduce((s,h)=>s+(h.marketValue||0),0);let totalPnl=holdings.reduce((s,h)=>s+(h.pnl||0),0);
let heroHtml='';if(holdings.length>0&&totalMV>0){const pnlC=totalPnl>=0?'var(--green)':'var(--red)';
heroHtml=`<div class="pnl-hero"><div class="pnl-label">股票持仓总市值</div><div class="pnl-total-value">¥${totalMV.toLocaleString()}</div><div class="pnl-change ${totalPnl>=0?'pos':'neg'}" style="color:${pnlC}">${totalPnl>=0?'+':''}${totalPnl.toFixed(0)}</div><div class="pnl-sub">${holdings.length} 只股票 · ${scanRes.scannedAt?'更新于 '+scanRes.scannedAt.slice(11,16):''}</div></div>`}
el.innerHTML=heroHtml+signalHtml+disciplineHtml+listHtml+`<div style="margin-top:16px"><button class="action-btn primary" onclick="showAddStockModal()" style="width:100%">➕ 添加股票</button></div>${isProMode()?'<div style="margin-top:8px"><button class="action-btn secondary" onclick="runStockAnalysis()" style="width:100%;border-color:rgba(99,102,241,.4);color:#818CF8" id="stockAnalyzeBtn">🤖 AI 深度分析（全持仓）</button></div>':''}<div style="margin-top:8px"><button class="action-btn secondary" onclick="renderStocksContent()" style="width:100%">🔄 刷新行情</button></div><div id="stockAnalysisResult"></div>`;
}catch(e){console.error('Stock load error:',e);document.getElementById('holdingsContent').innerHTML='<div style="text-align:center;padding:40px;color:var(--red)">加载失败: '+e.message+'</div>'}}

function showAddStockModal(){const overlay=document.createElement('div');overlay.className='modal-overlay';overlay.onclick=e=>{if(e.target===overlay)overlay.remove()};
overlay.innerHTML=`<div class="modal-sheet"><div class="modal-handle"></div><div class="modal-title">➕ 添加股票</div><div class="modal-subtitle">输入A股代码（如 600519、002594）</div><div class="form-row"><div class="form-label">股票代码 *</div><input class="form-input" id="addStockCode" placeholder="600519" inputmode="numeric"></div><div class="form-row"><div class="form-label">成本价（选填）</div><input class="form-input" id="addStockCost" type="number" placeholder="0" step="0.01" inputmode="decimal"></div><div class="form-row"><div class="form-label">持有股数（选填）</div><input class="form-input" id="addStockShares" type="number" placeholder="0" inputmode="numeric"></div><div class="form-row"><div class="form-label">备注（选填）</div><input class="form-input" id="addStockNote" placeholder=""></div><button class="form-submit" onclick="doAddStock()">添加</button></div>`;
document.body.appendChild(overlay)}

async function doAddStock(){const code=$('#addStockCode')?.value?.trim();if(!code){alert('请输入股票代码');return}

// Phase 0: AI 深度分析（股票持仓 7-Skill 框架）
async function runStockAnalysis(){
const btn=document.getElementById('stockAnalyzeBtn');const res=document.getElementById('stockAnalysisResult');
if(btn){btn.innerHTML='🤖 分析中...（预计 30-60 秒）';btn.disabled=true}
if(res)res.innerHTML=renderCard('🤖 AI 股票持仓分析','loading');
try{
  const r=await fetch(`${API_BASE}/stock-holdings/analyze`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:getProfileId()}),signal:AbortSignal.timeout(60000)});
  const d=await r.json();
  if(res)res.innerHTML=`<div class="dashboard-card" style="margin-top:12px;border-left:3px solid #6366F1">
    <div class="dashboard-card-title">🤖 AI 持仓深度分析</div>
    <div style="font-size:13px;line-height:1.8;color:var(--text1);white-space:pre-wrap">${d.analysis||'暂无分析'}</div>
    <div style="font-size:10px;color:var(--text3);margin-top:8px">来源: ${d.source||'ai'}</div>
  </div>`;
}catch(e){if(res)res.innerHTML=renderCard('🤖 AI 股票持仓分析','error')}
finally{if(btn){btn.innerHTML='🤖 AI 深度分析（全持仓）';btn.disabled=false}}}

// Phase 0: AI 深度分析（基金持仓）
async function runFundAnalysis(){
const btn=document.getElementById('fundAnalyzeBtn');const res=document.getElementById('fundAnalysisResult');
if(btn){btn.innerHTML='🤖 分析中...（预计 30-60 秒）';btn.disabled=true}
if(res)res.innerHTML=renderCard('🤖 AI 基金持仓分析','loading');
try{
  const r=await fetch(`${API_BASE}/fund-holdings/analyze`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:getProfileId()}),signal:AbortSignal.timeout(60000)});
  const d=await r.json();
  if(res)res.innerHTML=`<div class="dashboard-card" style="margin-top:12px;border-left:3px solid #10B981">
    <div class="dashboard-card-title">🤖 AI 基金持仓分析</div>
    <div style="font-size:13px;line-height:1.8;color:var(--text1);white-space:pre-wrap">${d.analysis||'暂无分析'}</div>
    <div style="font-size:10px;color:var(--text3);margin-top:8px">来源: ${d.source||'ai'}</div>
  </div>`;
}catch(e){if(res)res.innerHTML=renderCard('🤖 AI 基金持仓分析','error')}
finally{if(btn){btn.innerHTML='🤖 AI 深度分析（全持仓）';btn.disabled=false}}}
const cost=parseFloat($('#addStockCost')?.value)||0;const shares=parseInt($('#addStockShares')?.value)||0;const note=$('#addStockNote')?.value||'';
try{const r=await fetch(API_BASE+'/stock-holdings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code,costPrice:cost,shares,note,userId:getProfileId()})});
const d=await r.json();if(d.error){alert(d.error);return}
// 显示纪律检查警告
if(d.warnings&&d.warnings.length>0){const warnMsg=d.warnings.map(w=>w.msg).join('\n');setTimeout(()=>alert('⚠️ 纪律提醒\n\n'+warnMsg),200)}
document.querySelector('.modal-overlay')?.remove();renderStocksContent()}catch(e){alert('添加失败: '+e.message)}}

function showStockDetail(code){const h=(_stockScanData?.holdings||[]).find(x=>x.code===code);if(!h)return;
const overlay=document.createElement('div');overlay.className='modal-overlay';overlay.onclick=e=>{if(e.target===overlay)overlay.remove()};
const ind=h.indicators||{};const sigs=h.signals||[];
overlay.innerHTML=`<div class="modal-sheet"><div class="modal-handle"></div><div class="modal-title">${h.name||h.code}</div><div class="modal-subtitle">${h.code} · ${h.changePct!=null?(h.changePct>=0?'+':'')+h.changePct.toFixed(2)+'%':'--'}</div><div class="modal-stat-grid"><div class="modal-stat"><div class="modal-stat-label">当前价</div><div class="modal-stat-value">${h.price?'¥'+h.price.toFixed(2):'--'}</div></div><div class="modal-stat"><div class="modal-stat-label">RSI14</div><div class="modal-stat-value" style="color:${ind.rsi14>70?'var(--red)':ind.rsi14<30?'var(--green)':'var(--text)'}">${ind.rsi14||'--'}</div></div><div class="modal-stat"><div class="modal-stat-label">MACD</div><div class="modal-stat-value">${ind.macd_trend||'--'}</div></div><div class="modal-stat"><div class="modal-stat-label">量比</div><div class="modal-stat-value" style="color:${ind.volume_ratio>2?'var(--red)':'var(--text)'}">${ind.volume_ratio||'--'}</div></div></div>${sigs.length?'<div style="margin-top:16px"><div style="font-size:13px;font-weight:700;margin-bottom:8px">📡 信号</div>'+sigs.map(s=>`<div style="padding:6px 0;font-size:13px;border-bottom:1px solid var(--bg3)">${s.msg}</div>`).join('')+'</div>':''}<div id="stockIntel_${code}" style="margin-top:16px"><div style="text-align:center;padding:12px;color:var(--text2);font-size:12px">📰 加载个股情报...</div></div><div style="margin-top:16px;display:flex;gap:8px"><button class="action-btn secondary" style="flex:1" onclick="if(confirm('删除 ${h.name}？'))deleteStock('${h.code}')">🗑️ 删除</button></div></div>`;
document.body.appendChild(overlay);
// 异步加载持仓关联智能
if(API_AVAILABLE){fetch(API_BASE+'/holding-intelligence/'+code+'?'+getProfileParam(),{signal:AbortSignal.timeout(15000)}).then(r=>r.json()).then(d=>{
const el=document.getElementById('stockIntel_'+code);if(!el)return;
let h='';
if(d.news&&d.news.length){h+=`<div style="font-size:13px;font-weight:700;margin-bottom:6px">📰 个股新闻</div>`;h+=d.news.slice(0,3).map(n=>`<div style="padding:4px 0;font-size:12px;border-bottom:1px solid var(--bg3)">${n.title}</div>`).join('')}
if(d.fund_flow){const ff=d.fund_flow;h+=`<div style="font-size:13px;font-weight:700;margin-top:10px;margin-bottom:4px">💰 主力资金</div><div style="font-size:12px;color:${ff.net_amount>0?'var(--green)':'var(--red)'}">今日主力净${ff.net_amount>0?'流入':'流出'} ${Math.abs(ff.net_amount||0).toFixed(0)}万</div>`}
if(d.industry){h+=`<div style="font-size:12px;color:var(--text2);margin-top:8px">🏭 所属行业：${d.industry}</div>`}
if(d.unlock_risk){h+=`<div style="font-size:12px;color:var(--red);margin-top:6px;padding:6px;background:rgba(239,68,68,.06);border-radius:6px">🔓 解禁预警：${d.unlock_risk}</div>`}
el.innerHTML=h||'<div style="font-size:12px;color:var(--text2)">暂无关联情报</div>'
}).catch(()=>{const el=document.getElementById('stockIntel_'+code);if(el)el.innerHTML=''})}}

async function deleteStock(code){try{await fetch(API_BASE+'/stock-holdings/'+code+'?'+getProfileParam(),{method:'DELETE'});document.querySelector('.modal-overlay')?.remove();renderStocksContent()}catch(e){alert('删除失败')}}

// ---- 💰 基金持仓板块 ----
async function renderFundsContent(){
_holdingsSubTab='fund';
document.getElementById('subTabFund')?.classList.replace('secondary','primary');
document.getElementById('subTabStock')?.classList.replace('primary','secondary');
const el=document.getElementById('holdingsContent');
el.innerHTML='<div style="text-align:center;padding:40px"><div class="loading-spinner"></div><div style="color:var(--text2);margin-top:12px">加载基金持仓...</div></div>';
try{const[hRes,scanRes]=await Promise.all([fetch(API_BASE+'/fund-holdings?'+getProfileParam()).then(r=>r.json()),fetch(API_BASE+'/fund-holdings/scan?'+getProfileParam()).then(r=>r.json())]);
_fundScanData=scanRes;const holdings=scanRes.holdings||[];
// 信号汇总
let signalHtml='';const alerts=scanRes.alerts||[];
if(alerts.length){signalHtml='<div class="signal-summary"><div style="font-size:13px;font-weight:700;margin-bottom:8px">⚡ 基金异动信号</div>'+alerts.map(a=>{const bg=a.level==='warning'?'rgba(239,68,68,.08)':'rgba(34,197,94,.08)';return`<div style="background:${bg};border-radius:8px;padding:8px 10px;margin-bottom:4px;font-size:12px">${a.fund||''} ${a.msg}</div>`}).join('')+'</div>'}
// 列表
let listHtml='';
if(!holdings.length){listHtml='<div style="text-align:center;padding:40px;color:var(--text2)">暂无基金持仓<br><span style="font-size:12px">点击下方"添加基金"开始</span></div>'}
else{listHtml=holdings.map(h=>{const rt=h.realtime||{};const risk=h.risk||{};
const estRate=rt.estRate;const rateColor=estRate==null?'var(--text2)':estRate>=0?'var(--green)':'var(--red)';
const pnlColor=h.pnlPct==null?'var(--text2)':h.pnlPct>=0?'var(--green)':'var(--red)';
const ddStr=risk.maxDrawdown!=null?(risk.maxDrawdown*100).toFixed(1)+'%':'--';
const ddColor=risk.maxDrawdown!=null&&risk.maxDrawdown>0.03?'var(--red)':'var(--text2)';
return`<div class="stock-card" onclick="showFundHoldingDetail('${h.code}')" style="cursor:pointer">
<div style="display:flex;justify-content:space-between;align-items:center">
<div><div style="font-size:14px;font-weight:700">${h.name||h.code}</div><div style="font-size:11px;color:var(--text2)">${h.code}</div></div>
<div style="text-align:right"><div style="font-size:14px;font-weight:600">${rt.estNav||'--'}</div>
<div style="font-size:12px;color:${rateColor}">${estRate!=null?(estRate>=0?'+':'')+estRate.toFixed(2)+'%':'--'}</div></div></div>
<div style="display:flex;gap:12px;margin-top:8px;font-size:11px;color:var(--text2)">
<span>净值 ${rt.nav||'--'}</span><span style="color:${ddColor}">回撤 ${ddStr}</span>
<span>连跌 ${risk.downDays||0}天</span>
${h.pnlPct!=null?`<span style="color:${pnlColor};font-weight:600">盈亏 ${h.pnlPct>=0?'+':''}${h.pnlPct.toFixed(1)}%</span>`:''}
</div>${h.alerts&&h.alerts.length?'<div style="margin-top:6px;font-size:11px;color:var(--accent)">'+h.alerts.map(a=>a.msg).join(' · ')+'</div>':''}</div>`}).join('')}
// Hero
let heroHtml='';const totalPnl=holdings.reduce((s,h)=>s+(h.pnl||0),0);
if(holdings.length>0){heroHtml=`<div class="pnl-hero"><div class="pnl-label">基金持仓 ${holdings.length} 只</div><div class="pnl-change ${totalPnl>=0?'pos':'neg'}" style="color:${totalPnl>=0?'var(--green)':'var(--red)'}">总盈亏 ${totalPnl>=0?'+':''}¥${totalPnl.toFixed(0)}</div><div class="pnl-sub">${scanRes.scannedAt?'更新于 '+scanRes.scannedAt.slice(11,16):''}</div></div>`}
el.innerHTML=heroHtml+signalHtml+listHtml+`<div style="margin-top:16px"><button class="action-btn primary" onclick="showAddFundModal()" style="width:100%">➕ 添加基金</button></div>${isProMode()?'<div style="margin-top:8px"><button class="action-btn secondary" onclick="runFundAnalysis()" style="width:100%;border-color:rgba(16,185,129,.4);color:#10B981" id="fundAnalyzeBtn">🤖 AI 深度分析（全持仓）</button></div>':''}<div style="margin-top:8px"><button class="action-btn secondary" onclick="renderFundsContent()" style="width:100%">🔄 刷新估值</button></div><div id="fundAnalysisResult"></div>`;
}catch(e){console.error('Fund load error:',e);el.innerHTML='<div style="text-align:center;padding:40px;color:var(--red)">加载失败: '+e.message+'</div>'}}

function showAddFundModal(){const overlay=document.createElement('div');overlay.className='modal-overlay';overlay.onclick=e=>{if(e.target===overlay)overlay.remove()};
overlay.innerHTML=`<div class="modal-sheet"><div class="modal-handle"></div><div class="modal-title">添加基金持仓</div><div class="input-group"><label>基金代码</label><input id="addFundCode" placeholder="如 110011" class="input-field"></div><div class="input-group"><label>成本净值（选填）</label><input id="addFundCost" type="number" step="0.0001" placeholder="买入时的净值" class="input-field"></div><div class="input-group"><label>持有份额（选填）</label><input id="addFundShares" type="number" step="0.01" placeholder="持有份额" class="input-field"></div><div class="input-group"><label>备注（选填）</label><input id="addFundNote" placeholder="如：定投" class="input-field"></div><button class="action-btn primary" onclick="doAddFund()" style="width:100%;margin-top:16px">确认添加</button></div>`;
document.body.appendChild(overlay)}

async function doAddFund(){const code=$('#addFundCode')?.value?.trim();if(!code){alert('请输入基金代码');return}
const cost=parseFloat($('#addFundCost')?.value)||0;const shares=parseFloat($('#addFundShares')?.value)||0;const note=$('#addFundNote')?.value||'';
try{const r=await fetch(API_BASE+'/fund-holdings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code,costNav:cost,shares,note,userId:getProfileId()})});
const d=await r.json();if(d.error){alert(d.error);return}document.querySelector('.modal-overlay')?.remove();renderFundsContent()}catch(e){alert('添加失败: '+e.message)}}

function showFundHoldingDetail(code){const h=(_fundScanData?.holdings||[]).find(x=>x.code===code);if(!h)return;
const rt=h.realtime||{};const risk=h.risk||{};const alerts=h.alerts||[];
const overlay=document.createElement('div');overlay.className='modal-overlay';overlay.onclick=e=>{if(e.target===overlay)overlay.remove()};
overlay.innerHTML=`<div class="modal-sheet"><div class="modal-handle"></div><div class="modal-title">${h.name||h.code}</div><div class="modal-subtitle">${h.code} · 估算 ${rt.estRate!=null?(rt.estRate>=0?'+':'')+rt.estRate.toFixed(2)+'%':'--'}</div><div class="modal-stat-grid"><div class="modal-stat"><div class="modal-stat-label">估算净值</div><div class="modal-stat-value">${rt.estNav||'--'}</div></div><div class="modal-stat"><div class="modal-stat-label">最新净值</div><div class="modal-stat-value">${rt.nav||'--'}</div></div><div class="modal-stat"><div class="modal-stat-label">估算偏差</div><div class="modal-stat-value">${rt.estDeviation!=null?rt.estDeviation.toFixed(2)+'%':'--'}</div></div><div class="modal-stat"><div class="modal-stat-label">最大回撤</div><div class="modal-stat-value" style="color:${risk.maxDrawdown>0.03?'var(--red)':'var(--text)'}">${risk.maxDrawdown!=null?(risk.maxDrawdown*100).toFixed(1)+'%':'--'}</div></div><div class="modal-stat"><div class="modal-stat-label">年化波动</div><div class="modal-stat-value">${risk.volatility!=null?(risk.volatility*100).toFixed(1)+'%':'--'}</div></div><div class="modal-stat"><div class="modal-stat-label">连跌天数</div><div class="modal-stat-value" style="color:${risk.downDays>=3?'var(--red)':'var(--text)'}">${risk.downDays||0}天</div></div></div>${alerts.length?'<div style="margin-top:16px"><div style="font-size:13px;font-weight:700;margin-bottom:8px">⚡ 信号</div>'+alerts.map(a=>`<div style="background:rgba(239,68,68,.06);border-radius:8px;padding:8px;margin-bottom:4px;font-size:12px">${a.msg}</div>`).join('')+'</div>':''}<button class="action-btn" onclick="deleteFund('${h.code}')" style="width:100%;margin-top:16px;color:var(--red);border-color:var(--red)">🗑️ 删除此基金</button></div>`;
document.body.appendChild(overlay)}

async function deleteFund(code){try{await fetch(API_BASE+'/fund-holdings/'+code+'?'+getProfileParam(),{method:'DELETE'});document.querySelector('.modal-overlay')?.remove();renderFundsContent()}catch(e){alert('删除失败')}}

// ---- Phase 5: 因子 IC 检验 ----
async function renderFactorIC(el){
el.innerHTML=`<div class="dashboard-card" style="overflow:hidden">
<div class="dashboard-card-title">🔬 因子 IC 检验</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:8px">验证30因子中哪些真正具有收益预测能力（Spearman IC）</div>
<div style="font-size:11px;color:var(--accent);margin-bottom:12px;padding:6px 8px;background:rgba(245,158,11,.06);border-radius:6px">📊 |IC| > 0.05 = 优秀因子 · |IC| > 0.03 = 有效因子 · 参考 Barra 多因子模型标准</div>
<div id="factorICContent"><div style="text-align:center;padding:30px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>正在计算因子IC，需获取200只股票数据...<br><span style="font-size:11px;opacity:0.6">首次约30-60秒</span></div></div></div>`;
try{
const r=await fetch(API_BASE+'/factor-ic?forward_days=20&pool_size=200',{signal:AbortSignal.timeout(120000)});
if(!r.ok)throw new Error('fetch failed');
const d=await r.json();
if(d.error){document.getElementById('factorICContent').innerHTML=`<div style="text-align:center;padding:20px;color:var(--red)">${d.error}</div>`;return}
const ranking=d.ranking||[];
const summary=d.summary||{};
const recs=d.recommendations||[];
const ineffective=d.ineffective_factors||[];
let html=`<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:16px">
<div style="background:rgba(16,185,129,.08);border-radius:12px;padding:12px;text-align:center">
<div style="font-size:24px;font-weight:900;color:var(--green)">${summary.effective_factors||0}</div>
<div style="font-size:11px;color:var(--text2)">有效因子</div></div>
<div style="background:rgba(239,68,68,.08);border-radius:12px;padding:12px;text-align:center">
<div style="font-size:24px;font-weight:900;color:var(--red)">${(summary.total_factors||0)-(summary.effective_factors||0)}</div>
<div style="font-size:11px;color:var(--text2)">无效因子</div></div>
<div style="background:rgba(99,102,241,.08);border-radius:12px;padding:12px;text-align:center">
<div style="font-size:24px;font-weight:900;color:#818CF8">${summary.effectiveness_rate||0}%</div>
<div style="font-size:11px;color:var(--text2)">有效率</div></div></div>`;
// 建议
if(recs.length){html+=`<div style="background:rgba(59,130,246,.06);border-radius:10px;padding:10px 12px;margin-bottom:16px;font-size:12px;line-height:1.8;color:var(--text)">
<div style="font-weight:700;margin-bottom:4px">💡 分析建议</div>
${recs.map(r=>'• '+r).join('<br>')}</div>`}
// 因子排名表
html+=`<div style="font-size:13px;font-weight:700;margin-bottom:8px">📊 因子排名（按 |IC| 降序）</div>
<div style="display:grid;grid-template-columns:30px 1fr 60px 60px 60px;gap:4px;font-size:11px;color:var(--text2);font-weight:600;padding:6px 0;border-bottom:1px solid rgba(148,163,184,.1)">
<div>#</div><div>因子</div><div style="text-align:right">IC</div><div style="text-align:right">样本</div><div style="text-align:right">评级</div></div>`;
ranking.forEach((f,i)=>{
const levelColor=f.level==='优秀'?'var(--green)':f.level==='有效'?'#3B82F6':f.level==='微弱'?'var(--accent)':'var(--red)';
const icColor=f.ic>0?'var(--green)':'var(--red)';
html+=`<div style="display:grid;grid-template-columns:30px 1fr 60px 60px 60px;gap:4px;padding:8px 0;border-bottom:1px solid rgba(148,163,184,.04);align-items:center">
<div style="font-size:11px;color:var(--text2);font-weight:700">${i+1}</div>
<div><div style="font-size:12px;font-weight:600">${f.name_cn||f.factor}</div>
<div style="font-size:10px;color:var(--text2)">${f.direction||''} · ${f.factor}</div></div>
<div style="text-align:right;font-size:13px;font-weight:700;color:${icColor}">${f.ic>0?'+':''}${f.ic}</div>
<div style="text-align:right;font-size:11px;color:var(--text2)">${f.samples}</div>
<div style="text-align:right"><span style="font-size:10px;padding:2px 6px;border-radius:4px;background:${levelColor}20;color:${levelColor}">${f.level}</span></div></div>`});
html+=`<div style="text-align:center;margin-top:16px"><button class="action-btn secondary" style="display:inline-block;min-width:auto;padding:10px 24px" onclick="insightTab='factorictest';renderInsight()">🔄 重新检验</button></div>
<div style="font-size:11px;color:#475569;margin-top:8px;text-align:center">样本池 ${summary.pool_size||0} 只 · 预测周期 ${summary.forward_days||20} 日 · 耗时 ${summary.elapsed_seconds||0}s</div>`;
document.getElementById('factorICContent').innerHTML=html;
}catch(e){console.warn('Factor IC failed:',e);document.getElementById('factorICContent').innerHTML=`<div style="text-align:center;padding:20px;color:var(--text2)">加载失败<br><button onclick="insightTab='factorictest';renderInsight()" style="margin-top:8px;padding:6px 16px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:12px">🔄 重试</button></div>`}}


// ---- Phase 6: 蒙特卡洛模拟 ----
async function renderMonteCarlo(el){
el.innerHTML=`<div class="dashboard-card" style="overflow:hidden">
<div class="dashboard-card-title">🎲 蒙特卡洛模拟</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:8px">基于历史收益分布，5000次模拟生成概率预测（替代单点预测）</div>
<div style="font-size:11px;color:var(--accent);margin-bottom:12px;padding:6px 8px;background:rgba(245,158,11,.06);border-radius:6px">🎯 输入股票代码 → 获取盈利概率/最差情景/收益分布 · 参考 AQR 蒙特卡洛方法论</div>
<div style="display:flex;gap:8px;margin-bottom:8px">
<input id="mcCode" placeholder="股票代码 如 600519" class="input-field" style="flex:1;min-width:0;padding:10px 12px;border-radius:10px;border:1px solid var(--bg3);background:var(--bg2);color:var(--text);font-size:14px">
<select id="mcHorizon" style="padding:10px;border-radius:10px;border:1px solid var(--bg3);background:var(--bg2);color:var(--text);font-size:12px;flex-shrink:0">
<option value="125">半年</option><option value="250" selected>一年</option><option value="500">两年</option></select>
</div>
<button onclick="runMonteCarlo()" style="width:100%;padding:10px 16px;border-radius:10px;border:none;background:var(--accent);color:#fff;font-weight:700;cursor:pointer;font-size:14px;margin-bottom:16px">🎲 开始模拟</button>
<div id="mcResult"></div>
<div style="margin-top:16px;padding-top:16px;border-top:1px solid rgba(148,163,184,.1)">
<div style="font-size:13px;font-weight:700;margin-bottom:8px">📊 持仓组合模拟</div>
<div style="font-size:11px;color:var(--text2);margin-bottom:8px">自动读取你的持仓，模拟组合概率分布</div>
<button onclick="runPortfolioMC()" style="padding:10px 20px;border-radius:10px;border:1px solid var(--accent);background:transparent;color:var(--accent);font-weight:600;cursor:pointer;font-size:12px">🚀 模拟我的组合</button>
<div id="mcPortfolioResult" style="margin-top:12px"></div>
</div></div>`;
}

async function runMonteCarlo(){
const code=document.getElementById('mcCode')?.value?.trim();
if(!code){alert('请输入股票代码');return}
const horizon=parseInt(document.getElementById('mcHorizon')?.value||'250');
const el=document.getElementById('mcResult');
if(!el)return;
el.innerHTML=`<div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>5000次模拟进行中...</div>`;
try{
// 同时跑有纪律 vs 无纪律对比
const r=await fetch(API_BASE+'/monte-carlo/compare/'+code+'?simulations=5000&horizon_days='+horizon,{signal:AbortSignal.timeout(120000)});
if(!r.ok)throw new Error('API failed');
const d=await r.json();
if(d.error){el.innerHTML=`<div style="padding:16px;color:var(--red);text-align:center">${d.error}</div>`;return}
const w=d.with_discipline||{};
const wo=d.without_discipline||{};
const imp=d.improvement||{};
const wp=w.percentiles||{};const wop=wo.percentiles||{};
const wprob=w.probabilities||{};const woprob=wo.probabilities||{};
const wrisk=w.risk_metrics||{};
const wdisc=w.discipline_stats||{};
let html=`<div style="font-size:13px;font-weight:700;margin-bottom:12px">📊 ${code} · ${w.horizon_years||1}年模拟（${w.simulations||5000}次）</div>`;
// 核心概率卡片
html+=`<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:16px">
<div style="background:rgba(16,185,129,.08);border-radius:12px;padding:12px;text-align:center">
<div style="font-size:28px;font-weight:900;color:var(--green)">${wprob.profit||0}%</div>
<div style="font-size:11px;color:var(--text2)">盈利概率</div>
<div style="font-size:10px;color:var(--text3)">无纪律 ${woprob.profit||0}%</div></div>
<div style="background:rgba(239,68,68,.08);border-radius:12px;padding:12px;text-align:center">
<div style="font-size:28px;font-weight:900;color:var(--red)">${wprob.loss_over_10pct||0}%</div>
<div style="font-size:11px;color:var(--text2)">大亏概率(>10%)</div>
<div style="font-size:10px;color:var(--text3)">无纪律 ${woprob.loss_over_10pct||0}%</div></div>
<div style="background:rgba(99,102,241,.08);border-radius:12px;padding:12px;text-align:center">
<div style="font-size:28px;font-weight:900;color:#818CF8">${wprob.gain_over_20pct||0}%</div>
<div style="font-size:11px;color:var(--text2)">大赚概率(>20%)</div>
<div style="font-size:10px;color:var(--text3)">无纪律 ${woprob.gain_over_20pct||0}%</div></div></div>`;
// 收益分布对比
html+=`<div style="background:var(--bg2);border-radius:12px;padding:12px;margin-bottom:12px">
<div style="font-size:12px;font-weight:700;margin-bottom:8px">收益分布（有纪律 vs 无纪律）</div>
<div style="display:grid;grid-template-columns:60px 1fr 1fr;gap:4px;font-size:11px">
<div style="color:var(--text2);font-weight:600">分位</div>
<div style="color:var(--green);font-weight:600;text-align:right">✅ 有纪律</div>
<div style="color:var(--text2);font-weight:600;text-align:right">❌ 无纪律</div>
${['P10','P25','P50','P75','P90'].map(p=>{
const wv=wp[p]||0;const wov=wop[p]||0;
const label=p==='P10'?'最差10%':p==='P25'?'较差25%':p==='P50'?'中位数':p==='P75'?'较好75%':'最好90%';
return`<div style="color:var(--text2);padding:4px 0">${label}</div>
<div style="text-align:right;padding:4px 0;font-weight:700;color:${wv>=0?'var(--green)':'var(--red)'}">${wv>=0?'+':''}${wv}%</div>
<div style="text-align:right;padding:4px 0;color:${wov>=0?'var(--green)':'var(--red)'}">${wov>=0?'+':''}${wov}%</div>`}).join('')}</div></div>`;
// 风险指标
html+=`<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:12px">
<div style="background:var(--bg2);border-radius:10px;padding:10px">
<div style="font-size:11px;color:var(--text2)">VaR(95%)</div>
<div style="font-size:16px;font-weight:800;color:var(--red)">${wrisk.var_95||0}%</div>
<div style="font-size:10px;color:var(--text3)">最差5%情景的收益</div></div>
<div style="background:var(--bg2);border-radius:10px;padding:10px">
<div style="font-size:11px;color:var(--text2)">CVaR(95%)</div>
<div style="font-size:16px;font-weight:800;color:var(--red)">${wrisk.cvar_95||0}%</div>
<div style="font-size:10px;color:var(--text3)">尾部风险平均损失</div></div></div>`;
// 纪律触发统计
html+=`<div style="background:rgba(245,158,11,.06);border-radius:10px;padding:10px;margin-bottom:12px;font-size:12px">
<div style="font-weight:700;margin-bottom:4px">⚡ 纪律触发率</div>
止损(-8%)触发：${wdisc.stop_loss_triggered||0}% 的路径 · 止盈(+20%)触发：${wdisc.take_profit_triggered||0}% 的路径</div>`;
// 结论
if(d.conclusion){html+=`<div style="background:rgba(59,130,246,.06);border-radius:10px;padding:10px 12px;font-size:12px;line-height:1.8">
<div style="font-weight:700;margin-bottom:4px">📝 结论</div>${d.conclusion}</div>`}
// 历史参数
const hp=w.historical_params||{};
html+=`<div style="font-size:11px;color:#475569;margin-top:12px;text-align:center">基于历史：年化 ${hp.annual_return||0}% · 波动率 ${hp.annual_volatility||0}% · 偏度 ${hp.skewness||0} · 历史最大回撤 ${hp.historical_max_dd||0}%</div>`;
el.innerHTML=html;
}catch(e){console.warn('MC failed:',e);el.innerHTML=`<div style="text-align:center;padding:16px;color:var(--text2)">模拟失败<br><span style="font-size:11px;opacity:0.6">${e.message}</span><br><button onclick="runMonteCarlo()" style="margin-top:8px;padding:6px 16px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:12px">🔄 重试</button></div>`}}

async function runPortfolioMC(){
const el=document.getElementById('mcPortfolioResult');if(!el)return;
el.innerHTML=`<div style="text-align:center;padding:16px;color:var(--text2)"><div class="loading-spinner" style="width:20px;height:20px;margin:0 auto 6px;border-width:2px"></div>读取持仓并模拟中...</div>`;
try{
// 先获取用户持仓
const sr=await fetch(API_BASE+'/stock-holdings/scan?'+getProfileParam(),{signal:AbortSignal.timeout(30000)});
if(!sr.ok)throw new Error('获取持仓失败');
const sd=await sr.json();
const holdings=(sd.holdings||[]).filter(h=>h.code&&h.currentPrice>0);
if(!holdings.length){el.innerHTML='<div style="padding:12px;color:var(--text2);text-align:center">暂无股票持仓，请先添加</div>';return}
// 构建组合
const totalValue=holdings.reduce((s,h)=>s+(h.currentPrice*(h.quantity||0)),0)||holdings.length;
const payload={
holdings:holdings.map(h=>({code:h.code.replace(/^(sh|sz)/i,''),weight:totalValue>0?(h.currentPrice*(h.quantity||0))/totalValue:1/holdings.length})),
simulations:3000,horizon_days:250,initial:100000,discipline:true};
const r=await fetch(API_BASE+'/monte-carlo/portfolio',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:AbortSignal.timeout(120000)});
if(!r.ok)throw new Error('模拟失败');
const d=await r.json();
if(d.error){el.innerHTML=`<div style="padding:12px;color:var(--red)">${d.error}</div>`;return}
const p=d.percentiles||{};const prob=d.probabilities||{};
let html=`<div style="font-size:13px;font-weight:700;margin-bottom:8px">我的组合（${d.holdings?.length||0}只）· 1年模拟</div>
<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px">
<div style="background:rgba(16,185,129,.08);border-radius:10px;padding:10px;text-align:center">
<div style="font-size:22px;font-weight:900;color:var(--green)">${prob.profit||0}%</div>
<div style="font-size:10px;color:var(--text2)">盈利概率</div></div>
<div style="background:rgba(239,68,68,.08);border-radius:10px;padding:10px;text-align:center">
<div style="font-size:22px;font-weight:900;color:var(--red)">${prob.loss_over_10pct||0}%</div>
<div style="font-size:10px;color:var(--text2)">大亏(>10%)</div></div>
<div style="background:rgba(99,102,241,.08);border-radius:10px;padding:10px;text-align:center">
<div style="font-size:22px;font-weight:900;color:#818CF8">${prob.gain_over_20pct||0}%</div>
<div style="font-size:10px;color:var(--text2)">大赚(>20%)</div></div></div>
<div style="font-size:12px;line-height:1.8;color:var(--text)">
收益分布：最差10%=${p.P10||0}% · 中位数=${p.P50||0}% · 最好90%=${p.P90||0}%<br>
预期收益=${d.expected_return||0}% · VaR(95%)=${d.risk_metrics?.var_95||0}%</div>`;
// 持仓明细
if(d.holdings){html+=`<div style="margin-top:8px;font-size:11px;color:var(--text2)">
${d.holdings.map(h=>`${h.code}(${h.weight}%) 年化${h.annual_return||'--'}%`).join(' · ')}</div>`}
el.innerHTML=html;
}catch(e){console.warn('Portfolio MC failed:',e);el.innerHTML=`<div style="text-align:center;padding:12px;color:var(--text2)">组合模拟失败: ${e.message}<br><button onclick="runPortfolioMC()" style="margin-top:6px;padding:4px 12px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:11px">🔄 重试</button></div>`}}


// ============================================================
// 六大量化引擎 UI（对标幻方量化）
// ============================================================

// ---- P1: AI 预测引擎 ----
async function renderAIPredict(el){
el.innerHTML=`<div class="dashboard-card"><div class="dashboard-card-title">🤖 AI 预测引擎</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:8px">MLP神经网络 + GradientBoosting 双模型集成，~40特征预测未来涨跌</div>
<div style="font-size:11px;color:var(--accent);margin-bottom:12px;padding:6px 8px;background:rgba(245,158,11,.06);border-radius:6px">📊 输入股票代码 → 获取预测收益率 + 方向 + 置信度 + 特征重要性</div>
<div style="display:flex;gap:8px;margin-bottom:8px">
<input id="aiCode" placeholder="股票代码 如 000001" class="input-field" style="flex:1;min-width:0;padding:10px 12px;border-radius:10px;border:1px solid var(--bg3);background:var(--bg2);color:var(--text);font-size:14px">
<select id="aiDays" style="padding:10px;border-radius:10px;border:1px solid var(--bg3);background:var(--bg2);color:var(--text);font-size:12px;flex-shrink:0">
<option value="3">3天</option><option value="5" selected>5天</option><option value="10">10天</option><option value="20">20天</option></select>
</div>
<button onclick="runAIPredict()" style="width:100%;padding:10px 16px;border-radius:10px;border:none;background:var(--accent);color:#fff;font-weight:700;cursor:pointer;font-size:14px;margin-bottom:16px">🤖 开始预测</button>
<div id="aiPredResult"></div>
<div style="margin-top:16px;padding-top:16px;border-top:1px solid rgba(148,163,184,.1)">
<div style="font-size:13px;font-weight:700;margin-bottom:8px">📊 持仓组合预测</div>
<button onclick="runAIPredPortfolio()" style="padding:8px 16px;border-radius:8px;border:none;background:linear-gradient(135deg,#3B82F6,#8B5CF6);color:#fff;font-weight:600;cursor:pointer;font-size:12px">🚀 一键预测全部持仓</button>
<div id="aiPortResult" style="margin-top:12px"></div></div></div>`;
}

async function runAIPredict(){
const code=document.getElementById('aiCode')?.value?.trim();if(!code){alert('请输入股票代码');return}
const days=parseInt(document.getElementById('aiDays')?.value||'5');
const el=document.getElementById('aiPredResult');if(!el)return;
el.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>训练模型中（首次约15-30秒）...</div>';
try{const r=await fetch(API_BASE+`/ai-predict/${code}?days=${days}`,{signal:AbortSignal.timeout(60000)});const d=await r.json();
if(d.error){el.innerHTML=`<div style="color:var(--red);padding:12px">${d.error}</div>`;return}
const dirColor=d.direction==='看涨'?'#10B981':(d.direction==='看跌'?'#EF4444':'#94A3B8');
let html=`<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:16px">
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center"><div style="font-size:11px;color:var(--text2)">预测收益</div><div style="font-size:22px;font-weight:800;color:${dirColor}">${d.prediction>0?'+':''}${d.prediction}%</div></div>
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center"><div style="font-size:11px;color:var(--text2)">方向</div><div style="font-size:18px;font-weight:700;color:${dirColor}">${d.direction}</div></div>
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center"><div style="font-size:11px;color:var(--text2)">置信度</div><div style="font-size:18px;font-weight:700">${d.confidence}%</div></div></div>`;
html+=`<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px">
<div style="background:var(--bg2);border-radius:10px;padding:10px"><div style="font-size:11px;color:var(--text2);margin-bottom:6px">MLP 神经网络</div><div style="font-size:14px;font-weight:700">${d.models?.mlp?.prediction>0?'+':''}${d.models?.mlp?.prediction||0}% <span style="font-size:11px;color:var(--text2)">(权重${d.models?.mlp?.weight||0}%)</span></div></div>
<div style="background:var(--bg2);border-radius:10px;padding:10px"><div style="font-size:11px;color:var(--text2);margin-bottom:6px">GBM 梯度提升</div><div style="font-size:14px;font-weight:700">${d.models?.gbm?.prediction>0?'+':''}${d.models?.gbm?.prediction||0}% <span style="font-size:11px;color:var(--text2)">(权重${d.models?.gbm?.weight||0}%)</span></div></div></div>`;
const bt=d.backtest||{};
html+=`<div style="font-size:12px;color:var(--text2);padding:8px;background:var(--bg2);border-radius:8px;margin-bottom:12px">回测 | 方向准确率: <b>${bt.direction_accuracy||0}%</b> · 相关系数: <b>${bt.correlation||0}</b> · 训练样本: ${bt.train_samples||0} · 测试样本: ${bt.test_samples||0}</div>`;
if(d.feature_importance?.length>0){html+=`<div style="font-size:12px;font-weight:600;margin-bottom:6px">📊 特征重要性 Top 10</div>`;
d.feature_importance.slice(0,10).forEach((f,i)=>{const pct=Math.round(f[1]*1000)/10;
html+=`<div style="display:flex;align-items:center;gap:8px;margin-bottom:3px;font-size:11px"><span style="width:100px;color:var(--text2)">${f[0]}</span><div style="flex:1;height:6px;background:var(--bg3);border-radius:3px;overflow:hidden"><div style="height:100%;width:${Math.min(pct*5,100)}%;background:linear-gradient(90deg,#3B82F6,#8B5CF6);border-radius:3px"></div></div><span>${pct}%</span></div>`})}
el.innerHTML=html;
}catch(e){el.innerHTML=`<div style="color:var(--text2);text-align:center;padding:12px">预测失败: ${e.message}<br><button onclick="runAIPredict()" style="margin-top:6px;padding:4px 12px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:11px">🔄 重试</button></div>`}}

async function runAIPredPortfolio(){const el=document.getElementById('aiPortResult');if(!el)return;
el.innerHTML='<div style="text-align:center;padding:15px;color:var(--text2)"><div class="loading-spinner" style="width:20px;height:20px;margin:0 auto 6px;border-width:2px"></div>正在预测全部持仓...</div>';
try{const uid=getProfileId();const r=await fetch(API_BASE+`/ai-predict/portfolio/${uid}`,{signal:AbortSignal.timeout(120000)});const d=await r.json();
if(d.error){el.innerHTML=`<div style="color:var(--red);padding:8px">${d.error}</div>`;return}
const dirColor=d.portfolio_direction==='看涨'?'#10B981':(d.portfolio_direction==='看跌'?'#EF4444':'#94A3B8');
let html=`<div style="background:var(--bg2);border-radius:10px;padding:12px;margin-bottom:12px;text-align:center"><div style="font-size:11px;color:var(--text2)">组合预测</div><div style="font-size:24px;font-weight:800;color:${dirColor}">${d.portfolio_prediction>0?'+':''}${d.portfolio_prediction}%</div><div style="font-size:13px;color:${dirColor}">${d.portfolio_direction}</div></div>`;
(d.stocks||[]).forEach(s=>{const c=s.direction==='看涨'?'#10B981':(s.direction==='看跌'?'#EF4444':'#94A3B8');
html+=`<div style="display:flex;justify-content:space-between;align-items:center;padding:8px;border-bottom:1px solid rgba(148,163,184,.1);font-size:12px"><span>${s.name||s.code}</span><span style="color:var(--text2)">${s.weight}%仓位</span><span style="color:${c};font-weight:700">${s.prediction>0?'+':''}${s.prediction}%</span><span style="font-size:11px">${s.confidence}%信心</span></div>`});
el.innerHTML=html;
}catch(e){el.innerHTML=`<div style="color:var(--text2);padding:8px">失败: ${e.message}</div>`}}

// ---- P2: 遗传因子挖掘 ----
async function renderGeneticFactor(el){
el.innerHTML=`<div class="dashboard-card"><div class="dashboard-card-title">🧬 遗传编程因子挖掘</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:8px">用遗传算法自动发现人类想不到的 Alpha 因子（对标幻方量化因子挖掘）</div>
<div style="font-size:11px;color:var(--accent);margin-bottom:12px;padding:6px 8px;background:rgba(245,158,11,.06);border-radius:6px">🧬 200个体 × 30代进化 → 保留 IC 最高的因子表达式</div>
<div style="display:flex;gap:8px;margin-bottom:16px">
<input id="gfCode" placeholder="股票代码 如 000001" class="input-field" value="000001" style="flex:1;padding:10px 12px;border-radius:10px;border:1px solid var(--bg3);background:var(--bg2);color:var(--text);font-size:14px">
<button onclick="runGeneticFactor()" style="padding:10px 16px;border-radius:10px;border:none;background:linear-gradient(135deg,#10B981,#059669);color:#fff;font-weight:700;cursor:pointer;white-space:nowrap">🧬 开始进化</button></div>
<div id="gfResult"></div></div>`;
}

async function runGeneticFactor(){const code=document.getElementById('gfCode')?.value?.trim()||'000001';
const el=document.getElementById('gfResult');if(!el)return;
el.innerHTML='<div style="text-align:center;padding:30px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>遗传进化中... 200个体 × 30代<br><span style="font-size:11px;opacity:0.6">约30-90秒</span></div>';
try{const r=await fetch(API_BASE+`/genetic-factor/${code}?generations=30&top_k=10`,{signal:AbortSignal.timeout(120000)});const d=await r.json();
if(d.error){el.innerHTML=`<div style="color:var(--red);padding:12px">${d.error}</div>`;return}
const s=d.summary||{};
let html=`<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:16px">
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center"><div style="font-size:11px;color:var(--text2)">🏆 优秀因子</div><div style="font-size:20px;font-weight:800;color:#10B981">${s.excellent||0}</div></div>
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center"><div style="font-size:11px;color:var(--text2)">✅ 有效因子</div><div style="font-size:20px;font-weight:800;color:#3B82F6">${s.effective||0}</div></div>
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center"><div style="font-size:11px;color:var(--text2)">⚠️ 弱因子</div><div style="font-size:20px;font-weight:800;color:#94A3B8">${s.weak||0}</div></div></div>`;
(d.top_factors||[]).forEach(f=>{const icColor=f.ic>0.05?'#10B981':(f.ic>0.03?'#3B82F6':'#94A3B8');
html+=`<div style="padding:10px;margin-bottom:6px;background:var(--bg2);border-radius:10px;border-left:3px solid ${icColor}">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px"><span style="font-weight:700;font-size:12px">#${f.rank} ${f.rating}</span><span style="font-size:12px;color:${icColor};font-weight:700">IC=${f.ic}</span></div>
<div style="font-size:10px;color:var(--text2);font-family:monospace;word-break:break-all;background:var(--bg3);padding:4px 6px;border-radius:4px">${f.expression}</div></div>`});
el.innerHTML=html;
}catch(e){el.innerHTML=`<div style="color:var(--text2);text-align:center;padding:12px">进化失败: ${e.message}<br><button onclick="runGeneticFactor()" style="margin-top:6px;padding:4px 12px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:11px">🔄 重试</button></div>`}}

// ---- P3: 组合优化器 ----
async function renderOptimizer(el){
el.innerHTML=`<div class="dashboard-card"><div class="dashboard-card-title">⚡ 组合优化器</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:8px">5种方法计算数学最优持仓比例（从"拍脑袋"到Markowitz/CVaR/HRP）</div>
<div style="font-size:11px;color:var(--accent);margin-bottom:12px;padding:6px 8px;background:rgba(245,158,11,.06);border-radius:6px">📈 最大夏普 · 🛡️ 最小方差 · ⚡ CVaR(幻方方法) · 🌳 HRP · ⚖️ 等权基准</div>
<button onclick="runOptimizer()" style="padding:10px 20px;border-radius:10px;border:none;background:linear-gradient(135deg,#F59E0B,#EF4444);color:#fff;font-weight:700;cursor:pointer;font-size:13px">⚡ 优化我的持仓</button>
<div id="optResult" style="margin-top:16px"></div></div>`;
}

async function runOptimizer(){const el=document.getElementById('optResult');if(!el)return;
el.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>获取历史数据 + 计算协方差矩阵...</div>';
try{const uid=getProfileId();const r=await fetch(API_BASE+`/portfolio-optimize/${uid}`,{signal:AbortSignal.timeout(90000)});const d=await r.json();
if(d.error){el.innerHTML=`<div style="color:var(--red);padding:12px">${d.error}</div>`;return}
let html='';
if(d.recommendation)html+=`<div style="padding:10px;background:rgba(59,130,246,.1);border-radius:10px;border:1px solid rgba(59,130,246,.2);margin-bottom:16px;font-size:13px;font-weight:600">💡 ${d.recommendation}</div>`;
const methods=d.methods||{};
Object.entries(methods).forEach(([key,m])=>{const met=m.metrics||{};
html+=`<div style="padding:12px;margin-bottom:10px;background:var(--bg2);border-radius:12px"><div style="font-size:13px;font-weight:700;margin-bottom:8px">${m.name}</div>
<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px;font-size:11px;margin-bottom:8px">
<div>年化收益 <b style="color:#10B981">${met.annual_return}%</b></div>
<div>夏普比率 <b>${met.sharpe_ratio}</b></div>
<div>最大回撤 <b style="color:#EF4444">${met.max_drawdown}%</b></div></div>
<div style="display:flex;flex-wrap:wrap;gap:4px">`
;(m.allocations||[]).forEach(a=>{html+=`<span style="font-size:10px;padding:2px 8px;background:var(--bg3);border-radius:4px">${a.name} ${a.weight}%</span>`});
html+=`</div></div>`});
if(d.adjustments?.length>0){html+=`<div style="padding:12px;background:rgba(245,158,11,.08);border-radius:12px;border:1px solid rgba(245,158,11,.15)"><div style="font-size:13px;font-weight:700;margin-bottom:8px">📋 建议调仓</div>`;
d.adjustments.forEach(a=>{const c=a.action.includes('加仓')?'#10B981':'#EF4444';
html+=`<div style="display:flex;justify-content:space-between;padding:4px 0;font-size:12px;border-bottom:1px solid rgba(148,163,184,.08)"><span>${a.name}</span><span>${a.current}% → ${a.optimal}%</span><span style="color:${c};font-weight:700">${a.action}</span></div>`});
html+=`</div>`}
el.innerHTML=html;
}catch(e){el.innerHTML=`<div style="color:var(--text2);text-align:center;padding:12px">优化失败: ${e.message}<br><button onclick="runOptimizer()" style="margin-top:6px;padding:4px 12px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:11px">🔄 重试</button></div>`}}

// ---- P4: 另类数据 ----
async function renderAltData(el){
el.innerHTML=`<div class="dashboard-card"><div class="dashboard-card-title">📡 另类数据仪表盘</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:12px">散户版"卫星替代品" — 北向资金/融资融券/龙虎榜/大宗交易/行业资金流</div>
<div id="altDataResult"><div style="text-align:center;padding:30px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>加载6大另类数据源...</div></div></div>`;
try{const r=await fetch(API_BASE+'/alt-data/dashboard',{signal:AbortSignal.timeout(60000)});const d=await r.json();
const el2=document.getElementById('altDataResult');if(!el2)return;
let html='';
if(d.overall_signal)html+=`<div style="padding:10px;background:rgba(59,130,246,.1);border-radius:10px;border:1px solid rgba(59,130,246,.2);margin-bottom:16px;font-size:14px;font-weight:700;text-align:center">${d.overall_signal}</div>`;
// 北向资金
const nb=d.northbound||{};
html+=`<div style="padding:10px;margin-bottom:8px;background:var(--bg2);border-radius:10px"><div style="font-size:13px;font-weight:700;margin-bottom:6px">🏦 北向资金</div>`;
if(nb.signal)html+=`<div style="font-size:12px;margin-bottom:6px">${nb.signal}</div>`;
if(nb.top_stocks?.length>0){html+=`<div style="font-size:11px;color:var(--text2)">Top 持股: `;nb.top_stocks.slice(0,5).forEach(s=>{html+=`<span style="margin-right:6px">${s.name}</span>`});html+=`</div>`}
html+=`</div>`;
// 融资融券
const mg=d.margin||{};
html+=`<div style="padding:10px;margin-bottom:8px;background:var(--bg2);border-radius:10px"><div style="font-size:13px;font-weight:700;margin-bottom:6px">💰 融资融券</div>`;
if(mg.signal)html+=`<div style="font-size:12px">${mg.signal}</div>`;
html+=`</div>`;
// 行业资金流
const sf=d.sector_flow||{};
if(sf.inflow?.length>0){html+=`<div style="padding:10px;margin-bottom:8px;background:var(--bg2);border-radius:10px"><div style="font-size:13px;font-weight:700;margin-bottom:6px">🏭 行业资金流</div>`;
html+=`<div style="font-size:11px;color:#10B981;margin-bottom:4px">流入: `;sf.inflow.slice(0,5).forEach(s=>{html+=`${s.name}(${s.net_flow.toFixed(1)}亿) `});
html+=`</div><div style="font-size:11px;color:#EF4444">流出: `;(sf.outflow||[]).slice(0,5).forEach(s=>{html+=`${s.name}(${s.net_flow.toFixed(1)}亿) `});
html+=`</div></div>`}
// 龙虎榜
const dt=d.dragon_tiger||{};
if(dt.records?.length>0){html+=`<div style="padding:10px;margin-bottom:8px;background:var(--bg2);border-radius:10px"><div style="font-size:13px;font-weight:700;margin-bottom:6px">🐲 龙虎榜</div>`;
dt.records.slice(0,5).forEach(r2=>{html+=`<div style="font-size:11px;padding:3px 0;border-bottom:1px solid rgba(148,163,184,.06)">${r2.name} | ${r2.reason} | 净额 ${r2.net>0?'+':''}${r2.net.toFixed(0)}万</div>`});
html+=`</div>`}
el2.innerHTML=html;
}catch(e){document.getElementById('altDataResult').innerHTML=`<div style="color:var(--text2);text-align:center;padding:12px">加载失败: ${e.message}<br><button onclick="insightTab='altdata';renderInsight()" style="margin-top:6px;padding:4px 12px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:11px">🔄 重试</button></div>`}}

// ---- P5: RL 仓位管理 ----
async function renderRLPosition(el){
el.innerHTML=`<div class="dashboard-card"><div class="dashboard-card-title">🎮 强化学习仓位建议</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:8px">Q-Learning Agent 在历史数据上训练，给出动态仓位建议</div>
<div style="display:flex;gap:8px;margin-bottom:16px">
<input id="rlCode" placeholder="股票代码 如 600519" class="input-field" style="flex:1;padding:10px 12px;border-radius:10px;border:1px solid var(--bg3);background:var(--bg2);color:var(--text);font-size:14px">
<button onclick="runRL()" style="padding:10px 16px;border-radius:10px;border:none;background:linear-gradient(135deg,#8B5CF6,#6366F1);color:#fff;font-weight:700;cursor:pointer;white-space:nowrap">🎮 获取建议</button></div>
<div id="rlResult"></div></div>`;
}

async function runRL(){const code=document.getElementById('rlCode')?.value?.trim();if(!code){alert('请输入股票代码');return}
const el=document.getElementById('rlResult');if(!el)return;
el.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>Q-Learning 训练中（5轮迭代）...</div>';
try{const r=await fetch(API_BASE+`/rl-position/${code}`,{signal:AbortSignal.timeout(90000)});const d=await r.json();
if(d.error){el.innerHTML=`<div style="color:var(--red);padding:12px">${d.error}</div>`;return}
const ms=d.market_state||{};const ts=d.training_summary||{};
let html=`<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px">
<div style="background:var(--bg2);border-radius:10px;padding:10px"><div style="font-size:11px;color:var(--text2)">市场状态</div><div style="font-size:13px;font-weight:700">${ms.trend} | RSI ${ms.rsi}</div><div style="font-size:11px;color:var(--text2)">20日收益 ${ms.return_20d}% · 波动 ${ms.volatility}%</div></div>
<div style="background:var(--bg2);border-radius:10px;padding:10px"><div style="font-size:11px;color:var(--text2)">训练效果</div><div style="font-size:13px;font-weight:700;color:${ts.outperformance>0?'#10B981':'#EF4444'}">超额收益 ${ts.outperformance>0?'+':''}${ts.outperformance}%</div><div style="font-size:11px;color:var(--text2)">RL ${ts.final_rl_return}% vs 买入持有 ${ts.buy_hold_return}%</div></div></div>`;
html+=`<div style="font-size:12px;font-weight:600;margin-bottom:8px">📋 不同仓位下的建议</div>`;
(d.recommendations||[]).forEach(r2=>{
html+=`<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 10px;margin-bottom:4px;background:var(--bg2);border-radius:8px;font-size:12px"><span style="width:70px">当前 ${r2.current_position}</span><span style="flex:1;font-weight:700">${r2.action}</span><span>→ ${r2.target_position}</span></div>`});
el.innerHTML=html;
}catch(e){el.innerHTML=`<div style="color:var(--text2);text-align:center;padding:12px">失败: ${e.message}</div>`}}

// ---- P6: LLM 因子生成 ----
async function renderLLMFactor(el){
el.innerHTML=`<div class="dashboard-card"><div class="dashboard-card-title">🧠 LLM 因子生成器 (Alpha-GPT)</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:8px">让 DeepSeek AI 自动构思交易因子 → 生成代码 → IC 验证 → 迭代优化</div>
<div style="font-size:11px;color:var(--accent);margin-bottom:12px;padding:6px 8px;background:rgba(245,158,11,.06);border-radius:6px">🧠 AI 生成因子假设 → Python代码 → 自动回测IC → 反馈迭代（2轮进化）</div>
<div style="display:flex;gap:8px;margin-bottom:16px">
<input id="llmfCode" placeholder="股票代码 如 000001" class="input-field" value="000001" style="flex:1;padding:10px 12px;border-radius:10px;border:1px solid var(--bg3);background:var(--bg2);color:var(--text);font-size:14px">
<button onclick="runLLMFactor()" style="padding:10px 16px;border-radius:10px;border:none;background:linear-gradient(135deg,#EC4899,#8B5CF6);color:#fff;font-weight:700;cursor:pointer;white-space:nowrap">🧠 AI 生成</button></div>
<div id="llmfResult"></div></div>`;
}

async function runLLMFactor(){const code=document.getElementById('llmfCode')?.value?.trim()||'000001';
const el=document.getElementById('llmfResult');if(!el)return;
el.innerHTML='<div style="text-align:center;padding:30px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>DeepSeek AI 构思因子中（2轮迭代）...<br><span style="font-size:11px;opacity:0.6">约30-90秒</span></div>';
try{const r=await fetch(API_BASE+`/llm-factor/${code}?count=5&iterations=2`,{signal:AbortSignal.timeout(180000)});const d=await r.json();
if(d.error){el.innerHTML=`<div style="color:var(--red);padding:12px">${d.error}</div>`;return}
const s=d.summary||{};
let html=`<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:16px">
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center"><div style="font-size:11px;color:var(--text2)">生成因子</div><div style="font-size:20px;font-weight:800">${s.total_generated||0}</div></div>
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center"><div style="font-size:11px;color:var(--text2)">有效因子</div><div style="font-size:20px;font-weight:800;color:#10B981">${s.effective||0}</div></div>
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center"><div style="font-size:11px;color:var(--text2)">最高IC</div><div style="font-size:20px;font-weight:800;color:#3B82F6">${s.best_ic||0}</div></div></div>`;
(d.effective_factors||[]).forEach(f=>{const icColor=f.abs_ic>0.05?'#10B981':'#3B82F6';
html+=`<div style="padding:10px;margin-bottom:6px;background:var(--bg2);border-radius:10px;border-left:3px solid ${icColor}">
<div style="display:flex;justify-content:space-between;margin-bottom:4px"><span style="font-weight:700;font-size:12px">${f.name} ${f.status}</span><span style="color:${icColor};font-weight:700;font-size:12px">IC=${f.ic}</span></div>
<div style="font-size:11px;color:var(--text2);margin-bottom:4px">${f.logic||''}</div>
${f.code?`<details><summary style="font-size:10px;color:var(--accent);cursor:pointer">查看代码</summary><pre style="font-size:9px;background:var(--bg3);padding:6px;border-radius:4px;overflow-x:auto;margin-top:4px">${f.code}</pre></details>`:''}</div>`});
if(d.failed_factors?.length>0){html+=`<details style="margin-top:12px"><summary style="font-size:12px;color:var(--text2);cursor:pointer">❌ 失败/无效因子 (${d.failed_factors.length}个)</summary>`;
d.failed_factors.forEach(f=>{html+=`<div style="font-size:11px;color:var(--text2);padding:4px 8px">${f.name}: IC=${f.ic} ${f.status}</div>`});
html+=`</details>`}
el.innerHTML=html;
}catch(e){el.innerHTML=`<div style="color:var(--text2);text-align:center;padding:12px">生成失败: ${e.message}<br><button onclick="runLLMFactor()" style="margin-top:6px;padding:4px 12px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:11px">🔄 重试</button></div>`}}


// ---- 📡 信号侦察兵 Tab ----
async function renderSignalScout(el){
el.innerHTML=`<div class="dashboard-card" style="overflow:hidden">
<div class="dashboard-card-title">📡 信号侦察兵</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:8px">多源信号收集（新闻/公告/增减持/解禁/资金）→ 自动匹配你的持仓</div>
<div style="display:flex;gap:8px;margin-bottom:12px">
<button onclick="renderSignalScout(document.getElementById('insightContent'))" style="padding:8px 16px;border-radius:8px;border:none;background:var(--accent);color:#fff;font-weight:600;cursor:pointer;font-size:12px">🔄 刷新</button>
<button onclick="manualScanSignals()" id="scanBtn" style="padding:8px 16px;border-radius:8px;border:1px solid var(--accent);background:transparent;color:var(--accent);font-weight:600;cursor:pointer;font-size:12px">🔍 全市场扫描</button>
</div>
<div id="signalScoutContent"><div style="text-align:center;padding:30px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>正在匹配信号与你的持仓...</div></div>
</div>`;
try{
const uid=getProfileId();
const r=await fetch(API_BASE+'/signal-scout/latest?userId='+encodeURIComponent(uid),{signal:AbortSignal.timeout(30000)});
if(!r.ok)throw new Error('fetch failed');
const d=await r.json();
const el2=document.getElementById('signalScoutContent');if(!el2)return;
const signals=d.signals||[];
if(!signals.length){el2.innerHTML='<div style="text-align:center;padding:30px;color:var(--text2)">暂无匹配信号<br><span style="font-size:11px;opacity:0.6">你的持仓暂未检测到相关信号</span></div>';return}
const levelColor={danger:'var(--red)',warning:'#F59E0B',info:'var(--text2)'};
const levelIcon={danger:'🔴',warning:'⚠️',info:'📌'};
let html=`<div style="display:flex;gap:8px;margin-bottom:12px;font-size:12px;color:var(--text2)">
<span>匹配 <b style="color:var(--accent)">${d.total}</b> 条</span>
<span>高相关 <b style="color:var(--green)">${d.high_relevance}</b></span>
<span>${d.is_trading_day?'✅ 交易日':'🔒 非交易日'}</span></div>`;
html+=signals.map(s=>{
const icon=levelIcon[s.level]||'📌';
const color=levelColor[s.level]||'var(--text2)';
const relBadge=s.relevance>=50?`<span style="font-size:10px;padding:1px 6px;border-radius:4px;background:rgba(16,185,129,.15);color:var(--green)">持仓相关</span>`:'';
const holdingBadge=s.related_holding?`<span style="font-size:10px;padding:1px 6px;border-radius:4px;background:rgba(99,102,241,.15);color:#818CF8">${s.related_holding}</span>`:'';
const tags=(s.tags||[]).slice(0,3).map(t=>`<span style="font-size:10px;padding:1px 4px;border-radius:3px;background:var(--bg3);color:var(--text2)">${t}</span>`).join('');
return`<div style="padding:10px 0;border-bottom:1px solid rgba(148,163,184,.06)">
<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
<span style="color:${color}">${icon}</span>
<span style="font-size:13px;font-weight:600;flex:1">${s.title}</span>
${relBadge}${holdingBadge}
</div>
<div style="display:flex;gap:4px;flex-wrap:wrap;margin-top:4px">${tags}</div>
<div style="font-size:11px;color:var(--text2);margin-top:4px">${s.source||''} · ${s.time||''}</div>
</div>`}).join('');
html+=`<div style="font-size:11px;color:#475569;margin-top:12px;text-align:center">扫描于 ${new Date(d.scanned_at).toLocaleString('zh-CN')}</div>`;
el2.innerHTML=html;
// Phase 0 (3.1): 追加 AI 信号解读（Pro 模式调 /daily-signal/interpret）
if(isProMode()&&API_AVAILABLE){
  el2.insertAdjacentHTML('beforeend','<div id="signalInterpretBox" style="margin-top:16px;padding:12px;border-radius:10px;background:rgba(99,102,241,.06);border:1px solid rgba(99,102,241,.15)"><div class="dashboard-card-title" style="margin-bottom:8px">🤖 AI 信号解读</div><div style="text-align:center;padding:12px;color:var(--text2);font-size:12px"><div class="loading-spinner" style="width:16px;height:16px;margin:0 auto 6px;border-width:2px"></div>DeepSeek 正在解读 12 维信号...</div></div>');
  fetch(`${API_BASE}/daily-signal/interpret`,{signal:AbortSignal.timeout(30000)}).then(r=>r.json()).then(sd=>{
    const box=document.getElementById('signalInterpretBox');if(!box)return;
    const interp=sd.interpretation||sd.detail||'';
    const overall=sd.overall_score!==undefined?`<span style="font-size:20px;font-weight:700;color:${sd.overall_score>=60?'var(--green)':sd.overall_score>=40?'#F59E0B':'var(--red)'}">${sd.overall_score}分</span>`:'';
    box.innerHTML=`<div class="dashboard-card-title" style="margin-bottom:8px">🤖 AI 信号解读 ${overall}</div><div style="font-size:13px;line-height:1.8;color:var(--text1);white-space:pre-wrap">${interp}</div>`;
  }).catch(()=>{const box=document.getElementById('signalInterpretBox');if(box)box.innerHTML='<div class="dashboard-card-title">🤖 AI 信号解读</div><div style="color:var(--text2);font-size:12px">解读加载失败</div>'});
}
}catch(e){console.warn('Signal scout failed:',e);
const el2=document.getElementById('signalScoutContent');
if(el2)el2.innerHTML=`<div style="text-align:center;padding:20px;color:var(--text2)">信号加载失败<br><button onclick="renderSignalScout(document.getElementById('insightContent'))" style="margin-top:8px;padding:6px 16px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:12px">🔄 重试</button></div>`}}

async function manualScanSignals(){
const btn=document.getElementById('scanBtn');if(btn){btn.textContent='扫描中...';btn.disabled=true}
try{await fetch(API_BASE+'/signal-scout/scan',{method:'POST',signal:AbortSignal.timeout(30000)});
renderSignalScout(document.getElementById('insightContent'))}
catch(e){if(btn){btn.textContent='🔍 全市场扫描';btn.disabled=false}}}


// ---- 📊 判断成绩单 Tab ----
async function renderScorecard(el){
el.innerHTML=`<div class="dashboard-card" style="overflow:hidden">
<div class="dashboard-card-title">📊 判断成绩单</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:8px">追踪每次AI决策的准确率 → EMA自动校准模块权重 → 越用越准</div>
<div style="display:flex;gap:8px;margin-bottom:12px">
<button onclick="renderScorecard(document.getElementById('insightContent'))" style="padding:8px 16px;border-radius:8px;border:none;background:var(--accent);color:#fff;font-weight:600;cursor:pointer;font-size:12px">🔄 刷新</button>
<button onclick="manualCalibrate()" id="calibBtn" style="padding:8px 16px;border-radius:8px;border:1px solid var(--accent);background:transparent;color:var(--accent);font-weight:600;cursor:pointer;font-size:12px">⚖️ 手动校准权重</button>
</div>
<div id="scorecardContent"><div style="text-align:center;padding:30px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>加载成绩单...</div></div>
<div id="weightsContent" style="margin-top:16px"></div>
</div>`;
try{
const uid=getProfileId()||getUserId();
const[cardRes,weightRes]=await Promise.all([
fetch(API_BASE+'/judgment/scorecard?userId='+encodeURIComponent(uid),{signal:AbortSignal.timeout(15000)}),
fetch(API_BASE+'/judgment/weights?userId='+encodeURIComponent(uid),{signal:AbortSignal.timeout(10000)})
]);
const card=await cardRes.json();const wdata=await weightRes.json();
const el2=document.getElementById('scorecardContent');if(!el2)return;

// 核心指标卡
const accColor=card.accuracy>=70?'var(--green)':card.accuracy>=50?'var(--accent)':'var(--red)';
let html=`<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:16px">
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center">
<div style="font-size:28px;font-weight:900;color:${accColor}">${card.accuracy}%</div>
<div style="font-size:11px;color:var(--text2)">准确率</div></div>
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center">
<div style="font-size:28px;font-weight:900">${card.total}</div>
<div style="font-size:11px;color:var(--text2)">总判断</div></div>
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center">
<div style="font-size:28px;font-weight:900;color:var(--green)">${card.correct}</div>
<div style="font-size:11px;color:var(--text2)">✅正确 / ❌${card.wrong} / 🟡${card.partial}</div></div></div>`;

// 模块准确率
const modAcc=card.module_accuracy||{};
if(Object.keys(modAcc).length){
html+=`<div style="font-size:13px;font-weight:700;margin-bottom:8px">📊 各模块准确率</div>`;
Object.entries(modAcc).sort((a,b)=>b[1].accuracy-a[1].accuracy).forEach(([mod,s])=>{
const mc=s.accuracy>=70?'var(--green)':s.accuracy>=50?'var(--accent)':'var(--red)';
html+=`<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid rgba(148,163,184,.06)">
<span style="flex:1;font-size:12px">${mod}</span>
<span style="font-size:11px;color:var(--text2)">${s.correct}/${s.total}</span>
<div style="width:80px;height:6px;background:var(--bg3);border-radius:3px;overflow:hidden"><div style="height:100%;width:${s.accuracy}%;background:${mc};border-radius:3px"></div></div>
<span style="font-size:12px;font-weight:700;color:${mc};min-width:40px;text-align:right">${s.accuracy}%</span></div>`})}

// 最近判断
if(card.recent&&card.recent.length){
html+=`<div style="font-size:13px;font-weight:700;margin:16px 0 8px">📋 最近判断</div>`;
card.recent.forEach(r=>{
const dirIcon=r.direction==='bullish'?'📈':r.direction==='bearish'?'📉':'➖';
const verdictIcon=r.verdict==='correct'?'✅':r.verdict==='wrong'?'❌':r.verdict==='partial'?'🟡':'⏳';
const dt=r.recorded_at?.slice(0,16).replace('T',' ')||'';
html+=`<div style="display:flex;align-items:center;gap:6px;padding:6px 0;border-bottom:1px solid rgba(148,163,184,.04);font-size:12px">
<span>${dirIcon}</span><span style="flex:1">${r.regime||''} · 置信${r.confidence}%</span>
<span>${verdictIcon}</span>
${r.actual_return!=null?`<span style="color:${r.actual_return>=0?'var(--green)':'var(--red)'}">实际${r.actual_return>0?'+':''}${r.actual_return}%</span>`:'<span style="color:var(--text2)">待验证</span>'}
<span style="color:var(--text2);font-size:10px">${dt.slice(5)}</span></div>`})}

el2.innerHTML=html;

// 权重面板
const wel=document.getElementById('weightsContent');if(wel){
const w=wdata.weights||{};
let wh=`<div style="font-size:13px;font-weight:700;margin-bottom:8px">⚖️ 当前模块权重（EMA校准）</div>`;
Object.entries(w).sort((a,b)=>b[1]-a[1]).forEach(([mod,val])=>{
const pct=Math.round(val*100);
wh+=`<div style="display:flex;align-items:center;gap:8px;padding:4px 0;font-size:12px">
<span style="min-width:120px">${mod}</span>
<div style="flex:1;height:6px;background:var(--bg3);border-radius:3px;overflow:hidden"><div style="height:100%;width:${pct}%;background:var(--accent);border-radius:3px"></div></div>
<span style="min-width:35px;text-align:right;font-weight:600">${pct}%</span></div>`});
wel.innerHTML=wh}
}catch(e){console.warn('Scorecard failed:',e);
const el2=document.getElementById('scorecardContent');
if(el2)el2.innerHTML=`<div style="text-align:center;padding:20px;color:var(--text2)">暂无成绩数据<br><span style="font-size:11px;opacity:0.6">需要先有 Pipeline 决策记录</span></div>`}}

async function manualCalibrate(){
const btn=document.getElementById('calibBtn');if(btn){btn.textContent='校准中...';btn.disabled=true}
try{const uid=getProfileId()||getUserId();
const r=await fetch(API_BASE+'/judgment/calibrate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:uid}),signal:AbortSignal.timeout(15000)});
const d=await r.json();
if(d.status==='calibrated'){alert('✅ 权重校准完成！准确率:'+d.overall_accuracy+'%');renderScorecard(document.getElementById('insightContent'))}
else{alert('⚠️ '+d.message)}
}catch(e){alert('校准失败: '+e.message)}
finally{if(btn){btn.textContent='⚖️ 手动校准权重';btn.disabled=false}}}


// ---- 🏥 持仓体检 Tab ----
async function renderDoctor(el){
el.innerHTML=`<div class="dashboard-card" style="overflow:hidden">
<div class="dashboard-card-title">🏥 持仓体检</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:8px">压力测试+集中度诊断+健康评分 — 找出你的持仓隐患</div>
<button onclick="runDoctor()" style="width:100%;padding:12px;border-radius:10px;border:none;background:linear-gradient(135deg,#10B981,#059669);color:#fff;font-weight:700;cursor:pointer;font-size:14px;margin-bottom:16px">🏥 开始体检</button>
<div id="doctorResult"></div></div>`;
}

async function runDoctor(){
const el=document.getElementById('doctorResult');if(!el)return;
el.innerHTML='<div style="text-align:center;padding:30px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>正在体检（收集持仓+压力测试+集中度分析）...</div>';
try{
const r=await fetch(API_BASE+'/portfolio-doctor/diagnose?userId='+getProfileId(),{signal:AbortSignal.timeout(60000)});
if(!r.ok)throw new Error('体检失败');
const d=await r.json();
if(d.status==='no_data'){el.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)">暂无持仓，请先添加股票或基金</div>';return}
const h=d.health||{};const c=d.concentration||{};const s=d.stress_test||{};
let html='';
// 健康评分卡
html+=`<div style="text-align:center;padding:20px;margin-bottom:16px;background:var(--bg2);border-radius:16px">
<div style="font-size:48px;font-weight:900;color:${h.score>=70?'var(--green)':h.score>=50?'var(--accent)':'var(--red)'}">${h.score||0}</div>
<div style="font-size:16px;font-weight:700;margin-top:4px">${h.grade||'?'}</div>
<div style="display:flex;justify-content:center;gap:16px;margin-top:12px;font-size:12px">
${Object.entries(h.dimensions||{}).map(([k,v])=>{const max=(h.max_scores||{})[k]||25;const labels={concentration:'集中度',diversification:'多样性',risk:'风险',stability:'稳定性'};return`<div><div style="color:var(--text2)">${labels[k]||k}</div><div style="font-weight:700;color:${v>=max*0.7?'var(--green)':v>=max*0.4?'var(--accent)':'var(--red)'}\">${v}/${max}</div></div>`}).join('')}
</div></div>`;
// 集中度
html+=`<div style="margin-bottom:16px;background:var(--bg2);border-radius:12px;padding:12px">
<div style="font-size:13px;font-weight:700;margin-bottom:8px">📊 集中度 ${c.hhi_level||''}</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:8px">HHI=${c.hhi||0} | 权益占比 ${c.equity_pct||0}%</div>
${(c.holdings_weight||[]).slice(0,8).map(w=>`<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;font-size:12px"><span style="width:80px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${w.name}</span><div style="flex:1;height:6px;background:var(--bg3);border-radius:3px;overflow:hidden"><div style="height:100%;width:${Math.min(w.weight,100)}%;background:${w.weight>30?'var(--red)':w.weight>15?'var(--accent)':'var(--green)'};border-radius:3px"></div></div><span style="min-width:40px;text-align:right">${w.weight}%</span></div>`).join('')}
</div>`;
// 压力测试
if(s.scenarios&&s.scenarios.length){
html+=`<div style="margin-bottom:16px;background:var(--bg2);border-radius:12px;padding:12px">
<div style="font-size:13px;font-weight:700;margin-bottom:8px">🔬 压力测试（总市值 ¥${(s.total_value||0).toLocaleString()}）</div>
${s.scenarios.map(sc=>`<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid rgba(148,163,184,.06);font-size:12px">
<div><div style="font-weight:600">${sc.name}</div><div style="color:var(--text2);font-size:11px">${sc.description}</div></div>
<div style="text-align:right;min-width:70px"><div style="font-weight:800;color:var(--red)">${sc.loss_pct}%</div><div style="font-size:11px;color:var(--text2)">¥${Math.abs(sc.loss).toLocaleString()}</div></div>
</div>`).join('')}
</div>`}
// 问题清单
const issues=[...(h.issues||[]),...(c.issues||[])];
if(issues.length){
html+=`<div style="background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.15);border-radius:12px;padding:12px;margin-bottom:16px">
<div style="font-size:13px;font-weight:700;margin-bottom:8px">⚠️ 发现 ${issues.length} 个问题</div>
${issues.map(i=>`<div style="font-size:12px;padding:4px 0;border-bottom:1px solid rgba(148,163,184,.06)">${i}</div>`).join('')}
</div>`}
html+=`<div style="text-align:center;margin-top:12px"><button class="action-btn secondary" style="display:inline-block;min-width:auto;padding:10px 24px" onclick="runDoctor()">🔄 重新体检</button></div>`;
el.innerHTML=html;
}catch(e){el.innerHTML=`<div style="text-align:center;padding:20px;color:var(--text2)">体检失败: ${e.message}<br><button onclick="runDoctor()" style="margin-top:6px;padding:4px 12px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:11px">🔄 重试</button></div>`}}


// ---- 🤖 AI管家 Tab ----
async function renderSteward(el){
el.innerHTML=`<div class="dashboard-card" style="overflow:hidden">
<div class="dashboard-card-title">🤖 AI 投资管家</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:8px">管家按 Pipeline 全流程分析：Regime→模块并行→门控→EV→风控→结论</div>
<div style="display:flex;gap:8px;margin-bottom:8px">
<input id="stewardQ" placeholder="输入问题 如：茅台能买吗？" class="input-field" style="flex:1;min-width:0;padding:10px 12px;border-radius:10px;border:1px solid var(--bg3);background:var(--bg2);color:var(--text);font-size:14px">
<select id="stewardPipe" style="padding:10px;border-radius:10px;border:1px solid var(--bg3);background:var(--bg2);color:var(--text);font-size:12px;flex-shrink:0">
<option value="">自动选管线</option><option value="default">日常(default)</option><option value="fast">快速(fast)</option><option value="cautious">谨慎(cautious)</option></select>
</div>
<div style="display:flex;gap:8px;margin-bottom:16px">
<button onclick="runStewardAsk()" style="flex:2;padding:12px;border-radius:10px;border:none;background:var(--accent);color:#fff;font-weight:700;cursor:pointer;font-size:14px">🤖 管家分析</button>
<button onclick="runStewardBriefing()" style="flex:1;padding:12px;border-radius:10px;border:none;background:var(--card);border:1px solid var(--border);color:var(--text);font-weight:600;cursor:pointer;font-size:12px">📋 简报</button>
</div>
<div id="stewardResult"></div>
<div style="margin-top:16px;padding-top:16px;border-top:1px solid rgba(148,163,184,.1)">
<div style="font-size:13px;font-weight:700;margin-bottom:8px">📊 当前市场状态 (Regime)</div>
<div id="regimeResult"><div style="text-align:center;padding:12px;color:var(--text2);font-size:12px">点击上方按钮获取...</div></div>
</div></div>`;
loadRegime()}

async function loadRegime(){
const el=document.getElementById('regimeResult');if(!el||!API_AVAILABLE)return;
try{const r=await fetch(API_BASE+'/regime',{signal:AbortSignal.timeout(15000)});if(!r.ok)return;const d=await r.json();
const iconMap={'trending_bull':'📈','oscillating':'📊','high_vol_bear':'📉','rotation':'🔄'};
const colorMap={'trending_bull':'var(--green)','oscillating':'var(--accent)','high_vol_bear':'var(--red)','rotation':'#8B5CF6'};
el.innerHTML=`<div style="display:flex;align-items:center;gap:12px;padding:12px;background:var(--bg2);border-radius:12px;border-left:3px solid ${colorMap[d.regime]||'var(--accent)'}">
<div style="font-size:32px">${iconMap[d.regime]||'📊'}</div>
<div><div style="font-size:16px;font-weight:800;color:${colorMap[d.regime]||'var(--text)'}">${d.description||d.regime}</div>
<div style="font-size:12px;color:var(--text2);margin-top:4px">置信度 ${d.confidence}% · 管线→${d.regime==='high_vol_bear'?'cautious':d.regime==='rotation'?'fast':'default'}</div></div></div>`
}catch(e){el.innerHTML=`<div style="font-size:12px;color:var(--text2)">Regime 加载失败</div>`}}

async function runStewardAsk(){
const question=document.getElementById('stewardQ')?.value?.trim()||'综合分析';
const pipeline=document.getElementById('stewardPipe')?.value||null;
const el=document.getElementById('stewardResult');if(!el)return;
el.innerHTML='<div style="text-align:center;padding:30px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>管家正在跑 Pipeline 全流程分析...<br><span style="font-size:11px;opacity:0.6">Regime→模块并行→门控→EV→风控→结论</span></div>';
try{const uid=getProfileId()||getUserId();
const body={userId:uid,question};if(pipeline)body.pipeline=pipeline;
const r=await fetch(API_BASE+'/steward/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(60000)});
const d=await r.json();
const dirColor=d.direction==='bullish'?'var(--green)':d.direction==='bearish'?'var(--red)':d.direction==='blocked'?'#EF4444':'var(--accent)';
const dirIcon=d.direction==='bullish'?'📈':d.direction==='bearish'?'📉':d.direction==='blocked'?'🚫':'📊';
let html=`<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:16px">
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center"><div style="font-size:11px;color:var(--text2)">方向</div><div style="font-size:22px;font-weight:900;color:${dirColor}">${dirIcon} ${d.direction||'neutral'}</div></div>
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center"><div style="font-size:11px;color:var(--text2)">置信度</div><div style="font-size:22px;font-weight:900">${d.confidence||0}%</div></div>
<div style="background:var(--bg2);border-radius:12px;padding:12px;text-align:center"><div style="font-size:11px;color:var(--text2)">管线</div><div style="font-size:14px;font-weight:700">${d.pipeline||'?'}</div></div></div>`;
if(d.conclusion)html+=`<div style="padding:12px;background:rgba(99,102,241,.06);border-radius:10px;border-left:3px solid ${dirColor};margin-bottom:12px;font-size:13px;line-height:1.8">${d.conclusion}</div>`;
if(d.regime_description)html+=`<div style="font-size:12px;color:var(--text2);margin-bottom:8px">📊 Regime: ${d.regime_description}</div>`;
if(d.gate_decision)html+=`<div style="font-size:12px;color:var(--text2);margin-bottom:8px">🚦 门控: ${d.gate_decision} (${d.gate_reason||''})</div>`;
if(d.ev_params)html+=`<div style="font-size:12px;color:var(--text2);margin-bottom:8px">📐 EV: ${d.ev_params.ev_pct}% (胜率${d.ev_params.winrate}% 盈${d.ev_params.expected_gain}% 亏${d.ev_params.expected_loss}%)</div>`;
if(d.risk_level&&d.risk_level!=='normal')html+=`<div style="font-size:12px;padding:8px;background:rgba(239,68,68,.08);border-radius:8px;margin-bottom:8px;color:var(--red)">🛡️ 风控: ${d.risk_level} ${(d.risk_alerts||[]).map(a=>a.msg).join(' · ')}</div>`;
if(d.modules_called?.length)html+=`<div style="font-size:11px;color:var(--text2);margin-bottom:8px">📦 模块: ${d.modules_called.join(', ')} (${d.modules_called.length}个)</div>`;
html+=`<div style="font-size:11px;color:var(--text3);text-align:center;margin-top:8px">Pipeline ${d.pipeline_steps?.length||0}步 · ${d.elapsed||0}s · LLM调用 ${d.llm_calls||0}次 · ${d.timestamp||''}</div>`;
el.innerHTML=html;loadRegime();
}catch(e){el.innerHTML=`<div style="color:var(--text2);text-align:center;padding:16px">分析失败: ${e.message}<br><button onclick="runStewardAsk()" style="margin-top:6px;padding:4px 12px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:11px">🔄 重试</button></div>`}}

async function runStewardBriefing(){
const el=document.getElementById('stewardResult');if(!el)return;
el.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>获取每日简报（快速版，0次LLM）...</div>';
try{const uid=getProfileId()||getUserId();
const r=await fetch(API_BASE+'/steward/briefing?userId='+encodeURIComponent(uid),{signal:AbortSignal.timeout(30000)});
const d=await r.json();
const iconMap={'trending_bull':'📈','oscillating':'📊','high_vol_bear':'📉','rotation':'🔄'};
let html=`<div style="padding:16px;background:var(--bg2);border-radius:12px;margin-bottom:12px">
<div style="font-size:18px;font-weight:800;margin-bottom:8px">${d.one_line||'每日简报'}</div>
<div style="display:flex;gap:12px;font-size:12px;color:var(--text2)">
<span>${iconMap[d.regime]||'📊'} ${d.regime_description||d.regime}</span>
<span>🛡️ ${d.risk_level}</span>
<span>📡 ${d.signals_count||0}条信号</span>
</div>
${d.top_signal?`<div style="margin-top:8px;font-size:13px;padding:8px;background:rgba(245,158,11,.06);border-radius:8px">💡 ${d.top_signal}</div>`:''}
<div style="font-size:11px;color:var(--text3);margin-top:8px">${d.elapsed||0}s · 0次LLM</div></div>`;
el.innerHTML=html;loadRegime();
}catch(e){el.innerHTML=`<div style="color:var(--text2);text-align:center;padding:12px">简报获取失败: ${e.message}</div>`}}

// ---- 📋 周报 Tab ----
async function renderWeeklyReport(el){
el.innerHTML=`<div class="dashboard-card"><div class="dashboard-card-title">📋 投资周报</div>
<div style="font-size:12px;color:var(--text2);margin-bottom:12px">汇总一周的判断记录+持仓变化+市场回顾</div>
<button onclick="loadWeeklyReport(0)" style="padding:10px 20px;border-radius:10px;border:none;background:var(--accent);color:#fff;font-weight:700;cursor:pointer;font-size:13px;margin-bottom:16px">📋 生成本周报告</button>
<div id="weeklyResult"></div>
<div style="margin-top:16px;padding-top:16px;border-top:1px solid rgba(148,163,184,.1)">
<div style="font-size:13px;font-weight:700;margin-bottom:8px">📚 历史周报</div>
<div id="weeklyHistory"><div style="text-align:center;padding:12px;color:var(--text2);font-size:12px">点击上方按钮生成...</div></div>
</div></div>`;
loadWeeklyHistory()}

async function loadWeeklyReport(weeksAgo){
const el=document.getElementById('weeklyResult');if(!el)return;
el.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>生成周报中...</div>';
try{const r=await fetch(API_BASE+`/weekly-report?userId=${getProfileId()}&weeks_ago=${weeksAgo}`,{signal:AbortSignal.timeout(15000)});
const d=await r.json();if(d.error){el.innerHTML=`<div style="color:var(--red);padding:12px">${d.error}</div>`;return}
const j=d.judgments||{};const p=d.portfolio_changes||{};const m=d.market_review||{};const recs=d.recommendations||[];
let html=`<div style="font-size:14px;font-weight:700;margin-bottom:12px">📊 ${d.period}</div>
<div style="font-size:13px;color:var(--text1);margin-bottom:12px;padding:8px 12px;background:rgba(99,102,241,.06);border-radius:10px">${d.summary}</div>
<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:16px">
<div style="background:var(--bg2);border-radius:10px;padding:10px;text-align:center"><div style="font-size:11px;color:var(--text2)">分析次数</div><div style="font-size:20px;font-weight:800">${j.total_judgments||0}</div></div>
<div style="background:var(--bg2);border-radius:10px;padding:10px;text-align:center"><div style="font-size:11px;color:var(--text2)">准确率</div><div style="font-size:20px;font-weight:800;color:${(j.accuracy||0)>=60?'var(--green)':'var(--red)'}">${j.accuracy||0}%</div></div>
<div style="background:var(--bg2);border-radius:10px;padding:10px;text-align:center"><div style="font-size:11px;color:var(--text2)">交易笔数</div><div style="font-size:20px;font-weight:800">${p.total_transactions||0}</div></div></div>`;
if(m.regime)html+=`<div style="font-size:12px;color:var(--text2);margin-bottom:12px">📊 市场状态: <b>${m.regime_description||m.regime}</b> (${m.confidence||0}%)</div>`;
if(recs.length)html+=`<div style="padding:10px;background:rgba(59,130,246,.06);border-radius:10px;margin-bottom:12px">${recs.map(r2=>`<div style="font-size:12px;line-height:1.8">${r2}</div>`).join('')}</div>`;
el.innerHTML=html;
}catch(e){el.innerHTML=`<div style="color:var(--text2);padding:12px">生成失败: ${e.message}</div>`}}

async function loadWeeklyHistory(){
const el=document.getElementById('weeklyHistory');if(!el||!API_AVAILABLE)return;
try{const r=await fetch(API_BASE+`/weekly-report/history?userId=${getProfileId()}&limit=4`,{signal:AbortSignal.timeout(10000)});
const d=await r.json();const reports=d.reports||[];
if(!reports.length){el.innerHTML='<div style="text-align:center;padding:12px;font-size:12px;color:var(--text2)">暂无历史周报</div>';return}
el.innerHTML=reports.map(r2=>`<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--bg3);font-size:12px"><span>${r2.period}</span><span style="color:var(--text2)">${r2.summary||''}</span></div>`).join('');
}catch(e){el.innerHTML='<div style="font-size:12px;color:var(--text2)">加载失败</div>'}}

// ---- B3修复: 聊天记录持久化 ----
function _saveChatHistory(){try{localStorage.setItem(_uk('moneybag_chat_history'),JSON.stringify(chatMessages.slice(-50)))}catch{}}
function _loadChatHistory(){try{const s=localStorage.getItem(_uk('moneybag_chat_history'));if(s)chatMessages=JSON.parse(s)}catch{}}


// ---- 启动 ----
migrateV3toV4();
(async()=>{
  await checkAPI();
  await ensureProfile(); // 首次使用输入名字
  // Phase 0: 从后端同步用户偏好（模式、推送、盯盘阈值）
  try{
    const prefR=await fetch(`${API_BASE}/user/preference?userId=${encodeURIComponent(getProfileId())}`,{signal:AbortSignal.timeout(5000)});
    if(prefR.ok){const prefs=await prefR.json();if(prefs.display_mode&&!localStorage.getItem('moneybag_ui_mode_set_by_user')){_uiMode=prefs.display_mode;localStorage.setItem('moneybag_ui_mode',_uiMode)}}
  }catch(e){/* 降级：用 localStorage */}
  _loadChatHistory(); // B3: 恢复聊天记录
  fetchNav();syncFromCloud();loadModelList();
  // 老用户跳过问卷：有 profile 且后端有数据 → 直接进首页
  const pid=getProfileId();
  if(pid && pid!=='default'){
    // 检查本地是否有数据
    const p=loadPortfolio();const txns=loadTxns();const assets=loadAssets();
    const hasLocal=p.holdings.length>0||txns.length>0||assets.length>0||localStorage.getItem(_uk('moneybag_has_holdings'))==='1';
    if(!hasLocal && API_AVAILABLE){
      // 本地没有 → 检查服务器端持仓
      try{
        const [sh,fh]=await Promise.all([
          fetch(API_BASE+'/stock-holdings?userId='+pid,{signal:AbortSignal.timeout(5000)}).then(r=>r.json()).catch(()=>({holdings:[]})),
          fetch(API_BASE+'/fund-holdings?userId='+pid,{signal:AbortSignal.timeout(5000)}).then(r=>r.json()).catch(()=>({holdings:[]})),
        ]);
        if((sh.holdings||[]).length>0||(fh.holdings||[]).length>0){
          localStorage.setItem(_uk('moneybag_has_holdings'),'1'); // 标记老用户
        }
      }catch(e){console.warn('holdings check:',e)}
    }
  }
  renderLanding();
})();
