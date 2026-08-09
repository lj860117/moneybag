let fundPickSearch = "";

// ============================================================
// insight-fund.js — 基金选基模块（从 insight.js 拆分，v9.5.48 E1）
// 函数依赖：必须在 app.js 之后、insight.js 之前加载
// ============================================================

// v9.5.119: 这些函数定义在 _showFundData 闭包内部但被外部代码调用。
// 解法：在全局预设占位，_showFundData 首次执行时通过 window 赋值覆盖。
// 同时将 _showPotentialFunds 改为调用 window._renderFundList 确保一致。

// v9.5.90: 统一所有横条样式 — 同字号、同 padding、同 line-height，避免参差不齐
function _fundTagsHTML(f){
  const r=f.returns;
  const tags=[];
  if(r['1y']!=null&&r['3m']!=null&&r['6m']!=null&&r['1y']>0&&r['3m']>0&&r['6m']>0)tags.push('📈稳定上涨');
  if(r['1y']!=null&&r['1y']>15)tags.push('🔥高收益');
  if(f.fee&&parseFloat(f.fee)<0.5)tags.push('💰低费率');
  if(r['3y']!=null&&r['3y']>30)tags.push('⭐长期优秀');
  const policyBadges=_policyBadgesHTML(f.code,f.name||'');

  // 统一行样式（所有横条共用，保证对齐）
  const ROW = 'font-size:11px;padding:4px 9px;border-radius:6px;margin-bottom:4px;line-height:1.5;display:block;box-sizing:border-box;width:100%';

  let h='';
  // v9.5.123 P3: 风格标签(价值/成长/均衡/指数/量化/QDII)
  if(f.style_tag){
    const _stColors={'价值':'#F59E0B','成长':'#86EFAC','均衡':'#A5B4FC','指数':'#94A3B8','量化':'#D4A5F5','QDII':'#5DCAA5','主动':'#94A3B8'};
    const stc=_stColors[f.style_tag]||'#94A3B8';
    h+='<span style="font-size:10px;padding:1px 6px;border-radius:4px;background:rgba(148,163,184,.08);color:'+stc+';margin-right:4px;vertical-align:middle">'+f.style_tag+'</span>';
  }
  // v9.5.123: 走势预估标签（8维评分+置信度）
  if(f.trend_label){
    const tColor = f.trend_direction==='up'?'#86EFAC':f.trend_direction==='down'?'#FCA5A5':'#9AA1AC';
    const tBg = f.trend_direction==='up'?'rgba(134,239,172,.08)':f.trend_direction==='down'?'rgba(252,165,165,.08)':'rgba(148,163,184,.06)';
    const tScore = f.trend_score||0;
    const tConf = f.trend_confidence||0;
    const confTag = tConf>=70?`<span style="opacity:0.6;font-size:9px;margin-left:2px">置信${tConf}%</span>`:tConf<50?`<span style="opacity:0.7;font-size:9px;margin-left:2px;color:#F59E0B">⚠置信${tConf}%</span>`:'';
    h+=`<div style="font-size:11px;padding:3px 8px;border-radius:5px;margin-bottom:4px;background:${tBg};color:${tColor};display:inline-flex;align-items:center;gap:4px"><span style="font-weight:600">${f.trend_label} ${tScore>0?'+':''}${tScore}分</span><span style="opacity:0.8;font-size:10px">${f.trend_reason||''}</span>${confTag}</div>`;
  }
  if(tags.length||policyBadges){
    h+='<div style="display:flex;gap:4px;flex-wrap:wrap;margin-bottom:4px">'+
      tags.map(t=>'<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:rgba(16,185,129,.1);color:#6EE7B7">'+t+'</span>').join('')+
      policyBadges+'</div>';
  }
  // 潜力评分（最顶部）
  if(f.potential){
    const pt=f.potential;
    const ptBg=pt.level==='high'?'rgba(83,74,183,.12)':'rgba(186,117,23,.08)';
    const ptColor=pt.level==='high'?'#AFA9EC':'#EF9F27';
    h+='<div style="'+ROW+';background:'+ptBg+';color:'+ptColor+';font-weight:500">'+
        pt.label+'<span style="font-size:10px;margin-left:6px;opacity:0.7">'+(pt.signal_flags||'')+'</span>'+
        (pt.reason?'<div style="font-size:11px;margin-top:2px;font-weight:400;opacity:0.85">'+pt.reason+'</div>':'')+
      '</div>';
  }
  // 综合买入信号
  if(f.price_signal&&f.price_signal.level!=='neutral'){
    const ps=f.price_signal;
    const psBg=ps.level==='strong_buy'?'rgba(34,197,94,.15)':ps.level==='buy'?'rgba(34,197,94,.08)':ps.level==='caution'?'rgba(234,179,8,.1)':'rgba(239,68,68,.08)';
    const psColor=ps.level==='strong_buy'?'#4ADE80':ps.level==='buy'?'#86EFAC':ps.level==='caution'?'#FDE68A':'#FCA5A5';
    h+='<div style="'+ROW+';background:'+psBg+';color:'+psColor+';font-weight:500">'+ps.label+(ps.reason?' · '+ps.reason:'')+'</div>';
  }
  // 再平衡缺口方向
  if(f.gap_match&&f.gap_hint){
    h+='<div style="'+ROW+';background:rgba(34,197,94,.12);color:#86EFAC">'+f.gap_hint+'</div>';
  }
  // 持仓关联提示
  if(f.holding_relation&&f.holding_relation!=='🟢 新敞口'&&f.holding_hint){
    const hintColor=f.holding_relation==='🔵 已持仓'?'rgba(59,130,246,.15)':'rgba(234,179,8,.1)';
    const hintText=f.holding_relation==='🔵 已持仓'?'#93C5FD':'#FDE68A';
    h+='<div style="'+ROW+';background:'+hintColor+';color:'+hintText+'">'+f.holding_relation+' '+f.holding_hint+'</div>';
  }
  // 基金经理换届
  if(f.manager_change&&f.manager_warn){
    h+='<div style="'+ROW+';background:rgba(239,68,68,.1);color:#FCA5A5">'+f.manager_warn+'</div>';
  }else if(f.current_manager){
    h+='<div style="'+ROW+';background:rgba(100,100,100,.06);color:#9CA3AF">👤 '+f.current_manager+'</div>';
  }
  // 与持仓相关系数
  if(f.correlation_label&&f.correlation_hint){
    const corrBg=f.correlation_score<=0.3?'rgba(34,197,94,.1)':f.correlation_score<=0.6?'rgba(234,179,8,.08)':'rgba(239,68,68,.08)';
    const corrColor=f.correlation_score<=0.3?'#86EFAC':f.correlation_score<=0.6?'#FDE68A':'#FCA5A5';
    h+='<div style="'+ROW+';background:'+corrBg+';color:'+corrColor+'" title="'+f.correlation_hint+'">📊 与持仓相关系数 '+f.correlation_label+'</div>';
  }
  // v9.5.123: DNA画像个性化适配标签
  if(f.dna_match&&f.dna_match.length){
    for(const dm of f.dna_match){
      const dmBg=dm.type.includes('match')?'rgba(134,239,172,.08)':'rgba(245,158,11,.08)';
      h+='<div style="'+ROW+';background:'+dmBg+';color:'+dm.color+'">'+dm.text+'</div>';
    }
  }
  // v9.5.123: 动态热点标签
  if(f.hot_sector_match){
    const hs=f.hot_sector_match;
    h+='<div style="'+ROW+';background:rgba(255,138,76,.1);color:#FF8A4C">🔥 踩中今日热点: '+hs.name+(hs.pct?' +'+hs.pct+'%':'')+'</div>';
  }
  // v9.5.123: 中长期主题方向
  if(f.theme_direction){
    h+='<div style="'+ROW+';background:rgba(99,102,241,.08);color:#A5B4FC">🚀 主题方向: '+f.theme_direction+'</div>';
  }
  // v9.5.123: 今日实时估算涨跌(预热数据+异步刷新)
  {
    const te=f.today_estimate;
    const teText=te!=null?('📊 今日: '+(te>=0?'+':'')+te.toFixed(2)+'%'):'📊 今日: 加载中...';
    const teColor=te!=null?(te>=0?'#86EFAC':'#FCA5A5'):'#9CA3AF';
    h+='<div data-fund-est="'+(f.code||'')+'" style="'+ROW+';background:rgba(148,163,184,.04);color:'+teColor+'">'+teText+'</div>';
  }
  // v9.5.123 P3-2: 经理稳定性
  if(f.manager_stability&&f.manager_stability.level==='new'){
    h+='<div style="'+ROW+';background:rgba(245,158,11,.08);color:'+f.manager_stability.color+'">'+f.manager_stability.text+'</div>';
  }
  // v9.5.123: QDII限购/暂停申购标注
  if(f.purchase_warning){
    h+='<div style="'+ROW+';background:rgba(239,68,68,.1);color:#FCA5A5">'+f.purchase_warning+'</div>';
  }
  // 净值历史百分位（仅当没有 price_signal 或 price_signal 是 neutral 时显示，避免与"谨慎买入·净值历史高位N%"重复）
  const psHasInfo = f.price_signal && f.price_signal.level && f.price_signal.level!=='neutral';
  if(f.nav_percentile!=null && !psHasInfo){
    const p=f.nav_percentile;
    const navBg=p<=20?'rgba(34,197,94,.1)':p<=40?'rgba(34,197,94,.06)':p>=80?'rgba(239,68,68,.08)':'rgba(100,100,100,.06)';
    const navColor=p<=20?'#86EFAC':p<=40?'#A7F3D0':p>=80?'#FCA5A5':'#9CA3AF';
    const title=f.nav_low&&f.nav_high?`历史区间 ${f.nav_low}~${f.nav_high} (近${f.nav_hist_count||0}个交易日)`:'';
    h+='<div style="'+ROW+';background:'+navBg+';color:'+navColor+'" title="'+title+'">💰 净值位置 '+f.nav_pct_label+'</div>';
  }
  // v9.5.107: AI 点评由 _renderFundList 在卡片底部统一渲染，这里不重复输出
  // v9.5.91: 横条左 padding 22px 对齐上方标签云（序号14px + gap8px = 22px）
  return h?'<div style="padding:4px 12px 8px 22px;display:flex;flex-direction:column;align-items:stretch">'+h+'</div>':'';
}

function _fundPickBtnsHTML(){
// v9.5.41 UI 重构：3 行胶囊堆压成 2 行 — 类型1行 + (排序段控件 + 视图图标) 1行
// v9.5.42 加 ⚙️ 自定义筛选入口
const wishCount = _wishlistFunds().length;
const sortItems = [['score','综合'],['1y','1年'],['3y','3年'],['ytd','今年']];
const viewItems = [['hot','🌬️','风口'],['potential','🚀','潜力'],['mine','📋','持仓'],['wish','❤️','心愿'+(wishCount>0?` ${wishCount}`:'')]];
const f = window._fundFilter || {};
const filterActive = Object.values(f).some(v=>v!=null);

// v9.5.83b 布局：品类行（第一行）+ 排序|⚙️筛选 + 视图 tab（第二行）
return `<div id="fundPickTypeBar" style="display:flex;align-items:center;gap:5px;flex-wrap:wrap;margin-bottom:6px">
${[['all','全部'],['stock','股票'],['bond','债券'],['index','指数'],['qdii','🌐 海外']].map(([k,l])=>`<button class="section-tab ${fundPickType===k?'active':''}" onclick="fundPickType='${k}';_updateFundPickBtns();setTimeout(renderFundPickResult,0)" style="font-size:12px;padding:5px 10px">${l}</button>`).join('')}
</div>
<div id="fundPickSortBar" style="display:flex;align-items:center;gap:6px;margin-bottom:10px;flex-wrap:wrap">
  <div style="display:inline-flex;border:1px solid rgba(148,163,184,.18);border-radius:8px;overflow:hidden;background:rgba(15,23,42,.4);flex-shrink:0">
    ${sortItems.map(([k,l],i)=>{
      const isAct = fundPickSort===k;
      const isLast = i===sortItems.length-1;
      return `<button onclick="fundPickSort='${k}';_updateFundPickBtns();setTimeout(renderFundPickResult,0)" style="padding:5px 11px;font-size:11px;font-weight:${isAct?'700':'500'};border:none;background:${isAct?'#FF8A4C':'transparent'};color:${isAct?'#fff':'#9aa1ac'};cursor:pointer;border-right:${isLast?'none':'1px solid rgba(148,163,184,.18)'};transition:background .15s">${l}</button>`;
    }).join('')}
    <button onclick="_showFundFilterModal()" style="padding:5px 10px;font-size:11px;border:none;border-left:1px solid rgba(148,163,184,.18);background:${filterActive?'rgba(99,102,241,.25)':'transparent'};color:${filterActive?'#A5B4FC':'#9aa1ac'};cursor:pointer" title="自定义筛选">⚙️${filterActive?'●':''}</button>
  </div>
  <div style="display:inline-flex;gap:5px;flex-wrap:wrap">
    ${viewItems.map(([k,icon,label])=>{
      const isAct = fundPickSort===k;
      return `<button onclick="fundPickSort='${k}';_updateFundPickBtns();setTimeout(renderFundPickResult,0)" style="display:inline-flex;align-items:center;gap:3px;padding:4px 9px;font-size:11px;font-weight:${isAct?'700':'500'};border:1px solid ${isAct?'rgba(255,138,76,.7)':'rgba(148,163,184,.22)'};border-radius:14px;background:${isAct?'rgba(255,138,76,.18)':'transparent'};color:${isAct?'#FFB755':'#9aa1ac'};cursor:pointer;transition:all .15s">${icon} <span>${label}</span></button>`;
    }).join('')}
  </div>
</div>`}
function _updateFundPickBtns(){
const tb=document.getElementById('fundPickTypeBar');const sb=document.getElementById('fundPickSortBar');
if(tb)tb.innerHTML=[['all','全部'],['stock','股票'],['bond','债券'],['index','指数'],['qdii','🌐 海外']].map(([k,l])=>`<button class="section-tab ${fundPickType===k?'active':''}" onclick="fundPickType='${k}';_updateFundPickBtns();setTimeout(renderFundPickResult,0)" style="font-size:12px;padding:5px 10px">${l}</button>`).join('');
if(sb){
  // 用整段 HTML 重渲染保持新的 segmented + icon 视图样式一致
  const tmp=document.createElement('div');
  tmp.innerHTML=_fundPickBtnsHTML();
  const newSb=tmp.querySelector('#fundPickSortBar');
  if(newSb) sb.innerHTML = newSb.innerHTML;
}
}

