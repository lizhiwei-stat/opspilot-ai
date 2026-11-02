"""OpsPilot Ticket MCP server.

Tools:
  get_ticket, list_tickets   read-only, reporter/assignee/admin only
  create_ticket_draft        write: a PENDING draft only (idempotent per thread + args)
  update_ticket_status       write: executes an APPROVED draft exactly once (creates the ticket
                             or moves its status). Requires an approval row made by an admin;
                             nothing the model writes can satisfy that.
  revise_ticket_draft        write (backend-only, not offered to the model): new draft version
                             after a reviewer returned the previous one for revision
"""

from __future__ import annotations

from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from opspilot.agent.tools import (
    CreateTicketDraftIn,
    GetTicketIn,
    TicketDraftOut,
    TicketOut,
    _draft,
    _ticket,
)
from opspilot.mcp import impl
from opspilot.mcp.contracts import (
    ListTicketsIn,
    ReviseTicketDraftIn,
    TicketChangeOut,
    TicketListOut,
    UpdateTicketStatusIn,
)
from opspilot.mcp.runtime import Runtime, serve

RO = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
UUID = Field(pattern=r"^[0-9a-f-]{36}$")
SERVICE = Field(pattern=r"^[a-z][a-z0-9-]{2,40}$")


def build(rt: Runtime) -> MCPServer:
    mcp = MCPServer(
        "opspilot-ticket",
        instructions="工单系统。写操作只生成待审批草稿；只有管理员审批通过后才能执行。",
    )

    @mcp.tool(annotations=RO)
    def get_ticket(
        ticket_number: Annotated[str, Field(pattern=r"^TCK-\d{4}$")], ctx: Context
    ) -> TicketOut:
        """按工单号查询工单（仅本人提交/负责或管理员可见；无权限与不存在返回同样错误）。"""
        return rt.run_tool(ctx, "get_ticket", GetTicketIn(ticket_number=ticket_number), _ticket)  # type: ignore[return-value]

    @mcp.tool(annotations=RO)
    def list_tickets(
        ctx: Context,
        status: Annotated[
            str | None, Field(pattern=r"^(open|in_progress|resolved|closed)$")
        ] = None,
        service: Annotated[str | None, SERVICE] = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 10,
    ) -> TicketListOut:
        """列出当前用户可见的工单。"""
        return rt.run_tool(
            ctx,
            "list_tickets",
            ListTicketsIn(status=status, service=service, limit=limit),
            impl.list_tickets,
        )  # type: ignore[return-value]

    @mcp.tool(annotations=WRITE)
    def create_ticket_draft(
        title: Annotated[str, Field(min_length=4, max_length=120)],
        description: Annotated[str, Field(min_length=4, max_length=2000)],
        ctx: Context,
        priority: Annotated[str, Field(pattern=r"^P[1-4]$")] = "P3",
        service: Annotated[str | None, SERVICE] = None,
        category: Annotated[str, Field(pattern=r"^(incident|request|question)$")] = "incident",
    ) -> TicketDraftOut:
        """生成待审批工单草稿（不会创建工单）。同一会话相同内容重复调用返回同一草稿。"""
        a = CreateTicketDraftIn(
            title=title,
            description=description,
            priority=priority,
            service=service,
            category=category,
        )
        return rt.run_tool(ctx, "create_ticket_draft", a, _draft)  # type: ignore[return-value]

    @mcp.tool(annotations=WRITE)
    def update_ticket_status(
        draft_id: Annotated[str, UUID], approval_id: Annotated[str, UUID], ctx: Context
    ) -> TicketChangeOut:
        """执行一条已由管理员审批通过的草稿（新建工单或状态流转），每个审批最多执行一次。"""
        return rt.run_tool(
            ctx,
            "update_ticket_status",
            UpdateTicketStatusIn(draft_id=draft_id, approval_id=approval_id),
            impl.update_ticket_status,
        )  # type: ignore[return-value]

    @mcp.tool(annotations=WRITE)
    def revise_ticket_draft(
        parent_draft_id: Annotated[str, UUID],
        title: Annotated[str, Field(min_length=4, max_length=120)],
        description: Annotated[str, Field(min_length=4, max_length=2000)],
        ctx: Context,
        priority: Annotated[str, Field(pattern=r"^P[1-4]$")] = "P3",
    ) -> TicketDraftOut:
        """按审批人的退回意见生成草稿的新版本（仅后端在“退回修改”后调用）。"""
        a = ReviseTicketDraftIn(
            parent_draft_id=parent_draft_id, title=title, description=description, priority=priority
        )
        return rt.run_tool(ctx, "revise_ticket_draft", a, impl.revise_ticket_draft)  # type: ignore[return-value]

    return mcp


if __name__ == "__main__":
    serve(build, "ticket")
