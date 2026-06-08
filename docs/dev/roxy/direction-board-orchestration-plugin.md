# Direction — Board-driven coding orchestration as a protoAgent plugin

**Status:** Proposed (direction doc — not yet an upstream ADR)
**Date:** 2026-06-07
**Owner:** Roxy / Josh
**Supersedes (intent):** the standalone protoMaker board engine, for the fleet's purposes

> A roxy-side direction doc, written with ADR rigor. It proposes collapsing
> protoMaker's heavy board engine into a lean **protoAgent plugin** where Roxy is
> the orchestrator and real coding-agent CLIs (Claude Code / Codex / protoCLI) do
> the feature work, spawned over **ACP**. If adopted into protoAgent core it should
> graduate to a numbered upstream ADR. Lives in `docs/dev/roxy/` (not `docs/adr/`)
> so it doesn't collide with upstream ADR numbering on sync.

---

## Context

### The problem
protoMaker is a large, standalone board+execution engine. In practice the fleet
uses a thin slice of it (a feature board + an auto-mode loop), and the rest is
carrying cost: bespoke worktree management, a homegrown agent-execution runtime,
custom workflow/status machinery, and review/remediation plumbing that we
repeatedly have to debug (the `model=reasoning` review break, the 82-phantom
backlog drift, the merge-limbo and dispatch-drop fixes, etc.). The engine does a
lot we don't use and don't want to forward-port.

Meanwhile Roxy is already a protoAgent agent that **operates** protoMaker over A2A.
The orchestration intelligence lives in Roxy; protoMaker is mostly a board + a
runtime. That split is the opportunity.

### The vision (Josh)
- **Auto-mode becomes a protoAgent plugin**, not a separate engine.
- **The board IS the plugin face** — a combo Linear-style list **and** Kanban
  board, as console tabs — and Roxy manages projects through it.
- **Roxy spawns real coding-agent CLIs** (protoCLI / Codex / Claude Code) over
  **ACP** to implement features, instead of a homegrown execution runtime.
- **Shed protoMaker's cruft** — keep only the lean board + the orchestration loop.

### What the research says (2026-06-07; see References)
Three findings, each independently corroborated:

1. **This is OpenAI's actual play.** "OpenAI Symphony" is their internal setup
   using **Linear as the agent control plane**: a loop pulls `Ready` issues, a
   coding agent works one, opens a PR, advances `In Progress → In Review`, and a
   merge webhook sets `Done` (CI failure bounces it back). Reported ~5× PR
   throughput. The vision above is a near-exact restatement of it.

2. **Orchestrator-worker is the winning pattern for *coding*.** Across Anthropic's
   multi-agent research system, OpenAI's Agents SDK ("manager-as-tools"),
   LangGraph's supervisor, and the coding-orchestrator literature, the consensus
   for build-out work is **central orchestrator + isolated workers (git worktrees)
   + shared-state board + verification gates** — decisively over decentralized
   peer-handoffs, because coding has hard dependency graphs, file-level isolation
   needs a coordinator, and results must be integrated+verified. Roxy's existing
   topology already *is* this shape.

3. **The execution mechanism is standardized and ready: ACP.** Zed's **Agent
   Client Protocol** (JSON-RPC over stdio; `initialize → session/new →
   session/prompt`, streaming `session/update`, client-gated permissions) lets one
   client drive *any* compliant coding CLI. Claude Code (`claude-code-acp`), Codex,
   Gemini CLI (`--experimental-acp`), and opencode all speak it today. One client
   implementation → heterogeneous coders.

---

## Decision

Build a lean **`project-board` protoAgent plugin** and an **ACP spawn loop**, with
Roxy as the orchestrator. Collapse protoMaker (for the fleet) to a board table + a
spawn loop, both inside protoAgent.

> **Guiding constraint — API-first. No UI until the API works.** The board is a
> data model + an HTTP API + the `delegate_to` orchestration loop, and the entire
> flow (create project → decompose into Ready features → delegate to coder → PR →
> delegate to Quinn → merge → Done) must run **headlessly, driven by API/A2A
> calls**, before any UI is built. The combo list/Kanban view (D5) is a *later
> projection* over a proven API — explicitly deferred, not part of the first cut.

