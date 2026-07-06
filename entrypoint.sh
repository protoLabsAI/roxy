#!/bin/bash
# protoAgent — container entrypoint
# Secrets should be injected by `infisical run` (or similar) wrapping
# this script. See the deployment stack for the exact invocation.

set -e

AGENT_NAME="${AGENT_NAME:-protoagent}"

echo "[entrypoint] Starting ${AGENT_NAME}"

# tmpfs home — create dirs inside it
mkdir -p /home/sandbox/.local

# Persistent volume dirs (mounted by the stack)
mkdir -p /sandbox/audit /sandbox/knowledge

# Copy persona into workspace if one is shipped
if [ -f /opt/protoagent/config/SOUL.md ]; then
    cp /opt/protoagent/config/SOUL.md /sandbox/SOUL.md
fi

# protoCLI (proto) → the fleet gateway. proto is the ACP coder the coding_agent plugin
# drives; it reads ~/.proto/settings.json. /home/sandbox is tmpfs, so write the config at
# START (idempotent) — a baked copy would be shadowed by the tmpfs mount. Points every model
# at the gateway (OPENAI_API_KEY = the gateway key); default coder model protolabs/reasoning
# (local, no cloud cost). Env-tunable: PROTO_GATEWAY_URL, PROTO_MODEL.
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
    echo "[entrypoint] proto → gateway (${PROTO_GATEWAY_URL}, default model ${PROTO_MODEL})"
fi

# protoContent working clone — the coder's confined workdir (coding_agent.workdir).
# proto branches/commits/pushes/opens the PR here, so it needs a real git checkout + auth.
# GH token: ROXY_PC_GH_TOKEN (preferred; a protoContent-scoped write PAT) else GH_TOKEN.
# The credential rewrite lives in the tmpfs HOME (~/.gitconfig) — written fresh each boot,
# never persisted to the /sandbox volume, so the token isn't stored in the clone's .git.
# gh + the coder read GH_TOKEN from the process env (inherited by the ACP child).
PC_REPO="${ROXY_PC_REPO:-protoLabsAI/protoContent}"
PC_WORKDIR="${ROXY_PC_WORKDIR:-/sandbox/work/protoContent}"
PC_GH="${ROXY_PC_GH_TOKEN:-${GH_TOKEN:-}}"
if [ -n "$PC_GH" ]; then
    export GH_TOKEN="$PC_GH"          # gh CLI + coder shell
    git config --global user.name "roxy-protoContent"
    git config --global user.email "roxy@protolabs.studio"
    git config --global --add safe.directory "$PC_WORKDIR"
    # Transport-time token injection — origin stays the plain URL; the token is never
    # written into .git/config on the persistent volume.
    git config --global url."https://x-access-token:${PC_GH}@github.com/".insteadOf "https://github.com/"
    mkdir -p "$(dirname "$PC_WORKDIR")"
    if [ -d "$PC_WORKDIR/.git" ]; then
        # Hard-reset main to origin, not `pull --ff-only`: a coder that commits onto local
        # main (instead of a branch) leaves it AHEAD, so ff-only silently no-ops and the
        # cruft persists — then the NEXT feature branch, cut from the dirty main, drags those
        # stray commits into its PR. A boot is a clean-slate moment: reset to origin/main and
        # clean untracked scratch (gitignored files like node_modules are left alone) so every
        # branch starts from a pristine main.
        echo "[entrypoint] protoContent clone exists → hard-resetting main to origin/main"
        git -C "$PC_WORKDIR" fetch --prune origin \
            && git -C "$PC_WORKDIR" checkout -B main origin/main \
            && git -C "$PC_WORKDIR" clean -fd \
            || echo "[entrypoint] WARN: protoContent refresh failed (using existing checkout)"
    else
        echo "[entrypoint] cloning ${PC_REPO} → ${PC_WORKDIR}"
        git clone "https://github.com/${PC_REPO}.git" "$PC_WORKDIR" \
            || echo "[entrypoint] WARN: protoContent clone failed — the coder will have no repo"
    fi

    # Coder worktree pool — parallel isolated coders. Roxy fans independent build items
    # across a pool of ACP coders (proto-1..proto-N in coding_agent config), each confined
    # to its OWN linked git worktree of this clone: shared .git object store, isolated
    # working dir + index + branch, so concurrent builds never collide. Cap = ROXY_CODER_POOL
    # (default 3; keep it == the number of proto-N agents in the config). Recreated fresh off
    # origin/main each boot (worktrees hold no state we keep — coders push to origin). Each
    # coder still branches off origin/main per task (per SOUL).
    POOL="${ROXY_CODER_POOL:-3}"
    if [ -d "$PC_WORKDIR/.git" ]; then
        git -C "$PC_WORKDIR" worktree prune 2>/dev/null || true
        i=1
        while [ "$i" -le "$POOL" ]; do
            WT="/sandbox/work/wt-$i"
            git config --global --add safe.directory "$WT"
            git -C "$PC_WORKDIR" worktree remove --force "$WT" 2>/dev/null || rm -rf "$WT"
            git -C "$PC_WORKDIR" worktree add --force -B "pool-$i" "$WT" origin/main 2>/dev/null \
                && echo "[entrypoint] coder worktree $WT ready" \
                || echo "[entrypoint] WARN: could not add worktree $WT"
            i=$((i+1))
        done
        echo "[entrypoint] coder pool: ${POOL} worktree(s) at /sandbox/work/wt-1..${POOL}"
    fi
else
    echo "[entrypoint] WARN: no ROXY_PC_GH_TOKEN/GH_TOKEN — skipping protoContent clone (coder can't push)"
fi

# ADR 0023: server.py was promoted to a `server/` package. Launch it as a
# module with the install dir on PYTHONPATH so the package (and its sibling
# top-level modules: paths, events, graph, …) resolve, while keeping the
# agent's workspace (/sandbox) as the working directory.
#
# Bind all interfaces inside the container — the boundary is the published port
# + network policy, not the in-container bind. (The server defaults to loopback
# for local/desktop runs; PROTOAGENT_HOST overrides either way.)
exec env PYTHONPATH="/opt/protoagent${PYTHONPATH:+:$PYTHONPATH}" \
    python -m server --host "${PROTOAGENT_HOST:-0.0.0.0}"
