"""Fleet-onboarding plugin — Roxy's non-shell fleet tools.

The `onboard-project` skill needs privileged actions that, done via `run_command`
(curl/git), trip the HITL shell-approval gate (`filesystem.run_requires_approval`)
and stop onboarding from running unsupervised. These dedicated tools do the same
work without a shell — file reads and HTTP calls — so they are NOT gated.

- ``repo_github_remote`` — read a repo's GitHub remote from `.git/config`.
- ``fleet_register``     — register a project with the Workstacean fleet.
- ``fleet_registry``     — read the protoMaker project registry (the shared
  source of truth for the fleet, same one protoWorkstacean / pr-pipeline /
  ci-health use), so Roxy's landscape view stays in lockstep with the fleet.

Enable with ``plugins: { enabled: [fleet-onboarding] }``.
"""

from __future__ import annotations

import json
import os
import re

import project_session
from pathlib import Path

from langchain_core.tools import tool


def _parse_origin_url(git_config_text: str) -> str | None:
    """Return the `url` of the `[remote "origin"]` section, or the first remote
    url if origin is absent. `.git/config` is INI-ish with quoted subsections."""
    section = None
    origin_url = None
    first_url = None
    for raw in git_config_text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        m = re.match(r"url\s*=\s*(\S+)", line, re.IGNORECASE)
        if m:
            url = m.group(1)
            if first_url is None:
                first_url = url
            if section and "remote" in section and '"origin"' in section:
                origin_url = url
    return origin_url or first_url


def _to_owner_repo(url: str) -> str | None:
    """Normalize a GitHub remote URL to `owner/name` (drops host + `.git`).
    Handles `https://github.com/o/n(.git)` and `git@github.com:o/n(.git)`."""
    if not url:
        return None
    u = url.strip()
    u = re.sub(r"\.git$", "", u)
    # ssh: git@github.com:owner/name
    m = re.match(r"git@[^:]+:(?P<path>.+)$", u)
    if m:
        path = m.group("path")
    else:
        # https://github.com/owner/name  (or http, or with creds)
        m = re.match(r"https?://[^/]+/(?P<path>.+)$", u)
        path = m.group("path") if m else None
    if not path:
        return None
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        return None
    return f"{parts[0]}/{parts[1]}"


@tool
def repo_github_remote(project_path: str) -> str:
    """Read a local repo's GitHub remote from its `.git/config` — no shell, read-only.

    Returns a JSON object: {"owner_repo": "owner/name", "slug": "owner-name",
    "url": "<raw remote url>"} — or {"error": "..."} if there's no git remote
    (e.g. a local-only dir). Use this to get the `github` value for fleet
    registration instead of shelling out to `git remote`.
    """
    base = Path(project_path).expanduser()
    cfg = base / ".git" / "config"
    if not cfg.is_file():
        return json.dumps({"error": f"no .git/config at {project_path} (not a git repo, or local-only)"})
    try:
        text = cfg.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return json.dumps({"error": f"could not read {cfg}: {e}"})
    url = _parse_origin_url(text)
    if not url:
        return json.dumps({"error": "git repo has no remote url (local-only) — fleet registration needs a GitHub repo"})
    owner_repo = _to_owner_repo(url)
    if not owner_repo:
        return json.dumps({"error": f"remote is not a recognizable GitHub url: {url}"})
    return json.dumps({"owner_repo": owner_repo, "slug": owner_repo.replace("/", "-").lower(), "url": url})


@tool
async def fleet_register(slug: str, title: str, github: str) -> str:
    """Register a project with the Workstacean fleet — `POST /api/onboard`, no shell.

    Registers the Quinn PR-review webhook + the routing-index entry (idempotent).
    `slug` is the project slug, `title` the display name, `github` the
    `owner/name`. Reads `WORKSTACEAN_URL` + `WORKSTACEAN_API_KEY` from the env.
    Returns a JSON status. This is the unsupervised replacement for the
    `curl /api/onboard` shell step — it is not HITL-gated.
    """
    import httpx

    base = (os.environ.get("WORKSTACEAN_URL") or "").rstrip("/")
    key = os.environ.get("WORKSTACEAN_API_KEY") or ""
    if not base:
        return json.dumps({"error": "WORKSTACEAN_URL not set"})
    if not key:
        return json.dumps({"error": "WORKSTACEAN_API_KEY not set — cannot authenticate /api/onboard"})
    if not github or "/" not in github:
        return json.dumps({"error": f"github must be 'owner/name', got {github!r}"})
    payload = {"slug": slug, "title": title, "github": github}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{base}/api/onboard",
                headers={"X-API-Key": key, "Content-Type": "application/json"},
                json=payload,
            )
        ok = 200 <= resp.status_code < 300
        body = resp.text[:500]
        return json.dumps({"ok": ok, "status": resp.status_code, "registered": payload, "response": body})
    except Exception as e:  # noqa: BLE001 — surface the failure to the agent, don't crash the turn
        return json.dumps({"error": f"POST {base}/api/onboard failed: {e}"})


