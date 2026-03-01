#!/bin/bash
# Master test runner for KubeSentinel
# Runs all test suites and provides a comprehensive report

set -e  # Exit on first failure

echo "╔════════════════════════════════════════════════════════════╗"
echo "║  KubeSentinel Master Test Suite                           ║"
echo "║  Running all tests...                                     ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

TOTAL_TESTS=0
PASSED_TESTS=0
FAILED_TESTS=0

# Test 1: Unit Tests
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "1️⃣  Running Unit Tests (pytest)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if uv run pytest kubesentinel/tests/ -v --tb=short; then
    echo "✅ Unit Tests: PASSED"
    PASSED_TESTS=$((PASSED_TESTS + 16))
else
    echo "❌ Unit Tests: FAILED"
    FAILED_TESTS=$((FAILED_TESTS + 16))
fi
TOTAL_TESTS=$((TOTAL_TESTS + 16))
echo ""

# Test 2: Deterministic Layer
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "2️⃣  Running Deterministic Layer Tests"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if uv run python test_deterministic_layer.py; then
    echo "✅ Deterministic Layer: PASSED"
    PASSED_TESTS=$((PASSED_TESTS + 10))
else
    echo "❌ Deterministic Layer: FAILED"
    FAILED_TESTS=$((FAILED_TESTS + 10))
fi
TOTAL_TESTS=$((TOTAL_TESTS + 10))
echo ""

# Test 3: CLI Modes
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "3️⃣  Running CLI Mode Tests"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if uv run python test_cli_modes.py; then
    echo "✅ CLI Modes: PASSED"
    PASSED_TESTS=$((PASSED_TESTS + 6))
else
    echo "❌ CLI Modes: FAILED"
    FAILED_TESTS=$((FAILED_TESTS + 6))
fi
TOTAL_TESTS=$((TOTAL_TESTS + 6))
echo ""

# Test 4: Query Routing
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "4️⃣  Running Query Routing Tests"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if uv run python test_query_routing.py; then
    echo "✅ Query Routing: PASSED"
    PASSED_TESTS=$((PASSED_TESTS + 10))
else
    echo "❌ Query Routing: FAILED"
    FAILED_TESTS=$((FAILED_TESTS + 10))
fi
TOTAL_TESTS=$((TOTAL_TESTS + 10))
echo ""

# Test 5: Code Quality
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "5️⃣  Running Code Quality Checks"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if uv run ruff check kubesentinel/; then
    echo "✅ Ruff Lint: PASSED"
    PASSED_TESTS=$((PASSED_TESTS + 1))
else
    echo "❌ Ruff Lint: FAILED"
    FAILED_TESTS=$((FAILED_TESTS + 1))
fi
TOTAL_TESTS=$((TOTAL_TESTS + 1))
echo ""

# Summary
echo "╔════════════════════════════════════════════════════════════╗"
echo "║  Test Results Summary                                     ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "Total Tests:   $TOTAL_TESTS"
echo "Passed:        $PASSED_TESTS"
echo "Failed:        $FAILED_TESTS"
echo ""

if [ $FAILED_TESTS -eq 0 ]; then
    echo "🎉 All tests passed!"
    echo ""
    echo "✅ 16 Unit Tests"
    echo "✅ 10 Deterministic Layer Tests"
    echo "✅ 6 CLI Mode Tests"
    echo "✅ 10 Query Routing Tests"
    echo "✅ 1 Code Quality Check"
    echo ""
    exit 0
else
    echo "⚠️  $FAILED_TESTS test(s) failed"
    exit 1
fi
