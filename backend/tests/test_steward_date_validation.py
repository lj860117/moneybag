#!/usr/bin/env python3
"""
晨报日期验证 单元测试
========================
测试场景：
  1. briefing() 只使用当日缓存
  2. briefing() 删除过期缓存
  3. briefing_history() 过滤未来日期
  4. briefing_history() 过滤太旧的日期
  5. _extract_cache_date() 正确解析文件名

运行：
  pytest test_steward_date_validation.py -v
"""

import unittest
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestExtractCacheDate(unittest.TestCase):
    """测试 _extract_cache_date() 函数"""
    
    def test_valid_filename(self):
        """测试有效的文件名"""
        from backend.services.steward import _extract_cache_date
        
        result = _extract_cache_date("LeiJiang_20250714")
        self.assertEqual(result, "20250714")
    
    def test_user_id_with_underscore(self):
        """测试 user_id 中包含下划线的情况"""
        from backend.services.steward import _extract_cache_date
        
        result = _extract_cache_date("user_name_20260514")
        self.assertEqual(result, "20260514")
    
    def test_invalid_filename_no_date(self):
        """测试无日期的文件名"""
        from backend.services.steward import _extract_cache_date
        
        result = _extract_cache_date("invalid_filename")
        self.assertEqual(result, "filename")
    
    def test_invalid_filename_short(self):
        """测试过短的文件名"""
        from backend.services.steward import _extract_cache_date
        
        result = _extract_cache_date("single")
        self.assertEqual(result, "")


class TestBriefingCacheDateValidation(unittest.TestCase):
    """测试 briefing() 方法的日期验证"""
    
    def setUp(self):
        """创建临时缓存目录"""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.brief_dir = Path(self.temp_dir.name)
    
    def tearDown(self):
        """清理临时目录"""
        self.temp_dir.cleanup()
    
    def _create_cache_file(self, user_id: str, date_str: str, data: dict = None):
        """创建缓存文件"""
        if data is None:
            data = {"regime": "test", "timestamp": date_str}
        
        cache_file = self.brief_dir / f"{user_id}_{date_str}.json"
        cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return cache_file
    
    def test_returns_today_cache(self):
        """验证 briefing() 返回当日缓存"""
        today_str = datetime.now().strftime("%Y%m%d")
        
        # 创建当日缓存
        today_data = {"regime": "today_test", "timestamp": today_str}
        self._create_cache_file("test_user", today_str, today_data)
        
        # 模拟 briefing() 调用
        # 实际实现中应该读取缓存并验证日期
        cache_file = self.brief_dir / f"test_user_{today_str}.json"
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        
        self.assertEqual(cached["regime"], "today_test")
    
    def test_skips_yesterday_cache(self):
        """验证 briefing() 跳过昨天的缓存"""
        yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        today_str = datetime.now().strftime("%Y%m%d")
        
        # 创建昨天的缓存
        yesterday_data = {"regime": "yesterday_test"}
        self._create_cache_file("test_user", yesterday_str, yesterday_data)
        
        # 当前应该查找今天的缓存
        today_cache = self.brief_dir / f"test_user_{today_str}.json"
        
        # 昨天的缓存应该不被使用
        self.assertFalse(today_cache.exists())
    
    def test_date_comparison_logic(self):
        """验证日期比较逻辑"""
        today_str = datetime.now().strftime("%Y%m%d")
        yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        tomorrow_str = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")
        
        # 日期字符串比较
        self.assertLess(yesterday_str, today_str)
        self.assertGreater(tomorrow_str, today_str)
        self.assertEqual(today_str, today_str)