@tool
async def fleet_registry() -> str:
    """List the fleet from the protoMaker project registry — the shared source of truth, no shell.

    Reads `GET /api/settings/global` → `settings.projects[]` from protoMaker
    (`PROTOMAKER_API_BASE` or `AUTOMAKER_API_URL`, keyed by `AUTOMAKER_API_KEY`) —
    the SAME registry protoWorkstacean, pr-pipeline, and ci-health derive their
    fleet from. Use this as the authoritative fleet/landscape, never a hardcoded
    list, so my view of which projects exist stays in lockstep with the rest of
    the fleet.

    Returns JSON: {"count": N, "projects": [{"github": "owner/name", "slug":
    "name", "path": "<local path>"}], "coords": ["owner/name", ...],
    "source": "<url>"} — or {"error": "..."}.
    """
    import httpx

    base = (os.environ.get("PROTOMAKER_API_BASE") or os.environ.get("AUTOMAKER_API_URL") or "").rstrip("/")
    key = os.environ.get("AUTOMAKER_API_KEY") or ""
    if not base:
        return json.dumps({"error": "neither PROTOMAKER_API_BASE nor AUTOMAKER_API_URL is set"})
    if not key:
        return json.dumps({"error": "AUTOMAKER_API_KEY not set — protoMaker rejects the registry read with 401"})
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{base}/api/settings/global", headers={"X-API-Key": key})
        if not (200 <= resp.status_code < 300):
            return json.dumps({"error": f"GET {base}/api/settings/global -> {resp.status_code}", "body": resp.text[:300]})
        settings = resp.json().get("settings", resp.json())
        raw = settings.get("projects") or []
    except Exception as e:  # noqa: BLE001 — surface the failure, don't crash the turn
        return json.dumps({"error": f"registry read failed: {e}"})

    projects, coords = [], []
    for p in raw:
        g = p.get("github") or {}
        owner, repo = g.get("owner"), g.get("repo")
        coord = f"{owner}/{repo}" if owner and repo else None
        projects.append({"github": coord, "slug": p.get("slug") or (repo.lower() if repo else None), "path": p.get("path")})
        if coord:
            coords.append(coord)
    return json.dumps({"count": len(projects), "projects": projects, "coords": coords, "source": f"{base}/api/settings/global"})


_SITREP_KEYMAP = {"inProgress": "in_progress"}
_STATUS_KEYS = ("total", "backlog", "in_progress", "review", "blocked", "done", "interrupted")


def _classify(entry: dict) -> str:
    """Deterministic per-project health label (no LLM judgement)."""
    if entry.get("error"):
        return "unreachable"
    total = entry.get("total") or 0
    if total == 0:
        return "empty"
    if (entry.get("blocked") or 0) and entry.get("blocked_pct", 0) >= 25:
        return "blocked-heavy"
    if (entry.get("in_progress") or 0) or (entry.get("review") or 0):
        return "active"
    if entry.get("backlog") or 0:
        return "ready"
    return "done"


