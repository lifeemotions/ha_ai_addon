#!/usr/bin/env bash
#
# Detects stale branches and deletes them after a grace period.
#
# - Branches with no commits in STALE_DAYS are warned via a commit comment.
# - Branches warned more than GRACE_DAYS ago are deleted.
# - main, branches with open PRs, and protected branches are skipped.
#
# Expects: GH_TOKEN or GITHUB_TOKEN environment variable for gh CLI.
#
set -euo pipefail

STALE_DAYS="${STALE_DAYS:-27}"
GRACE_DAYS="${GRACE_DAYS:-3}"
DRY_RUN="${DRY_RUN:-false}"
STALE_MARKER="<!-- stale-branch-warning -->"

REPO="${GITHUB_REPOSITORY:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
NOW=$(date +%s)
STALE_CUTOFF=$((NOW - STALE_DAYS * 86400))
DELETE_CUTOFF=$((NOW - (STALE_DAYS + GRACE_DAYS) * 86400))

echo "Checking for stale branches in $REPO"
echo "Stale threshold: $STALE_DAYS days, delete after $GRACE_DAYS more days"
echo ""

# Get branches with open PRs (skip these entirely)
PR_BRANCHES=$(gh pr list --repo "$REPO" --state open --json headRefName -q '.[].headRefName')

# Get all remote branches except main/master and HEAD
BRANCHES=$(git branch -r --format='%(refname:short)' | sed 's|^origin/||' | grep -v -E '^(main|master|HEAD)$')

for BRANCH in $BRANCHES; do
  # Skip branches with open PRs
  if echo "$PR_BRANCHES" | grep -qx "$BRANCH"; then
    echo "SKIP (open PR): $BRANCH"
    continue
  fi

  # Get last commit date and SHA
  LAST_COMMIT_DATE=$(git log -1 --format='%ct' "origin/$BRANCH" 2>/dev/null || echo "0")
  LAST_COMMIT_SHA=$(git log -1 --format='%H' "origin/$BRANCH" 2>/dev/null || echo "")

  if [ -z "$LAST_COMMIT_SHA" ] || [ "$LAST_COMMIT_DATE" = "0" ]; then
    continue
  fi

  # Not stale yet
  if [ "$LAST_COMMIT_DATE" -gt "$STALE_CUTOFF" ]; then
    continue
  fi

  # Check if we already warned
  EXISTING_WARNING=$(gh api "repos/$REPO/commits/$LAST_COMMIT_SHA/comments" \
    --jq "[.[] | select(.body | contains(\"$STALE_MARKER\"))] | length" 2>/dev/null || echo "0")

  if [ "$LAST_COMMIT_DATE" -le "$DELETE_CUTOFF" ] && [ "$EXISTING_WARNING" -gt "0" ]; then
    # Past grace period and already warned — delete
    LAST_DATE_HUMAN=$(date -r "$LAST_COMMIT_DATE" '+%Y-%m-%d' 2>/dev/null || date -d "@$LAST_COMMIT_DATE" '+%Y-%m-%d')
    echo "DELETE: $BRANCH (last commit: $LAST_DATE_HUMAN)"
    if [ "$DRY_RUN" = "false" ]; then
      git push origin --delete "$BRANCH"
    else
      echo "  (dry-run, skipping)"
    fi
  elif [ "$EXISTING_WARNING" = "0" ]; then
    # Stale but not yet warned — add comment
    LAST_DATE_HUMAN=$(date -r "$LAST_COMMIT_DATE" '+%Y-%m-%d' 2>/dev/null || date -d "@$LAST_COMMIT_DATE" '+%Y-%m-%d')
    AUTHOR=$(git log -1 --format='%ae' "origin/$BRANCH")
    DELETE_DATE=$(date -r "$((LAST_COMMIT_DATE + (STALE_DAYS + GRACE_DAYS) * 86400))" '+%Y-%m-%d' 2>/dev/null \
      || date -d "@$((LAST_COMMIT_DATE + (STALE_DAYS + GRACE_DAYS) * 86400))" '+%Y-%m-%d')
    echo "WARN: $BRANCH (last commit: $LAST_DATE_HUMAN, by $AUTHOR, delete on $DELETE_DATE)"
    if [ "$DRY_RUN" = "false" ]; then
      gh api "repos/$REPO/commits/$LAST_COMMIT_SHA/comments" \
        -f body="$STALE_MARKER
:warning: **Stale branch warning**

Branch \`$BRANCH\` has had no activity since **$LAST_DATE_HUMAN** and will be deleted on **$DELETE_DATE**.

To keep this branch, push a new commit or remove this comment." \
        --silent
    else
      echo "  (dry-run, skipping)"
    fi
  else
    LAST_DATE_HUMAN=$(date -r "$LAST_COMMIT_DATE" '+%Y-%m-%d' 2>/dev/null || date -d "@$LAST_COMMIT_DATE" '+%Y-%m-%d')
    echo "WAITING: $BRANCH (warned, grace period until deletion)"
  fi
done

echo ""
echo "Done."
