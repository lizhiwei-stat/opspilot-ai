# ruff: noqa: E501, E731
"""Build notebooks/mcp_agent_workflow.ipynb (execute with nbconvert afterwards).
Mock model only, no paid calls. Real MCP servers (stdio subprocesses). *_test database only."""

from pathlib import Path

import nbformat as nbf

C = []
md = lambda s: C.append(nbf.v4.new_markdown_cell(s))
code = lambda s: C.append(nbf.v4.new_code_cell(s))

md("""# MCP 工具接入 + LangGraph 审批工作流（阶段 4，Mock 模型）
本 Notebook 用**真实 MCP 协议**（官方 Python SDK `mcp 2.3`，stdio 子进程）演示：
1. 三个 MCP Server（knowledge / ticket / diagnostics）的工具发现（`tools/list`）与调用（`tools/call`）；
2. 身份带外注入：调用者令牌放在 `_meta`，绑定 server、tool、参数哈希、jti 与 30 秒 TTL；缺失、篡改、重放都会被服务端拒绝；
3. 场景「ORD-5003 错误，查文档和日志，分析并创建待审核工单」：理解 → RAG → 日志 → 错误码关联 → 方案 → 草稿 → 人工审批（interrupt）→ 业务操作（Ticket MCP `update_ticket_status`）；
4. 退回修改 → 第 2 版 → 批准 → 工单 + 状态流转事件；重放去重；
5. 审计记录（已脱敏）。
> ⚠️ 全程 **Mock 智能体**，不调用任何大模型，无付费请求；只验证协议、流程与安全控制。""")
code("""import os, sys, json, re, uuid, asyncio, secrets
from pathlib import Path
ROOT = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()
sys.path[:0] = [str(ROOT / "backend" / "src"), str(ROOT / "evals"), str(ROOT)]
os.chdir(ROOT)
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")  # 只读 OpsPilot 自己的 .env；不打印任何密钥
base = os.environ.get("OPSPILOT_TEST_DATABASE_URL") or re.sub(r"/[^/]+$", "/opspilot_test", os.environ["OPSPILOT_DATABASE_URL"])
assert base.rsplit("/", 1)[1].endswith("_test"), "只允许在 *_test 库上运行"
os.environ.setdefault("OPSPILOT_JWT_SECRET", secrets.token_hex(32))  # 本地演示用随机值
from opspilot.core.config import Settings
settings = Settings(database_url=base, agent_llm_provider="mock", agent_checkpoint_auto_setup=False, agent_tool_transport="mcp")
assert settings.agent_llm_provider == "mock" and settings.paid_max_calls == 0
import mcp, langgraph, importlib.metadata as md_
print("mcp", md_.version("mcp"), "| langgraph", md_.version("langgraph"), "| transport:", settings.agent_tool_transport, "| db:", base.rsplit("/", 1)[1])""")
md("## 1. 准备：迁移、种子数据、语料（与 Agent 评测相同，幂等）")
code("""import run_agent_eval as ev
ev.prepare(settings)
from opspilot.agent import checkpoint
checkpoint.setup(settings)
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from opspilot.db.models import User
sf = sessionmaker(create_engine(base), expire_on_commit=False)
with sf() as s:
    U = {u.email.split("@")[0]: u for u in s.scalars(select(User))}
print(sorted(U))""")
md("## 2. 工具发现：MCPHub 启动三个 stdio Server，校验服务器名、工具白名单与输入 schema")
code("""import time
from opspilot.mcp.client import MCPHub, CallerCtx, child_env
hub = MCPHub(settings, session_factory=sf)
t0 = time.perf_counter(); d = hub.discover(); print(f"discovery {time.perf_counter()-t0:.2f}s")
for name, info in d.items():
    print(f"[{name}] protocol={info['protocol']} ignored={info['ignored']}")
    for t, m in info["tools"].items():
        print(f"   {t:<22} read_only={m['read_only']!s:<5} props={sorted(m['input_schema'].get('properties', {}))}")""")