@tool
async def fleet_sitrep() -> str:
    """Exact health of the ENTIRE fleet in one deterministic, parallel call — no hand-tallying.

    Reads the protoMaker registry, then fans `get_sitrep` out across every project
    CONCURRENTLY (asyncio) and returns the **full** board counts per project — never
    a capped list, never the wrong project. Each project is classified
    (`active`/`ready`/`blocked-heavy`/`empty`/`done`/`unreachable`) and flagged for
    attention in code, so fleet health doesn't depend on the model counting rows.

    Returns JSON: {"count": N, "fleet": {<rolled-up status totals>}, "attention":
    ["owner/name", ...], "projects": [{"repo","path","total","backlog",
    "in_progress","review","blocked","done","interrupted","blocked_pct","status"}]}
    sorted by blocked count desc. Use this for any fleet sweep / "how's everything".
    """
    import asyncio

    import httpx

    base = (os.environ.get("PROTOMAKER_API_BASE") or os.environ.get("AUTOMAKER_API_URL") or "").rstrip("/")
    key = os.environ.get("AUTOMAKER_API_KEY") or ""
    if not base:
        return json.dumps({"error": "neither PROTOMAKER_API_BASE nor AUTOMAKER_API_URL is set"})
    if not key:
        return json.dumps({"error": "AUTOMAKER_API_KEY not set"})
    headers = {"X-API-Key": key, "Content-Type": "application/json"}

    async def _one(client: "httpx.AsyncClient", proj: dict) -> dict:
        g = proj.get("github") or {}
        repo = f"{g.get('owner')}/{g.get('repo')}" if g.get("owner") and g.get("repo") else None
        entry = {"repo": repo, "path": proj.get("path")}
        try:
            r = await client.post(f"{base}/api/sitrep", headers=headers, json={"projectPath": proj.get("path")})
            body = r.json()
            s = body.get("board") or body.get("sitrep") or body
            for k, v in s.items():
                if isinstance(v, int):
                    entry[_SITREP_KEYMAP.get(k, k)] = v
        except Exception as e:  # noqa: BLE001
            entry["error"] = str(e)[:80]
        return entry

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            g = await client.get(f"{base}/api/settings/global", headers=headers)
            settings = g.json().get("settings", g.json())
            projs = settings.get("projects") or []
            results = await asyncio.gather(*[_one(client, p) for p in projs])
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": f"fleet sitrep failed: {e}"})

    fleet: dict[str, int] = {}
    attention: list[str] = []
    for e in results:
        total = e.get("total") or 0
        e["blocked_pct"] = round(100 * (e.get("blocked") or 0) / total) if total else 0
        e["status"] = _classify(e)
        if e["status"] in ("blocked-heavy", "unreachable") and e.get("repo"):
            attention.append(e["repo"])
        for k in _STATUS_KEYS:
            if k in e:
                fleet[k] = fleet.get(k, 0) + e[k]

    results.sort(key=lambda e: -(e.get("blocked") or 0))
    return json.dumps({"count": len(results), "fleet": fleet, "attention": attention, "projects": results})


def _api_base_key() -> tuple[str, str]:
    base = (os.environ.get("PROTOMAKER_API_BASE") or os.environ.get("AUTOMAKER_API_URL") or "").rstrip("/")
    return base, (os.environ.get("AUTOMAKER_API_KEY") or "")


async def _origin_state(client, base: str, headers: dict, path: str) -> dict:
    """Open issues + open/merged PRs for a repo, via protoMaker's GitHub App (server-side `gh`)."""
    state: dict = {"open_issue_numbers": set(), "open_issues": [], "open_prs": [], "merged_prs": []}
    ir = await client.post(f"{base}/api/github/issues", headers=headers, json={"projectPath": path})
    for it in (ir.json().get("openIssues") or []):
        n = it.get("number")
        if n is not None:
            state["open_issue_numbers"].add(n)
            state["open_issues"].append({"number": n, "title": it.get("title")})
    pr = await client.post(f"{base}/api/github/prs", headers=headers, json={"projectPath": path})
    pj = pr.json()
    state["open_prs"] = [{"number": p.get("number"), "title": p.get("title")} for p in (pj.get("openPRs") or [])]
    state["merged_prs"] = [{"number": p.get("number"), "title": p.get("title")} for p in (pj.get("mergedPRs") or [])]
    return state


