# Roxy handoff — 2026-06-08

**For:** whoever pulls Roxy down to the local dev machine.
**Theme of this session:** strategic pivot — retire protoMaker's heavy engine in favor of a lean **protoAgent board-orchestration plugin** where Roxy spawns coding CLIs over ACP and delegates review to Quinn over A2A. Plus the upstream fixes that unblock it.

---

## ▶ START HERE — the teed-up task

**Pull Roxy local, prove the full delegate spine, then build the lean board + loop (API-first).**

1. **Pull roxy to the dev machine** (it currently runs on ava as a container; locally `proto` exists + repos are local).
2. **Provision `proto` on PATH** — the only gap for the coder half. The `delegates` plugin spawns `proto --acp` as a subprocess in a repo workdir; in the ava container `proto` isn't installed (the workdirs *are* mounted). Locally that's solved.
3. **Prove the full spine end-to-end:**
   - `delegate_to("quinn", …)` (a2a reviewer) — **already working** (A2A 1.0 fix landed).
   - `delegate_to("proto", …)` (acp coder) — should work once `proto` is on PATH: a feature spec → diff/PR in a worktree.
4. **Build the lean board + orchestration loop — API-only, no UI** (per Josh: API-first; defer all front-end). The 6-state board (Backlog→Ready→In Progress→In Review→Done + Blocked) + the `Ready → delegate_to(proto) → PR → delegate_to(quinn) → merge→Done` loop, driven/verified over HTTP/A2A.

