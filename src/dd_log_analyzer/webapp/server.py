"""FastAPI web server for dd-log-analyzer dashboard."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dd_log_analyzer.config import AppConfig, load_config, _project_root, _load_yaml, _deep_merge
from dd_log_analyzer.webapp.auth import (
    USERNAME,
    create_token,
    require_auth,
    verify_password,
)
from dd_log_analyzer.webapp.db import (
    count_anomalies,
    get_services_with_counts,
    init_db,
    list_anomalies,
    save_anomaly,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="dd-log-analyzer", docs_url="/api/docs")

_default_origins = ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost", "http://localhost:80"]
_cors_origins = os.getenv("CORS_ORIGINS", "").split(",") if os.getenv("CORS_ORIGINS") else _default_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Init DB on startup
init_db()

# Config cache
_config: AppConfig | None = None
_config_yaml_path = Path(os.getenv("CONFIG_PATH", str(_project_root / "config" / "default.yaml")))


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config() -> AppConfig:
    global _config
    _config = load_config()
    return _config


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str
    password: str


class PresetRequest(BaseModel):
    query: str
    description: str = ""
    services: list[str] = []


class AnalysisRequest(BaseModel):
    query: str = "*"
    preset: str | None = None
    time_range: str = "last 1h"


class ConfigUpdate(BaseModel):
    analysis: dict | None = None
    alerts: dict | None = None
    scope: dict | None = None


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------


@app.post("/api/auth/login")
async def login(req: LoginRequest):
    if req.username != USERNAME or not verify_password(req.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_token(req.username)
    return {"token": token, "username": req.username}


@app.get("/api/auth/me")
async def me(username: str = Depends(require_auth)):
    return {"username": username}


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@app.get("/api/dashboard")
async def dashboard(username: str = Depends(require_auth)):
    config = get_config()
    now = datetime.now(timezone.utc)
    since_24h = (now - timedelta(hours=24)).isoformat()

    # Anomaly stats from DB
    total_anomalies_24h = count_anomalies(since=since_24h)
    critical_24h = count_anomalies(severity="critical", since=since_24h)
    warning_24h = count_anomalies(severity="warning", since=since_24h)
    service_counts = get_services_with_counts(since=since_24h)

    # Recent anomalies
    recent = list_anomalies(limit=10)

    # Preset info
    presets = {k: {"query": v.query, "description": v.description} for k, v in config.presets.items()}

    return {
        "anomalies_24h": total_anomalies_24h,
        "critical_24h": critical_24h,
        "warning_24h": warning_24h,
        "service_counts": service_counts,
        "recent_anomalies": recent,
        "presets": presets,
        "scope": config.scope.model_dump(),
        "analysis_config": config.analysis.model_dump(),
    }


# ---------------------------------------------------------------------------
# Anomaly history
# ---------------------------------------------------------------------------


@app.get("/api/anomalies")
async def get_anomalies(
    limit: int = 50,
    offset: int = 0,
    service: str | None = None,
    severity: str | None = None,
    since: str | None = None,
    _: str = Depends(require_auth),
):
    anomalies = list_anomalies(limit=limit, offset=offset, service=service, severity=severity, since=since)
    total = count_anomalies(service=service, severity=severity, since=since)
    return {"anomalies": anomalies, "total": total, "limit": limit, "offset": offset}


# ---------------------------------------------------------------------------
# Run analysis on-demand
# ---------------------------------------------------------------------------


@app.post("/api/analyze")
async def run_analysis(req: AnalysisRequest, _: str = Depends(require_auth)):
    config = get_config()

    from dd_log_analyzer.client import DatadogLogClient
    from dd_log_analyzer.query.engine import QueryEngine, parse_time_range
    from dd_log_analyzer.analysis.engine import AnalysisEngine

    client = DatadogLogClient(config)
    engine = QueryEngine(client, config)
    analysis_engine = AnalysisEngine(config)

    time_from, time_to = parse_time_range(req.time_range)

    try:
        logs = engine.query(
            raw=req.query if not req.preset else None,
            preset=req.preset,
            time_range=req.time_range,
            limit=config.analysis.sample_size,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Datadog query failed: {e}")

    resolved_query = req.query
    if req.preset:
        resolved_query = engine.resolve_preset(req.preset)

    result = analysis_engine.analyze(
        logs=logs,
        query=resolved_query,
        time_from=time_from,
        time_to=time_to,
    )

    # Persist anomalies to DB
    for anom in result.anomalies:
        save_anomaly(
            timestamp=datetime.now(timezone.utc).isoformat(),
            service=anom.service,
            anomaly_type=anom.anomaly_type.value,
            severity=anom.severity.value,
            description=anom.description,
            metric_value=anom.metric_value,
            expected_value=anom.expected_value,
            query=resolved_query,
        )

    return {
        "query": result.query,
        "time_from": result.time_from.isoformat(),
        "time_to": result.time_to.isoformat(),
        "total_logs": result.total_logs,
        "anomalies": [a.model_dump() for a in result.anomalies],
        "patterns": [p.model_dump() for p in result.patterns[:20]],
        "error_groups": [eg.model_dump() for eg in result.error_groups[:15]],
        "trends": result.trends.model_dump(),
    }


# ---------------------------------------------------------------------------
# Log viewer
# ---------------------------------------------------------------------------


@app.get("/api/logs")
async def query_logs(
    query: str = "*",
    preset: str | None = None,
    time_range: str = "last 1h",
    limit: int = 100,
    _: str = Depends(require_auth),
):
    config = get_config()

    from dd_log_analyzer.client import DatadogLogClient
    from dd_log_analyzer.query.engine import QueryEngine

    client = DatadogLogClient(config)
    engine = QueryEngine(client, config)

    try:
        logs = engine.query(
            raw=query if not preset else None,
            preset=preset,
            time_range=time_range,
            limit=limit,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Datadog query failed: {e}")

    return {
        "logs": [
            {
                "id": l.id,
                "timestamp": l.timestamp.isoformat(),
                "status": l.status,
                "service": l.service,
                "host": l.host,
                "message": l.message,
            }
            for l in logs
        ],
        "total": len(logs),
    }


# ---------------------------------------------------------------------------
# Config / Presets
# ---------------------------------------------------------------------------


def _read_yaml() -> dict:
    return _load_yaml(_config_yaml_path)


def _write_yaml(data: dict) -> None:
    _config_yaml_path.parent.mkdir(parents=True, exist_ok=True)
    with open(_config_yaml_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


@app.get("/api/config")
async def get_full_config(_: str = Depends(require_auth)):
    config = get_config()
    safe = config.model_dump()
    # Mask sensitive fields
    safe["datadog"]["api_key"] = "***" if safe["datadog"]["api_key"] else ""
    safe["datadog"]["app_key"] = "***" if safe["datadog"]["app_key"] else ""
    safe["slack"]["webhook_url"] = "***" if safe["slack"]["webhook_url"] else ""
    safe["jira"]["api_token"] = "***" if safe["jira"]["api_token"] else ""
    return safe


@app.put("/api/config")
async def update_config(req: ConfigUpdate, _: str = Depends(require_auth)):
    data = _read_yaml()
    if req.analysis:
        data["analysis"] = {**(data.get("analysis") or {}), **req.analysis}
    if req.alerts:
        data["alerts"] = {**(data.get("alerts") or {}), **req.alerts}
    if req.scope:
        data["scope"] = {**(data.get("scope") or {}), **req.scope}
    _write_yaml(data)
    reload_config()
    return {"status": "ok", "message": "Config updated"}


@app.get("/api/config/presets")
async def get_presets(_: str = Depends(require_auth)):
    config = get_config()
    return {k: v.model_dump() for k, v in config.presets.items()}


@app.post("/api/config/presets/{name}")
async def upsert_preset(name: str, req: PresetRequest, _: str = Depends(require_auth)):
    data = _read_yaml()
    if "presets" not in data:
        data["presets"] = {}
    data["presets"][name] = {"query": req.query, "description": req.description, "services": req.services}
    _write_yaml(data)
    reload_config()
    return {"status": "ok", "preset": name}


@app.delete("/api/config/presets/{name}")
async def delete_preset(name: str, _: str = Depends(require_auth)):
    data = _read_yaml()
    if "presets" in data and name in data["presets"]:
        del data["presets"][name]
        _write_yaml(data)
        reload_config()
        return {"status": "ok", "deleted": name}
    raise HTTPException(status_code=404, detail=f"Preset '{name}' not found")


# ---------------------------------------------------------------------------
# Serve frontend (production)
# ---------------------------------------------------------------------------

_frontend_dist = Path(__file__).resolve().parent.parent.parent.parent / "webapp" / "frontend" / "dist"

if _frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=_frontend_dist / "assets"), name="assets")

    @app.get("/{path:path}")
    async def serve_spa(path: str):
        file_path = _frontend_dist / path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(_frontend_dist / "index.html")
