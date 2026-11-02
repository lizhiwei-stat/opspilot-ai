"""Evidence-sufficiency assessment (the node that decides answer / tools / clarify / refuse).

Stage 2.5 showed a similarity threshold cannot separate "on-topic but unanswerable" questions
(held-out h029/h032/h033/h035/h036 scored as high as answerable ones, even with a
cross-encoder). This node therefore checks *what the question asks for* against *what the
evidence actually says*, deterministically:

  1. slot analysis    - specific-fact questions (value / entity / choice / yes-no) have a
                        head term (商户号, 云厂商, 年假, 调休 ...)
  2. corpus presence  - the head term must exist somewhere in the knowledge THIS user may
                        read (ACL-scoped SQL); absent => the KB cannot answer => refuse
  3. evidence presence- present in the KB but not in the retrieved evidence => need_tools
                        (targeted search_knowledge), not a guess
  4. value slot       - for value questions the sentence naming the head must carry a value
  5. contradictions   - the same head with different values/units in different sources =>
                        ask for clarification and show both sources
  6. anchors          - Latin product names in the question (Java, Redis ...) must occur
  7. retrieval gate   - Stage 2.5 QA gate (lexical/reranker) for open questions
  8. optional LLM judge (real mode only) may ADD missing-info / contradiction findings or
     veto; any confidence number it reports is ignored (never treated as a probability)
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from opspilot.agent.rules import fact_question, head_parts
from opspilot.agent.state import Assessment

_SENT = re.compile(r"[^。！？!?\n；;]+[。！？!?；;]?")
_ANCHOR = re.compile(r"[A-Za-z][A-Za-z0-9.+#]{2,}")
_ANCHOR_STOP = frozenset({"api", "http", "https", "sql", "url", "json", "id", "token", "key"})
_VALUE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(ms|毫秒|秒|分钟|小时|天|周|个月|月|年|次|%|个|台|步|元|位)"
)
_ANY_NUM = re.compile(r"\d|[一二两三四五六七八九十百千]+\s*(秒|分钟|小时|天|周|个月|次|步|位)")

Presence = Callable[[list[str]], bool]  # ACL-scoped "do these parts co-occur in one chunk"


def _sentences(text: str) -> list[str]:
    return [m.group().strip() for m in _SENT.finditer(text) if m.group().strip()]


def _has_parts(text: str, parts: list[str]) -> bool:
    t = text.lower()
    return all(p.lower() in t for p in parts)


def assess(
    *,
    question: str,
    intent: str,
    entities: dict[str, list[str]],
    evidence: list[dict[str, Any]],
    tool_records: list[dict[str, Any]],
    qa_gate: dict[str, Any],
    presence: Presence,
    tools_available: list[str],
    judge: dict[str, Any] | None = None,
) -> Assessment:
    kb = [e for e in evidence if e["source"] == "kb"]
    tool_ok = [r for r in tool_records if r.get("status") == "ok"]
    tools_done = {r["tool"] for r in tool_records}
    checks: dict[str, Any] = {"qa_gate": qa_gate, "kb_evidence": len(kb), "tool_ok": len(tool_ok)}
    reasons: list[str] = []
    missing: list[str] = []
    contradictions: list[str] = []
    need: list[str] = []

    def out(decision: str, answers: bool) -> Assessment:
        return Assessment(
            decision=decision,  # type: ignore[arg-type]
            answers_question=answers,
            missing=missing,
            contradictions=contradictions,
            need_tools=need,
            reasons=reasons,
            checks=checks,
        )

    # --- tools the plan requires but that have not run yet (troubleshooting, tickets)
    wanted = []
    if entities.get("error_codes") and "analyze_error_code" not in tools_done:
        wanted.append("analyze_error_code")
    if intent == "ticket_query" and "get_ticket" not in tools_done:
        wanted.append("get_ticket")
    wanted = [t for t in wanted if t in tools_available]

    # --- 6. anchors (Latin names must occur in evidence)
    ev_text = "\n".join(e["text"] for e in kb + [e for e in evidence if e["source"] == "tool"])
    anchors = [
        w
        for w in _ANCHOR.findall(question)
        if w.lower() not in _ANCHOR_STOP
        and not any(c.isdigit() for c in w)
        and not re.fullmatch(r"[A-Z]{2,6}", w)  # P1/ORD-like codes handled elsewhere
    ]
    missing_anchor = [a for a in anchors if a.lower() not in ev_text.lower() and not presence([a])]
    checks["anchors"] = {"asked": anchors, "missing": missing_anchor}
    if missing_anchor and not wanted:
        missing += [f"知识库中没有关于「{a}」的信息" for a in missing_anchor]
        reasons.append("unmatched_anchor:" + ",".join(missing_anchor))
        return out("refuse", False)

    # --- 1-5. specific-fact questions
    fq = fact_question(question)
    checks["fact_question"] = fq
    if fq:
        heads: list[str] = list(fq["heads"])  # type: ignore[arg-type]
        head_info = []
        for h in heads:
            parts = head_parts(h)
            in_corpus = presence(parts) if parts else True
            sents = [s for e in kb for s in _sentences(e["text"]) if parts and _has_parts(s, parts)]
            head_info.append(
                {"head": h, "parts": parts, "in_corpus": in_corpus, "sentences": len(sents)}
            )
            if not in_corpus:
                missing.append(f"你可访问的知识库中没有关于「{h}」的信息")
            elif not sents:
                missing.append(f"检索结果未覆盖「{h}」")
        checks["heads"] = head_info
        if fq["type"] == "choice":
            # one of the options must be named in the evidence; else unanswerable
            if not any(i["in_corpus"] for i in head_info):
                reasons.append("choice_options_absent")
                return out("refuse", False)
            missing[:] = [m for m in missing if "没有关于" not in m]
        elif any(not i["in_corpus"] for i in head_info):
            reasons.append("head_term_absent_from_accessible_kb")
            return out("refuse", False)
        if any(i["in_corpus"] and i["sentences"] == 0 for i in head_info):
            if "search_knowledge" in tools_available and "search_knowledge" not in tools_done:
                need.append("search_knowledge")
                reasons.append("head_term_in_kb_but_not_retrieved")
                return out("need_tools", False)
            reasons.append("head_term_not_in_evidence_after_search")
            return out("refuse", False)
        missing[:] = []
        # value slot + contradictions on the sentences that name the head
        if fq["type"] == "value":
            head_sents = [
                (e["title"], s)
                for e in kb
                for s in _sentences(e["text"])
                for i in head_info
                if i["parts"] and _has_parts(s, i["parts"])
            ]
            if not any(_ANY_NUM.search(s) for _, s in head_sents):
                missing.append("证据提到了该对象，但没有给出具体数值")
                reasons.append("value_slot_empty")
                return out("refuse", False)
            by_unit: dict[str, dict[str, set[str]]] = {}
            for title, s in head_sents:
                for num, unit in _VALUE.findall(s):
                    by_unit.setdefault(unit, {}).setdefault(num, set()).add(title)
            for unit, vals in by_unit.items():
                docs = {d for ds in vals.values() for d in ds}
                if len(vals) > 1 and len(docs) > 1:
                    contradictions.append(
                        f"不同来源给出不同数值（{unit}）："
                        + "；".join(
                            f"{v}{unit}←{'/'.join(sorted(d))}" for v, d in sorted(vals.items())
                        )
                    )
            checks["values"] = {
                u: {v: sorted(d) for v, d in vs.items()} for u, vs in by_unit.items()
            }
        if contradictions:
            reasons.append("contradictory_evidence")
            return out("ask_clarification", False)
        reasons.append("fact_slot_satisfied")
    else:
        # --- 7. open / procedural questions: retrieval gate or tool evidence
        if not qa_gate.get("ok") and not tool_ok:
            if wanted:
                need += wanted
                reasons.append("tools_required")
                return out("need_tools", False)
            if (
                "search_knowledge" in tools_available
                and "search_knowledge" not in tools_done
                and kb
            ):
                need.append("search_knowledge")
                reasons.append("weak_retrieval_retry_search")
                return out("need_tools", False)
            reasons.append(str(qa_gate.get("reason") or "no_evidence"))
            return out("refuse", False)
        reasons.append("retrieval_gate_ok" if qa_gate.get("ok") else "tool_evidence")

    if wanted:
        need += wanted
        reasons.append("tools_required")
        return out("need_tools", False)

    # --- 8. optional real-model judge: can only make the decision stricter
    if judge is not None:
        checks["judge"] = {k: v for k, v in judge.items() if k != "confidence"}
        contradictions += [str(c) for c in judge.get("contradictions", [])][:3]
        if judge.get("answers_question") is False:
            missing += [str(m) for m in judge.get("missing", [])][:3]
            reasons.append("judge_veto")
            return out("refuse", False)
        if contradictions:
            reasons.append("judge_contradiction")
            return out("ask_clarification", False)
    return out("sufficient", True)
