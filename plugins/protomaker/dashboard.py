"""protoMaker Fleet dashboard — a console rail view (ADR 0026).

A plugin-contributed console surface: a left-rail "Fleet" icon opens this live
view of the portfolio — per-project board counts, readiness, and the fleet
rollup — so the operator can WATCH the boards instead of polling over A2A. The
console embeds ``GET /plugins/protomaker/dashboard`` in an iframe; the page polls
``GET /plugins/protomaker/state`` (server-side, via the fleet tools) and renders.
The snapshot is cached briefly so dashboard polling doesn't hammer the board API.
"""

from __future__ import annotations

import json
import time

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

from ._fleet import fleet_readiness

_CACHE: dict = {"at": -999.0, "data": None}
_TTL = 8.0  # seconds — cap how often the panel hits the board API


async def _state() -> dict:
    now = time.monotonic()
    if _CACHE["data"] is not None and now - _CACHE["at"] < _TTL:
        return _CACHE["data"]
    try:
        raw = await fleet_readiness.ainvoke({})  # the fleet_readiness tool's JSON
        data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception as e:  # noqa: BLE001 — surface a soft error in the panel
        data = {"error": str(e)[:200], "projects": []}
    _CACHE.update(at=now, data=data)
    return data


_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>protoMaker Fleet</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; font:13px/1.5 ui-sans-serif,system-ui,sans-serif; background:#0b0e14; color:#c9d1d9; padding:16px; }
  h1 { font-size:15px; margin:0 0 2px; color:#e6edf3; }
  .sub { color:#7d8590; font-size:12px; margin-bottom:14px; }
  .roll { display:flex; gap:10px; margin-bottom:14px; flex-wrap:wrap; }
  .card { background:#161b22; border:1px solid #21262d; border-radius:8px; padding:10px 14px; min-width:96px; }
  .card .n { font-size:20px; font-weight:600; color:#e6edf3; }
  .card .l { font-size:11px; color:#7d8590; text-transform:uppercase; letter-spacing:.04em; }
  table { width:100%; border-collapse:collapse; }
  th,td { text-align:left; padding:7px 10px; border-bottom:1px solid #21262d; }
  th { color:#7d8590; font-size:11px; text-transform:uppercase; letter-spacing:.04em; }
  td.num { text-align:right; font-variant-numeric:tabular-nums; }
  .pill { padding:1px 8px; border-radius:999px; font-size:11px; font-weight:600; }
  .ready { background:#12261a; color:#3fb950; } .notready { background:#2d2111; color:#d29922; }
  .blocked { color:#f85149; } .muted { color:#7d8590; }
  .err { background:#2d1417; border:1px solid #5c2228; color:#f85149; padding:10px; border-radius:8px; }
  .ts { color:#7d8590; font-size:11px; margin-top:12px; }
</style></head>
<body>
  <h1>protoMaker Fleet</h1>
  <div class="sub">Portfolio readiness + board counts — live from the fleet tools.</div>
  <div id="roll" class="roll"></div>
  <div id="body"></div>
  <div class="ts" id="ts"></div>
<script>
async function tick() {
  try {
    const d = await (await fetch('state')).json();
    if (d.error) { document.getElementById('body').innerHTML =
      '<div class="err">'+d.error+'</div>'; return; }
    const projs = d.projects || [];
    const ready = (d.ready || []).length;
    const total = projs.length;
    const blocked = projs.reduce((a,p)=>a+(p.blocked||0),0);
    const backlog = projs.reduce((a,p)=>a+(p.backlog||0),0);
    document.getElementById('roll').innerHTML = [
      ['Projects', total], ['Ready', ready], ['Backlog', backlog], ['Blocked', blocked]
    ].map(([l,n])=>'<div class="card"><div class="n">'+n+'</div><div class="l">'+l+'</div></div>').join('');
    const rows = projs.map(p => {
      const repo = (p.repo||'').split('/').pop() || p.path || '?';
      const rd = p.ready ? '<span class="pill ready">ready</span>'
                         : '<span class="pill notready">not ready</span>';
      const bl = (p.blocked||0) > 0 ? '<span class="blocked">'+p.blocked+'</span>' : '<span class="muted">0</span>';
      return '<tr><td>'+repo+'</td><td class="num">'+(p.backlog??'–')+'</td>'
           + '<td class="num">'+bl+'</td><td>'+rd+'</td></tr>';
    }).join('');
    document.getElementById('body').innerHTML =
      '<table><thead><tr><th>Project</th><th style="text-align:right">Backlog</th>'
      +'<th style="text-align:right">Blocked</th><th>Readiness</th></tr></thead><tbody>'
      + (rows || '<tr><td colspan=4 class="muted">no projects</td></tr>') + '</tbody></table>';
    document.getElementById('ts').textContent = 'updated ' + new Date().toLocaleTimeString();
  } catch (e) {
    document.getElementById('body').innerHTML = '<div class="err">'+e+'</div>';
  }
}
tick(); setInterval(tick, 10000);
</script></body></html>"""


def build_dashboard_router() -> APIRouter:
    """The Fleet dashboard router — mounted at /plugins/protomaker/ by the loader."""
    router = APIRouter()

    @router.get("/dashboard")
    async def dashboard() -> HTMLResponse:  # noqa: ANN202
        return HTMLResponse(_PAGE)

    @router.get("/state")
    async def state() -> JSONResponse:  # noqa: ANN202
        return JSONResponse(await _state())

    return router
