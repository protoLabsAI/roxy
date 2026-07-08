#!/bin/bash
# roxy (Portfolio Manager) — coder-runtime prep, then hand off to the stock upstream
# launch. roxy runs no coders herself, but the Lead Engineer teams she spins up do:
# their project_board spawn loop dispatches builds to the `proto` acp coder in
# per-feature git worktrees. So this preps the two things a spawned team needs on this
# host — proto's gateway settings and git push auth (roxy keeps no standing checkout; each
# on-demand team clones its own repo) — then execs the base image's
# /opt/protoagent/entrypoint.sh (PROTOAGENT_HOME=/sandbox, config/SOUL seed, server launch).
# Secrets are injected by `infisical run` wrapping this script.
set -e

echo "[roxy] coder-runtime prep"

# --- proto (the teams' coder) -> the fleet gateway -------------------------------
# proto reads ~/.proto/settings.json. /home/sandbox is tmpfs, so write it at START.
# Every model points at the gateway (OPENAI_API_KEY = gateway key). Env-tunable.
if command -v proto >/dev/null 2>&1; then
    PROTO_GATEWAY_URL="${PROTO_GATEWAY_URL:-http://gateway:4000/v1}"
    PROTO_MODEL="${PROTO_MODEL:-protolabs/reasoning}"
    mkdir -p /home/sandbox/.proto
    cat > /home/sandbox/.proto/settings.json <<JSON
{
  "modelProviders": {
    "openai": [
      { "id": "protolabs/reasoning", "name": "protolabs/reasoning (gateway)", "baseUrl": "${PROTO_GATEWAY_URL}", "envKey": "OPENAI_API_KEY", "generationConfig": { "contextWindowSize": 1000000 } },
      { "id": "protolabs/smart", "name": "protolabs/smart (gateway)", "baseUrl": "${PROTO_GATEWAY_URL}", "envKey": "OPENAI_API_KEY", "capabilities": { "vision": true }, "generationConfig": { "contextWindowSize": 262144 } },
      { "id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6 (gateway)", "baseUrl": "${PROTO_GATEWAY_URL}", "envKey": "OPENAI_API_KEY", "capabilities": { "vision": true }, "generationConfig": { "contextWindowSize": 200000 } }
    ]
  },
  "security": { "auth": { "selectedType": "openai" } },
  "model": { "name": "${PROTO_MODEL}" }
}
JSON
    echo "[roxy] proto -> gateway (${PROTO_GATEWAY_URL}, default model ${PROTO_MODEL})"
fi

# --- git push auth for on-demand teams (no standing clone) -----------------------
# roxy keeps NO standing repo checkout now — she's the ecosystem PM and spins a Lead
# Engineer team up per project on demand (`portfolio_spinup_team(name, repo)`); THAT team
# clones its own repo and the MANAGED-GIT harness (ADR 0076) owns branch/commit/push/PR.
# So there's nothing to pre-clone or preflight at boot — but every spawned team still needs
# push auth, which it inherits from this global git config in the shared (tmpfs) HOME. The
# token (GH_TOKEN, from ROXY_GH_TOKEN / GITHUB_TOKEN) must carry write on roxy's ecosystem
# repos (protoAgent, protoLab, ORBIS). Written fresh each boot, never persisted.
GH="${ROXY_GH_TOKEN:-${GH_TOKEN:-}}"
if [ -n "$GH" ]; then
    export GH_TOKEN="$GH"             # gh CLI (harness PR create) + coder shell
    git config --global user.name "roxy"
    git config --global user.email "roxy@protolabs.studio"
    git config --global url."https://x-access-token:${GH}@github.com/".insteadOf "https://github.com/"
    echo "[roxy] git push auth configured for spawned teams"
else
    echo "[roxy] WARN: no ROXY_GH_TOKEN/GH_TOKEN — spawned teams won't be able to push"
fi

# --- hand off to the stock upstream launch ---------------------------------------
echo "[roxy] prep done -> exec stock protoAgent entrypoint"
exec /opt/protoagent/entrypoint.sh