md("子进程环境是最小权限的：没有 LLM Key、没有真实 JWT 密钥，`llm_provider` 被强制为 mock。")
code("""env = child_env(settings)
print({k: ("<set>" if "SECRET" in k else v) for k, v in env.items() if k in ("OPSPILOT_LLM_PROVIDER", "OPSPILOT_MCP_CALLER_SECRET", "OPSPILOT_LLM_API_KEY", "OPSPILOT_JWT_SECRET")})
print("OPSPILOT_LLM_API_KEY" in env)""")
md("## 3. 工具调用（以 alice 身份；身份来自服务端，不来自参数）")
code("""alice = CallerCtx(str(U["alice"].id), "user", "nb-thread", None, None)
for server, tool, args in [
    ("diagnostics", "analyze_error_code", {"code": "ORD-5003"}),
    ("diagnostics", "get_service_status", {"service": "order-service"}),
    ("knowledge", "search_knowledge", {"query": "ORD-5003 错误 处理", "top_k": 2}),
    ("ticket", "get_ticket", {"ticket_number": "TCK-0002"}),   # bob 的工单 → not_found（不泄露存在性）
    ("diagnostics", "query_system_logs", {"service": "../../etc"}),  # schema 拒绝
    ("diagnostics", "run_shell", {"cmd": "id"}),                  # 不在白名单
]:
    try:
        out = hub.call(server, tool, args, alice)
        print("OK  ", f"{server}.{tool}", json.dumps(out.model_dump(mode="json"), ensure_ascii=False)[:150])
    except Exception as e:
        print("ERR ", f"{server}.{tool}", type(e).__name__, str(e)[:100])""")
md("## 4. 原始 MCP 协议：直接用 SDK `Client` 连 diagnostics，演示调用者令牌的绑定与防重放")
code("""from mcp import Client, StdioServerParameters
from opspilot.mcp.identity import META_KEY, derive_caller_secret, mint
from opspilot.mcp.client import ROOT as MROOT
sec = derive_caller_secret(settings.jwt_secret.get_secret_value())
tok = lambda args, tool="analyze_error_code": mint(sec, server="diagnostics", tool=tool, args=args, user_id=str(U["alice"].id), role="user", thread_id="nb-raw", run_id=None, call_id=None, ttl_s=30)
async def raw():
    p = StdioServerParameters(command=sys.executable, args=["-m", "opspilot.mcp.servers.diagnostics"], env=child_env(settings), cwd=str(MROOT))
    async with Client(p) as c:
        print("server:", c.server_info.name, "protocol:", c.protocol_version)
        good = tok({"code": "ORD-5003"})
        for label, args, meta in [
            ("无令牌", {"code": "ORD-5003"}, None),
            ("参数被篡改", {"code": "PAY-4001"}, {META_KEY: good}),
            ("令牌签给别的工具", {"code": "ORD-5003"}, {META_KEY: tok({"code": "ORD-5003"}, "query_system_logs")}),
            ("正常", {"code": "ORD-5003"}, {META_KEY: good}),
            ("重放同一令牌", {"code": "ORD-5003"}, {META_KEY: good}),
        ]:
            r = await c.call_tool("analyze_error_code", args, meta=meta)
            txt = "".join(getattr(b, "text", "") for b in r.content)
            print(f"  {label:<10} is_error={r.is_error!s:<5} {txt[-60:] if r.is_error else json.dumps(r.structured_content, ensure_ascii=False)[:80]}")
await raw()""")
md("## 5. 端到端：ORD-5003 事件 → 草稿 → 审批（LangGraph interrupt，PostgresSaver 持久化）")
code("""from langgraph.checkpoint.postgres import PostgresSaver
from opspilot.agent.service import AgentService
from opspilot.agent.llm import MockAgentLLM
from opspilot.llm.mock import MockEmbedding
pool = checkpoint.open_pool(settings)
svc = AgentService(settings, sf, MockEmbedding(), PostgresSaver(pool), llm=MockAgentLLM())
Q = "ORD-5003 错误，查文档和日志，分析并创建待审核工单"
v = svc.start(U["alice"], Q)
print("intent:", v["intent"], "| status:", v["status"])
print("trace:", " → ".join(n["node"] for n in v["node_trace"]))
for t in v["tools"]:
    print(f"  {t['tool']:<22} {t['status']:<4} {t['duration_ms']:>4}ms  {(t['result'] or '')[:70]}")
print("draft:", v["draft"]["title"], v["draft"]["priority"], "v", v["draft"]["revision"])
print((v["answer"] or "")[:400])""")
md("普通用户不能审批；管理员的待审批队列来自数据库 + 检查点（重启后仍在）。")
code("""from opspilot.core.errors import ForbiddenError, ConflictError
try:
    svc.approve(uuid.UUID(v["run_id"]), U["alice"], "approved")
except ForbiddenError as e:
    print("alice approve ->", type(e).__name__, e)
q = svc.pending_approvals(U["admin"])
print("queue has run:", v["run_id"] in [x["run_id"] for x in q], "| size:", len(q))""")
md("## 6. 模拟后端重启：关闭 MCP 子进程与连接池，用新的 Service 恢复；先退回修改，再批准")
code("""from opspilot.mcp.client import close_all
pool.close(); close_all(); hub.close()
pool = checkpoint.open_pool(settings)
svc2 = AgentService(settings, sf, MockEmbedding(), PostgresSaver(pool), llm=MockAgentLLM())
rid = uuid.UUID(v["run_id"])
try:
    svc2.approve(rid, U["admin"], "returned", "")
except ConflictError as e:
    print("退回不带意见 ->", e)
back = svc2.approve(rid, U["admin"], "returned", "请补充影响范围与回滚方案")
print("after return:", back["status"], "| draft v", back["draft"]["revision"], "parent", back["draft"]["parent_draft_id"][:8])
done = svc2.approve(rid, U["admin"], "approved", "同意")
print("after approve:", done["status"], "| draft:", done["draft"]["status"])
print("draft_result:", json.dumps(done["draft_result"], ensure_ascii=False))
print("trace tail:", " → ".join(n["node"] for n in done["node_trace"][-6:]))""")
md("## 7. 工单与状态流转事件；重放业务操作 → 去重（一张工单）")
code("""from opspilot.db.models import Ticket, TicketEvent, McpToolAudit
from sqlalchemy import func
hub2 = MCPHub(settings, session_factory=sf)
adm = CallerCtx(str(U["admin"].id), "admin", v["thread_id"], None, None)
again = hub2.call("ticket", "update_ticket_status", {"draft_id": done["draft"]["draft_id"], "approval_id": done["draft_result"]["approval_id"]}, adm)
print("replay ->", again.model_dump())
with sf() as s:
    t = s.scalar(select(Ticket).where(Ticket.number == done["draft_result"]["ticket_number"]))
    n = s.scalar(select(func.count()).select_from(Ticket).where(Ticket.source_draft_id == t.source_draft_id))
    print(t.number, t.title, t.priority, t.status, "| tickets for this draft:", n)
    for e in s.scalars(select(TicketEvent).where(TicketEvent.ticket_id == t.id)):
        print(f"  event {e.from_status} -> {e.to_status} approver={str(e.approver_id)[:8]} approved_at={e.approved_at:%H:%M:%S}")""")
