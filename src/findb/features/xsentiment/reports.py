"""Read-only enrichment reports for CLI inspection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

from findb.config import settings
from findb.core.db.connection import connect


@dataclass(frozen=True)
class QualifiedReportRow:
    """One row for inspecting qualified posts and their ticker sentiment."""

    created_at: datetime
    post_id: str
    ticker: str | None
    ticker_confidence: float | None
    qualification_reason: str
    sentiment_label: str | None
    sentiment_score: float | None
    sentiment_rationale: str | None
    text: str


def parse_since_date(value: str | None) -> datetime | None:
    """Parse an ISO date/datetime string for report filtering."""
    if value is None:
        return None

    stripped = value.strip()
    if not stripped:
        return None

    try:
        if len(stripped) == 10:
            return datetime.combine(date.fromisoformat(stripped), time.min)
        return datetime.fromisoformat(stripped)
    except ValueError as exc:
        raise ValueError("--since must be an ISO date or datetime, e.g. 2026-06-08") from exc


def _preview(text: str, *, max_chars: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return compact
    return f"{compact[: max_chars - 3]}..."


def fetch_qualified_report_rows(
    *,
    limit: int,
    ticker: str | None,
    min_abs_score: float | None,
    since: datetime | None,
    text_chars: int,
) -> list[QualifiedReportRow]:
    """Fetch qualified post/ticker sentiment rows for CLI reporting."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if text_chars < 20:
        raise ValueError("text_chars must be at least 20")
    if min_abs_score is not None and min_abs_score < 0:
        raise ValueError("min_abs_score must be non-negative")

    predicates = [
        "q.prompt_version = %s",
        "q.qualified = true",
    ]
    params: list[Any] = [settings.qualify_prompt_version]

    if ticker is not None:
        predicates.append("pt.ticker = %s")
        params.append(ticker.upper())
    if min_abs_score is not None:
        predicates.append("abs(s.score) >= %s")
        params.append(min_abs_score)
    if since is not None:
        predicates.append("p.created_at >= %s")
        params.append(since)

    params.append(limit)
    where_sql = " AND ".join(predicates)
    sql = f"""
        SELECT
            p.created_at,
            p.id,
            pt.ticker,
            pt.confidence,
            q.reason,
            s.label,
            s.score,
            s.rationale,
            p.text
        FROM post_qualifications q
        JOIN posts p
            ON p.id = q.post_id
        LEFT JOIN post_tickers pt
            ON pt.post_id = q.post_id
        LEFT JOIN sentiments s
            ON s.post_id = pt.post_id
            AND s.ticker = pt.ticker
            AND s.model = %s
            AND s.prompt_version = %s
        WHERE {where_sql}
        ORDER BY p.created_at DESC, pt.ticker NULLS LAST
        LIMIT %s
    """
    params = [
        settings.sentiment_model,
        settings.sentiment_prompt_version,
        *params,
    ]

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    return [
        QualifiedReportRow(
            created_at=row[0],
            post_id=row[1],
            ticker=row[2],
            ticker_confidence=float(row[3]) if row[3] is not None else None,
            qualification_reason=row[4],
            sentiment_label=row[5],
            sentiment_score=float(row[6]) if row[6] is not None else None,
            sentiment_rationale=row[7],
            text=_preview(row[8], max_chars=text_chars),
        )
        for row in rows
    ]
