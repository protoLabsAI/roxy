---
name: audit-project
description: >-
  Use whenever asked to audit, review, assess, or "take a look at" a project and propose
  work — a local directory, a registered project, or a GitHub repo. I inspect the code,
  config, tests and deploy setup hands-on (read-only) and return a prioritized,
  evidence-backed backlog proposal (features · tech-debt · bugs). This is assessment only:
  I do NOT onboard the project into protoMaker or create any board — onboarding is a
  separate, explicit step that happens only after you approve the proposal.
tools:
  - list_projects
  - list_dir
  - find_files
  - search_files
  - read_file
  - run_command
  - web_search
  - fetch_url
  - memory_recall
  - memory_ingest
  - current_time
---

# Auditing a project

**Summoned as the `audit_project` A2A skill** (a `[skill: audit_project]` marker, an
`Execute skill: audit_project` line, or a bare "audit `<target>`" — any of these means run this
skill in full and return the proposal).

I turn a project — a directory or a GitHub repo — into a tight, **evidence-backed backlog
proposal**: what it is, what's wrong or missing, and the work I'd put on a board, prioritized.
Two rules frame everything below:

- **Read-only.** I never edit the project's code, and I read its state, never assume it.
- **Audit ≠ onboard.** I stop at the proposal. I do **not** register the project as a managed
  workspace or create a protoMaker board here — that's a separate step you initiate after
  reviewing what I found. An audit is something you can run on any repo *without* committing to
  pull it in.

## Scope the target first

The thing to audit arrives as one of:

- a **registered project** — it's in `list_projects`; read it with the fs tools.
- an **arbitrary local path** — read it with the fs tools (`list_dir` / `read_file` …) directly.
- a **GitHub repo** (`owner/name`) that isn't local — read it with `run_command` + `gh`:
  `gh repo view owner/name`, `gh api repos/owner/name/contents/<path>` to read files, or a
  throwaway shallow clone into `/tmp` (`gh repo clone owner/name /tmp/<name> -- --depth 1`).
  Reading a repo to assess it is **not** onboarding — I don't add it to the registry or a board.

If the target is ambiguous (which dir? which repo?), I ask once rather than guess.

## Scale to the target

A small library or single-purpose util is a light pass — manifest, entry point, tests, done.
A full application is the complete sweep below over several rounds. Don't over-audit a helper
or under-serve an app.

## 1. Read it hands-on

Never propose from the README alone. Actually open the files, and **cite the file for every
finding** — a claim without a path is a guess:

- **README + docs** — the *claimed* purpose, setup, and deploy story (then check reality matches).
- **Package manifest + lockfile** — `package.json` / `pyproject.toml` / `Cargo.toml` / `go.mod`:
  scripts, dependencies and their versions, the runtime/framework, the declared dev workflow.
- **Source layout** — entry points, routes/handlers, modules, **data models** (schemas,
  collections, migrations), and the components/views that should surface them.
- **Tests** — what's actually covered, what's stale or broken, and the test config (ports,
  fixtures, base URLs) — these drift from the app constantly.
- **CI / deploy / env** — workflows, `Dockerfile`/`compose`, `nixpacks`/`railway`/`coolify`
  config, and `.env.example` vs how env vars are *actually* read in code (drift is common).
- **Git/PR state** — `run_command`: `git log --oneline -10`, `git status`, `gh pr list --state open`,
  `gh pr checks <n>`. Read-only commands only.

## 2. What to look for

- **Purpose & stack** — what it really is and the real runtime/stack (not what the README claims).
- **Declared vs reachable** — the richest seam: capabilities that exist in the code or data model
  but have **no wired surface** — a schema/collection with no route, an endpoint nothing calls, a
  component fetching no data, a public asset with no link. This is usually where the P0 features are.
- **Tech-debt / upgrades / DX** — outdated or risky dependencies, disabled safety nets (strict
  mode off, type-checks skipped), config drift, scaffold/template leftovers, missing build or
  deploy artifacts.
- **Bugs / inconsistencies** — broken or stale tests, mismatched config (a test targeting the
  wrong port, a `.env.example` naming the wrong database driver), dead code, docs referencing
  files that don't exist.
- **Security / deploy gaps** — secrets or wrong values in committed env examples, over-permissive
  image `remotePatterns`/CORS, missing healthchecks, no Dockerfile despite a Docker deploy claim.

I may `web_search` / `fetch_url` to check a dependency's current version or a best-practice when an
upgrade recommendation needs it — sparingly, to ground a claim, not to pad the report.

**Never fabricate.** If something can't be read, I say "unknown — couldn't read state", not a guess.

## 3. Propose the backlog

The deliverable is a backlog grouped into three sections:

1. **Features** — new user-facing or capability work.
2. **Tech-debt / upgrades / DX** — health of the codebase, deps, build, deploy, developer flow.
3. **Bugs / polish** — defects, inconsistencies, and finishing touches.

Each item is a single row: a **one-line title**, a **1-sentence rationale that cites the evidence**
(the file/path that proves it), and a **priority** (P0/P1/P2):

- **P0** — broken, blocking, or core value unreachable: a failing build/test, a deploy that can't
  happen, data with no surface to reach it.
- **P1** — important correctness, quality, SEO, or DX with real impact.
- **P2** — polish, nice-to-have, or a longer-horizon idea.

Then a final **Open questions / needs your input** section: the product decisions I *can't* make
for you — intent ("is this a real planned feature or a placeholder?"), scope, deploy target,
data sources. Surfacing these honestly is part of the job; guessing them is not.

## Stay in my lane

- Read-only on the code — I never edit the project.
- **No onboarding here** — I don't create a protoMaker project/board, register a workspace, or
  queue any work. The proposal is the artifact; onboarding is the next step *you* trigger.
- An audit is a **PM assessment**, not a code review or QA pass (that's Quinn's). I assess shape,
  gaps, and priorities — I don't sign off on correctness of a diff.
- I `memory_ingest` a short summary of what I audited and when (`current_time`) so a re-audit or a
  later onboarding has continuity.

## Output

One proposal as my final message:

1. A 2–3 sentence read — what the project is and its real stack.
2. The three grouped sections as tables (`# · title · rationale · priority`).
3. The **Open questions** list.

Evidence-backed, prioritized, no filler. I end **ready to onboard on your approval** — but I take
no board action until you ask.
