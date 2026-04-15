#!/usr/bin/env python3
"""全面验证 dashboard 数据准确性"""
import os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
_env = Path(__file__).parent.parent / ".env"
if _env.exists():
    for l in _env.read_text().splitlines():
        if l.strip() and not l.startswith("#") and "=" in l:
            k, v = l.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

print("=" * 50)
print("  钱袋子数据准确性验证")
print("=" * 50)

# 1. PE-TTM
print("\n📊 1. 估值百分位")
try:
    import tushare as ts
    pro = ts.pro_api()
    df = pro.index_dailybasic(ts_code="399300.SZ", fields="trade_date,pe_ttm", start_date="20260410")
    if df is not None and len(df) > 0:
        pe = df.iloc[0]["pe_ttm"]
        print(f"  Tushare PE-TTM = {pe} (日期: {df.iloc[0]['trade_date']})")
    
    # 算百分位
    df_hist = pro.index_dailybasic(ts_code="399300.SZ", fields="trade_date,pe_ttm", start_date="20160101", end_date="20260414")
    if df_hist is not None and len(df_hist) > 0:
        all_pe = df_hist["pe_ttm"].dropna().tolist()
        below = sum(1 for p in all_pe if p < pe)
        pct = round(below / len(all_pe) * 100, 1)
        print(f"  历史数据: {len(all_pe)}天, PE范围: {min(all_pe):.1f} ~ {max(all_pe):.1f}")
        print(f"  手算百分位 = {pct}% (我们显示: 88.7%)")
        print(f"  ✅ 一致" if abs(pct - 88.7) < 3 else f"  ⚠️ 差异较大!")
except Exception as e:
    print(f"  ❌ Tushare 验证失败: {e}")

# 2. RSI
print("\n📈 2. RSI(14)")
try:
    import akshare as ak
    df2 = ak.stock_zh_index_daily_em(symbol="sh000300")
    closes = df2["close"].tail(15).tolist()
    gains = [max(0, closes[i] - closes[i-1]) for i in range(1, len(closes))]
    losses = [max(0, closes[i-1] - closes[i]) for i in range(1, len(closes))]
    ag = sum(gains) / 14
    al = sum(losses) / 14
    rsi = 100 - 100 / (1 + ag / al) if al > 0 else 100
    print(f"  AKShare 手算 RSI(14) = {rsi:.1f}")
    
    from services.technical import get_technical_indicators
    tech = get_technical_indicators()
    our_rsi = tech.get("rsi")
    print(f"  我们显示 RSI(14) = {our_rsi}")
    diff = abs(rsi - float(our_rsi)) if our_rsi else 999
    print(f"  ✅ 差异{diff:.1f}" if diff < 5 else f"  ⚠️ 差异较大!")
except Exception as e:
    print(f"  ❌ RSI 验证失败: {e}")

# 3. 布林带
print("\n📉 3. 布林带(20,2)")
try:
    tech = get_technical_indicators()
    b = tech.get("bollinger", {})
    print(f"  上轨={b.get('upper')}, 中轨={b.get('middle')}, 下轨={b.get('lower')}")
    print(f"  当前价={b.get('current')}")
    
    # 手算验证
    df3 = ak.stock_zh_index_daily_em(symbol="sh000300")
    c20 = df3["close"].tail(20).tolist()
    import statistics
    ma20 = statistics.mean(c20)
    std20 = statistics.stdev(c20)
    calc_upper = round(ma20 + 2 * std20, 2)
    calc_middle = round(ma20, 2)
    calc_lower = round(ma20 - 2 * std20, 2)
    print(f"  手算: 上={calc_upper}, 中={calc_middle}, 下={calc_lower}")
    print(f"  ✅ 一致" if abs(float(b.get('middle', 0)) - calc_middle) < 20 else "  ⚠️ 差异较大!")
except Exception as e:
    print(f"  ❌ 布林带验证失败: {e}")

# 4. MACD
print("\n📊 4. MACD")
try:
    m = tech.get("macd", {})
    print(f"  DIF={m.get('dif')}, DEA={m.get('dea')}, MACD={m.get('macd')}")
    print(f"  趋势={m.get('trend')}")
    # MACD 数值很难简单手算验证，但看正负号
    dif = float(m.get("dif", 0))
    dea = float(m.get("dea", 0))
    macd = float(m.get("macd", 0))
    print(f"  DIF>DEA = {'✅' if dif > dea else '❌'} (实际: {dif:.2f} vs {dea:.2f})")
    print(f"  MACD柱 = {macd:.2f} ({'红柱' if macd > 0 else '绿柱'})")
except Exception as e:
    print(f"  ❌ MACD 验证失败: {e}")

# 5. 恐贪指数
print("\n😨 5. 恐贪指数")
try:
    from services.market_data import get_fear_greed_index
    fg = get_fear_greed_index()
    print(f"  综合分 = {fg.get('score')}, level = {fg.get('level')}")
    dims = fg.get("dimensions", {})
    for k, v in dims.items():
        print(f"    {v.get('label', k)}: value={v.get('value')}, score={v.get('score')}")
    # 恐贪是我们自己算的多维度，不是 CNN 指数
    print(f"  说明: 这是钱袋子自研的多维度恐贪指数，非CNN Fear&Greed Index")
    s = float(fg.get("score", 50))
    if 20 < s < 80:
        print(f"  ✅ 数值合理（中性区间）")
except Exception as e:
    print(f"  ❌ 恐贪验证失败: {e}")

# 6. 北向资金
print("\n🏦 6. 北向资金")
try:
    from services.factor_data import get_northbound_flow
    nb = get_northbound_flow()
    print(f"  今日净流入={nb.get('net_flow_today')}亿")
    print(f"  5日累计={nb.get('net_flow_5d')}亿")
    print(f"  available={nb.get('available')}")
    if not nb.get("available"):
        print(f"  ⚠️ 北向数据不可用（可能收盘后/非交易时段）")
except Exception as e:
    print(f"  ❌ 北向验证失败: {e}")

# 7. 宏观数据
print("\n🌐 7. 宏观数据")
try:
    from services.macro_data import get_macro_calendar
    mc = get_macro_calendar()
    for item in mc[:4]:
        print(f"  {item.get('name')}: {item.get('value')} ({item.get('date')})")
except Exception as e:
    print(f"  ❌ 宏观验证失败: {e}")

# 汇总
print("\n" + "=" * 50)
print("  验证完成")
print("=" * 50)
