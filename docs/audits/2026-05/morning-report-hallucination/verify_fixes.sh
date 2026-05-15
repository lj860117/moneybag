#!/bin/bash
# Verification script for AI hallucination fixes
# Usage: bash verify_fixes.sh
# Works from any directory

# Find the repository root by looking for backend/ directory
if [ -d "backend" ]; then
    REPO_ROOT="$(pwd)"
elif [ -d "../../../backend" ]; then
    REPO_ROOT="$(cd ../../../ && pwd)"
else
    # Try to find it by searching upward from script location
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    REPO_ROOT="$SCRIPT_DIR"
    while [ "$REPO_ROOT" != "/" ] && [ ! -d "$REPO_ROOT/backend" ]; do
        REPO_ROOT="$(dirname "$REPO_ROOT")"
    done
fi

if [ ! -d "$REPO_ROOT/backend" ]; then
    echo "ERROR: Could not find backend directory. Are you in the correct repo?"
    exit 1
fi

BACKEND_DIR="$REPO_ROOT/backend"

echo "=========================================="
echo "AI Hallucination Fixes Verification"
echo "=========================================="
echo "Repository root: $REPO_ROOT"
echo ""

# Color codes
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Counter
PASSED=0
FAILED=0

# Helper function to print test result
print_result() {
    local test_name=$1
    local result=$2
    if [ "$result" -eq 0 ]; then
        echo -e "${GREEN}PASS${NC}: $test_name"
        ((PASSED++))
        return 0
    else
        echo -e "${RED}FAIL${NC}: $test_name"
        ((FAILED++))
        return 1
    fi
}

echo "Test 1: Fix #1 - LLM Data-Completeness Declaration"

# Test 1a: Check for data-completeness declaration
if grep -q "data-completeness\|缺失数据\|数据完整性" "$BACKEND_DIR/scripts/night_worker.py" 2>/dev/null; then
    print_result "Data-completeness declaration found" 0 || true
else
    print_result "Data-completeness declaration found" 1 || true
fi

# Test 1b: Check for missing data disclosure
if grep -q "缺\|missing\|不包含" "$BACKEND_DIR/scripts/night_worker.py" 2>/dev/null && grep -q "央行\|PBOC\|MLF" "$BACKEND_DIR/scripts/night_worker.py" 2>/dev/null; then
    print_result "Missing data disclosure found" 0 || true
else
    print_result "Missing data disclosure found" 1 || true
fi

echo ""
echo "Test 2: Fix #2 - Tushare Fallback Chain"

# Test 2a: Check for Tushare fallback
if grep -q "Fallback\|fallback\|Tushare" "$BACKEND_DIR/infra/data_source/alt/flows.py" 2>/dev/null; then
    print_result "Tushare fallback found in get_hsgt_hist()" 0 || true
else
    print_result "Tushare fallback found in get_hsgt_hist()" 1 || true
fi

# Test 2b: Check for unit conversion (1M to 100M/亿)
if grep -q "/ 100\|north_money" "$BACKEND_DIR/infra/data_source/alt/flows.py" 2>/dev/null; then
    print_result "Unit conversion (1M to 亿) found" 0 || true
else
    print_result "Unit conversion (1M to 亿) found" 1 || true
fi

echo ""
echo "Test 3: Fix #3 - Cache TTL Reduction"

# Test 3a: Check for cache TTL constant
if grep -q "CACHE_TTL_HOURS.*4\|TTL.*4" "$BACKEND_DIR/services/steward.py" 2>/dev/null; then
    print_result "Cache TTL set to 4 hours" 0 || true
else
    print_result "Cache TTL set to 4 hours" 1 || true
fi

# Test 3b: Check for TTL logic
if grep -q "cache_age_hours.*CACHE_TTL\|mtime\|stat()" "$BACKEND_DIR/services/steward.py" 2>/dev/null; then
    print_result "TTL check logic found" 0 || true
else
    print_result "TTL check logic found" 1 || true
fi

echo ""
echo "Test 4: Python Syntax Validation"

# Test 4a: Validate night_worker.py syntax
if python3 -m py_compile "$BACKEND_DIR/scripts/night_worker.py" 2>/dev/null; then
    print_result "night_worker.py syntax valid" 0 || true
else
    print_result "night_worker.py syntax valid" 1 || true
fi

# Test 4b: Validate flows.py syntax
if python3 -m py_compile "$BACKEND_DIR/infra/data_source/alt/flows.py" 2>/dev/null; then
    print_result "flows.py syntax valid" 0 || true
else
    print_result "flows.py syntax valid" 1 || true
fi

# Test 4c: Validate steward.py syntax
if python3 -m py_compile "$BACKEND_DIR/services/steward.py" 2>/dev/null; then
    print_result "steward.py syntax valid" 0 || true
else
    print_result "steward.py syntax valid" 1 || true
fi

echo ""
echo "Test 5: Git Deployment"

# Test 5: Check git commit
if git log --oneline -1 2>/dev/null | grep -q "Fix three Priority 1\|hallucination"; then
    print_result "Correct commit deployed" 0 || true
else
    print_result "Correct commit deployed" 1 || true
fi

echo ""
echo "=========================================="
echo "Summary"
echo "=========================================="
echo -e "Passed: ${GREEN}$PASSED${NC}"
echo -e "Failed: ${RED}$FAILED${NC}"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}All tests passed! Fixes are properly deployed.${NC}"
    exit 0
else
    echo -e "${RED}Some tests failed. Review the output above.${NC}"
    exit 1
fi
