#!/usr/bin/env bash
# scripts/cut-release.sh
# Sync OSS repo, run tests, open PR, and optionally tag a release.
#
# Usage:
#   ./scripts/cut-release.sh <version>         # e.g. v0.9.1
#   ./scripts/cut-release.sh <version> --merge # also merge PR + tag after CI

set -euo pipefail

OSS_REPO="${HOME}/code/filament-iq"
PRIVATE_SCRIPTS="$(cd "$(dirname "$0")" && pwd)"

VERSION="${1:-}"
MERGE_MODE=false
if [[ "${2:-}" == "--merge" ]]; then
  MERGE_MODE=true
fi

if [[ -z "$VERSION" ]]; then
  echo "Usage: $0 <version> [--merge]"
  echo "  e.g. $0 v0.9.1"
  exit 1
fi

# Validate version format
if ! [[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "ERROR: version must be in format vX.Y.Z (got: $VERSION)"
  exit 1
fi

echo "=== Filament IQ Release: ${VERSION} ==="
echo ""

# Step 1: sync files
echo "── Step 1: Sync OSS repo ──"
"${PRIVATE_SCRIPTS}/sync-oss.sh" --copy
echo ""

# Step 2: run OSS test suite
echo "── Step 2: Run OSS tests ──"
cd "$OSS_REPO"
if ! python3 -m pytest tests/ -q --tb=short; then
  echo "ERROR: OSS tests failed — aborting release"
  exit 1
fi
echo ""

# Step 3: update CHANGELOG date if placeholder exists
echo "── Step 3: Check CHANGELOG ──"
TODAY=$(date +%Y-%m-%d)
if grep -q "\[${VERSION}\] - UNRELEASED" CHANGELOG.md 2>/dev/null; then
  sed -i.bak "s/\[${VERSION}\] - UNRELEASED/[${VERSION}] - ${TODAY}/" CHANGELOG.md
  rm -f CHANGELOG.md.bak
  echo "  Updated CHANGELOG date to ${TODAY}"
else
  echo "  CHANGELOG entry for ${VERSION} already dated or not found — skipping"
fi
echo ""

# Step 4: commit and push release branch
echo "── Step 4: Commit + push release branch ──"
BRANCH="release/${VERSION}"
git checkout -b "$BRANCH" 2>/dev/null || git checkout "$BRANCH"
git add -A
git diff --cached --quiet && echo "  Nothing to commit — files already in sync" || \
  git commit -m "release: sync ${VERSION} from home_assistant repo"
git push origin "$BRANCH"
echo "  Branch pushed: ${BRANCH}"
echo ""

# Step 5: open PR
echo "── Step 5: Open PR ──"
# Extract changelog section for PR body
CHANGELOG_BODY=$(awk "/## \[${VERSION}\]/,/## \[v/" CHANGELOG.md 2>/dev/null | \
  head -n -1 | tail -n +2 || echo "See CHANGELOG.md for details.")

PR_URL=$(gh pr create \
  --title "release: ${VERSION}" \
  --body "${CHANGELOG_BODY}" \
  --base main \
  --head "$BRANCH" 2>/dev/null || gh pr view "$BRANCH" --json url -q .url)

echo "  PR: ${PR_URL}"
echo ""

if ! $MERGE_MODE; then
  echo "✅ Release ${VERSION} ready for review"
  echo "   PR: ${PR_URL}"
  echo ""
  echo "After PR merges, tag the release:"
  echo "  cd ${OSS_REPO} && git checkout main && git pull"
  echo "  git tag ${VERSION} && git push origin ${VERSION}"
  echo "  gh release create ${VERSION} --title '${VERSION}' --generate-notes --latest"
  exit 0
fi

# Step 6: merge PR + tag (--merge mode only)
echo "── Step 6: Merge PR + tag ──"
gh pr merge "$BRANCH" --squash --delete-branch
git checkout main && git pull origin main
git tag "$VERSION"
git push origin "$VERSION"

# Step 7: create GitHub release
echo "── Step 7: Create GitHub release ──"
gh release create "$VERSION" \
  --title "${VERSION}" \
  --notes "${CHANGELOG_BODY}" \
  --latest

echo ""
echo "✅ Released ${VERSION}"
gh release view "$VERSION"