Design + rationale: **`docs/dev/roxy/direction-board-orchestration-plugin.md`** (read this first — it's the spec).

---

## Overview

Roxy is converging on "**stock protoAgent + plugins, no fork**." This session shipped the protoMaker capability as a plugin, eliminated the remaining generic fork-deltas upstream, and laid down the next architecture: a board-driven coding-orchestration plugin (validated by OpenAI's "Symphony" pattern) using the **already-existing** `delegate_to` machinery (ADR 0024 ACP + ADR 0025 registry).

---

## Current State

**Completed + merged/live:**
- [x] **protoMaker plugin** — full-bundle agent-as-plugin (12 fleet tools + 7 A2A skills + per-project resolver + scope middleware + Fleet dashboard). roxy **#70 merged**, running live on ava. `plugins/protomaker/`.
- [x] **A2A 1.0 client fix** — `peer_tools` + the `delegates` adapter were on the **v0.3 legacy `message/send`**; the fleet (a2a-sdk≥1.1) is **A2A 1.0 `SendMessage`** → every a2a `peer_consult`/`delegate_to` was `-32601`. Fixed both → protoAgent **#705 merged**. `delegate_to('quinn')` verified live.
- [x] **Upstream delta-elimination** (Roxy → stock): **PA #692** (CI-triage tools → the `github` plugin), **#695** (peer_consult skill routing + timeout), **#696** (protolabs gateway pricing + python-multipart). All merged → those files go zero-delta on next sync.
- [x] **Gateway fix** — `model=reasoning` reviews were 400'ing (bare alias not on the gateway). Added bare `reasoning`/`smart`/`fast` `model_group_alias`es → protoMaker reviews unblocked. Committed `homelab-iac` `94ed47e`; live.
- [x] **protoMaker unblocked + flowing** — reconciled drift, reset the model-blocked features, auto-mode **started** (3 features back in the review pipeline). One PR (#4130) needs a human rebase.
- [x] **Direction doc + memory** for the board-orchestration plan.

**Wired but not fully proven (the spine):**
- [x] `delegates` plugin enabled in roxy config; `quinn` (a2a) + `proto` (acp) declared. `delegate_to` live (2 delegates).
- [x] **Reviewer half (`quinn`, a2a):** working end-to-end.
- [ ] **Coder half (`proto`, acp):** blocked only on `proto` **binary not on PATH** in the ava container → **resolved by running locally**.

**Not started:**
- [ ] The lean board (data model + API) + the orchestration loop.
- [ ] Quinn-skill routing via delegate: `delegate_to` passes no `skillHint`, so it hits workstacean's *default* executor, not Quinn's `pr_review` skill. Use `peer_consult(skill=pr_review)` for skill-specific review, **or** add a `skill` field to the a2a delegate (small enhancement).

---

## Technical Approach (continue this)

- **Orchestrator-worker + isolated workers + shared-state board + verification gates** — the documented winning pattern for coding (Anthropic / OpenAI Agents SDK / Symphony). Roxy = supervisor; coders = ephemeral ACP CLIs; Quinn = a2a reviewer.
- **Reuse, don't rebuild:** the spawn primitive is `delegate_to` (ADR 0024/0025) — `acp` for coders, `a2a` for review. The board loop just calls it; no bespoke runtime.
- **Lean board:** 6 states, hard-coded ~10-field card schema, **Ready-gate** (spec + acceptance criteria) is the highest-ROI piece, **merge-webhook → Done** is the *only* external transition (designs out the historic 82-phantom drift).
- **Drop the protoMaker "Jira tax":** custom workflows, sub-statuses, custom fields, dependency DAGs, sprint analytics, the homegrown execution runtime.
- **API-first.** No UI until the board + loop work headlessly.

---

## Key Files and Documentation

| File | Purpose |
|------|---------|
| `docs/dev/roxy/direction-board-orchestration-plugin.md` | **The spec** — full design, keep/drop, ACP/delegate mechanics, references (incl. OpenAI Symphony). |
| `plugins/protomaker/` | The shipped agent-as-plugin (tools, skills, scope middleware, dashboard). |
| `plugins/delegates/` (upstream) | `delegate_to` over a2a/openai/acp (ADR 0025). `adapters.py` now A2A-1.0. |
| `tools/peer_tools.py` (upstream) | `peer_consult` — A2A-1.0 fixed; carries the `skill=`/skillHint route. |
| `homelab-iac/stacks/roxy/config/langgraph-config.yaml` | roxy deploy config — `plugins.enabled: [protomaker, delegates]` + the `delegates:` list (gitignored; host-only). |
| `homelab-iac/stacks/ai/config/litellm/config.yaml` | gateway — bare `reasoning/smart/fast` aliases (`94ed47e`). |
| `docs/adr/0024-spawn-cli-coding-agents-acp.md` / `0025-…` | the ACP + delegate registry design. |

**Memory** (auto-loads): `roxy-board-orchestration-direction`, `roxy-a2a-1.0-protocol`, `roxy-protomaker-plugin`(implied), `roxy-upstream-sync`.

---

## Acceptance Criteria (spine + first board cut)

- [ ] `proto` on PATH locally → `delegate_to("proto", <feature spec>)` produces a diff/PR in a git worktree.
- [ ] `delegate_to("quinn", <PR>)` returns a real review (a2a) — and decide skill-routing (peer_consult skill= vs a delegate `skill` field).
- [ ] Lean board store + API: create project → `Ready` feature (with spec + acceptance criteria) → loop drives it to `In Review` → `Done` on merge-webhook. **All via API; no UI.**
- [ ] Board state never drifts: only the merge-webhook sets `Done`.

---

## Open Questions / Watch-outs

- **Board store:** plugin-owned DB (SQLite/Postgres) vs a thin mirror over GitHub Projects (Symphony mirrors Linear). *(Decide before building the store.)*
- **Coder selection:** fixed per-project vs Roxy chooses per feature (`delegate_to` already supports multiple named delegates).
- **Plugin boundary:** fold board-orchestration into the `protomaker` plugin vs a sibling `project-board` plugin.
- **Concurrency + token budget:** orchestrator-worker runs hot (~15× tokens); cap concurrency + worktree resources.
- **Quinn skill routing** (above) — the delegate hits workstacean's default executor without a skillHint.
- **`gh_*` tools** now live in the upstream `github` plugin (PA #692); when it syncs, enable `github` + drop the `gh_*` from the protomaker bundle (find-replace `gh_ci_runs`→`github_ci_runs`, etc.).

---

## System State / how to pick up

- **Upstream is now v0.26.0** (#704) with notable console work: Plugins view as **tabs** (Installed/Marketplace/Install), MCP servers from the console, one-click plugin enable/disable, decentralized settings. **A sync is pending** — it brings #705 (A2A 1.0) + #692/#695/#696 (so the 3 fork-delta files + the gh_* tools land) + the console plugin UX. Sync is owned by the automated `chore/sync-*` process (don't race it; see `roxy-upstream-sync`).
- **roxy on ava:** running the protomaker + delegates plugins; auto-mode flowing on protoMaker. roxy's running container has the A2A-1.0 `adapters.py` `cp`'d in (works); it reverts to the merged upstream version on sync.
- **Deploy:** roxy = locally-built base image (CI can't publish it); `/config` is bind-mounted **read-only** (so runtime `plugin install` fails — plugins are baked or config-enabled). See `roxy-homelab-deploy`.
- **Pulling local:** `proto` + repos are present locally, so the coder half should "just work"; container-provisioning `proto` on ava is the explicit "later" item.