// v9.5.39 心愿单存储（按用户隔离）
function _wishlistFunds(){try{return JSON.parse(localStorage.getItem(_uk('moneybag_fund_wishlist')))||[]}catch{return[]}}
function _wishlistSave(arr){try{localStorage.setItem(_uk('moneybag_fund_wishlist'),JSON.stringify(arr.slice(0,30)))}catch{}}

// v9.5.42 F1 基金 P5 自定义筛选器
if(!window._fundFilter) window._fundFilter = {fee_max:null,r1y_min:null,r3y_min:null,scale_min:null};
window._showFundFilterModal = function(){
  const f = window._fundFilter;
  const o = document.createElement('div');
  o.className = 'modal-overlay';
  o.onclick = e => { if(e.target===o) o.remove(); };
  o.innerHTML = `<div class="modal-sheet" onclick="event.stopPropagation()" style="max-height:80vh;overflow-y:auto">
    <div class="modal-handle"></div>
    <div class="modal-title">⚙️ 自定义筛选 — 基金</div>
    <div style="font-size:11px;color:var(--text2);margin-bottom:14px">留空即不限制；筛选在当前榜单内进行</div>
    <div style="display:grid;grid-template-columns:auto 1fr;gap:10px 12px;font-size:12px;align-items:center">
      <span style="color:#9aa1ac">费率 ≤ (%)</span><input id="ff_fee" type="number" step="0.1" placeholder="例：1.5" value="${f.fee_max??''}" style="padding:6px 8px;border:1px solid rgba(148,163,184,.3);border-radius:6px;background:rgba(15,23,42,.5);color:#fff;width:100%">
      <span style="color:#9aa1ac">近1年 ≥ (%)</span><input id="ff_r1y" type="number" step="1" placeholder="例：10" value="${f.r1y_min??''}" style="padding:6px 8px;border:1px solid rgba(148,163,184,.3);border-radius:6px;background:rgba(15,23,42,.5);color:#fff;width:100%">
      <span style="color:#9aa1ac">近3年 ≥ (%)</span><input id="ff_r3y" type="number" step="1" placeholder="例：30" value="${f.r3y_min??''}" style="padding:6px 8px;border:1px solid rgba(148,163,184,.3);border-radius:6px;background:rgba(15,23,42,.5);color:#fff;width:100%">
      <span style="color:#9aa1ac">规模 ≥ (亿)</span><input id="ff_scale" type="number" step="1" placeholder="例：10" value="${f.scale_min??''}" style="padding:6px 8px;border:1px solid rgba(148,163,184,.3);border-radius:6px;background:rgba(15,23,42,.5);color:#fff;width:100%">
    </div>
    <div style="margin-top:12px;padding:8px 10px;background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.18);border-radius:8px;font-size:11px;color:#F59E0B">
      💡 提示：规模字段需要详情接口才能完整匹配，部分基金可能因数据不全被过滤
    </div>
    <div style="display:flex;gap:8px;margin-top:16px">
      <button class="mb-btn mb-btn--secondary" style="flex:1" onclick="window._fundFilter={fee_max:null,r1y_min:null,r3y_min:null,scale_min:null};document.querySelector('.modal-overlay')?.remove();_updateFundPickBtns();setTimeout(renderFundPickResult,0)">清空</button>
      <button class="mb-btn mb-btn--primary" style="flex:1" onclick="(function(){const g=id=>{const v=document.getElementById(id).value.trim();return v===''?null:parseFloat(v);};window._fundFilter={fee_max:g('ff_fee'),r1y_min:g('ff_r1y'),r3y_min:g('ff_r3y'),scale_min:g('ff_scale')};document.querySelector('.modal-overlay')?.remove();_updateFundPickBtns();setTimeout(renderFundPickResult,0);})()">应用</button>
    </div>
  </div>`;
  document.body.appendChild(o);
};
window._toggleWish = function(code, name){
  const arr = _wishlistFunds();
  const idx = arr.findIndex(x=>x.code===code);
  if(idx>=0){ arr.splice(idx,1); _wishlistSave(arr); }
  else{ arr.unshift({code, name, addedAt: Date.now()}); _wishlistSave(arr); }
  // 刷新当前列表
  if(typeof renderFundPickResult==='function') renderFundPickResult();
  if(typeof _updateFundPickBtns==='function') _updateFundPickBtns();
};
function _isWished(code){ return _wishlistFunds().some(x=>x.code===code); }
async function renderFundPick(el){
el.innerHTML=`<div class="dashboard-card" style="overflow:hidden">
<div class="dashboard-card-title" style="display:flex;align-items:center;gap:8px">🔍 基金智能筛选 <span id="backtestBadge" style="display:none"></span></div>
<div style="font-size:12px;color:var(--text2);margin-bottom:12px">8维AI评分(动量/技术面/估值/资金/环境/赛道/波动/情绪) + 双因子智能定投</div>
${_fundPickBtnsHTML()}
<div id="fundPickList"><div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>正在筛选基金...</div></div>
</div>`;
renderFundPickResult();
}

// v9.5.27: 删除"持仓排名追踪"功能（鸡肋——榜单算法迭代后早期持仓永远未上榜，无监控价值）
// 保留空函数兼容性，避免缓存的旧 HTML 调用报错
async function loadHoldingRankCompare(){ /* removed in v9.5.27 */ }

async function renderFundPickResult(){

  // v9.9.0: 支持 fundPickSearch 精确搜索
  if(fundPickSearch && fundPickSearch.trim()){
    const searchCode = fundPickSearch.trim();
    fundPickSearch = ''; // 清空，避免重复搜索
    const listEl = document.getElementById('fundPickList');
    if(listEl){
      listEl.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>正在搜索基金 '+searchCode+'...</div>';
      try{
        const r = await fetch(API_BASE+'/fund-screen?codes='+searchCode+'&userId='+getProfileId(),{signal:AbortSignal.timeout(45000)});
        if(!r.ok)throw new Error('fetch failed');
        const data = await r.json();
        if(data.funds && data.funds.length){
          _showFundData(listEl, data);
        } else {
          listEl.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)">未找到基金 '+searchCode+'，请检查代码是否正确</div>';
        }
      }catch(e){console.warn('Fund search failed:',e);listEl.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)">搜索失败，请稍后重试</div>'}
    }
    return;
  }