### D1 — Auto-mode is a protoAgent plugin, not a separate engine
The orchestration loop (pull `Ready` → spawn coder → open PR → advance → merge)
ships as a plugin using the existing reach (`register_tool`, `register_router`,
`register_surface` for the background loop, `register_a2a_skill`, ADR 0026 views).
No core edits. Roxy = stock protoAgent + the protomaker plugin (already built) +
this board-orchestration capability folded in (or a sibling plugin).

### D2 — Roxy is the orchestrator/supervisor
Roxy owns: project decomposition into `Ready` features, the pull/dispatch
decision, monitoring, and the integration/verification gate (delegating PR review
to Quinn over A2A, as today). Workers are ephemeral coding-agent CLIs, not
long-lived peers. This is the supervisor / "manager-as-tools" pattern — the one the
research says wins for coding.

### D3 — A lean 6-state board (the only board we keep)
```
Backlog → Ready → In Progress → In Review → Done
                       │
                       └── Blocked  (a flag off In Progress, not a lane)
```
- **Ready gate is the highest-ROI piece:** a feature is `Ready` only with a
  self-sufficient spec + acceptance criteria ("a junior could pick it up"). The
  orchestrator pulls **only** from `Ready`. Under-specified work is the #1
  documented failure mode.
- **`Done` is set by exactly one external transition: the merge webhook.**
  (The 82-phantom-backlog drift was precisely this edge missing — wire
  merge/close → Done explicitly, and nothing else sets Done.)
- Minimal card schema — **~10 fields, hard-coded, no custom-field engine:**
  `id, title, status, spec, acceptance_criteria, assignee_agent, repo,
  base_branch (+ worktree), pr_url, priority, project_id(parent, one level),
  session_id?`.

### D4 — Drive coders + reviewers via the existing `delegate_to` (already built)
**The spawn primitive already exists in protoAgent — we compose it, we don't build
it.** ADR 0024 ships an `AcpClient` (JSON-RPC over a child CLI's stdio) and ADR
0025 ships the `delegates` plugin: one `delegate_to(target, query)` tool that
dispatches to **a2a / openai / acp** delegates declared in `langgraph-config.yaml`.
It ships **disabled, with no delegates declared** — so this is **config wiring +
composition**, not new spawn code:
- **Coders = `acp` delegates.** protoCLI is already ACP-ready — the manifest's
  canonical example is `name: proto, type: acp, command: proto, args: ["--acp"],
  workdir: …` (the `workdir` is the confinement boundary). Claude Code / Codex /
  Gemini drop in the same way (different `command`/`args`).
- **Quinn (PR review) = an `a2a` delegate.** `delegate_to("quinn", …)` over A2A
  (the same peer path the `peer_consult` skill-routing in protoAgent #695 hardened).
- **The board loop just calls `delegate_to`:** `delegate_to(<coder>, spec)` to
  build (in a per-feature worktree = the delegate `workdir`) → open PR →
  `delegate_to("quinn", …)` to review. No bespoke spawner.

The per-feature mechanics the registry/AcpClient already handle:
1. `git worktree add .worktrees/feat-<id> -b feat/<id> <base>` (isolation — non-negotiable for parallel agents; "one file, one owner").
2. Spawn the chosen coder as an ACP subprocess (cwd = worktree): `claude-code-acp`,
   `codex` (ACP), `gemini --experimental-acp`, opencode, or **protoCLI if/when it
   ships an ACP adapter**.
3. `initialize → session/new → session/prompt` with the feature spec + repo context.
4. Stream `session/update`; auto-gate `session/request_permission` by policy
   (allow edits inside the worktree, deny network/dangerous ops).
5. On stop-reason: verify a non-empty diff, commit if needed, push branch,
   `gh pr create` → set `In Review`. On failure/timeout: tear down + `Blocked`.

**Fallback (per-CLI headless)** for any coder lacking an ACP adapter:
`claude -p --output-format json` / `codex exec --json --sandbox workspace-write` /
`opencode run` — same worktree+PR envelope, less uniform plumbing.

> ⚠️ **ACP disambiguation:** this is **Zed's Agent Client Protocol** (editor↔agent,
> stdio JSON-RPC), *not* IBM/BeeAI's "Agent Communication Protocol" (that one
> merged into A2A and is winding down), and orthogonal to MCP (agent↔tools). An
> ACP agent can itself be an MCP client. Today's reliable transport is **stdio**
> (subprocess-per-agent); remote ACP transport is WIP.

