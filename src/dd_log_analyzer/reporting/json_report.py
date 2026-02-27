"""JSON report — structured export for pipeline integration."""

from __future__ import annotations

import json
from pathlib import Path

from dd_log_analyzer.models.log_entry import AnalysisResult


def generate_json_report(
    result: AnalysisResult,
    output_path: str | Path | None = None,
    indent: int = 2,
) -> str:
    """Generate a JSON report from an analysis result.

    Args:
        result: The analysis result to export.
        output_path: If provided, write to this file.
        indent: JSON indentation level.

    Returns:
        JSON string.
    """
    data = result.model_dump(mode="json")

    json_str = json.dumps(data, indent=indent, default=str)

    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json_str)

    return json_str
