// ============================================================
// 钱袋子 — AI 智能资产配置助手 V4.0
// 交易流水制 + 全资产管理 + 净资产仪表盘
// 实时盈亏 + AI 对话 + 云端持久化 + OCR 记账
// + 市场资讯 + 技术指标 + 宏观日历 + 收入源管理
// ============================================================

// ============================================================
// 【自毁升级】2026-04-18: 清理所有"厉害了哥"等旧账号残留
// 只要localStorage里有这些旧名字，全部清掉并重新用企微ID登录
// ============================================================
(function _cleanLegacyIds() {
  try {
    // 只清旧账号残留，不依赖版本号，避免每次升级都清缓存扰动用户
    const BLACKLIST_NAMES = ['厉害了哥', '部落格里', 'test_a', 'test_v', 'test_user'];
    let needClean = false;

    try {
      const cur = localStorage.getItem('moneybag_current_profile');
      if (cur) {
        const p = JSON.parse(cur);
        if (p && BLACKLIST_NAMES.includes(p.name)) {
          console.warn('[清理] 检测到旧账号名:', p.name);
          needClean = true;
        }
      }
      const profiles = localStorage.getItem('moneybag_profiles');
      if (profiles) {
        const arr = JSON.parse(profiles);
        if (Array.isArray(arr) && arr.some(p => BLACKLIST_NAMES.includes(p.name))) {
          needClean = true;
        }
      }
    } catch (e) {}

    // 只有检测到黑名单名字才动手，其余情况什么都不做
    if (needClean) {
      // 保留企微 context（下次自动用企微ID登录）
      ['moneybag_current_profile', 'moneybag_profiles',
       'moneybag_market_cache', 'moneybag_ai_cache'].forEach(k => {
        localStorage.removeItem(k);
      });
      console.log('[清理] 旧账号数据已清理，将重新登录');

      if ('caches' in window) {
        caches.keys().then(keys => {
          keys.forEach(k => caches.delete(k));
          setTimeout(() => location.reload(), 500);
        });
      }
    }
  } catch (e) {
    console.error('[清理] 失败:', e);
  }
})();

// ---- API 配置 ----
// 优先相对路径（同源请求，无 Mixed Content 问题）
// 用户直接访问腾讯云 http://150.158.47.189:8000 或 Railway https://...railway.app 都走 /api
const API_BASE = (() => {
  const h = location.hostname;
  if (h === 'localhost' || h === '127.0.0.1' || h.startsWith('192.168.')) return 'http://localhost:8000/api';
  return '/api'; // 同源，自动匹配当前协议和域名
})();

// ============================================================
// E2 v9.5.47: 统一 API 请求封装 — apiFetch(path, opts)
// 替代各处散落的 fetch + AbortSignal.timeout + catch
// 用法：
//   const d = await apiFetch('/fund/detail/110019')          // GET
//   const d = await apiFetch('/chat', {method:'POST',json:{msg}}) // POST JSON
//   const d = await apiFetch('/url', {timeout:5000})          // 自定义超时
// 返回：已解析的 JSON 对象，失败时返回 null（不抛异常）
// ============================================================
async function apiFetch(path, opts = {}) {
  if (!API_AVAILABLE && !opts.ignoreOffline) return null;
  const {
    method = 'GET', json = null, timeout = 15000,
    cache = null, cacheKey = null, cacheTTL = 300,
  } = opts;
  // 缓存命中（短期内存缓存）
  const ck = cacheKey || (cache ? path : null);
  if (ck) { const hit = getCached(ck); if (hit !== null) return hit; }
  // v9.5.123: 附加鉴权 token
  const authToken = localStorage.getItem('moneybag_auth_token') || '';
  const headers = {};
  if (json) headers['Content-Type'] = 'application/json';
  if (authToken) headers['Authorization'] = 'Bearer ' + authToken;
  const fetchOpts = {
    method,
    signal: AbortSignal.timeout(timeout),
    headers,
    ...(json ? {body: JSON.stringify(json)} : {}),
  };
  try {
    const r = await fetch(API_BASE + path, fetchOpts);
    // v9.5.123: 401 时跳转登录
    if (r.status === 401) {
      if (!path.includes('/auth/')) { _showLoginDialog(); }
      return null;
    }
    if (!r.ok) return null;
    const d = await r.json();
    if (ck) setCached(ck, d, cacheTTL);
    return d;
  } catch (e) {
    if (!(e instanceof DOMException && e.name === 'AbortError')) {
      console.warn(`[apiFetch] ${method} ${path} failed:`, e.message || e);
    }
    return null;
  }
}

// ============================================================
// V7.5 全局翻译对象（修复数据展示问题）
// ============================================================
const ANALYSIS_TRANSLATE = {
    direction: {
        'bullish': '看多',
        'bearish': '看空',
        'neutral': '中性',
        'unknown': '未知',
        'auto': '自动分析'
    },
    type: {
        'full': '每日复盘',
        'stock': '个股分析',
        'fund': '基金分析',
        'scenario': '情景分析'
    }
};

function translateDirection(dir) {
    return ANALYSIS_TRANSLATE.direction[dir] || dir;
}

function translateAnalysisType(type_str) {
    return ANALYSIS_TRANSLATE.type[type_str] || type_str;
}

// 全局 API 路径工具 — 防止 /api/api/ 双前缀
function api(path) {
  if (!path.startsWith('/')) path = '/' + path;
  if (path.startsWith('/api/') && API_BASE.endsWith('/api')) path = path.slice(4);
  return API_BASE + path;
}
window.api = api;

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
  _uiMode=_uiMode==='simple'?'pro':'simple';
  localStorage.setItem('moneybag_ui_mode',_uiMode);
  localStorage.setItem('moneybag_ui_mode_set_by_user','1');
  fetch(API_BASE+'/user/preference?userId='+encodeURIComponent(getProfileId()),{
    method:'PUT',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({display_mode:_uiMode})
  }).catch(function(){});
  location.reload()
}
function isProMode(){return _uiMode==='pro'}

// Phase 5: 亮/暗/系统 主题切换
var _THEME_CYCLE=['system','dark','light'];
var _currentTheme=localStorage.getItem('moneybag_theme')||'dark';
function applyTheme(theme){
  _currentTheme=theme;
  localStorage.setItem('moneybag_theme',theme);
  if(theme==='light'){document.documentElement.setAttribute('data-theme','light')}
  else if(theme==='dark'){document.documentElement.setAttribute('data-theme','dark')}
  else{document.documentElement.removeAttribute('data-theme')}
  var btn=document.getElementById('themeBtn');if(btn)btn.textContent=getThemeIcon();
}
function getThemeIcon(){return _currentTheme==='light'?'☀️':_currentTheme==='dark'?'🌙':'🖥️'}
function cycleTheme(){
  var idx=(_THEME_CYCLE.indexOf(_currentTheme)+1)%_THEME_CYCLE.length;
  applyTheme(_THEME_CYCLE[idx]);
}
applyTheme(_currentTheme);

