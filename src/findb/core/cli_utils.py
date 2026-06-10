"""Small shared helpers for Typer CLIs."""

from rich.console import Console
from rich.table import Table

console = Console()


def _parse_csv(value: str | None) -> list[str]:
    if value is None:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_tickers(value: str | None) -> list[str]:
    return [item.upper() for item in _parse_csv(value)]


def _print_kv_table(title: str, rows: list[tuple[str, object]]) -> None:
    table = Table(title=title, show_header=False)
    table.add_column("Field", style="cyan", no_wrap=True)
    table.add_column("Value")
    for label, value in rows:
        table.add_row(label, str(value))
    console.print(table)


def _format_optional(value: object | None) -> str:
    if value is None:
        return "-"
    return str(value)


def _clip(value: str | None, *, max_chars: int) -> str:
    if value is None:
        return "-"
    compact = " ".join(value.split())
    if len(compact) <= max_chars:
        return compact
    return f"{compact[: max_chars - 3]}..."
