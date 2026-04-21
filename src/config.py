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
    llm_model: str = "glm-4-flash"
    llm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    llm_api_key: str = ""
    llm_temperature: float = 0.1

    # arXiv
    max_results: int = 100
    days_back: int = 3

    # Filter
    batch_size: int = 6
    relevance_threshold: int = 4

    # Report
    report_output_dir: str = "reports"


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

    # Allow env-var overrides for secrets and model
    api_key = os.getenv("GLM_API_KEY", "")
    model = os.getenv("LLM_MODEL", llm.get("model", "glm-4-flash"))
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
        relevance_threshold=filt.get("relevance_threshold", 4),
        report_output_dir=report.get("output_dir", "reports"),
    )
