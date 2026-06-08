"""protoMaker plugin — Roxy as a full-bundle agent-as-plugin (ADR 0027).

Turns stock protoAgent into **Roxy, the protoMaker portfolio manager**: fleet
read/triage tools, the 7 A2A fleet skills, per-project memory scoping, Quinn
peer-delegation, the project-operations/audit/onboard skills, and the
project-scope middleware. Supersedes the ``fleet-onboarding`` plugin — enable
exactly one (``plugins.enabled: [protomaker]``, drop ``fleet-onboarding``).

Persona/SOUL is **deploy config** (``langgraph-config.yaml`` ``a2a.description`` +
``SOUL.md``), not bundled here — matching the spacetraders/finance reference
plugins (identity = base config + skills; capability = the plugin).

Designed to extract cleanly to a standalone ``protomaker-plugin`` repo: fleet
tools live in ``_fleet`` (relocated verbatim from fleet-onboarding), the scope
banner in ``middleware``, skills in ``skills/``. The Quinn peer tools are imported
from the core ``tools.peer_tools`` during this in-roxy validation phase; inline
them on extraction.
"""

from __future__ import annotations

import logging

from ._fleet import (
    _A2A_SKILLS,
    _roxy_thread_id_resolver,
    fleet_readiness,
    fleet_reconcile,
    fleet_register,
    fleet_registry,
    fleet_sitrep,
    gh_ci_failure,
    gh_ci_runs,
    gh_issue,
    gh_issues,
    gh_pr,
    repo_github_remote,
    repo_origin_state,
)
from .middleware import register_scope_middleware

log = logging.getLogger("protoagent.plugins.protomaker")

# The 12 fleet read/triage/board tools (relocated from fleet-onboarding).
_FLEET_TOOLS = [
    repo_github_remote,
    fleet_register,
    fleet_registry,
    fleet_sitrep,
    repo_origin_state,
    fleet_reconcile,
    fleet_readiness,
    gh_ci_runs,      # read-only GitHub triage eyes (ROXY_GH_READ_TOKEN)
    gh_ci_failure,
    gh_issue,
    gh_pr,
    gh_issues,
]


def register(registry) -> None:
    """Entry point — wire the full protoMaker bundle onto the host."""
    for t in _FLEET_TOOLS:
        registry.register_tool(t)

    # Quinn peer-delegation (peer_list / peer_consult) — get_peer_tools() returns
    # them only when a PEER_<HANDLE>_URL is configured (Quinn = PEER_WORKSTACEAN_URL).
    try:
        from tools.peer_tools import get_peer_tools

        peers = list(get_peer_tools())
        for t in peers:
            registry.register_tool(t)
        log.info("[protomaker] registered %d peer-delegation tool(s)", len(peers))
    except Exception:  # noqa: BLE001 — peer federation is optional, never break load
        log.exception("[protomaker] peer tools unavailable — skipping")

    # Roxy identity (7 A2A card skills) + per-project memory scoping (fork seams
    # #570/#571) — what made roxy's a2a.py/chat.py zero-delta vs upstream.
    for spec in _A2A_SKILLS:
        registry.register_a2a_skill(spec)
    registry.register_thread_id_resolver(_roxy_thread_id_resolver)

    # Project-scope middleware (domain separation). Rides register_middleware when
    # the host exposes it; dormant otherwise (the inline a2a_executor banner still
    # covers it until the seam lands + this supersedes it).
    register_scope_middleware(registry)

    # Console "Fleet" rail view (ADR 0026) — a live readiness/board panel at
    # /plugins/protomaker/dashboard. Best-effort: a router failure never breaks load.
    try:
        from .dashboard import build_dashboard_router

        registry.register_router(build_dashboard_router())
        log.info("[protomaker] registered Fleet dashboard view (/plugins/protomaker/dashboard)")
    except Exception:  # noqa: BLE001
        log.exception("[protomaker] dashboard router unavailable — skipping")

    log.info(
        "[protomaker] registered: %d fleet tools + %d A2A skills + thread_id resolver",
        len(_FLEET_TOOLS),
        len(_A2A_SKILLS),
    )
