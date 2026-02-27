"""AWS config loader — loads secrets from Secrets Manager and config from SSM."""

from __future__ import annotations

import json
import logging
import os

import boto3
import yaml

from dd_log_analyzer.config import AppConfig, _deep_merge

logger = logging.getLogger(__name__)


def load_config_from_aws(
    secret_name: str | None = None,
    ssm_config_path: str | None = None,
    region: str | None = None,
) -> AppConfig:
    """Load AppConfig from AWS Secrets Manager + SSM Parameter Store.

    1. Fetch secrets (API keys, tokens) from Secrets Manager
    2. Fetch analysis config (YAML) from SSM Parameter Store
    3. Merge and return AppConfig

    Args:
        secret_name: Secrets Manager secret name (or env: SECRET_NAME).
        ssm_config_path: SSM parameter path (or env: SSM_CONFIG_PATH).
        region: AWS region (or env: AWS_REGION_NAME).

    Returns:
        Fully resolved AppConfig.
    """
    secret_name = secret_name or os.environ.get("SECRET_NAME", "dd-log-analyzer/secrets")
    ssm_config_path = ssm_config_path or os.environ.get("SSM_CONFIG_PATH", "/dd-log-analyzer/config")
    region = region or os.environ.get("AWS_REGION_NAME", "eu-west-2")

    # --- 1. Load secrets from Secrets Manager ---
    sm_client = boto3.client("secretsmanager", region_name=region)
    try:
        secret_response = sm_client.get_secret_value(SecretId=secret_name)
        secrets = json.loads(secret_response["SecretString"])
        logger.info(f"Loaded secrets from Secrets Manager: {secret_name}")
    except Exception as e:
        logger.error(f"Failed to load secrets from Secrets Manager: {e}")
        secrets = {}

    # --- 2. Load config from SSM Parameter Store ---
    ssm_client = boto3.client("ssm", region_name=region)
    try:
        param_response = ssm_client.get_parameter(Name=ssm_config_path)
        yaml_str = param_response["Parameter"]["Value"]
        config_data = yaml.safe_load(yaml_str) or {}
        logger.info(f"Loaded config from SSM: {ssm_config_path}")
    except Exception as e:
        logger.warning(f"Failed to load config from SSM (using defaults): {e}")
        config_data = {}

    # --- 3. Merge secrets into config ---
    secret_overrides: dict = {}

    # Datadog credentials
    if secrets.get("DD_API_KEY"):
        secret_overrides.setdefault("datadog", {})["api_key"] = secrets["DD_API_KEY"]
    if secrets.get("DD_APP_KEY"):
        secret_overrides.setdefault("datadog", {})["app_key"] = secrets["DD_APP_KEY"]
    if secrets.get("DD_SITE"):
        secret_overrides.setdefault("datadog", {})["site"] = secrets["DD_SITE"]

    # Slack
    if secrets.get("SLACK_WEBHOOK_URL"):
        secret_overrides.setdefault("slack", {})["webhook_url"] = secrets["SLACK_WEBHOOK_URL"]

    # Jira
    if secrets.get("JIRA_BASE_URL"):
        secret_overrides.setdefault("jira", {})["base_url"] = secrets["JIRA_BASE_URL"]
    if secrets.get("JIRA_EMAIL"):
        secret_overrides.setdefault("jira", {})["email"] = secrets["JIRA_EMAIL"]
    if secrets.get("JIRA_API_TOKEN"):
        secret_overrides.setdefault("jira", {})["api_token"] = secrets["JIRA_API_TOKEN"]

    # Merge: config (SSM) → secrets (Secrets Manager)
    merged = _deep_merge(config_data, secret_overrides)

    return AppConfig(**merged)
