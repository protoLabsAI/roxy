# Roxy handoff — 2026-06-05

**Owner-facing snapshot for the next team.** Roxy is healthy and live on `ava`; auto-mode is
**paused** (intentional — see Blockers). This session shipped the deterministic fleet toolkit +
domain separation (Phase 1 + Phase 2 working-memory). The **one teed-up task is the upstream
ADR-0021 memory sync** — fully assessed, low-risk, not yet executed.

---

## ▶ START HERE — the teed-up task: upstream memory sync (ADR 0021)

Upstream protoAgent merged a full memory redesign (#535–#540). Roxy forks protoAgent and should
sync it in — it brings semantic fact extraction + a hybrid embedding store, and critically a
**namespace dimension (#538)** that makes Roxy's per-project durable memory a trivial follow-on.

**Assessment already done (trial-merged + aborted, tree clean):**
- roxy is **34 behind upstream** (`upstream/main` tip `3861589`; memory set is `8f86c67..d2b73f6`).
- **`server.py` AUTO-MERGES** — the per-project `thread_key` work (this session) composes cleanly
  with the upstream memory rewrite. This was the risk; it's clear.
- **Conflicts are only the 4 routine fork ones:** `.github/workflows/prepare-release.yml` +
  `release.yml` (keep roxy's `github.repository == 'protoLabsAI/roxy'` guard + roxy IMAGE_NAME),
  `CHANGELOG.md` (combine roxy + upstream entries), `pyproject.toml` (combine — roxy name/version +
  upstream deps). Same pattern as the last sync (PR #33).
- **No heavy new deps** — Phase 1.5 *wired dormant* embeddings (already in the tree), not a
  torch/transformers adoption.

**Execution steps:**
1. `git switch -c chore/sync-upstream-adr0021 && git merge upstream/main` (a **MERGE COMMIT** —
   never squash; squash re-breaks the fork base).
2. Resolve the 4 conflicts (keep roxy's CI guards + image name; combine CHANGELOG + pyproject).
3. Build + deploy on ava: `docker build --build-arg UI=none -t ghcr.io/protolabsai/roxy:latest .`
   then from `homelab-iac/stacks/roxy`: `infisical run … -- docker compose up -d --build
   --force-recreate --renew-anon-volumes` (the `--renew-anon-volumes` is required — bundled config
   is masked by a stale anon volume otherwise).
4. **Verify both memory paths compose:**
   - per-project working memory still works (the BANANA test below),
   - the new subsystem runs in Roxy: fact extraction on thread retirement, hybrid recall, cleaned
     `<prior_sessions>`.
5. PR it; **merge with `gh pr merge --merge` (NOT `--squash`)**.

**After the sync — the reframed durable piece (small):** per-project durable memory is no longer a
subsystem. The upstream fact extractor already carries a namespace dimension (#538); wire the active
project (derivable from the per-project `thread_id = a2a:proj:<slug>`) into the fact's namespace and
filter hybrid recall by it. That's the whole "durable per-project memory" item now.

---

## What shipped this session (all merged + live)

Direction (Josh): **less LLM agency — bespoke deterministic tools/parsers, LangGraph-parallel, not
prompting.** Driven by a fleet-management eval that found her judgment strong but mechanical
orchestration unreliable (wrong-project answers, capped reads, shelling out).

| PR | What | Notes |
|----|------|-------|
| #42 | `fleet_registry` | fleet = protoMaker registry (`GET /api/settings/global`), same source as protoWorkstacean |
| #43 | `fleet_sitrep` | exact parallel fleet health, computed status/attention |
| #44 | `repo_origin_state` + `fleet_reconcile` | reconcile rule as code, non-shell origin truth via GitHub-App `/api/github/issues\|prs` |
| #45 | **project-scope primitive** | domain separation Phase 1 — request `projectPath` metadata → scope banner; no cross-project bleed |
| #46 | `fleet_readiness` | auto-mode pre-flight: isolation + clean base + ready backlog + not blocked-heavy |
| #47 / #48 | **per-project session memory** | Phase 2 core — `project_session.py` + `thread_key`; checkpoint thread keyed to `proj:<slug>`, decoupled from A2A `context_id`. Validated. (#47 merged red; #48 fixed mock signatures — watch required-checks.) |

All tools live in `plugins/fleet-onboarding/__init__.py` (7 tools). Roxy reaches all 8 fleet boards
via the automaker MCP. Domain separation validated: scoped protoMaker → 58 blocked (was wrong-project
"1"); scoped protoApp → 26 (was "empty"); BANANA codeword persisted per-project + isolated across
projects.

---

## Blockers + follow-ups

- 🚨 **protoMaker #4086 (external) — blocks autonomous delivery FLEET-WIDE.** Worktree creation
  refuses a dirty base; `fleet_readiness` proved **7/8 projects have a dirty `.automaker/` runtime
  base** → none can deliver. Recommended fix (commented on #4086): the worktree cleanliness check
  should exclude protoMaker's own `.automaker/` runtime. **Auto-mode stays paused until this ships**
  — running is pointless (every project holds). `fleet_readiness` correctly gates it.
- **protoMaker #4074 (external)** — per-project delivery-vs-observe stance (their backlog).
- **protoMaker #4073 — CLOSED** (read-only-trap delivery fix landed via their #4075).
- **release-tools board residue** — carries a blocked zizmor feature + features lacking
  `githubIssueNumber` (so `fleet_reconcile` can't link them). Minor; reconcile when convenient.

---

## Domain-separation architecture (context)

Phase 1 (scope) + Phase 2 (session memory) make ONE Roxy behave as **N project-scoped operators** by
keying state to the **project, not the process** — no N containers, no lifecycle system. Ava's
routing contract is trivial: send `projectPath` in request metadata; the per-project thread
auto-derives. **N containers are deferred** as a per-project "promote for hard fault/resource
isolation" escape hatch — the routing contract is identical either way.

## System state / how to pick up

- Roxy: live on ava, healthy (`docker exec roxy curl -fsS localhost:7870/healthz`). Image is a
  derived `protolabs/roxy-homelab` on a locally-built base (CI can't publish the roxy image).
- Auto-mode ceremony: `protoWorkstacean/workspace/ceremonies/roxy.board-pulse.yaml` → `enabled:false`.
- A2A test harness pattern (from this session): `docker exec -i roxy python3` → POST `/a2a`
  `SendMessage` with `metadata: {projectPath, project}` for scoped turns. BANANA test:
  set a codeword scoped to one project, recall it from a *different* A2A context (same project) →
  should remember; recall from another project → should not.
- Memory (persistent, `START HERE`): `roxy-handoff-state`, `roxy-domain-separation`,
  `roxy-evals-and-tooling-direction`, `roxy-fleet-registry`, `roxy-upstream-sync`.
