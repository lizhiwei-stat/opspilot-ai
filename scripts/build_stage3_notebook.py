"""Build notebooks/langgraph_agent_walkthrough.ipynb (execute with nbconvert afterwards).
Mock only: no paid calls. Uses the *_test database (prepared like the agent eval)."""
import nbformat as nbf
from pathlib import Path

C = []
md = lambda s: C.append(nbf.v4.new_markdown_cell(s))
code = lambda s: C.append(nbf.v4.new_code_cell(s))

md("""# LangGraph 企业 Agent 演示（阶段 3，Mock）
本 Notebook 演示：状态机结构、路由决策、工具调用（含故障与降级）、拒答、PostgreSQL 检查点持久化、多轮记忆、人工审批中断与恢复。
> ⚠️ 全程使用 **Mock 智能体**（规则 + 抽取式生成），**不调用任何大模型、无付费请求**。结果只说明流程与守卫是否按设计工作。""")
code('''import os, sys, json, re, uuid
from pathlib import Path
ROOT = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()
sys.path[:0] = [str(ROOT / "backend" / "src"), str(ROOT / "evals"), str(ROOT)]
os.chdir(ROOT)
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")  # 只读 OpsPilot 自己的 .env；不打印任何密钥
base = os.environ.get("OPSPILOT_TEST_DATABASE_URL") or re.sub(r"/[^/]+$", "/opspilot_test", os.environ["OPSPILOT_DATABASE_URL"])
assert base.rsplit("/", 1)[1].endswith("_test"), "只允许在 *_test 库上运行"
from opspilot.core.config import Settings
settings = Settings(database_url=base, agent_llm_provider="mock", agent_checkpoint_auto_setup=False)
assert settings.agent_llm_provider == "mock" and settings.paid_max_calls == 0
print("agent provider:", settings.agent_llm_provider, "| paid_max_calls:", settings.paid_max_calls, "| db:", base.rsplit("/", 1)[1])''')
md("## 1. 准备：迁移、种子数据、语料（与 Agent 评测相同，幂等）")
code('''import run_agent_eval as ev
ev.prepare(settings)
from opspilot.agent import checkpoint
checkpoint.setup(settings)
print("prepared")''')
md("## 2. 状态机结构\n节点通过 `Command(goto=...)` 路由，下图是 LangGraph 自动导出的结构（虚线 = 条件路由）；注释版见 `docs/diagrams/stage3_agent_graph.png`。")
code('''from opspilot.agent.graph import AgentDeps, build_graph
from opspilot.agent.llm import MockAgentLLM
from opspilot.llm.mock import MockEmbedding
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
sf = sessionmaker(create_engine(base), expire_on_commit=False)
g = build_graph(AgentDeps(settings, sf, MockEmbedding(), MockAgentLLM()))
gg = g.get_graph()
print("节点:", [n for n in gg.nodes if not n.startswith("__")])
print(gg.draw_mermaid()[:1500])''')
md("## 3. AgentService + PostgresSaver")
code('''from langgraph.checkpoint.postgres import PostgresSaver
from opspilot.agent.service import AgentService
from opspilot.db.models import User
from sqlalchemy import select
pool = checkpoint.open_pool(settings)
svc = AgentService(settings, sf, MockEmbedding(), PostgresSaver(pool), llm=MockAgentLLM())
with sf() as s:
    U = {u.email.split("@")[0]: u for u in s.scalars(select(User))}
def show(v):
    print(f"status={v['status']} final={v.get('final_status')} mock={v.get('is_mock')}")
    print("trace:", " → ".join(n["node"] for n in v.get("node_trace") or []))
    for t in v.get("tools") or []:
        print(f"  tool {t.get('tool') or t.get('name')}: {t.get('status')} attempts={t.get('attempts')} dedup={t.get('deduplicated')}")
    if v.get("degraded"): print("degraded:", v["degraded"])
    print("answer:", (v.get("answer") or "")[:300].replace("\\n", " "))
    if v.get("refusal_reason"): print("refusal_reason:", v["refusal_reason"])
print(sorted(U))''')
md("## 4. 路由：知识库问答 / 多工具 ReAct")
code('''show(svc.start(U["alice"], "支付网关默认超时时间是多少"))''')
code('''show(svc.start(U["alice"], "ORD-5003 是什么错误，怎么处理"))''')
md("## 5. 证据不足 → 拒答（不编造）")
code('''show(svc.start(U["alice"], "支付网关的商户号是多少"))''')
md("## 6. 权限：普通用户查看他人工单 → 服务端 ACL 返回 not_found（不泄露存在性）→ 拒答")
code('''show(svc.start(U["alice"], "查一下工单 TCK-0002 的状态"))''')
md("## 7. 工具故障与降级（故障注入，仅测试/演示）")
code('''fs = settings.model_copy(update={"agent_fault_injection": True})
svc_f = AgentService(fs, sf, MockEmbedding(), PostgresSaver(pool), llm=MockAgentLLM(), faults={"query_mock_logs": "timeout"})
show(svc_f.start(U["alice"], "订单服务最近的错误日志里有什么"))''')
md("## 8. 多轮记忆（同一 thread，追问用代词）")
code('''v1 = svc.start(U["alice"], "查一下工单 TCK-0001 的状态"); show(v1)
v2 = svc.start(U["alice"], "它现在是什么优先级", v1["thread_id"]); show(v2)
print("same thread:", v1["thread_id"] == v2["thread_id"])''')
md("## 9. 人工审批：interrupt → 管理员批准 → 从检查点恢复（模拟进程重启）")
code('''v = svc.start(U["alice"], "支付网关夜间超时，帮我创建一个工单，P1"); show(v)
rid = uuid.UUID(v["run_id"])
try:
    svc.approve(rid, U["alice"], "approved")
except Exception as e:
    print("alice 审批 →", type(e).__name__)
# 新建 service + 新连接池，相当于后端重启后只依赖 PostgreSQL 中的检查点
pool.close(); pool = checkpoint.open_pool(settings)
svc2 = AgentService(settings, sf, MockEmbedding(), PostgresSaver(pool), llm=MockAgentLLM())
show(svc2.approve(rid, U["admin"], "approved"))
try:
    svc2.approve(rid, U["admin"], "approved")
except Exception as e:
    print("重复审批 →", type(e).__name__)''')
md("## 10. 检查点历史（PostgreSQL `langgraph` schema）")
code('''h = svc2.history(rid, U["admin"])
print("checkpoints in thread:", len(h))
import psycopg
with psycopg.connect(checkpoint.conninfo(settings)) as c:
    print(c.execute("select table_name from information_schema.tables where table_schema='langgraph' order by 1").fetchall())
pool.close()''')
md("## 小结\n- 所有输出均为 Mock；真实 Qwen 测试见 `docs/STAGE3_QWEN_TEST_PLAN.md`，需单独授权。")

nb = nbf.v4.new_notebook(cells=C, metadata={"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}})
out = Path(__file__).resolve().parents[1] / "notebooks" / "langgraph_agent_walkthrough.ipynb"
nbf.write(nb, out)
print(out)
