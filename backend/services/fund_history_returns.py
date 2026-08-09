"""
基金历史收益率 - 使用 Tushare 作为主要数据源
"""
from datetime import datetime, timedelta
from config import TUSHARE_TOKEN

# 延迟导入 Tushare（避免模块加载失败）
pro = None

def _init_tushare():
    """延迟初始化 Tushare"""
    global pro
    if pro is not None:
        return pro
    
    try:
        import tushare as ts
        if TUSHARE_TOKEN:
            ts.set_token(TUSHARE_TOKEN)
            pro = ts.pro_api()
            return pro
    except ImportError:
        pass
    return None

def get_fund_history_returns(code: str) -> dict:
    """
    获取基金历史收益率（1个月、3个月、6个月、1年、3年）
    优先使用 Tushare，失败则降级到 AKShare
    
    Args:
        code: 基金代码（如 000001）
    
    Returns:
        {
            '1m': 5.23,   # 1个月收益率（%）
            '3m': 12.45,  # 3个月收益率（%）
            '6m': 25.67,  # 6个月收益率（%）
            '1y': 45.89,  # 1年收益率（%）
            '3y': 120.34, # 3年收益率（%）
            'date': '2025-12-31'  # 数据截至日期
        }
    """
    # 初始化 Tushare（延迟导入）
    _init_tushare()
    
    # 先尝试 Tushare
    if pro:
        result = _get_from_tushare(code)
        if result:
            return result
    
    # Tushare 失败，降级到 AKShare
    print(f"  ⚠️ Tushare 获取失败，降级到 AKShare: {code}")
    return _get_from_akshare(code)

def _get_from_tushare(code: str) -> dict or None:
    """
    从 Tushare 获取基金历史净值并计算收益率
    Tushare 基金代码格式：xxx.OF（Open Fund）
    """
    # 确保 Tushare 已初始化
    _init_tushare()
    if not pro:
        return None
    
    try:
        # 转换基金代码格式：000001 -> 000001.OF
        if not code.endswith('.OF'):
            ts_code = code + '.OF'
        else:
            ts_code = code
        
        # 获取过去3年的数据
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=3*365)).strftime('%Y%m%d')
        
        print(f"  📊 Tushare 获取基金历史净值: {ts_code}")
        df = pro.fund_nav(ts_code=ts_code, start_date=start_date, end_date=end_date)
        
        if df is None or df.empty:
            print(f"  ⚠️ Tushare 无数据: {ts_code}")
            return None
        
        # 按日期排序（升序）
        df = df.sort_values('nav_date')
        
        # 获取最新净值
        latest_nav = df.iloc[-1]['unit_nav']
        latest_date = df.iloc[-1]['nav_date']
        
        # 计算各周期收益率
        result = {'date': latest_date}
        
        # 1个月
        nav_1m = _get_nav_by_date(df, timedelta(days=30))
        result['1m'] = round((latest_nav / nav_1m - 1) * 100, 2) if nav_1m else None
        
        # 3个月
        nav_3m = _get_nav_by_date(df, timedelta(days=90))
        result['3m'] = round((latest_nav / nav_3m - 1) * 100, 2) if nav_3m else None
        
        # 6个月
        nav_6m = _get_nav_by_date(df, timedelta(days=180))
        result['6m'] = round((latest_nav / nav_6m - 1) * 100, 2) if nav_6m else None
        
        # 1年
        nav_1y = _get_nav_by_date(df, timedelta(days=365))
        result['1y'] = round((latest_nav / nav_1y - 1) * 100, 2) if nav_1y else None
        
        # 3年
        nav_3y = _get_nav_by_date(df, timedelta(days=3*365))
        result['3y'] = round((latest_nav / nav_3y - 1) * 100, 2) if nav_3y else None
        
        print(f"  ✅ Tushare 计算完成: {code}")
        return result
        
    except Exception as e:
        print(f"  ⚠️ Tushare 错误: {type(e).__name__}: {e}")
        return None

def _get_nav_by_date(df, delta: timedelta) -> float or None:
    """
    根据时间差获取对应的净值
    """
    target_date = (datetime.now() - delta).strftime('%Y%m%d')
    
    # 找到目标日期之前的最后一个净值
    df_filtered = df[df['nav_date'] <= target_date]
    
    if df_filtered.empty:
        return None
    
    return df_filtered.iloc[-1]['unit_nav']

def _get_from_akshare(code: str) -> dict:
    """
    从 AKShare 获取基金历史净值并计算收益率（降级方案）
    """
    try:
        import akshare as ak
        print(f"  📊 AKShare 获取基金历史净值: {code}")
        
        # AKShare 获取基金历史净值
        # 注意：fund_open_fund_hist_em 可能需要调整参数
        df = ak.fund_open_fund_hist_em(fund=code, period="历史净值")
        
        if df is None or df.empty:
            print(f"  ⚠️ AKShare 无数据: {code}")
            return None
        
        # 按日期排序
        df = df.sort_values('净值日期')
        
        # 获取最新净值
        latest_nav = df.iloc[-1]['单位净值']
        latest_date = df.iloc[-1]['净值日期']
        
        # 计算各周期收益率
        result = {'date': latest_date}
        
        # 1个月
        nav_1m = _get_ak_nav_by_date(df, timedelta(days=30))
        result['1m'] = round((latest_nav / nav_1m - 1) * 100, 2) if nav_1m else None
        
        # 3个月
        nav_3m = _get_ak_nav_by_date(df, timedelta(days=90))
        result['3m'] = round((latest_nav / nav_3m - 1) * 100, 2) if nav_3m else None
        
        # 6个月
        nav_6m = _get_ak_nav_by_date(df, timedelta(days=180))
        result['6m'] = round((latest_nav / nav_6m - 1) * 100, 2) if nav_6m else None
        
        # 1年
        nav_1y = _get_ak_nav_by_date(df, timedelta(days=365))
        result['1y'] = round((latest_nav / nav_1y - 1) * 100, 2) if nav_1y else None
        
        # 3年
        nav_3y = _get_ak_nav_by_date(df, timedelta(days=3*365))
        result['3y'] = round((latest_nav / nav_3y - 1) * 100, 2) if nav_3y else None
        
        print(f"  ✅ AKShare 计算完成: {code}")
        return result
        
    except Exception as e:
        print(f"  ⚠️ AKShare 错误: {type(e).__name__}: {e}")
        return None

def _get_ak_nav_by_date(df, delta: timedelta) -> float or None:
    """
    根据时间差获取对应的净值（AKShare 数据）
    """
    target_date = (datetime.now() - delta).strftime('%Y-%m-%d')
    
    # 找到目标日期之前的最后一个净值
    df_filtered = df[df['净值日期'] <= target_date]
    
    if df_filtered.empty:
        return None
    
    return df_filtered.iloc[-1]['单位净值']

# 测试函数
if __name__ == '__main__':
    # 测试：华夏成长混合（000001）
    result = get_fund_history_returns('000001')
    print(f"\n测试结果: {result}")
