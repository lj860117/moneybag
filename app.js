// ============================================================
// 钱袋子 — AI 智能资产配置助手 MVP
// 纯前端单文件应用
// ============================================================

// ---- 数据配置 ----

const QUESTIONS = [
  {
    emoji: '💰',
    question: '你打算拿出多少钱来理财？',
    options: [
      { text: '10万以下', score: 1 },
      { text: '10-30万', score: 2 },
      { text: '30-50万', score: 3 },
      { text: '50-100万', score: 4 },
      { text: '100万以上', score: 5 },
    ],
  },
  {
    emoji: '⏰',
    question: '这笔钱你多久不会用到？',
    options: [
      { text: '随时可能用', score: 1 },
      { text: '1年内不用', score: 2 },
      { text: '1-3年', score: 3 },
      { text: '3-5年', score: 4 },
      { text: '5年以上', score: 5 },
    ],
  },
  {
    emoji: '📉',
    question: '投了10万，一个月后变成8.5万，你会？',
    options: [
      { text: '立刻全卖 😱', score: 1 },
      { text: '卖掉一半 😟', score: 2 },
      { text: '不动，观望 😐', score: 3 },
      { text: '再买一点 😏', score: 4 },
      { text: '加大力度买 💪', score: 5 },
    ],
  },
  {
    emoji: '🎯',
    question: '你最期望的年收益是？',
    options: [
      { text: '3-5%（跑赢存款）', score: 1 },
      { text: '5-8%', score: 2 },
      { text: '8-15%', score: 3 },
      { text: '15-25%', score: 4 },
      { text: '25%+（搏一把）', score: 5 },
    ],
  },
  {
    emoji: '📚',
    question: '你对理财投资的了解程度？',
    options: [
      { text: '完全不懂', score: 1 },
      { text: '听过基金股票', score: 2 },
      { text: '买过余额宝/银行理财', score: 3 },
      { text: '买过基金/股票', score: 4 },
      { text: '有系统投资经验', score: 5 },
    ],
  },
];

const RISK_PROFILES = [
  { min: 5,  max: 8,  name: '保守型', emoji: '🐢', color: '#4CAF50', desc: '你追求资产安全，不愿承受大幅波动。稳稳当当，积少成多。', period: '1年以上' },
  { min: 9,  max: 12, name: '稳健型', emoji: '🐰', color: '#2196F3', desc: '你希望有一定收益，但也注重安全。偶尔小波动可以接受。', period: '2年以上' },
  { min: 13, max: 17, name: '平衡型', emoji: '🦊', color: '#FF9800', desc: '你希望资产稳步增长，能接受一定波动，但不想大起大落。', period: '3年以上' },
  { min: 18, max: 21, name: '进取型', emoji: '🦁', color: '#F44336', desc: '你追求较高收益，能承受较大波动。长期持有是你的策略。', period: '3-5年' },
  { min: 22, max: 25, name: '激进型', emoji: '🦅', color: '#9C27B0', desc: '你追求最大化收益，能承受剧烈波动甚至短期大幅亏损。', period: '5年以上' },
];

