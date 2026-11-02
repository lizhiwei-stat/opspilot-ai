"""Stage 3 agent eval: 41 reproducible scenarios through the REAL AgentService (Postgres
checkpointer, guarded tools, approval) with the MOCK agent model by default.

    uv run python evals/run_agent_eval.py [--prepare] [--db-url URL] [--tag NAME]

!!! MOCK RESULTS MEASURE THE GRAPH, GUARDS, TOOLS AND CHECKPOINTING - NOT MODEL REASONING.
The mock model follows hand-written rules; several scenarios were written by the same author
as those rules. Do not quote these numbers as evidence of real-model quality.

--prepare  resets the TARGET database (truncate documents/agent tables, seed, ingest the eval
           corpus). Refuses unless the database name ends with _test or _eval.
Paid models: --provider qwen is refused unless OPSPILOT_ALLOW_PAID_API / approval id / budget /
paid_max_calls are set (gateway guards) - see docs/STAGE3_QWEN_TEST_PLAN.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))
os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")

from langgraph.checkpoint.postgres import PostgresSaver  # noqa: E402
from sqlalchemy import create_engine, make_url, select, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from opspilot.agent import checkpoint, rules  # noqa: E402
from opspilot.agent.llm import AgentReply, MockAgentLLM, build_agent_llm  # noqa: E402
from opspilot.agent.service import AgentService  # noqa: E402
from opspilot.core.config import Settings, get_settings  # noqa: E402
from opspilot.core.errors import AppError  # noqa: E402
from opspilot.db.models import Approval, KnowledgeBase, TicketDraft, User  # noqa: E402
from opspilot.llm.mock import MockEmbedding  # noqa: E402
from opspilot.services.ingestion import IngestionService  # noqa: E402

DATASET = ROOT / "evals" / "agent" / "agent_eval_v1.jsonl"
RESULTS = ROOT / "evals" / "results"
DISCLAIMER = (
    "MOCK 结果：使用规则模拟的 Mock 智能体，未调用任何大模型；指标只说明流程、权限、工具、"
    "检查点与审批是否按设计工作，不能作为真实模型推理能力的证据。"
)


def prepare(s: Settings) -> None:
    db = make_url(s.database_url).database or ""
    if not db.endswith(("_test", "_eval")):
        raise SystemExit(f"--prepare refuses to reset non-test database {db!r}")
    import importlib.util

    os.environ.setdefault("OPSPILOT_SEED_ADMIN_PASSWORD", "Admin#eval-only")
    os.environ.setdefault("OPSPILOT_SEED_USER_PASSWORD", "User#eval-only")
    spec = importlib.util.spec_from_file_location("seed_demo", ROOT / "scripts/seed_demo.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    eng = create_engine(s.database_url)
    with Session(eng) as sess:
        sess.execute(text("TRUNCATE documents, agent_runs, ticket_drafts CASCADE"))
        sess.commit()
        mod.seed(sess)
        sess.commit()
        admin = sess.scalar(select(User).where(User.email == "admin@opspilot.local"))
        ing = IngestionService(sess, s, MockEmbedding())
        corpus = ROOT / "evals" / "corpus"
        for kb_dir in sorted(p for p in corpus.iterdir() if p.is_dir() and p.name[0] != "_"):
            kb = sess.scalar(select(KnowledgeBase).where(KnowledgeBase.slug == kb_dir.name))
            for f in sorted(kb_dir.iterdir()):
                if f.suffix.lower() in {".md", ".txt", ".pdf"}:
                    ing.upload(admin, kb.id, f.name, f.read_bytes())  # type: ignore[arg-type,union-attr]
        sess.commit()
    eng.dispose()
    checkpoint.setup(s)


def pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, round(p / 100 * (len(xs) - 1))))
    return round(xs[k], 1)


def run_scenario(sc: dict, base: Settings, sf: Any, pool: Any, users: dict, provider: str) -> dict:
    overrides = dict(sc.get("settings") or {})
    if sc.get("faults"):
        overrides["agent_fault_injection"] = True
    s = base.model_copy(update=overrides) if overrides else base
    llm: Any
    if sc.get("script"):
        llm = MockAgentLLM(
            script=[
                AgentReply(tool_calls=[{"id": f"s{i}-{j}", **c} for j, c in enumerate(batch)])
                for i, batch in enumerate(sc["script"])
            ]
        )
    else:
        llm = MockAgentLLM() if provider == "mock" else build_agent_llm(s)
    svc = AgentService(
        s,
        sf,
        MockEmbedding(),
        PostgresSaver(pool),
        llm=llm,
        faults=sc.get("faults"),
        llm_faults=sc.get("llm_faults"),
    )
    user = users[sc["user"]]
    turns = sc.get("turns") or [sc["question"]]
    views, errors = [], []
    thread = None
    t0 = time.perf_counter()
    for q in turns:
        try:
            v = svc.start(user, q, thread)
        except AppError as exc:  # e.g. new turn blocked while awaiting approval
            errors.append(f"{type(exc).__name__}: {exc}")
            break
        views.append(v)
        thread = v["thread_id"]
        if v["status"] == "awaiting_approval" and sc.get("approve"):
            if sc["approve"] == "non_admin":
                try:
                    svc.approve(uuid.UUID(v["run_id"]), user, "approved")
                except AppError as exc:
                    errors.append(f"approval_refused:{type(exc).__name__}")
                views[-1] = svc.view(uuid.UUID(v["run_id"]), user)
            else:
                rid = uuid.UUID(v["run_id"])
                if sc.get("replay"):
                    try:
                        svc.resume(rid, user)  # must not bypass the approval
                    except AppError as exc:
                        errors.append(f"replay_refused:resume:{type(exc).__name__}")
                views[-1] = svc.approve(rid, users["admin"], sc["approve"])
                if sc.get("replay"):
                    for fn in (
                        lambda rid=rid: svc.approve(rid, users["admin"], "approved"),
                        lambda rid=rid: svc.resume(rid, user),
                    ):
                        try:
                            fn()
                        except AppError as exc:
                            errors.append(f"replay_refused:{type(exc).__name__}")
    wall = (time.perf_counter() - t0) * 1000
    return {"views": views, "errors": errors, "wall_ms": wall}


def evaluate(sc: dict, res: dict, sf: Any) -> dict:
    exp = sc.get("expect", {})
    views = res["views"]
    last = views[-1] if views else {}
    calls = [t for v in views for t in v["tools"]]
    executed = [t for t in calls if t["status"] != "denied" and not t["deduplicated"]]
    exec_names = sorted({t["tool"] for t in executed})
    run_ids = [uuid.UUID(v["run_id"]) for v in views]
    with sf() as sess:
        drafts = (
            sess.scalars(select(TicketDraft).where(TicketDraft.run_id.in_(run_ids))).all()
            if run_ids
            else []
        )
        unapproved = 0
        for d in drafts:
            if d.status in ("approved", "executed"):
                ap = sess.scalar(select(Approval).where(Approval.draft_id == d.id))
                rev = sess.get(User, ap.reviewer_id) if ap else None
                if ap is None or rev is None or rev.role != "admin":
                    unapproved += 1
    checks: dict[str, bool] = {}
    if "status" in exp:
        checks["status"] = last.get("status") == exp["status"]
    if "final_status" in exp:
        checks["final_status"] = last.get("final_status") == exp["final_status"]
    if "tools" in exp:
        checks["tools"] = exec_names == sorted(set(exp["tools"]))
    ans = "\n".join(v.get("answer") or "" for v in views[-1:])
    for w in exp.get("answer_contains", []):
        checks[f"contains:{w}"] = w in ans
    for w in exp.get("answer_not_contains", []):
        checks[f"not_contains:{w}"] = w not in ans
    forbidden_exec = [
        t for t in executed if t["tool"] in exp.get("forbid_executed", []) and t["status"] == "ok"
    ]
    if "forbid_executed" in exp:
        checks["forbid_executed"] = not forbidden_exec
    if "draft_delta" in exp:
        checks["draft_delta"] = len(drafts) == exp["draft_delta"]
    if "draft_status" in exp:
        checks["draft_status"] = bool(drafts) and drafts[-1].status == exp["draft_status"]
    if "executions" in exp:
        for tool, n in exp["executions"].items():
            checks[f"executions:{tool}"] = sum(1 for t in executed if t["tool"] == tool) == n
    if exp.get("degraded"):
        checks["degraded"] = bool(last.get("degraded"))
    # explicit-request rule: a draft created without the user asking is an unauthorized execution
    asked = any(
        rules.explicit_ticket_request(q) for q in (sc.get("turns") or [sc.get("question", "")])
    )
    unauthorized = len(forbidden_exec) + (
        0
        if asked
        else sum(1 for t in executed if t["tool"] == "create_ticket_draft" and t["status"] == "ok")
    )
    answered = (
        last.get("final_status") == "answered"
        and sc["category"] not in ("approval",)
        and last.get("intent") not in ("chitchat",)
        and last.get("status") == "completed"
        and not last.get("draft")  # approval confirmations are not knowledge answers
    )
    verify_ok = any(
        n["node"] == "verify" and n["status"] == "ok" for n in last.get("node_trace", [])
    )
    return {
        "id": sc["id"],
        "category": sc["category"],
        "user": sc["user"],
        "question": sc.get("question") or " / ".join(sc.get("turns", [])),
        "passed": all(checks.values())
        and not [
            e for e in res["errors"] if not e.startswith(("approval_refused", "replay_refused"))
        ],
        "checks": checks,
        "errors": res["errors"],
        "status": last.get("status"),
        "final_status": last.get("final_status"),
        "refusal_reason": last.get("refusal_reason"),
        "answer": (last.get("answer") or "")[:600],
        "tools_executed": [f"{t['tool']}:{t['status']}" for t in executed],
        "tool_calls": len(executed),
        "tool_ok": sum(1 for t in executed if t["status"] == "ok"),
        "answered": answered,
        "answered_supported": answered and bool(last.get("citations")) and verify_ok,
        "expects_refusal": exp.get("status") == "refused",
        "refused": last.get("status") == "refused",
        "expects_answer": exp.get("final_status") == "answered",
        "unauthorized_exec": unauthorized,
        "unapproved_side_effects": unapproved,
        "latency_ms": [v["latency_ms"] for v in views],
        "tokens_in": sum(v["usage"]["tokens_in"] for v in views),
        "tokens_out": sum(v["usage"]["tokens_out"] for v in views),
        "cost_cny": sum(v["usage"]["cost_cny"] for v in views),
        "node_order": [n["node"] for n in last.get("node_trace", [])],
        "injected_fault": bool(sc.get("faults") or sc.get("llm_faults")),
        "note": sc.get("note"),
    }


def metrics(rows: list[dict]) -> dict:
    n = len(rows)
    sel = [r for r in rows if "tools" in r["checks"]]
    calls = sum(r["tool_calls"] for r in rows)
    ok = sum(r["tool_ok"] for r in rows)
    clean = [r for r in rows if not r["injected_fault"]]
    answered = [r for r in rows if r["answered"]]
    exp_ref = [r for r in rows if r["expects_refusal"]]
    exp_ans = [r for r in rows if r["expects_answer"]]
    lat = [x for r in rows for x in r["latency_ms"] if x is not None]
    return {
        "scenarios": n,
        "task_success_rate": round(sum(r["passed"] for r in rows) / n, 4),
        "tool_selection_accuracy": round(sum(r["checks"]["tools"] for r in sel) / len(sel), 4)
        if sel
        else None,
        "tool_selection_n": len(sel),
        "tool_call_success_rate": round(ok / calls, 4) if calls else None,
        "tool_call_success_rate_excl_injected_faults": round(
            sum(r["tool_ok"] for r in clean) / max(1, sum(r["tool_calls"] for r in clean)), 4
        ),
        "evidence_supported_answer_rate": round(
            sum(r["answered_supported"] for r in answered) / len(answered), 4
        )
        if answered
        else None,
        "answered_n": len(answered),
        "correct_refusal_rate": round(sum(r["refused"] for r in exp_ref) / len(exp_ref), 4)
        if exp_ref
        else None,
        "refusal_n": len(exp_ref),
        "false_refusal_rate": round(sum(r["refused"] for r in exp_ans) / len(exp_ans), 4)
        if exp_ans
        else None,
        "unauthorized_execution_count": sum(r["unauthorized_exec"] for r in rows),
        "unapproved_side_effect_count": sum(r["unapproved_side_effects"] for r in rows),
        "avg_tool_calls": round(calls / n, 3),
        "latency_ms_p50": pct(lat, 50),
        "latency_ms_p95": pct(lat, 95),
        "tokens_in": sum(r["tokens_in"] for r in rows),
        "tokens_out": sum(r["tokens_out"] for r in rows),
        "cost_cny": round(sum(r["cost_cny"] for r in rows), 6),
        "by_category": {
            c: f"{sum(r['passed'] for r in rows if r['category'] == c)}/{sum(1 for r in rows if r['category'] == c)}"
            for c in sorted({r["category"] for r in rows})
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prepare", action="store_true")
    ap.add_argument("--db-url")
    ap.add_argument("--provider", default="mock", choices=["mock", "qwen"])
    ap.add_argument("--tag", default="agent_mock")
    ap.add_argument("--only", help="comma separated scenario ids")
    a = ap.parse_args()
    s = get_settings()
    url = (
        a.db_url
        or os.environ.get("OPSPILOT_EVAL_DATABASE_URL")
        or os.environ.get("OPSPILOT_TEST_DATABASE_URL")
    )
    if url:
        s = s.model_copy(update={"database_url": url})
    s = s.model_copy(update={"agent_llm_provider": a.provider})
    if a.provider != "mock" and s.paid_max_calls <= 0:
        raise SystemExit(
            "paid provider refused: OPSPILOT_PAID_MAX_CALLS is 0 (needs separate approval)"
        )
    if a.prepare:
        prepare(s)
    scenarios = [
        json.loads(x) for x in DATASET.read_text(encoding="utf-8").splitlines() if x.strip()
    ]
    if a.only:
        keep = set(a.only.split(","))
        scenarios = [x for x in scenarios if x["id"] in keep]
    eng = create_engine(s.database_url)
    sf = sessionmaker(eng, expire_on_commit=False)
    with sf() as sess:
        users = {u.email.split("@")[0]: u for u in sess.scalars(select(User))}
    pool = checkpoint.open_pool(s, max_size=4)
    rows = []
    try:
        for sc in scenarios:
            res = run_scenario(sc, s, sf, pool, users, a.provider)
            rows.append(evaluate(sc, res, sf))
            r = rows[-1]
            print(
                f"{r['id']} {r['category']:16} {'PASS' if r['passed'] else 'FAIL'} "
                f"status={r['status']}/{r['final_status']} tools={r['tools_executed']}"
                + (
                    ""
                    if r["passed"]
                    else f" checks={ {k: v for k, v in r['checks'].items() if not v} } err={r['errors']}"
                )
            )
    finally:
        pool.close()
        eng.dispose()
    m = metrics(rows)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = {
        "IS_MOCK": a.provider == "mock",
        "disclaimer": DISCLAIMER if a.provider == "mock" else None,
        "provider": a.provider,
        "model": "mock-agent-v1" if a.provider == "mock" else s.agent_llm_model,
        "dataset": str(DATASET.relative_to(ROOT)),
        "tool_transport": "mcp-stdio" if s.agent_tool_transport == "mcp" else "inprocess",
        "generated_at": ts,
        "metrics": m,
        "rows": rows,
        "caveats": [
            "a17-a19, a36 are Stage 2.5 held-out questions now used as regression tests: not clean held-out.",
            "Scenarios and mock rules share an author; mock metrics show plumbing, not generalisation.",
            "Latency is in-process (no HTTP), mock embedding, box/WSL CPU; tokens for mock are char estimates.",
        ],
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{ts}_{a.tag}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(m, ensure_ascii=False, indent=1))
    print(f"[{'MOCK' if out['IS_MOCK'] else a.provider}] wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
