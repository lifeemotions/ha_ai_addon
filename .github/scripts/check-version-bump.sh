#!/usr/bin/env bash
#
# Checks that production code changes include a version bump in config.yaml.
# Called by the check-version-bump GitHub Action.
#
# Expects: BASE_SHA and HEAD_SHA environment variables.
#
set -euo pipefail

if [ -z "${BASE_SHA:-}" ] || [ -z "${HEAD_SHA:-}" ]; then
  echo "::error::BASE_SHA and HEAD_SHA must be set"
  exit 1
fi

# Get list of changed files
CHANGED_FILES=$(git diff --name-only "$BASE_SHA" "$HEAD_SHA")

# Check if any production code changed (files under lifeemotions_ai_addon/, excluding docs)
PROD_CHANGES=$(echo "$CHANGED_FILES" | grep '^lifeemotions_ai_addon/' | grep -v '\.md$' || true)

if [ -z "$PROD_CHANGES" ]; then
  echo "No production code changes. Version bump not required."
  exit 0
fi

echo "Production code changes detected:"
echo "$PROD_CHANGES"

# Check if config.yaml version was changed
VERSION_CHANGED=$(git diff "$BASE_SHA" "$HEAD_SHA" -- lifeemotions_ai_addon/config.yaml | grep '^[+-]version:' || true)

if [ -z "$VERSION_CHANGED" ]; then
  echo ""
  echo "::error::Production code changed without version bump in lifeemotions_ai_addon/config.yaml. HA Supervisor uses this version to detect updates — without a bump, different users will get different code for the same version."
  exit 1
fi

echo ""
echo "Version bump detected:"
echo "$VERSION_CHANGED"
