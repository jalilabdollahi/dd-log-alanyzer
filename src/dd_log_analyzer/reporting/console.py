"""Rich console output — formatted tables, sparklines, and severity colors."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from dd_log_analyzer.models.log_entry import (
    AlertSeverity,
    AnalysisResult,
    LogEntry,
)

console = Console()

_SEVERITY_COLORS = {
    "critical": "bold red",
    "error": "red",
    "warn": "yellow",
    "warning": "yellow",
    "info": "cyan",
    "debug": "dim",
}

_TREND_EMOJI = {
    "increasing": "📈",
    "decreasing": "📉",
    "stable": "➡️",
}


def print_logs(logs: list[LogEntry], limit: int = 50) -> None:
    """Print log entries in a formatted table."""
    table = Table(title=f"Log Results ({len(logs)} total)", show_lines=True, expand=True)
    table.add_column("Time", style="dim", width=20)
    table.add_column("Status", width=8)
    table.add_column("Service", style="cyan", width=20)
    table.add_column("Message", ratio=1)

    for log in logs[:limit]:
        status_style = _SEVERITY_COLORS.get(log.status, "")
        table.add_row(
            log.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            Text(log.status.upper(), style=status_style),
            log.service,
            log.message[:200],
        )

    console.print(table)
    if len(logs) > limit:
        console.print(f"[dim]  ... and {len(logs) - limit} more logs[/dim]")


def print_analysis(result: AnalysisResult) -> None:
    """Print a full analysis result with sections for each module."""
    console.print()
    console.print(
        Panel(
            f"[bold]Analysis Report[/bold]\n"
            f"Query: [cyan]{result.query}[/cyan]\n"
            f"Time: {result.time_from.strftime('%H:%M:%S')} → {result.time_to.strftime('%H:%M:%S')}\n"
            f"Total logs analyzed: [bold]{result.total_logs:,}[/bold]",
            title="📊 Datadog Log Analysis",
            border_style="blue",
        )
    )

    # --- Anomalies ---
    if result.anomalies:
        console.print()
        table = Table(title=f"🔍 Anomalies Detected ({len(result.anomalies)})", show_lines=True, expand=True)
        table.add_column("Severity", width=10)
        table.add_column("Type", width=18)
        table.add_column("Service", width=18)
        table.add_column("Description", ratio=1)

        for anom in result.anomalies:
            sev_style = _SEVERITY_COLORS.get(anom.severity.value, "")
            table.add_row(
                Text(anom.severity.value.upper(), style=sev_style),
                anom.anomaly_type.value,
                anom.service or "—",
                anom.description,
            )
        console.print(table)
    else:
        console.print("\n[green]✅ No anomalies detected[/green]")

    # --- Patterns ---
    if result.patterns:
        console.print()
        table = Table(title=f"📋 Top Log Patterns ({len(result.patterns)})", show_lines=True, expand=True)
        table.add_column("#", width=4)
        table.add_column("Count", width=8)
        table.add_column("%", width=6)
        table.add_column("Template", ratio=1)
        table.add_column("Services", width=25)

        for i, pat in enumerate(result.patterns[:15], 1):
            services_str = ", ".join(f"{s}({c})" for s, c in sorted(pat.services.items(), key=lambda x: -x[1])[:3])
            table.add_row(
                str(i),
                f"{pat.count:,}",
                f"{pat.percentage:.1f}%",
                pat.template[:120],
                services_str,
            )
        console.print(table)

    # --- Error Groups ---
    if result.error_groups:
        console.print()
        table = Table(title=f"🔴 Error Groups ({len(result.error_groups)})", show_lines=True, expand=True)
        table.add_column("#", width=4)
        table.add_column("Count", width=8)
        table.add_column("Services", width=25)
        table.add_column("Root Cause?", width=15)
        table.add_column("Sample Message", ratio=1)

        for i, eg in enumerate(result.error_groups[:10], 1):
            root = eg.root_cause_candidate or "—"
            sample = eg.sample_messages[0][:100] if eg.sample_messages else "—"
            table.add_row(
                str(i),
                f"{eg.count:,}",
                ", ".join(eg.services[:3]),
                root,
                sample,
            )
        console.print(table)

    # --- Trends ---
    trends = result.trends
    if trends.buckets:
        console.print()
        emoji = _TREND_EMOJI.get(trends.trend_direction, "")
        trend_style = "red" if trends.trend_direction == "increasing" else (
            "green" if trends.trend_direction == "decreasing" else "dim"
        )
        console.print(
            Panel(
                f"Direction: {emoji} [bold {trend_style}]{trends.trend_direction.upper()}[/bold {trend_style}]\n"
                f"Baseline avg: {trends.baseline_avg:.0f} logs/bucket → Current avg: {trends.current_avg:.0f} "
                f"([{'red' if trends.change_percentage > 0 else 'green'}]"
                f"{trends.change_percentage:+.1f}%[/])\n"
                f"Slope: {trends.slope:.2f} logs/bucket",
                title="📈 Trend Analysis",
                border_style="blue",
            )
        )

        # Mini sparkline
        max_count = max(b.count for b in trends.buckets) if trends.buckets else 1
        spark_chars = "▁▂▃▄▅▆▇█"
        sparkline = ""
        for b in trends.buckets:
            idx = min(int((b.count / max_count) * (len(spark_chars) - 1)), len(spark_chars) - 1) if max_count > 0 else 0
            sparkline += spark_chars[idx]
        console.print(f"  Volume: [cyan]{sparkline}[/cyan]")

    console.print()
