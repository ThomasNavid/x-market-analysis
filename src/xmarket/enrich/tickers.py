"""Qualify X posts as trade intelligence and extract canonical stock tickers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from anthropic import Anthropic
from psycopg.types.json import Jsonb

from xmarket.config import settings
from xmarket.db.connection import connect
from xmarket.enrich.anthropic_json import create_anthropic_client, create_json_message

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9]{0,5}(?:[.-][A-Z])?$")


QUALIFICATION_SYSTEM = """You classify X posts for a stock-trading research pipeline.
Return JSON only.

A post is qualified when it discusses a public company, ticker, market-moving event,
trading setup, or stock-specific information that could plausibly be useful as
trading intelligence.

Qualified examples include product demand/news, earnings commentary, analyst or
regulatory updates, executive changes, supply-chain signals, unusual business
activity, or explicit trade commentary about a particular public stock.

Reject generic market chatter, politics or macro commentary without a clear
investable company target, jokes with no trading signal, private companies only,
crypto-only posts, and posts where no public stock can be inferred.

Schema:
{"qualified": true|false, "reason": "short reason"}
"""


TICKER_SYSTEM = """You extract canonical public stock tickers from qualified X posts.
Return JSON only.

Resolve company names and cashtags to investable ticker symbols. For example,
"Apple" should return "AAPL" when the post is about Apple Inc. Do not return a
ticker when the company is private, ambiguous, not investable, or only mentioned
as a metaphor.