@tool
async def repo_origin_state(project_path: str) -> str:
    """Origin truth for one repo — open issues + open/merged PRs, no shell, no token.

    Reads protoMaker's GitHub-App-backed `/api/github/issues` + `/api/github/prs`
    (server-side `gh`), so I can answer "is this actually shipped?" without running
    git/gh myself (which trips the HITL gate). Returns JSON: {"open_issues":
    [{number,title}], "open_prs": [...], "merged_prs": [...]}.
    """
    base, key = _api_base_key()
    if not base or not key:
        return json.dumps({"error": "PROTOMAKER_API_BASE/AUTOMAKER_API_URL or AUTOMAKER_API_KEY not set"})
    import httpx

    headers = {"X-API-Key": key, "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            s = await _origin_state(client, base, headers, project_path)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": f"origin state read failed: {e}"})
    s.pop("open_issue_numbers", None)
    return json.dumps(s)


_DONE = {"done"}


def _verdict(status: str, issue_open: bool) -> str:
    """Reconcile rule as code: done = issue closed / merged; an OPEN issue means actionable."""
    done = status in _DONE
    if done and issue_open:
        return "over_closed"      # marked done but its issue is still OPEN — don't trust the done
    if not done and not issue_open:
        return "maybe_shipped"    # issue closed but feature still open — likely shipped elsewhere
    return "consistent"


@tool
async def fleet_reconcile() -> str:
    """Deterministic drift report across the whole fleet — features vs origin truth, in parallel.

    For every project (concurrently): cross-references each issue-linked feature's
    board status against the live GitHub issue state, applying the reconcile rule
    in CODE — a `done` feature whose issue is still OPEN is `over_closed` (don't
    trust the done); an open feature whose issue is CLOSED is `maybe_shipped`. No
    shell, no hand-judgement.

    Returns JSON: {"fleet": {over_closed,maybe_shipped,consistent,unlinked},
    "drift": [{repo,feature_id,title,status,issue,issue_open,verdict}],
    "projects": [{repo, over_closed, maybe_shipped, consistent, unlinked}]}.
    Only `over_closed` + `maybe_shipped` items appear in `drift` — those are what I act on.
    """
    base, key = _api_base_key()
    if not base or not key:
        return json.dumps({"error": "PROTOMAKER_API_BASE/AUTOMAKER_API_URL or AUTOMAKER_API_KEY not set"})
    import asyncio

    import httpx

    headers = {"X-API-Key": key, "Content-Type": "application/json"}

    async def _proj(client, proj):
        g = proj.get("github") or {}
        repo = f"{g.get('owner')}/{g.get('repo')}" if g.get("owner") and g.get("repo") else None
        path = proj.get("path")
        row = {"repo": repo, "over_closed": 0, "maybe_shipped": 0, "consistent": 0, "unlinked": 0, "drift": []}
        try:
            fr = await client.post(f"{base}/api/features/list", headers=headers, json={"projectPath": path})
            feats = fr.json().get("features") or []
            origin = await _origin_state(client, base, headers, path)
        except Exception as e:  # noqa: BLE001
            row["error"] = str(e)[:80]
            return row
        openset = origin["open_issue_numbers"]
        for f in feats:
            num = f.get("githubIssueNumber") or f.get("issueNumber")
            if not num:
                row["unlinked"] += 1
                continue
            v = _verdict(f.get("status") or "", num in openset)
            row[v] += 1
            if v != "consistent":
                row["drift"].append({"repo": repo, "feature_id": f.get("id"), "title": (f.get("title") or "")[:80],
                                     "status": f.get("status"), "issue": num, "issue_open": num in openset, "verdict": v})
        return row

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            g = await client.get(f"{base}/api/settings/global", headers=headers)
            projs = g.json().get("settings", g.json()).get("projects") or []
            rows = await asyncio.gather(*[_proj(client, p) for p in projs])
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": f"fleet reconcile failed: {e}"})

    fleet = {"over_closed": 0, "maybe_shipped": 0, "consistent": 0, "unlinked": 0}
    drift = []
    for r in rows:
        for k in fleet:
            fleet[k] += r.get(k, 0)
        drift.extend(r.pop("drift", []))
    return json.dumps({"fleet": fleet, "drift": drift, "projects": rows})


