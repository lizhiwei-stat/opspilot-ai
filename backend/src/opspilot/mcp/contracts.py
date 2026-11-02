"""Tool contracts shared by the MCP servers (what they implement) and the backend client
(what it accepts from them). The client validates outgoing arguments with the *In models and
incoming `structured_content` with the *Out models, so a compromised or buggy server cannot
smuggle unexpected shapes into the agent state.

SERVER_TOOLS is the allowlist: the client launches only these servers and exposes only these
tools from each, whatever a server advertises in tools/list.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from opspilot.agent.tools import (  # Stage 3 contracts, reused unchanged
    AnalyzeErrorCodeIn,
    CreateTicketDraftIn,
    ErrorCodeOut,
    GetTicketIn,
    KnowledgeHit,
    QueryLogsIn,
    QueryLogsOut,
    SearchKnowledgeIn,
    SearchKnowledgeOut,
    TicketDraftOut,
    TicketOut,
)

SLUG = r"^[a-z0-9][a-z0-9-]{1,63}$"


# ---------------------------------------------------------------- knowledge
class GetDocumentIn(BaseModel):
    document: str = Field(
        min_length=3,
        max_length=200,
        description="文档名（source_name），如 order-api-error-codes.md",
    )
    max_chunks: int = Field(default=6, ge=1, le=20)


class DocumentChunk(BaseModel):
    index: int
    locator: str
    text: str
    flagged: bool = False


class DocumentOut(BaseModel):
    document: str
    knowledge_base: str
    version: int
    total_chunks: int
    chunks: list[DocumentChunk]


class ListKnowledgeBasesIn(BaseModel):
    pass


class KnowledgeBaseItem(BaseModel):
    slug: str
    name: str
    visibility: str
    documents: int


class KnowledgeBasesOut(BaseModel):
    knowledge_bases: list[KnowledgeBaseItem]


# ---------------------------------------------------------------- ticket
class ListTicketsIn(BaseModel):
    status: str | None = Field(default=None, pattern=r"^(open|in_progress|resolved|closed)$")
    service: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{2,40}$")
    limit: int = Field(default=10, ge=1, le=50)


class TicketListOut(BaseModel):
    tickets: list[TicketOut]
    total: int


class UpdateTicketStatusIn(BaseModel):
    """Executes an APPROVED draft: action=create -> creates the ticket (status open);
    action=update -> moves the target ticket to the draft's target_status. The approval must
    exist in the DB, be 'approved' by an active admin, match the draft payload hash and be
    unexecuted. Nothing in the arguments (or in any model output) can stand in for it."""

    draft_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    approval_id: str = Field(pattern=r"^[0-9a-f-]{36}$")


class TicketChangeOut(BaseModel):
    ticket_number: str
    status: str
    from_status: str | None
    draft_status: str
    deduplicated: bool  # True when this approval had already been executed


class ReviseTicketDraftIn(BaseModel):
    """Not LLM-visible: used by the backend after a reviewer returns a draft for revision."""

    parent_draft_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    title: str = Field(min_length=4, max_length=120)
    description: str = Field(min_length=4, max_length=2000)
    priority: str = Field(default="P3", pattern=r"^P[1-4]$")


# ---------------------------------------------------------------- diagnostics
class GetServiceStatusIn(BaseModel):
    service: str = Field(pattern=r"^[a-z][a-z0-9-]{2,40}$")


class ServiceStatusOut(BaseModel):
    service: str
    status: str  # healthy | degraded | down (mock signal from recent logs)
    error_rate_5m: float
    recent_errors: int
    top_codes: dict[str, int]
    owner: str | None


SERVER_TOOLS: dict[str, dict[str, tuple[type[BaseModel], type[BaseModel]]]] = {
    "knowledge": {
        "search_knowledge": (SearchKnowledgeIn, SearchKnowledgeOut),
        "get_document": (GetDocumentIn, DocumentOut),
        "list_knowledge_bases": (ListKnowledgeBasesIn, KnowledgeBasesOut),
    },
    "ticket": {
        "get_ticket": (GetTicketIn, TicketOut),
        "list_tickets": (ListTicketsIn, TicketListOut),
        "create_ticket_draft": (CreateTicketDraftIn, TicketDraftOut),
        "update_ticket_status": (UpdateTicketStatusIn, TicketChangeOut),
        "revise_ticket_draft": (ReviseTicketDraftIn, TicketDraftOut),
    },
    "diagnostics": {
        "query_system_logs": (QueryLogsIn, QueryLogsOut),
        "get_service_status": (GetServiceStatusIn, ServiceStatusOut),
        "analyze_error_code": (AnalyzeErrorCodeIn, ErrorCodeOut),
    },
}
TOOL_SERVER = {t: srv for srv, tools in SERVER_TOOLS.items() for t in tools}
WRITE_TOOLS = frozenset({"create_ticket_draft", "update_ticket_status", "revise_ticket_draft"})

__all__ = [
    "SERVER_TOOLS",
    "TOOL_SERVER",
    "WRITE_TOOLS",
    "KnowledgeHit",
]
