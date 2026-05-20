"""
基金分类器测试（FIX 2026-05-20 MB-008）
测试场景：混合/QDII 基金的正确分类和比例分配
"""
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.fund_classifier import classify_fund, classify_and_allocate


def test_pure_fund_types():
    """测试纯正基金类型的分类"""
    test_cases = [
        # (code, name, expected_type)
        ("", "易方达消费行业", "equity"),
        ("", "货币基金-中国银河", "money"),
        ("", "中国债券基金", "bond"),
        ("000216", "华泰柏瑞黄金ETF", "gold"),
    ]
    
    for code, name, expected in test_cases:
        result = classify_fund(code, name)
        print(f"✓ {name:30} → {result['type']:10} (expected: {expected})")
        assert result['type'] == expected, f"Expected {expected}, got {result['type']}"


def test_mixed_fund_classification():
    """测试混合基金的分类（MB-008 重点场景）"""
    test_cases = [
        # 混合基金
        ("", "华夏成长混合", "mixed"),
        ("", "东方灵活配置混合", "mixed"),
        ("", "嘉实QDII美元", "mixed"),
        ("", "易方达QDII基金", "mixed"),
        ("", "富国偏股混合", "mixed"),
        ("", "鹏华偏债混合", "mixed"),
    ]
    
    for code, name, expected in test_cases:
        result = classify_fund(code, name)
        print(f"✓ {name:30} → {result['type']:10} (is_mixed: {result['is_mixed']})")
        assert result['type'] == expected, f"Expected {expected}, got {result['type']}"
        assert result['is_mixed'] == True


def test_mixed_fund_allocation():
    """测试混合基金的配置比例推断"""
    test_cases = [
        # (name, expected_allocation_pattern)
        ("华夏成长混合", {"equity": (0.5, 0.7), "bond": (0.2, 0.4), "money": (0.1, 0.2)}),
        ("东方灵活配置混合", {"equity": (0.5, 0.7), "bond": (0.2, 0.4), "money": (0.08, 0.12)}),
        ("嘉实QDII基金", {"equity": (0.6, 0.7), "bond": (0.2, 0.3), "money": (0.08, 0.12)}),
        ("富国偏股混合", {"equity": (0.65, 0.75), "bond": (0.15, 0.25), "money": (0.08, 0.12)}),
        ("鹏华偏债混合", {"equity": (0.2, 0.3), "bond": (0.55, 0.65), "money": (0.1, 0.2)}),
    ]
    
    for name, expected_ranges in test_cases:
        result = classify_fund("", name)
        assert result['type'] == "mixed"
        alloc = result.get('allocation', {})
        
        for asset_type, (min_val, max_val) in expected_ranges.items():
            actual = alloc.get(asset_type, 0)
            in_range = min_val <= actual <= max_val
            symbol = "✓" if in_range else "✗"
            print(f"{symbol} {name:30} {asset_type:6}: {actual:.2f} (expected {min_val:.2f}-{max_val:.2f})")
            assert in_range, f"{asset_type} out of range"


def test_classify_and_allocate():
    """测试一步到位的分类+金额分配"""
    holdings = [
        # (code, name, nav_cost, shares, expected_equity_alloc)
        ("", "华夏成长混合", 1.5, 100, 0.50 * 150),  # 100*1.5 = 150, equity 50%
        ("", "东方灵活配置混合", 2.0, 100, 0.60 * 200),  # 100*2.0 = 200, equity 60%
        ("", "嘉实QDII基金", 1.0, 499, 0.65 * 499),  # 499 shares, 499 cost, equity 65%
    ]
    
    print("\n测试 classify_and_allocate:")
    for code, name, nav, shares, expected_equity in holdings:
        result = classify_and_allocate(code, name, nav, shares)
        total_cost = result["totalCost"]
        equity_alloc = result["equity"]
        
        print(f"✓ {name:30} cost={total_cost:.0f}, equity_alloc={equity_alloc:.0f} (expected ~{expected_equity:.0f})")
        # 允许小数精度差异
        assert abs(equity_alloc - expected_equity) < 1, f"Equity allocation mismatch"


def test_user_scenario_mb008():
    """
    MB-008 真实场景：
    用户有 5 只混合/QDII 基金共 ¥499，不应该全部分配为股票 100%
    """
    print("\n=== MB-008 真实场景测试 ===")
    funds = [
        {"code": "", "name": "华夏成长混合", "costNav": 1.5, "shares": 50},
        {"code": "", "name": "东方灵活配置", "costNav": 1.2, "shares": 100},
        {"code": "", "name": "嘉实QDII", "costNav": 1.0, "shares": 100},
        {"code": "", "name": "富国偏股混合", "costNav": 1.8, "shares": 75},
        {"code": "", "name": "鹏华偏债混合", "costNav": 1.5, "shares": 74},
    ]
    
    total_equity = 0
    total_bond = 0
    total_money = 0
    total_cost = 0
    
    print(f"{'基金名称':30} {'成本':8} {'权益分配':10} {'债券分配':10} {'现金分配':10}")
    print("-" * 70)
    
    for f in funds:
        result = classify_and_allocate(
            code=f["code"],
            name=f["name"],
            nav_cost=f["costNav"],
            shares=f["shares"],
        )
        total_equity += result["equity"]
        total_bond += result["bond"]
        total_money += result["money"]
        total_cost += result["totalCost"]
        
        print(f"{f['name']:30} ¥{result['totalCost']:7.0f} ¥{result['equity']:9.0f} ¥{result['bond']:9.0f} ¥{result['money']:9.0f}")
    
    print("-" * 70)
    print(f"{'总计':30} ¥{total_cost:7.0f} ¥{total_equity:9.0f} ¥{total_bond:9.0f} ¥{total_money:9.0f}")
    
    # 计算分配百分比
    equity_pct = total_equity / total_cost * 100 if total_cost > 0 else 0
    bond_pct = total_bond / total_cost * 100 if total_cost > 0 else 0
    money_pct = total_money / total_cost * 100 if total_cost > 0 else 0
    
    print(f"\n资产配置占比:")
    print(f"  权益类: {equity_pct:.1f}% (不应该是 100%!)")
    print(f"  债券类: {bond_pct:.1f}%")
    print(f"  现金类: {money_pct:.1f}%")
    
    # 验证不是 100% 股票
    assert equity_pct < 100, "Bug still exists: Mixed funds should not be 100% equity!"
    assert equity_pct < 80, "Mixed fund allocation seems incorrect"
    assert bond_pct > 5, "Mixed fund allocation should include some bonds"
    
    print("\n✅ MB-008 bug 已修复！混合基金不再被错误分类为 100% 股票")


if __name__ == "__main__":
    print("运行基金分类器测试...\n")
    print("=== 1. 纯正基金类型 ===")
    test_pure_fund_types()
    
    print("\n=== 2. 混合基金分类 ===")
    test_mixed_fund_classification()
    
    print("\n=== 3. 混合基金配置推断 ===")
    test_mixed_fund_allocation()
    
    print("\n=== 4. classify_and_allocate ===")
    test_classify_and_allocate()
    
    test_user_scenario_mb008()
    
    print("\n" + "="*70)
    print("✅ 所有测试通过！")
    print("="*70)
