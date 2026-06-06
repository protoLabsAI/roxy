"""roxy's thread_id resolver — per-project working-memory scoping via the upstream
#571 seam (replaces the old executor ``thread_key`` plumbing).

The fleet-onboarding plugin registers ``_roxy_thread_id_resolver`` through
``register_thread_id_resolver``; the chat backend calls it as
``(request_metadata, session_id) -> str | None``. This locks the mapping: a turn
pinned to a project (via A2A request metadata) keys memory to ``a2a:proj:<slug>``
— the SAME thread across different A2A conversations — while an unscoped turn
returns ``None`` so the backend falls back to ``a2a:<session_id>``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PLUGIN = Path(__file__).resolve().parent.parent / "plugins" / "fleet-onboarding" / "__init__.py"


def _resolver():
    spec = importlib.util.spec_from_file_location("fleet_onboarding_resolver_under_test", _PLUGIN)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m._roxy_thread_id_resolver


def test_scoped_turn_keys_memory_to_the_project():
    r = _resolver()
    # projectPath → slug from the path leaf
    assert r({"projectPath": "/work/protoContent"}, "ctx-1") == "a2a:proj:protocontent"
    # explicit project name wins over path
    assert r({"project": "protoPen", "projectPath": "/work/whatever"}, "ctx-1") == "a2a:proj:protopen"
    # alternate metadata keys are honored
    assert r({"projectSlug": "release-tools"}, "ctx-1") == "a2a:proj:release-tools"


def test_same_project_shares_a_thread_across_a2a_contexts():
    r = _resolver()
    a = r({"project": "protoContent"}, "context-A")
    b = r({"project": "protoContent"}, "context-B")  # different A2A context, same project
    assert a == b == "a2a:proj:protocontent"  # the BANANA invariant: memory follows the project


def test_unscoped_turn_falls_back_to_default():
    r = _resolver()
    assert r({}, "ctx-1") is None
    assert r(None, "ctx-1") is None
    assert r({"origin": "scheduler"}, "ctx-1") is None  # non-project metadata → no scoping


def test_cross_project_isolation():
    r = _resolver()
    assert r({"project": "protoContent"}, "ctx") != r({"project": "protoPen"}, "ctx")
