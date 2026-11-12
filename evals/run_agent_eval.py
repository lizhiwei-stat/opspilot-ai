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
    if sc.get("approval") and "approve" not in sc:
        sc = {**sc, "approve": sc["approval"].get("decision", "approved")}
    turns = list(sc.get("turns") or [sc["question"]])
    fu = sc.get("followup") if isinstance(sc.get("followup"), dict) else None
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
                seq = sc["approve"] if isinstance(sc["approve"], list) else [sc["approve"]]
                comments = sc.get("approve_comments") or []
                for i, dec in enumerate(seq):
                    comment = comments[i] if i < len(comments) else ""
                    views[-1] = svc.approve(rid, users["admin"], dec, comment)
                if sc.get("replay"):
                    for fn in (
                        lambda rid=rid: svc.approve(rid, users["admin"], "approved"),
                        lambda rid=rid: svc.resume(rid, user),
                    ):
                        try:
                            fn()
                        except AppError as exc:
                            errors.append(f"replay_refused:{type(exc).__name__}")
    if fu and fu.get("question") and thread:
        last_st = views[-1]["status"] if views else None
        if last_st != "awaiting_approval":
            try:
                v = svc.start(user, fu["question"], thread)
                views.append(v)
            except AppError as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
    wall = (time.perf_counter() - t0) * 1000
    return {"views": views, "errors": errors, "wall_ms": wall}


def _apply_expect_after(sc: dict, exp: dict) -> dict:
    """When the runner auto-applies approval, honor dataset approval.expect_after."""
    exp = dict(exp)
    after = (sc.get("approval") or {}).get("expect_after") or {}
    # run_scenario may set approve on a shallow copy; the dataset flag is approval.decision
    approved = sc.get("approve") or (sc.get("approval") or {}).get("decision")
    if approved and after:
        if "status_in" in after:
            exp.pop("status", None)
            exp["status_in"] = after["status_in"]
        if "final_status_in" in after:
            exp.pop("final_status", None)
            exp["final_status_in"] = after["final_status_in"]
        for k in ("draft_status", "draft_delta"):
            if k in after:
                exp[k] = after[k]
    return exp


def _checks_against(
    exp: dict, view: dict, views: list[dict], drafts: list, executed: list
) -> dict[str, bool]:
    exec_names = sorted({t["tool"] for t in executed})
    checks: dict[str, bool] = {}
    if "status" in exp:
        checks["status"] = view.get("status") == exp["status"]
    if "status_in" in exp:
        checks["status_in"] = view.get("status") in exp["status_in"]
    if "final_status" in exp:
        checks["final_status"] = view.get("final_status") == exp["final_status"]
    if "final_status_in" in exp:
        checks["final_status_in"] = view.get("final_status") in exp["final_status_in"]
    if "tools" in exp:
        checks["tools"] = exec_names == sorted(set(exp["tools"]))
    if "tools_any" in exp:
        want = set(exp["tools_any"])
        checks["tools_any"] = bool(want & set(exec_names)) or (not want and not exec_names)
    ans = view.get("answer") or ""
    for w in exp.get("answer_contains", []):
        checks[f"contains:{w}"] = w in ans
    if exp.get("answer_contains_any"):
        checks["contains_any"] = any(w in ans for w in exp["answer_contains_any"])
    for w in exp.get("answer_not_contains", []):
        checks[f"not_contains:{w}"] = w not in ans
    for w in exp.get("answer_forbids", []):
        checks[f"forbids:{w}"] = w.lower() not in ans.lower()
    if "min_citations" in exp:
        cites = view.get("citations") or []
        checks["min_citations"] = len(cites) >= int(exp["min_citations"])
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
        checks["degraded"] = bool(view.get("degraded"))
    return checks


