"""AI-powered anomaly description enhancer using AWS Bedrock."""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

_DEFAULT_MODEL_ID = "anthropic.claude-3-haiku-20240307-v1:0"

_SYSTEM_PROMPT = (
    "You are a senior DevOps/SRE incident analyst. You are given an anomaly alert "
    "together with the ACTUAL error log messages from the affected service.\n\n"
    "Analyze the error logs carefully and write a concise incident report (4-6 sentences). Include:\n"
    "1. What is failing — identify the specific error pattern from the logs\n"
    "2. Root cause hypothesis based on the log content\n"
    "3. Impact — which services and users are affected\n"
    "4. Recommended investigation steps (specific kubectl, Datadog, or infra checks)\n\n"
    "Be direct, technical, and actionable. Reference specific error messages you see in the logs. "
    "No markdown formatting, no bullet points, just plain text paragraphs."
)


class AnomalyDescriber:
    """Enhance anomaly descriptions using AWS Bedrock (Claude 3 Haiku)."""

    def __init__(self, region: str = "us-east-1", model_id: str | None = None):
        self._model_id = model_id or os.environ.get("BEDROCK_MODEL_ID", _DEFAULT_MODEL_ID)
        self._region = region
        self._client = None

    def _get_client(self):
        """Lazy-init Bedrock client."""
        if self._client is None:
            import boto3
            self._client = boto3.client(
                "bedrock-runtime",
                region_name=self._region,
            )
        return self._client

    def enhance(
        self,
        anomaly_type: str,
        description: str,
        service: str | None = None,
        metric_value: float = 0,
        expected_value: float = 0,
        error_logs: list[str] | None = None,
        query: str | None = None,
    ) -> str:
        """Generate an AI-enhanced description for an anomaly.

        Args:
            anomaly_type: Type of anomaly (e.g. 'error_burst', 'volume_anomaly').
            description: Original template description.
            service: Affected service name.
            metric_value: Actual observed value.
            expected_value: Expected/baseline value.
            error_logs: Full error log messages from the service.
            query: The Datadog query that produced this anomaly.

        Returns:
            Enhanced description string, or the original if AI fails.
        """
        # Build context for the model
        context_parts = [
            f"Anomaly Type: {anomaly_type}",
            f"Original Alert: {description}",
        ]
        if service:
            context_parts.append(f"Service: {service}")
        if metric_value or expected_value:
            context_parts.append(f"Actual: {metric_value:.0f} logs, Expected: {expected_value:.0f} logs")
        if query:
            context_parts.append(f"Datadog Query: {query}")

        # Send ALL error logs (up to 30) so the model can read the real errors
        if error_logs:
            truncated = error_logs[:30]
            logs_block = "\n".join(f"  [{i+1}] {msg[:500]}" for i, msg in enumerate(truncated))
            context_parts.append(
                f"\n--- ERROR LOGS ({len(error_logs)} total, showing {len(truncated)}) ---\n{logs_block}"
            )
        else:
            context_parts.append("\n(No error log messages available)")

        user_message = "\n".join(context_parts)

        try:
            client = self._get_client()
            body = json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 400,
                "system": _SYSTEM_PROMPT,
                "messages": [
                    {"role": "user", "content": user_message},
                ],
            })

            response = client.invoke_model(
                modelId=self._model_id,
                contentType="application/json",
                accept="application/json",
                body=body,
            )

            result = json.loads(response["body"].read())
            ai_text = result["content"][0]["text"].strip()

            logger.info(f"AI description generated for {anomaly_type} ({service}) — fed {len(error_logs or [])} error logs")
            return ai_text

        except Exception as e:
            logger.warning(f"Bedrock AI failed, using original description: {e}")
            return description

