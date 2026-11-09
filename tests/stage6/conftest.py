"""Stage 6 reuses Stage 5 API fixtures (real app + seeded agent test DB, mock LLM)."""

from tests.agent.conftest import agent_db  # noqa: F401
from tests.integration.conftest import migrated_db  # noqa: F401
from tests.stage5.conftest import api, login, parse_sse  # noqa: F401
