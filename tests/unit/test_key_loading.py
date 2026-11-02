"""Key-loading audit: OpsPilot reads only its own env / repo-root .env."""

from __future__ import annotations

import subprocess
from pathlib import Path

from opspilot.core.config import OWN_ENV_FILE, REPO_ROOT, Settings

ROOT = Path(__file__).resolve().parents[2]


def test_env_file_is_anchored_to_this_repo() -> None:
    assert OWN_ENV_FILE.is_absolute()
    assert REPO_ROOT == ROOT
    assert Settings.model_config["env_file"] == OWN_ENV_FILE


def test_other_project_env_in_cwd_is_ignored(tmp_path, monkeypatch) -> None:
    (tmp_path / ".env").write_text("OPSPILOT_LLM_API_KEY=from-another-project\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPSPILOT_LLM_API_KEY", raising=False)
    s = Settings(_env_file=OWN_ENV_FILE)
    assert s.llm_api_key.get_secret_value() != "from-another-project"


def test_api_key_only_from_opspilot_prefixed_variable(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "generic-var-must-not-be-used")
    monkeypatch.delenv("OPSPILOT_LLM_API_KEY", raising=False)
    s = Settings(_env_file=None)
    assert s.llm_api_key.get_secret_value() == ""


def test_no_tracked_file_references_another_projects_env() -> None:
    out = subprocess.run(  # noqa: S603
        ["git", "grep", "-n", "-E", r"zhanggui[^ ]*/\.env|projects/[a-z-]+/\.env"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
    ).stdout
    hits = [
        ln
        for ln in out.splitlines()
        if "opspilot-ai/.env" not in ln and "test_key_loading" not in ln
    ]
    assert hits == [], hits