// 不同风险等级对应的资产配比
const ALLOCATIONS = {
  '保守型': [
    { name: '沪深300指数基金', code: '110020', fullName: '易方达沪深300ETF联接A', pct: 10, color: '#3B82F6', returns: { good: 0.15, mid: 0.08, bad: -0.10 } },
    { name: '标普500指数基金', code: '050025', fullName: '博时标普500ETF联接A', pct: 5,  color: '#10B981', returns: { good: 0.18, mid: 0.10, bad: -0.12 } },
    { name: '债券基金',       code: '217022', fullName: '招商产业债A',         pct: 50, color: '#F59E0B', returns: { good: 0.06, mid: 0.04, bad: 0.01 } },
    { name: '黄金ETF',       code: '000216', fullName: '华安黄金ETF联接A',    pct: 15, color: '#F97316', returns: { good: 0.15, mid: 0.08, bad: -0.05 } },
    { name: 'REITs基金',     code: '070018', fullName: '嘉实多利优选A',       pct: 10, color: '#EF4444', returns: { good: 0.10, mid: 0.06, bad: -0.05 } },
    { name: '货币基金(应急)', code: '余额宝', fullName: '余额宝',              pct: 10, color: '#E5E7EB', returns: { good: 0.02, mid: 0.018, bad: 0.015 } },
  ],
  '稳健型': [
    { name: '沪深300指数基金', code: '110020', fullName: '易方达沪深300ETF联接A', pct: 20, color: '#3B82F6', returns: { good: 0.15, mid: 0.08, bad: -0.10 } },
    { name: '标普500指数基金', code: '050025', fullName: '博时标普500ETF联接A', pct: 10, color: '#10B981', returns: { good: 0.18, mid: 0.10, bad: -0.12 } },
    { name: '债券基金',       code: '217022', fullName: '招商产业债A',         pct: 35, color: '#F59E0B', returns: { good: 0.06, mid: 0.04, bad: 0.01 } },
    { name: '黄金ETF',       code: '000216', fullName: '华安黄金ETF联接A',    pct: 15, color: '#F97316', returns: { good: 0.15, mid: 0.08, bad: -0.05 } },
    { name: 'REITs基金',     code: '070018', fullName: '嘉实多利优选A',       pct: 10, color: '#EF4444', returns: { good: 0.10, mid: 0.06, bad: -0.05 } },
    { name: '货币基金(应急)', code: '余额宝', fullName: '余额宝',              pct: 10, color: '#E5E7EB', returns: { good: 0.02, mid: 0.018, bad: 0.015 } },
  ],
  '平衡型': [
    { name: '沪深300指数基金', code: '110020', fullName: '易方达沪深300ETF联接A', pct: 30, color: '#3B82F6', returns: { good: 0.20, mid: 0.10, bad: -0.15 } },
    { name: '标普500指数基金', code: '050025', fullName: '博时标普500ETF联接A', pct: 20, color: '#10B981', returns: { good: 0.22, mid: 0.12, bad: -0.15 } },
    { name: '债券基金',       code: '217022', fullName: '招商产业债A',         pct: 20, color: '#F59E0B', returns: { good: 0.06, mid: 0.04, bad: 0.01 } },
    { name: '黄金ETF',       code: '000216', fullName: '华安黄金ETF联接A',    pct: 15, color: '#F97316', returns: { good: 0.15, mid: 0.08, bad: -0.05 } },
    { name: 'REITs基金',     code: '070018', fullName: '嘉实多利优选A',       pct: 10, color: '#EF4444', returns: { good: 0.10, mid: 0.06, bad: -0.05 } },
    { name: '货币基金(应急)', code: '余额宝', fullName: '余额宝',              pct: 5,  color: '#E5E7EB', returns: { good: 0.02, mid: 0.018, bad: 0.015 } },
  ],
  '进取型': [
    { name: '沪深300指数基金', code: '110020', fullName: '易方达沪深300ETF联接A', pct: 35, color: '#3B82F6', returns: { good: 0.25, mid: 0.12, bad: -0.18 } },
    { name: '标普500指数基金', code: '050025', fullName: '博时标普500ETF联接A', pct: 25, color: '#10B981', returns: { good: 0.25, mid: 0.13, bad: -0.18 } },
    { name: '债券基金',       code: '217022', fullName: '招商产业债A',         pct: 10, color: '#F59E0B', returns: { good: 0.06, mid: 0.04, bad: 0.01 } },
    { name: '黄金ETF',       code: '000216', fullName: '华安黄金ETF联接A',    pct: 10, color: '#F97316', returns: { good: 0.15, mid: 0.08, bad: -0.05 } },
    { name: 'REITs基金',     code: '070018', fullName: '嘉实多利优选A',       pct: 15, color: '#EF4444', returns: { good: 0.12, mid: 0.07, bad: -0.08 } },
    { name: '货币基金(应急)', code: '余额宝', fullName: '余额宝',              pct: 5,  color: '#E5E7EB', returns: { good: 0.02, mid: 0.018, bad: 0.015 } },
  ],
  '激进型': [
    { name: '沪深300指数基金', code: '110020', fullName: '易方达沪深300ETF联接A', pct: 40, color: '#3B82F6', returns: { good: 0.30, mid: 0.12, bad: -0.22 } },
    { name: '标普500指数基金', code: '050025', fullName: '博时标普500ETF联接A', pct: 30, color: '#10B981', returns: { good: 0.30, mid: 0.15, bad: -0.22 } },
    { name: '债券基金',       code: '217022', fullName: '招商产业债A',         pct: 5,  color: '#F59E0B', returns: { good: 0.06, mid: 0.04, bad: 0.01 } },
    { name: '黄金ETF',       code: '000216', fullName: '华安黄金ETF联接A',    pct: 5,  color: '#F97316', returns: { good: 0.15, mid: 0.08, bad: -0.05 } },
    { name: 'REITs基金',     code: '070018', fullName: '嘉实多利优选A',       pct: 15, color: '#EF4444', returns: { good: 0.15, mid: 0.08, bad: -0.10 } },
    { name: '货币基金(应急)', code: '余额宝', fullName: '余额宝',              pct: 5,  color: '#E5E7EB', returns: { good: 0.02, mid: 0.018, bad: 0.015 } },
  ],
};

// 金额选项映射
const AMOUNT_MAP = [0, 80000, 200000, 400000, 750000, 1500000];

// ---- 全局状态 ----
let currentPage = 'landing';    // landing | quiz | result
let currentQuestion = 0;
let answers = [];
let selectedAmount = 0;
let chartInstance = null;
let projectionChartInstance = null;

