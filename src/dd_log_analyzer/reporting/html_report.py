"""HTML report — standalone report with embedded Chart.js visualizations."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from dd_log_analyzer.models.log_entry import AnalysisResult


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def generate_html_report(
    result: AnalysisResult,
    output_path: str | Path | None = "report.html",
    error_logs: list[dict] | None = None,
) -> Path | str:
    """Generate a standalone HTML report with charts.

    Args:
        result: The analysis result.
        output_path: Output file path. If None, returns HTML string instead of writing.
        error_logs: Optional list of error log dicts with 'timestamp', 'service', 'message' keys.

    Returns:
        Path to the generated HTML file, or HTML string if output_path is None.
    """

    # Prepare chart data
    trend_labels = [b.timestamp.strftime("%H:%M") for b in result.trends.buckets]
    trend_counts = [b.count for b in result.trends.buckets]
    trend_errors = [b.error_count for b in result.trends.buckets]

    pattern_labels = [p.template[:50] for p in result.patterns[:10]]
    pattern_counts = [p.count for p in result.patterns[:10]]

    anomaly_count = len(result.anomalies)
    critical_count = sum(1 for a in result.anomalies if a.severity.value == "critical")
    warning_count = sum(1 for a in result.anomalies if a.severity.value == "warning")
    total_errors = sum(eg.count for eg in result.error_groups)
    error_logs = error_logs or []

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Datadog Log Analysis Report</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0f172a; color: #e2e8f0; padding: 2rem;
        }}
        .header {{
            text-align: center; margin-bottom: 2rem;
            background: linear-gradient(135deg, #1e293b, #334155);
            padding: 2rem; border-radius: 12px;
        }}
        .header h1 {{ color: #60a5fa; font-size: 1.8rem; }}
        .header .meta {{ color: #94a3b8; margin-top: 0.5rem; font-size: 0.9rem; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 1.5rem; }}
        .card {{
            background: #1e293b; border-radius: 12px; padding: 1.5rem;
            border: 1px solid #334155;
        }}
        .card h2 {{ color: #60a5fa; font-size: 1.2rem; margin-bottom: 1rem; }}
        .stat-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; margin-bottom: 1.5rem; }}
        .stat {{
            text-align: center; padding: 1rem; background: #0f172a;
            border-radius: 8px; border: 1px solid #334155;
        }}
        .stat .value {{ font-size: 1.8rem; font-weight: bold; color: #60a5fa; }}
        .stat .label {{ color: #94a3b8; font-size: 0.8rem; margin-top: 0.3rem; }}
        .stat.danger .value {{ color: #f87171; }}
        .stat.warning .value {{ color: #fbbf24; }}
        .stat.ok .value {{ color: #4ade80; }}
        table {{
            width: 100%; border-collapse: collapse; margin-top: 0.5rem;
            font-size: 0.85rem;
        }}
        th {{ text-align: left; padding: 0.6rem; color: #94a3b8; border-bottom: 1px solid #334155; }}
        td {{ padding: 0.6rem; border-bottom: 1px solid #1e293b; }}
        .severity-critical {{ color: #f87171; font-weight: bold; }}
        .severity-warning {{ color: #fbbf24; }}
        .chart-container {{ position: relative; height: 250px; margin-top: 1rem; }}
        .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }}
        .badge-critical {{ background: rgba(248,113,113,0.15); color: #f87171; border: 1px solid rgba(248,113,113,0.3); }}
        .badge-warning {{ background: rgba(251,191,36,0.15); color: #fbbf24; border: 1px solid rgba(251,191,36,0.3); }}
        .badge-error {{ background: rgba(248,113,113,0.1); color: #fca5a5; }}
        .trend-up {{ color: #f87171; }}
        .trend-down {{ color: #4ade80; }}
        .trend-stable {{ color: #94a3b8; }}
        .error-msg {{ font-family: 'SF Mono', 'Fira Code', monospace; font-size: 0.8rem; color: #fca5a5; word-break: break-all; }}
        .svc-tag {{ display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 0.75rem; background: rgba(96,165,250,0.15); color: #93c5fd; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>📊 Datadog Log Analysis Report</h1>
        <div class="meta">
            Query: <code>{_escape_html(result.query)}</code><br>
            Time: {result.time_from.strftime('%Y-%m-%d %H:%M')} → {result.time_to.strftime('%Y-%m-%d %H:%M')}<br>
            Generated: {result.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}
        </div>
    </div>

    <div class="stat-grid">
        <div class="stat">
            <div class="value">{result.total_logs:,}</div>
            <div class="label">Total Logs</div>
        </div>
        <div class="stat {'danger' if anomaly_count > 0 else 'ok'}">
            <div class="value">{anomaly_count}</div>
            <div class="label">Anomalies ({critical_count} critical, {warning_count} warning)</div>
        </div>
        <div class="stat {'danger' if total_errors > 0 else 'ok'}">
            <div class="value">{total_errors:,}</div>
            <div class="label">Error Count</div>
        </div>
        <div class="stat {'trend-up' if result.trends.trend_direction == 'increasing' else 'ok'}">
            <div class="value" style="font-size: 1.4rem;">{'▲' if result.trends.trend_direction == 'increasing' else '▼' if result.trends.trend_direction == 'decreasing' else '→'} {result.trends.trend_direction.title()}</div>
            <div class="label">Trend ({result.trends.change_percentage:+.0f}%)</div>
        </div>
    </div>

    <div class="grid">
        <div class="card">
            <h2>📈 Volume Trend</h2>
            <div class="chart-container">
                <canvas id="trendChart"></canvas>
            </div>
        </div>

        <div class="card">
            <h2>📋 Top Patterns</h2>
            <div class="chart-container">
                <canvas id="patternChart"></canvas>
            </div>
        </div>

        <div class="card" style="grid-column: 1 / -1;">
            <h2>🔍 Anomalies ({anomaly_count})</h2>
            <table>
                <tr><th>Severity</th><th>Type</th><th>Service</th><th>Description</th></tr>
                {''.join(
                    f'<tr>'
                    f'<td><span class="badge badge-{a.severity.value}">{a.severity.value.upper()}</span></td>'
                    f'<td>{a.anomaly_type.value}</td>'
                    f'<td><span class="svc-tag">{_escape_html(a.service or "—")}</span></td>'
                    f'<td>{_escape_html(a.description[:200])}</td>'
                    f'</tr>'
                    for a in result.anomalies
                ) if result.anomalies else '<tr><td colspan="4" style="text-align:center;color:#94a3b8;">No anomalies detected ✅</td></tr>'}
            </table>
        </div>

        <div class="card" style="grid-column: 1 / -1;">
            <h2>🔴 Error Groups ({len(result.error_groups)})</h2>
            <table>
                <tr><th>Count</th><th>Services</th><th>Root Cause</th><th>Sample</th></tr>
                {''.join(
                    f'<tr>'
                    f'<td>{eg.count:,}</td>'
                    f'<td>{"".join(f"<span class=svc-tag style=margin-right:4px>{_escape_html(s)}</span>" for s in eg.services[:3])}</td>'
                    f'<td>{_escape_html(eg.root_cause_candidate or "—")}</td>'
                    f'<td class="error-msg">{_escape_html(eg.sample_messages[0][:120] if eg.sample_messages else "—")}</td>'
                    f'</tr>'
                    for eg in result.error_groups[:15]
                ) if result.error_groups else '<tr><td colspan="4" style="text-align:center;color:#94a3b8;">No error groups found</td></tr>'}
            </table>
        </div>

        {'<div class="card" style="grid-column: 1 / -1;">' + chr(10) +
         '            <h2>📜 Error Logs (' + str(len(error_logs)) + ')</h2>' + chr(10) +
         '            <table>' + chr(10) +
         '                <tr><th style="width:140px">Timestamp</th><th style="width:140px">Service</th><th>Message</th></tr>' + chr(10) +
         ''.join(
             f'<tr>'
             f'<td style="white-space:nowrap;color:#94a3b8;font-size:0.8rem;">{_escape_html(str(el.get("timestamp", ""))[:19])}</td>'
             f'<td><span class="svc-tag">{_escape_html(str(el.get("service", "—")))}</span></td>'
             f'<td class="error-msg">{_escape_html(str(el.get("message", ""))[:300])}</td>'
             f'</tr>'
             for el in error_logs[:50]
         ) +
         '            </table>' + chr(10) +
         '        </div>'
         if error_logs else ''}
    </div>

    <script>
        // Trend chart
        new Chart(document.getElementById('trendChart'), {{
            type: 'line',
            data: {{
                labels: {json.dumps(trend_labels)},
                datasets: [
                    {{
                        label: 'Total Logs',
                        data: {json.dumps(trend_counts)},
                        borderColor: '#60a5fa',
                        backgroundColor: 'rgba(96,165,250,0.1)',
                        fill: true, tension: 0.3,
                    }},
                    {{
                        label: 'Errors',
                        data: {json.dumps(trend_errors)},
                        borderColor: '#f87171',
                        backgroundColor: 'rgba(248,113,113,0.1)',
                        fill: true, tension: 0.3,
                    }}
                ]
            }},
            options: {{
                responsive: true, maintainAspectRatio: false,
                plugins: {{ legend: {{ labels: {{ color: '#94a3b8' }} }} }},
                scales: {{
                    x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#334155' }} }},
                    y: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#334155' }} }}
                }}
            }}
        }});

        // Pattern chart
        new Chart(document.getElementById('patternChart'), {{
            type: 'bar',
            data: {{
                labels: {json.dumps(pattern_labels)},
                datasets: [{{
                    label: 'Occurrences',
                    data: {json.dumps(pattern_counts)},
                    backgroundColor: '#60a5fa',
                    borderRadius: 4,
                }}]
            }},
            options: {{
                indexAxis: 'y',
                responsive: true, maintainAspectRatio: false,
                plugins: {{ legend: {{ display: false }} }},
                scales: {{
                    x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#334155' }} }},
                    y: {{ ticks: {{ color: '#94a3b8', font: {{ size: 10 }} }}, grid: {{ display: false }} }}
                }}
            }}
        }});
    </script>
</body>
</html>"""

    if output_path is None:
        return html

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html)
    return path