const listEl=document.getElementById('fundPickList');
if(!listEl)return;
// v9.5.39: 心愿单
if(fundPickSort==='wish'){if(window._showWishlist){window._showWishlist(listEl)}else{listEl.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)">请先浏览综合榜后再查看心愿单</div>'}return;}
// v9.5.31: 我的持仓 — 显示当前持仓 vs 同类平均
if(fundPickSort==='mine'){_showMyHoldings(listEl);return;}
// 风口观察：按近3月涨幅排序，带追高风险警告，不走普通评分逻辑
if(fundPickSort==='hot'){_showHotFunds(listEl);return;}
// v9.5.83: 潜力榜 — 筛选有 potential 标识的基金，按信号强度排序
if(fundPickSort==='potential'){_showPotentialFunds(listEl);return;}
// v9.5.120: 前端不做缓存，后端保证秒回（per-user文件缓存2h+stale-while-revalidate）
listEl.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>正在筛选基金...</div>';
try{
const r=await fetch(API_BASE+'/fund-screen?fund_type='+fundPickType+'&sort_by='+fundPickSort+'&top_n=30&userId='+getProfileId(),{signal:AbortSignal.timeout(45000)});
if(!r.ok)throw new Error('fetch failed');
const data=await r.json();
_showFundData(listEl,data);
// v9.5.123: 异步刷新今日估算(盘中实时,不影响首次渲染)
if(data.funds&&data.funds.length){
  const codes=data.funds.map(f=>f.code).filter(Boolean).join(',');
  fetch(API_BASE+'/fund-estimate-batch?codes='+codes,{signal:AbortSignal.timeout(5000)}).then(r=>r.ok?r.json():{}).then(est=>{
    if(!est||!Object.keys(est).length)return;
    document.querySelectorAll('[data-fund-est]').forEach(el=>{
      const code=el.getAttribute('data-fund-est');
      if(est[code]!=null){const v=est[code];el.textContent='📊 今日: '+(v>=0?'+':'')+v.toFixed(2)+'%';el.style.color=v>=0?'#86EFAC':'#FCA5A5'}
    });
  }).catch(()=>{});
}
}catch(e){console.warn('Fund pick failed:',e);listEl.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)">📡 数据加载中，请稍后重试<br><button onclick="setTimeout(renderFundPickResult,0)" style="margin-top:8px;padding:6px 16px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:12px">🔄 重试</button></div>'}}

// v9.5.120: 持仓诊断重构 — 和选基榜单同级别展示（潜力/评分/百分位/加减仓建议）
function _showMyHoldings(listEl){
  if(typeof loadTxns!=='function' || typeof calcHoldingsFromTxns!=='function'){
    listEl.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)">持仓数据未加载，请先访问"持仓"页</div>';
    return;
  }
  const txns = loadTxns();
  const holdings = calcHoldingsFromTxns(txns).filter(h=>{
    const c=(h.code||'').replace(/^(sh|sz)/i,'');
    return /^\d{6}$/.test(c) && (h.shares||0)>0;
  });
  if(!holdings.length){
    listEl.innerHTML='<div style="text-align:center;padding:30px;color:var(--text2)"><div style="font-size:32px;margin-bottom:8px">📋</div><div style="font-size:13px">还没有基金持仓</div><div style="font-size:11px;color:var(--text3,#7A8499);margin-top:6px">在"持仓"页添加交易后会显示在这里</div></div>';
    return;
  }
  listEl.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>正在加载持仓诊断...</div>';

  const codes = holdings.map(h=>(h.code||'').replace(/^(sh|sz)/i,''));
  // v9.5.121: 调新的 enrich 接口（后端一次性返回所有持仓基金的评分/百分位/潜力/行业）+ 实时净值
  Promise.all([
    fetch(API_BASE+'/fund-holdings/scan?'+getProfileParam(),{signal:AbortSignal.timeout(10000)}).then(r=>r.ok?r.json():{holdings:[]}).catch(()=>({holdings:[]})),
    fetch(API_BASE+'/fund-holdings/enrich?userId='+getProfileId(),{signal:AbortSignal.timeout(15000)}).then(r=>r.ok?r.json():{funds:[]}).catch(()=>({funds:[]})),
  ]).then(([scanRes, enrichData])=>{
    const navMap = {};
    (scanRes.holdings||[]).forEach(h=>{ navMap[(h.code||'').replace(/^(sh|sz)/i,'')] = h.realtime || {}; });
    const enrichFunds = enrichData.funds || [];
    // 用 code 做 map
    const enrichMap = {}; enrichFunds.forEach(f=>{enrichMap[f.code]=f;});

    let totalPnl = 0, totalCost = 0;
    let missingNavCount = 0;
    // 存持仓数据到全局（供深度诊断用）
    window._myHoldingsDiagData = [];

    const rows = holdings.map((h, i)=>{
      const code = (h.code||'').replace(/^(sh|sz)/i,'');
      const navInfo = navMap[code] || {};
      // 从 enrich 接口获取评分/潜力/百分位/行业
      const ef = enrichMap[code] || {};
      const score = ef.score || 0;
      const navPct = ef.nav_percentile;
      const navPctLabel = ef.nav_pct_label || '';
      const industryTag = ef.industry_tag || '';
      const timingLabel = ef.timing_label || '';
      const potential = ef.potential;
      const returns = ef.returns || {};
      const r1y = returns['1y'];
      const r3m = returns['3m'];

      // v9.5.121: 净值多层 fallback — realtime → window缓存 → enrich的nav_cur → 持仓页缓存
      let currentNav = (navInfo && (navInfo.nav || navInfo.estNav)) || 0;
      if(!currentNav){
        const cachedPnl = (window._holdingsPnl||{})[code];
        if(cachedPnl && cachedPnl.nav) currentNav = cachedPnl.nav;
      }
      if(!currentNav && ef.nav_cur) currentNav = ef.nav_cur;
      // 最终 fallback：从持仓页的 market_value / shares 反算
      if(!currentNav && h.marketPrice) currentNav = h.marketPrice;
      const cost = h.avgPrice || 0;
      const shares = h.shares || 0;
      const totalCostThis = cost * shares;
      const hasNav = currentNav > 0;
      const mv = hasNav ? currentNav * shares : totalCostThis;
      const pnl = hasNav ? mv - totalCostThis : 0;
      const pnlPct = (hasNav && totalCostThis>0) ? (mv - totalCostThis)/totalCostThis * 100 : 0;
      if(!hasNav) missingNavCount++;
      totalPnl += pnl; totalCost += totalCostThis;

      // 生成加仓/减仓/持有建议
      let actionTag = ''; let actionColor = '';
      if(potential && potential.level === 'high'){
        actionTag = '🚀 强潜力·可加仓'; actionColor = '#AFA9EC';
      } else if(potential && potential.level === 'mid'){
        actionTag = '📈 有潜力·适量加'; actionColor = '#86EFAC';
      } else if(navPct != null && navPct >= 85){
        actionTag = '⚠️ 高位·考虑减仓'; actionColor = '#F59E0B';
      } else if(navPct != null && navPct <= 20){
        actionTag = '💰 低位·可加仓'; actionColor = '#86EFAC';
      } else if(r1y != null && r1y < -15){
        actionTag = '🔻 大幅跑输·关注'; actionColor = '#F87171';
      } else if(pnlPct > 50){
        actionTag = '🎯 已大赚·可止盈'; actionColor = '#FCD34D';
      } else if(pnlPct < -20){
        actionTag = '📉 深亏·评估是否止损'; actionColor = '#F87171';
      } else {
        actionTag = '✋ 持有观察'; actionColor = '#9AA1AC';
      }

      // v9.5.123: 走势预估标签
      const trendDir = ef.trend_direction || '';
      const trendLabel = ef.trend_label || '';
      const trendReason = ef.trend_reason || '';
      const trendScore = ef.trend_score || 0;
      const trendConf = ef.trend_confidence || 0;

      // 标签行（和选基榜单同级别信息密度）
      let tagsHtml = '';
      const tags = [];
      // 走势预估在最顶部（最醒目）
      if(trendLabel){
        const tColor = trendDir==='up'?'#86EFAC':trendDir==='down'?'#FCA5A5':'#9AA1AC';
        const tBg = trendDir==='up'?'rgba(134,239,172,.12)':trendDir==='down'?'rgba(252,165,165,.12)':'rgba(154,161,172,.08)';
        tags.push(`<span style="background:${tBg};color:${tColor};padding:1px 6px;border-radius:8px;font-weight:600">${trendLabel} ${trendScore>0?'+':''}${trendScore}分</span>`);
        if(trendReason) tags.push(`<span style="color:${tColor};opacity:.85">${trendReason}</span>`);
      }
      // v9.5.123: 双因子定投建议标签
      const dcaLabel = ef.dca_label || '';
      const dcaMult = ef.dca_multiplier;
      if(dcaLabel && dcaMult != null){
        const dcaColor = dcaMult>=1.5?'#86EFAC':dcaMult>=1.0?'#A5B4FC':dcaMult>=0.5?'#F59E0B':'#FCA5A5';
        tags.push(`<span style="background:rgba(99,102,241,.08);color:${dcaColor};padding:1px 6px;border-radius:8px;font-weight:500">${dcaLabel}</span>`);
      }
      if(actionTag) tags.push(`<span style="color:${actionColor};font-weight:600">${actionTag}</span>`);
      if(score > 0) tags.push(`<span style="color:#818CF8">评分${score}</span>`);
      if(navPctLabel) tags.push(`<span style="color:${navPct>=70?'#F59E0B':navPct<=30?'#86EFAC':'#9AA1AC'}">百分位${navPctLabel}</span>`);
      if(industryTag && industryTag!=='其他') tags.push(`<span style="color:#9AA1AC">${industryTag}</span>`);
      if(timingLabel) tags.push(`<span style="color:#9AA1AC">${timingLabel}</span>`);
      if(tags.length) tagsHtml = `<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px;font-size:10px">${tags.join('')}</div>`;

      const pnlColor = pnl>=0 ? 'var(--color-bull,#FF6B6B)' : 'var(--color-bear,#00E5A0)';
      const pctSign = pnl>=0 ? '+' : '';

      // 收益信息行
      let returnInfo = '';
      if(r1y != null || r3m != null){
        const parts = [];
        if(r3m != null) parts.push(`3月${r3m>0?'+':''}${r3m.toFixed(1)}%`);
        if(r1y != null) parts.push(`1年${r1y>0?'+':''}${r1y.toFixed(1)}%`);
        returnInfo = `<div style="font-size:10px;color:var(--text-tertiary);margin-top:2px">${parts.join(' · ')}</div>`;
      }

      window._myHoldingsDiagData.push({code, name:h.name||'', industryTag, navPct});

      return `<div style="padding:10px 0;border-bottom:1px solid rgba(148,163,184,.06);cursor:pointer" onclick="showFundDetailModal('${code}','${(h.name||'').replace(/'/g,'')}')">
        <div style="display:flex;align-items:flex-start;gap:8px">
          <span style="font-size:12px;color:var(--text2);font-weight:700;min-width:16px;padding-top:2px">${i+1}</span>
          <div style="flex:1;min-width:0">
            <div style="font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--text-primary,#F0F2F7)">${h.name||code}</div>
            <div style="font-size:11px;color:var(--text2);margin-top:2px">${code} · 持${shares.toFixed(2)}份 · 成本¥${cost.toFixed(4)}${hasNav?' · 现¥'+currentNav.toFixed(4):''}</div>
            ${tagsHtml}
            ${returnInfo}
          </div>
          <div style="text-align:right;min-width:72px;padding-top:2px">
            <div style="font-size:15px;font-weight:800;color:${pnlColor}">${pctSign}${pnlPct.toFixed(2)}%</div>
            <div style="font-size:10px;color:${pnlColor}">${pctSign}¥${Math.abs(pnl)<1?(Math.abs(pnl)<0.01?'0':Math.abs(pnl).toFixed(1)):fmtMoney(Math.abs(Math.round(pnl)))}</div>
          </div>
        </div>
      </div>`;
    }).join('');

    const totalPnlColor = totalPnl>=0 ? 'var(--color-bull,#FF6B6B)' : 'var(--color-bear,#00E5A0)';
    const totalPctSign = totalPnl>=0 ? '+' : '';
    const totalPct = totalCost>0 ? totalPnl/totalCost*100 : 0;
    const missingHint = missingNavCount>0
      ? `<div style="font-size:10px;color:#F59E0B;margin-top:4px">⏳ ${missingNavCount} 只基金净值数据更新中，可能与首页/持仓页有偏差</div>`
      : '';

    listEl.innerHTML = `<div style="padding:10px 12px;margin-bottom:10px;background:rgba(99,102,241,.05);border:1px solid rgba(99,102,241,.15);border-radius:8px">
      <div style="font-size:11px;color:var(--text-secondary,#9AA1AC);margin-bottom:4px">📋 我的基金持仓 · ${holdings.length} 只</div>
      <div style="display:flex;justify-content:space-between;align-items:baseline">
        <span style="font-size:11px;color:var(--text2)">总成本 ¥${fmtMoney(Math.round(totalCost))} → 总市值 ¥${fmtMoney(Math.round(totalCost+totalPnl))}</span>
        <span style="font-size:14px;font-weight:800;color:${totalPnlColor}">${totalPctSign}${totalPct.toFixed(2)}%</span>
      </div>
      ${missingHint}
      <div id="deepDiagArea" style="margin-top:8px"></div>
      <button onclick="_showDeepDiag()" style="margin-top:6px;width:100%;padding:6px;border-radius:6px;border:1px dashed rgba(99,102,241,.3);background:transparent;color:#818CF8;font-size:11px;cursor:pointer">🔬 展开深度诊断（行业分布 · K线对比）</button>
    </div>${rows}`;
  }).catch(e=>{
    console.warn('[MyHoldings]', e);
    listEl.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)">加载失败：'+(e.message||'未知错误')+'<br><button onclick="setTimeout(renderFundPickResult,0)" style="margin-top:8px;padding:6px 16px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:12px">🔄 重试</button></div>';
  });
}

// F6+ v9.5.122: 持仓深度诊断（toggle 模式：点击展开/收回）
async function _showDeepDiag(){
  const targetEl = document.getElementById('deepDiagArea');
  if(!targetEl) return;
  // toggle: 已有内容则收回
  if(targetEl.innerHTML.trim()){
    targetEl.innerHTML = '';
    // 更新按钮文案
    const btn = targetEl.nextElementSibling;
    if(btn && btn.tagName==='BUTTON') btn.textContent = '🔬 展开深度诊断（行业分布 · K线对比）';
    return;
  }
  const holdings = window._myHoldingsDiagData || [];
  if(!holdings.length){ targetEl.innerHTML='<div style="font-size:11px;color:var(--text2)">暂无持仓数据</div>'; return; }
  targetEl.innerHTML='<div style="padding:8px 0;color:var(--text2);font-size:12px">🔬 加载诊断数据中...</div>';

  // 行业分布：优先用已有的 industryTag，缺失的再调接口
  const codes = holdings.map(h=>h.code);
  let industryMap = {};
  holdings.forEach(h=>{ if(h.industryTag && h.industryTag!=='其他') industryMap[h.code]=h.industryTag; });
  try{
    // v9.5.113: 改用 top_n=30 + 用户ID（命中已有缓存秒返回），不再 top_n=2000 触发慢路径
    // 持仓基金大概率在综合榜里，找不到的退化为"其他"
    const d = await fetch(API_BASE+'/fund-screen?fund_type=all&sort_by=score&top_n=30&userId='+getProfileId(),{signal:AbortSignal.timeout(10000)}).then(r=>r.ok?r.json():{funds:[]}).catch(()=>({funds:[]}));
    (d.funds||[]).forEach(f=>{ if(codes.includes(f.code)) industryMap[f.code]=f.industry_tag||'其他'; });
    // 没匹配到的，调单独的 fund-detail 接口取行业
    const missing = codes.filter(c=>!industryMap[c]);
    if(missing.length){
      await Promise.all(missing.slice(0,8).map(c=>
        fetch(API_BASE+'/fund/detail/'+c,{signal:AbortSignal.timeout(8000)})
          .then(r=>r.ok?r.json():null)
          .then(dd=>{ if(dd && dd.industry_tag) industryMap[c]=dd.industry_tag; else industryMap[c]='其他'; })
          .catch(()=>{ industryMap[c]='其他'; })
      ));
    }
  }catch{}

  // 统计行业集中度
  const industryCnt = {};
  holdings.forEach(h=>{ const ind=industryMap[h.code]||'其他'; industryCnt[ind]=(industryCnt[ind]||0)+1; });
  const totalH = holdings.length;
  const industrySorted = Object.entries(industryCnt).sort((a,b)=>b[1]-a[1]);
  const topIndustry = industrySorted[0];

  // 集中度诊断
  let concentrationDiag = '';
  if(topIndustry && topIndustry[1]/totalH >= 0.5){
    concentrationDiag = `<div style="font-size:11px;color:#F59E0B;margin-top:4px">⚠️ 集中度较高：${topIndustry[0]} 占 ${Math.round(topIndustry[1]/totalH*100)}%，注意分散</div>`;
  } else if(industrySorted.length >= 4){
    concentrationDiag = `<div style="font-size:11px;color:var(--color-bull,#FF6B6B);margin-top:4px">✅ 行业分散度良好（${industrySorted.length} 个赛道）</div>`;
  }

  // 同类均值（已在持仓列表中展示，这里简化）
  const peerHtml = '';

  // v9.5.89: 升级为赛道重叠热力图（持仓基金 × 行业赛道矩阵）
  const topIndustries = industrySorted.slice(0,5).map(([ind])=>ind);
  const matrixHtml = topIndustries.length >= 2 ? (()=>{
    // 每个基金的行业归属
    const fundIndustries = holdings.map(h=>({
      name: (h.name||h.code).slice(0,5),
      code: h.code,
      tag: industryMap[h.code]||'其他'
    }));
    // 计算两两基金是否同赛道
    const overlapMatrix = [];
    for(let i=0;i<fundIndustries.length;i++){
      for(let j=i+1;j<fundIndustries.length;j++){
        const same = fundIndustries[i].tag === fundIndustries[j].tag && fundIndustries[i].tag!=='其他';
        overlapMatrix.push({a:fundIndustries[i].name,b:fundIndustries[j].name,same,tag:fundIndustries[i].tag});
      }
    }
    const overlapPairs = overlapMatrix.filter(x=>x.same);
    if(!overlapPairs.length) return `<div style="font-size:11px;color:#86EFAC;margin-top:8px">✅ 各基金赛道互不重叠，分散度较好</div>`;
    return `<div style="font-size:11px;color:#F59E0B;margin-top:8px;padding:8px;background:rgba(245,158,11,.06);border-radius:6px">
      <div style="margin-bottom:5px;font-weight:600">⚠️ 赛道重叠（${overlapPairs.length}对）</div>
      ${overlapPairs.map(p=>`<div style="font-size:10px;color:#D1D5DB;margin-top:3px">• ${p.a} ↔ ${p.b} <span style="color:#F59E0B">同属「${p.tag}」</span></div>`).join('')}
    </div>`;
  })() : '';

  // 行业分布条（保留原有）
  const barHtml = industrySorted.slice(0,6).map(([ind,cnt])=>{
    const pct = Math.round(cnt/totalH*100);
    return `<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
      <span style="font-size:11px;color:var(--text2);width:90px;flex-shrink:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${ind}</span>
      <div style="flex:1;height:6px;background:rgba(255,255,255,.06);border-radius:3px"><div style="width:${pct}%;height:100%;background:rgba(99,102,241,.6);border-radius:3px"></div></div>
      <span style="font-size:10px;color:var(--text-tertiary,#7A8499);width:30px;text-align:right">${pct}%</span>
    </div>`;
  }).join('');

  // K线按钮列表
  const klineBtns = holdings.slice(0,6).map(h=>`<button onclick="_showFundKlineModal('${h.code}','${(h.name||h.code).replace(/'/g,'')}')" style="padding:5px 8px;border-radius:6px;border:1px solid rgba(148,163,184,.15);background:rgba(255,255,255,.03);color:var(--text2);font-size:11px;cursor:pointer;display:flex;align-items:center;gap:3px">📈 ${(h.name||h.code).slice(0,6)}</button>`).join('');

  targetEl.innerHTML=`<div style="padding:10px;background:rgba(255,255,255,.03);border-radius:8px;margin-top:4px">
    <div style="font-size:11px;font-weight:600;color:var(--text-primary,#F0F2F7);margin-bottom:8px">🔬 深度诊断</div>
    <div style="font-size:11px;color:var(--text-tertiary,#7A8499);margin-bottom:4px">行业分布</div>
    ${barHtml}${concentrationDiag}
    ${matrixHtml}
    ${peerHtml?`<div style="margin-top:8px">${peerHtml}</div>`:''}
    <div style="font-size:11px;color:var(--text-tertiary,#7A8499);margin:10px 0 6px">净值走势 K 线</div>
    <div style="display:flex;flex-wrap:wrap;gap:6px">${klineBtns}</div>
    <div style="margin-top:12px;border-top:1px solid rgba(148,163,184,.1);padding-top:10px">
      <div style="font-size:11px;font-weight:600;color:var(--text-primary,#F0F2F7);margin-bottom:6px">🤖 AI 深度体检</div>
      <div id="aiCheckupArea" style="font-size:11px;color:var(--text2)">
        <button onclick="_loadAiCheckup()" style="width:100%;padding:6px;border-radius:6px;border:1px dashed rgba(139,92,246,.3);background:transparent;color:#A78BFA;font-size:11px;cursor:pointer">🧠 启动 AI 深度体检（Pro 级分析 · 6维度诊断）</button>
      </div>
    </div>
  </div>`;
  // 更新按钮文案为"收起"
  const btn = targetEl.nextElementSibling;
  if(btn && btn.tagName==='BUTTON') btn.textContent = '🔬 收起深度诊断';
}