// ---- 工具函数 ----
function $(sel) { return document.querySelector(sel); }
function formatMoney(n) {
  if (n >= 10000) return (n / 10000).toFixed(n % 10000 === 0 ? 0 : 1) + '万';
  return n.toLocaleString('zh-CN');
}
function formatMoneyFull(n) { return '¥' + n.toLocaleString('zh-CN'); }

function getProfile(totalScore) {
  return RISK_PROFILES.find(p => totalScore >= p.min && totalScore <= p.max) || RISK_PROFILES[2];
}

function calcReturns(allocs, amount, scenario) {
  let total = 0;
  allocs.forEach(a => {
    total += (amount * a.pct / 100) * a.returns[scenario];
  });
  return total;
}

// ---- 注入全局样式 ----
function injectStyles() {
  const style = document.createElement('style');
  style.textContent = `
    * { margin: 0; padding: 0; box-sizing: border-box; }
    
    :root {
      --bg: #0F172A;
      --bg2: #1E293B;
      --bg3: #334155;
      --text: #F1F5F9;
      --text2: #94A3B8;
      --accent: #F59E0B;
      --accent2: #FBBF24;
      --green: #10B981;
      --red: #EF4444;
      --blue: #3B82F6;
      --radius: 16px;
      --shadow: 0 4px 24px rgba(0,0,0,0.3);
    }
    
    body {
      font-family: 'Noto Sans SC', -apple-system, BlinkMacSystemFont, sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      overflow-x: hidden;
    }
    
    #app {
      max-width: 480px;
      margin: 0 auto;
      padding: 20px;
      min-height: 100vh;
    }
    
    /* ---- 通用动画 ---- */
    @keyframes fadeUp {
      from { opacity: 0; transform: translateY(30px); }
      to { opacity: 1; transform: translateY(0); }
    }
    @keyframes fadeIn {
      from { opacity: 0; }
      to { opacity: 1; }
    }
    @keyframes scaleIn {
      from { opacity: 0; transform: scale(0.9); }
      to { opacity: 1; transform: scale(1); }
    }
    @keyframes slideRight {
      from { opacity: 0; transform: translateX(-40px); }
      to { opacity: 1; transform: translateX(0); }
    }
    @keyframes pulse {
      0%, 100% { transform: scale(1); }
      50% { transform: scale(1.05); }
    }
    @keyframes shimmer {
      0% { background-position: -200% 0; }
      100% { background-position: 200% 0; }
    }
    @keyframes float {
      0%, 100% { transform: translateY(0); }
      50% { transform: translateY(-8px); }
    }
    
    .fade-up { animation: fadeUp 0.6s ease forwards; }
    .fade-in { animation: fadeIn 0.5s ease forwards; }
    .scale-in { animation: scaleIn 0.5s ease forwards; }
    
    .stagger > * { opacity: 0; animation: fadeUp 0.5s ease forwards; }
    .stagger > *:nth-child(1) { animation-delay: 0.1s; }
    .stagger > *:nth-child(2) { animation-delay: 0.2s; }
    .stagger > *:nth-child(3) { animation-delay: 0.3s; }
    .stagger > *:nth-child(4) { animation-delay: 0.4s; }
    .stagger > *:nth-child(5) { animation-delay: 0.5s; }
    .stagger > *:nth-child(6) { animation-delay: 0.6s; }
    
    /* ---- 落地页 ---- */
    .landing {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-height: 90vh;
      text-align: center;
    }
    .landing-icon {
      font-size: 72px;
      margin-bottom: 24px;
      animation: float 3s ease-in-out infinite;
    }
    .landing h1 {
      font-size: 32px;
      font-weight: 900;
      margin-bottom: 12px;
      background: linear-gradient(135deg, var(--accent), #F472B6, var(--blue));
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
    }
    .landing .subtitle {
      font-size: 16px;
      color: var(--text2);
      margin-bottom: 40px;
      line-height: 1.6;
    }
    .landing .cta-btn {
      background: linear-gradient(135deg, var(--accent), #F97316);
      color: #000;
      border: none;
      padding: 16px 48px;
      font-size: 18px;
      font-weight: 700;
      border-radius: 50px;
      cursor: pointer;
      transition: all 0.3s ease;
      font-family: inherit;
      box-shadow: 0 4px 20px rgba(245,158,11,0.4);
    }
    .cta-btn:hover {
      transform: translateY(-2px);
      box-shadow: 0 6px 28px rgba(245,158,11,0.5);
    }
    .cta-btn:active { transform: translateY(0); }
    
    .trust-badges {
      margin-top: 32px;
      display: flex;
      gap: 20px;
      flex-wrap: wrap;
      justify-content: center;
    }
    .trust-badge {
      font-size: 13px;
      color: var(--text2);
      display: flex;
      align-items: center;
      gap: 4px;
    }
    .trust-badge::before { content: '✓'; color: var(--green); font-weight: 700; }
    
    .disclaimer {
      position: fixed;
      bottom: 12px;
      left: 50%;
      transform: translateX(-50%);
      font-size: 11px;
      color: #475569;
      text-align: center;
      max-width: 400px;
    }
    
    /* ---- 问卷页 ---- */
    .quiz-header {
      margin-bottom: 32px;
    }
    .progress-bar-bg {
      width: 100%;
      height: 6px;
      background: var(--bg3);
      border-radius: 3px;
      overflow: hidden;
      margin-bottom: 16px;
    }
    .progress-bar-fill {
      height: 100%;
      background: linear-gradient(90deg, var(--accent), #F472B6);
      border-radius: 3px;
      transition: width 0.5s ease;
    }
    .quiz-step {
      font-size: 13px;
      color: var(--text2);
    }
    
    .question-card {
      animation: fadeUp 0.4s ease forwards;
    }
    .question-emoji {
      font-size: 48px;
      margin-bottom: 16px;
    }
    .question-text {
      font-size: 22px;
      font-weight: 700;
      margin-bottom: 28px;
      line-height: 1.4;
    }
    
    .options {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .option-btn {
      background: var(--bg2);
      border: 2px solid var(--bg3);
      color: var(--text);
      padding: 16px 20px;
      font-size: 16px;
      border-radius: 12px;
      cursor: pointer;
      transition: all 0.25s ease;
      text-align: left;
      font-family: inherit;
      position: relative;
      overflow: hidden;
    }
    .option-btn:hover {
      border-color: var(--accent);
      background: rgba(245,158,11,0.08);
      transform: translateX(4px);
    }
    .option-btn:active {
      transform: scale(0.98);
    }
    .option-btn.selected {
      border-color: var(--accent);
      background: rgba(245,158,11,0.15);
    }
    .option-btn.selected::after {
      content: '✓';
      position: absolute;
      right: 16px;
      top: 50%;
      transform: translateY(-50%);
      color: var(--accent);
      font-weight: 700;
      font-size: 18px;
    }
    
    /* ---- 金额输入 ---- */
    .amount-section {
      margin-top: 32px;
      animation: fadeUp 0.5s ease forwards;
    }
    .amount-label {
      font-size: 14px;
      color: var(--text2);
      margin-bottom: 12px;
    }
    .amount-input-wrap {
      position: relative;
      margin-bottom: 8px;
    }
    .amount-input {
      width: 100%;
      background: var(--bg2);
      border: 2px solid var(--bg3);
      color: var(--text);
      padding: 16px 60px 16px 36px;
      font-size: 24px;
      font-weight: 700;
      border-radius: 12px;
      outline: none;
      font-family: inherit;
      transition: border-color 0.3s;
    }
    .amount-input:focus { border-color: var(--accent); }
    .amount-prefix {
      position: absolute;
      left: 16px;
      top: 50%;
      transform: translateY(-50%);
      color: var(--text2);
      font-size: 18px;
    }
    .amount-suffix {
      position: absolute;
      right: 16px;
      top: 50%;
      transform: translateY(-50%);
      color: var(--text2);
      font-size: 14px;
    }
    .amount-quick {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 12px;
    }
    .quick-btn {
      background: var(--bg3);
      border: none;
      color: var(--text2);
      padding: 8px 16px;
      font-size: 13px;
      border-radius: 8px;
      cursor: pointer;
      transition: all 0.2s;
      font-family: inherit;
    }
    .quick-btn:hover, .quick-btn.active {
      background: var(--accent);
      color: #000;
    }
    
    .generate-btn {
      width: 100%;
      margin-top: 24px;
      background: linear-gradient(135deg, var(--accent), #F97316);
      color: #000;
      border: none;
      padding: 16px;
      font-size: 17px;
      font-weight: 700;
      border-radius: 12px;
      cursor: pointer;
      font-family: inherit;
      transition: all 0.3s;
      box-shadow: 0 4px 20px rgba(245,158,11,0.3);
    }
    .generate-btn:hover {
      transform: translateY(-2px);
      box-shadow: 0 6px 28px rgba(245,158,11,0.4);
    }
    .generate-btn:disabled {
      opacity: 0.4;
      cursor: not-allowed;
      transform: none;
      box-shadow: none;
    }
    
    /* ---- 加载动画 ---- */
    .loading-screen {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-height: 60vh;
      text-align: center;
    }
    .loading-spinner {
      width: 48px;
      height: 48px;
      border: 4px solid var(--bg3);
      border-top-color: var(--accent);
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
      margin-bottom: 20px;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    .loading-text {
      color: var(--text2);
      font-size: 15px;
    }
    .loading-text span {
      display: inline-block;
      animation: pulse 1.5s ease infinite;
    }
    
    /* ---- 结果页 ---- */
    .result-page { padding-bottom: 80px; }
    
    .profile-card {
      background: linear-gradient(135deg, var(--bg2), var(--bg3));
      border-radius: var(--radius);
      padding: 28px;
      margin-bottom: 24px;
      text-align: center;
      position: relative;
      overflow: hidden;
      animation: scaleIn 0.5s ease forwards;
    }
    .profile-card::before {
      content: '';
      position: absolute;
      top: -50%;
      left: -50%;
      width: 200%;
      height: 200%;
      background: radial-gradient(circle, rgba(245,158,11,0.06) 0%, transparent 60%);
    }
    .profile-emoji { font-size: 56px; margin-bottom: 12px; position: relative; }
    .profile-name {
      font-size: 26px;
      font-weight: 900;
      margin-bottom: 8px;
      position: relative;
    }
    .profile-desc {
      font-size: 14px;
      color: var(--text2);
      line-height: 1.6;
      position: relative;
    }
    .profile-period {
      margin-top: 12px;
      font-size: 13px;
      color: var(--accent);
      position: relative;
    }
    
    .section-title {
      font-size: 18px;
      font-weight: 700;
      margin: 28px 0 16px;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    
    /* 饼图 */
    .chart-card {
      background: var(--bg2);
      border-radius: var(--radius);
      padding: 24px;
      margin-bottom: 24px;
      animation: fadeUp 0.5s ease 0.2s forwards;
      opacity: 0;
    }
    .chart-wrap {
      width: 240px;
      height: 240px;
      margin: 0 auto 20px;
    }
    .alloc-list {
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .alloc-item {
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 14px;
    }
    .alloc-dot {
      width: 12px;
      height: 12px;
      border-radius: 50%;
      flex-shrink: 0;
    }
    .alloc-name { flex: 1; color: var(--text2); }
    .alloc-pct { font-weight: 700; width: 40px; text-align: right; }
    .alloc-money { color: var(--accent); font-weight: 500; width: 90px; text-align: right; }
    
    /* 购物清单 */
    .shopping-list {
      background: var(--bg2);
      border-radius: var(--radius);
      padding: 24px;
      margin-bottom: 24px;
      animation: fadeUp 0.5s ease 0.3s forwards;
      opacity: 0;
    }
    .shop-item {
      display: flex;
      align-items: flex-start;
      gap: 12px;
      padding: 14px 0;
      border-bottom: 1px solid var(--bg3);
      animation: slideRight 0.4s ease forwards;
      opacity: 0;
    }
    .shop-item:last-child { border-bottom: none; }
    .shop-num {
      width: 28px;
      height: 28px;
      background: var(--accent);
      color: #000;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 13px;
      font-weight: 700;
      flex-shrink: 0;
      margin-top: 2px;
    }
    .shop-detail { flex: 1; }
    .shop-fund-name { font-weight: 600; font-size: 15px; margin-bottom: 4px; }
    .shop-code {
      display: inline-block;
      background: var(--bg3);
      padding: 2px 8px;
      border-radius: 4px;
      font-size: 13px;
      color: var(--accent);
      font-family: 'SF Mono', 'Courier New', monospace;
      margin-right: 6px;
    }
    .shop-platform { font-size: 12px; color: var(--text2); }
    .shop-amount {
      font-size: 17px;
      font-weight: 700;
      color: var(--accent);
      white-space: nowrap;
    }
    
    /* 收益预测 */
    .projection-card {
      background: var(--bg2);
      border-radius: var(--radius);
      padding: 24px;
      margin-bottom: 24px;
      animation: fadeUp 0.5s ease 0.4s forwards;
      opacity: 0;
    }
    .scenario-grid {
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      gap: 12px;
      margin-bottom: 20px;
    }
    .scenario-item {
      text-align: center;
      padding: 16px 8px;
      background: var(--bg);
      border-radius: 12px;
    }
    .scenario-label {
      font-size: 12px;
      color: var(--text2);
      margin-bottom: 4px;
    }
    .scenario-icon { font-size: 20px; margin-bottom: 4px; }
    .scenario-return {
      font-size: 20px;
      font-weight: 900;
    }
    .scenario-money {
      font-size: 12px;
      color: var(--text2);
      margin-top: 4px;
    }
    .scenario-return.positive { color: var(--green); }
    .scenario-return.negative { color: var(--red); }
    
    .projection-chart-wrap {
      height: 200px;
      margin-top: 16px;
    }
    
    /* 铁律 */
    .rules-card {
      background: linear-gradient(135deg, #1a1a2e, #16213e);
      border: 1px solid rgba(245,158,11,0.2);
      border-radius: var(--radius);
      padding: 24px;
      margin-bottom: 24px;
      animation: fadeUp 0.5s ease 0.5s forwards;
      opacity: 0;
    }
    .rule-item {
      display: flex;
      gap: 12px;
      margin-bottom: 16px;
      align-items: flex-start;
    }
    .rule-item:last-child { margin-bottom: 0; }
    .rule-num {
      width: 28px;
      height: 28px;
      background: rgba(245,158,11,0.15);
      color: var(--accent);
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 14px;
      font-weight: 700;
      flex-shrink: 0;
    }
    .rule-text { font-size: 15px; line-height: 1.5; }
    .rule-text strong { color: var(--accent); }
    
    /* 底部操作 */
    .bottom-actions {
      display: flex;
      gap: 12px;
      animation: fadeUp 0.5s ease 0.6s forwards;
      opacity: 0;
    }
    .action-btn {
      flex: 1;
      padding: 14px;
      border-radius: 12px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      font-family: inherit;
      transition: all 0.3s;
      text-align: center;
    }
    .action-btn.primary {
      background: linear-gradient(135deg, var(--accent), #F97316);
      color: #000;
      border: none;
    }
    .action-btn.secondary {
      background: transparent;
      color: var(--text2);
      border: 1px solid var(--bg3);
    }
    .action-btn:hover { transform: translateY(-2px); }
    
    /* 免责 */
    .footer-disclaimer {
      text-align: center;
      font-size: 11px;
      color: #475569;
      margin-top: 32px;
      padding: 16px;
      line-height: 1.5;
    }
  `;
  document.head.appendChild(style);
}

