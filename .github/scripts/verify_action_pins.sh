#!/usr/bin/env bash
# Root-F supply-chain guard.
# Exit 1 if any GitHub Actions `uses:` ref is a mutable tag instead of a full
# 40-char commit SHA, or if backend-deploy still deploys the mutable :main tag.
set -uo pipefail

cd "$(dirname "$0")/../.." || exit 2
WF=.github/workflows
status=0

# 1) Every `uses:` ref must be pinned to a 40-hex commit SHA.
mutable=$(grep -REn 'uses:[[:space:]]*[^@[:space:]]+@' "$WF" \
  | grep -vE '@[0-9a-f]{40}([[:space:]]|$)')
if [ -n "$mutable" ]; then
  echo "UNPINNED ACTION TAGS FOUND:"
  echo "$mutable"
  status=1
fi

# 2) backend-deploy must run the immutable digest, not the mutable :main tag.
if grep -qE 'REMOTE_IMAGE:[[:space:]]*\$\{\{[[:space:]]*needs\.build\.outputs\.main_tag' "$WF/backend-deploy.yml"; then
  echo "REMOTE_IMAGE still uses the mutable main_tag (must be needs.build.outputs.digest)"
  status=1
fi

if [ "$status" -eq 0 ]; then
  echo "OK: all actions SHA-pinned; backend deploys by digest."
fi
exit "$status"
