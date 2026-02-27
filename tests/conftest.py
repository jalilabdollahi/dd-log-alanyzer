"""Test fixtures and shared configuration."""

import pytest

from dd_log_analyzer.config import AppConfig, load_config


@pytest.fixture
def sample_config() -> AppConfig:
    """Create a test config with no real credentials."""
    return AppConfig(
        datadog={"api_key": "test_api_key", "app_key": "test_app_key", "site": "datadoghq.com"},
        slack={"enabled": False, "webhook_url": ""},
        jira={"enabled": False, "base_url": ""},
    )