// ---- 渲染函数 ----

function renderLanding() {
  const app = $('#app');
  app.innerHTML = `
    <div class="landing stagger">
      <div class="landing-icon">💰</div>
      <h1>你的钱，该怎么放？</h1>
      <p class="subtitle">回答 5 个问题，AI 帮你出一份<br>专属资产配置方案</p>
      <button class="cta-btn" onclick="startQuiz()">开始测评</button>
      <div class="trust-badges">
        <span class="trust-badge">不收费</span>
        <span class="trust-badge">不推销</span>
        <span class="trust-badge">不注册</span>
      </div>
    </div>
    <div class="disclaimer">⚠️ 本工具仅供参考学习，不构成任何投资建议</div>
  `;
}

function renderQuiz() {
  const q = QUESTIONS[currentQuestion];
  const progress = ((currentQuestion) / QUESTIONS.length) * 100;
  const app = $('#app');
  
  app.innerHTML = `
    <div class="quiz-header fade-in">
      <div class="progress-bar-bg">
        <div class="progress-bar-fill" style="width:${progress}%"></div>
      </div>
      <div class="quiz-step">第 ${currentQuestion + 1} / ${QUESTIONS.length} 题</div>
    </div>
    <div class="question-card">
      <div class="question-emoji">${q.emoji}</div>
      <div class="question-text">${q.question}</div>
      <div class="options stagger">
        ${q.options.map((opt, i) => `
          <button class="option-btn" onclick="selectAnswer(${i}, ${opt.score})">${opt.text}</button>
        `).join('')}
      </div>
    </div>
  `;
}