// Phase 5: 通用三态渲染
function renderCard(title,state,content){
  content=content||'';
  if(state==='loading')return '<div class="dashboard-card"><div class="dashboard-card-title">'+title+'</div><div style="padding:16px;text-align:center;color:var(--text2)"><div class="loading-spinner" style="width:20px;height:20px;margin:0 auto 8px;border-width:2px"></div>加载中...</div></div>';
  if(state==='error')return '<div class="dashboard-card" style="border-left:3px solid var(--red)"><div class="dashboard-card-title">'+title+'</div><div style="padding:12px;text-align:center;color:var(--text2)">加载失败</div></div>';
  if(state==='empty')return '<div class="dashboard-card"><div class="dashboard-card-title">'+title+'</div><div style="padding:12px;text-align:center;color:var(--text2);font-size:12px">暂无数据</div></div>';
  return '<div class="dashboard-card"><div class="dashboard-card-title">'+title+'</div>'+content+'</div>';
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
// 2026-04-19 V7.7: 后端 id 已统一为 name（废弃 u_xxx），此函数本应直接返 _profileId
// 但保留下面的 fallback 链：老 localStorage 可能还存 u_xxx，此时用 _profileName 兜底
// （后端数据目录按 name 组织，如 data/LeiJiang/memory/）
function getProfileId(){
  const wx = localStorage.getItem('moneybag_wxwork_uid');
  if (wx) return wx;
  // _profileName 存在且看起来是用户名（非空、不是 default），优先用它
  if (_profileName && _profileName !== 'default') return _profileName;
  return _profileId || 'default';
}
function getProfileParam(){ return `userId=${encodeURIComponent(getProfileId())}` }

async function ensureProfile(){
  // v9.5.119: 检查所有身份来源（profileId / profileName / wxwork_uid）
  if(_profileId || _profileName || localStorage.getItem('moneybag_wxwork_uid')) return; // 已有身份
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
      // v9.5.123: 注册/登录后获取 auth token
      try{
        const ar=await fetch(`${API_BASE}/auth/login`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:_profileName,password:code})});
        const ad=await ar.json();
        if(ad.token)localStorage.setItem('moneybag_auth_token',ad.token);
      }catch(e){console.warn('[AUTH] token获取失败:',e)}
      if(window._profileOverlay)window._profileOverlay.remove();
      if(window._profileResolve)window._profileResolve();
    }else{
      if(errEl){errEl.style.display='block';errEl.textContent=d.detail||'注册失败，请检查邀请码'}
    }
  }catch(e){
    if(errEl){errEl.style.display='block';errEl.textContent='网络错误，请重试'}
  }
}
// v9.5.123: 登录弹窗（401时触发）
function _showLoginDialog(){
  if(document.querySelector('.auth-login-overlay'))return; // 防重复弹
  const o=document.createElement('div');
  o.className='auth-login-overlay';
  o.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:99999;display:flex;align-items:center;justify-content:center';
  o.innerHTML=`<div style="background:var(--bg2,#1e293b);border-radius:16px;padding:28px;max-width:300px;width:90%;text-align:center">
    <div style="font-size:36px;margin-bottom:12px">🔐</div>
    <h3 style="color:var(--text,#f1f5f9);margin:0 0 8px">需要重新登录</h3>
    <p style="color:var(--text2,#94a3b8);font-size:13px;margin:0 0 16px">请输入密码验证身份</p>
    <input id="authPwdIn" type="password" placeholder="密码（即邀请码）"
      style="width:100%;box-sizing:border-box;padding:10px;border-radius:8px;border:1px solid var(--bg3,#334155);background:var(--bg,#0f172a);color:var(--text,#f1f5f9);font-size:14px;text-align:center;margin-bottom:12px">
    <div id="authLoginErr" style="color:#EF4444;font-size:12px;margin-bottom:8px;display:none"></div>
    <button onclick="_doReLogin()" style="width:100%;padding:10px;border-radius:8px;border:none;background:var(--accent,#F59E0B);color:#000;font-size:14px;font-weight:700;cursor:pointer">登录</button>
  </div>`;
  document.body.appendChild(o);
  setTimeout(()=>{const inp=document.getElementById('authPwdIn');if(inp)inp.focus()},100);
}
async function _doReLogin(){
  const pwd=(document.getElementById('authPwdIn')||{}).value||'';
  const errEl=document.getElementById('authLoginErr');
  if(!pwd){if(errEl){errEl.style.display='block';errEl.textContent='请输入密码'}return}
  try{
    const uid=getProfileId();
    const r=await fetch(`${API_BASE}/auth/login`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({userId:uid,password:pwd})});
    const d=await r.json();
    if(r.ok&&d.token){
      localStorage.setItem('moneybag_auth_token',d.token);
      document.querySelector('.auth-login-overlay')?.remove();
      location.reload(); // 刷新重试
    }else{
      if(errEl){errEl.style.display='block';errEl.textContent=d.detail||'密码错误'}
    }
  }catch(e){if(errEl){errEl.style.display='block';errEl.textContent='网络错误'}}
}

const EXPENSE_ICONS={'餐饮':'🍜','交通':'🚗','购物':'🛍️','娱乐':'🎮','医疗':'🏥','教育':'📚','房租':'🏠','日用':'🧴','通讯':'📱','其他':'📌'};
const INCOME_ICONS={'工资':'💰','兼职':'🔧','民宿':'🏡','外包':'💻','理财收益':'📈','红包':'🧧','退款':'↩️','出租房':'🏘️','其他收入':'💵'};
const SOURCE_TYPE_ICONS={'民宿':'🏡','出租房':'🏘️','外包':'💻','兼职':'🔧','工资':'💰','理财收益':'📈','电商':'🛒','自媒体':'📱','其他':'💵'};
const LEDGER_ICONS={...EXPENSE_ICONS,...INCOME_ICONS};
let ledgerDirection='expense'; // 当前记账方向

// ---- V4 交易流水 + 资产管理数据层 ----
function loadTxns(){try{return JSON.parse(localStorage.getItem(TXN_KEY))||[]}catch{return[]}}
function saveTxns(d){
  localStorage.setItem(TXN_KEY,JSON.stringify(d));
  // v9.5.13: 持仓变更后自动同步到后端（debounced 1s，避免连续写时多次推送）
  try{ _scheduleHoldingsSync(); }catch(e){console.warn('sync schedule fail:',e)}
}
function loadAssets(){try{return JSON.parse(localStorage.getItem(ASSETS_KEY))||[]}catch{return[]}}
function saveAssets(d){localStorage.setItem(ASSETS_KEY,JSON.stringify(d))}

