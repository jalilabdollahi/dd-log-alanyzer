"""Datadog API v2 client wrapper — search, aggregate, paginate."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from datadog_api_client import ApiClient, Configuration
from datadog_api_client.v2.api.logs_api import LogsApi
from datadog_api_client.v2.model.logs_aggregate_request import LogsAggregateRequest
from datadog_api_client.v2.model.logs_aggregate_sort import LogsAggregateSort
from datadog_api_client.v2.model.logs_aggregate_sort_type import LogsAggregateSortType
from datadog_api_client.v2.model.logs_aggregation_function import LogsAggregationFunction
from datadog_api_client.v2.model.logs_compute import LogsCompute
from datadog_api_client.v2.model.logs_compute_type import LogsComputeType
from datadog_api_client.v2.model.logs_group_by import LogsGroupBy
from datadog_api_client.v2.model.logs_list_request import LogsListRequest
from datadog_api_client.v2.model.logs_list_request_page import LogsListRequestPage
from datadog_api_client.v2.model.logs_query_filter import LogsQueryFilter
from datadog_api_client.v2.model.logs_query_options import LogsQueryOptions
from datadog_api_client.v2.model.logs_sort import LogsSort

from dd_log_analyzer.cache import ResponseCache
from dd_log_analyzer.config import AppConfig
from dd_log_analyzer.models.log_entry import AggregationBucket, AggregationResult, LogEntry

logger = logging.getLogger(__name__)


class DatadogLogClient:
    """Wrapper around the Datadog Logs API v2 with pagination, retries, and caching."""

    MAX_PAGE_SIZE = 1000
    MAX_RETRIES = 6
    RETRY_BASE_DELAY = 1.0

    def __init__(self, config: AppConfig):
        self._config = config
        self._cache = ResponseCache(ttl=config.datadog.cache_ttl)

        # Configure Datadog SDK
        self._dd_config = Configuration()
        self._dd_config.api_key["apiKeyAuth"] = config.datadog.api_key
        self._dd_config.api_key["appKeyAuth"] = config.datadog.app_key
        self._dd_config.server_variables["site"] = config.datadog.site

    def _get_api(self) -> tuple[ApiClient, LogsApi]:
        """Create a fresh API client + LogsApi instance."""
        client = ApiClient(self._dd_config)
        return client, LogsApi(client)

    def _parse_log(self, raw: Any) -> LogEntry:
        """Parse a raw Datadog log response into a LogEntry."""
        attrs = raw.attributes if hasattr(raw, "attributes") else {}

        # The SDK returns LogAttributes objects — use getattr for field access
        def _get(obj: Any, key: str, default: Any = None) -> Any:
            """Get a value from either a dict or an SDK model object."""
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        # Extract nested custom attributes
        log_attrs = _get(attrs, "attributes", {})
        if not isinstance(log_attrs, dict):
            try:
                log_attrs = log_attrs.to_dict() if hasattr(log_attrs, "to_dict") else {}
            except Exception:
                log_attrs = {}

        tags = _get(attrs, "tags", [])
        if not isinstance(tags, list):
            tags = []

        # Parse timestamp
        ts = _get(attrs, "timestamp", None)
        if ts is None:
            ts = datetime.utcnow()
        elif isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                ts = datetime.utcnow()

        return LogEntry(
            id=raw.id if hasattr(raw, "id") else str(id(raw)),
            timestamp=ts,
            status=str(_get(attrs, "status", "info")).lower(),
            service=str(_get(attrs, "service", "unknown")),
            host=_get(attrs, "host", None),
            message=str(_get(attrs, "message", "")),
            attributes=log_attrs,
            tags=tags,
        )

    def _retry_with_backoff(self, func, *args, **kwargs) -> Any:
        """Execute function with exponential backoff on rate limit (429)."""
        for attempt in range(self.MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                error_str = str(e)
                if "429" in error_str or "Too Many Requests" in error_str:
                    delay = self.RETRY_BASE_DELAY * (2**attempt)
                    logger.warning(f"Rate limited (429), retrying in {delay}s (attempt {attempt + 1})")
                    time.sleep(delay)
                else:
                    raise
        raise RuntimeError(f"Failed after {self.MAX_RETRIES} retries due to rate limiting")

    def search_logs(
        self,
        query: str,
        time_from: datetime,
        time_to: datetime,
        indexes: list[str] | None = None,
        limit: int = 1000,
    ) -> list[LogEntry]:
        """Search logs with automatic pagination.

        Args:
            query: Datadog query string (e.g. "env:prod service:api-gateway status:error").
            time_from: Start of time range.
            time_to: End of time range.
            indexes: Log indexes to search (default: configured index).
            limit: Max total logs to return (may span multiple pages).

        Returns:
            List of LogEntry objects.
        """
        cache_key = ResponseCache._make_key(
            "search", query, str(time_from), str(time_to), str(indexes), limit
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit for search query: {query[:80]}")
            return cached

        if indexes is None:
            indexes = [self._config.datadog.log_index]

        all_logs: list[LogEntry] = []
        cursor: str | None = None
        page_limit = min(limit, self.MAX_PAGE_SIZE)

        while len(all_logs) < limit:
            page = LogsListRequestPage(limit=page_limit)
            if cursor:
                page = LogsListRequestPage(limit=page_limit, cursor=cursor)

            body = LogsListRequest(
                filter=LogsQueryFilter(
                    query=query,
                    indexes=indexes,
                    _from=time_from.isoformat() + "Z" if time_from.tzinfo is None else time_from.isoformat(),
                    to=time_to.isoformat() + "Z" if time_to.tzinfo is None else time_to.isoformat(),
                ),
                sort=LogsSort.TIMESTAMP_DESCENDING,
                page=page,
            )

            api_client, logs_api = self._get_api()
            with api_client:
                response = self._retry_with_backoff(logs_api.list_logs, body=body)

            if response.data:
                for raw_log in response.data:
                    all_logs.append(self._parse_log(raw_log))

            # Check for next page
            if hasattr(response, "meta") and response.meta:
                page_meta = response.meta
                if hasattr(page_meta, "page") and page_meta.page:
                    cursor = getattr(page_meta.page, "after", None)
                else:
                    break
            else:
                break

            if cursor is None:
                break

            logger.debug(f"Fetched page, total logs so far: {len(all_logs)}")

        result = all_logs[:limit]
        self._cache.set(cache_key, result)
        return result

    def aggregate_logs(
        self,
        query: str,
        time_from: datetime,
        time_to: datetime,
        group_by: list[str] | None = None,
        compute_count: bool = True,
    ) -> AggregationResult:
        """Aggregate logs server-side using Datadog's aggregate endpoint.

        This processes ALL matching logs on Datadog's servers without pulling
        individual log entries — ideal for 2M+ log environments.

        Args:
            query: Datadog query string.
            time_from: Start of time range.
            time_to: End of time range.
            group_by: Facets to group by (e.g. ["service", "status"]).
            compute_count: Whether to compute count per group.

        Returns:
            AggregationResult with bucketed data.
        """
        cache_key = ResponseCache._make_key(
            "aggregate", query, str(time_from), str(time_to), str(group_by)
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit for aggregate query: {query[:80]}")
            return cached

        compute_list = []
        if compute_count:
            compute_list.append(
                LogsCompute(
                    aggregation=LogsAggregationFunction.COUNT,
                    type=LogsComputeType.TOTAL,
                )
            )

        group_by_list = []
        if group_by:
            for facet in group_by:
                group_by_list.append(
                    LogsGroupBy(
                        facet=facet,
                        limit=50,
                        sort=LogsAggregateSort(
                            type=LogsAggregateSortType.MEASURE,
                            aggregation=LogsAggregationFunction.COUNT,
                        ),
                    )
                )

        body = LogsAggregateRequest(
            filter=LogsQueryFilter(
                query=query,
                indexes=[self._config.datadog.log_index],
                _from=time_from.isoformat() + "Z" if time_from.tzinfo is None else time_from.isoformat(),
                to=time_to.isoformat() + "Z" if time_to.tzinfo is None else time_to.isoformat(),
            ),
            compute=compute_list,
            group_by=group_by_list if group_by_list else None,
        )

        api_client, logs_api = self._get_api()
        with api_client:
            response = self._retry_with_backoff(logs_api.aggregate_logs, body=body)

        buckets: list[AggregationBucket] = []
        total = 0

        if hasattr(response, "data") and response.data:
            if hasattr(response.data, "buckets") and response.data.buckets:
                for bucket in response.data.buckets:
                    group_vals = {}
                    if hasattr(bucket, "by") and bucket.by:
                        group_vals = dict(bucket.by) if isinstance(bucket.by, dict) else {}

                    computed = {}
                    if hasattr(bucket, "computes") and bucket.computes:
                        comp = bucket.computes
                        if isinstance(comp, dict):
                            for k, v in comp.items():
                                if hasattr(v, "value"):
                                    computed[k] = float(v.value)
                                else:
                                    computed[k] = float(v) if v else 0
                        elif hasattr(comp, "c0") and hasattr(comp.c0, "value"):
                            computed["count"] = float(comp.c0.value)

                    count = int(computed.get("count", computed.get("c0", 0)))
                    total += count
                    buckets.append(AggregationBucket(group_by=group_vals, count=count, computed=computed))

        result = AggregationResult(buckets=buckets, total=total)
        self._cache.set(cache_key, result)
        return result

    def health_check(self) -> dict[str, Any]:
        """Verify Datadog API connectivity and permissions."""
        try:
            api_client, logs_api = self._get_api()
            with api_client:
                # Try a minimal query
                body = LogsListRequest(
                    filter=LogsQueryFilter(
                        query="*",
                        indexes=[self._config.datadog.log_index],
                        _from=datetime.utcnow().isoformat() + "Z",
                        to=datetime.utcnow().isoformat() + "Z",
                    ),
                    page=LogsListRequestPage(limit=1),
                )
                self._retry_with_backoff(logs_api.list_logs, body=body)
            return {"status": "ok", "site": self._config.datadog.site, "index": self._config.datadog.log_index}
        except Exception as e:
            return {"status": "error", "error": str(e)}
