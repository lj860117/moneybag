"""
钱袋子 — 财经日历服务
优先级：
  1. 在线爬虫抓取（cngold.org，缓存1天）
  2. 手动配置的重要事件（data/common/calendar/manual_events.json）
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

MODULE_META = {
    "name": "financial_calendar",
    "scope": "global",
    "description": "财经日历 - 重要经济事件提醒",
}

# 在线财经日历URL
CALENDAR_URL = "https://m.cngold.org/calendar/nextweek_usd_3.html"

# 缓存配置
CACHE_HOURS = 24  # 缓存24小时


def get_calendar_events(days_ahead: int = 7, countries: list = None) -> list:
    """
    获取未来N天的重要财经事件
    
    参数：
        days_ahead: 未来天数
        countries: 国家列表（["中国", "美国"]），None=全部
    
    返回：
        list of event dicts
    """
    from datetime import datetime, timedelta
    
    start_dt = datetime.now()
    end_dt = datetime.now() + timedelta(days=days_ahead)
    
    # 优先级1：在线爬虫（带缓存）
    try:
        online_events = _fetch_online_events(start_dt, end_dt, countries)
        if online_events:
            print(f"[CALENDAR] 使用在线爬虫数据: {len(online_events)} 条")
            return online_events[:10]  # 最多返回10条
    except Exception as e:
        print(f"[CALENDAR] 在线爬虫失败: {e}")
    
    # 优先级2：手动配置的事件
    manual_events = _load_manual_events(start_dt, end_dt, countries)
    if manual_events:
        print(f"[CALENDAR] 使用手动配置数据: {len(manual_events)} 条")
        return manual_events[:10]
    
    # 都失败，返回空
    return []


def _fetch_online_events(start_dt, end_dt, countries: list = None) -> list:
    """从在线源抓取财经日历（带缓存）"""
    try:
        from config import DATA_DIR
        
        # 缓存文件路径
        cache_dir = Path(DATA_DIR) / "common" / "calendar"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / "online_events_cache.json"
        
        # 检查缓存
        if _is_cache_valid(cache_file):
            print(f"[CALENDAR] 使用缓存数据: {cache_file}")
            with open(cache_file, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
            return _filter_events_by_date(cached_data.get("events", []), start_dt, end_dt, countries)
        
        # 缓存无效，重新抓取
        print(f"[CALENDAR] 缓存过期，重新抓取: {CALENDAR_URL}")
        events = _crawl_cngold_calendar()
        
        if not events:
            print(f"[CALENDAR] 抓取失败，尝试使用过期缓存")
            if cache_file.exists():
                with open(cache_file, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)
                return _filter_events_by_date(cached_data.get("events", []), start_dt, end_dt, countries)
            return []
        
        # 保存缓存
        cache_data = {
            "fetch_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "events": events,
        }
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
        
        print(f"[CALENDAR] 抓取成功: {len(events)} 条事件")
        return _filter_events_by_date(events, start_dt, end_dt, countries)
        
    except Exception as e:
        print(f"[CALENDAR] 在线抓取失败: {e}")
        import traceback
        traceback.print_exc()
        return []


def _is_cache_valid(cache_file: Path) -> bool:
    """检查缓存是否有效（24小时内）"""
    try:
        if not cache_file.exists():
            return False
        
        with open(cache_file, "r", encoding="utf-8") as f:
            cache_data = json.load(f)
        
        fetch_time_str = cache_data.get("fetch_time", "")
        if not fetch_time_str:
            return False
        
        fetch_time = datetime.strptime(fetch_time_str, "%Y-%m-%d %H:%M:%S")
        hours_diff = (datetime.now() - fetch_time).total_seconds() / 3600
        
        return hours_diff < CACHE_HOURS
        
    except Exception:
        return False


def _parse_date(date_str: str) -> str:
    """解析日期字符串，返回 YYYYMMDD 格式"""
    try:
        # 尝试解析 "2026-06-08 星期一" 格式
        date_part = date_str.split()[0] if " " in date_str else date_str
        dt = datetime.strptime(date_part, "%Y-%m-%d")
        return dt.strftime("%Y%m%d")
    except Exception:
        return ""


def _guess_importance(event_name: str, country: str) -> str:
    """根据事件名称猜测重要性"""
    high_keywords = ["利率", "CPI", "GDP", "非农", "失业率", "央行", "FOMC", "决议"]
    medium_keywords = ["PMI", "零售", "工业", "通胀", "贸易"]
    
    for kw in high_keywords:
        if kw in event_name:
            return "高"
    
    for kw in medium_keywords:
        if kw in event_name:
            return "中"
    
    return "低"


def _crawl_cngold_calendar() -> list:
    """爬取中国黄金网的财经日历"""
    try:
        import requests
        from bs4 import BeautifulSoup
        
        # 发送HTTP请求
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = requests.get(CALENDAR_URL, headers=headers, timeout=10)
        response.raise_for_status()
        response.encoding = "utf-8"
        
        # 解析HTML
        soup = BeautifulSoup(response.text, "lxml")
        
        events = []
        
        # 找到所有事件div
        event_divs = soup.find_all("div", class_="sort-week")
        
        print(f"[CALENDAR] 找到 {len(event_divs)} 个事件div")
        
        for event_div in event_divs:
            # 向前查找最近的日期标题（兄弟关系）
            current_date = ""
            prev_sibling = event_div.find_previous("div", class_="index_tit")
            
            if prev_sibling:
                h2 = prev_sibling.find("h2")
                if h2:
                    current_date = _parse_date(h2.get_text(strip=True))
            
            if not current_date:
                continue
            
            # 提取国家
            country_p = event_div.find("p", class_="city")
            country = ""
            if country_p:
                country = country_p.get_text(strip=True)
                # 去掉 <span> 中的内容
                span = country_p.find("span")
                if span:
                    span_text = span.get_text(strip=True)
                    country = country.replace(span_text, "").strip()
            
            # 提取事件名称
            event_h3 = event_div.find("h3")
            event_name = event_h3.get_text(strip=True) if event_h3 else ""
            
            if not event_name:
                continue
            
            # 提取重要性（根据事件名称判断）
            importance = _guess_importance(event_name, country)
            
            events.append({
                "date": current_date,
                "time": "",  # 这个网站似乎不提供具体时间
                "country": country,
                "event": event_name,
                "importance": importance,
            })
        
        print(f"[CALENDAR] 爬取完成: {len(events)} 条事件")
        return events
        
    except ImportError:
        print("[CALENDAR] 缺少依赖库: requests, beautifulsoup4, lxml")
        print("[CALENDAR] 请运行: pip install requests beautifulsoup4 lxml")
        return []
    except Exception as e:
        print(f"[CALENDAR] 爬取失败: {e}")
        import traceback
        traceback.print_exc()
        return []


def _filter_events_by_date(events: list, start_dt, end_dt, countries: list = None) -> list:
    """按日期范围和国家过滤事件"""
    filtered = []
    
    for event in events:
        event_date_str = event.get("date", "")
        if not event_date_str:
            continue
        
        try:
            event_dt = datetime.strptime(event_date_str, "%Y%m%d")
        except ValueError:
            continue
        
        # 日期过滤
        if event_dt < start_dt or event_dt > end_dt:
            continue
        
        # 国家过滤
        if countries:
            country = event.get("country", "")
            if country not in countries:
                continue
        
        filtered.append(event)
    
    # 按日期排序
    filtered.sort(key=lambda x: (x.get("date", ""), x.get("time", "")))
    
    return filtered


def _load_manual_events(start_dt, end_dt, countries: list = None) -> list:
    """读取手动配置的重要事件"""
    try:
        from config import DATA_DIR
        calendar_dir = Path(DATA_DIR) / "common" / "calendar"
        events_file = calendar_dir / "manual_events.json"
        
        if not events_file.exists():
            # 创建默认事件文件
            _create_default_events(events_file)
        
        with open(events_file, "r", encoding="utf-8") as f:
            all_events = json.load(f)
        
        # 过滤日期范围和国家
        filtered = []
        for event in all_events:
            event_date = event.get("date", "")
            if not event_date:
                continue
            
            try:
                event_dt = datetime.strptime(event_date, "%Y%m%d")
            except ValueError:
                continue
            
            # 日期过滤
            if event_dt < start_dt or event_dt > end_dt:
                continue
            
            # 国家过滤
            if countries:
                country = event.get("country", "")
                if country not in countries:
                    continue
            
            filtered.append(event)
        
        # 按日期排序
        filtered.sort(key=lambda x: x.get("date", "99999999"))
        
        return filtered
        
    except Exception as e:
        print(f"[CALENDAR] 读取手动事件失败: {e}")
        return []


def _create_default_events(events_file: Path):
    """创建默认重要事件配置"""
    calendar_dir = events_file.parent
    calendar_dir.mkdir(parents=True, exist_ok=True)
    
    # 2026年重要事件（示例）
    default_events = [
        {
            "date": "20260618",
            "time": "02:00",
            "country": "美国",
            "event": "美联储利率决议",
            "importance": "高",
            "description": "FOMC利率决定，关注点阵图和鲍威尔讲话"
        },
        {
            "date": "20260609",
            "time": "10:00",
            "country": "中国",
            "event": "中国CPI/PPI数据",
            "importance": "高",
            "description": "5月通胀数据，影响货币政策预期"
        },
        {
            "date": "20260616",
            "time": "10:00",
            "country": "中国",
            "event": "中国5月经济数据",
            "importance": "高",
            "description": "工业增加值、固定资产投资、社会消费品零售总额"
        },
        {
            "date": "20260715",
            "time": "10:00",
            "country": "中国",
            "event": "中国Q2 GDP",
            "importance": "高",
            "description": "第二季度GDP增速，全年经济走势关键"
        },
    ]
    
    with open(events_file, "w", encoding="utf-8") as f:
        json.dump(default_events, f, ensure_ascii=False, indent=2)
    
    print(f"[CALENDAR] 创建默认事件文件: {events_file}")


def add_manual_event(date: str, event: str, country: str = "中国", time: str = "", importance: str = "中", description: str = "") -> bool:
    """添加手动事件"""
    try:
        from config import DATA_DIR
        events_file = Path(DATA_DIR) / "common" / "calendar" / "manual_events.json"
        
        # 读取现有事件
        if events_file.exists():
            with open(events_file, "r", encoding="utf-8") as f:
                events = json.load(f)
        else:
            events = []
        
        # 添加新事件
        new_event = {
            "date": date,
            "time": time,
            "country": country,
            "event": event,
            "importance": importance,
            "description": description,
        }
        events.append(new_event)
        
        # 保存
        with open(events_file, "w", encoding="utf-8") as f:
            json.dump(events, f, ensure_ascii=False, indent=2)
        
        print(f"[CALENDAR] 添加事件: {date} {event}")
        return True
        
    except Exception as e:
        print(f"[CALENDAR] 添加事件失败: {e}")
        return False


def clear_cache():
    """清除缓存，强制重新抓取"""
    try:
        from config import DATA_DIR
        cache_file = Path(DATA_DIR) / "common" / "calendar" / "online_events_cache.json"
        
        if cache_file.exists():
            cache_file.unlink()
            print(f"[CALENDAR] 缓存已清除: {cache_file}")
            return True
        
        print(f"[CALENDAR] 缓存文件不存在")
        return False
        
    except Exception as e:
        print(f"[CALENDAR] 清除缓存失败: {e}")
        return False
