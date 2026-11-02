"""Stage 4 demo: connect to the three OpsPilot MCP servers over stdio with the official SDK,
discover their tools (tools/list) and call a few of them as a seeded user.

    uv run python scripts/mcp_demo_client.py [--as alice] [--json out.json]

Reads ONLY OpsPilot's own settings (OPSPILOT_* env / this repo's .env). The MCP server
subprocesses get a minimal env: no LLM key, no JWT secret (identity travels as a short-lived
signed caller token in `_meta`). No paid API is ever called (servers force llm_provider=mock).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from opspilot.core.config import Settings
from opspilot.db.models import User
from opspilot.mcp.client import CallerCtx, MCPHub


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as", dest="who", default="alice")
    ap.add_argument("--json", dest="out")
    a = ap.parse_args()
    s = Settings()
    eng = create_engine(s.database_url)
    sf = sessionmaker(eng, expire_on_commit=False)
    with sf() as sess:
        u = sess.scalar(select(User).where(User.email == f"{a.who}@opspilot.local"))
    if u is None:
        print(f"user {a.who} not seeded", file=sys.stderr)
        return 2
    hub = MCPHub(s, session_factory=sf)
    rec: dict = {"user": a.who, "role": u.role, "calls": []}
    try:
        t0 = time.perf_counter()
        d = hub.discover()
        rec["discovery_s"] = round(time.perf_counter() - t0, 2)
        rec["servers"] = {
            n: {"protocol": v.get("protocol"), "tools": sorted(v.get("tools", {}))}
            for n, v in d.items()
        }
        print("== tools/list ==")
        for n, v in rec["servers"].items():
            print(f"  {n:<12} protocol={v['protocol']}  {', '.join(v['tools'])}")
        thread = f"mcp-demo-{uuid.uuid4().hex[:8]}"
        ctx = CallerCtx(str(u.id), u.role, thread, None, None)
        plan = [
            ("knowledge", "list_knowledge_bases", {}),
            ("knowledge", "search_knowledge", {"query": "ORD-5003 错误 处理", "top_k": 3}),
            ("diagnostics", "analyze_error_code", {"code": "ORD-5003"}),
            (
                "diagnostics",
                "query_system_logs",
                {"service": "order-service", "level": "ERROR", "limit": 5},
            ),
            ("diagnostics", "get_service_status", {"service": "order-service"}),
            ("ticket", "list_tickets", {"limit": 5}),
            (
                "ticket",
                "get_ticket",
                {"ticket_number": "TCK-0002"},
            ),  # bob's: ACL -> not_found for alice
            ("diagnostics", "query_system_logs", {"service": "../../etc"}),  # schema rejects
        ]
        print(f"== tools/call (thread {thread}) ==")
        for server, tool, args in plan:
            t1 = time.perf_counter()
            try:
                out = hub.call(server, tool, args, ctx)
                body = out.model_dump(mode="json")
                status, summary = "ok", json.dumps(body, ensure_ascii=False)[:160]
            except Exception as exc:
                status, summary = type(exc).__name__, str(exc)[:160]
            ms = int((time.perf_counter() - t1) * 1000)
            rec["calls"].append(
                {"server": server, "tool": tool, "status": status, "ms": ms, "summary": summary}
            )
            print(f"  {server}.{tool:<22} {status:<20} {ms:>5}ms  {summary}")
    finally:
        hub.close()
        eng.dispose()
    if a.out:
        Path(a.out).write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
