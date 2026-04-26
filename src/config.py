"""Load YAML configs and provide typed settings for the pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


@dataclass
class Topic:
    name: str
    description: str
    keywords: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> Topic:
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            keywords=d.get("keywords", []),
            categories=d.get("categories", []),
        )


@dataclass
class Settings:
    # LLM
    llm_provider: str = "openai_compatible"
    llm_model: str = "glm-4.7-flash"
    llm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    llm_api_key: str = ""
    llm_temperature: float = 0.1

    # arXiv
    max_results: int = 100
    days_back: int = 3

    # Filter
    batch_size: int = 6
    batch_delay: float = 5
    relevance_threshold: int = 4
    pre_filter_max: int = 30

    # Report
    report_output_dir: str = "reports"

    # Database
    db_path: str = "data/arxistant.db"

    # Candidates
    candidates_path: str = "data/candidates.yaml"

    # Reading
    max_text_chars: int = 80000
    executive_read_max_chars: int = 30000
    html_timeout: int = 30

    # Feishu
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_verification_token: str = ""
    feishu_encrypt_key: str = ""
    feishu_bot_name: str = "ArXistant"

    # Bot
    target_chat_id: str = ""
    session_max_messages: int = 20
    report_cron: str = "30 10 * * 1-5"

    # Understanding Verifier
    verifier_enabled: bool = True
    verifier_max_points: int = 5
    verifier_max_iterations: int = 1
    verifier_run_feynman: bool = True
    verifier_feynman_importance_threshold: int = 4
    verifier_logic_pass_threshold: int = 8
    verifier_feynman_pass_threshold: int = 8
    verifier_max_context_chars: int = 20000
    verifier_store_certificates: bool = True
    verifier_ask_user_on_gaps: bool = True
    verifier_progress_interval: int = 30


def load_topics(path: str | Path | None = None) -> list[Topic]:
    """Load topic definitions from YAML."""
    if path is None:
        path = PROJECT_ROOT / "config" / "topics.yaml"
    with open(path) as f:
        data = yaml.safe_load(f)
    return [Topic.from_dict(t) for t in data.get("topics", [])]


def load_settings(path: str | Path | None = None) -> Settings:
    """Load runtime settings from YAML, with .env overrides."""
    if path is None:
        path = PROJECT_ROOT / "config" / "settings.yaml"
    with open(path) as f:
        data = yaml.safe_load(f)

    llm = data.get("llm", {})
    arxiv = data.get("arxiv", {})
    filt = data.get("filter", {})
    report = data.get("report", {})
    database = data.get("database", {})
    candidates = data.get("candidates", {})
    reading = data.get("reading", {})
    feishu = data.get("feishu", {})
    bot = data.get("bot", {})
    verifier = data.get("verifier", {})

    # Allow env-var overrides for secrets and model
    api_key = os.getenv("GLM_API_KEY", "")
    model = os.getenv("LLM_MODEL", llm.get("model", "glm-4.7-flash"))
    base_url = os.getenv("LLM_BASE_URL", llm.get("base_url", "https://open.bigmodel.cn/api/paas/v4"))

    return Settings(
        llm_provider=llm.get("provider", "openai_compatible"),
        llm_model=model,
        llm_base_url=base_url,
        llm_api_key=api_key,
        llm_temperature=llm.get("temperature", 0.1),
        max_results=arxiv.get("max_results", 100),
        days_back=arxiv.get("days_back", 3),
        batch_size=filt.get("batch_size", 6),
        batch_delay=filt.get("batch_delay", 5),
        relevance_threshold=filt.get("relevance_threshold", 4),
        pre_filter_max=filt.get("pre_filter_max", 30),
        report_output_dir=report.get("output_dir", "reports"),
        db_path=database.get("path", "data/arxistant.db"),
        candidates_path=candidates.get("path", "data/candidates.yaml"),
        max_text_chars=reading.get("max_text_chars", 80000),
        executive_read_max_chars=reading.get("executive_read_max_chars", 30000),
        html_timeout=reading.get("html_timeout", 30),
        feishu_app_id=os.getenv("FEISHU_APP_ID", feishu.get("app_id", "")),
        feishu_app_secret=os.getenv("FEISHU_APP_SECRET", feishu.get("app_secret", "")),
        feishu_verification_token=os.getenv("FEISHU_VERIFICATION_TOKEN", feishu.get("verification_token", "")),
        feishu_encrypt_key=os.getenv("FEISHU_ENCRYPT_KEY", feishu.get("encrypt_key", "")),
        feishu_bot_name=feishu.get("bot_name", "ArXistant"),
        target_chat_id=bot.get("target_chat_id", ""),
        session_max_messages=bot.get("session_max_messages", 20),
        report_cron=bot.get("report_cron", "30 10 * * 1-5"),
        verifier_enabled=verifier.get("enabled", True),
        verifier_max_points=verifier.get("max_points", 5),
        verifier_max_iterations=verifier.get("max_iterations", 1),
        verifier_run_feynman=verifier.get("run_feynman", True),
        verifier_feynman_importance_threshold=verifier.get("feynman_importance_threshold", 4),
        verifier_logic_pass_threshold=verifier.get("logic_pass_threshold", 8),
        verifier_feynman_pass_threshold=verifier.get("feynman_pass_threshold", 8),
        verifier_max_context_chars=verifier.get("max_context_chars", 20000),
        verifier_store_certificates=verifier.get("store_certificates", True),
        verifier_ask_user_on_gaps=verifier.get("ask_user_on_gaps", True),
        verifier_progress_interval=verifier.get("progress_interval", 30),
    )