// ---- v9.5.13: 前端持仓 → 后端覆盖式同步 ----
// 修复历史欠账：之前持仓只在浏览器 localStorage，导致后端晨报/复盘/定投建议拿不到数据
let _holdingsSyncTimer=null;
let _holdingsSyncLast=0;
function _scheduleHoldingsSync(){
  if(_holdingsSyncTimer)clearTimeout(_holdingsSyncTimer);
  _holdingsSyncTimer=setTimeout(()=>{ syncHoldingsToBackend().catch(e=>console.warn('sync fail:',e)); }, 1000);
}
async function syncHoldingsToBackend(force){
  if(!API_AVAILABLE)return;
  const uid=getProfileId(); if(!uid||uid==='default')return;
  // 节流：非 force 模式下，同一用户 30 秒内只推一次
  const now=Date.now();
  if(!force && (now-_holdingsSyncLast)<30000) return;
  _holdingsSyncLast=now;

  try{
    const txns=loadTxns();
    const all=calcHoldingsFromTxns(txns);
    // 判断股票 vs 基金（v9.5.119: 名称优先，修复基金被误判为股票）
    const isStockFn=h=>{
      if(h.assetType==='stock')return true;
      if(h.assetType==='fund')return false;
      const fkw=['基金','债券','货币','指数','ETF','QDII','混合','商品','联接','LOF'];
      const nm=(h.name||'')+(h.category||'');
      if(fkw.some(k=>nm.includes(k)))return false;
      const c=(h.code||'').replace(/^(sh|sz)/i,'');
      if(/^(00|30|60)\d{4}$/.test(c) && !(typeof FUND_DETAILS!=='undefined' && FUND_DETAILS && FUND_DETAILS[c])) return true;
      return false;
    };
    const stocks=[]; const funds=[];
    for(const h of all){
      if(!h.shares||h.shares<=0||!h.avgPrice||h.avgPrice<=0)continue;
      const code=(h.code||'').replace(/^(sh|sz)/i,'');
      if(!/^\d{6}$/.test(code))continue; // 跳过非 6 位数字（如"余额宝"）
      if(isStockFn(h)){
        stocks.push({code, name:h.name||code, costPrice:h.avgPrice, shares:Math.round(h.shares), note:''});
      }else{
        funds.push({code, name:h.name||code, costNav:h.avgPrice, shares:h.shares, note:''});
      }
    }
    const headers={'Content-Type':'application/json'};
    const tasks=[
      fetch(API_BASE+'/stock-holdings/sync',{method:'POST',headers,body:JSON.stringify({userId:uid,holdings:stocks}),signal:AbortSignal.timeout(8000)}).then(r=>r.json()).catch(e=>({err:String(e)})),
      fetch(API_BASE+'/fund-holdings/sync', {method:'POST',headers,body:JSON.stringify({userId:uid,holdings:funds}),signal:AbortSignal.timeout(8000)}).then(r=>r.json()).catch(e=>({err:String(e)})),
    ];
    const [sr,fr]=await Promise.all(tasks);
    console.log(`[SYNC] ${uid}: stocks ${sr?.before||0}→${sr?.after||0}, funds ${fr?.before||0}→${fr?.after||0}`);
  }catch(e){console.warn('syncHoldingsToBackend:',e)}
}
window.syncHoldingsToBackend=syncHoldingsToBackend;

