"""Per-project session scoping — roxy Phase 2 (greenfield).

One Roxy deployment behaves as *N project-scoped operators* by keying agent state
to the **project**, not the **process**. The A2A project scope (set per request via
metadata, surfaced by ``a2a_executor._extract_project_scope``) is resolved here into
a stable ``ProjectSession``:

- ``thread_key`` — the LangGraph **checkpoint thread**. Decoupling this from the A2A
  ``context_id`` is the whole trick: every turn scoped to ``protocli`` shares one
  continuous working memory (history/context), no matter which A2A conversation it
  arrived on, while ``protoagent`` lives on a completely separate thread. "Remembers
  its own project" with no N-container lifecycle.
- ``memory_ns`` — the durable **memory / ledger namespace** so learned facts and the
  beads ledger isolate per project too (wired by the memory/ledger layer).

An unscoped (fleet-wide) turn resolves to ``None`` — the existing global behavior is
unchanged, so fleet sweeps and general chat are untouched.

The session key is deterministic and derived (slug of the project name, else the
path leaf), so the caller doesn't have to invent thread ids — it just sends the
project. That keeps the contract for Ava trivial: route work with ``projectPath``
(and/or ``project``) and the right session is selected here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    return _NON_SLUG.sub("-", value.strip().lower()).strip("-")


def _path_leaf(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1] if path else ""


@dataclass(frozen=True)
class ProjectSession:
    """The resolved per-project session for a turn."""

    slug: str  # canonical project key, e.g. "protocli"
    thread_key: str  # checkpoint thread suffix, e.g. "proj:protocli"
    memory_ns: str  # durable memory/ledger namespace, e.g. "proj:protocli"


def resolve(scope: dict | None) -> ProjectSession | None:
    """Resolve an A2A project scope (``{path?, name?}``) to a ``ProjectSession``.

    ``None`` for an unscoped turn (fleet-wide / general) — global behavior, untouched.
    A scope with neither a usable name nor path also yields ``None`` (treated as
    unscoped rather than guessing).
    """
    if not scope:
        return None
    name = (scope.get("name") or "").strip()
    path = (scope.get("path") or "").strip()
    # Prefer the human label; fall back to the path's leaf directory.
    base = name or _path_leaf(path)
    slug = _slugify(base)
    if not slug:
        return None
    return ProjectSession(slug=slug, thread_key=f"proj:{slug}", memory_ns=f"proj:{slug}")