// v9.5.121: AI 深度体检加载
function _loadAiCheckup(){
  const area = document.getElementById('aiCheckupArea');
  if(!area) return;
  area.innerHTML='<div style="padding:8px 0"><div class="loading-spinner" style="width:18px;height:18px;margin:0 auto 6px;border-width:2px"></div><div style="text-align:center;color:var(--text-tertiary)">AI 正在分析你的持仓组合...</div><div style="text-align:center;font-size:10px;color:var(--text-tertiary);margin-top:4px">DeepSeek Pro · 6维度深度体检 · 首次约5-10秒</div></div>';
  fetch(API_BASE+'/fund-holdings/ai-checkup?userId='+getProfileId(),{signal:AbortSignal.timeout(30000)})
  .then(r=>r.json())
  .then(d=>{
    if(d.status==='no_holdings'){
      area.innerHTML='<div style="color:var(--text-tertiary)">暂无持仓数据</div>';
      return;
    }
    const analysis = d.analysis || '';
    const dims = d.dimensions || {};
    const source = d.source || '';
    const modelLabel = source==='ai_pro'?'DeepSeek Pro':source==='ai_flash'?'DeepSeek Flash':'数据摘要';
    const time = d.generated_at || '';
    
    // 格式化 AI 输出
    let html = `<div style="padding:10px;background:rgba(139,92,246,.04);border:1px solid rgba(139,92,246,.12);border-radius:8px">`;
    html += `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">`;
    html += `<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:rgba(139,92,246,.15);color:#A78BFA">${modelLabel}</span>`;
    html += `<span style="font-size:9px;color:var(--text-tertiary)">${time}${d.from_cache?' · 缓存':''}</span>`;
    html += `</div>`;
    // 指标卡片
    if(dims.fund_count){
      html += `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">`;
      html += `<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:rgba(99,102,241,.1);color:#A5B4FC">${dims.fund_count}只基金</span>`;
      if(dims.avg_nav_pct!=null) html += `<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:${dims.avg_nav_pct>70?'rgba(245,158,11,.1)':'rgba(134,239,172,.1)'};color:${dims.avg_nav_pct>70?'#F59E0B':'#86EFAC'}">均值百分位 ${dims.avg_nav_pct}%</span>`;
      if(dims.concentration) html += `<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:${dims.concentration>50?'rgba(245,158,11,.1)':'rgba(134,239,172,.1)'};color:${dims.concentration>50?'#F59E0B':'#86EFAC'}">集中度 ${dims.concentration}%</span>`;
      if(dims.industries) html += `<span style="font-size:10px;padding:2px 6px;border-radius:4px;background:rgba(99,102,241,.1);color:#A5B4FC">${dims.industries}个行业</span>`;
      html += `</div>`;
    }
    // AI 分析正文
    html += `<div style="font-size:12px;line-height:1.8;color:var(--text-primary,#F0F2F7);white-space:pre-wrap">${analysis.replace(/\n/g,'<br>')}</div>`;
    html += `</div>`;
    area.innerHTML = html;
  })
  .catch(e=>{
    area.innerHTML=`<div style="color:#F87171;font-size:11px">AI 分析加载失败：${e.message}<br><button onclick="_loadAiCheckup()" style="margin-top:6px;padding:4px 12px;border-radius:4px;border:1px solid rgba(248,113,113,.3);background:transparent;color:#F87171;font-size:11px;cursor:pointer">重试</button></div>`;
  });
}

// F10 v9.5.47: 基金净值 K 线弹窗
async function _showFundKlineModal(code, name){
  const o=document.createElement('div');o.className='modal-overlay';o.onclick=e=>{if(e.target===o)o.remove()};
  // F11 v9.5.48: QDII 检测（名称含关键字 → 显示币种切换）
  const isQdii = /QDII|纳指|标普|纳斯达克|S&P|海外|港股|美股|日经|越南|印度/i.test(name||'');
  const fxCur = isQdii ? (/纳指|标普|纳斯达克|S&P|美股|海外科技/i.test(name||'') ? 'USD' : /港股|恒生/i.test(name||'') ? 'HKD' : /日经/i.test(name||'') ? 'JPY' : 'USD') : null;
  o.innerHTML=`<div class="modal-sheet" onclick="event.stopPropagation()" style="max-height:85vh">
    <div class="modal-handle"></div>
    <div class="modal-title">📈 ${name||code} 净值走势</div>
    ${isQdii?`<div id="fxToggleBar" style="padding:0 12px;margin-bottom:8px;display:flex;gap:6px;align-items:center">
      <span style="font-size:11px;color:var(--text2)">币种：</span>
      <button onclick="_fxSwitch('CNY')" data-cur="CNY" class="fx-btn active" style="padding:3px 10px;border-radius:4px;border:1px solid rgba(99,102,241,.4);background:rgba(99,102,241,.15);color:#A5B4FC;font-size:11px;cursor:pointer">¥ CNY</button>
      <button onclick="_fxSwitch('${fxCur}')" data-cur="${fxCur}" class="fx-btn" style="padding:3px 10px;border-radius:4px;border:1px solid rgba(148,163,184,.2);background:transparent;color:var(--text2);font-size:11px;cursor:pointer">${fxCur==='USD'?'$':fxCur==='HKD'?'HK$':fxCur==='JPY'?'¥(日)':fxCur} ${fxCur}</button>
      <span id="fxNote" style="font-size:10px;color:var(--text-tertiary,#7A8499);margin-left:auto"></span>
    </div>`:''}
    <div id="klineChartArea" style="padding:12px 0">
      <div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>加载中...</div>
    </div>
  </div>`;
  document.body.appendChild(o);

  // 存储原始 CNY 数据 + 当前显示币种（供 _fxSwitch 切换）
  window._klineRawData = null;
  window._klineFxRate = 1;  // 1 = CNY 原值
  window._klineDisplayCur = 'CNY';
  window._klineForeignCur = fxCur;

  try{
    const d = await fetch(API_BASE+'/fund/nav-history/'+code+'?days=90',{signal:AbortSignal.timeout(15000)}).then(r=>r.ok?r.json():null).catch(()=>null);
    const area = document.getElementById('klineChartArea');
    if(!area) return;
    if(!d || !d.ok || !d.data || !d.data.length){
      area.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)">暂无净值数据</div>';
      return;
    }
    window._klineRawData = d.data;
    _renderKlineChart();
  }catch(e){
    const area=document.getElementById('klineChartArea');
    if(area) area.innerHTML=`<div style="text-align:center;padding:20px;color:var(--text2)">加载失败: ${e.message||'未知'}</div>`;
  }
}

// F11 v9.5.48: K 线币种切换
window._fxSwitch = async function(cur){
  if(cur === window._klineDisplayCur) return;
  // 切换按钮高亮
  document.querySelectorAll('.fx-btn').forEach(b=>{
    const isActive = b.dataset.cur === cur;
    b.style.borderColor = isActive?'rgba(99,102,241,.4)':'rgba(148,163,184,.2)';
    b.style.background = isActive?'rgba(99,102,241,.15)':'transparent';
    b.style.color = isActive?'#A5B4FC':'var(--text2)';
  });
  if(cur === 'CNY'){
    window._klineFxRate = 1;
    window._klineDisplayCur = 'CNY';
    const note = document.getElementById('fxNote');
    if(note) note.textContent = '';
    _renderKlineChart();
    return;
  }
  // 拉外币汇率
  const note = document.getElementById('fxNote');
  if(note) note.textContent = '汇率加载中...';
  try{
    const r = await fetch(API_BASE+'/fx/rate?currency='+cur,{signal:AbortSignal.timeout(8000)});
    const d = await r.json();
    if(!d.ok){
      if(note) note.textContent = '汇率拉取失败';
      return;
    }
    window._klineFxRate = d.rate;
    window._klineDisplayCur = cur;
    if(note) note.textContent = `1 ${cur}=¥${d.rate} · ${d.date}`;
    _renderKlineChart();
  }catch{
    if(note) note.textContent = '汇率拉取失败';
  }
};