// 从交易流水计算持仓（加权平均成本法）
function calcHoldingsFromTxns(txns){
  const map={};
  txns.filter(t=>t.type==='BUY'||t.type==='SELL').sort((a,b)=>new Date(a.date)-new Date(b.date)).forEach(t=>{
    if(!map[t.code])map[t.code]={code:t.code,name:t.name||t.code,shares:0,totalCost:0,avgPrice:0,category:t.category||'',assetType:t.assetType||''};
    const h=map[t.code];
    // v9.5.109: OCR 上传的字段名是 nav，手动添加是 price，amount 兜底
    const unitPrice = Number(t.price || t.nav || 0);
    const shares = Number(t.shares || 0);
    const amount = Number(t.amount || (shares * unitPrice));
    if(t.type==='BUY'){
      // 优先用 amount（最准），其次 shares*price
      h.totalCost += amount > 0 ? amount : shares*unitPrice;
      h.shares += shares;
      h.avgPrice = h.shares>0 ? h.totalCost/h.shares : 0;
      if(t.name)h.name=t.name;if(t.category)h.category=t.category;if(t.assetType)h.assetType=t.assetType;
    }else if(t.type==='SELL'){
      const sellShares=Math.min(shares, h.shares);
      h.totalCost -= sellShares*h.avgPrice;
      h.shares -= sellShares;
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
  // 区分股票/基金金额
  const _isStock=h=>{
    if(h.assetType==='stock')return true;if(h.assetType==='fund')return false;
    const fkw=['基金','债券','货币','指数','ETF','QDII','混合','商品','联接'];
    if(h.category&&fkw.some(k=>h.category.includes(k)))return false;
    const c=(h.code||'').replace(/^(sh|sz)/i,'');
    if(/^(00|30|60)\d{4}$/.test(c)&&!FUND_DETAILS[c])return true;
    return false;
  };
  const stockValue=holdings.filter(h=>_isStock(h)).reduce((s,h)=>s+h.totalCost,0);
  const fundOnlyValue=fundValue-stockValue;
  const assetTotal=assets.filter(a=>a.type!=='liability').reduce((s,a)=>s+(a.value||0),0);
  const liabilities=assets.filter(a=>a.type==='liability').reduce((s,a)=>s+(a.value||0),0);
  const ledgerIncome=ledger.filter(e=>e.direction==='income').reduce((s,e)=>s+(e.amount||0),0);
  const ledgerExpense=ledger.filter(e=>e.direction!=='income').reduce((s,e)=>s+(e.amount||0),0);
  return {fundValue,stockValue,fundOnlyValue,assetTotal,liabilities,ledgerIncome,ledgerExpense,ledgerNet:ledgerIncome-ledgerExpense,netWorth:fundValue+assetTotal-liabilities+ledgerIncome-ledgerExpense,holdings};
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
// v9.5.112: 云端优先 — 之前只在本地为空时才同步，导致后端清理脏数据后用户前端还看老的
//          现在每次登录都用云端 transactions/assets 覆盖本地
async function syncFromCloud(){
  if(!API_AVAILABLE)return;
  try{
    // v9.5.119: 用轻量接口只拉 portfolio（避免拉 4MB+ 全量用户数据导致手机超时/OOM）
    const r=await fetch(API_BASE+'/user/'+getUserId()+'/portfolio',{signal:AbortSignal.timeout(10000)});
    if(!r.ok)return;
    const d=await r.json();
    if(d.portfolio){
      const l=loadPortfolio();
      // 云端 transactions/assets 是 source of truth，总是覆盖
      const cloudTxns = d.portfolio.transactions || [];
      const cloudAssets = d.portfolio.assets || [];
      l.transactions = cloudTxns;
      l.assets = cloudAssets;
      // holdings/history/profile 保留本地（这些是 V3 客户端计算的）
      savePortfolio(l);
      // v9.5.119: 同步写入独立的 TXN_KEY 和 ASSETS_KEY（持仓页用这两个 key 读数据）
      if(cloudTxns.length) saveTxns(cloudTxns);
      if(cloudAssets.length) saveAssets(cloudAssets);
    }
  }catch(e){console.warn('[syncFromCloud] failed:',e.message)}
}

// ---- 工具 ----
function $(s){return document.querySelector(s)}
function fmtMoney(n){if(Math.abs(n)>=10000)return(n/10000).toFixed(1)+'万';return n.toLocaleString('zh-CN')}
function fmtFull(n){return'¥'+n.toLocaleString('zh-CN')}
function getProfile(s){return RISK_PROFILES.find(p=>s>=p.min&&s<=p.max)||RISK_PROFILES[2]}
function calcReturns(a,amt,sc){let t=0;a.forEach(x=>{t+=(amt*x.pct/100)*x.returns[sc]});return t}


// ---- 客户端数据缓存（P0 性能优化：避免重复 fetch 造成的 3-4s 等待）----
// 2026-05-11 FIX: 基于后端 TTL 配置实现的前端内存缓存
// v9.5.93: 升级为 localStorage 持久化（刷新后仍命中），并加 SWR 模式
// 缓存结构: { [cacheKey]: { data, timestamp, ttl }, ... }
const INSIGHT_CACHE = {
  dashboard: { ttl: 120000 },    // 2 分钟（数据源有多个 TTL，取保守值）
  news: { ttl: 300000 },          // 5 分钟（新闻变化快）
  policy: { ttl: 600000 },        // 10 分钟
  macro: { ttl: 900000 },         // 15 分钟
  global: { ttl: 900000 },        // 15 分钟
  fund_news: { ttl: 600000 },     // 10 分钟
  portfolio_news: { ttl: 600000 }, // 10 分钟
  policy_news: { ttl: 600000 },   // 10 分钟
  signals: { ttl: 900000 },       // 15 分钟
  pnl: { ttl: 900000 },           // 15 分钟
  nav: { ttl: 600000 },           // 10 分钟
  fund_dynamic: { ttl: 900000 },  // 15 分钟
  global_snap: { ttl: 900000 },   // 15 分钟
  global_impact: { ttl: 900000 }, // 15 分钟
  news_impact: { ttl: 900000 },   // 15 分钟
  sector: { ttl: 900000 },        // 15 分钟（行业板块）
  broker: { ttl: 900000 },        // 15 分钟（研报）
  fund_screen: { ttl: 1800000 },  // 30 分钟（选基，v9.5.93 延长）
  fund_potential: { ttl: 1800000 }, // 30 分钟（潜力榜独立缓存，v9.5.117）
  stock_screen: { ttl: 1800000 }, // 30 分钟（选股，v9.5.93 延长）
  longterm: { ttl: 1800000 },     // 30 分钟（长持，v9.5.93 新增）
};

// v9.5.93: localStorage 持久化前缀（按用户隔离）
// v9.5.104: bump v1→v2 解决 aiComment 错位
// v9.5.111: bump v2→v3 — 之前缓存的 fund_screen 不含 D 信号潜力数据（v9.5.103 才加的）
// v9.5.116: bump v3→v4 — 强制清残留 fund_screen 缓存，让选基潜力榜能正确显示 11 只
const _LS_CACHE_PREFIX = 'moneybag_cache_v4_';

// 启动时清理旧版本缓存
try {
  for (let i = localStorage.length - 1; i >= 0; i--) {
    const k = localStorage.key(i);
    if (k && (k.startsWith('moneybag_cache_v1_') || k.startsWith('moneybag_cache_v2_') || k.startsWith('moneybag_cache_v3_'))) {
      localStorage.removeItem(k);
    }
  }
} catch {}
function _lsCacheKey(key) {
  const uid = (typeof getProfileId === 'function' ? getProfileId() : '') || 'anon';
  return _LS_CACHE_PREFIX + uid + '_' + key;
}

function getCached(key) {
  let cfg = INSIGHT_CACHE[key];
  if (!cfg) {
    // v9.5.119: 递归去掉后缀查找基础 key（fund_screen_all_score → fund_screen_all → fund_screen）
    let base = key;
    let baseCfg = null;
    while (base.includes('_')) {
      base = base.replace(/_[^_]+$/, '');
      baseCfg = INSIGHT_CACHE[base];
      if (baseCfg) break;
    }
    if (!baseCfg) return null;
    cfg = INSIGHT_CACHE[key] = { ttl: baseCfg.ttl };
  }
  // 1) 内存命中（最快）
  if (cfg.cached) {
    const age = Date.now() - cfg.timestamp;
    if (age <= cfg.ttl) return cfg.cached;
    cfg.cached = null;
    cfg.timestamp = 0;
  }
  // 2) localStorage 命中（v9.5.93 跨刷新）
  try {
    const raw = localStorage.getItem(_lsCacheKey(key));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || !parsed.data || !parsed.timestamp) return null;
    const age = Date.now() - parsed.timestamp;
    if (age > cfg.ttl) {
      localStorage.removeItem(_lsCacheKey(key));
      return null;
    }
    // 回填内存缓存
    cfg.cached = parsed.data;
    cfg.timestamp = parsed.timestamp;
    return parsed.data;
  } catch { return null; }
}

function setCached(key, data) {
  let cfg = INSIGHT_CACHE[key];
  if (!cfg) {
    // v9.5.119: 递归去后缀匹配（同 getCached）
    let base = key;
    let baseCfg = null;
    while (base.includes('_')) {
      base = base.replace(/_[^_]+$/, '');
      baseCfg = INSIGHT_CACHE[base];
      if (baseCfg) break;
    }
    if (!baseCfg) return;
    cfg = INSIGHT_CACHE[key] = { ttl: baseCfg.ttl };
  }
  cfg.cached = data;
  cfg.timestamp = Date.now();
  // v9.5.93: 同步写 localStorage（持久化）
  try {
    localStorage.setItem(_lsCacheKey(key), JSON.stringify({ data, timestamp: cfg.timestamp }));
  } catch (e) {
    // 配额超出时清掉旧缓存重试一次
    if (e && e.name === 'QuotaExceededError') {
      try {
        for (let i = localStorage.length - 1; i >= 0; i--) {
          const k = localStorage.key(i);
          if (k && k.startsWith(_LS_CACHE_PREFIX)) localStorage.removeItem(k);
        }
        localStorage.setItem(_lsCacheKey(key), JSON.stringify({ data, timestamp: cfg.timestamp }));
      } catch {}
    }
  }
}

function clearInsightCache() {
  Object.values(INSIGHT_CACHE).forEach(cfg => {
    cfg.cached = null;
    cfg.timestamp = 0;
  });
}

// ---- API ----
// v9.5.55: 修复「后端未连接」假阴性 — 8秒超时 + 失败自动重试 + 周期性恢复检测
async function checkAPI(opts){
  const timeout = (opts&&opts.timeout) || 8000;
  try{
    const authToken = localStorage.getItem('moneybag_auth_token') || '';
    const headers = authToken ? {'Authorization': 'Bearer ' + authToken} : {};
    const r=await fetch(API_BASE+'/health',{signal:AbortSignal.timeout(timeout), headers});
    const newState = r.ok;
    if(newState !== API_AVAILABLE){
      console.log('[API] state change:', API_AVAILABLE, '→', newState);
    }
    API_AVAILABLE = newState;
    // v9.5.123: 数据源降级检测
    if(r.ok){
      try{
        const d=await r.json();
        const dh=d.data_health||{};
        if(dh.overall==='degraded'&&dh.degraded&&dh.degraded.length){
          _showDegradeBanner(dh.degraded);
        }else{
          _hideDegradeBanner();
        }
      }catch{}
    }
    return newState;
  }catch(e){
    if(API_AVAILABLE===false){} else {
      console.warn('[API] check failed:', e.message||e);
      API_AVAILABLE=false;
    }
    return false;
  }
}
// v9.5.123: 数据源降级提示横幅
function _showDegradeBanner(issues){
  let el=document.getElementById('degradeBanner');
  if(!el){
    el=document.createElement('div');el.id='degradeBanner';
    el.style.cssText='position:fixed;top:0;left:0;right:0;z-index:9990;padding:6px 16px;background:rgba(245,158,11,.95);color:#000;font-size:12px;font-weight:500;text-align:center;display:flex;align-items:center;justify-content:center;gap:6px';
    document.body.appendChild(el);
  }
  el.innerHTML=`⚠️ 数据源异常: ${issues.join(', ')} — 当前展示最近有效数据，可能不是最新 <button onclick="this.parentElement.remove()" style="margin-left:12px;padding:2px 8px;border-radius:4px;border:1px solid rgba(0,0,0,.3);background:transparent;cursor:pointer;font-size:11px">关闭</button>`;
  el.style.display='flex';
}
function _hideDegradeBanner(){const el=document.getElementById('degradeBanner');if(el)el.style.display='none'}
// 周期性恢复检测：每 60s 重试一次（如果当前离线）
setInterval(()=>{ if(!API_AVAILABLE) checkAPI({timeout:5000}); }, 60000);
// 窗口聚焦时立即重试（用户切回 App 时第一时间恢复）
window.addEventListener('focus', ()=>{ if(!API_AVAILABLE) checkAPI({timeout:5000}); });
async function fetchNav(){if(!API_AVAILABLE)return;let cached=getCached('nav');if(cached){liveNavData=cached;return}try{const r=await fetch(API_BASE+'/nav/all');if(r.ok){liveNavData=await r.json();setCached('nav',liveNavData)}}catch{}}
async function fetchSignals(){if(!API_AVAILABLE)return null;try{const p=loadPortfolio();if(!p.holdings.length)return null;const r=await fetch(API_BASE+'/signals',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});if(r.ok)return await r.json()}catch{}return null}
async function fetchPnl(){if(!API_AVAILABLE)return null;try{const p=loadPortfolio();if(!p.holdings.length)return null;const r=await fetch(API_BASE+'/portfolio/pnl',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});if(r.ok)return await r.json()}catch{}return null}
async function fetchDashboard(){if(!API_AVAILABLE)return null;let cached=getCached('dashboard');if(cached)return cached;try{const r=await fetch(API_BASE+'/dashboard',{signal:AbortSignal.timeout(30000)});if(r.ok){const d=await r.json();setCached('dashboard',d);return d}}catch(e){console.warn('Dashboard fetch failed:',e)}return null}
async function fetchFundNews(code){if(!API_AVAILABLE)return[];const ckey='fund_news_'+code;let cached=getCached(ckey);if(cached)return cached;try{const r=await fetch(API_BASE+'/news/'+code);if(r.ok){const d=await r.json();const news=d.news||[];setCached(ckey,news);return news}}catch{}return[]}
async function fetchPortfolioNews(){if(!API_AVAILABLE)return{};let cached=getCached('portfolio_news');if(cached)return cached;try{const r=await fetch(API_BASE+'/news/portfolio');if(r.ok){const d=await r.json();setCached('portfolio_news',d);return d}}catch{}return{}}
async function fetchFundDynamic(code){if(!API_AVAILABLE)return null;const ckey='fund_dynamic_'+code;let cached=getCached(ckey);if(cached)return cached;try{const r=await fetch(API_BASE+'/fund/info/'+code,{signal:AbortSignal.timeout(15000)});if(r.ok){const d=await r.json();setCached(ckey,d);return d}}catch{}return null}
async function fetchPolicyNews(){if(!API_AVAILABLE)return[];let cached=getCached('policy_news');if(cached)return cached;try{const r=await fetch(API_BASE+'/news/policy',{signal:AbortSignal.timeout(15000)});if(r.ok){const d=await r.json();const news=d.news||[];setCached('policy_news',news);return news}}catch{}return[]}
async function runDataAudit(){const btn=document.getElementById('auditBtn');if(btn)btn.textContent='🔄 检查中...';try{const r=await fetch(API_BASE+'/health/data-audit',{signal:AbortSignal.timeout(60000)});if(!r.ok)throw new Error('audit failed');const d=await r.json();const statusIcon={'ok':'✅','warn':'⚠️','error':'❌'};const statusColor={'ok':'#10B981','warn':'#F59E0B','error':'#EF4444'};const overallIcon={'healthy':'✅','degraded':'⚠️','unhealthy':'❌'};const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()"><div class="modal-handle"></div><div class="modal-title">${overallIcon[d.overall]||'🔍'} 数据健康体检报告</div><div class="modal-subtitle">${d.summary} · ${new Date(d.timestamp).toLocaleString('zh-CN')}</div><div style="padding:12px 0">${d.checks.map(c=>`<div style="display:flex;align-items:center;padding:8px 0;border-bottom:1px solid rgba(148,163,184,.1)"><span style="font-size:16px;width:28px">${statusIcon[c.status]}</span><span style="flex:1;font-size:13px;color:var(--text1)">${c.name}</span><span style="font-size:12px;color:${statusColor[c.status]};text-align:right;max-width:50%">${c.msg}</span></div>`).join('')}</div><div style="font-size:11px;color:var(--text2);padding-top:8px;border-top:1px solid rgba(148,163,184,.1)">💡 此体检自动检查：宏观数据新鲜度、估值准确性、基金净值及时性、新闻相关性、API响应速度</div></div>`;document.body.appendChild(o);if(btn)btn.textContent='🔍 数据体检'}catch(e){if(btn)btn.textContent='❌ 检查失败';setTimeout(()=>{if(btn)btn.textContent='🔍 数据体检'},3000)}}

// ---- 样式已迁移至 styles.css ----

// ---- 底部导航 ----
function renderNav(){let n=document.getElementById('btmNav');if(!n){n=document.createElement('div');n.id='btmNav';n.className='bottom-nav';document.body.appendChild(n)}
const tabs=[
  {id:'landing',icon:'🏠',label:'首页'},
  {id:'portfolio',icon:'📊',label:'持仓'},
  {id:'insight',icon:'📰',label:'资讯'},
  {id:'chat',icon:'🤖',label:'AI'},
  {id:'assets',icon:'💰',label:'资产'}
];
n.innerHTML=tabs.map(t=>`<div class="nav-item ${currentPage===t.id?'active':''}" onclick="navigateTo('${t.id}')"><div class="nav-icon">${t.icon}</div><div>${t.label}</div></div>`).join('');
// 顶部用户名条（2026-04-19 V7.7: 只在首页显示，其他页面隐藏省屏幕空间）
// v9.3.0: 首页已有自己的顶部条（PR-4），旧 profileHeader 永久隐藏
let hdr=document.getElementById('profileHeader');
const showHeader = false; // v9.3.0: 旧顶栏不再显示，首页用新模板内置的顶部条
if(!hdr){hdr=document.createElement('div');hdr.id='profileHeader';hdr.style.cssText='position:fixed;top:0;left:0;right:0;z-index:100;padding:6px 16px;font-size:12px;color:var(--text2,#94a3b8);background:var(--bg,#0f172a);display:none;justify-content:space-between;align-items:center;border-bottom:1px solid var(--bg3,#334155);transition:transform .2s ease';document.body.appendChild(hdr)}
// V7.7.4 FIX: hdr.style.display = '' 会擦掉 cssText 里的 display:flex 导致 block fallback
// 必须用 'flex' 明确指定，或者用 visibility/transform
hdr.style.display = 'none';
// V7.7: 非首页同时把 marketStatusBar 也隐藏 + 清空 paddingTop
// v9.3.0: marketStatusBar 也永久隐藏，首页有自己的非交易日横幅
const mktBar = document.getElementById('marketStatusBar');
if (mktBar) mktBar.style.display = 'none';
document.body.style.paddingTop = '0';
hdr.innerHTML=`<span onclick="showProfileSettings()" style="cursor:pointer">👋 ${_profileName||'未登录'} ⚙️</span><span style="display:flex;align-items:center;gap:8px"><button onclick="cycleTheme()" style="font-size:10px;padding:2px 8px;border-radius:4px;border:1px solid var(--bg3);background:transparent;color:var(--text2);cursor:pointer" id="themeBtn">${getThemeIcon()}</button><button onclick="toggleUIMode()" style="font-size:10px;padding:2px 8px;border-radius:4px;border:1px solid var(--bg3);background:${isProMode()?'rgba(99,102,241,.2)':'rgba(16,185,129,.2)'};color:${isProMode()?'#818CF8':'#10B981'};cursor:pointer">${isProMode()?'🔬 专业':'🌱 简洁'}</button></span>`}

function showProfileSettings(){
const pid=getProfileId();const wxId=localStorage.getItem('moneybag_wxwork_uid')||'';
const statusText=wxId?`<div style="background:rgba(16,185,129,.1);border:1px solid rgba(16,185,129,.3);border-radius:8px;padding:8px 12px;margin-bottom:12px;font-size:12px;color:#10B981">✅ 已绑定企微：<b>${wxId}</b>　盯盘信号会推送到你的微信</div>`:`<div style="background:rgba(245,158,11,.1);border:1px solid rgba(245,158,11,.3);border-radius:8px;padding:8px 12px;margin-bottom:12px;font-size:12px;color:#F59E0B">⚠️ 未绑定企微　绑定后才能收到盯盘推送</div>`;
const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()"><div class="modal-handle"></div>
<div class="modal-title" style="display:flex;justify-content:space-between;align-items:center">⚙️ 个人设置 <button onclick="clearLocalCache()" style="font-size:11px;padding:4px 10px;border-radius:6px;border:1px solid rgba(239,68,68,.3);background:transparent;color:var(--red);cursor:pointer">🗑️ 清缓存</button></div>
<div class="modal-subtitle">Profile: ${_profileName} (${pid.slice(0,8)})</div>
<div class="manual-form" style="background:transparent;padding:0;margin-top:16px">
${statusText}
<div id="llmBudgetCard" style="background:rgba(99,102,241,.08);border:1px solid rgba(99,102,241,.2);border-radius:8px;padding:10px 12px;margin-bottom:12px;font-size:12px;color:var(--text2)">💰 AI 用量加载中...</div>
<div class="form-row"><div class="form-label">企业微信账号 (用于个人推送)</div>
<input class="form-input" type="text" id="wxworkUidInput" placeholder="如 LeiJiang" value="${wxId}">
<div style="font-size:11px;color:var(--text2);margin-top:4px">填写你的企微账号（在企微通讯录→个人信息→账号）</div></div>
<button class="form-submit" id="wxSaveBtn" onclick="saveProfileSettings()">💾 保存</button>
</div></div>`;document.body.appendChild(o);
// V7.7.5: 异步加载 LLM 用量到 Profile 设置
loadLLMBudgetCard();}

async function loadLLMBudgetCard(){
  const el=document.getElementById('llmBudgetCard');if(!el)return;
  try{
    const r=await fetch(API_BASE+'/health',{signal:AbortSignal.timeout(3000)});
    if(!r.ok){el.innerHTML='💰 AI 用量：加载失败';return}
    const d=await r.json();const u=d.llm_usage||{};
    const cost=u.today_cost_rmb||0;const budget=u.daily_budget_rmb||3;
    const pct=u.usage_pct||0;const calls=u.today_calls||0;
    const emoji=u.status==='critical'?'🔴':u.status==='warning'?'🟡':'🟢';
    const color=u.status==='critical'?'var(--red)':u.status==='warning'?'#F59E0B':'#10B981';
    el.innerHTML=`<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px"><span>${emoji} 今日 AI 用量</span><span style="color:${color};font-weight:700">¥${cost.toFixed(3)} / ¥${budget}</span></div><div style="font-size:11px;color:var(--text2)">已调用 ${calls} 次 · 用量 ${pct}%</div>`;
  }catch(e){el.innerHTML='💰 AI 用量：网络失败'}
}

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

function navigateTo(p, tab){
  const prev = currentPage;
  // 离开 chat 页时保存"距底部距离"（用负数表示，0=在底部）
  if(prev==='chat'){
    const el=document.getElementById('chatMsgs');
    if(el&&typeof _chatScrollPos!=='undefined'){
      const distFromBottom=el.scrollTop-el.scrollHeight+el.clientHeight;
      // distFromBottom 接近 0 = 在底部，负值 = 往上翻了
      _chatScrollPos=distFromBottom<-50?distFromBottom:0;
    }
  }
  currentPage=p;
  renderNav();
  if(p==='landing')renderLanding();
  else if(p==='portfolio')renderPortfolio();
  else if(p==='stocks')renderStocks();
  else if(p==='insight'){
    // C5 v9.5.46: 支持 tab 参数直接跳到指定 tab（如 'weekly'）
    insightTab=tab||'overview';
    renderInsight();
  }
  else if(p==='chat'){
    // chat页面：只要 DOM 还在就不重渲染
    if(document.getElementById('chatMsgs')){
      currentPage='chat';renderNav();
    }else{
      renderChat(); // renderChat 内部会用 _chatScrollPos 恢复位置
    }
  }
  else if(p==='history')renderHistory();
  else if(p==='ledger')renderLedger();
  else if(p==='assets')renderAssets();
  else if(p==='weekly-lesson')renderWeeklyLesson();
  else if(p==='chart')renderChart();
  else if(p==='market-panorama')renderMarketPanorama();
  else if(p==='todos')renderTodos();
  else if(p==='behavior-history')renderBehaviorHistory();
  else if(p==='monthly-rebalance')renderMonthlyRebalance();
  else if(p==='settings')renderSettings();
}


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
if(src){let srcText='📊 实时数据·'+info.source;if(info.returns_date&&info.returns_date.length===8){srcText+='（截至 '+info.returns_date.slice(0,4)+'-'+info.returns_date.slice(4,6)+'-'+info.returns_date.slice(6,8)+'）'}src.textContent=srcText}
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


// ---- B3修复: 聊天记录持久化 + 会话管理（v9.5.5）----
// 当前会话：moneybag_chat_history（保持兼容旧key）
// 归档：moneybag_chat_archive，按日期分组，保留30天
function _saveChatHistory(){
  try{
    localStorage.setItem(_uk('moneybag_chat_history'),JSON.stringify(chatMessages.slice(-50)));
    // 同时记录当前会话最后活跃日期，用于自动归档判断
    localStorage.setItem(_uk('moneybag_chat_lastdate'),new Date().toISOString().slice(0,10));
  }catch{}
}
function _loadChatHistory(){
  try{
    const s=localStorage.getItem(_uk('moneybag_chat_history'));
    if(s)chatMessages=JSON.parse(s);
    // 启动时检查：如果上次活跃日期不是今天，自动归档
    _autoArchiveIfNewDay();
  }catch{}
}
// 自动归档：进入新一天时，把昨天的对话归档为新会话条目
function _autoArchiveIfNewDay(){
  try{
    const lastDate=localStorage.getItem(_uk('moneybag_chat_lastdate'));
    const today=new Date().toISOString().slice(0,10);
    if(lastDate&&lastDate!==today&&chatMessages&&chatMessages.length>0){
      _archiveCurrentChat(lastDate);
      chatMessages=[]; // 开始新一天的对话
      localStorage.removeItem(_uk('moneybag_chat_history'));
    }
  }catch(e){console.warn('auto archive failed',e)}
}
function _archiveCurrentChat(dateStr){
  if(!chatMessages||chatMessages.length===0)return;
  try{
    const key=_uk('moneybag_chat_archive');
    const raw=localStorage.getItem(key);
    const archive=raw?JSON.parse(raw):[];
    // 取首条用户消息当标题
    const firstUserMsg=chatMessages.find(m=>m.role==='user');
    const title=firstUserMsg?(firstUserMsg.text||'').slice(0,30):'对话';
    archive.unshift({
      id:Date.now()+'-'+Math.random().toString(36).slice(2,8),
      date:dateStr||new Date().toISOString().slice(0,10),
      title:title,
      msgCount:chatMessages.length,
      messages:chatMessages.slice(),
      createdAt:Date.now()
    });
    // 只保留近30天
    const cutoff=Date.now()-30*24*3600*1000;
    const trimmed=archive.filter(a=>a.createdAt>cutoff).slice(0,50); // 最多50条
    localStorage.setItem(key,JSON.stringify(trimmed));
  }catch(e){console.warn('archive failed',e)}
}
function _loadArchives(){
  try{
    const raw=localStorage.getItem(_uk('moneybag_chat_archive'));
    return raw?JSON.parse(raw):[];
  }catch{return []}
}
// 手动新建对话：归档当前 + 清空
window.newChatSession=function(){
  if(chatMessages&&chatMessages.length>0){
    _archiveCurrentChat(new Date().toISOString().slice(0,10));
  }
  chatMessages=[];
  localStorage.removeItem(_uk('moneybag_chat_history'));
  if(typeof _chatScrollPos!=='undefined')_chatScrollPos=0;
  if(typeof _chatLastMsgCount!=='undefined')_chatLastMsgCount=0;
  if(typeof renderChat==='function')renderChat();
};
// 从历史恢复某个会话到当前
window.restoreChatSession=function(sessionId){
  const archives=_loadArchives();
  const sess=archives.find(a=>a.id===sessionId);
  if(!sess)return;
  if(chatMessages&&chatMessages.length>0){
    if(!confirm('当前对话会被归档，是否继续？'))return;
    _archiveCurrentChat(new Date().toISOString().slice(0,10));
  }
  chatMessages=sess.messages.slice();
  _saveChatHistory();
  document.querySelector('.modal-overlay')?.remove();
  if(typeof _chatScrollPos!=='undefined')_chatScrollPos=0;
  if(typeof _chatLastMsgCount!=='undefined')_chatLastMsgCount=0;
  renderChat();
};
// 删除归档中的某条
window.deleteArchiveSession=function(sessionId){
  if(!confirm('删除这条历史对话？'))return;
  const archives=_loadArchives().filter(a=>a.id!==sessionId);
  localStorage.setItem(_uk('moneybag_chat_archive'),JSON.stringify(archives));
  if(typeof showChatHistoryPanel==='function')showChatHistoryPanel();
};
// 历史会话面板
window.showChatHistoryPanel=function(){
  const archives=_loadArchives();
  // 移除旧面板（刷新）
  document.querySelectorAll('.modal-overlay').forEach(o=>{if(o.dataset.kind==='chatHistory')o.remove()});
  const o=document.createElement('div');
  o.className='modal-overlay';
  o.dataset.kind='chatHistory';
  o.onclick=e=>{if(e.target===o)o.remove()};
  let body='';
  if(!archives.length){
    body='<div style="text-align:center;padding:40px;color:var(--text-tertiary,#7A8499)"><div style="font-size:36px;margin-bottom:12px">📭</div>暂无历史对话</div>';
  }else{
    // 按日期分组
    const groups={};
    archives.forEach(a=>{(groups[a.date]=groups[a.date]||[]).push(a)});
    body=Object.keys(groups).sort().reverse().map(date=>{
      const items=groups[date].map(a=>`<div onclick="restoreChatSession('${a.id}')" style="padding:10px 12px;background:var(--bg-elevated,#10131C);border-radius:10px;margin-bottom:6px;cursor:pointer;border:1px solid rgba(148,163,184,.06)">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
          <div style="flex:1;min-width:0">
            <div style="font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${a.title||'对话'}</div>
            <div style="font-size:10px;color:var(--text-tertiary,#7A8499);margin-top:2px">${a.msgCount} 条消息</div>
          </div>
          <button onclick="event.stopPropagation();deleteArchiveSession('${a.id}')" style="background:transparent;border:none;color:var(--text-tertiary,#7A8499);font-size:14px;cursor:pointer;padding:4px">🗑️</button>
        </div>
      </div>`).join('');
      return `<div style="margin-bottom:14px"><div style="font-size:11px;color:var(--text-tertiary,#7A8499);margin-bottom:6px">📅 ${date}</div>${items}</div>`;
    }).join('');
  }
  o.innerHTML=`<div class="modal-sheet" style="max-height:80vh;display:flex;flex-direction:column" onclick="event.stopPropagation()">
    <div class="modal-handle"></div>
    <div class="modal-title">📜 历史对话<span style="font-size:11px;color:var(--text-tertiary,#7A8499);font-weight:400;margin-left:8px">保留30天</span></div>
    <div style="display:flex;gap:6px;margin-top:8px;padding-bottom:10px;border-bottom:1px solid rgba(148,163,184,.08)">
      ${chatMessages&&chatMessages.length>0
        ? `<button onclick="document.querySelector('.modal-overlay[data-kind=chatHistory]')?.remove();newChatSession();setTimeout(()=>showChatHistoryPanel(),50)" style="flex:1;padding:8px;border-radius:8px;border:1px solid rgba(99,102,241,.3);background:rgba(99,102,241,.08);color:#818CF8;font-size:12px;cursor:pointer">＋ 新建对话（归档当前 ${chatMessages.length} 条）</button>
           <button onclick="if(confirm('彻底清除当前对话？不保存到历史')){chatMessages=[];try{localStorage.removeItem(_uk('moneybag_chat_history'))}catch{};if(typeof _chatScrollPos!=='undefined')_chatScrollPos=0;if(typeof _chatLastMsgCount!=='undefined')_chatLastMsgCount=0;renderChat();document.querySelector('.modal-overlay[data-kind=chatHistory]')?.remove();setTimeout(()=>showChatHistoryPanel(),50);}" style="padding:8px 12px;border-radius:8px;border:1px solid rgba(239,68,68,.3);background:rgba(239,68,68,.08);color:var(--red);font-size:12px;cursor:pointer" title="清除当前对话">🗑️ 清除当前</button>`
        : `<button disabled style="flex:1;padding:8px;border-radius:8px;border:1px dashed rgba(148,163,184,.25);background:transparent;color:var(--text-tertiary,#7A8499);font-size:12px;cursor:not-allowed">＋ 新建对话（当前已为空）</button>`
      }
    </div>
    <div style="flex:1;overflow-y:auto;margin-top:12px;min-height:200px">${body}</div>
  </div>`;
  document.body.appendChild(o);
};


// ---- 启动 ----
migrateV3toV4();
(async()=>{
  await checkAPI();
  await ensureProfile(); // 首次使用输入名字
  try{var prefR=await fetch(API_BASE+'/user/preference?userId='+encodeURIComponent(getProfileId()),{signal:AbortSignal.timeout(5000)});
  if(prefR.ok){var prefs=await prefR.json();if(prefs.display_mode&&!localStorage.getItem('moneybag_ui_mode_set_by_user')){_uiMode=prefs.display_mode;localStorage.setItem('moneybag_ui_mode',_uiMode)}}}catch(e){}
  _loadChatHistory(); // B3: 恢复聊天记录
  fetchNav();await syncFromCloud();loadModelList();
  // v9.5.13: 启动后 3 秒做一次持仓全量同步（修历史欠账）
  setTimeout(()=>{try{ if(typeof syncHoldingsToBackend==='function') syncHoldingsToBackend(true); }catch(e){}}, 3000);
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
  // 周自检横幅（审计报告 overall_status != healthy 时显示）
  if(API_AVAILABLE) checkAuditBanner();
})();

// ---- 周自检横幅 ----
function checkAuditBanner(){
  fetch(API_BASE+'/audit/latest',{signal:AbortSignal.timeout(5000)})
    .then(function(r){return r.ok?r.json():null})
    .then(function(d){
      if(!d||d.read||d.overall_status==='healthy'||d.overall_status==='none'||!d.banner_title)return;
      showAuditBanner(d);
    })
    .catch(function(){});
}
function showAuditBanner(d){
  if(document.getElementById('_auditBanner'))return;
  var color=d.overall_status==='critical'?'#c0392b':'#e67e22';
  var icon=d.overall_status==='critical'?'🚨':'⚠️';
  var el=document.createElement('div');
  el.id='_auditBanner';
  el.style.cssText='position:fixed;top:0;left:0;right:0;z-index:10002;background:'+color+';color:#fff;padding:10px 16px;display:flex;align-items:center;gap:10px;font-size:14px;box-shadow:0 2px 8px rgba(0,0,0,.3)';
  el.innerHTML='<span style="font-size:18px">'+icon+'</span>'
    +'<div style="flex:1"><strong>'+_escHtml(d.banner_title)+'</strong>'
    +(d.banner_message?'<br><span style="font-size:12px;opacity:.9">'+_escHtml(d.banner_message)+'</span>':'')
    +'</div>'
    +'<button onclick="dismissAuditBanner()" style="background:rgba(255,255,255,.25);border:none;color:#fff;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:13px">知道了</button>';
  document.body.insertBefore(el,document.body.firstChild);
  // 为页面主内容留出顶部空间
  var h=el.offsetHeight||52;
  document.body.style.paddingTop=h+'px';
}
function dismissAuditBanner(){
  var el=document.getElementById('_auditBanner');
  if(el){document.body.style.paddingTop='';el.remove();}
  // 告知后端已读
  fetch(API_BASE+'/audit/mark-read',{method:'POST',signal:AbortSignal.timeout(5000)}).catch(function(){});
}
function _escHtml(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

// Phase 5: 盯盘预警轮询 + visibilitychange
var _alertPolling=null;
function startAlertPolling(){
  if(_alertPolling||!API_AVAILABLE)return;
  _alertPolling=setInterval(function(){
    fetch(API_BASE+'/watchlist/alerts?userId='+encodeURIComponent(getProfileId()),{signal:AbortSignal.timeout(10000)}).then(function(r){return r.json()}).then(function(d){
      if(d.alerts&&d.alerts.length>0){
        d.alerts.filter(function(a){return a.level==='danger'}).forEach(function(a){showToast('⚠️ '+a.message,'danger')});
      }
    }).catch(function(){});
  },15000);
}
function stopAlertPolling(){if(_alertPolling){clearInterval(_alertPolling);_alertPolling=null}}
document.addEventListener('visibilitychange',function(){
  if(document.hidden){stopAlertPolling()}else{
    startAlertPolling();
    // v9.5.29: 从后台切回前台时检查跨天归档（修复"App 不关跨天后还显示昨天对话"）
    try{ if(typeof _autoArchiveIfNewDay==='function'){
      _autoArchiveIfNewDay();
      // 如果当前在 chat 页，刷新一下显示
      if(typeof currentPage!=='undefined' && currentPage==='chat' && typeof renderChat==='function'){
        renderChat();
      }
    }}catch(e){}
  }
});
function checkTradingHours(){
  var now=new Date(),h=now.getHours(),m=now.getMinutes(),day=now.getDay();
  if(day>=1&&day<=5&&((h===9&&m>=25)||h>=10)&&(h<15||(h===15&&m<=5))){startAlertPolling()}
  else{stopAlertPolling()}
}
setInterval(checkTradingHours,300000);
checkTradingHours();

// ===== V6 FRONTEND PATCHES START =====

// --- 00-common.js ---
/* =========================================================================
 * V6 Phase 5 前端欠账补丁 - 公共工具
 * 追加式模块化：不改原函数体，通过劫持/后插注入新 UI
 * 依赖全局：API_BASE, API_AVAILABLE, getProfileParam, getProfileId, isProMode
 * ========================================================================= */
;(function(){
  'use strict';
  if (window._V6Patches) return; // 防重复加载
  window._V6Patches = { version: '1.0.0', loadedAt: Date.now() };

  // --- 通用 fetch 封装：统一超时 + 错误回退 ---
  window._v6Fetch = async function(path, opts){
    opts = opts || {};
    const timeout = opts.timeout || 15000;
    const url = (path.startsWith('http') ? path : API_BASE + path);
    try {
      const r = await fetch(url, Object.assign({
        signal: AbortSignal.timeout(timeout)
      }, opts));
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return await r.json();
    } catch (e) {
      console.warn('[V6] fetch fail:', path, e.message);
      return null;
    }
  };

  // --- 等锚点出现再注入（带超时） ---
  window._v6WaitEl = function(selector, maxMs){
    maxMs = maxMs || 3000;
    return new Promise(resolve => {
      const start = Date.now();
      const tick = () => {
        const el = document.querySelector(selector);
        if (el) return resolve(el);
        if (Date.now() - start > maxMs) return resolve(null);
        setTimeout(tick, 80);
      };
      tick();
    });
  };

  // --- 卡片模板：统一风格 ---
  window._v6Card = function(title, bodyHtml, opts){
    opts = opts || {};
    const border = opts.border ? `border-left:3px solid ${opts.border};` : '';
    const badge = opts.badge
      ? `<span style="font-size:10px;padding:2px 6px;border-radius:6px;background:rgba(59,130,246,.12);color:#3B82F6;margin-left:6px;font-weight:600">${opts.badge}</span>`
      : '';
    return `<div class="dashboard-card" style="${border}margin-top:8px">
      <div class="dashboard-card-title">${title}${badge}</div>
      ${bodyHtml}
    </div>`;
  };

  // --- 骨架屏 ---
  window._v6Skeleton = function(msg){
    return `<div style="text-align:center;padding:24px;color:var(--text2)">
      <div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>
      <div style="font-size:12px">${msg || '加载中...'}</div>
    </div>`;
  };

  // --- 劫持全局函数：链式包装，每次追加一个 afterFn 钩子 ---
  window._v6Hijack = function(funcName, afterFn){
    const orig = window[funcName];
    if (typeof orig !== 'function') {
      console.warn('[V6] hijack target not found:', funcName);
      return;
    }
    // 允许多次劫持：每次在当前版本外面再包一层
    const wrapped = async function(...args){
      const r = await orig.apply(this, args);
      try { await afterFn.apply(this, args); } catch(e){ console.warn('[V6] after hook error:', funcName, e); }
      return r;
    };
    wrapped.__v6Hijacked = true;
    wrapped.__orig = orig;
    window[funcName] = wrapped;
  };

  // --- 持仓是否为空（判断是否进入"空仓模式"）---
  window._v6IsEmptyHoldings = function(){
    try {
      // 优先信任服务端持仓检查结果（异步写入的 moneybag_has_holdings 标志）
      const flagKey = (typeof _uk === 'function') ? _uk('moneybag_has_holdings') : 'moneybag_has_holdings';
      if (localStorage.getItem(flagKey) === '1') return false;
      const p = (typeof loadPortfolio === 'function') ? (loadPortfolio() || {}) : {};
      const txns = (typeof loadTxns === 'function') ? (loadTxns() || []) : [];
      const assets = (typeof loadAssets === 'function') ? (loadAssets() || []) : [];
      // holdings 数组为空 + 无交易记录 + 无资产 = 空仓
      const hCount = (p.holdings || []).length;
      const tCount = txns.length;
      const aCount = assets.length;
      return hCount === 0 && tCount === 0 && aCount === 0;
    } catch(e){ return false; }
  };

  console.log('[V6] common utils loaded');
})();


// ===== V6 FRONTEND PATCHES END =====
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// V6 Phase 5: 分析历史页面
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
let historyTab='all';
