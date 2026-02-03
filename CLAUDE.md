# CLAUDE.md

## Python Environment

- Python version: 3.14.2
- Virtual environment: `.venv/` (activate with `source .venv/bin/activate`)
- Run Python: `.venv/bin/python`

## Running Tests

```bash
.venv/bin/python -m pytest tests/ -v --ignore=tests/test_integration_ha_docker.py
```

- Test framework: pytest with pytest-asyncio
- Config: `pytest.ini` (asyncio_mode=auto)
- Integration tests (`tests/test_integration_ha_docker.py`) require Docker and are marked with `@pytest.mark.integration`

## Bug Fixing Workflow

When a bug is reported, always follow this order:

1. **Reproduce first**: Write a failing test that demonstrates the bug before touching any production code.
2. **Fix**: Only after the test confirms the bug, work on the fix.
3. **Verify**: Run the failing test again to confirm it passes with the fix.