### D5 — Combo board UI (DEFERRED — after the API works)
**Not in the first cut.** Per the API-first constraint, no board UI until the data
model + API + orchestration loop run headlessly. When we do build it: list view
(dense, keyboard-first) **and** Kanban view (columns = states = live ops view) are
**two projections of the same issues**, toggled — never two data models — over the
already-proven API. The Fleet dashboard in the protomaker plugin
(`/plugins/protomaker/dashboard`, ADR 0026) is a seed to revisit *then*. Until
then the board is API-only; humans/Roxy drive it over HTTP/A2A.

### D6 — Drop the protoMaker cruft (the "Jira tax")
Do **not** forward-port: custom workflow schemes / configurable transition graphs,
sub-statuses, multi-level sub-tasks, a custom-field engine, elaborate dependency
DAGs/schedulers, velocity/sprint/cycle/roadmap analytics, per-transition rule
engines, and the homegrown agent-execution runtime (replaced by ACP coders).
Keep: the 6-state pipeline, the Ready gate, `pr_url` linkage + merge→Done webhook,
worktree-per-card isolation, one optional parent link, agent-as-assignee with
human ownership retained, and priority ordering for the puller. This aligns with
the standing roxy direction ("less LLM agency, deterministic tools/loops").

---

## Consequences

**Gained**
- The whole capability lives in protoAgent (plugin reach), so Roxy keeps trending
  toward "stock protoAgent + plugins, no fork."
- Heterogeneous, best-of-breed coders (Claude Code / Codex / protoCLI) behind one
  ACP client — swap or A/B coders per feature without engine changes.
- A board model small enough to reason about, with the drift/limbo failure classes
  designed out (single Done edge, fixed pipeline).

**Cost / risk**
- **ACP maturity:** "early but usable"; stdio-only transport; pin per-CLI adapter
  versions (`claude-code-acp`, Codex ACP) and read the live JSON schemas before
  building. Keep the headless fallback (D4) for resilience.
- **Token cost:** orchestrator-worker runs hot (Anthropic measured ~15× vs single
  agent). Budget + concurrency caps required.
- **Integration is the real bottleneck:** parallel worktrees move the cost to
  merge/verify. The verification gate (Quinn review + CI + merge-on-green) is
  load-bearing, not optional.
- **Migration:** existing protoMaker boards/features need an export → lean-board
  import; auto-mode cutover is staged (run the ACP loop on one project first).
- **The spawn/review primitive is already built** (ADR 0024/0025 `delegate_to`) and
  protoCLI is ACP-ready (`proto --acp`) — the only gap is **config wiring** (the
  `delegates` plugin ships disabled with no delegates declared). De-risk is mostly
  done; the remaining build is the board + the loop, not the coder mechanism.

**Validation already in hand**
- Roxy's per-project scope middleware + A2A skills + the Fleet dashboard (the
  protomaker plugin) prove the plugin surface. The portfolio-check + unblock runs
  this session prove Roxy can drive a board read/write loop end-to-end.

---

## Alternatives considered
- **Keep protoMaker, just trim it.** Rejected: the engine's execution runtime +
  workflow machinery are the bulk of the cost and the bug surface; ACP coders +
  a 6-state board replace them outright.
- **Decentralized agent handoffs (Swarm-style).** Rejected for coding: loses the
  global view needed to enforce file ownership and sequence dependencies.
- **Build our own coding-execution runtime (status quo).** Rejected: re-solves
  what Claude Code / Codex / ACP already standardize; high maintenance, no
  best-of-breed swap.

---

## Out of scope / open questions
- ~~protoCLI ACP support~~ — **resolved:** protoCLI runs as an `acp` delegate
  (`proto --acp`); the `delegates` plugin (ADR 0024/0025) is the spawn path. Only
  the config (enable + declare delegates) is outstanding.
- **Coder selection policy**: fixed per-project, or Roxy chooses per feature
  (cost/capability)? (`delegate_to` already supports multiple named delegates.)
