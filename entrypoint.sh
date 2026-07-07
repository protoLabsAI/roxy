#!/bin/bash
# roxy (Portfolio Manager) — coder-runtime prep, then hand off to the stock upstream
# launch. roxy runs no coders herself, but the Lead Engineer teams she spins up do:
# their project_board spawn loop dispatches builds to the `proto` acp coder in
# per-feature git worktrees. So this preps the two things a spawned team needs on this
# host — proto's gateway settings and a bound repo checkout — then execs the base
# image's /opt/protoagent/entrypoint.sh (PROTOAGENT_HOME=/sandbox, config/SOUL seed,
# server launch). Secrets are injected by `infisical run` wrapping this script.
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

# --- protoContent working clone (bound by the standing protoContent team) --------
# The Lead Engineer team roxy spins up for protoContent binds this checkout; its
# project_board spawn loop cuts per-feature worktrees off it and the MANAGED-GIT
# harness (ADR 0076) owns branch/commit/push/PR. Needs a real checkout + push auth.
# GH token: ROXY_PC_GH_TOKEN (a protoContent-scoped write PAT) else GH_TOKEN. The
# credential rewrite lives in the tmpfs HOME — written fresh each boot, never persisted.
PC_REPO="${ROXY_PC_REPO:-protoLabsAI/protoContent}"
PC_WORKDIR="${ROXY_PC_WORKDIR:-/sandbox/work/protoContent}"
PC_GH="${ROXY_PC_GH_TOKEN:-${GH_TOKEN:-}}"
if [ -n "$PC_GH" ]; then
    export GH_TOKEN="$PC_GH"          # gh CLI (harness PR create) + coder shell
    git config --global user.name "roxy-protoContent"
    git config --global user.email "roxy@protolabs.studio"
    git config --global --add safe.directory "$PC_WORKDIR"
    git config --global url."https://x-access-token:${PC_GH}@github.com/".insteadOf "https://github.com/"
    mkdir -p "$(dirname "$PC_WORKDIR")"
    if [ -d "$PC_WORKDIR/.git" ]; then
        echo "[roxy] protoContent clone exists -> hard-resetting main to origin/main"
        git -C "$PC_WORKDIR" fetch --prune origin \
            && git -C "$PC_WORKDIR" checkout -B main origin/main \
            && git -C "$PC_WORKDIR" clean -fd \
            || echo "[roxy] WARN: protoContent refresh failed (using existing checkout)"
    else
        echo "[roxy] cloning ${PC_REPO} -> ${PC_WORKDIR}"
        git clone "https://github.com/${PC_REPO}.git" "$PC_WORKDIR" \
            || echo "[roxy] WARN: protoContent clone failed — the team will have no repo"
    fi
else
    echo "[roxy] WARN: no ROXY_PC_GH_TOKEN/GH_TOKEN — skipping protoContent clone (team can't push)"
fi

# --- coder toolchain PREFLIGHT: never start work against a gate that can't run ----
# Early boot smoke of the SAME gate the spawned teams run. The team's project_board
# loop is the AUTHORITATIVE fail-closed preflight (it HOLDS ready work when the gate
# can't run on clean base); this is just an early operator signal before the server is
# even up. The gate is DISCOVERED from the repo, never hard-coded — a transcribed gate
# rots the moment the repo's CI changes or a team is pointed at a different repo (this
# is the same resolution the plugin does for local_gate_cmd: "auto"):
#   1. package.json ci/check/verify script → `pnpm run <it>` (repo-declared; its own
#      CI should call the same target, so this smoke == CI by construction)
#   2. Makefile/justfile ci|check target   → `make/just <it>`
#   3. package.json, none declared         → `pnpm -r --if-present typecheck build test`
#   4. nothing recognized                  → gateless (teams will be too)
# Override with ROXY_PC_GATE. The pnpm store lives under /sandbox so deps persist across
# image rolls (per-worktree install is a warm-cache no-op). Result left in the log and
# /sandbox/.preflight-failed for the operator/board to read.
resolve_gate() {
    local repo="$1" install="pnpm install --frozen-lockfile --prefer-offline"
    if [ -n "${ROXY_PC_GATE:-}" ]; then printf '%s' "$ROXY_PC_GATE"; return; fi
    if [ -f "$repo/package.json" ]; then
        for s in ci check verify; do
            if node -e "process.exit((((require('$repo/package.json').scripts)||{})['$s'])?0:1)" 2>/dev/null; then
                printf '%s && pnpm run %s' "$install" "$s"; return
            fi
        done
        printf '%s && pnpm -r --if-present typecheck && pnpm -r --if-present build && pnpm -r --if-present test' "$install"; return
    fi
    for f in Makefile makefile justfile Justfile; do
        [ -f "$repo/$f" ] || continue
        local runner=make; case "$f" in justfile|Justfile) runner=just;; esac
        for t in ci check; do grep -qE "^${t}:" "$repo/$f" && { printf '%s %s' "$runner" "$t"; return; }; done
    done
    printf ''
}
PC_GATE="$(resolve_gate "$PC_WORKDIR")"
rm -f /sandbox/.preflight-failed
if [ -z "$PC_GATE" ]; then
    echo "[roxy] PREFLIGHT: no gate discovered for $PC_WORKDIR — running gateless (teams will too)"
elif [ -d "$PC_WORKDIR/.git" ] && command -v pnpm >/dev/null 2>&1; then
    pnpm config set store-dir /sandbox/.pnpm-store >/dev/null 2>&1 || true
    echo "[roxy] PREFLIGHT: smoking the discovered gate on clean main -> ${PC_GATE}"
    if ( cd "$PC_WORKDIR" && eval "$PC_GATE" ) >/tmp/preflight.log 2>&1; then
        echo "[roxy] PREFLIGHT OK: gate is runnable — teams may dispatch work."
    else
        echo "[roxy] !!! PREFLIGHT FAILED: the discovered gate does not pass on clean main." >&2
        tail -25 /tmp/preflight.log >&2
        echo "[roxy] !!! Coder environment is broken — do NOT dispatch work until fixed." >&2
        cp /tmp/preflight.log /sandbox/.preflight-failed 2>/dev/null || date > /sandbox/.preflight-failed
    fi
elif [ -d "$PC_WORKDIR/.git" ]; then
    echo "[roxy] !!! PREFLIGHT SKIPPED: pnpm not on PATH." >&2
    date > /sandbox/.preflight-failed
fi

# --- hand off to the stock upstream launch ---------------------------------------
echo "[roxy] prep done -> exec stock protoAgent entrypoint"
exec /opt/protoagent/entrypoint.sh
