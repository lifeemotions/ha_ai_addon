# Copilot Code Review Instructions

## Version Bump Required for Code Changes

This is a Home Assistant add-on. The version in `lifeemotions_ai_addon/config.yaml` must be bumped whenever production code changes, because HA Supervisor uses this version to detect updates and rebuild the container image. Without a bump, users may get different code for the same version number.

**Rule:** If a PR modifies any file under `lifeemotions_ai_addon/` (except docs like `README.md`, `CHANGELOG.md`), the `version` field in `lifeemotions_ai_addon/config.yaml` must also be changed in the same PR.

**Exempt files (no version bump needed):**
- `*.md` files (documentation)
- `tests/` directory (test files)
- `CLAUDE.md`
- `.github/` directory
- `repository.json`
- `pytest.ini`
- `scripts/` directory

**If this rule is violated, flag it as a blocking issue:** "Production code changed without version bump in `lifeemotions_ai_addon/config.yaml`. HA Supervisor uses this version to detect updates — without a bump, different users will get different code for the same version."