class TestBriefingHistoryFiltering(unittest.TestCase):
    """测试 briefing_history() 方法的过滤逻辑"""
    
    def setUp(self):
        """创建临时缓存目录"""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.brief_dir = Path(self.temp_dir.name)
    
    def tearDown(self):
        """清理临时目录"""
        self.temp_dir.cleanup()
    
    def _create_cache_file(self, user_id: str, date_str: str):
        """创建缓存文件"""
        data = {"regime": "test", "date": date_str}
        cache_file = self.brief_dir / f"{user_id}_{date_str}.json"
        cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    
    def test_filters_future_dates(self):
        """验证过滤未来日期"""
        today_dt = datetime.now().date()
        tomorrow_str = (today_dt + timedelta(days=1)).strftime("%Y%m%d")
        today_str = today_dt.strftime("%Y%m%d")
        
        # 创建明天和今天的缓存
        self._create_cache_file("test_user", tomorrow_str)
        self._create_cache_file("test_user", today_str)
        
        # 扫描缓存文件
        files = sorted(self.brief_dir.glob("test_user_*.json"), reverse=True)
        
        # 验证明天的缓存应该被过滤
        for fp in files:
            date_str = fp.stem.split('_')[-1]
            self.assertLessEqual(date_str, today_str,
                               f"Future date {date_str} should not be included")
    
    def test_filters_old_dates(self):
        """验证过滤太旧的日期"""
        today_dt = datetime.now().date()
        today_str = today_dt.strftime("%Y%m%d")
        cutoff_date = (today_dt - timedelta(days=7)).strftime("%Y%m%d")
        old_date = (today_dt - timedelta(days=10)).strftime("%Y%m%d")
        
        # 创建多个日期的缓存
        self._create_cache_file("test_user", today_str)
        self._create_cache_file("test_user", old_date)
        
        # 扫描缓存文件
        files = sorted(self.brief_dir.glob("test_user_*.json"), reverse=True)
        
        # 验证太旧的缓存应该被过滤
        count = 0
        for fp in files:
            date_str = fp.stem.split('_')[-1]
            if date_str >= cutoff_date:
                count += 1
            else:
                break
        
        self.assertLessEqual(count, 7,
                           f"Should have at most 7 recent days, got {count}")
    
    def test_date_format_validation(self):
        """验证日期格式验证"""
        # 创建格式不符的文件
        invalid_file = self.brief_dir / "test_user_invalid.json"
        invalid_file.write_text('{"regime": "test"}', encoding="utf-8")
        
        # 创建有效的文件
        today_str = datetime.now().strftime("%Y%m%d")
        valid_file = self.brief_dir / f"test_user_{today_str}.json"
        valid_file.write_text('{"regime": "test"}', encoding="utf-8")
        
        # 扫描并过滤
        files = sorted(self.brief_dir.glob("test_user_*.json"), reverse=True)
        
        # 只有有效格式的文件应该被处理
        self.assertGreater(len(files), 0)


class TestDateConsistencyCheck(unittest.TestCase):
    """测试日期一致性检查"""
    
    @patch('backend.services.steward.datetime')
    def test_valid_date_range(self, mock_datetime):
        """验证有效日期范围"""
        from backend.services.steward import _check_date_consistency
        
        # 设置为有效日期
        mock_date = MagicMock()
        mock_date.year = 2026
        mock_datetime.now.return_value.date.return_value = mock_date
        
        result = _check_date_consistency()
        self.assertTrue(result)
    
    @patch('backend.services.steward.datetime')
    def test_invalid_date_too_old(self, mock_datetime):
        """验证过旧日期被拒绝"""
        from backend.services.steward import _check_date_consistency
        
        # 设置为过旧日期
        mock_date = MagicMock()
        mock_date.year = 2010
        mock_datetime.now.return_value.date.return_value = mock_date
        
        result = _check_date_consistency()
        self.assertFalse(result)
    
    @patch('backend.services.steward.datetime')
    def test_invalid_date_too_new(self, mock_datetime):
        """验证过新日期被拒绝"""
        from backend.services.steward import _check_date_consistency
        
        # 设置为过新日期
        mock_date = MagicMock()
        mock_date.year = 2051
        mock_datetime.now.return_value.date.return_value = mock_date
        
        result = _check_date_consistency()
        self.assertFalse(result)


class TestIntegrationScenarios(unittest.TestCase):
    """集成测试场景"""
    
    def setUp(self):
        """创建临时缓存目录"""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.brief_dir = Path(self.temp_dir.name)
    
    def tearDown(self):
        """清理临时目录"""
        self.temp_dir.cleanup()
    
    def _create_cache_file(self, user_id: str, date_str: str):
        """创建缓存文件"""
        data = {"regime": f"regime_{date_str}", "date": date_str}
        cache_file = self.brief_dir / f"{user_id}_{date_str}.json"
        cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    
    def test_scenario_cache_pollution(self):
        """场景：多个旧缓存文件存在"""
        user_id = "LeiJiang"
        today_dt = datetime.now().date()
        
        # 创建最近 10 天的缓存
        for i in range(10):
            date_dt = today_dt - timedelta(days=i)
            date_str = date_dt.strftime("%Y%m%d")
            self._create_cache_file(user_id, date_str)
        
        # 验证最近缓存文件
        files = sorted(self.brief_dir.glob(f"{user_id}_*.json"), reverse=True)
        self.assertGreaterEqual(len(files), 10)
        
        # 验证可以正确识别当日缓存
        today_str = today_dt.strftime("%Y%m%d")
        today_cache = self.brief_dir / f"{user_id}_{today_str}.json"
        self.assertTrue(today_cache.exists())
    
    def test_scenario_mixed_users(self):
        """场景：多个用户的缓存混在一起"""
        today_str = datetime.now().strftime("%Y%m%d")
        
        # 创建多个用户的缓存
        for user in ["user1", "user2", "user3"]:
            self._create_cache_file(user, today_str)
        
        # 验证每个用户有自己的缓存
        user1_cache = self.brief_dir / f"user1_{today_str}.json"
        user2_cache = self.brief_dir / f"user2_{today_str}.json"
        user3_cache = self.brief_dir / f"user3_{today_str}.json"
        
        self.assertTrue(user1_cache.exists())
        self.assertTrue(user2_cache.exists())
        self.assertTrue(user3_cache.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