@tool
async def fleet_readiness() -> str:
    """Auto-mode pre-flight for every project — is it SAFE to start? Deterministic, parallel.

    Encodes the preconditions the delivery saga taught us, so I never start a
    project that will local-merge, churn, or sit paused. Per project (concurrently)
    it checks: a **GitHub remote** to open PRs against, **isolation** on (effective
    per-project `useWorktrees`), **not paused** (protoMaker `pausedProjects`), a
    **ready backlog** to actually run, and **not blocked-heavy**.
    Branch protection / required-checks is enforced by protoMaker at start (there's
    no pre-check endpoint) and is flagged as such rather than asserted.

    **Base dirtiness is NOT a blocker** (corrected 2026-06-05). Verified against
    protoMaker's ``createWorktreeForBranch`` (post-#4100): it checks out
    ``origin/<base>`` into a *separate* dir, so the base working tree's
    uncommitted/untracked files are irrelevant and don't propagate. The old
    "dirty base → worktree creation will fail" gate was a false premise that
    over-gated the whole fleet to 0/8 on protoMaker's own ``.automaker/``/``.beads/``
    runtime churn. We now surface genuine stray *source* files (runtime paths
    filtered) as an advisory ``notes`` entry, never a blocker. The real
    worktree-creation failure mode — a configured base branch absent on origin
    (the #4086 ``origin/dev`` invalid-ref) — now degrades rather than throws and
    isn't cheaply pre-checkable here; a dirty *feature* worktree is a runtime
    restart-safety check, also not pre-flight.

    Returns JSON: {"ready": ["owner/name", ...], "not_ready": [{repo, blockers}],
    "compliance_note": "...", "projects": [{repo, ready, worktrees, paused, backlog,
    blocked, blocked_pct, dirty_files, runtime_dirt, blockers, notes}]}.
    """
    base, key = _api_base_key()
    if not base or not key:
        return json.dumps({"error": "PROTOMAKER_API_BASE/AUTOMAKER_API_URL or AUTOMAKER_API_KEY not set"})
    import asyncio

    import httpx

    headers = {"X-API-Key": key, "Content-Type": "application/json"}

    # protoMaker writes these into a checkout at runtime — never a real readiness
    # signal, and base dirtiness doesn't block worktree creation regardless.
    _runtime_dirt = (".automaker/", ".automaker", ".beads/", ".beads")

    def _is_runtime(p: str) -> bool:
        return p.startswith(_runtime_dirt)

    async def _ready(client, proj, worktrees_global, paused_keys):
        g = proj.get("github") or {}
        repo = f"{g.get('owner')}/{g.get('repo')}" if g.get("owner") and g.get("repo") else None
        path = proj.get("path")
        # Always carry a readable label — a project with no remote has repo=None
        # (a hard blocker handled below) but must still be identifiable in output.
        row = {"repo": repo or proj.get("name") or path or "(unknown)", "blockers": [], "notes": []}
        try:
            sj = (await client.post(f"{base}/api/sitrep", headers=headers, json={"projectPath": path})).json()
            s = sj.get("board") or sj.get("sitrep") or sj
            gj = (await client.post(f"{base}/api/git/enhanced-status", headers=headers, json={"projectPath": path})).json()
            files = gj.get("files") or []
        except Exception as e:  # noqa: BLE001
            row.update({"error": str(e)[:80], "ready": False})
            return row
        total = s.get("total") or 0
        backlog = s.get("backlog") or 0
        blocked = s.get("blocked") or 0
        pct = round(100 * blocked / total) if total else 0
        # Split base dirt into runtime churn (.automaker/.beads — ignored) vs genuine
        # source changes (advisory only; not a worktree blocker — see docstring).
        paths = [(f.get("filePath") if isinstance(f, dict) else f) or "" for f in files]
        src_dirty = [p for p in paths if p and not _is_runtime(p)]
        runtime_dirt = len(paths) - len(src_dirty)
        # Worktree isolation is per-project: protoMaker resolves useWorktrees from the
        # project's own settings (default true); the global projects[] entries don't
        # carry it, so a global-only read would miss an explicit per-project opt-out.
        # Resolve per-project -> global -> true; a settings-fetch failure degrades to
        # the global value (a transient error must never flip a readiness verdict).
        wt = worktrees_global
        try:
            ps = (await client.post(f"{base}/api/settings/project", headers=headers,
                                    json={"projectPath": path})).json()
            pset = ps.get("settings", ps) if isinstance(ps, dict) else {}
            if isinstance(pset, dict) and pset.get("useWorktrees") is not None:
                wt = bool(pset.get("useWorktrees"))
        except Exception:  # noqa: BLE001
            row["notes"].append("per-project settings unreadable — used global useWorktrees")
        paused = bool(proj.get("id") in paused_keys or path in paused_keys
                      or (repo and repo in paused_keys) or proj.get("name") in paused_keys)
        row.update({"worktrees": wt, "paused": paused, "backlog": backlog,
                    "blocked": blocked, "blocked_pct": pct,
                    "dirty_files": len(src_dirty), "runtime_dirt": runtime_dirt})
        if not repo:
            row["blockers"].append("no GitHub remote — can't open a PR")
        if not wt:
            row["blockers"].append("useWorktrees OFF — agents would commit in-place, no PR")
        if paused:
            row["blockers"].append("paused (protoMaker pausedProjects)")
        if backlog == 0:
            row["blockers"].append("no ready backlog")
        if pct >= 25:
            row["blockers"].append(f"blocked-heavy ({pct}%)")
        if src_dirty:
            row["notes"].append(
                f"{len(src_dirty)} uncommitted source file(s) (advisory — inert for "
                f"worktree creation): {', '.join(src_dirty[:5])}"
                + (" …" if len(src_dirty) > 5 else ""))
        row["ready"] = not row["blockers"]
        return row

    def _paused_keys(settings) -> set:
        keys: set = set()
        for p in settings.get("pausedProjects") or []:
            if isinstance(p, str):
                keys.add(p)
            elif isinstance(p, dict):
                keys.update(v for v in (p.get("id"), p.get("path"), p.get("name")) if v)
        return keys

    try:
        async with httpx.AsyncClient(timeout=45) as client:
            g = (await client.get(f"{base}/api/settings/global", headers=headers)).json()
            settings = g.get("settings", g)
            worktrees_global = bool(settings.get("useWorktrees"))
            paused_keys = _paused_keys(settings)
            projs = settings.get("projects") or []
            rows = await asyncio.gather(*[_ready(client, p, worktrees_global, paused_keys) for p in projs])
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": f"fleet readiness failed: {e}"})

    return json.dumps({
        "ready": [r["repo"] for r in rows if r.get("ready")],
        "not_ready": [{"repo": r["repo"], "blockers": r["blockers"]} for r in rows if not r.get("ready")],
        "compliance_note": "branch-protection / required-checks is enforced by protoMaker at auto-mode start (no pre-check endpoint)",
        "projects": rows,
    })


