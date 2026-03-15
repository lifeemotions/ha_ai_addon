#!/bin/bash
# Bump the add-on version in config.yaml and CHANGELOG.md, then create a git tag.
#
# Usage:
#   ./scripts/bump_version.sh patch    # 1.0.0 → 1.0.1
#   ./scripts/bump_version.sh minor    # 1.0.0 → 1.1.0
#   ./scripts/bump_version.sh major    # 1.0.0 → 2.0.0
#   ./scripts/bump_version.sh 2.3.1   # set explicit version

set -e

cd "$(dirname "$0")/.."

CONFIG="lifeemotions_ai_addon/config.yaml"
CHANGELOG="lifeemotions_ai_addon/CHANGELOG.md"

# ── Read current version ─────────────────────────────────────────────
current=$(grep '^version:' "$CONFIG" | sed 's/^version: *"\{0,1\}\([^"]*\)"\{0,1\}/\1/')
if [ -z "$current" ]; then
    echo "Error: could not read version from $CONFIG"
    exit 1
fi

IFS='.' read -r major minor patch <<< "$current"

# ── Compute next version ─────────────────────────────────────────────
case "${1:-}" in
    patch)
        next="$major.$minor.$((patch + 1))"
        ;;
    minor)
        next="$major.$((minor + 1)).0"
        ;;
    major)
        next="$((major + 1)).0.0"
        ;;
    "")
        echo "Usage: $0 {patch|minor|major|X.Y.Z}"
        echo ""
        echo "Current version: $current"
        exit 1
        ;;
    *)
        # Validate explicit version format
        if ! echo "$1" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
            echo "Error: invalid version format '$1' (expected X.Y.Z)"
            exit 1
        fi
        next="$1"
        ;;
esac

if [ "$next" = "$current" ]; then
    echo "Version is already $current"
    exit 0
fi

echo "Bumping version: $current → $next"

# ── Check for clean working tree ─────────────────────────────────────
if [ -n "$(git status --porcelain)" ]; then
    echo ""
    echo "Warning: working tree is not clean. Commit or stash changes first?"
    read -p "Continue anyway? [y/N] " confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        exit 1
    fi
fi

# ── Update config.yaml ───────────────────────────────────────────────
sed -i.bak "s/^version: .*/version: \"$next\"/" "$CONFIG"
rm -f "$CONFIG.bak"
echo "Updated $CONFIG"

# ── Update CHANGELOG.md ──────────────────────────────────────────────
today=$(date +%Y-%m-%d)

if [ -f "$CHANGELOG" ]; then
    # Create temp file with new entry inserted after the "# Changelog" heading
    {
        head -1 "$CHANGELOG"
        echo ""
        echo "## [$next] - $today"
        echo ""
        echo "### Changed"
        echo "- (describe your changes here)"
        echo ""
        tail -n +2 "$CHANGELOG"
    } > "$CHANGELOG.tmp"
    mv "$CHANGELOG.tmp" "$CHANGELOG"
    echo "Updated $CHANGELOG — edit the entry before committing"
else
    cat > "$CHANGELOG" << EOF
# Changelog

## [$next] - $today

### Changed
- (describe your changes here)
EOF
    echo "Created $CHANGELOG"
fi

# ── Summary ──────────────────────────────────────────────────────────
echo ""
echo "Next steps:"
echo "  1. Edit $CHANGELOG with your changes"
echo "  2. git add $CONFIG $CHANGELOG"
echo "  3. git commit -m 'Release v$next'"
echo "  4. git tag v$next"
echo "  5. git push origin main --tags"