Schema:
{
  "tickers": [
    {"ticker": "AAPL", "confidence": 0.0-1.0, "reason": "short reason"}
  ]
}
"""


@dataclass(frozen=True)
class PostForEnrichment:
    """Minimal post fields needed by enrichment."""

    id: str
    text: str
    created_at: datetime


@dataclass(frozen=True)
class Qualification:
    """LLM qualification result for one post."""

    post_id: str
    qualified: bool
    reason: str
    model: str
    prompt_version: str


@dataclass(frozen=True)
class ExtractedTicker:
    """One canonical ticker extracted from a qualified post."""

    post_id: str
    ticker: str
    confidence: float
    reason: str


@dataclass(frozen=True)
class TickerEnrichmentResult:
    """Summary of ticker enrichment work."""

    qualified_checked: int
    qualified: int
    rejected: int
    extraction_checked: int
    ticker_rows_upserted: int
    ticker_dates: list[tuple[str, datetime]]


def _confidence(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, numeric))


def _clean_ticker(value: Any) -> str | None:
    ticker = str(value or "").strip().upper().removeprefix("$")
    if not TICKER_RE.fullmatch(ticker):
        return None
    return ticker


def _post_prompt(post: PostForEnrichment) -> str:
    return f"Post id: {post.id}\nCreated at: {post.created_at.isoformat()}\nText:\n{post.text}"


def fetch_posts_needing_qualification(*, limit: int) -> list[PostForEnrichment]:
    """Return posts without a cached qualification for the active prompt."""
    sql = """
        SELECT p.id, p.text, p.created_at
        FROM posts p
        LEFT JOIN post_qualifications q
            ON q.post_id = p.id
            AND q.prompt_version = %s
        WHERE q.post_id IS NULL
        ORDER BY p.created_at DESC
        LIMIT %s
    """
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (settings.qualify_prompt_version, limit))
            return [
                PostForEnrichment(id=row[0], text=row[1], created_at=row[2])
                for row in cur.fetchall()
            ]


def fetch_posts_needing_ticker_extraction(*, limit: int) -> list[PostForEnrichment]:
    """Return qualified posts without a cached ticker extraction for the active prompt."""
    sql = """
        SELECT p.id, p.text, p.created_at
        FROM posts p
        JOIN post_qualifications q
            ON q.post_id = p.id
            AND q.prompt_version = %s
            AND q.qualified = true
        LEFT JOIN post_ticker_extractions e
            ON e.post_id = p.id
            AND e.prompt_version = %s
        WHERE e.post_id IS NULL
        ORDER BY p.created_at DESC
        LIMIT %s
    """
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    settings.qualify_prompt_version,
                    settings.ticker_prompt_version,
                    limit,
                ),
            )
            return [
                PostForEnrichment(id=row[0], text=row[1], created_at=row[2])
                for row in cur.fetchall()
            ]


def qualify_post(client: Anthropic, post: PostForEnrichment) -> Qualification:
    """Ask Claude whether one post should proceed to ticker extraction."""
    payload = create_json_message(
        client,
        model=settings.qualify_model,
        system=QUALIFICATION_SYSTEM,
        user=_post_prompt(post),
        max_tokens=300,
    )
    return Qualification(
        post_id=post.id,
        qualified=bool(payload.get("qualified", False)),
        reason=str(payload.get("reason", ""))[:1000],
        model=settings.qualify_model,
        prompt_version=settings.qualify_prompt_version,
    )


def extract_tickers_for_post(client: Anthropic, post: PostForEnrichment) -> list[ExtractedTicker]:
    """Ask Claude to resolve canonical tickers for one qualified post."""
    payload = create_json_message(
        client,
        model=settings.qualify_model,
        system=TICKER_SYSTEM,
        user=_post_prompt(post),
        max_tokens=600,
    )
    raw_tickers = payload.get("tickers", [])
    if not isinstance(raw_tickers, list):
        return []

    tickers: list[ExtractedTicker] = []
    seen: set[str] = set()
    for item in raw_tickers:
        if not isinstance(item, dict):
            continue
        ticker = _clean_ticker(item.get("ticker"))
        if ticker is None or ticker in seen:
            continue
        seen.add(ticker)
        tickers.append(
            ExtractedTicker(
                post_id=post.id,
                ticker=ticker,
                confidence=_confidence(item.get("confidence")),
                reason=str(item.get("reason", ""))[:1000],
            )
        )
    return tickers


def upsert_qualifications(qualifications: list[Qualification]) -> int:
    """Persist cached post qualification decisions."""
    if not qualifications:
        return 0

    sql = """
        INSERT INTO post_qualifications (
            post_id, prompt_version, qualified, reason, model
        )
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (post_id, prompt_version)
        DO UPDATE SET
            qualified = EXCLUDED.qualified,
            reason = EXCLUDED.reason,
            model = EXCLUDED.model,
            created_at = now()
    """
    rows = [(q.post_id, q.prompt_version, q.qualified, q.reason, q.model) for q in qualifications]
    with connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()
    return len(qualifications)


def persist_ticker_extraction(
    *,
    post: PostForEnrichment,
    tickers: list[ExtractedTicker],
) -> int:
    """Cache extraction output and upsert normalized `post_tickers` rows."""
    extraction_sql = """
        INSERT INTO post_ticker_extractions (
            post_id, prompt_version, model, raw_tickers
        )
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (post_id, prompt_version)
        DO UPDATE SET
            model = EXCLUDED.model,
            raw_tickers = EXCLUDED.raw_tickers,
            created_at = now()
    """
    ticker_sql = """
        INSERT INTO post_tickers (
            post_id, ticker, match_method, confidence,
            qualification_prompt_version, extraction_prompt_version,
            qualification_reason, extracted_at
        )
        SELECT %s, %s, 'llm', %s, q.prompt_version, %s, q.reason, now()
        FROM post_qualifications q
        WHERE q.post_id = %s
            AND q.prompt_version = %s
        ON CONFLICT (post_id, ticker)
        DO UPDATE SET
            match_method = EXCLUDED.match_method,
            confidence = EXCLUDED.confidence,
            qualification_prompt_version = EXCLUDED.qualification_prompt_version,
            extraction_prompt_version = EXCLUDED.extraction_prompt_version,
            qualification_reason = EXCLUDED.qualification_reason,
            extracted_at = now()
    """
    raw_tickers = [
        {"ticker": item.ticker, "confidence": item.confidence, "reason": item.reason}
        for item in tickers
    ]
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                extraction_sql,
                (
                    post.id,
                    settings.ticker_prompt_version,
                    settings.qualify_model,
                    Jsonb(raw_tickers),
                ),
            )
            for ticker in tickers:
                cur.execute(
                    ticker_sql,
                    (
                        post.id,
                        ticker.ticker,
                        ticker.confidence,
                        settings.ticker_prompt_version,
                        post.id,
                        settings.qualify_prompt_version,
                    ),
                )
        conn.commit()
    return len(tickers)


def qualify_and_extract_tickers(*, limit: int) -> TickerEnrichmentResult:
    """Run Step 4: qualify posts, then extract canonical tickers for qualified posts."""
    if limit < 1:
        raise ValueError("limit must be at least 1")

    client = create_anthropic_client()

    qualification_posts = fetch_posts_needing_qualification(limit=limit)
    qualifications = [qualify_post(client, post) for post in qualification_posts]
    upsert_qualifications(qualifications)

    extraction_posts = fetch_posts_needing_ticker_extraction(limit=limit)
    ticker_rows = 0
    ticker_dates: list[tuple[str, datetime]] = []
    for post in extraction_posts:
        tickers = extract_tickers_for_post(client, post)
        ticker_rows += persist_ticker_extraction(post=post, tickers=tickers)
        ticker_dates.extend((ticker.ticker, post.created_at) for ticker in tickers)

    qualified = sum(1 for item in qualifications if item.qualified)
    return TickerEnrichmentResult(
        qualified_checked=len(qualifications),
        qualified=qualified,
        rejected=len(qualifications) - qualified,
        extraction_checked=len(extraction_posts),
        ticker_rows_upserted=ticker_rows,
        ticker_dates=ticker_dates,
    )