# A2A card skills (roxy identity) — registered via the upstream #570 seam
# (registry.register_a2a_skill) so server/a2a.py is never edited on a fork.
_A2A_SKILLS: list[dict] = [
    {
        "id": "portfolio_sitrep",
        "name": "Portfolio SitRep",
        "description": "Sweep every managed protoMaker project and return a roll-up: a portfolio total then per-project flowing / stalled / blocked.",
        "tags": ["pm", "status"],
        "examples": ["portfolio_sitrep"],
    },
    {
        "id": "board_sweep",
        "name": "Board Sweep",
        "description": "Sweep the portfolio, then take the smallest unblocking action per project and report what was done.",
        "tags": ["pm", "unblock"],
        "examples": ["board_sweep", "board_sweep protocli"],
    },
    {
        "id": "project_decompose",
        "name": "Project Decompose",
        "description": "Decompose a project into epics -> milestones -> features (research -> PRD -> milestones -> features), pausing at the human approval gate.",
        "tags": ["pm", "planning"],
        "examples": ["project_decompose <project>"],
    },
    {
        "id": "unblock_feature",
        "name": "Unblock Feature",
        "description": "Investigate a blocked/stalled feature and take the smallest unblocking action, or escalate with a crisp ask.",
        "tags": ["pm", "unblock"],
        "examples": ["unblock_feature <featureId>"],
    },
    {
        "id": "audit_project",
        "name": "Audit Project",
        "description": "Audit a project (a local dir or GitHub repo), read-only: inspect code, config, tests and deploy setup and return a prioritized, evidence-backed backlog proposal (features / tech-debt / bugs). Assessment only — stops before onboarding.",
        "tags": ["pm", "audit"],
        "examples": ["audit_project <dir|owner/repo>", "audit the portfolio"],
    },
    {
        # Structured: the #476 finalizer enforces output_schema and emits the
        # validated onboarding plan as an onboarding-plan-v1 DataPart alongside
        # the prose. The skillHint surfaces [skill: onboard_project], anchoring
        # the lead to the onboard-project disk-skill playbook (not project_decompose).
        "id": "onboard_project",
        "name": "Onboard Project",
        "description": "Onboard a project (a local dir or GitHub repo) into the protoMaker fleet: read its conformance gaps against the workspace-config + CI-lockdown standards, create the onboarding project with a two-epic board (fleet-conformance true-up + the audit's product backlog), and register it via /api/onboard. Branch protection is an operator step. Read-only; proposes first, executes on approval.",
        "tags": ["pm", "onboarding", "planning"],
        "examples": ["onboard_project <dir|owner/repo>", "onboard the portfolio"],
        "result_mime": "application/vnd.protolabs.onboarding-plan-v1+json",
        "output_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "project": {"type": "string", "description": "project slug, e.g. portfolio"},
                "target": {"type": "string", "description": "local path or owner/repo audited"},
                "summary": {"type": "string", "description": "2-3 sentence read: what it is + stack"},
                "conformance": {
                    "type": "array",
                    "description": "one row per workspace-config / CI-lockdown rule",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "rule": {"type": "string"},
                            "status": {"type": "string", "enum": ["pass", "fail", "unknown"]},
                            "trueUp": {"type": "string", "description": "action to close the gap; empty if pass"},
                        },
                        "required": ["rule", "status"],
                    },
                },
                "board": {
                    "type": "object",
                    "additionalProperties": False,
                    "description": "the two-epic onboarding board",
                    "properties": {
                        "fleetConformance": {
                            "type": "array",
                            "description": "true-up features; actor=operator for branch protection",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "title": {"type": "string"},
                                    "actor": {"type": "string", "enum": ["agent", "operator"]},
                                },
                                "required": ["title", "actor"],
                            },
                        },
                        "productBacklog": {
                            "type": "array",
                            "description": "features / tech-debt / bugs from the audit",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "title": {"type": "string"},
                                    "priority": {"type": "string", "enum": ["P0", "P1", "P2"]},
                                },
                                "required": ["title", "priority"],
                            },
                        },
                    },
                    "required": ["fleetConformance", "productBacklog"],
                },
                "fleetRegister": {"type": "string", "description": "the exact POST /api/onboard call to run on approval"},
                "operatorActions": {"type": "array", "items": {"type": "string"}, "description": "e.g. the apply-branch-protection command + ruleset prerequisite"},
                "openQuestions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["project", "conformance", "board", "fleetRegister"],
        },
    },
    {
        "id": "chat",
        "name": "Chat",
        "description": "General-purpose chat / Q&A about the portfolio.",
        "tags": ["general"],
        "examples": ["what's the portfolio status?"],
    },
]


