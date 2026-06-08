"""Per-turn project-scope middleware (roxy domain-separation, Phase 1).

When a caller pins a turn to ONE project — request metadata ``projectPath``
(absolute workspace path) and/or a label in ``project``/``projectSlug``/
``projectRepo`` — inject a prominent ACTIVE PROJECT SCOPE directive so every
board/automaker/filesystem tool call binds to that project and a multi-project
board can't bleed across turns (the wrong-project failure the fleet eval found).

This is the scope-banner that currently lives **inlined in the core**
``a2a_executor.execute`` — roxy's last remaining core fork-delta. ADR 0032
(upstream #687) makes it plugin-contributable: ``register_middleware(factory)`` +
``current_request_metadata()``. This module ports the banner to a LangGraph
``AgentMiddleware`` reading the per-turn metadata — **zero core edits**.

Import-guarded: ADR 0032 lands in roxy on the next upstream sync (it's
post-v0.24.0). Until then ``AgentMiddleware`` / ``current_request_metadata`` /
``register_middleware`` aren't present, so this stays **dormant** (the inline
``a2a_executor`` banner keeps working — no behavior gap). When the sync lands it
activates; then delete the inline banner so ``a2a_executor`` is zero-delta too.
"""

from __future__ import annotations

import logging

log = logging.getLogger("protoagent.plugins.protomaker.middleware")


# ── Pure scope helpers (unit-testable, framework-independent) ────────────────

def extract_project_scope(metadata: dict) -> dict:
    """The per-request project scope, or ``{}`` for a normal fleet-wide turn.

    ``path`` (preferred) is the absolute workspace path; ``name`` a human label.
    Mirrors ``a2a_executor._extract_project_scope`` exactly so behavior is
    identical when this supersedes the inline version.
    """
    md = metadata or {}
    path = md.get("projectPath") or md.get("project_path")
    name = (md.get("project") or md.get("projectSlug")
            or md.get("projectRepo") or md.get("project_slug"))
    scope: dict = {}
    if isinstance(path, str) and path.strip():
        scope["path"] = path.strip()
    if isinstance(name, str) and name.strip():
        scope["name"] = name.strip()
    return scope


def project_scope_banner(scope: dict) -> str:
    """The active-project directive injected as a system message so the scope is
    structural, not something the model must infer."""
    label = scope.get("name") or scope.get("path") or "?"
    path = scope.get("path")
    head = f"[project: {label}" + (f" | path: {path}" if path else "") + "]"
    body = (
        "ACTIVE PROJECT SCOPE — this turn operates on the project above ONLY. Every "
        "board / automaker / filesystem tool call MUST target this project"
        + (f' (projectPath="{path}")' if path else "")
        + ". Do NOT query, sweep, reconcile, or report on any other project this turn. "
        "If a path was not given, resolve it once via fleet_registry and use only that. "
        "Reach for fleet-wide tools only if explicitly asked for the whole fleet."
    )
    return f"{head}\n{body}"


# ── ADR 0032 AgentMiddleware (import-guarded; dormant until the sync) ─────────

try:
    from langchain.agents.middleware import AgentMiddleware  # type: ignore
    from langchain_core.messages import SystemMessage  # type: ignore

    from graph.middleware.request_context import current_request_metadata  # type: ignore

    _ADR0032 = True
except Exception:  # noqa: BLE001 — pre-sync (roxy ≤ v0.24.0): seam not present yet
    _ADR0032 = False


if _ADR0032:

    class ProjectScopeMiddleware(AgentMiddleware):  # type: ignore[misc]
        """Prepend the project-scope banner as a SystemMessage when the in-flight
        turn is project-pinned (reads the per-request A2A metadata contextvar)."""

        def before_model(self, state, runtime):  # noqa: ANN001 — LangGraph hook
            scope = extract_project_scope(current_request_metadata())
            if not scope:
                return None
            banner = SystemMessage(content=project_scope_banner(scope))
            return {"messages": [banner, *state["messages"]]}


def register_scope_middleware(registry) -> bool:
    """Wire the scope-banner middleware onto the host's ADR 0032 seam.

    Returns True if wired (seam + base class present), False if the host predates
    #687 (then the inline ``a2a_executor`` banner is still doing the job — no
    gap). Best-effort: never raises, never breaks plugin load.
    """
    register = getattr(registry, "register_middleware", None)
    if not callable(register) or not _ADR0032:
        log.info("[scope] register_middleware/AgentMiddleware not present yet — dormant "
                 "(inline a2a_executor banner remains active until #687 syncs)")
        return False
    try:
        register(lambda config: ProjectScopeMiddleware())
        log.info("[scope] ProjectScopeMiddleware registered via ADR 0032 seam")
        return True
    except Exception:  # noqa: BLE001
        log.exception("[scope] failed to register scope middleware")
        return False
