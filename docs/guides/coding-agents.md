# Spawn CLI coding agents (ACP)

::: warning Prefer `delegate_to`
The standalone `code_with` tool is **deprecated**. The unified [delegate
registry](/guides/delegates) (ADR 0025) does the same thing via an **`acp`**
delegate — `delegate_to(target, query)` — and is managed/hot-swappable from the
console. This guide still explains the ACP mechanics, which both paths share.
:::

An **optional, opt-in plugin** ([ADR 0024](/adr/0024-spawn-cli-coding-agents-acp))
that lets the lead agent hand a real coding job to a purpose-built **CLI coding
agent** — protoCLI (`proto`), Claude Code, Codex, Gemini CLI — and get the result
back.

Where `task()` delegates to an in-process LLM subagent and `peer_consult` talks
to a remote A2A peer, **`code_with(agent, task)`** spawns a coding agent that
carries its own file access, shell, repo-map, and edit/verify loop — so it can
read/edit/run code in a repo far better than a generic tool loop. It drives the
coding agent over the [Agent Client Protocol](https://agentclientprotocol.com)
(ACP): JSON-RPC 2.0 over the child's stdin/stdout. protoAgent is the ACP
*client*; `proto --acp` is the matching server.

> **Security:** a configured coding agent gets **file + shell access in its
> workdir** (auto-allowed, confined to that directory — see
> [Permission posture](#permission-posture)). The plugin therefore ships
> **disabled with no agents** — you enable it *and* declare agents explicitly.

## Enable it

The coding agent runs as a local subprocess, so this is configured in YAML, not
the in-app Settings (each agent grants local authority and deserves a deliberate
edit):

```yaml
# config/langgraph-config.yaml
plugins:
  enabled: [coding_agent]

coding_agent:
  default_timeout_s: 600          # coding is slow; per-agent override below
  agents:
    - name: proto                 # the name the LLM passes to code_with(agent=…)
      command: proto              # binary on PATH
      args: ["--acp"]             # ACP server mode
      workdir: ~/dev/my-repo      # session cwd — the confinement boundary
      # env: { SOME_KEY: value }  # optional extra env, merged over the process env
      # timeout_s: 900            # optional per-agent override (seconds)
      # permissions: allowlist    # auto (default) | allowlist | readonly
      # confirm: true             # ask the operator before each code_with call
```

Enabling plugins needs a **restart** (plugin tools wire once at process init).
On boot you'll see `[coding_agent] registered code_with for N agent(s)`.

### Other coding agents

Any agent that speaks ACP works — just point `command`/`args` at it:

```yaml
  agents:
    - name: proto
      command: proto
      args: ["--acp"]
      workdir: ~/dev/my-repo
    - name: claude-code
      command: npx
      args: ["@zed-industries/claude-code-acp"]
      workdir: ~/dev/my-repo
    - name: codex
      command: codex
      args: ["acp"]
      workdir: ~/dev/my-repo
    - name: gemini
      command: gemini
      args: ["--experimental-acp"]
      workdir: ~/dev/my-repo
```

The binary must be installed and on the `PATH` of the process running protoAgent.
A missing binary returns a clear error string to the agent (it doesn't crash).

## Use it

The lead agent calls the tool; the configured agent names appear in the tool's
description so the model knows what it can pass:

```
code_with(agent="proto", task="Add a GET /healthz route to server/, wire it
into the app, and run the tests. Report what you changed.")
```

Notes for whoever writes the `task`:

- The coding agent **does not see this conversation** — make `task` a
  self-contained brief: the goal, the relevant files if known, and the
  definition of done ("run the tests", "and lint").
- You **cannot** choose the directory — each agent works in its pre-configured
  `workdir`. To work in a different repo, configure another agent.
- The call **blocks** until the turn finishes (coding is slow). The default
  timeout is `default_timeout_s` (600s) unless the agent overrides it.
- **Follow-up calls to the same agent continue the same session** — so you can
  iterate: `code_with(agent="proto", task="now also add a test for it")`.

## Permission posture

A coding agent works in its **config-pinned workdir** (`code_with` takes only
`agent` + `task`, never a path — the model can't aim it elsewhere) and uses its
*own* file/shell access there: protoAgent advertises no client-served
`fs`/`terminal` capability. When the coding agent asks to do something risky it
sends a `session/request_permission`, which protoAgent answers with the agent's
**permission policy**:

| `permissions` | Behaviour |
|---|---|
| `auto` *(default)* | Allow everything — the agent self-governs within its workdir. |
| `allowlist` | Allow all action kinds **except** `execute` and `delete` (override with `allow_kinds` / `deny_kinds`). |
| `readonly` | Allow only read-like kinds (`read`, `search`, `fetch`, …); deny edits, shell, and deletes. |

Action kinds come from the ACP request (`toolCall.kind`: `read` / `edit` /
`execute` / `delete` / `fetch` / `move` / `search` / …). Tune a policy per agent:

```yaml
    - name: proto
      command: proto
      args: ["--acp"]
      workdir: ~/dev/my-repo
      permissions: allowlist
      deny_kinds: [execute, delete]   # the allowlist default, shown explicitly
```

### Per-call consent gate

Set `confirm: true` on an agent and `code_with` asks the operator to approve
**before each call** to that agent (via `ask_human` — the turn parks as
`input-required` until you reply `yes`):

```yaml
    - name: proto
      command: proto
      args: ["--acp"]
      workdir: ~/dev/my-repo
      confirm: true
```

> **Per-action** live HITL (approve each individual edit/shell command as the
> coding agent works) is **not** available: it would require pausing a blocking
> subprocess session mid-turn, which LangGraph's checkpoint/resume model can't do
> without re-running the tool. Use `permissions: readonly`/`allowlist` for
> deterministic per-action control, and `confirm` for a per-call human gate.

### Environment

The subprocess **inherits protoAgent's environment** (plus any per-agent `env`).
Run protoAgent under an account whose ambient credentials you're willing to lend
the coding agent, or scope the `workdir` to a throwaway checkout.

### In a container, wired to a gateway

The setup above assumes the coder binary is already on `PATH` — true for a local run,
but a **containerized** protoAgent starts from a bare image with no coder and no model
credentials. Two things to add to your **deploy** (not the template — this is your
Dockerfile + entrypoint, `COPY . /opt/protoagent/` already ships your config):

**1. Bake the coder into the image.** For `proto` (a Node CLI), that's Node + one
`npm i -g`; the other adapters install the same way (`@agentclientprotocol/claude-agent-acp`,
`@zed-industries/codex-acp`, …):

```dockerfile
ARG PROTOCLI_VERSION=latest
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g "@protolabsai/proto@${PROTOCLI_VERSION}" \
    && proto --version   # fail the build if it didn't land
```

**2. Point the coder at your gateway, not a cloud key.** A CLI coder normally wants its
own provider API key. To reuse the **same OpenAI-compatible gateway** protoAgent already
uses — one key, one bill, local models available — write the coder's config at
**entrypoint** rather than baking it: the sandbox `$HOME` is typically a tmpfs mount that
would shadow a baked file, and writing at start keeps it idempotent and env-tunable.
`proto` reads `~/.proto/settings.json`; the shape differs per CLI but the idea is the same
(base URL → your gateway, key from an env var):

```sh
# entrypoint.sh — before `exec … python -m server`
if command -v proto >/dev/null 2>&1; then
    GATEWAY_URL="${CODER_GATEWAY_URL:-http://gateway:4000/v1}"
    mkdir -p "$HOME/.proto"
    cat > "$HOME/.proto/settings.json" <<JSON
{ "modelProviders": { "openai": [
    { "id": "my/coder-model", "baseUrl": "${GATEWAY_URL}", "envKey": "OPENAI_API_KEY" }
  ] },
  "security": { "auth": { "selectedType": "openai" } },
  "model": { "name": "my/coder-model" } }
JSON
fi
```

Because the ACP child **inherits protoAgent's environment** (see above), `OPENAI_API_KEY`
— the gateway key protoAgent already has — flows straight through, and so does anything
else the coder needs from its shell (e.g. a `GH_TOKEN` for `git push` / `gh pr create`
from its `workdir` — run by the coder itself in the default mode, or by the framework's
git harness under `manage_git: true`, §Managed git below). No second secret store.

> The `workdir` still has to be a real, writable checkout of the repo the coder edits —
> provision it however you like (bake a clone, or `git clone` it at entrypoint with a
> token). A neat trick to keep the token out of the persisted `.git/config`: set it via a
> global `url.<https://x-access-token:$TOK@github.com/>.insteadOf` rewrite in the tmpfs
> `$HOME` — written fresh each boot, never stored in the volume.

### Parallel builds: a worktree-backed coder pool

One coder in one `workdir` is **sequential** — a second `code_with`/`delegate_to` into the
same directory while the first is mid-edit will collide (shared working tree + index +
branch). An orchestrator that wants to build several independent things at once (a lead
fanning issues out to a crew) needs each concurrent coder in its **own** working tree.

The clean way is a **pool of coders over git worktrees**: linked worktrees share one clone's
`.git` object store but have an isolated working dir, index, and checked-out branch — exactly
the isolation concurrent coders need, without N full clones.

**1. Provision the worktrees at entrypoint** (cap `N` = your concurrency budget):

```sh
git clone https://github.com/you/repo /work/repo         # the base clone (on main)
for i in $(seq 1 "${CODER_POOL:-3}"); do
    git -C /work/repo worktree add --force -B "pool-$i" "/work/wt-$i" origin/main
done
# recreate them fresh each boot — worktrees hold no state you keep (coders push to origin)
```

**2. Declare one coder per worktree** — same binary, distinct `workdir`:

```yaml
delegates:
  - { name: coder-1, type: acp, command: proto, args: ["--acp"], workdir: /work/wt-1, manage_git: true }
  - { name: coder-2, type: acp, command: proto, args: ["--acp"], workdir: /work/wt-2, manage_git: true }
  - { name: coder-3, type: acp, command: proto, args: ["--acp"], workdir: /work/wt-3, manage_git: true }
```

**3. Fan out.** The agent issues several `delegate_to(coder-N, …)` calls in one turn (the
tool node runs a turn's tool calls concurrently), or several `delegate_to(…,
background=True)` calls — each lands on a free coder in its own worktree. The pool size is
the cap; extra work queues.

Two caveats worth planning for: worktrees don't share `node_modules`/build caches (install
per-worktree, or share a package store), and two parallel PRs that touch the same file will
conflict at *merge* time (normal parallel-dev friction — rebase the loser), not at build time.

### Managed git: the framework owns branch/commit/push/PR (ADR 0076)

By default the coder owns its own git lifecycle — fine for a single supervised coder in a
disposable checkout. At pool scale it is the reliability ceiling: coders invent colliding
branch names (linked worktrees refuse the same branch twice), report "done" without ever
pushing, open duplicate PRs when one item is fanned to several coders, and `git add -A`
their scratch into the diff. Every one of those is a *deterministic* step an LLM was asked
to perform.

`manage_git: true` on an `acp` delegate moves the whole lifecycle into the framework
(`plugins/coding_agent/git_harness.py`); the coder is told to **edit files and run tests
only**. Per dispatch, the harness:

1. derives a stable **work-item id** — `delegate_to(…, item_id="issue-42")`, or a hash of
   the query text when omitted — and **claims** it: a second dispatch of an in-flight item
   (any coder) is refused instead of duplicated, and an already-open PR for the item's
   branch short-circuits before the coder even runs;
2. mints the branch deterministically (`<branch_prefix>/<slug>-<id7>`, prefix defaults to
   the delegate name) and cuts it from **fresh `origin/<base_branch>`** — never local HEAD;
3. after the coder finishes: refuses to commit on the base branch (work stays recoverable
   in the worktree — no completion theater), scans the diff for secrets, commits on the
   coder's behalf, rebases onto fresh base (a conflict is reported and pushed as-is, not
   fatal), pushes with `--force-with-lease`, **verifies the remote SHA actually moved**,
   and opens the PR idempotently (re-runs reuse the existing PR).

The lifecycle is idempotent to a coder that did partial git anyway (its commits are
adopted, not duplicated), and the run's outcome — branch, verified push, PR URL, or the
exact reason nothing was published — is appended to the coder's reply.

```yaml
delegates:
  - name: coder-1
    type: acp
    command: proto
    args: ["--acp"]
    workdir: /work/wt-1
    manage_git: true       # framework-owned git lifecycle
    base_branch: main      # branches cut from origin/<base>; PRs target it
    # branch_prefix: wt-1  # optional; defaults to the delegate name
```

The PR step needs `gh` on PATH and a `GH_TOKEN`/`GITHUB_TOKEN` (the same container env as
above). Without them the branch is still pushed and verified — the reply just reports the
PR step's failure instead of a URL.

## How it works

```
code_with(agent, task)
  → AcpClient (plugins/coding_agent/acp_client.py)
      → spawn `command args` in workdir, JSON-RPC 2.0 over its stdio:
        initialize → session/new(cwd) → session/prompt(task)
      ← session/update {agent_message_chunk}   → accumulated into the answer
      ← session/update {tool_call, title}        → narrated (logged)
      ← session/request_permission               → auto-allowed
  → returns the agent's final message text
```

One `AcpClient` (subprocess + session) is **cached per agent** so follow-up calls
continue the thread; a per-agent lock serializes turns (a session is a single
conversation — `task_batch` won't interleave two prompts on one).

## Eval it

A gated eval case (`code_with_delegation`) verifies end-to-end delegation against
a live agent. It's skipped unless you opt in — configure an agent, then:

```bash
export EVAL_CODING_AGENT=1
python -m evals.runner --tasks code_with_delegation
```

It drives a real A2A turn that asks the agent to use `code_with`, and asserts
(via the audit channel) that the tool fired. Without `EVAL_CODING_AGENT` set it
`SKIP`s, so it never breaks the default board. See [Eval your fork](/guides/evals).

See [Plugins](/guides/plugins) for the plugin model in general, and
[ADR 0024](/adr/0024-spawn-cli-coding-agents-acp) for the design rationale.