function renderAmountInput() {
  const app = $('#app');
  const progress = 100;
  
  // 如果问卷第一题选了金额，预填
  const presetAmounts = [100000, 200000, 300000, 500000, 1000000];
  
  app.innerHTML = `
    <div class="quiz-header fade-in">
      <div class="progress-bar-bg">
        <div class="progress-bar-fill" style="width:${progress}%"></div>
      </div>
      <div class="quiz-step">最后一步 ✨</div>
    </div>
    <div class="amount-section">
      <div class="question-emoji">🎯</div>
      <div class="question-text">你具体想投多少钱？</div>
      <div class="amount-label">输入你的理财金额，我们会算出每个篮子该放多少</div>
      <div class="amount-input-wrap">
        <span class="amount-prefix">¥</span>
        <input class="amount-input" type="number" id="amountInput" placeholder="500000" value="${AMOUNT_MAP[answers[0]] || ''}" oninput="onAmountChange()">
        <span class="amount-suffix">元</span>
      </div>
      <div class="amount-quick">
        ${presetAmounts.map(a => `
          <button class="quick-btn" onclick="setAmount(${a})">${formatMoney(a)}</button>
        `).join('')}
      </div>
      <button class="generate-btn" id="genBtn" onclick="generateResult()" ${AMOUNT_MAP[answers[0]] ? '' : 'disabled'}>
        生成我的配置方案 →
      </button>
    </div>
  `;
  onAmountChange();
}