md("## 8. 审计（服务端 + 客户端两侧，参数只存摘要与哈希，无令牌）")
code("""with sf() as s:
    rows = s.scalars(select(McpToolAudit).where(McpToolAudit.thread_id == v["thread_id"]).order_by(McpToolAudit.created_at)).all()
    for a in rows:
        print(f"{a.side:<6} {a.server:<11} {a.tool:<22} {a.status:<6} {a.user_role or '-':<5} {a.duration_ms:>4}ms args={json.dumps(a.args_summary, ensure_ascii=False)[:60]}")
    blob = json.dumps([str(r.args_summary) + str(r.error) for r in rows])
    print("contains raw token?", "eyJ" in blob)
    print("denied/replay rows on raw demo thread:", sorted({r.status for r in s.scalars(select(McpToolAudit).where(McpToolAudit.tool == "analyze_error_code", McpToolAudit.side == "server"))}))
hub2.close(); close_all(); pool.close()""")
md("""## 小结
- 工具全部经真实 MCP 协议（stdio）发现与调用；客户端校验服务器名、工具白名单、输入/输出 schema 与输出大小。
- 身份、角色由服务端根据签名令牌从数据库重新加载；参数里的 user_id 会因 schema 不匹配被拒绝。
- 业务写操作只能在数据库里存在管理员审批记录时执行，唯一约束保证一次；重放返回 `deduplicated=True`。
- 以上全部为 Mock 模型结果，不代表真实大模型的推理质量。""")

nb = nbf.v4.new_notebook(
    cells=C, metadata={"kernelspec": {"name": "python3", "display_name": "Python 3"}}
)
out = Path(__file__).resolve().parents[1] / "notebooks" / "mcp_agent_workflow.ipynb"
nbf.write(nb, out)
print("wrote", out)