- **Where the board state lives**: a plugin-owned store (Postgres/SQLite) vs a
  thin mirror over GitHub Projects/Issues (Symphony uses Linear as the store).
- **Plugin boundary**: fold board-orchestration into the existing `protomaker`
  plugin, or ship a sibling `project-board` plugin that the protomaker bundle
  depends on?
- **Concurrency + budget governance**: caps, per-worktree resource limits,
  the token-cost ceiling.

### Proposed next step
The ACP-spawn primitive is already built (ADR 0024/0025) — so the next step is
**wiring + the loop**, not a spawn spike:
1. **Wire the delegates (config):** enable the `delegates` plugin in roxy and
   declare `proto` (`type: acp`, `proto --acp`) + `quinn` (`type: a2a`). Prove
   `delegate_to("proto", <spec>)` builds in a workdir and `delegate_to("quinn", …)`
   reviews — both ends, end-to-end, on one project.
2. **Build the lean board + the orchestration loop — API-only** (D3): the 6-state
   store + an HTTP/A2A API + the Ready→`delegate_to(coder)`→PR→`delegate_to(quinn)`
   →merge→Done loop. Drive + verify it entirely via API calls. **No UI** (D5 is
   deferred).
Settle the one shaping decision first (open questions): **board store** —
plugin-owned DB vs a thin mirror over GitHub Projects (Symphony uses Linear).

---

## References
**Orchestration frameworks**
- OpenAI Agents SDK — multi-agent patterns: https://openai.github.io/openai-agents-python/multi_agent/
- OpenAI Swarm (experimental predecessor): https://github.com/openai/swarm
- OpenAI AgentKit (DevDay 2025): https://openai.com/index/introducing-agentkit/
- Anthropic — multi-agent research system (orchestrator-worker): https://www.anthropic.com/engineering/multi-agent-research-system
- Addy Osmani — The Code Agent Orchestra: https://addyosmani.com/blog/code-agent-orchestra/
- ComposioHQ/agent-orchestrator (reference impl): https://github.com/ComposioHQ/agent-orchestrator

**ACP + driving coding CLIs**
- Agent Client Protocol — intro + overview: https://agentclientprotocol.com/get-started/introduction · https://agentclientprotocol.com/protocol/overview
- Zed — ACP + registry: https://zed.dev/acp · https://zed.dev/blog/acp-registry
- Claude Code via ACP adapter: https://www.npmjs.com/package/@zed-industries/claude-code-acp
- Claude Code headless + Agent SDK: https://code.claude.com/docs/en/headless · https://code.claude.com/docs/en/agent-sdk/overview
- Codex non-interactive (`exec --json`): https://developers.openai.com/codex/noninteractive
- Gemini CLI ACP mode: https://geminicli.com/docs/cli/acp-mode/
- ACP-vs-ACP naming collision (IBM ACP → A2A): https://lfaidata.foundation/communityblog/2025/08/29/acp-joins-forces-with-a2a-under-the-linux-foundations-lf-ai-data/

**Board-driven orchestration + lean board UX**
- OpenAI Symphony — Linear as agent control plane (secondary write-up): https://www.mindstudio.ai/blog/openai-symphony-spec-linear-agent-control-plane-500-percent-pr-increase
- Linear — AI agents / agent sessions: https://linear.app/docs/agents-in-linear · https://linear.app/developers/agents
- GitHub Copilot coding agent (issue→draft PR): https://docs.github.com/copilot/concepts/agents/coding-agent/about-coding-agent
- Vibe Kanban: https://vibekanban.com/ · Cline Kanban: https://cline.bot/blog/announcing-kanban · Agent Kanban: https://agent-kanban.dev/
- Linear-vs-Jira design philosophy (constraints-as-features): https://everhour.com/blog/linear-vs-jira/
- Git worktrees for parallel agents: https://www.augmentcode.com/guides/git-worktrees-parallel-ai-agent-execution

**Unverified / flagged**
- A blogged "April 2026 Agents SDK subagent primitive / native sandbox" — **not
  confirmed against an official OpenAI source**; do not architect against it.
- Exact OpenAI Symphony mechanics are from a secondary source (pattern solid,
  precise cron/comment details illustrative).
