"""fleet_readiness — the auto-mode pre-flight gate.

Locks the 2026-06-05 correction: a dirty BASE working tree (protoMaker's own
``.automaker/``/``.beads/`` runtime churn AND genuine stray source files) is NOT
a worktree-creation blocker, so it must not gate a project to not-ready. The real
gates that DO block: useWorktrees off, paused (``pausedProjects``), no ready
backlog, blocked-heavy. See the fleet_readiness docstring for the protoMaker
``createWorktreeForBranch`` evidence.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

_PLUGIN = Path(__file__).resolve().parent.parent / "plugins" / "fleet-onboarding" / "__init__.py"


def _load():
    spec = importlib.util.spec_from_file_location("fleet_onboarding_under_test", _PLUGIN)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    """Routes GET /settings/global, POST /sitrep, /git/enhanced-status, /settings/project."""

    def __init__(self, settings, sitreps, statuses, proj_settings=None, raise_settings_for=()):
        self._settings = settings
        self._sitreps = sitreps                 # path -> {total, backlog, blocked}
        self._statuses = statuses               # path -> [file dicts]
        self._proj_settings = proj_settings or {}  # path -> per-project settings dict
        self._raise_settings_for = set(raise_settings_for)  # paths whose /settings/project errors

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        assert url.endswith("/api/settings/global")
        return _Resp({"settings": self._settings})

    async def post(self, url, headers=None, json=None):
        path = (json or {}).get("projectPath")
        if url.endswith("/api/sitrep"):
            return _Resp({"board": self._sitreps.get(path, {})})
        if url.endswith("/api/git/enhanced-status"):
            return _Resp({"files": self._statuses.get(path, []), "success": True})
        if url.endswith("/api/settings/project"):
            if path in self._raise_settings_for:
                raise httpx.ConnectError("settings endpoint down")
            return _Resp({"settings": self._proj_settings.get(path, {})})
        raise AssertionError(f"unexpected url {url}")


def _proj(pid, repo, path, name):
    owner, r = repo.split("/")
    return {"id": pid, "name": name, "path": path,
            "github": {"owner": owner, "repo": r}, "defaultBranch": "main"}


@pytest.mark.asyncio
async def test_dirty_base_is_not_a_blocker_but_paused_and_no_backlog_are(monkeypatch):
    monkeypatch.setenv("AUTOMAKER_API_URL", "http://pm.test")
    monkeypatch.setenv("AUTOMAKER_API_KEY", "k")

    settings = {
        "useWorktrees": True,
        "pausedProjects": [{"id": "p-app", "name": "protoApp", "path": "/w/app"}],
        "projects": [
            _proj("p-content", "protoLabsAI/protoContent", "/w/content", "protoContent"),
            _proj("p-app", "protoLabsAI/protoApp", "/w/app", "protoApp"),
            _proj("p-rel", "protoLabsAI/release-tools", "/w/rel", "release-tools"),
        ],
    }
    sitreps = {
        "/w/content": {"total": 10, "backlog": 8, "blocked": 0},
        "/w/app": {"total": 24, "backlog": 24, "blocked": 0},
        "/w/rel": {"total": 5, "backlog": 0, "blocked": 0},
    }
    # content has BOTH runtime dirt (.automaker/.beads) and a genuine source file —
    # none of it may block. app & rel dirty too, to prove it never gates.
    statuses = {
        "/w/content": [
            {"filePath": ".automaker/settings.json"},
            {"filePath": ".beads/issues.jsonl"},
            {"filePath": "docs/reference/voice-audit.md"},
        ],
        "/w/app": [{"filePath": ".automaker/x"}],
        "/w/rel": [{"filePath": "bin/roll-changelog.mjs"}],
    }

    m = _load()
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **k: _FakeClient(settings, sitreps, statuses))

    coro = m.fleet_readiness.coroutine
    out = json.loads(await coro())

    by_repo = {p["repo"]: p for p in out["projects"]}

    # protoContent: dirty base (runtime + source) but worktrees on, not paused,
    # has backlog, not blocked-heavy → READY despite the dirt.
    content = by_repo["protoLabsAI/protoContent"]
    assert content["ready"] is True, content
    assert content["runtime_dirt"] == 2          # .automaker + .beads filtered out
    assert content["dirty_files"] == 1           # only the genuine source file counts
    assert content["notes"] and "voice-audit" in content["notes"][0]
    assert "protoLabsAI/protoContent" in out["ready"]

    # protoApp: paused → NOT ready, even with a full backlog.
    app = by_repo["protoLabsAI/protoApp"]
    assert app["ready"] is False
    assert any("paused" in b for b in app["blockers"])

    # release-tools: no backlog → NOT ready (and the source-dirty file is advisory).
    rel = by_repo["protoLabsAI/release-tools"]
    assert rel["ready"] is False
    assert any("no ready backlog" in b for b in rel["blockers"])
    assert not any("dirty base" in b for b in rel["blockers"])  # the old false gate is gone


@pytest.mark.asyncio
async def test_per_project_useworktrees_off_blocks_even_with_backlog(monkeypatch):
    """A project that opts OUT of worktrees in its OWN settings must gate, even when
    the global default is ON — the global projects[] list doesn't carry useWorktrees,
    so a global-only read would have missed this and green-lit an in-place run."""
    monkeypatch.setenv("AUTOMAKER_API_URL", "http://pm.test")
    monkeypatch.setenv("AUTOMAKER_API_KEY", "k")
    settings = {
        "useWorktrees": True,  # global ON ...
        "pausedProjects": [],
        "projects": [_proj("p-x", "protoLabsAI/projX", "/w/x", "projX")],
    }
    sitreps = {"/w/x": {"total": 4, "backlog": 4, "blocked": 0}}
    proj_settings = {"/w/x": {"useWorktrees": False}}  # ... but the project overrides OFF

    m = _load()
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **k: _FakeClient(settings, sitreps, {}, proj_settings))
    out = json.loads(await m.fleet_readiness.coroutine())

    x = {p["repo"]: p for p in out["projects"]}["protoLabsAI/projX"]
    assert x["worktrees"] is False
    assert x["ready"] is False
    assert any("useWorktrees OFF" in b for b in x["blockers"])
    assert "protoLabsAI/projX" not in out["ready"]


@pytest.mark.asyncio
async def test_no_github_remote_blocks(monkeypatch):
    """A project with no GitHub remote can't open a PR — it must never be 'ready',
    and it stays identifiable in output via a name fallback (repo is None)."""
    monkeypatch.setenv("AUTOMAKER_API_URL", "http://pm.test")
    monkeypatch.setenv("AUTOMAKER_API_KEY", "k")
    settings = {
        "useWorktrees": True,
        "pausedProjects": [],
        "projects": [{"id": "p-local", "name": "localOnly", "path": "/w/local", "github": {}}],
    }
    sitreps = {"/w/local": {"total": 3, "backlog": 3, "blocked": 0}}

    m = _load()
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **k: _FakeClient(settings, sitreps, {}))
    out = json.loads(await m.fleet_readiness.coroutine())

    row = {p["repo"]: p for p in out["projects"]}["localOnly"]  # label falls back to name
    assert row["ready"] is False
    assert any("no GitHub remote" in b for b in row["blockers"])
    assert "localOnly" not in out["ready"]


@pytest.mark.asyncio
async def test_project_settings_fetch_failure_degrades_to_global(monkeypatch):
    """A transient /settings/project failure must degrade to the global useWorktrees
    value, never flip a verdict — and it records a note so the degrade is visible."""
    monkeypatch.setenv("AUTOMAKER_API_URL", "http://pm.test")
    monkeypatch.setenv("AUTOMAKER_API_KEY", "k")
    settings = {
        "useWorktrees": True,
        "pausedProjects": [],
        "projects": [_proj("p-y", "protoLabsAI/projY", "/w/y", "projY")],
    }
    sitreps = {"/w/y": {"total": 2, "backlog": 2, "blocked": 0}}

    m = _load()
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **k: _FakeClient(settings, sitreps, {}, {}, raise_settings_for=("/w/y",)))
    out = json.loads(await m.fleet_readiness.coroutine())

    y = {p["repo"]: p for p in out["projects"]}["protoLabsAI/projY"]
    assert y["worktrees"] is True       # fell back to global, didn't flip
    assert y["ready"] is True
    assert any("per-project settings unreadable" in n for n in y["notes"])
