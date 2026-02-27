"""DynamoDB alert state adapter — same interface as SQLite, backed by DynamoDB + TTL."""

from __future__ import annotations

import time
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key


class DynamoAlertState:
    """Alert dedup using DynamoDB instead of SQLite.

    Drop-in replacement for AlertStateDB with the same interface:
    - should_alert(fingerprint, cooldown_minutes) -> bool
    - record_alert(fingerprint, jira_key) -> None
    - get_existing_ticket(fingerprint) -> str | None
    - cleanup_old() -> Not needed (DynamoDB TTL handles it)
    """

    def __init__(self, table_name: str, region: str = "eu-west-2"):
        self._dynamodb = boto3.resource("dynamodb", region_name=region)
        self._table = self._dynamodb.Table(table_name)

    def should_alert(self, fingerprint: str, cooldown_minutes: int = 15) -> bool:
        """Check if this alert should fire based on cooldown period."""
        response = self._table.get_item(Key={"fingerprint": fingerprint})
        item = response.get("Item")

        if item is None:
            return True

        last_fired = float(item.get("last_fired", 0))
        elapsed = time.time() - last_fired
        return elapsed >= (cooldown_minutes * 60)

    def record_alert(
        self,
        fingerprint: str,
        jira_key: str | None = None,
        ttl_hours: int = 24,
    ) -> None:
        """Record that an alert was fired. Sets TTL for automatic cleanup."""
        now = time.time()
        ttl_expiry = int(now + (ttl_hours * 3600))

        item = {
            "fingerprint": fingerprint,
            "last_fired": Decimal(str(now)),
            "ttl_expiry": ttl_expiry,
            "count": 1,
        }
        if jira_key:
            item["jira_key"] = jira_key

        # Use UpdateItem for atomic increment of count
        update_expr = (
            "SET last_fired = :now, ttl_expiry = :ttl"
            " ADD #cnt :one"
        )
        expr_names = {"#cnt": "count"}
        expr_values = {
            ":now": Decimal(str(now)),
            ":ttl": ttl_expiry,
            ":one": 1,
        }

        if jira_key:
            update_expr += ", jira_key = :jk"
            expr_values[":jk"] = jira_key

        self._table.update_item(
            Key={"fingerprint": fingerprint},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
        )

    def get_existing_ticket(self, fingerprint: str) -> str | None:
        """Get the Jira ticket key for a fingerprint, if one exists."""
        response = self._table.get_item(
            Key={"fingerprint": fingerprint},
            ProjectionExpression="jira_key",
        )
        item = response.get("Item")
        return item.get("jira_key") if item else None

    def cleanup_old(self, max_age_hours: int = 24) -> int:
        """Not needed — DynamoDB TTL handles automatic cleanup.

        Included for interface compatibility. Returns 0.
        """
        return 0

    def close(self) -> None:
        """No-op — DynamoDB doesn't need explicit close."""
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