function renderLoading() {
  const app = $('#app');
  app.innerHTML = `
    <div class="loading-screen">
      <div class="loading-spinner"></div>
      <div class="loading-text"><span>AI 正在为你计算最优配置方案...</span></div>
    </div>
  `;
}

function renderResult() {
  const totalScore = answers.reduce((s, a) => s + a, 0);
  const profile = getProfile(totalScore);
  const allocs = ALLOCATIONS[profile.name];
  const amount = selectedAmount;
  
  // 计算收益
  const goodReturn = calcReturns(allocs, amount, 'good');
  const midReturn = calcReturns(allocs, amount, 'mid');
  const badReturn = calcReturns(allocs, amount, 'bad');
  
  const goodPct = (goodReturn / amount * 100).toFixed(1);
  const midPct = (midReturn / amount * 100).toFixed(1);
  const badPct = (badReturn / amount * 100).toFixed(1);
  
  const app = $('#app');
  app.innerHTML = `
    <div class="result-page">
      <!-- 风险画像 -->
      <div class="profile-card">
        <div class="profile-emoji">${profile.emoji}</div>
        <div class="profile-name" style="color:${profile.color}">你是「${profile.name}」投资者</div>
        <div class="profile-desc">${profile.desc}</div>
        <div class="profile-period">建议投资周期：${profile.period}</div>
      </div>
      
      <!-- 资产配置 -->
      <div class="section-title">📊 你的 ${formatMoney(amount)} 这样分</div>
      <div class="chart-card">
        <div class="chart-wrap"><canvas id="allocChart"></canvas></div>
        <div class="alloc-list">
          ${allocs.map(a => `
            <div class="alloc-item">
              <div class="alloc-dot" style="background:${a.color}"></div>
              <div class="alloc-name">${a.name}</div>
              <div class="alloc-pct">${a.pct}%</div>
              <div class="alloc-money">${formatMoneyFull(Math.round(amount * a.pct / 100))}</div>
            </div>
          `).join('')}
        </div>
      </div>
      
      <!-- 购物清单 -->
      <div class="section-title">📋 打开支付宝，照着买</div>
      <div class="shopping-list">
        ${allocs.map((a, i) => `
          <div class="shop-item" style="animation-delay:${0.1 * (i + 1)}s">
            <div class="shop-num">${i + 1}</div>
            <div class="shop-detail">
              <div class="shop-fund-name">${a.fullName}</div>
              <span class="shop-code">${a.code}</span>
              <span class="shop-platform">${a.code === '余额宝' ? '直接留在余额宝' : '支付宝搜索代码'}</span>
            </div>
            <div class="shop-amount">${formatMoneyFull(Math.round(amount * a.pct / 100))}</div>
          </div>
        `).join('')}
      </div>
      
      <!-- 收益预测 -->
      <div class="section-title">💰 一年后可能会怎样</div>
      <div class="projection-card">
        <div class="scenario-grid">
          <div class="scenario-item">
            <div class="scenario-icon">📈</div>
            <div class="scenario-label">乐观</div>
            <div class="scenario-return positive">+${goodPct}%</div>
            <div class="scenario-money">赚 ${formatMoney(Math.round(goodReturn))}</div>
          </div>
          <div class="scenario-item">
            <div class="scenario-icon">📊</div>
            <div class="scenario-label">中性</div>
            <div class="scenario-return positive">+${midPct}%</div>
            <div class="scenario-money">赚 ${formatMoney(Math.round(midReturn))}</div>
          </div>
          <div class="scenario-item">
            <div class="scenario-icon">📉</div>
            <div class="scenario-label">悲观</div>
            <div class="scenario-return ${badReturn >= 0 ? 'positive' : 'negative'}">${badReturn >= 0 ? '+' : ''}${badPct}%</div>
            <div class="scenario-money">${badReturn >= 0 ? '赚' : '亏'} ${formatMoney(Math.abs(Math.round(badReturn)))}</div>
          </div>
        </div>
        <div style="font-size:13px;color:var(--text2);margin-bottom:8px;">3 年累计预测（中性场景，复利计算）</div>
        <div class="projection-chart-wrap"><canvas id="projChart"></canvas></div>
      </div>
      
      <!-- 铁律 -->
      <div class="section-title">⚠️ 三条铁律，比什么都重要</div>
      <div class="rules-card">
        <div class="rule-item">
          <div class="rule-num">1</div>
          <div class="rule-text"><strong>跌了别卖</strong> — 越跌越该买（定投的意义就在这）</div>
        </div>
        <div class="rule-item">
          <div class="rule-num">2</div>
          <div class="rule-text"><strong>别看新闻瞎操作</strong> — 关掉手机，该干嘛干嘛</div>
        </div>
        <div class="rule-item">
          <div class="rule-num">3</div>
          <div class="rule-text"><strong>至少拿 3 年</strong> — 短期亏很正常，3 年赚钱概率 > 85%</div>
        </div>
      </div>
      
      <!-- 操作按钮 -->
      <div class="bottom-actions">
        <button class="action-btn primary" onclick="saveAsImage()">📤 保存方案</button>
        <button class="action-btn secondary" onclick="restart()">🔄 重新测评</button>
      </div>
      
      <div class="footer-disclaimer">
        ⚠️ 免责声明：本工具基于历史数据和通用资产配置理论，仅供参考学习。
        不构成任何投资建议。投资有风险，入市需谨慎。
        基金过往业绩不代表未来表现。
      </div>
    </div>
  `;
  
  // 画饼图
  setTimeout(() => drawAllocChart(allocs), 100);
  setTimeout(() => drawProjectionChart(amount, midReturn / amount), 200);
}

