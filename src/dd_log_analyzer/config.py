"""Configuration manager — loads from CLI flags, ENV vars, .env, YAML profiles."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Load .env early
# ---------------------------------------------------------------------------

_project_root = Path(__file__).resolve().parent.parent.parent  # src/dd_log_analyzer/config.py → project root
_env_path = _project_root / ".env"
if _env_path.exists():
    load_dotenv(_env_path)


# ---------------------------------------------------------------------------
# Config sub-models
# ---------------------------------------------------------------------------


class ScopeConfig(BaseModel):
    env: str | None = None
    services: list[str] = Field(default_factory=list)


class DatadogConfig(BaseModel):
    api_key: str = ""
    app_key: str = ""
    site: str = "datadoghq.com"
    log_index: str = "main"
    max_results: int = 5000
    cache_ttl: int = 300


class AnalysisConfig(BaseModel):
    anomaly_zscore_threshold: float = 2.5
    burst_window_seconds: int = 120
    burst_min_count: int = 50
    trend_bucket_minutes: int = 5
    top_patterns: int = 20
    sample_size: int = 5000


class AlertConfig(BaseModel):
    cooldown_minutes: int = 15
    severity_threshold: str = "warning"


class SlackConfig(BaseModel):
    enabled: bool = True
    webhook_url: str = ""
    channel: str | None = None


class JiraConfig(BaseModel):
    enabled: bool = True
    base_url: str = ""
    email: str = ""
    api_token: str = ""
    project_key: str = "OPS"
    issue_type: str = "Bug"
    auto_create: bool = True
    severity_mapping: dict[str, str] = Field(
        default_factory=lambda: {"critical": "Highest", "warning": "High", "info": "Medium"}
    )
    assignees: dict[str, str] = Field(default_factory=dict)


class QueryPreset(BaseModel):
    query: str
    services: list[str] = Field(default_factory=list)
    description: str = ""


# ---------------------------------------------------------------------------
# Main config
# ---------------------------------------------------------------------------


class AppConfig(BaseModel):
    """Full application configuration."""

    scope: ScopeConfig = Field(default_factory=ScopeConfig)
    datadog: DatadogConfig = Field(default_factory=DatadogConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    alerts: AlertConfig = Field(default_factory=AlertConfig)
    slack: SlackConfig = Field(default_factory=SlackConfig)
    jira: JiraConfig = Field(default_factory=JiraConfig)
    presets: dict[str, QueryPreset] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML config file, return empty dict if missing."""
    if not path.exists():
        return {}
    with open(path) as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def load_config(
    profile: str = "default",
    config_dir: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> AppConfig:
    """Load configuration with precedence: overrides → ENV → .env → YAML → defaults.

    Args:
        profile: Name of the YAML profile to load (e.g. "default", "staging").
        config_dir: Directory containing YAML config files.
        overrides: CLI-provided overrides to apply last.

    Returns:
        Fully resolved AppConfig.
    """
    # 1. Load YAML profile
    if config_dir is None:
        config_dir = _project_root / "config"
    yaml_data = _load_yaml(config_dir / f"{profile}.yaml")

    # 2. Apply environment variable overrides
    env_overrides: dict[str, Any] = {}

    # Datadog
    if dd_api := os.getenv("DD_API_KEY"):
        env_overrides.setdefault("datadog", {})["api_key"] = dd_api
    if dd_app := os.getenv("DD_APP_KEY"):
        env_overrides.setdefault("datadog", {})["app_key"] = dd_app
    if dd_site := os.getenv("DD_SITE"):
        env_overrides.setdefault("datadog", {})["site"] = dd_site

    # Slack
    if slack_url := os.getenv("SLACK_WEBHOOK_URL"):
        env_overrides.setdefault("slack", {})["webhook_url"] = slack_url

    # Jira
    if jira_url := os.getenv("JIRA_BASE_URL"):
        env_overrides.setdefault("jira", {})["base_url"] = jira_url
    if jira_email := os.getenv("JIRA_EMAIL"):
        env_overrides.setdefault("jira", {})["email"] = jira_email
    if jira_token := os.getenv("JIRA_API_TOKEN"):
        env_overrides.setdefault("jira", {})["api_token"] = jira_token

    # 3. Merge: YAML → ENV → CLI overrides
    merged = yaml_data
    merged = _deep_merge(merged, env_overrides)
    if overrides:
        merged = _deep_merge(merged, overrides)

    return AppConfig(**merged)