// 通用 K 线渲染（按当前币种）
function _renderKlineChart(){
  const data = window._klineRawData || [];
  const rate = window._klineFxRate || 1;
  const cur = window._klineDisplayCur || 'CNY';
  const symbol = cur==='CNY'?'¥':cur==='USD'?'$':cur==='HKD'?'HK$':cur==='JPY'?'¥':cur+' ';
  const area = document.getElementById('klineChartArea');
  if(!area || !data.length) return;
  const dates = data.map(x=>x.date.slice(5));
  // 净值原始单位是 CNY，外币 = CNY / rate
  const navs = data.map(x=> rate>0 ? x.nav/rate : x.nav);
  const minNav = Math.min(...navs), maxNav = Math.max(...navs);
  const range = maxNav - minNav || 0.01;
  const W=300, H=80, PAD=8;
  const points = navs.map((v,i)=>{
    const x = PAD + (i/(navs.length-1))*(W-PAD*2);
    const y = PAD + (1-(v-minNav)/range)*(H-PAD*2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const isUp = navs[navs.length-1] >= navs[0];
  const lineColor = isUp ? 'var(--color-bull,#FF6B6B)' : 'var(--color-bear,#00E5A0)';
  const pct = navs[0]>0 ? ((navs[navs.length-1]-navs[0])/navs[0]*100).toFixed(2) : '0.00';
  const pctColor = parseFloat(pct)>=0 ? 'var(--color-bull,#FF6B6B)' : 'var(--color-bear,#00E5A0)';
  // 外币时小数位多一点
  const dec = cur==='CNY'?4:cur==='JPY'?2:5;
  area.innerHTML=`<div style="padding:0 12px">
    <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:8px">
      <span style="font-size:12px;color:var(--text2)">近90日净值走势${cur!=='CNY'?' ('+cur+'计价)':''}</span>
      <span style="font-size:14px;font-weight:700;color:${pctColor}">${parseFloat(pct)>=0?'+':''}${pct}%</span>
    </div>
    <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:80px;display:block">
      <polyline points="${points}" fill="none" stroke="${lineColor}" stroke-width="1.5" stroke-linejoin="round"/>
    </svg>
    <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text-tertiary,#7A8499);margin-top:4px">
      <span>${dates[0]}</span><span>最新净值 ${symbol}${navs[navs.length-1].toFixed(dec)}</span><span>${dates[dates.length-1]}</span>
    </div>
    <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text-tertiary,#7A8499);margin-top:4px">
      <span>最低 ${symbol}${minNav.toFixed(dec)}</span><span>最高 ${symbol}${maxNav.toFixed(dec)}</span>
    </div>
  </div>`;
}

// v9.5.83: 潜力榜 — 拉综合榜（top_n 放大到 50），前端过滤出有 potential 字段的，按信号强度排序
function _showPotentialFunds(listEl){
// v9.5.119: 修复"点了没反应" — 立刻显示 loading，防止任何异步延迟导致用户感知为无响应
if(!listEl){listEl=document.getElementById('fundPickList');if(!listEl)return;}
// v9.5.120: 前端不缓存，后端文件缓存12h+stale-while-revalidate保证秒回

const _doRender=(data)=>{
  const funds=(data.funds||[]);
  // 后端已按 high>mid 排序，前端直接展示

  if(!funds.length){
    listEl.innerHTML=`<div style="text-align:center;padding:40px 20px;color:var(--text2)">
      <div style="font-size:32px;margin-bottom:8px">🔭</div>
      <div style="font-size:13px;font-weight:500">当前榜单没有达到潜力标准的基金</div>
      <div style="font-size:11px;color:var(--text-tertiary);margin-top:8px;line-height:1.7">
        潜力 = 至少满足 2 个信号：<br>
        🟢 A 赛道集中（AI/算力/机器人等）<br>
        🟢 B 净值低位 ≤60% 或近期深度回调<br>
        🟢 C 管理人强（综合分≥55 + 近1年>10%）<br>
        🟢 D 强动量持续（6月+30%/1年+30%）
      </div>
      <div style="margin-top:14px;display:flex;gap:8px;justify-content:center">
        <button onclick="(function(){try{Object.keys(localStorage).filter(k=>k.includes('fund_potential')||k.includes('fund_screen')).forEach(k=>localStorage.removeItem(k));if(typeof INSIGHT_CACHE!=='undefined'){Object.keys(INSIGHT_CACHE).forEach(k=>{if(INSIGHT_CACHE[k]){INSIGHT_CACHE[k].cached=null;INSIGHT_CACHE[k].timestamp=0}})}}catch(e){}renderFundPickResult()})()" style="padding:6px 16px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:12px">🔄 强制刷新</button>
        <button onclick="fundPickSort='score';_updateFundPickBtns();setTimeout(renderFundPickResult,0)" style="padding:6px 16px;border-radius:6px;border:1px solid rgba(148,163,184,.3);background:transparent;color:var(--text2);cursor:pointer;font-size:12px">📊 看综合榜</button>
      </div>
    </div>`;
    return;
  }

  const hi = data.high_count || funds.filter(f=>f.potential?.level==='high').length;
  const mi = data.mid_count || funds.filter(f=>f.potential?.level==='mid').length;
  let html=`<div style="padding:10px 12px;margin-bottom:10px;background:rgba(83,74,183,.08);border:1px solid rgba(83,74,183,.2);border-radius:8px;font-size:11px;color:#AFA9EC">
    🚀 潜力榜 · 共 ${funds.length} 只（高 ${hi} · 中 ${mi}）· 扫描 top ${data.total_scanned||80} · A赛道+B低位+C管理人+D强动量 · 仅供参考
  </div>`;
  // v9.5.119: _renderFundList 在 _showFundData 闭包内，首次打开潜力时可能未初始化
  // 使用 window._renderFundList 如果可用，否则用简易渲染
  if(window._renderFundList && typeof window._renderFundList === 'function'){
    html+=window._renderFundList(funds);
  } else {
    html+=funds.map((f,i)=>{
      const r=f.returns||{};const r1y=r['1y'];
      const r1yColor=r1y>0?'var(--color-bull,#FF6B6B)':'var(--color-bear,#00E5A0)';
      const pt=f.potential||{};
      return `<div style="padding:12px 0;border-bottom:1px solid rgba(148,163,184,.06);cursor:pointer" onclick="showFundDetailModal('${f.code}','${(f.name||'').replace(/'/g,'')}')">
        <div style="display:flex;align-items:flex-start;gap:8px">
          <span style="font-size:12px;color:var(--text2);font-weight:700;flex-shrink:0;min-width:14px">${i+1}</span>
          <div style="flex:1;min-width:0">
            <div style="font-size:13px;font-weight:600;color:var(--text-primary,#F0F2F7)">${f.name}</div>
            <div style="font-size:11px;color:#AFA9EC;margin-top:4px">${pt.label||''} ${pt.signal_flags||''} — ${pt.reason||''}</div>
            <div style="font-size:10px;color:var(--text-tertiary);margin-top:3px">${f.code} · 评分${f.score} · ${f.timing_label||''}</div>
          </div>
          <div style="text-align:right;flex-shrink:0">
            <div style="font-size:16px;font-weight:800;color:${r1yColor}">${r1y!=null?(r1y>0?'+':'')+r1y+'%':'—'}</div>
            <div style="font-size:9px;color:var(--text-tertiary)">近1年</div>
          </div>
        </div>
      </div>`;
    }).join('');
  }
  listEl.innerHTML=html;
};

// 直接拉后端（后端保证秒回）
listEl.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>正在加载潜力基金...</div>';
const url = API_BASE+'/fund-potential?userId='+getProfileId()+'&limit=30';
fetch(url, {signal: AbortSignal.timeout(45000)})
.then(r=>r.json()).then(data=>{
  _doRender(data);
})
.catch((err)=>{
  clearTimeout(fastTimer);
  clearTimeout(slowTimer);
  console.error('[POTENTIAL] fetch failed:', err);
  listEl.innerHTML='<div style="text-align:center;padding:30px 20px;color:var(--text2)">'
    +'<div style="font-size:32px;margin-bottom:8px">⏱️</div>'
    +'<div style="font-size:13px;font-weight:500">加载失败</div>'
    +'<div style="font-size:11px;color:var(--text-tertiary);margin-top:6px">'+(err?.message||'网络错误')+'</div>'
    +'<div style="margin-top:14px;display:flex;gap:8px;justify-content:center">'
    +'<button onclick="renderFundPickResult()" style="padding:6px 16px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:12px">🔄 重试</button>'
    +'<button onclick="fundPickSort=\'score\';_updateFundPickBtns();setTimeout(renderFundPickResult,0)" style="padding:6px 16px;border-radius:6px;border:1px solid rgba(148,163,184,.3);background:transparent;color:var(--text2);cursor:pointer;font-size:12px">📊 看综合榜</button>'
    +'</div></div>';
});
}

function _showHotFunds(listEl){
// v9.5.120: 前端不缓存，后端文件缓存保证秒回
listEl.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>加载风口数据...</div>';
// 拉近1年排序（数据全，再前端综合计算热度分）
fetch(API_BASE+'/fund-screen?fund_type='+fundPickType+'&sort_by=1y&top_n=50&userId='+getProfileId(),{signal:AbortSignal.timeout(30000)})
.then(r=>r.json()).then(data=>{
const funds=data.funds||[];
if(!funds.length){listEl.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)">暂无数据</div>';return;}
// 综合热度分：近3月(40%) + 近6月(35%) + 近1年(25%)
// 三项都为正才算真风口，任一为负说明期间有明显回撤
const hotFunds=funds
  .filter(f=>{const r=f.returns;return (r['3m']||0)>10&&(r['6m']||0)>15&&(r['1y']||0)>20;})
  .map(f=>{
    const r=f.returns;
    const heat=(r['3m']||0)*0.4+(r['6m']||0)*0.35+(r['1y']||0)*0.25;
    return {...f,heatScore:Math.round(heat*10)/10};
  })
  .sort((a,b)=>b.heatScore-a.heatScore)
  .slice(0,20);
// v9.5.119: 缓存热门结果
// 不写前端缓存，后端自有缓存
_showHotRender(listEl,hotFunds);
}).catch(()=>{listEl.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)">加载失败，请重试</div>';});}
function _showHotRender(listEl,hotFunds){
if(!hotFunds.length){listEl.innerHTML='<div style="text-align:center;padding:30px;color:var(--text2)">当前没有符合"持续风口"条件的基金<br><span style="font-size:11px;opacity:0.6">（需近3月>10%、近6月>15%、近1年>20% 三项同时满足）</span></div>';return;}
let html=`<div style="padding:10px 12px;margin-bottom:12px;background:rgba(239,68,68,.06);border:1px solid rgba(239,68,68,.15);border-radius:10px">
  <div style="font-size:13px;font-weight:700;color:#F87171;margin-bottom:4px">🌬️ 风口观察 — 仅供参考，不构成推荐</div>
  <div style="font-size:11px;color:var(--text2);line-height:1.6">近1年/6月/3月<b>三段均强势</b>的基金，通常集中在热门主题（AI/新能源/科技）。<b style="color:#F87171">高位追涨风险极大</b>，适合了解市场热点，不建议跟风买入。</div>
</div>`;
html+=hotFunds.map((f,i)=>{
const r=f.returns;const r3m=r['3m'];const r6m=r['6m'];const r1y=r['1y'];
const heat=f.heatScore;
const heatColor=heat>50?'#F87171':heat>35?'#FB923C':'#FBBF24';
const riskLabel=heat>50?'极高':heat>35?'高':'中等';
// 持仓标记
const isHolding=f.holding_relation==='🔵 已持仓';
const holdingHtml=isHolding
  ?'<span style="font-size:10px;padding:1px 6px;border-radius:4px;background:rgba(59,130,246,.15);color:#93C5FD;margin-left:4px">🔵 已持仓</span>'
  :'';
return`<div style="padding:12px 0;border-bottom:1px solid rgba(148,163,184,.06);cursor:pointer" onclick="showFundDetailModal('${f.code}','${(f.name||'').replace(/'/g,'')}')">
  <!-- v9.5.49 行1: 序号 + 完整名字 + 热度分大字（右） -->
  <div style="display:flex;align-items:flex-start;gap:8px">
    <span style="font-size:12px;color:var(--text2);font-weight:700;flex-shrink:0;line-height:18px">${i+1}</span>
    <div style="flex:1;min-width:0">
      <div style="font-size:13px;font-weight:600;line-height:1.35;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;word-break:break-all">${f.name}${holdingHtml}</div>
    </div>
    <div style="text-align:right;flex-shrink:0;line-height:1">
      <div style="font-size:15px;font-weight:800;color:${heatColor}">${heat}</div>
      <div style="font-size:9px;color:${heatColor};margin-top:2px">热度分</div>
    </div>
  </div>
  <!-- 行2: 行业 + 风险等级 + 持仓提醒 -->
  <div style="display:flex;align-items:center;gap:6px;margin-top:8px;padding-left:26px;flex-wrap:wrap">
    <span style="font-size:11px;color:var(--text2)">${f.code}</span>
    ${f.industry_tag?`<span style="background:rgba(99,102,241,.15);color:#A5B4FC;padding:1px 6px;border-radius:3px;font-size:10px;font-weight:600">${f.industry_tag}</span>`:''}
    <span style="font-size:10px;color:${heatColor};font-weight:600">⚠️ 追高风险${riskLabel}</span>
  </div>
  <!-- 行3: 三段涨幅横向（紧凑） -->
  <div style="display:flex;gap:12px;margin-top:6px;padding-left:26px;font-size:11px;color:var(--text2)">
    <span>3月 <b style="color:${heatColor}">${r3m!=null?'+'+r3m+'%':'—'}</b></span>
    <span>6月 <b>${r6m!=null?'+'+r6m+'%':'—'}</b></span>
    <span>1年 <b>${r1y!=null?'+'+r1y+'%':'—'}</b></span>
  </div>
  ${isHolding?'<div style="margin-top:6px;padding-left:26px;font-size:10px;color:#93C5FD">📌 你已持有此基金，注意仓位集中风险</div>':''}
</div>${_fundTagsHTML(f)}`;}).join('');
html+=`<div style="text-align:center;margin-top:12px;font-size:11px;color:var(--text2);opacity:0.6">热度分 = 近3月×40% + 近6月×35% + 近1年×25% · 高位入场风险自担</div>`;
listEl.innerHTML=html;
}

function _showFundData(listEl,data){
const funds=data.funds||[];
if(!funds.length){listEl.innerHTML='<div style="text-align:center;padding:20px;color:var(--text2)">暂无符合条件的基金</div>';return}

// v9.5.123: 异步加载回测验证+AI权重进化(首次进入时)
if(!window._backtestLoaded){
  window._backtestLoaded=true;
  fetch(API_BASE+'/backtest/trend-validation',{signal:AbortSignal.timeout(5000)}).then(r=>r.ok?r.json():null).then(d=>{
    if(!d||d.error)return;
    const el=document.getElementById('backtestBadge');if(!el)return;
    const excess=d.dca_comparison?.avg_excess||0;
    const acc=d.trend_accuracy?.avg_overall||0;
    el.innerHTML=`<span style="font-size:10px;padding:2px 8px;border-radius:8px;background:rgba(99,102,241,.08);color:#A5B4FC;cursor:pointer" onclick="alert('回测详情:\\n\\n走势预估综合准确率: ${acc}%\\n双因子定投超额收益: +${excess}%\\n\\n基于8只代表性基金2年历史数据验证')">📊 准确率${acc}% · 超额+${excess}%</span>`;
    el.style.display='inline-flex';
    // AI权重进化标签
    fetch(API_BASE+'/investor/weight-evolution',{signal:AbortSignal.timeout(5000)}).then(r2=>r2.ok?r2.json():null).then(w=>{
      if(!w||!w.evolved)return;
      el.insertAdjacentHTML('beforeend',` <span style="font-size:10px;padding:2px 8px;border-radius:8px;background:rgba(134,239,172,.08);color:#86EFAC;cursor:pointer" onclick="alert('AI权重自进化:\\n${(w.reason||'').replace(/'/g,"\\'")}')">🧬 权重已进化</span>`);
    }).catch(()=>{});
  }).catch(()=>{});
}

// QDII 地区筛选（前端过滤，不重新请求）
const REGION_KEYS={
  '🌍 全球':['全球','环球','世界','国际'],
  '🇺🇸 美国':['美国','标普','纳斯达克','纳100','道琼','S&P','标普500'],
  '🇯🇵 日本':['日本','日经','东证'],
  '🇭🇰 港股':['香港','恒生','港股'],
  '🇪🇺 欧洲':['欧洲','法国','德国','英国','法兰克福'],
  '🇰🇷 韩国':['韩国','KOSPI'],
  '🇮🇳 印度':['印度','孟买','印度尼西亚'],
};
let _qdiiRegion='all'; // 当前选中地区
function _filterByRegion(flist,region){
  if(region==='all')return flist;
  const keys=REGION_KEYS[region]||[];
  return flist.filter(f=>keys.some(k=>f.name.includes(k)));
}
// v9.5.119: 把闭包内函数暴露到 window，供外部代码调用
window._renderFundList = _renderFundList;
window._buildFundTagPool = _buildFundTagPool;
window._showWishlist = _showWishlist;
window._renderCompareBar = _renderCompareBar;

// v9.5.50 方案A: 多维度标签云生成器
// 返回 [{kind, label, color, bg, title}] 数组，每只基金最多展示 4 个
// kind 优先级：hold > industry > theme > policy > style > event > risk
function _buildFundTagPool(f){
  const tags = [];
  const name = f.name || '';
  const r = f.returns || {};
  const r1y = r['1y']; const r3m = r['3m']; const r6m = r['6m']; const r3y = r['3y'];

  // 1. 持仓关系（最高优先，绑定 + 提醒）
  if(f.holding_relation==='🔵 已持仓'){
    tags.push({kind:'hold', label:'🔵 已持有', color:'#93C5FD', bg:'rgba(59,130,246,.18)', title:'你已持有此基金'});
  }

  // 2. 行业/赛道徽章（沿用后端的 industry_tag）
  if(f.industry_tag){
    tags.push({kind:'industry', label:f.industry_tag, color:'#A5B4FC', bg:'rgba(99,102,241,.18)', title:'行业/赛道'});
  }

  // 3. 主题标签（从名字提取，最多 2 个补充行业徽章未覆盖的细分赛道）
  // v9.5.58: 关键字大幅扩充，覆盖宽基/混合/股票型基金的兜底识别
  const themeRules = [
    {kw:['AI算力','算力','智算','人工智能','人工智能50','AIGC'], label:'🤖 AI算力'},
    {kw:['半导体','芯片','存储','集成电路','半导体材料','光刻','SOC'], label:'💎 半导体'},
    {kw:['新能源车','电动车','智能汽车','锂电','智能驾驶'], label:'🚗 新能源车'},
    {kw:['光伏','风电','清洁能源','绿电','碳中和','新能源','储能'], label:'☀️ 新能源'},
    {kw:['军工','航天','防务','国防','航空航天'], label:'🛡️ 军工'},
    {kw:['医药','创新药','生物科技','医疗','医械','生物医药','医疗器械'], label:'💊 医药'},
    {kw:['消费','白酒','食品饮料','餐饮','食品'], label:'🍷 消费'},
    {kw:['煤炭','石油','油气','资源','有色'], label:'⛽ 能源资源'},
    {kw:['银行','金融','保险','券商','证券'], label:'🏦 金融'},
    {kw:['黄金','贵金属','金条','金ETF'], label:'🥇 黄金'},
    {kw:['红利','低波','价值','蓝筹','大盘价值','质量'], label:'💰 红利价值'},
    {kw:['通信','5G','光通信'], label:'📡 通信5G'},
    {kw:['数字基建','数据中心','算力基建','信创','云计算','大数据'], label:'🏗️ 数字基建'},
    {kw:['高端制造','智能制造','机器人','工业'], label:'🏭 高端制造'},
    {kw:['物流','航运','港口','交运'], label:'🚢 物流交运'},
    {kw:['养老','银发'], label:'👴 养老'},
    // v9.5.58 新增：宽基/特征识别
    {kw:['沪深300','上证50','中证300','大盘'], label:'📊 大盘宽基'},
    {kw:['中证500','中证1000','小盘','中盘'], label:'📈 中小盘'},
    {kw:['创业板','创业50','创成长'], label:'🚀 创业板'},
    {kw:['科创','科创50','科创100'], label:'🔬 科创板'},
    {kw:['北证','北交所'], label:'🆕 北交所'},
    {kw:['沪港深','互联互通','两地'], label:'🌐 沪港深'},
    {kw:['全球','环球','海外','QDII'], label:'🌍 全球配置'},
    {kw:['国企','央企','国资'], label:'🏛️ 国企央企'},
    {kw:['ESG','可持续','社会责任'], label:'🌱 ESG'},
    {kw:['量化','指数增强','增强'], label:'📐 指数增强'},
    {kw:['股票','股票型','积极'], label:'📈 主动股票'},
    {kw:['混合','灵活配置','偏股'], label:'⚖️ 混合配置'},
  ];
  // v9.5.124: seenThemes 模糊去重 — 如果 industry_tag 含某 theme 关键词，预排除该 theme label
  const seenThemes = new Set([f.industry_tag].filter(Boolean));
  const _indTag = (f.industry_tag||'').toLowerCase();
  for(const rule of themeRules){
    if(rule.kw.some(k=>_indTag.includes(k.toLowerCase()))){
      seenThemes.add(rule.label);
    }
  }
  for(const rule of themeRules){
    if(rule.kw.some(k=>name.includes(k))){
      if(seenThemes.has(rule.label)) continue;
      tags.push({kind:'theme', label:rule.label, color:'#F9A8D4', bg:'rgba(244,114,182,.15)', title:'细分主题'});
      seenThemes.add(rule.label);
      if(tags.filter(t=>t.kind==='theme').length >= 2) break;
    }
  }

  // 4. 地域/海外标签
  const regionRules = [
    {kw:['纳指','纳斯达克','纳100','S&P','标普500','标普','美股','美国'], label:'🇺🇸 美股'},
    {kw:['日本','日经','东证','日股'], label:'🇯🇵 日股'},
    {kw:['港股','恒生','香港','沪港','深港','H股'], label:'🇭🇰 港股'},
    {kw:['印度','孟买'], label:'🇮🇳 印度'},
    {kw:['越南','胡志明'], label:'🇻🇳 越南'},
    {kw:['德国','欧洲','法国'], label:'🇪🇺 欧洲'},
  ];
  for(const rule of regionRules){
    if(rule.kw.some(k=>name.includes(k))){
      tags.push({kind:'region', label:rule.label, color:'#5EEAD4', bg:'rgba(20,184,166,.15)', title:'海外市场'});
      break;
    }
  }

  // 5. 政策受益（如果后端有 policy_tags / policy_badges）
  if(f.policy_tags && Array.isArray(f.policy_tags) && f.policy_tags.length){
    tags.push({kind:'policy', label:'🏛️ 政策受益', color:'#D8B4FE', bg:'rgba(168,85,247,.15)', title:'政策利好：'+f.policy_tags.join(', ')});
  }

  // 6. 风格/属性标签
  const fee = parseFloat(f.fee);
  if(!isNaN(fee) && fee < 0.5){
    tags.push({kind:'style', label:'💵 低费率', color:'#9CA3AF', bg:'rgba(75,85,99,.4)', title:'管理费率<0.5%'});
  }
  if(r3y!=null && r3y > 50){
    tags.push({kind:'style', label:'⭐ 长跑优秀', color:'#FBBF24', bg:'rgba(245,158,11,.15)', title:`近3年涨${r3y.toFixed(0)}%`});
  }
  // v9.5.89: 规模警戒线 — 过大/过小都标注
  if(f.scale_billion != null){
    if(f.scale_billion < 2){
      tags.push({kind:'scale_warn', label:'⚠️ 规模过小', color:'#FCA5A5', bg:'rgba(239,68,68,.15)', title:`规模仅${f.scale_billion}亿，有清盘风险（<2亿警戒线）`});
    } else if(f.scale_billion > 500){
      tags.push({kind:'scale_warn', label:'🐘 超大规模', color:'#9CA3AF', bg:'rgba(75,85,99,.35)', title:`规模${f.scale_billion}亿，超大规模可能影响灵活性和超额收益`});
    } else if(f.scale_billion > 200){
      tags.push({kind:'style', label:'🐘 大规模', color:'#9CA3AF', bg:'rgba(75,85,99,.3)', title:`规模${f.scale_billion}亿，主动管理alpha受限`});
    }
  }

  // v9.5.89: 基金经理换届 tag
  if(f.manager_change){
    tags.push({kind:'mgr_change', label:'🔄 经理新任', color:'#FCA5A5', bg:'rgba(239,68,68,.13)', title:f.manager_warn||'近6个月基金经理变更，历史业绩参考价值降低'});
  }

  // 7. 事件/警示标签（v9.5.58 阈值优化 — 近1年大涨也算警示）
  if(f.has_dividend_recent){
    tags.push({kind:'event', label:`💰 ${f.dividend_label||'近期分红'}`, color:'#FDA4AF', bg:'rgba(245,158,11,.15)', title:'1年内有分红/拆分，注意成本'});
  }
  // v9.5.58: 三档警示
  if(r3m!=null && r3m > 30){
    tags.push({kind:'event', label:'⚠️ 短期过热', color:'#FCA5A5', bg:'rgba(239,68,68,.18)', title:`近3月涨${r3m.toFixed(0)}%，警惕追高`});
  } else if(r1y!=null && r1y > 50){
    tags.push({kind:'event', label:'⚠️ 高位区间', color:'#FCA5A5', bg:'rgba(239,68,68,.15)', title:`近1年涨${r1y.toFixed(0)}%，处历史高位`});
  } else if(r1y!=null && r1y > 30){
    tags.push({kind:'event', label:'📈 强势上涨', color:'#FBBF24', bg:'rgba(245,158,11,.15)', title:`近1年涨${r1y.toFixed(0)}%，注意节奏`});
  }
  if(r1y!=null && r1y < -10){
    tags.push({kind:'event', label:'📉 深度回调', color:'#86EFAC', bg:'rgba(34,197,94,.15)', title:`近1年跌${Math.abs(r1y).toFixed(0)}%，可能潜伏机会`});
  }

  // 8. 风险等级（v9.5.58 降为最低优先级，避免挤占主题/警示 slot）
  const riskMap = {
    low:  {label:'🟢 低波', color:'#86EFAC', bg:'rgba(34,197,94,.15)', title:'低波动'},
    mid:  {label:'🟡 中波', color:'#FBBF24', bg:'rgba(245,158,11,.15)', title:'中等波动'},
    high: {label:'🔴 高波', color:'#FCA5A5', bg:'rgba(239,68,68,.15)', title:'高波动，主题/海外类'},
  };
  const rk = riskMap[f.risk_level] || riskMap.mid;
  tags.push({kind:'risk', label:rk.label, color:rk.color, bg:rk.bg, title:rk.title});

  // 优先级排序（v9.5.58 调整：事件警示前置，style 降级，risk 最低）
  // 排序：hold/industry/theme/region/event/policy/risk/style
  const priority = {hold:0, industry:1, theme:2, region:3, event:4, scale_warn:4, mgr_change:4, policy:5, risk:6, style:7};
  tags.sort((a,b)=> (priority[a.kind]||9) - (priority[b.kind]||9));

  return tags;
}

function _renderFundList(flist){
  // v9.5.42 P5 自定义筛选（fee/r1y/r3y/scale）
  const ff = window._fundFilter || {};
  if(Object.values(ff).some(v=>v!=null)){
    flist = flist.filter(f=>{
      const r=f.returns||{};
      if(ff.fee_max!=null){ const fee=parseFloat(f.fee); if(!isNaN(fee) && fee>ff.fee_max) return false; }
      if(ff.r1y_min!=null && (r['1y']==null || r['1y']<ff.r1y_min)) return false;
      if(ff.r3y_min!=null && (r['3y']==null || r['3y']<ff.r3y_min)) return false;
      if(ff.scale_min!=null && (f.scale_billion==null || f.scale_billion<ff.scale_min)) return false;
      return true;
    });
  }
  // v9.5.87: 组合多样性保障 — 同一 industry_tag 最多展示 3 只，避免榜单全是同赛道
  // v9.5.94: 阈值从 2 提到 3，配合 top_n=30，过滤后能保证15+只
  // 仅在有 holding_relation 数据时应用（说明是个人化视图）
  if(flist.length>0 && flist[0].holding_relation!==undefined){
    const tagCount={};
    flist = flist.filter(f=>{
      const tag = f.industry_tag || f.timing_label || '其他';
      tagCount[tag]=(tagCount[tag]||0)+1;
      return tagCount[tag]<=3; // 同赛道最多保留前3只
    });
  }
  if(!flist.length) return '<div style="text-align:center;padding:30px;color:var(--text2);font-size:12px">⚙️ 当前筛选条件无匹配基金 — 点 ⚙️ 调整或清空</div>';

  return flist.map((f,i)=>{
    const r1y=f.returns['1y'];
    const r1yColor = r1y>0 ? 'var(--color-bull,#FF6B6B)' : 'var(--color-bear,#00E5A0)';

    // v9.5.50 方案A: 构建标签云，最多展示 4 个（保留持仓+行业+1主题+1风险）
    const allTags = _buildFundTagPool(f);
    const showTags = allTags.slice(0, 4);
    const tagsHtml = showTags.map(t =>
      `<span style="display:inline-flex;align-items:center;font-size:10px;padding:2px 7px;border-radius:4px;background:${t.bg};color:${t.color};font-weight:500;line-height:1.4;white-space:nowrap" title="${t.title}">${t.label}</span>`
    ).join('');

    // v9.5.86: 个人化标签区（潜力/缺口方向/净值百分位/买入信号/相关系数）
    const personalHtml = _fundTagsHTML(f);

    // AI 点评
    let aiCommentText = '';
    if (f.aiComment) {
      aiCommentText = f.aiComment.replace(/^[\s\S]*?(?:逐只思考[：:]?\s*|思考[：:]?\s*|分析[：:]?\s*)/,'').trim();
    }
    if(!aiCommentText && f.reason) aiCommentText = f.reason;
    const commentHtml = aiCommentText && aiCommentText.length > 2
      ? `<div style="font-size:11px;color:#A5B4FC;line-height:1.5;margin-top:6px;padding-left:22px">🤖 ${aiCommentText}</div>`
      : '';

    const subInfo = [
      f.code,
      `费率${f.fee||'-'}`,
      `评分${f.score}`,
      f.timing_label || '',
    ].filter(Boolean).join(' · ');

    // 操作按钮
    const inCompare = (window._compareSet||new Set()).has(f.code);
    const compareBtn = `<button onclick="event.stopPropagation();_toggleCompare('${f.code}','${(f.name||'').replace(/'/g,'')}')" style="padding:2px 7px;font-size:10px;font-weight:600;border:1px solid ${inCompare?'#818CF8':'rgba(148,163,184,.3)'};border-radius:4px;background:${inCompare?'rgba(99,102,241,.18)':'transparent'};color:${inCompare?'#818CF8':'#9aa1ac'};cursor:pointer;flex-shrink:0" title="${inCompare?'已加入对比':'加入对比（最多3只）'}">${inCompare?'✓':'+'}</button>`;
    const wished = _isWished(f.code);
    const wishBtn = `<button onclick="event.stopPropagation();_toggleWish('${f.code}','${(f.name||'').replace(/'/g,'')}')" style="padding:2px 5px;font-size:13px;border:none;background:transparent;cursor:pointer;flex-shrink:0" title="${wished?'从心愿单移除':'加入心愿单'}">${wished?'❤️':'🤍'}</button>`;

    return `<div style="padding:12px 0;border-bottom:1px solid rgba(148,163,184,.06);cursor:pointer" onclick="showFundDetailModal('${f.code}','${(f.name||'').replace(/'/g,'')}')">
      <!-- 行1: 序号 + 名字（2行完整） + 涨幅大字（右上） -->
      <div style="display:flex;align-items:flex-start;gap:8px">
        <span style="font-size:12px;color:var(--text2);font-weight:700;flex-shrink:0;line-height:18px;min-width:14px">${i+1}</span>
        <div style="flex:1;min-width:0">
          <div style="font-size:13px;font-weight:600;line-height:1.35;color:var(--text-primary,#F0F2F7);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;word-break:break-all">${f.name}</div>
          <!-- 行2: 标签云（最多4个，按优先级排序） -->
          <div style="margin-top:6px;display:flex;flex-wrap:wrap;gap:4px">${tagsHtml}</div>
        </div>
        <div style="text-align:right;flex-shrink:0;line-height:1">
          <div style="font-size:16px;font-weight:800;color:${r1yColor}">${r1y!=null?(r1y>0?'+':'')+r1y+'%':'—'}</div>
          <div style="font-size:9px;color:var(--text-tertiary,#7A8499);margin-top:2px">近1年</div>
        </div>
      </div>
      ${personalHtml}
      ${commentHtml}
      <!-- 行3: 代码/费率/评分（左灰字） + 操作按钮组（右） -->
      <div style="display:flex;align-items:center;justify-content:space-between;margin-top:8px;padding-left:22px;gap:8px">
        <span style="font-size:10px;color:var(--text-tertiary,#7A8499);flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${subInfo}</span>
        <div style="display:flex;gap:4px;flex-shrink:0;align-items:center">
          ${wishBtn}
          ${compareBtn}
          <button onclick="event.stopPropagation();_showFundKlineModal('${f.code}','${(f.name||'').replace(/'/g,'')}')" style="padding:2px 8px;font-size:10px;border:1px solid rgba(148,163,184,.3);border-radius:4px;background:transparent;color:#9aa1ac;cursor:pointer">📈 K线</button>
          ${_buyMemoIndicator(f.code)}
        </div>
      </div>
    </div>`;
  }).join('');
}

// v9.5.39 P7 心愿单 tab 渲染
function _showWishlist(listEl){
  const wishlist = _wishlistFunds();
  if(!wishlist.length){
    listEl.innerHTML = `<div style="text-align:center;padding:40px 20px;color:var(--text2)">
      <div style="font-size:32px;margin-bottom:8px">🤍</div>
      <div style="font-size:13px">心愿单空空</div>
      <div style="font-size:11px;color:var(--text-tertiary,#7A8499);margin-top:6px">在其他 tab 点 🤍 加入观察名单<br>每周回头看你的判断准不准</div>
    </div>`;
    return;
  }
  listEl.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text2)"><div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 8px;border-width:2px"></div>加载心愿单...</div>';
  // v9.8.7: 用 codes 参数精确查询（替代 top_n=2000 全量筛选）
  const wishCodes = wishlist.map(w=>w.code).join(',');
  fetch(API_BASE+'/fund-screen?codes='+wishCodes+'&userId='+getProfileId(),{signal:AbortSignal.timeout(15000)})
    .then(r=>r.ok?r.json():{funds:[]})
    .then(d=>{
      const all = d.funds || [];
      const codes = new Set(wishlist.map(x=>x.code));
      const matched = all.filter(f=>codes.has(f.code));
      // 没匹配上的基金（QDII/小盘）也显示，但没收益数据
      const missing = wishlist.filter(w=>!matched.some(f=>f.code===w.code))
        .map(w=>({code:w.code, name:w.name, returns:{}, fee:'-', score:0}));
      const all_in_wish = [...matched, ...missing];
      if(!all_in_wish.length){
        listEl.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text2)">心愿单基金都不在主榜中，请稍后</div>';
        return;
      }
      listEl.innerHTML = `<div style="padding:10px 12px;margin-bottom:10px;background:rgba(244,114,182,.08);border:1px solid rgba(244,114,182,.15);border-radius:8px;font-size:11px;color:#F472B6">
        ❤️ 心愿单 · ${wishlist.length} 只 · 点 ❤️ 移除 · 每周关注涨幅看判断
      </div>` + _renderFundList(all_in_wish);
    }).catch(()=>{
      listEl.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text2)">加载失败<br><button onclick="renderFundPickResult()" style="margin-top:8px;padding:6px 16px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:12px">🔄 重试</button></div>';
    });
}

// v9.5.39 P2 多选对比 — 浮动条 + 对比表
if(!window._compareSet) window._compareSet = new Set();
if(!window._compareData) window._compareData = {};  // code → fund obj

window._toggleCompare = function(code, name){
  if(window._compareSet.has(code)){
    window._compareSet.delete(code);
    delete window._compareData[code];
  } else {
    if(window._compareSet.size >= 3){
      alert('最多对比 3 只，请先取消一只');
      return;
    }
    window._compareSet.add(code);
    // 从当前列表数据捞 fund 对象
    const allItems = document.querySelectorAll('#fundPickList [onclick*="showFundDetailModal"]');
    // 不依赖 DOM，靠 cache
    const cacheKey = 'fund_screen_'+fundPickType+'_'+fundPickSort;
    const cached = getCached(cacheKey);
    const fund = (cached?.funds||[]).find(f=>f.code===code);
    if(fund) window._compareData[code] = fund;
    else window._compareData[code] = {code, name, returns:{}};
  }
  _renderCompareBar();
  if(typeof renderFundPickResult==='function') renderFundPickResult();
};

window._clearCompare = function(){
  window._compareSet.clear();
  window._compareData = {};
  _renderCompareBar();
  if(typeof renderFundPickResult==='function') renderFundPickResult();
};

function _renderCompareBar(){
  let bar = document.getElementById('compareBar');
  if(!bar){
    bar = document.createElement('div');
    bar.id = 'compareBar';
    bar.style.cssText = 'position:fixed;left:12px;right:12px;bottom:72px;z-index:200;background:rgba(99,102,241,.22);backdrop-filter:blur(10px);border:1px solid rgba(99,102,241,.4);border-radius:10px;padding:10px 14px;display:flex;align-items:center;gap:10px;box-shadow:0 4px 16px rgba(0,0,0,.3)';
    document.body.appendChild(bar);
  }
  const n = window._compareSet.size;
  if(n === 0){ bar.remove(); return; }
  bar.innerHTML = `<span style="font-size:11px;color:#C7D2FE;flex:1">✓ 已选 ${n} 只${n>=2?'':'（至少选 2 只）'}</span>
    <button onclick="_showCompareModal()" style="padding:5px 12px;font-size:11px;border:1px solid #818CF8;border-radius:6px;background:${n>=2?'#818CF8':'rgba(99,102,241,.25)'};color:${n>=2?'#fff':'#9CA3AF'};cursor:${n>=2?'pointer':'not-allowed'};font-weight:600" ${n<2?'disabled':''}>📊 对比</button>
    <button onclick="_clearCompare()" style="padding:5px 10px;font-size:11px;border:1px solid rgba(148,163,184,.3);border-radius:6px;background:transparent;color:#9aa1ac;cursor:pointer">清空</button>`;
}

// v9.5.40: 修复对比表显示 "—" 问题 — 打开时异步拉详情接口补全数据
window._showCompareModal = async function(){
  const codes = Array.from(window._compareSet);
  if(codes.length < 2){ alert('请至少选 2 只基金'); return; }

  const o = document.createElement('div');
  o.className = 'modal-overlay';
  o.onclick = e => { if(e.target===o) o.remove(); };

  // 1) 先打开 modal 显示骨架屏
  o.innerHTML = `<div class="modal-sheet" onclick="event.stopPropagation()" style="max-height:85vh;display:flex;flex-direction:column">
    <div class="modal-handle"></div>
    <div class="modal-title">📊 基金对比 (${codes.length} 只)</div>
    <div id="compareBody" style="flex:1;overflow-y:auto;padding:20px 4px;text-align:center;color:var(--text2);font-size:12px">
      <div class="loading-spinner" style="width:24px;height:24px;margin:0 auto 10px;border-width:2px"></div>
      正在拉取基金详情对比数据...
    </div>
    <div style="display:flex;gap:8px;margin-top:8px">
      <button class="mb-btn mb-btn--secondary" style="flex:1" onclick="document.querySelector('.modal-overlay')?.remove()">关闭</button>
      <button class="mb-btn mb-btn--primary" style="flex:1" onclick="_clearCompare();document.querySelector('.modal-overlay')?.remove()">清空选择</button>
    </div>
  </div>`;
  document.body.appendChild(o);

  // 2) 并行拉详情，合并到 _compareData
  const detailPromises = codes.map(async code => {
    const base = window._compareData[code] || {code, name:code, returns:{}};
    try{
      const r = await fetch(API_BASE + '/fund/detail/' + code, { signal: AbortSignal.timeout(12000) });
      if(!r.ok) return base;
      const d = await r.json();
      // 合并：详情数据优先
      const merged = {
        code: code,
        name: d.fullName || d.name || base.name || code,
        returns: Object.assign({}, base.returns||{}, d.returns||{}),
        // 费率：优先用 d.fee（管理费率，长期持有可比），降级 base.fee
        fee: d.fee || base.fee,
        // risk_level / industry_tag / score 从原 cache 取（详情接口未必有）
        risk_level: base.risk_level || d.risk_level,
        industry_tag: base.industry_tag || d.industry_tag,
        score: base.score || d.score,
        scale_billion: d.scale_billion,
        manager_tenure: d.manager?.tenure_years,
      };
      return merged;
    }catch(e){
      return base;
    }
  });

  const funds = await Promise.all(detailPromises);

  // 3) 找最佳值
  const r1yVals = funds.map(f=>f.returns?.['1y']).filter(v=>v!=null);
  const r3yVals = funds.map(f=>f.returns?.['3y']).filter(v=>v!=null);
  const r6mVals = funds.map(f=>f.returns?.['6m']).filter(v=>v!=null);
  const r3mVals = funds.map(f=>f.returns?.['3m']).filter(v=>v!=null);
  const feeVals = funds.map(f=>parseFloat(f.fee)).filter(v=>!isNaN(v));
  const scaleVals = funds.map(f=>f.scale_billion).filter(v=>v!=null);
  const tenureVals = funds.map(f=>f.manager_tenure).filter(v=>v!=null);
  const scoreVals = funds.map(f=>f.score).filter(v=>v!=null);

  const bestR1y = r1yVals.length ? Math.max(...r1yVals) : null;
  const bestR3y = r3yVals.length ? Math.max(...r3yVals) : null;
  const bestR6m = r6mVals.length ? Math.max(...r6mVals) : null;
  const bestR3m = r3mVals.length ? Math.max(...r3mVals) : null;
  const bestFee = feeVals.length ? Math.min(...feeVals) : null;
  const bestScale = scaleVals.length ? Math.max(...scaleVals) : null;
  const bestTenure = tenureVals.length ? Math.max(...tenureVals) : null;
  const bestScore = scoreVals.length ? Math.max(...scoreVals) : null;

  const rowsHtml = (label, getter, fmt, bestVal) => {
    return `<tr style="border-top:1px solid rgba(148,163,184,.08)">
      <td style="padding:8px 6px;color:var(--text-tertiary,#7A8499);font-size:11px;white-space:nowrap">${label}</td>
      ${funds.map(f=>{
        const v = getter(f);
        const isBest = v != null && bestVal != null && v === bestVal;
        const display = fmt(v);
        return `<td style="text-align:right;padding:8px 6px;font-size:12px;font-weight:${isBest?'700':'400'};color:${isBest?'#FFB755':'var(--text-default,#D8DCE5)'}">${display}${isBest?' 🏆':''}</td>`;
      }).join('')}
    </tr>`;
  };

  // 4) 表头：基金名 2 行展示，不截断
  const headHtml = `<tr>
    <td style="padding:8px 6px;width:60px"></td>
    ${funds.map(f=>{
      const fullName = f.name || f.code;
      return `<td style="text-align:right;padding:8px 6px;font-size:11px;font-weight:600;color:var(--text-default,#D8DCE5);line-height:1.3;vertical-align:bottom" title="${fullName}">
        <div style="word-break:break-all;white-space:normal;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;max-height:2.6em">${fullName}</div>
      </td>`;
    }).join('')}
  </tr>`;

  const body = document.getElementById('compareBody');
  if(!body) return; // modal 已被关闭
  body.style.cssText = 'flex:1;overflow-y:auto;padding:0 4px;text-align:left;color:inherit';
  body.innerHTML = `<table style="width:100%;border-collapse:collapse;table-layout:fixed">
      <thead>${headHtml}</thead>
      <tbody>
        ${rowsHtml('代码', f=>f.code, v=>v||'—', null)}
        ${rowsHtml('风险', f=>f.risk_level, v=>({low:'🟢 低',mid:'🟡 中',high:'🔴 高'})[v]||'—', null)}
        ${rowsHtml('行业', f=>f.industry_tag, v=>v||'—', null)}
        ${rowsHtml('近 1 年', f=>f.returns?.['1y'], v=>v!=null?(v>0?'+':'')+Number(v).toFixed(1)+'%':'—', bestR1y)}
        ${rowsHtml('近 3 年', f=>f.returns?.['3y'], v=>v!=null?(v>0?'+':'')+Number(v).toFixed(1)+'%':'—', bestR3y)}
        ${rowsHtml('近 6 月', f=>f.returns?.['6m'], v=>v!=null?(v>0?'+':'')+Number(v).toFixed(1)+'%':'—', bestR6m)}
        ${rowsHtml('近 3 月', f=>f.returns?.['3m'], v=>v!=null?(v>0?'+':'')+Number(v).toFixed(1)+'%':'—', bestR3m)}
        ${rowsHtml('费率', f=>{const v=parseFloat(f.fee); return isNaN(v)?null:v;}, v=>v!=null?v.toFixed(2)+'%':'—', bestFee)}
        ${rowsHtml('规模', f=>f.scale_billion, v=>v!=null?Number(v).toFixed(1)+'亿':'—', bestScale)}
        ${rowsHtml('经理任期', f=>f.manager_tenure, v=>v!=null?Number(v).toFixed(1)+'年':'—', bestTenure)}
        ${rowsHtml('评分', f=>f.score, v=>v!=null?Number(v).toFixed(1):'—', bestScore)}
      </tbody>
    </table>
    <div style="margin-top:12px;padding:10px 12px;background:rgba(99,102,241,.08);border-radius:8px;font-size:11px;color:#A5B4FC;line-height:1.6">
      💡 提示：🏆 标记代表该维度最优。<br>
      • 收益高 ≠ 适合自己，结合风险等级 + 费率 + 持有时长选择<br>
      • 长期持有看 3 年/费率，短期看 1 年/6 月走势<br>
      • 评分综合考虑了收益、稳定性、费率、成熟度
    </div>`;
};
// QDII时生成地区筛选条HTML
function _qdiiRegionBar(){
  if(fundPickType!=='qdii')return '';
  const regions=['all',...Object.keys(REGION_KEYS)];
  return `<div id="qdiiRegionBar" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px;padding:8px 0;border-bottom:1px solid rgba(148,163,184,.06)">
  <span style="font-size:11px;color:var(--text2);align-self:center;white-space:nowrap">地区：</span>
  ${regions.map(r=>`<button class="section-tab ${_qdiiRegion===r?'active':''}" style="font-size:10px;padding:3px 8px" onclick="window._qdiiRegionFilter('${r}')">${r==='all'?'全部':r}</button>`).join('')}
  </div>`;
}
// 挂载地区筛选回调
window._qdiiRegionFilter=function(region){
  _qdiiRegion=region;
  const el=document.getElementById('qdiiRegionBar');
  if(el)el.querySelectorAll('button').forEach(b=>{b.classList.toggle('active',b.textContent===(region==='all'?'全部':region));});
  const listArea=document.getElementById('qdiiListArea');
  if(listArea){
    const filtered=_filterByRegion(funds,region);
    listArea.innerHTML=filtered.length?_renderFundList(filtered):'<div style="text-align:center;padding:16px;color:var(--text2);font-size:12px">该地区暂无符合条件的基金</div>';
    // 重新注册弹窗
    filtered.forEach(f=>{
      const r=f.returns;
      setExplain('fund_'+f.code,f.name+' ('+f.code+')',
      '综合评分：'+f.score+'\n近3月：'+(r['3m']!=null?r['3m']+'%':'—')+'\n近1年：'+(r['1y']!=null?r['1y']+'%':'—')+'\n近3年：'+(r['3y']!=null?r['3y']+'%':'—')+'\n费率：'+(f.fee||'—'),
      {type:'fund',code:f.code,name:f.name,score:f.score,fee:f.fee||'',returns:r});
    });
  }
};
// 大盘时机横幅（选基版，加 regime_hint 和 style_timing 行业流动提示）
const mt=data.market_timing||{};
// regime_hint 来自接口（今日加了这个字段）
const regimeHintFund = mt.regime_hint || '';
// style_timing：高位/低位行业
const stFund = data.style_timing || {};
const stylesFund = stFund.styles || [];
const highFund = stylesFund.filter(s=>(s.avg_3m||0)>12).map(s=>s.style).slice(0,3);
const lowFund  = stylesFund.filter(s=>(s.avg_3m||0)<-2).map(s=>s.style).slice(0,3);
let styleHintFund = '';
if(highFund.length||lowFund.length){
  const p=[];
  if(highFund.length) p.push(`高位: ${highFund.join('/')}`);
  if(lowFund.length)  p.push(`低估: ${lowFund.join('/')}`);
  styleHintFund = p.join('　');
}
const timingBanner=mt.signal?`<div style="padding:10px 12px;margin-bottom:12px;background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.12);border-radius:10px">
  <div style="display:flex;align-items:center;gap:8px">
    <span style="font-size:18px">${mt.signal}</span>
    <div>
      <div style="font-size:12px;font-weight:700;color:var(--text1)">大盘时机: ${mt.verdict}</div>
      <div style="font-size:11px;color:var(--text2)">${mt.detail}</div>
    </div>
  </div>
  ${regimeHintFund?`<div style="font-size:11px;color:var(--color-brand-400,#FFB755);margin-top:4px;line-height:1.6">${regimeHintFund}</div>`:''}
  ${styleHintFund?`<div style="font-size:10px;color:var(--text2);margin-top:2px;opacity:0.8">${styleHintFund}</div>`:''}
</div>`:'';
const qualityNote=data.quality_note?`<div style="font-size:11px;color:var(--green);margin-bottom:6px;padding:4px 8px;background:rgba(16,185,129,.06);border-radius:6px">🛡️ ${data.quality_note}</div>`:'';
const sortWarn=(data.sort==='1y'||data.sort==='ytd')?`<div style="font-size:11px;color:#F59E0B;margin-bottom:8px;padding:6px 10px;background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.2);border-radius:8px">⚠️ 当前按收益排序——显示的是近期涨幅最高的基金，高度集中于AI/科技行业ETF。追高风险极大，建议切换「综合评分」查看均衡推荐</div>`:'';
const qdiiNote=fundPickType==='qdii'?`<div style="font-size:11px;color:var(--text2);margin-bottom:8px;padding:6px 10px;background:rgba(99,102,241,.06);border-radius:8px">🌐 <b>海外基金</b>：用人民币购买，投资境外市场（美股/日股/港股等）。受汇率影响，涨跌与A股相对独立，可做全球分散配置。</div>`:'';
_qdiiRegion='all';  // 切换类型时重置地区筛选
listEl.innerHTML=`${timingBanner}${qualityNote}${sortWarn}${qdiiNote}
<div style="font-size:11px;color:var(--text2);margin-bottom:8px">共筛选 ${data.total} 只基金，显示 TOP ${funds.length}</div>
${_qdiiRegionBar()}
<div id="qdiiListArea">${_renderFundList(funds)}</div>
<div style="text-align:center;margin-top:12px"><button class="action-btn secondary" style="display:inline-block;min-width:auto;padding:10px 24px" onclick="renderFundPickResult()">🔄 刷新</button></div>`;
// 注册每只基金的白话弹窗
funds.forEach(f=>{
const r=f.returns;
setExplain('fund_'+f.code,f.name+' ('+f.code+')',
'📊 综合评分：'+f.score+'\n\n📈 收益表现：\n• 近3月：'+(r['3m']!=null?r['3m']+'%':'—')+'\n• 近6月：'+(r['6m']!=null?r['6m']+'%':'—')+'\n• 近1年：'+(r['1y']!=null?r['1y']+'%':'—')+'\n• 近3年：'+(r['3y']!=null?r['3y']+'%':'—')+'\n• 今年来：'+(r.ytd!=null?r.ytd+'%':'—')+'\n\n💰 费率：'+(f.fee||'—')+'\n\n💡 评分方法：近1年35%+近3年25%+近6月20%+近3月10%+费率加减分。仅供参考，不构成投资建议。',
{type:'fund',code:f.code,name:f.name,score:f.score,fee:f.fee||'',returns:r})
})}

// ============================================================
// v9.5.89: 买入理由备忘录（📝 记录每次买入决策，本地存储）
// ============================================================

function _buyMemoKey(code){ return _uk('fund_buy_memo_'+code); }

function _getBuyMemos(code){
  try{ return JSON.parse(localStorage.getItem(_buyMemoKey(code)))||[]; }catch{ return []; }
}

function _saveBuyMemos(code, arr){
  try{ localStorage.setItem(_buyMemoKey(code), JSON.stringify(arr.slice(0,20))); }catch{}
}

// 在基金卡片右下角显示📝图标（若有备忘）
function _buyMemoIndicator(code){
  const memos = _getBuyMemos(code);
  if(!memos.length) return '';
  return `<span onclick="event.stopPropagation();_showBuyMemoModal('${code}','')" title="查看买入备忘录（${memos.length}条）" style="font-size:12px;cursor:pointer;opacity:0.8">📝</span>`;
}

window._showBuyMemoModal = function(code, name){
  const memos = _getBuyMemos(code);
  const today = new Date().toISOString().slice(0,10);
  // 尝试从当前榜单缓存里取个人化标签作为预填
  let prefill = '';
  try{
    const cacheKey = 'fund_screen_'+fundPickType+'_'+fundPickSort;
    const cached = getCached(cacheKey);
    const f = (cached?.funds||[]).find(x=>x.code===code);
    if(f){
      const parts = [];
      if(f.nav_percentile!=null) parts.push(`净值位置 ${f.nav_pct_label||f.nav_percentile+'%'}`);
      if(f.potential) parts.push(`潜力 ${f.potential.label}`);
      if(f.price_signal&&f.price_signal.label) parts.push(f.price_signal.label);
      if(f.correlation_label) parts.push(`与持仓相关 ${f.correlation_label}`);
      if(parts.length) prefill = parts.join(' · ');
    }
  }catch{}

  const histHtml = memos.slice(0,5).map((m,i)=>`
    <div style="padding:8px 10px;background:rgba(255,255,255,.03);border-radius:6px;margin-bottom:6px;position:relative">
      <div style="font-size:10px;color:#6B7280;margin-bottom:3px">${m.date} ${m.tags||''}</div>
      <div style="font-size:12px;color:#D1D5DB;line-height:1.5">${m.note}</div>
      <button onclick="(function(){const arr=_getBuyMemos('${code}');arr.splice(${i},1);_saveBuyMemos('${code}',arr);document.querySelector('.modal-overlay')?.remove();_showBuyMemoModal('${code}','${(name||'').replace(/'/g,'\\\'')}')})()" style="position:absolute;top:6px;right:8px;border:none;background:transparent;color:#6B7280;font-size:11px;cursor:pointer">✕</button>
    </div>`).join('');

  const o = document.createElement('div');
  o.className = 'modal-overlay';
  o.onclick = e => { if(e.target===o) o.remove(); };
  o.innerHTML = `<div class="modal-sheet" onclick="event.stopPropagation()" style="max-height:85vh;overflow-y:auto">
    <div class="modal-handle"></div>
    <div class="modal-title">📝 买入理由备忘 · ${name||code}</div>
    <div style="font-size:11px;color:var(--text2);margin-bottom:12px">${code} · 记录你此刻的判断依据，方便日后复盘</div>
    ${prefill?`<div style="font-size:11px;padding:6px 10px;background:rgba(99,102,241,.06);border-radius:6px;margin-bottom:10px;color:#9CA3AF">💡 当前信号：${prefill}</div>`:''}
    <div style="margin-bottom:8px">
      <div style="font-size:12px;color:#9CA3AF;margin-bottom:4px">备忘内容 <span style="opacity:0.6">（可记录买入理由、止盈条件、关注点等）</span></div>
      <textarea id="buyMemoText" rows="4" placeholder="例：PE百分位历史低位27%，净值在历史底部区间，准备定投建仓，止盈目标+30%，关注AI主题持续性…" style="width:100%;box-sizing:border-box;padding:10px;border:1px solid rgba(148,163,184,.25);border-radius:8px;background:rgba(15,23,42,.6);color:#E5E7EB;font-size:12px;line-height:1.6;resize:vertical">${prefill?'基于：'+prefill+'\n\n':''}</textarea>
    </div>
    <div style="display:flex;gap:8px;margin-bottom:16px">
      <button class="mb-btn mb-btn--primary" style="flex:1" onclick="(function(){
        const txt=document.getElementById('buyMemoText').value.trim();
        if(!txt){alert('请填写备忘内容');return;}
        const arr=_getBuyMemos('${code}');
        arr.unshift({date:'${today}',note:txt,tags:'${prefill.slice(0,40)}'});
        _saveBuyMemos('${code}',arr);
        document.querySelector('.modal-overlay')?.remove();
        if(typeof renderFundPickResult==='function') renderFundPickResult();
        // 轻提示
        const t=document.createElement('div');
        t.style.cssText='position:fixed;top:20px;left:50%;transform:translateX(-50%);padding:8px 16px;background:rgba(34,197,94,.9);color:#fff;border-radius:8px;font-size:13px;z-index:9999;pointer-events:none';
        t.textContent='✅ 备忘已保存';
        document.body.appendChild(t);
        setTimeout(()=>t.remove(),2000);
      })()">💾 保存备忘</button>
      <button class="mb-btn mb-btn--secondary" style="flex-shrink:0;padding:0 14px" onclick="document.querySelector('.modal-overlay')?.remove()">取消</button>
    </div>
    ${memos.length?`<div style="font-size:12px;color:#9CA3AF;margin-bottom:8px;font-weight:500">历史备忘（${memos.length}条）</div>${histHtml}`:'<div style="text-align:center;padding:12px;color:#6B7280;font-size:12px">还没有备忘，记录第一条吧 👆</div>'}
  </div>`;
  document.body.appendChild(o);
};

// AI 多因子选股页