def _roxy_thread_id_resolver(request_metadata: dict, session_id: str):
    """Key working memory to the PROJECT (roxy domain separation, Phase 2),
    derived from A2A request metadata. This is the upstream #571 thread_id-resolver
    seam, replacing the old executor ``thread_key`` plumbing: returns the
    project-scoped thread_id when the turn is pinned to a project, else ``None``
    so the chat backend falls back to the default ``a2a:<session_id>``.
    """
    md = request_metadata or {}
    path = md.get("projectPath") or md.get("project_path")
    name = (md.get("project") or md.get("projectSlug")
            or md.get("projectRepo") or md.get("project_slug"))
    scope: dict = {}
    if isinstance(path, str) and path.strip():
        scope["path"] = path.strip()
    if isinstance(name, str) and name.strip():
        scope["name"] = name.strip()
    session = project_session.resolve(scope)
    return f"a2a:{session.thread_key}" if session else None


def register(registry) -> None:
    """Entry point — register the fleet power tools."""
    registry.register_tool(repo_github_remote)
    registry.register_tool(fleet_register)
    registry.register_tool(fleet_registry)
    registry.register_tool(fleet_sitrep)
    registry.register_tool(repo_origin_state)
    registry.register_tool(fleet_reconcile)
    registry.register_tool(fleet_readiness)
    # roxy identity + memory scoping via the upstream fork seams (#570/#571)
    for _spec in _A2A_SKILLS:
        registry.register_a2a_skill(_spec)
    registry.register_thread_id_resolver(_roxy_thread_id_resolver)
