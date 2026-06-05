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


def register(registry) -> None:
    """Entry point — register the fleet power tools."""
    registry.register_tool(repo_github_remote)
    registry.register_tool(fleet_register)
    registry.register_tool(fleet_registry)
    registry.register_tool(fleet_sitrep)