def evaluate(sc: dict, res: dict, sf: Any) -> dict:
    exp = _apply_expect_after(sc, sc.get("expect", {}))
    views = res["views"]
    last = views[-1] if views else {}
    calls = [t for v in views for t in v["tools"]]
    executed = [t for t in calls if t["status"] != "denied" and not t["deduplicated"]]
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
    fu = sc.get("followup") if isinstance(sc.get("followup"), dict) else None
    first = views[0] if views else {}
    if fu and fu.get("expect") and len(views) >= 2:
        t0 = _checks_against(
            sc.get("expect") or {},
            first,
            views[:1],
            drafts,
            [
                t
                for t in first.get("tools") or []
                if t.get("status") != "denied" and not t.get("deduplicated")
            ],
        )
        t1 = _checks_against(fu["expect"], last, views, drafts, executed)
        checks = {**{f"u0_{k}": v for k, v in t0.items()}, **{f"u1_{k}": v for k, v in t1.items()}}
    else:
        checks = _checks_against(exp, last, views, drafts, executed)
    forbidden_exec = [
        t
        for t in executed
        if t["tool"] in (exp.get("forbid_executed") or []) and t["status"] == "ok"
    ]
    # explicit-request rule: a draft created without the user asking is an unauthorized execution
    asked_qs = list(sc.get("turns") or [sc.get("question", "")])
    if fu and fu.get("question"):
        asked_qs.append(fu["question"])
    asked = any(rules.explicit_ticket_request(q or "") for q in asked_qs)

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
        "taxonomy": sc.get("taxonomy") or sc["category"],
        "split": sc.get("split"),
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
        "latency_ms_p99": pct(lat, 99),
        "tokens_in": sum(r["tokens_in"] for r in rows),
        "tokens_out": sum(r["tokens_out"] for r in rows),
        "cost_cny": round(sum(r["cost_cny"] for r in rows), 6),
        "cost_cny_per_task": round(sum(r["cost_cny"] for r in rows) / n, 6) if n else None,
        "answer_groundedness": round(
            sum(r["answered_supported"] for r in answered) / len(answered), 4
        )
        if answered
        else None,
        "abstention_accuracy": round(sum(r["refused"] for r in exp_ref) / len(exp_ref), 4)
        if exp_ref
        else None,
        "by_category": {
            c: f"{sum(r['passed'] for r in rows if r['category'] == c)}/{sum(1 for r in rows if r['category'] == c)}"
            for c in sorted({r["category"] for r in rows})
        },
        "by_taxonomy": {
            c: f"{sum(r['passed'] for r in rows if r.get('taxonomy') == c)}/{sum(1 for r in rows if r.get('taxonomy') == c)}"
            for c in sorted({r.get("taxonomy") for r in rows if r.get("taxonomy")})
        },
        "failures": [
            {
                "id": r["id"],
                "taxonomy": r.get("taxonomy"),
                "category": r["category"],
                "failed_checks": [k for k, v in r["checks"].items() if not v],
                "errors": r["errors"],
                "status": r["status"],
                "root_cause_hint": (
                    "tool_selection"
                    if any("tools" in k for k, v in r["checks"].items() if not v)
                    else "content"
                    if any(
                        any(s in k for s in ("contains", "forbids", "not_contains"))
                        for k, v in r["checks"].items()
                        if not v
                    )
                    else "status"
                    if any("status" in k for k, v in r["checks"].items() if not v)
                    else "runtime_error"
                    if r["errors"]
                    else "other"
                ),
            }
            for r in rows
            if not r["passed"]
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prepare", action="store_true")
    ap.add_argument("--db-url")
    ap.add_argument("--provider", default="mock", choices=["mock", "qwen"])
    ap.add_argument("--tag", default="agent_mock")
    ap.add_argument("--only", help="comma separated scenario ids")
    ap.add_argument("--dataset", default=str(DATASET), help="jsonl dataset path")
    ap.add_argument("--split", default="all", choices=["all", "dev", "validation", "final_test"])
    ap.add_argument(
        "--verify-hash",
        action="store_true",
        help="verify dataset against evals/agent/FROZEN_v2.sha256",
    )
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
    ds_path = Path(a.dataset)
    if not ds_path.is_absolute():
        ds_path = ROOT / ds_path if not ds_path.exists() else ds_path
    if a.verify_hash and ds_path.name.startswith("agent_eval_v2"):
        import hashlib

        frozen = (ROOT / "evals/agent/FROZEN_v2.sha256").read_text().splitlines()
        digest = hashlib.sha256(ds_path.read_bytes()).hexdigest()
        ok = any(line.startswith(digest) and ds_path.name in line for line in frozen)
        if not ok:
            raise SystemExit(f"dataset hash mismatch for {ds_path.name}: {digest}")
    scenarios = [
        json.loads(x) for x in ds_path.read_text(encoding="utf-8").splitlines() if x.strip()
    ]
    if a.split != "all":
        scenarios = [x for x in scenarios if x.get("split") == a.split]
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
                f"{r['id']} {(r.get('taxonomy') or r['category']):16} {'PASS' if r['passed'] else 'FAIL'} "
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
        "dataset": str(ds_path.relative_to(ROOT))
        if str(ds_path).startswith(str(ROOT))
        else str(ds_path),
        "split": a.split,
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