// ---- 图表绘制 ----

function drawAllocChart(allocs) {
  const ctx = document.getElementById('allocChart');
  if (!ctx) return;
  if (chartInstance) chartInstance.destroy();
  
  chartInstance = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: allocs.map(a => a.name),
      datasets: [{
        data: allocs.map(a => a.pct),
        backgroundColor: allocs.map(a => a.color),
        borderColor: '#1E293B',
        borderWidth: 3,
        hoverOffset: 8,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      cutout: '55%',
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#1E293B',
          titleColor: '#F1F5F9',
          bodyColor: '#94A3B8',
          padding: 12,
          cornerRadius: 8,
          callbacks: {
            label: (ctx) => ` ${ctx.label}: ${ctx.parsed}%`,
          },
        },
      },
      animation: {
        animateRotate: true,
        duration: 1200,
        easing: 'easeOutQuart',
      },
    },
  });
}

function drawProjectionChart(amount, midRate) {
  const ctx = document.getElementById('projChart');
  if (!ctx) return;
  if (projectionChartInstance) projectionChartInstance.destroy();
  
  const years = ['现在', '1年后', '2年后', '3年后'];
  const values = [amount];
  for (let i = 1; i <= 3; i++) {
    values.push(Math.round(values[i-1] * (1 + midRate)));
  }
  
  projectionChartInstance = new Chart(ctx, {
    type: 'line',
    data: {
      labels: years,
      datasets: [{
        data: values,
        borderColor: '#F59E0B',
        backgroundColor: 'rgba(245,158,11,0.1)',
        fill: true,
        tension: 0.4,
        pointBackgroundColor: '#F59E0B',
        pointBorderColor: '#1E293B',
        pointBorderWidth: 2,
        pointRadius: 6,
        pointHoverRadius: 8,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#1E293B',
          titleColor: '#F1F5F9',
          bodyColor: '#F59E0B',
          padding: 12,
          cornerRadius: 8,
          callbacks: {
            label: (ctx) => ' ' + formatMoneyFull(ctx.parsed.y),
          },
        },
      },
      scales: {
        x: {
          ticks: { color: '#94A3B8', font: { size: 12 } },
          grid: { display: false },
        },
        y: {
          ticks: {
            color: '#94A3B8',
            font: { size: 11 },
            callback: (v) => formatMoney(v),
          },
          grid: { color: 'rgba(148,163,184,0.1)' },
        },
      },
      animation: { duration: 1500, easing: 'easeOutQuart' },
    },
  });
}

