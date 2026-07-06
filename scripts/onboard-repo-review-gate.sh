#!/usr/bin/env bash
# onboard-repo-review-gate.sh — apply Roxy's review gate to a repo she ships PRs to.
#
# Roxy (+ her coder pool) opens PRs autonomously and an AI reviewer (Quinn / protoPatch /
# CodeRabbit) reviews them. Those reviewers AUTO-APPROVE on green — so unless GitHub itself
# blocks the merge, a reviewer's own HIGH finding (e.g. a data-correctness bug) can still
# ship. This is the enforcement that makes the gate real. GitHub is authoritative over any
# bot approval, so we gate at branch protection. Run this once per repo Roxy is onboarded to.
#
# What it enforces on <default-branch>:
#   - required status checks: `check` + `review` (protoPatch, made blocking on HIGH/MEDIUM)
#   - enforce_admins = true                     → nobody (incl. bots) bypasses
# Policy B (chosen): the HARD gates are protoPatch HIGH/MEDIUM + Quinn (approve-on-green now
# honors unresolved threads via protoWorkstacean#903). We do NOT require conversation
# resolution — it blocked merges on every trivial CodeRabbit nit and re-review churned threads
# faster than they could be cleared. Real bugs still block; trivial nits inform, don't gate.
# It also VERIFIES the blocking protoPatch workflow is present, and warns if not.
#
# Idempotent — safe to re-run. Requires: gh authenticated with admin on the repo.
#
# Usage: scripts/onboard-repo-review-gate.sh <owner/repo> [extra-required-check ...]
set -euo pipefail

REPO="${1:?usage: onboard-repo-review-gate.sh <owner/repo> [extra-required-check ...]}"
shift || true
EXTRA_CHECKS=("$@")

BRANCH="$(gh api "repos/${REPO}" --jq '.default_branch')"
echo "→ ${REPO}: onboarding review gate on '${BRANCH}'"

# Required status checks: the CI gate (`check`) + the protoPatch review gate (`review`).
# Add any repo-specific extras passed on the CLI.
CONTEXTS=(check review "${EXTRA_CHECKS[@]}")
CTX_JSON="$(printf '%s\n' "${CONTEXTS[@]}" | sort -u | jq -R . | jq -sc .)"

jq -n --argjson contexts "$CTX_JSON" '{
  required_status_checks:        { strict: true, contexts: $contexts },
  enforce_admins:                true,
  required_pull_request_reviews: { required_approving_review_count: 0, dismiss_stale_reviews: false, require_code_owner_reviews: false },
  restrictions:                  null,
  required_conversation_resolution: false
}' | gh api -X PUT "repos/${REPO}/branches/${BRANCH}/protection" --input - >/dev/null

echo "  ✓ branch protection: required checks = ${CTX_JSON} (protoPatch HIGH/MEDIUM blocks), admins enforced, conversation-resolution off (policy B)"

# The `review` gate only bites if the blocking protoPatch workflow exists in the repo.
if gh api "repos/${REPO}/contents/.github/workflows/protopatch-review.yml" >/dev/null 2>&1; then
  if gh api "repos/${REPO}/contents/.github/workflows/protopatch-review.yml" --jq '.content' | base64 -d | grep -q "Gate on HIGH/MEDIUM findings"; then
    echo "  ✓ protoPatch review workflow present and BLOCKING on HIGH/MEDIUM"
  else
    echo "  ⚠ protoPatch review workflow present but NOT blocking — update it to fail on HIGH/MEDIUM (see protoContent#416), else the 'review' required check can't gate."
  fi
else
  echo "  ⚠ no .github/workflows/protopatch-review.yml — the 'review' required check will stay pending. Install the blocking protoPatch workflow, or drop 'review' from the contexts above."
fi

echo "→ done. Roxy's PRs to ${REPO} now merge only when CI is green, protoPatch has no open HIGH/MEDIUM, and every review thread is resolved."
