#!/bin/bash
# Run all tests for the Life Emotions AI Add-on
#
# Usage:
#   ./scripts/run_tests.sh          # Run all tests
#   ./scripts/run_tests.sh unit     # Run only unit tests
#   ./scripts/run_tests.sh ha       # Run only HA Docker integration tests
#   ./scripts/run_tests.sh e2e      # Run only E2E integration tests (HA + mock Cloud API)
#   ./scripts/run_tests.sh addon    # Run only add-on installation tests
#   ./scripts/run_tests.sh fast     # Run unit + HA Docker tests (skip slow addon tests)

set -e

cd "$(dirname "$0")/.."

VENV_PYTHON=".venv/bin/python"

if [ ! -f "$VENV_PYTHON" ]; then
    echo "Error: Virtual environment not found. Run: python -m venv .venv && .venv/bin/pip install -r requirements-test.txt"
    exit 1
fi

run_unit_tests() {
    echo "=========================================="
    echo "Running Unit Tests"
    echo "=========================================="
    $VENV_PYTHON -m pytest tests/ -v \
        --ignore=tests/test_integration_addon_install.py \
        --ignore=tests/test_integration_ha_docker.py \
        --ignore=tests/test_integration_e2e.py
}

run_ha_docker_tests() {
    echo "=========================================="
    echo "Running HA Docker Integration Tests"
    echo "=========================================="
    $VENV_PYTHON -m pytest tests/test_integration_ha_docker.py -v
}

run_e2e_tests() {
    echo "=========================================="
    echo "Running E2E Integration Tests"
    echo "=========================================="
    $VENV_PYTHON -m pytest tests/test_integration_e2e.py -v
}

run_addon_install_tests() {
    echo "=========================================="
    echo "Running Add-on Installation Tests (slow)"
    echo "=========================================="
    $VENV_PYTHON -m pytest tests/test_integration_addon_install.py -v
}

case "${1:-all}" in
    unit)
        run_unit_tests
        ;;
    ha)
        run_ha_docker_tests
        ;;
    e2e)
        run_e2e_tests
        ;;
    addon)
        run_addon_install_tests
        ;;
    fast)
        run_unit_tests
        echo ""
        run_ha_docker_tests
        ;;
    all)
        run_unit_tests
        echo ""
        run_ha_docker_tests
        echo ""
        run_e2e_tests
        echo ""
        run_addon_install_tests
        ;;
    *)
        echo "Usage: $0 {unit|ha|e2e|addon|fast|all}"
        echo ""
        echo "  unit  - Run only unit tests (fast, no Docker)"
        echo "  ha    - Run only HA Docker integration tests"
        echo "  e2e   - Run only E2E integration tests (HA + mock Cloud API)"
        echo "  addon - Run only add-on installation tests (slow, ~3 min)"
        echo "  fast  - Run unit + HA Docker tests (skip slow addon tests)"
        echo "  all   - Run all tests (default)"
        exit 1
        ;;
esac

echo ""
echo "=========================================="
echo "All requested tests passed!"
echo "=========================================="