// ---- 事件处理 ----

function startQuiz() {
  currentPage = 'quiz';
  currentQuestion = 0;
  answers = [];
  renderQuiz();
}

function selectAnswer(index, score) {
  // 选中动效
  const btns = document.querySelectorAll('.option-btn');
  btns.forEach(b => b.classList.remove('selected'));
  btns[index].classList.add('selected');
  
  answers.push(score);
  
  setTimeout(() => {
    currentQuestion++;
    if (currentQuestion < QUESTIONS.length) {
      renderQuiz();
    } else {
      renderAmountInput();
    }
  }, 300);
}

function onAmountChange() {
  const input = document.getElementById('amountInput');
  const btn = document.getElementById('genBtn');
  if (!input || !btn) return;
  const val = parseInt(input.value);
  btn.disabled = !(val > 0);
  selectedAmount = val || 0;
  
  // 高亮匹配的快捷按钮
  document.querySelectorAll('.quick-btn').forEach(b => b.classList.remove('active'));
}

function setAmount(val) {
  const input = document.getElementById('amountInput');
  if (input) {
    input.value = val;
    selectedAmount = val;
    onAmountChange();
    document.getElementById('genBtn').disabled = false;
  }
  // 高亮
  document.querySelectorAll('.quick-btn').forEach(b => {
    b.classList.toggle('active', parseInt(b.textContent) === val || b.textContent === formatMoney(val));
  });
}

function generateResult() {
  if (!selectedAmount || selectedAmount <= 0) return;
  renderLoading();
  // 模拟 AI 计算延迟
  setTimeout(() => {
    currentPage = 'result';
    renderResult();
  }, 1500);
}

function restart() {
  currentPage = 'landing';
  currentQuestion = 0;
  answers = [];
  selectedAmount = 0;
  renderLanding();
}

function saveAsImage() {
  // 简单实现：提示截图
  alert('💡 小技巧：长按屏幕截图保存，或者用手机截屏功能！\n\n后续版本将支持一键导出 PDF。');
}

// ---- 启动 ----
injectStyles();
renderLanding();
