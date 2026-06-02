# Roxy — codebase learning + cross-project context (plan)

**Owner:** Josh · **Drafted:** 2026-06-02 · **Status:** approved direction, not yet built

How Roxy goes from "board PM that reads state" to "operator that *understands* the codebases
she manages and *remembers* across them." Grounded in: Quinn's clawpatch deep-dive (`~/dev/protoPatch`),
protoAgent's learning flywheel (which Roxy inherits), and the beads task ledger.

## Where Roxy is now
Deployed PM for the protoMaker board; manages **2 projects** (protoApp, protoWorkstacean), read-only
on code, board-writes via the `automaker` MCP. She already inherits the protoAgent **learning
flywheel** — knowledge store (FTS5), KnowledgeMiddleware + auto-ingest, researcher subagent, and the
skill loop (emit → index → retrieve → curate). Gaps: she doesn't *learn the codebase*, and she has no
*cross-project memory* between sweeps.

## Decisions
- **D1 — Codebase learning = clawpatch `map` → ingest into her knowledge store.** Use Quinn's proven,
  **deterministic, LLM-free** structural mapper (`clawpatch map --json`: feature slices, entrypoints,
  owned/context files, tests, trust boundaries). Ingest the map as `domain=codebase:<project>` chunks.
  **Not** clawpatch `review`/`fix` — code review stays Quinn's; Roxy only needs structure.
- **D2 — Beads cross-project ledger = Roxy keeps her own.** The projects she manages mostly track work
  in `.automaker/features` and have **empty beads boards**, so beads-as-project-truth isn't reliable.
  Instead Roxy maintains **her own beads ledger** (in a writable path — the projects are mounted
  read-only) recording per-project state (flowing/stalled/blocked, open threads, pending decisions) as
  her **durable cross-project memory**. No project changes required.

---

## Thread 1 — Codebase learning (clawpatch map → knowledge)

**Mechanism.** `clawpatch map` slices a repo into semantic features deterministically (no LLM, no
embeddings); output is JSON FeatureRecords (`.clawpatch/features/*.json`). Cheap + fast + language-auto.

**Build:**
1. **Make `clawpatch map` available to Roxy** — vendor/install the clawpatch CLI into her image (same
   pattern as the vendored `automaker` MCP bundle), or call a Workstacean map endpoint if exposed.
2. **`learn_codebase` capability** — for a project: run `clawpatch map --root <path> --json` → distil
   each feature (title, kind, entrypoints, trust boundaries, tests) → `knowledge.add_chunk(domain="codebase:<project>", source="clawpatch:<featureId>")`.
3. **Triggers** — on project onboard, before a decomposition (so the PRD/milestones are grounded in
   real structure), and on a periodic refresh.
4. **Retrieval** — KnowledgeMiddleware surfaces the matching `codebase:<project>` chunks per turn; the
   skill loop captures good "learn/decompose" runs so she gets sharper per repo over time.

**Boundary.** Structural understanding only. Findings/bugs/review = Quinn (clawpatch review). Roxy is
read-only on code; this just makes her *informed*.

**Risks.** Knowledge-store bloat (ingest feature *summaries*, not file contents); keep maps fresh
(re-map on significant change). clawpatch availability in-container is the main wire-up unknown.

---

## Thread 2 — Beads cross-project ledger (her durable memory)

**Mechanism.** `br` (beads 0.1.23) is already in Roxy's container. The projects are mounted **read-only**
so she cannot write their `.beads`; her ledger lives in a **writable path** (e.g. `/sandbox/roxy-ledger/.beads`).

**Build:**
1. **Init her ledger** — a beads workspace in `/sandbox` (persisted via the `roxy-data` volume).
2. **Sweep contract (project-operations skill)** — every sweep: read each project's live state
   (`automaker` features + that project's beads where populated) → upsert a ledger entry per project /
   per open thread (status, reason, next action, last-seen). Beads' own status/priority/deps model fits.
3. **Inject** — a compact "cross-project ledger" digest (per-project flowing/stalled/blocked + open
   threads) injected into her prompt each run via the knowledge middleware (hot-memory / `domain=hot`),
   so a sweep of project A informs B and she has continuity across runs.
4. **Read project beads too** — where a project *does* use beads (e.g. protoMaker, 52 issues), read it
   (`br list --json`) as first-class task state alongside the automaker features.

**Why her own ledger:** it's reliable today (no dependency on projects adopting beads), it's the natural
home for *cross-project* state (which no single project's board holds), and it's durable.

---

## Thread 3 — Roadmap (the system's future)

1. **Now** — multi-project board PM; manual A2A + the portfolio-sitrep ceremony (weekday 1pm).
2. **Next** — Thread 1 (codebase learning) + Thread 2 (beads ledger + injected cross-project context).
3. **Then** — **Ava (Workstacean) orchestrates Roxy event-driven** over the bus (the deferred
   "as trust improves" milestone): CI-fail / stalled-PR / new-project events → dispatch Roxy.
4. **Throughline** — the flywheel: every sweep/decompose/unblock makes her better at each codebase;
   the skill curator keeps the learned patterns sharp.

## Implementation order
1. Beads ledger (Thread 2) — fastest value, no new deps (br is present); proves cross-project continuity.
2. clawpatch-map ingest (Thread 1) — needs the CLI in her image; grounds her decomposition in real structure.
3. Event-driven Ava→Roxy (Thread 3) — once 1–2 are solid and trust is established.

## Out of scope
- clawpatch `review`/`fix` (Quinn owns code review). Roxy writing to projects' code or beads (read-only).
- Embeddings/vector KG (clawpatch is deterministic; the FTS5 store is enough to start — HybridKnowledgeStore later if needed).
